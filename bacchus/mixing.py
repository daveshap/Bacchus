"""Prepare a complete voice-first soundtrack and record its PCM evidence."""

import hashlib
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import wave

from . import media_tools
from .storage_paths import writable_path

SAMPLE_RATE = 48_000
CHANNELS = 2
MAX_MUSIC_REGIONS = 24
TARGET_LUFS = -14.0
DIALOGUE_LUFS = -16.0
DELIVERY_CEILING_DBTP = -1.5
MASTER_CEILING_DBTP = -2.0  # Leave room for the AAC encoder's intersample peaks.
LOUDNESS_TOLERANCE_LU = 1.0
AAC_BITRATE = "384k"
_METER_BLOCK_SECONDS = 0.4
_MASTER_ATTEMPTS = 4
_MAX_GAIN_DB = 24.0
_SHA256 = re.compile(r"[0-9a-f]{64}")
_METER_NUMBER = r"(?:-?\d+(?:\.\d+)?|-inf)"


class MixError(ValueError):
    pass


def validate_music_regions(project, regions):
    if not isinstance(regions, list) or len(regions) > MAX_MUSIC_REGIONS:
        raise MixError(f"Use at most {MAX_MUSIC_REGIONS} music regions.")
    line_count = len(project["input"]["lines"])
    result = []
    for region in regions:
        if not isinstance(region, dict) or region.keys() != {"startLine", "endLine", "gainDb"}:
            raise MixError("Each music region needs a first cue, last cue, and level.")
        start, end, gain = region["startLine"], region["endLine"], region["gainDb"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= line_count:
            raise MixError("Music regions must use existing narration cues in order.")
        if gain is not None:
            _number(gain, "Music region gain", -48, 0)
        result.append({"startLine": start, "endLine": end, "gainDb": gain})
    result.sort(key=lambda region: region["startLine"])
    if any(previous["endLine"] >= current["startLine"] for previous, current in zip(result, result[1:])):
        raise MixError("Music regions cannot overlap.")
    return result


def _number(value, label, minimum, maximum):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise MixError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _verified_wav(folder, directory, sha256, label):
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise MixError(f"{label} has an invalid fingerprint.")
    path = folder / directory / (sha256 + ".wav")
    try:
        actual = _sha256(path)
    except OSError as error:
        raise MixError(f"{label} could not be read.") from error
    if actual != sha256:
        raise MixError(f"{label} no longer matches its fingerprint.")
    return path


def _wav_format(path):
    try:
        with wave.open(str(path), "rb") as recording:
            return recording.getnframes(), recording.getframerate(), recording.getnchannels(), recording.getsampwidth()
    except (OSError, EOFError, wave.Error) as error:
        raise MixError("The soundtrack could not be read as PCM WAV.") from error


def _sample_count(frame_count, fps):
    return (frame_count * SAMPLE_RATE + fps - 1) // fps


def verify_mix(folder, metadata, frame_count, fps):
    """Check the actual approved WAV before it is previewed or encoded."""
    expected = _sample_count(frame_count, fps)
    if not isinstance(metadata, dict) or (
        metadata.get("samples"), metadata.get("sampleRate"), metadata.get("channels"), metadata.get("duration")
    ) != (expected, SAMPLE_RATE, CHANNELS, expected / SAMPLE_RATE):
        raise MixError("Prepare a soundtrack for this entire cut before rendering.")
    path = _verified_wav(folder, "mixes", metadata.get("sha256"), "The prepared soundtrack")
    if _wav_format(path) != (expected, SAMPLE_RATE, CHANNELS, 2):
        raise MixError("The prepared soundtrack no longer covers the cut in PCM.")
    return path


def _music_volume(project, gain):
    regions = validate_music_regions(project, project["mix"].get("musicRegions", []))
    if not regions:
        return f"volume={gain:.3f}dB"
    lines = project["input"]["lines"]
    duration = project["input"]["duration"]
    base = 10 ** (gain / 20)
    terms = [f"{base:.12f}"]
    for region in regions:
        start = lines[region["startLine"] - 1]["start"]
        end_line = region["endLine"]
        end = lines[end_line]["start"] if end_line < len(lines) else duration
        if end <= start:
            continue
        fade = max(1 / SAMPLE_RATE, min(0.14, (end - start) / 2))
        fade_in = f"min(1,max(0,(t-{start:.9f})/{fade:.9f}))" if start > 0 else "1"
        fade_out = f"min(1,max(0,({end:.9f}-t)/{fade:.9f}))" if end < duration else "1"
        region_gain = region["gainDb"]
        difference = (0 if region_gain is None else 10 ** (region_gain / 20)) - base
        terms.append(f"({difference:.12f})*between(t,{start:.9f},{end:.9f})*{fade_in}*{fade_out}")
    expression = "+".join(terms)
    return f"volume='if(isnan(t),{base:.12f},max(0,{expression}))':eval=frame"


def _sound_options(project, folder, samples, voice_gain):
    window = f"apad=whole_len={samples},atrim=end_sample={samples}"
    audio_format = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    options, filters, outputs = [], [], []
    input_count = 0

    def source(path, *, loop_samples=None):
        nonlocal input_count
        options.extend(("-f", "wav", "-i", str(path)))
        channel = f"[{input_count}:a:0]"
        input_count += 1
        # Loop the sample window before resampling so the ducking input has a continuous clock.
        loop = f"aloop=loop=-1:size={loop_samples}," if loop_samples else ""
        return f"{channel}{loop}{audio_format},asetpts=N/SR/TB"

    mix = project["mix"]
    voice = project["audio"]
    music = mix.get("music") if mix.get("musicMode") == "track" else None
    if mix.get("musicMode") == "track" and not music:
        raise MixError("The selected music track is missing.")

    if voice:
        narration_path = _verified_wav(folder, "audio", voice["hash"], "Narration")
        narration = source(narration_path)
        split = ",asplit=2[voice][sidechain]" if music else "[voice]"
        filters.append(
            f"{narration},highpass=f=70,volume={voice_gain:.3f}dB,"
            "acompressor=threshold=0.178:ratio=2.2:attack=5:release=90:knee=2.8:"
            f"link=maximum:detection=peak,{window}{split}"
        )
        outputs.append("[voice]")

    if music:
        gain = _number(music["gainDb"], "Music gain", -48, 0)
        music_path = _verified_wav(folder, "audio", music["hash"], "Music")
        music_samples, music_rate, _, _ = _wav_format(music_path)
        if not music_samples:
            raise MixError("The music track has no samples.")
        loop_samples = None
        if not music.get("arrangement") and music_samples * SAMPLE_RATE < samples * music_rate:
            loop_samples = music_samples
        backing = source(music_path, loop_samples=loop_samples)
        volume = _music_volume(project, gain)
        fade = min(int(SAMPLE_RATE * 0.33), samples // 4)
        fades = f",afade=t=in:ns={fade},afade=t=out:ss={samples - fade}:ns={fade}"
        track = f"{backing},{volume},{window}{fades}"
        if voice:
            filters.append(f"{track},equalizer=f=2300:t=o:w=2:g=-4[music]")
            filters.append(
                "[music][sidechain]sidechaincompress=threshold=0.035:ratio=6:"
                "attack=12:release=900:link=maximum:detection=rms[backing]"
            )
        else:
            filters.append(f"{track}[backing]")
        outputs.append("[backing]")

    for index, effect in enumerate(mix.get("effects", []), start=1):
        label = f"Sound effect {index}"
        gain = _number(effect["gainDb"], f"{label} gain", -48, 0)
        start = _number(effect["start"], f"{label} start", 0, project["input"]["duration"])
        delay = round(start * SAMPLE_RATE)
        output = f"[effect{index}]"
        clip = source(_verified_wav(folder, "audio", effect["hash"], label))
        filters.append(f"{clip},volume={gain:.3f}dB,adelay={delay}S:all=1,atrim=end_sample={samples}{output}")
        outputs.append(output)

    if not outputs:
        return [
            "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
            "-filter_complex", f"[0:a]{window}[mix]",
        ]

    graph = "".join(outputs)
    if len(outputs) > 1:
        graph += f"amix=inputs={len(outputs)}:duration=longest:normalize=0,"
    filters.append(f"{graph}{window},asetpts=N/SR/TB[mix]")
    return [*options, "-filter_complex", ";".join(filters)]


def measure_levels(path, duration):
    """Measure decoded audio. Missing finite true peak denotes digital silence."""
    command = [
        media_tools.command("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
        "-xerror", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true:framelog=verbose",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=max(45, duration), **media_tools.process_options(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MixError("FFmpeg could not decode and measure the soundtrack.") from error
    summary = result.stderr.decode("utf-8", "replace").rpartition("Summary:")[2]
    fields = {"integratedLufs": "I", "loudnessRange": "LRA", "truePeakDbtp": "Peak"}
    levels = {}
    for key, label in fields.items():
        measured = re.search(rf"^\s*{label}:\s*({_METER_NUMBER})\s", summary, re.MULTILINE)
        if not measured:
            raise MixError("FFmpeg did not report complete soundtrack levels.")
        number = measured[1]
        levels[key] = None if number == "-inf" else float(number)
    # A soundtrack shorter than one BS.1770 gating block has no integrated reading.
    if duration < _METER_BLOCK_SECONDS or levels["integratedLufs"] == -70.0:
        levels["integratedLufs"] = None
    return levels


def _render_audio(inputs, output, duration, *options):
    command = [
        media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
        "-y", *inputs, *options, "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
        "-fflags", "+bitexact", "-flags:a", "+bitexact", str(output),
    ]
    try:
        subprocess.run(command, check=True, timeout=max(45, duration * 2), capture_output=True,
                       **media_tools.process_options())
    except (OSError, subprocess.SubprocessError) as error:
        raise MixError("FFmpeg could not produce the soundtrack.") from error


def _gain_to(target, measured):
    return 0.0 if measured is None else min(_MAX_GAIN_DB, target - measured)


def _master_pass(source, output, duration, samples, gain, peak, ceiling):
    filters = [f"volume={gain:.4f}dB"]
    if peak is not None and peak + gain > ceiling:
        limit = 10 ** (ceiling / 20)
        filters.extend((
            "aresample=192000",
            f"alimiter=limit={limit:.9f}:attack=5:release=120:level=false:latency=true",
            f"aresample={SAMPLE_RATE}",
        ))
    filters.append(f"apad=whole_len={samples},atrim=end_sample={samples}")
    _render_audio(["-i", str(source)], output, duration, "-af", ",".join(filters), "-c:a", "pcm_s16le")
    return measure_levels(output, duration)


def _aac_levels(master, work, duration):
    preview = work / "aac-check.m4a"
    _render_audio(["-i", str(master)], preview, duration, "-c:a", "aac", "-b:a", AAC_BITRATE)
    return measure_levels(preview, duration)


def _master(source, work, duration, samples, input_levels):
    gain = _gain_to(TARGET_LUFS, input_levels["integratedLufs"])
    ceiling = MASTER_CEILING_DBTP
    candidate, master = work / "trial.wav", work / "master.wav"
    best = float("inf")
    chosen = None
    for _ in range(_MASTER_ATTEMPTS):
        levels = _master_pass(source, candidate, duration, samples, gain,
                              input_levels["truePeakDbtp"], ceiling)
        loudness = levels["integratedLufs"]
        peak = levels["truePeakDbtp"]
        if peak is not None and peak > MASTER_CEILING_DBTP:
            ceiling -= peak - MASTER_CEILING_DBTP + 0.1
            continue
        error = 0 if loudness is None else TARGET_LUFS - loudness
        if abs(error) < best:
            best = abs(error)
            candidate.replace(master)
            chosen = levels, gain
        if abs(error) <= 0.2:
            break
        next_gain = min(_MAX_GAIN_DB, gain + error)
        if abs(next_gain - gain) < 0.05:
            break
        gain = next_gain
    if chosen is None:
        raise MixError("The soundtrack could not be mastered within the true-peak limit.")

    levels, gain = chosen
    encoded = _aac_levels(master, work, duration)
    peak = encoded["truePeakDbtp"]
    if peak is not None and peak > DELIVERY_CEILING_DBTP:
        correction = DELIVERY_CEILING_DBTP - peak - 0.2
        levels = _master_pass(master, candidate, duration, samples, correction, levels["truePeakDbtp"],
                              MASTER_CEILING_DBTP)
        candidate.replace(master)
        gain += correction
        encoded = _aac_levels(master, work, duration)
        if encoded["truePeakDbtp"] is not None and encoded["truePeakDbtp"] > DELIVERY_CEILING_DBTP:
            raise MixError("The soundtrack's AAC encode exceeds the true-peak ceiling.")

    loudness = encoded["integratedLufs"]
    status = "target"
    if loudness is None:
        status = "silence" if encoded["truePeakDbtp"] is None else "unmetered"
    elif abs(loudness - TARGET_LUFS) > LOUDNESS_TOLERANCE_LU:
        status = "outside-target"
    record = {
        "targetLufs": TARGET_LUFS, "masterCeilingDbtp": MASTER_CEILING_DBTP,
        "deliveryCeilingDbtp": DELIVERY_CEILING_DBTP, "appliedGainDb": round(gain, 3),
        "status": status, "aacCheck": encoded,
    }
    return master, levels, record


def verify_delivery(path, duration, approved_mix):
    """Verify the actual encoded AAC against the mix and its recorded mastering check."""
    levels = measure_levels(path, duration)
    if not (record := approved_mix.get("mastering")):
        return levels
    loudness, peak = levels["integratedLufs"], levels["truePeakDbtp"]
    expected = record["aacCheck"]["integratedLufs"]
    if (loudness is None) != (expected is None) or (
        loudness is not None and abs(loudness - expected) > LOUDNESS_TOLERANCE_LU
    ):
        raise MixError("The encoded audio loudness differs from the approved soundtrack.")
    if (peak is not None and peak > DELIVERY_CEILING_DBTP) or (
        record["status"] == "target" and (
            loudness is None or abs(loudness - TARGET_LUFS) > LOUDNESS_TOLERANCE_LU
        )
    ):
        raise MixError("The encoded audio did not meet the approved loudness and true-peak targets.")
    return levels


def prepare_mix(project, folder, destination, frame_count, fps):
    if not media_tools.available():
        raise MixError("Preparing the soundtrack needs FFmpeg and FFprobe.")
    samples = _sample_count(frame_count, fps)
    duration = samples / SAMPLE_RATE
    voice = project.get("audio")
    narration = None
    voice_gain = 0.0
    if voice:
        narration_path = _verified_wav(folder, "audio", voice["hash"], "Narration")
        voice_samples, voice_rate, _, _ = _wav_format(narration_path)
        voice_duration = voice_samples / voice_rate
        if (not voice_samples or voice_duration > duration
                or abs(voice_duration - project["input"]["duration"]) > 1 / voice_rate):
            raise MixError("The cut does not cover the complete recording.")
        voice_levels = measure_levels(narration_path, voice_duration)
        voice_gain = _gain_to(DIALOGUE_LUFS, voice_levels["integratedLufs"])
        narration = {
            "samples": voice_samples, "sampleRate": voice_rate, "duration": voice_duration,
            "integratedLufs": voice_levels["integratedLufs"], "truePeakDbtp": voice_levels["truePeakDbtp"],
        }
    options = _sound_options(project, folder, samples, voice_gain)
    destination = writable_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".bacchus-mix-", dir=destination.parent) as work:
        work = Path(work)
        combined = work / "combined.wav"
        _render_audio(options, combined, duration, "-map", "[mix]", "-c:a", "pcm_f32le", "-rf64", "auto")
        combined_levels = measure_levels(combined, duration)
        output, levels, mastering = _master(combined, work, duration, samples, combined_levels)
        if _wav_format(output) != (samples, SAMPLE_RATE, CHANNELS, 2):
            raise MixError("The soundtrack does not cover the complete cut in PCM.")
        warnings = []
        if narration and narration["truePeakDbtp"] is None:
            warnings.append("The narration recording has no measurable peak signal.")
        if mastering["status"] == "silence" and narration:
            warnings.append("The soundtrack has no measurable peak signal.")
        elif mastering["status"] == "unmetered":
            warnings.append("This soundtrack has no reliable integrated loudness reading.")
        elif mastering["status"] == "outside-target":
            warnings.append(
                f"The AAC check measured {mastering['aacCheck']['integratedLufs']:.1f} LUFS. "
                f"The target is {TARGET_LUFS:.0f} LUFS. Listen before approving the soundtrack."
            )
        if narration and (project["mix"].get("musicMode") == "track" or project["mix"].get("effects")):
            unmastered = combined_levels["integratedLufs"]
            if unmastered is not None and unmastered > DIALOGUE_LUFS + 3:
                warnings.append("Listen for music or effects masking words.")
        digest = _sha256(output)
        output.replace(destination)
    return {
        "sha256": digest, "duration": duration, "sampleRate": SAMPLE_RATE, "channels": CHANNELS,
        "samples": samples, "levels": levels, "narration": narration, "warnings": warnings, "mastering": mastering,
    }
