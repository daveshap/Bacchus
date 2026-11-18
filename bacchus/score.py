"""Original procedural backing music and a short transition accent."""

from array import array
from functools import lru_cache
from io import BytesIO
from math import exp, pi, sin, tau
from sys import byteorder
import wave


SAMPLE_RATE = 24_000
PRESETS = {"ambient": "Night Tide", "pulse": "Quiet Current"}
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
