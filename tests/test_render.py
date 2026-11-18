from array import array
import hashlib
from io import BytesIO
import math
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import wave

from PIL import Image

from bacchus import render


def project():
    return {
        "input": {"duration": 3.0, "lines": [{"start": 0.0}]},
        "plan": {"scenes": [{
            "startLine": 1, "endLine": 1, "layout": "title", "motion": "still",
            "title": "The first cut", "caption": "A sound composition test.",
        }]},
        "audio": None,
        "assets": [],
    }


def tone(folder, name, frequency, duration, *, start=0.0, end=None, channels=2):
    rate = 16_000
    end = duration if end is None else end
    samples = array("h")
    for index in range(round(rate * duration)):
        time = index / rate
        value = round(15_000 * math.sin(math.tau * frequency * time)) if start <= time < end else 0
        samples.extend([value] * channels)
    if sys.byteorder != "little":
        samples.byteswap()
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as stream:
            stream.setparams((channels, 2, rate, 0, "NONE", "PCM"))
            stream.writeframes(samples.tobytes())
        raw = buffer.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    (folder / "audio" / f"{digest}.wav").write_bytes(raw)
    return {"name": f"{name}.wav", "hash": digest, "duration": duration}


def left_channel(pcm):
    samples = array("h", pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples[::2]


def frequency_level(samples, frequency, start, end):
    rate = render.SAMPLE_RATE
    window = samples[round(start * rate):round(end * rate)]
    phase = math.tau * frequency / rate
    real = sum(sample * math.cos(index * phase) for index, sample in enumerate(window))
    imaginary = sum(sample * math.sin(index * phase) for index, sample in enumerate(window))
    return math.hypot(real, imaginary) * 2 / len(window)


class RendererTests(unittest.TestCase):
    def test_opening_scene_animates_during_narration_lead_in(self):
        source = project()
        source["input"]["lines"] = [{"start": 0.8}, {"start": 1.5}]
        first = source["plan"]["scenes"][0]
        first["motion"] = "fade"
        source["plan"]["scenes"].append({
            **first, "startLine": 2, "endLine": 2, "motion": "still", "title": "The second cut",
        })
        folder = Path(".")
        during_lead_in = render.preview(source, folder, 0.28)
        self.assertNotEqual(during_lead_in, render.preview(source, folder, 0.0))
        before_boundary = render.preview(source, folder, 1.49)
        self.assertNotEqual(before_boundary, render.preview(source, folder, 1.5))
        source["input"]["lines"][0]["start"] = 0.0
        self.assertEqual(during_lead_in, render.preview(source, folder, 0.28))

    def test_style_choices_render_distinct_palettes(self):
        colors = set()
        for style in ("warm-editorial", "studio-ink", "night-signal"):
            picture = render.preview({**project(), "style": style}, Path("."), 0)
            with Image.open(BytesIO(picture)) as frame:
                self.assertEqual(frame.size, (1280, 720))
                colors.add(frame.getpixel((0, 0)))
        self.assertEqual(len(colors), 3)

    @unittest.skipUnless(render.render_available(), "FFmpeg and FFprobe are required")
    def test_mix_and_mp4_duck_loop_and_place_sound(self):
        with TemporaryDirectory(prefix="bacchus-sound-test-") as temporary:
            folder = Path(temporary)
            (folder / "audio").mkdir()
            source = {
                **project(),
                "audio": tone(folder, "voice", 400, 3, start=0.75, end=1.5, channels=1),
                "mix": {
                    "musicMode": "track",
                    "music": {**tone(folder, "music", 650, 0.4), "gainDb": -10},
                    "effects": [{**tone(folder, "effect", 1200, 0.2),
                                 "id": "bell", "start": 2.3, "gainDb": -6}],
                    "approved": True,
                },
            }
            wav_path = folder / "mix.wav"
            mix = render.export_mix(source, folder, wav_path)
            self.assertEqual((mix["duration"], mix["sampleRate"], mix["channels"], mix["samples"]),
                             (3.0, 48_000, 2, 144_000))
            self.assertEqual(mix["sha256"], hashlib.sha256(wav_path.read_bytes()).hexdigest())
            self.assertLess(mix["levels"]["truePeakDbtp"], 0)
            self.assertEqual(mix["narration"]["samples"], 48_000)
            with wave.open(str(wav_path), "rb") as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getnframes()), (48_000, 2, 144_000))
                pcm = left_channel(wav.readframes(wav.getnframes()))
            self.check_composition(pcm)

            mp4_path = folder / "video.mp4"
            with self.assertRaisesRegex(render.RenderError, "approve the soundtrack"):
                render.export_video(source, folder, mp4_path)
            (folder / "mixes").mkdir()
            prepared = folder / "mixes" / f"{mix['sha256']}.wav"
            wav_path.replace(prepared)
            for stem in (folder / "audio").iterdir():
                stem.unlink()
            media = render.export_video(source, folder, mp4_path, approved_mix=mix)
            self.assertEqual((media["audio"], media["audioSampleRate"], media["audioChannels"]),
                             ("aac", 48_000, 2))
            decoded = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(mp4_path), "-map", "0:a:0", "-f", "s16le", "pipe:1"],
                capture_output=True, check=True, timeout=15,
            )
            self.check_composition(left_channel(decoded.stdout))

            prepared.write_bytes(b"changed after approval")
            with self.assertRaisesRegex(render.RenderError, "fingerprint"):
                render.export_video(source, folder, mp4_path, approved_mix=mix)

    @unittest.skipUnless(render.render_available(), "FFmpeg and FFprobe are required")
    def test_music_regions_mute_backing_without_cutting_narration(self):
        with TemporaryDirectory(prefix="bacchus-music-regions-") as temporary:
            folder = Path(temporary)
            (folder / "audio").mkdir()
            source = project()
            source["input"] = {"duration": 3.011, "lines": [{"start": 0.0}, {"start": 1.0}, {"start": 2.0}]}
            regions = [{"startLine": 2, "endLine": 2, "gainDb": None}]
            source["audio"] = tone(folder, "voice", 400, 3.011)
            source["mix"] = {
                "musicMode": "track", "music": {**tone(folder, "score", 650, 0.4), "gainDb": -10},
                "musicRegions": regions, "effects": [],
            }
            self.assertEqual(render.validate_music_regions(source, regions), regions)
            with self.assertRaisesRegex(render.RenderError, "overlap"):
                render.validate_music_regions(source, [*regions, {"startLine": 1, "endLine": 2, "gainDb": -25}])
            result = render.export_mix(source, folder, folder / "mix.wav")
            self.assertEqual(result["narration"]["samples"], 48_176)
            self.assertEqual(result["samples"], 146_000)
            with wave.open(str(folder / "mix.wav"), "rb") as wav:
                pcm = left_channel(wav.readframes(wav.getnframes()))
            self.assertGreater(frequency_level(pcm, 650, 0.5, 0.7), 100)
            self.assertLess(frequency_level(pcm, 650, 1.3, 1.5), 10)
            self.assertGreater(frequency_level(pcm, 650, 2.3, 2.5), 100)
            self.assertGreater(frequency_level(pcm, 400, 1.3, 1.5), 8_000)
            self.assertGreater(frequency_level(pcm, 400, 3.0, 3.01), 7_000)

    def check_composition(self, samples):
        background = frequency_level(samples, 650, 0.5, 0.65)
        under_voice = frequency_level(samples, 650, 1.15, 1.35)
        later_background = frequency_level(samples, 650, 2.03, 2.23)
        self.assertGreater(background, 1500)
        self.assertLess(under_voice, background * 0.45)
        self.assertGreater(later_background, background * 0.65)
        self.assertGreater(frequency_level(samples, 400, 1.15, 1.35), 6000)
        self.assertLess(frequency_level(samples, 1200, 2.13, 2.23), 150)
        self.assertGreater(frequency_level(samples, 1200, 2.35, 2.45), 4500)


if __name__ == "__main__":
    unittest.main()
