"""Create recording-aligned cue evidence with a selected local Parakeet TDT model."""

import hashlib
from importlib.util import find_spec
import math
from pathlib import Path
import subprocess

from . import media_tools
from .timing import TimingError


SAMPLE_RATE = 16_000
WINDOW_SECONDS = 30
CONTEXT_SECONDS = 2
MODEL_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")


def available():
    return bool(media_tools.path("ffmpeg") and find_spec("sherpa_onnx") and find_spec("numpy"))


def _sha(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _window_words(result, offset, lower, upper, decoded_end):
    tokens = list(result.tokens)
    starts = list(result.timestamps)
    durations = list(result.durations)
    if len(tokens) != len(starts) or len(tokens) != len(durations):
        raise TimingError("The recognizer must supply measured starts and durations for each token.")
    words = []
    for token, position, duration in zip(tokens, starts, durations, strict=True):
        raw = token.replace("▁", " ")
        text = raw.strip()
        if not text:
            continue
        spoken = any(char.isalnum() for char in text)
        if not spoken:
            if words:
                words[-1]["text"] += text
            continue
        start = offset + position
        if not math.isfinite(start) or not math.isfinite(duration) or duration < 0:
            raise TimingError("The recognizer returned an invalid timestamp. Supply corrected timing evidence.")
        end = start + duration
        if not words or raw[0].isspace():
            words.append({"text": text, "start": start, "end": end, "end_basis": "recognizer-duration"})
        else:
            word = words[-1]
            word["text"] += text
            word["end"] = max(word["end"], end)
    owned = [word for word in words if lower <= word["start"] < upper]
    for word in owned:
        if not word["start"] < word["end"] <= decoded_end + 0.000001:
            raise TimingError(
                f"A spoken word at {word['start']:.2f}s lacks a valid measured span. Supply a corrected cue sheet."
            )
    return owned


def derive(wav_path: Path, model_directory: str) -> dict:
    """Decode the complete source with overlap at window edges and no estimated word endpoints."""
    if not isinstance(model_directory, str) or not model_directory or len(model_directory) > 2048:
        raise TimingError("Choose the absolute path to the installed Parakeet TDT model folder.")
    model = Path(model_directory)
    if not model.is_absolute() or not all((model / filename).is_file() for filename in MODEL_FILES):
        raise TimingError("Select a Parakeet TDT folder with encoder.int8.onnx, decoder.int8.onnx, joiner.int8.onnx and tokens.txt.")
    try:
        import numpy as np
        import sherpa_onnx
    except (ImportError, OSError) as error:
        raise TimingError("Local cue recognition requires the Bacchus audio timing dependencies. You can also import measured timings.") from error
    try:
        models = {name: _sha(model / name) for name in MODEL_FILES}
        identity = _sha(wav_path)
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            **{key: str(model / name) for key, name in zip(("encoder", "decoder", "joiner", "tokens"), MODEL_FILES, strict=True)},
            num_threads=2, model_type="nemo_transducer",
        )
        decoded = subprocess.run(
            [media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
             "-i", str(wav_path), "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"],
            capture_output=True, check=True, timeout=120, **media_tools.process_options(),
        )
        samples = np.frombuffer(decoded.stdout, dtype="<f4")
        if not len(samples) or not np.isfinite(samples).all():
            raise TimingError("The recording did not decode to finite audio for cue recognition.")
        context = CONTEXT_SECONDS * SAMPLE_RATE
        window = WINDOW_SECONDS * SAMPLE_RATE
        words = []
        for start in range(0, len(samples), window):
            stop = min(start + window, len(samples))
            left, right = max(0, start - context), min(len(samples), stop + context)
            stream = recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples[left:right])
            recognizer.decode_stream(stream)
            words.extend(_window_words(stream.result, left / SAMPLE_RATE, start / SAMPLE_RATE,
                                       stop / SAMPLE_RATE, right / SAMPLE_RATE))
        if _sha(wav_path) != identity or any(_sha(model / name) != value for name, value in models.items()):
            raise TimingError("The recording or recognizer files changed during cue recognition.")
        if not words:
            raise TimingError("The recognizer found no usable spoken words.")
        return {"words": words, "provenance": {"method": "local Parakeet TDT", "models": models,
                                               "sampleRate": SAMPLE_RATE, "windowSeconds": WINDOW_SECONDS}}
    except TimingError:
        raise
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        raise TimingError("Bacchus could not recognize cues with this audio and local Parakeet model.") from error
