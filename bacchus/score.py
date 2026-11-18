"""Cue-arranged synth scores, generic backing beds, and a transition accent."""

from array import array
from bisect import bisect_left
from functools import lru_cache
from io import BytesIO
from math import ceil, exp, isfinite, pi, sin, tau
from sys import byteorder
import wave

import numpy as np


SAMPLE_RATE = 24_000
PRESETS = {"ambient": "Night Tide", "pulse": "Quiet Current"}
ARRANGEMENT_STYLES = {
    "ambient": "Ambient",
    "pulse": "Pulse",
    "warm": "Warm",
    "minimal": "Minimal",
}
_STYLE_SETTINGS = {
    "ambient": (62, 0, 0.66, 0.18, 0.0),
    "pulse": (84, 2, 0.49, 0.30, 0.22),
    "warm": (70, 5, 0.55, 0.36, 0.0),
    "minimal": (57, -2, 0.31, 0.12, 0.0),
}
_PROGRESSIONS = {
    "opening": ((48, 55, 60, 64), (45, 52, 59, 64), (41, 48, 55, 60), (43, 50, 57, 62)),
    "development": ((45, 52, 60, 64), (41, 53, 57, 60), (48, 55, 62, 64), (43, 50, 59, 62)),
    "reflection": ((50, 57, 60, 65), (45, 52, 59, 64), (41, 48, 55, 60), (43, 55, 60, 62)),
    "resolution": ((41, 48, 57, 60), (43, 50, 59, 62), (48, 55, 60, 64)),
}
_MAX_SECTIONS = 18
_MIN_PAUSE = 0.22
CHORDS = ((48, 55, 62, 64), (45, 52, 59, 60), (41, 48, 55, 60), (43, 50, 57, 62))


def _wav(samples):
    audio = array("h", (round(sample * 30_000) for sample in samples))
    if byteorder == "big":
        audio.byteswap()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as track:
        track.setparams((1, 2, SAMPLE_RATE, len(audio), "NONE", "not compressed"))
        track.writeframes(audio.tobytes())
    return buffer.getvalue(), len(audio) / SAMPLE_RATE


def _frequency(note):
    return 440 * 2 ** ((note - 69) / 12)


def _loop(preset):
    chord_length = 3 if preset == "pulse" else 4
    frames = SAMPLE_RATE * chord_length
    for chord in CHORDS:
        frequencies = tuple(tau * _frequency(note) for note in chord)
        for frame in range(frames):
            t = frame / SAMPLE_RATE
            envelope = sin(pi * frame / frames) ** 0.7
            chord_sample = sum(sin(frequency * t) + 0.13 * sin(2 * frequency * t) for frequency in frequencies)
            sample = 0.10 * envelope * chord_sample
            if preset == "pulse":
                beat = t % 0.5
                sample += 0.24 * envelope * exp(-beat * 14) * sin(0.5 * frequencies[0] * beat)
            yield sample


@lru_cache(maxsize=len(PRESETS))
def soundtrack(preset):
    if preset not in PRESETS:
        raise ValueError("Unknown soundtrack preset.")
    return _wav(_loop(preset))


@lru_cache(maxsize=1)
def accent():
    def samples():
        for frame in range(SAMPLE_RATE // 3):
            t = frame / SAMPLE_RATE
            attack = min(1, t * 250)
            yield attack * exp(-15 * t) * (0.36 * sin(tau * 392 * t) + 0.17 * sin(tau * 587 * t))
    return _wav(samples())


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
        # A long single shot can still close at a real pause in the spoken material.
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
    return {"version": 1, "style": style, "duration": duration, "sections": sections}


def _measure_samples(settings, chord, frames, measure_frames, offset, section_frames):
    _, transpose, pad_level, pluck_level, pulse_level = settings
    time = np.arange(frames, dtype=np.float64) / SAMPLE_RATE
    bar = measure_frames / SAMPLE_RATE
    sample = np.zeros(frames)
    for index, note in enumerate(chord):
        phase = tau * _frequency(note + transpose) * time
        color = 0.15 * np.sin(2 * phase) + 0.055 * np.sin(3 * phase) * np.exp(-time)
        sample += pad_level * (np.sin(phase + 0.12 * index) + color) / len(chord)
    pad_fade = np.minimum(1, np.minimum(time / 0.26, (bar - time) / 0.5))
    sample *= pad_fade
    for index, note in ((0, chord[2]), (2, chord[3]), (3, chord[1])):
        first = measure_frames * index // 4
        end = min(frames, measure_frames * (index + 1) // 4)
        if end <= first:
            break
        tail = time[:end - first]
        phase = tau * _frequency(note + transpose + 12) * tail
        envelope = np.minimum(1, tail / 0.022) * np.exp(-6 * tail)
        envelope *= np.minimum(1, (time[end - 1] - time[first:end]) / 0.045)
        sample[first:end] += pluck_level * envelope * (np.sin(phase) + 0.26 * np.sin(2 * phase))
    if pulse_level:
        beat = np.remainder(time, bar / 4)
        envelope = np.minimum(1, beat / 0.018) * np.exp(-beat * 12)
        sample += pulse_level * envelope * np.sin(tau * _frequency(chord[0] + transpose - 12) * beat)
    section_time = time + offset / SAMPLE_RATE
    fade = np.minimum(1, np.minimum(section_time / 0.36, (section_frames / SAMPLE_RATE - section_time) / 0.55))
    return np.rint(np.clip(sample * fade, -1, 1) * 25_000).astype("<i2").tobytes()


def write_arrangement(destination, plan):
    """Render the section plan to mono PCM, keeping only one bar in memory at a time."""
    settings = _STYLE_SETTINGS[plan["style"]]
    duration = plan["duration"]
    sections = plan["sections"]
    starts = [round(section["start"] * SAMPLE_RATE) for section in sections]
    frames = ceil(duration * SAMPLE_RATE)
    if not starts or starts[0] != 0 or any(end <= start for start, end in zip(starts, [*starts[1:], frames])):
        raise ValueError("The original score sections must grow across the complete film.")
    measure_frames = round(SAMPLE_RATE * 4 * 60 / settings[0])
    with destination.open("xb") as file, wave.open(file, "wb") as track:
        track.setparams((1, 2, SAMPLE_RATE, frames, "NONE", "not compressed"))
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
