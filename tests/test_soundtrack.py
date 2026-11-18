import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import wave

import numpy as np

from bacchus import media_tools, mixing, score


def write_wav(path, samples):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, mixing.SAMPLE_RATE, len(samples), "NONE", "not compressed"))
        output.writeframes(np.rint(samples * 32_767).astype("<i2").tobytes())


class SoundtrackTests(unittest.TestCase):
    @unittest.skipUnless(media_tools.available(), "FFmpeg and FFprobe are required")
    def test_master_normalizes_dialogue_and_verifies_encoded_audio(self):
        with TemporaryDirectory(prefix="bacchus-master-test-") as temporary:
            folder = Path(temporary)
            (folder / "audio").mkdir()
            voice = folder / "voice.wav"
            duration = 4
            rate = mixing.SAMPLE_RATE
            time = np.arange(rate * duration) / rate
            speech = 0.038 * np.sin(2 * np.pi * 357 * time) * (0.65 + 0.35 * np.sin(7 * time))
            speech[rate:rate + 24] += 0.9 * np.hanning(24)
            write_wav(voice, speech)
            original = voice.read_bytes()
            key = hashlib.sha256(original).hexdigest()
            source = folder / "audio" / (key + ".wav")
            voice.replace(source)
            project = {
                "input": {"duration": duration, "lines": [{"start": 0}]},
                "audio": {"hash": key},
                "mix": {"musicMode": "none", "effects": []},
            }
            master = folder / "master.wav"
            metadata = mixing.prepare_mix(project, folder, master, duration * 60, 60)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(metadata["samples"], rate * duration)
            self.assertLess(metadata["narration"]["integratedLufs"], -20)
            self.assertEqual(metadata["mastering"]["status"], "target")
            self.assertLessEqual(metadata["levels"]["truePeakDbtp"], mixing.MASTER_CEILING_DBTP)

            encoded = folder / "checked.m4a"
            mixing._render_audio(["-i", str(master)], encoded, duration, "-c:a", "aac", "-b:a", mixing.AAC_BITRATE)
            actual = mixing.verify_delivery(encoded, duration, metadata)
            self.assertAlmostEqual(actual["integratedLufs"], -14, delta=0.5)
            self.assertLessEqual(actual["truePeakDbtp"], mixing.DELIVERY_CEILING_DBTP)
            with self.assertRaisesRegex(mixing.MixError, "differs from the approved soundtrack"):
                mixing.verify_delivery(source, duration, metadata)

    @unittest.skipUnless(media_tools.available(), "FFmpeg and FFprobe are required")
    def test_silence_stays_silent(self):
        with TemporaryDirectory(prefix="bacchus-silence-test-") as temporary:
            folder = Path(temporary)
            project = {"input": {"duration": 1, "lines": [{"start": 0}]},
                       "audio": None, "mix": {"musicMode": "none", "effects": []}}
            output = folder / "mix.wav"
            result = mixing.prepare_mix(project, folder, output, 60, 60)
            self.assertEqual(result["mastering"]["status"], "silence")
            self.assertIsNone(result["levels"]["integratedLufs"])
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.readframes(audio.getnframes()), bytes(mixing.SAMPLE_RATE * 4))


class ScoreTests(unittest.TestCase):
    def test_arrangement_uses_recording_pauses_and_is_repeatable(self):
        project = {
            "input": {"duration": 22.0, "lines": [{"start": 0.0}, {"start": 8.0}, {"start": 18.0}],
                      "words": [{"start": 0, "end": 7.5}, {"start": 8, "end": 17.6}, {"start": 18, "end": 21.4}]},
            "plan": {"scenes": [{"startLine": i, "title": f"Scene {i}"} for i in range(1, 4)]},
        }
        plan = score.arrangement(project, "warm")
        self.assertEqual([row["start"] for row in plan["sections"]], [0, 7.75, 17.8])
        self.assertEqual([row["anchor"] for row in plan["sections"]], ["opening", "pause", "pause"])
        with TemporaryDirectory(prefix="bacchus-score-test-") as temporary:
            folder = Path(temporary)
            first, second, pulse = (folder / f"{name}.wav" for name in ("first", "second", "pulse"))
            self.assertEqual(score.write_arrangement(first, plan), 22)
            score.write_arrangement(second, plan)
            score.write_arrangement(pulse, score.arrangement(project, "pulse"))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertNotEqual(first.read_bytes(), pulse.read_bytes())
            with wave.open(str(first), "rb") as music:
                self.assertEqual(music.getnframes(), 22 * score.SAMPLE_RATE)
                self.assertNotEqual(music.readframes(score.SAMPLE_RATE), bytes(2 * score.SAMPLE_RATE))


if __name__ == "__main__":
    unittest.main()
