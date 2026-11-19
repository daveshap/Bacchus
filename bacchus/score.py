"""Cue-arranged stereo editorial scores, backing beds, and transition accents."""

from bisect import bisect_left
from functools import lru_cache
from io import BytesIO
from math import ceil, exp, isfinite, pi, sin, tau
import wave

import numpy as np

from .storage_paths import writable_path


SAMPLE_RATE = 48_000
CHANNELS = 2
PRESETS = {"ambient": "Night Tide", "pulse": "Quiet Current"}
ARRANGEMENT_STYLES = {
    "ambient": "Ambient",
    "pulse": "Pulse",
    "warm": "Warm",
    "minimal": "Minimal",
}
_STYLE_SETTINGS = {
    "ambient": (64, 0, 0.56, 0.24, 0.0, 0.34),
    "pulse": (86, 2, 0.44, 0.30, 0.22, 0.26),
    "warm": (72, 5, 0.52, 0.36, 0.06, 0.32),
    "minimal": (58, -2, 0.34, 0.16, 0.0, 0.38),
}
_PROGRESSIONS = {
    "opening": ((48, 55, 60, 64, 67), (45, 52, 57, 60, 64), (41, 48, 53, 57, 60), (43, 50, 55, 59, 62)),
    "development": ((45, 52, 60, 64, 67), (41, 53, 57, 60, 64), (48, 55, 62, 64, 67), (43, 50, 59, 62, 67)),
    "reflection": ((50, 57, 60, 65, 69), (45, 52, 59, 64, 67), (41, 48, 55, 60, 64), (43, 55, 60, 62, 67)),
    "resolution": ((41, 48, 57, 60, 64), (43, 50, 59, 62, 67), (48, 55, 60, 64, 67)),
}
_MAX_SECTIONS = 18
_MIN_PAUSE = 0.22
CHORDS = ((48, 55, 62, 64, 67), (45, 52, 59, 60, 64), (41, 48, 55, 60, 64), (43, 50, 57, 62, 67))


def _frequency(note):
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


def _stereo_reverb(left, right, wet=0.3):
    """Lightweight deterministic stereo hall diffusion (comb taps + cross-channel early reflections)."""
    if wet <= 0 or len(left) < 4096:
        return left, right
    out_l = left.copy()
    out_r = right.copy()
    taps = (
        (int(SAMPLE_RATE * 0.029), 0.34, True),
        (int(SAMPLE_RATE * 0.043), 0.27, False),
        (int(SAMPLE_RATE * 0.067), 0.21, True),
        (int(SAMPLE_RATE * 0.103), 0.16, False),
        (int(SAMPLE_RATE * 0.149), 0.11, True),
    )
    for delay, gain, cross in taps:
        if delay >= len(left):
            continue
        src_l, src_r = (right, left) if cross else (left, right)
        out_l[delay:] += wet * gain * src_l[:-delay]
        out_r[delay:] += wet * gain * src_r[:-delay]
    return out_l, out_r


def _encode_stereo_wav(left, right, peak_scale=26_000):
    interleaved = np.empty(len(left) * 2, dtype=np.float64)
    interleaved[0::2] = np.clip(left, -1.0, 1.0)
    interleaved[1::2] = np.clip(right, -1.0, 1.0)
    pcm = np.rint(interleaved * peak_scale).astype("<i2").tobytes()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as track:
        track.setparams((CHANNELS, 2, SAMPLE_RATE, len(left), "NONE", "not compressed"))
        track.writeframes(pcm)
    return buffer.getvalue(), len(left) / SAMPLE_RATE


def _piano_voice(time_arr, freq, decay=3.6):
    """Warm acoustic-style electric piano harmonic voice with soft hammer attack."""
    phase = tau * freq * time_arr
    attack = np.minimum(1.0, time_arr / 0.014)
    env = attack * np.exp(-decay * time_arr)
    body = (
        np.sin(phase)
        + 0.38 * np.sin(2.0 * phase) * np.exp(-1.5 * time_arr)
        + 0.14 * np.sin(3.0 * phase) * np.exp(-3.2 * time_arr)
        + 0.05 * np.sin(4.0 * phase) * np.exp(-5.0 * time_arr)
    )
    return env * body


@lru_cache(maxsize=len(PRESETS))
def soundtrack(preset):
    if preset not in PRESETS:
        raise ValueError("Unknown soundtrack preset.")
    chord_length = 3 if preset == "pulse" else 4
    frames_per_chord = SAMPLE_RATE * chord_length
    total_frames = frames_per_chord * len(CHORDS)
    left = np.zeros(total_frames, dtype=np.float64)
    right = np.zeros(total_frames, dtype=np.float64)
    t = np.arange(frames_per_chord, dtype=np.float64) / SAMPLE_RATE
    env = np.sin(pi * np.arange(frames_per_chord, dtype=np.float64) / frames_per_chord) ** 0.72
    for idx, chord in enumerate(CHORDS):
        start = idx * frames_per_chord
        end = start + frames_per_chord
        l_bar = np.zeros(frames_per_chord, dtype=np.float64)
        r_bar = np.zeros(frames_per_chord, dtype=np.float64)
        for n_idx, note in enumerate(chord):
            freq = _frequency(note)
            pan = 0.25 + 0.5 * (n_idx / max(1, len(chord) - 1))
            detune = 1.0 + (0.0014 if n_idx % 2 else -0.0014)
            voice_l = np.sin(tau * freq * t + 0.15 * n_idx) + 0.16 * np.sin(2.0 * tau * freq * t)
            voice_r = np.sin(tau * freq * detune * t + 0.4 * n_idx) + 0.16 * np.sin(2.0 * tau * freq * detune * t)
            l_bar += (1.0 - pan * 0.4) * voice_l
            r_bar += (0.6 + pan * 0.4) * voice_r
        l_bar *= 0.11 * env
        r_bar *= 0.11 * env
        # Gentle piano arpeggio articulation
        for beat_idx, note in enumerate(chord[1:4]):
            offset = int(frames_per_chord * beat_idx / 4)
            tail = t[:frames_per_chord - offset]
            voice = 0.14 * _piano_voice(tail, _frequency(note + 12), decay=2.8)
            pan = 0.35 + 0.3 * (beat_idx / 2)
            l_bar[offset:] += (1.0 - pan) * voice
            r_bar[offset:] += pan * voice
        if preset == "pulse":
            beat = np.remainder(t, 0.5)
            bass_env = np.minimum(1.0, beat / 0.015) * np.exp(-beat * 12.0)
            bass = 0.20 * env * bass_env * np.sin(tau * _frequency(chord[0] - 12) * beat)
            l_bar += bass
            r_bar += bass
        left[start:end] = l_bar
        right[start:end] = r_bar
    left, right = _stereo_reverb(left, right, wet=0.30)
    return _encode_stereo_wav(left, right)


@lru_cache(maxsize=1)
def accent():
    frames = SAMPLE_RATE // 3
    t = np.arange(frames, dtype=np.float64) / SAMPLE_RATE
    attack = np.minimum(1.0, t * 220.0)
    env = attack * np.exp(-13.5 * t)
    sig_l = env * (0.34 * np.sin(tau * 392.0 * t) + 0.16 * np.sin(tau * 587.33 * t) + 0.06 * np.sin(tau * 784.0 * t))
    sig_r = env * (0.32 * np.sin(tau * 392.4 * t) + 0.18 * np.sin(tau * 587.33 * t) + 0.07 * np.sin(tau * 784.5 * t))
    sig_l, sig_r = _stereo_reverb(sig_l, sig_r, wet=0.22)
    return _encode_stereo_wav(sig_l, sig_r, peak_scale=28_000)


def arrangement(project, style):
    """Build a score plan on approved scene boundaries and the pauses leading into them."""
    if style not in ARRANGEMENT_STYLES:
        raise ValueError("Choose a supported orchestration for the original score.")
    source = project["input"]
    duration = source["duration"]
    if not isfinite(duration) or duration <= 0:
        raise ValueError("An original score needs the complete cue timeline.")
    scenes = project["plan"]["scenes"]
    lines = source["lines"]
    words = source.get("words") or ()
    starts = [word["start"] for word in words]
    sections = [{"start": 0.0, "scene": 1, "title": scenes[0]["title"], "anchor": "opening"}]
    minimum = min(8.0, max(1.5, duration / 12))
    for scene_index, scene in enumerate(scenes[1:], 2):
        line = scene["startLine"]
        start = lines[line - 1]["start"]
        anchor = "scene"
        word_index = bisect_left(starts, start)
        if 0 < word_index < len(words) and words[word_index]["start"] == start:
            pause = start - words[word_index - 1]["end"]
            if pause >= _MIN_PAUSE:
                start -= min(pause / 2, 0.7)
                anchor = "pause"
        if start - sections[-1]["start"] >= minimum and duration - start >= minimum / 2:
            sections.append({"start": start, "scene": scene_index, "title": scene["title"], "anchor": anchor})
    if len(sections) == 1 and duration >= 12:
        pauses = [(left["end"] + right["start"]) / 2 for left, right in zip(words, words[1:])
                  if right["start"] - left["end"] >= _MIN_PAUSE
                  and duration * 0.55 <= right["start"] <= duration - minimum]
        if pauses:
            start = min(pauses, key=lambda time: abs(time - duration * 0.75))
            sections.append({"start": start, "scene": 1, "title": scenes[0]["title"], "anchor": "pause"})
    if len(sections) > _MAX_SECTIONS:
        last = len(sections) - 1
        sections = [sections[round(index * last / (_MAX_SECTIONS - 1))] for index in range(_MAX_SECTIONS)]
    for index, section in enumerate(sections):
        section["character"] = (
            "opening" if index == 0 else
            "resolution" if index == len(sections) - 1 else
            "reflection" if index % 3 == 2 else
            "development"
        )
    return {"version": 2, "style": style, "duration": duration, "sections": sections}


def _measure_samples(settings, chord, frames, measure_frames, offset, section_frames):
    _, transpose, pad_level, piano_level, pulse_level, reverb_wet = settings
    time = np.arange(frames, dtype=np.float64) / SAMPLE_RATE
    bar = measure_frames / SAMPLE_RATE
    left = np.zeros(frames, dtype=np.float64)
    right = np.zeros(frames, dtype=np.float64)

    # Warm stereo detuned string/pad bed
    for index, note in enumerate(chord):
        freq = _frequency(note + transpose)
        pan = 0.2 + 0.6 * (index / max(1, len(chord) - 1))
        ph_l = tau * freq * time + 0.14 * index
        ph_r = tau * (freq * (1.0 + (0.0012 if index % 2 else -0.0012))) * time + 0.35 * index
        over_l = 0.18 * np.sin(2.0 * ph_l) + 0.06 * np.sin(3.0 * ph_l) * np.exp(-0.9 * time)
        over_r = 0.18 * np.sin(2.0 * ph_r) + 0.06 * np.sin(3.0 * ph_r) * np.exp(-0.9 * time)
        gain = pad_level / len(chord)
        left += gain * (1.0 - pan * 0.45) * (np.sin(ph_l) + over_l)
        right += gain * (0.55 + pan * 0.45) * (np.sin(ph_r) + over_r)
    pad_fade = np.minimum(1.0, np.minimum(time / 0.28, (bar - time) / 0.52))
    left *= pad_fade
    right *= pad_fade

    # Warm bass foundation
    bass_freq = _frequency(chord[0] + transpose - 12)
    bass_env = np.minimum(1.0, time / 0.18) * np.exp(-0.45 * time) * pad_fade
    bass = 0.16 * pad_level * bass_env * (np.sin(tau * bass_freq * time) + 0.2 * np.sin(2.0 * tau * bass_freq * time))
    left += bass
    right += bass

    # Articulated warm piano motif notes across the measure
    motif = ((0, chord[2], 0.38), (1, chord[4], 0.62), (2, chord[3], 0.44), (3, chord[1], 0.58))
    for beat_slot, note, pan in motif:
        first = measure_frames * beat_slot // 4
        end = min(frames, measure_frames * (beat_slot + 1) // 4 + measure_frames // 6)
        if first >= frames or end <= first:
            continue
        tail = time[:end - first]
        voice = piano_level * _piano_voice(tail, _frequency(note + transpose + 12), decay=3.4)
        tail_fade = np.minimum(1.0, (tail[-1] - tail + 1.0 / SAMPLE_RATE) / 0.06)
        voice *= tail_fade
        left[first:end] += (1.0 - pan * 0.5) * voice
        right[first:end] += (0.5 + pan * 0.5) * voice

    if pulse_level:
        beat = np.remainder(time, bar / 4.0)
        p_env = np.minimum(1.0, beat / 0.016) * np.exp(-beat * 11.5)
        pulse_sig = pulse_level * p_env * np.sin(tau * bass_freq * beat)
        left += pulse_sig
        right += pulse_sig

    left, right = _stereo_reverb(left, right, wet=reverb_wet)
    section_time = time + offset / SAMPLE_RATE
    fade = np.minimum(1.0, np.minimum(section_time / 0.36, (section_frames / SAMPLE_RATE - section_time) / 0.55))
    left *= fade
    right *= fade
    interleaved = np.empty(frames * 2, dtype=np.float64)
    interleaved[0::2] = np.clip(left, -1.0, 1.0)
    interleaved[1::2] = np.clip(right, -1.0, 1.0)
    return np.rint(interleaved * 25_000).astype("<i2").tobytes()


def write_arrangement(destination, plan):
    """Render the section plan to 48 kHz stereo PCM, keeping only one bar in memory at a time."""
    settings = _STYLE_SETTINGS[plan["style"]]
    duration = plan["duration"]
    sections = plan["sections"]
    starts = [round(section["start"] * SAMPLE_RATE) for section in sections]
    frames = ceil(duration * SAMPLE_RATE)
    if not starts or starts[0] != 0 or any(end <= start for start, end in zip(starts, [*starts[1:], frames])):
        raise ValueError("The original score sections must grow across the complete film.")
    measure_frames = round(SAMPLE_RATE * 4 * 60 / settings[0])
    with writable_path(destination).open("xb") as file, wave.open(file, "wb") as track:
        track.setparams((CHANNELS, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
        for index, section in enumerate(sections):
            section_frames = (starts[index + 1] if index + 1 < len(starts) else frames) - starts[index]
            chords = _PROGRESSIONS[section["character"]]
            for bar, offset in enumerate(range(0, section_frames, measure_frames)):
                remaining = section_frames - offset
                chord = chords[bar % len(chords)]
                if remaining <= measure_frames and index == len(sections) - 1:
                    chord = _PROGRESSIONS["resolution"][-1]
                track.writeframesraw(_measure_samples(settings, chord, min(remaining, measure_frames),
                                                       measure_frames, offset, section_frames))
    return frames / SAMPLE_RATE
