from io import BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
import wave

from bacchus import audio, providers, settings


def stereo_wav():
    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as file:
            file.setparams((2, 2, 16_000, 0, "NONE", "PCM"))
            file.writeframes(b"\x00\x08\xff\xf8" * 32_000)
        return buffer.getvalue()


@unittest.skipUnless(audio.audio_available(), "FFmpeg is required")
class AudioTests(unittest.TestCase):
    def test_imports_declared_audio_formats(self):
        with TemporaryDirectory(prefix="bacchus-audio-test-") as work:
            source = Path(work) / "source.wav"
            source.write_bytes(stereo_wav())
            inputs = [source]
            for suffix in ("flac", "mp3"):
                converted = Path(work) / f"source.{suffix}"
                result = subprocess.run(
                    ["ffmpeg", "-v", "error", "-nostdin", "-i", str(source), str(converted)],
                    capture_output=True, timeout=20,
                )
                if result.returncode == 0:
                    inputs.append(converted)
            for input_path in inputs:
                with self.subTest(format=input_path.suffix):
                    normalized, duration = audio.normalize_audio(input_path.read_bytes(), input_path.name, 8)
                    self.assertAlmostEqual(duration, 2, places=2)
                    with wave.open(BytesIO(normalized), "rb") as recording:
                        self.assertEqual((recording.getnchannels(), recording.getframerate()), (1, 24_000))
            self.assertTrue(any(path.suffix == ".flac" for path in inputs))

    def test_preserves_stereo_and_enforces_upload_bounds(self):
        normalized, _ = audio.normalize_audio(stereo_wav(), "A Song.WAV", 4, voice=False)
        with wave.open(BytesIO(normalized), "rb") as recording:
            self.assertEqual((recording.getnchannels(), recording.getframerate()), (2, 48_000))
        with self.assertRaisesRegex(audio.AudioError, "FLAC"):
            audio.normalize_audio(stereo_wav(), "spoof.flac", 4)
        with self.assertRaisesRegex(audio.AudioError, "between"):
            audio.normalize_audio(stereo_wav(), "long.wav", 1)

    def test_transcription_uploads_a_compact_copy_and_checks_response(self):
        response = MagicMock()
        response.headers = {"x-request-id": "test-transcription"}
        response.getcode.return_value = 200
        response.read.return_value = json.dumps({
            "text": "A voice was recorded. The recording was transcribed.",
            "usage": {"type": "duration", "seconds": 2},
        }).encode("utf-8")
        opener = MagicMock()
        opener.open.return_value = response
        with TemporaryDirectory(prefix="bacchus-transcribe-test-") as work, patch.dict(
            "os.environ", {
                "OPENAI_API_KEY": "test-only",
                "BACCHUS_TRANSCRIBE_MODEL": "gpt-transcribe-fixed",
                "BACCHUS_USE_OPENAI_TRANSCRIPTION": "1",
            },
        ), patch.object(providers, "build_opener", return_value=opener), patch.object(
            settings, "api_log_directory", return_value=Path(work) / "audit",
        ):
            original = Path(work) / "private name.wav"
            original.write_bytes(stereo_wav())
            transcript = audio.transcribe(original)
            request = opener.open.call_args.args[0]
            self.assertEqual(transcript["model"], "gpt-transcribe-fixed")
            self.assertEqual(transcript["timing"], "estimated")
            self.assertEqual(transcript["requestId"], "test-transcription")
            self.assertIn("The recording was transcribed.", transcript["script"])
            self.assertEqual(request.full_url, "https://api.openai.com/v1/audio/transcriptions")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only")
            self.assertIn(b'filename="narration.m4a"', request.data)
            self.assertIn(b"ftypM4A", request.data)
            self.assertNotIn(b"private name", request.data)
            [logged_request] = (Path(work) / "audit").glob("*/request.body")
            self.assertEqual(logged_request.read_bytes(), request.data)
            response.read.return_value = b'{"text":" "}'
            with self.assertRaisesRegex(audio.AudioError, "no narration"):
                audio.transcribe(original)

    def test_disabled_transcription_stops_before_encoding(self):
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-only", "BACCHUS_USE_OPENAI_TRANSCRIPTION": "0"}),
            patch.object(audio, "_convert") as convert,
            patch.object(audio, "send_request") as send,
        ):
            with self.assertRaisesRegex(audio.AudioError, "Enable OpenAI transcription"):
                audio.transcribe(Path("recording.wav"))
            convert.assert_not_called()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
