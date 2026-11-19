"""Validated local audio intake and opt-in file transcription."""

import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import textwrap
from urllib.request import Request
import uuid
import wave

from . import media_tools
from .providers import ProviderError, send_request
from .settings import DEFAULT_TRANSCRIBE_MODEL, openai_transcription_enabled, provider_value


FORMATS = {".mp3": "mp3", ".flac": "flac", ".wav": "wav"}
TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
TRANSCRIPTION_INPUT_LIMIT = 25_000_000
TRANSCRIPTION_RESPONSE_LIMIT = 512 * 1024
MAX_CUES = 1200
MAX_TRANSCRIPT_LENGTH = 250_000


class AudioError(ValueError):
    pass


def audio_available():
    return bool(media_tools.path("ffmpeg"))


def transcriber_available():
    return openai_transcription_enabled() and bool(provider_value("OPENAI_API_KEY", "").strip()) and audio_available()


def transcription_model():
    return provider_value("BACCHUS_TRANSCRIBE_MODEL", "").strip() or DEFAULT_TRANSCRIBE_MODEL


def _convert(path, container, output, settings):
    if not audio_available():
        raise AudioError("Open Settings to set up FFmpeg for audio import and transcription.")
    command = [
        media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
        "-max_alloc", str(64 * 1024 * 1024), "-filter_threads", "1", "-threads", "2",
        "-protocol_whitelist", "file", "-format_whitelist", container, "-i", str(path),
        "-map", "0:a:0", "-map_metadata", "-1", "-map_chapters", "-1",
        *settings, str(output),
    ]
    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=90,
                       **media_tools.process_options())
    except subprocess.TimeoutExpired as error:
        raise AudioError("Audio decoding exceeded its time limit.") from error
    except (subprocess.CalledProcessError, OSError) as error:
        raise AudioError(f"FFmpeg could not process this {container.upper()} recording.") from error


def normalize_audio(raw, name, max_duration, *, voice=True):
    """Decode an upload into content-addressable PCM WAV."""
    container = FORMATS.get(Path(name).suffix.lower())
    if not container:
        raise AudioError("Choose an MP3, FLAC, or WAV recording.")
    channels, rate = (1, 24_000) if voice else (2, 48_000)
    with TemporaryDirectory(prefix="bacchus-audio-") as work:
        path = Path(work) / f"recording.{container}"
        output = Path(work) / "normalized.wav"
        path.write_bytes(raw)
        settings = [
            "-ac", str(channels), "-ar", str(rate), "-c:a", "pcm_s16le",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", "-t", str(max_duration + 1),
        ]
        _convert(path, container, output, settings)
        with wave.open(str(output), "rb") as recording:
            duration = recording.getnframes() / recording.getframerate()
        if not 0.2 <= duration <= max_duration:
            raise AudioError(f"Use an audio recording between 0.2 and {max_duration} seconds.")
        return output.read_bytes(), duration


def _transcript_script(value):
    if not isinstance(value, str):
        raise AudioError("The transcription service returned no usable text.")
    transcript = " ".join(value.split())
    if not transcript:
        raise AudioError("The transcription service detected no narration.")
    if len(transcript) > MAX_TRANSCRIPT_LENGTH:
        raise AudioError("The transcript is too long for this project.")
    sentences = re.split(r"(?<=[.!?]) +|(?<=[。！？]) *(?=\S)", transcript)
    options = {"break_on_hyphens": False}
    cues = []
    pending = ""
    for sentence in sentences:
        for chunk in textwrap.wrap(sentence, 210, **options):
            if pending and len(pending) + len(chunk) + 1 > 220:
                cues.append(pending)
                pending = ""
            pending = f"{pending} {chunk}" if pending else chunk
    if pending:
        cues.append(pending)
    if len(cues) > MAX_CUES:
        cues = textwrap.wrap(transcript, 500, **options)
    if len(cues) > MAX_CUES:
        raise AudioError("The transcript has too many cues. Try a shorter recording.")
    script = "\n".join(cues)
    if len(script) > MAX_TRANSCRIPT_LENGTH:
        raise AudioError("The transcript is too long for this project.")
    return script


def _transcription_request(encoded_audio, key, model):
    boundary = f"bacchus-{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n',
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="narration.m4a"\r\n'
        "Content-Type: audio/mp4\r\n\r\n",
    ]
    body = "".join(parts).encode("ascii") + encoded_audio + f"\r\n--{boundary}--\r\n".encode("ascii")
    return Request(
        TRANSCRIPTION_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )


def transcribe(wav_path):
    """Send a compact narration copy and return an editable transcript draft."""
    if not openai_transcription_enabled():
        raise AudioError("Enable OpenAI transcription in Settings to transcribe.")
    key = provider_value("OPENAI_API_KEY", "").strip()
    if not key:
        raise AudioError("Add an OpenAI API key in Settings to transcribe.")
    model = transcription_model()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", model):
        raise AudioError("BACCHUS_TRANSCRIBE_MODEL must be a valid model ID.")
    with TemporaryDirectory(prefix="bacchus-transcribe-") as work:
        encoded_path = Path(work) / "narration.m4a"
        settings = ["-ac", "1", "-ar", "24000", "-c:a", "aac", "-b:a", "48k", "-fs", str(TRANSCRIPTION_INPUT_LIMIT)]
        _convert(wav_path, "wav", encoded_path, settings)
        if not 0 < encoded_path.stat().st_size < TRANSCRIPTION_INPUT_LIMIT:
            raise AudioError("The narration is empty or too long for one transcription request.")
        request = _transcription_request(encoded_path.read_bytes(), key, model)
    try:
        raw, request_id = send_request(request, "OpenAI", timeout=105, limit=TRANSCRIPTION_RESPONSE_LIMIT)
    except ProviderError as error:
        raise AudioError(str(error)) from error
    try:
        result = json.loads(raw)
        script = _transcript_script(result.get("text"))
    except (UnicodeError, json.JSONDecodeError, AttributeError, RecursionError) as error:
        raise AudioError("The transcription service returned an unreadable response.") from error
    return {
        "script": script,
        "model": model,
        "timing": "estimated",
        "requestId": request_id,
        "usage": result.get("usage"),
    }
