from pathlib import PurePosixPath
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bacchus import video_encoding


class VideoEncodingTests(unittest.TestCase):
    def test_platform_candidates_prioritize_nvidia_and_limit_backend_by_platform(self):
        windows = [item.name for item in video_encoding.hardware_candidates("win32")]
        self.assertEqual(windows[0], "h264_nvenc")
        self.assertSetEqual(set(windows[1:]), {"h264_qsv", "h264_amf"})

        macos = [item.name for item in video_encoding.hardware_candidates("darwin")]
        self.assertEqual(macos, ["h264_videotoolbox"])

    def test_linux_vaapi_candidates_require_a_drm_render_node(self):
        device = PurePosixPath("/dev/dri/renderD128")
        with patch.object(video_encoding.Path, "glob", return_value=[device]):
            candidates = list(video_encoding.hardware_candidates("linux"))
        self.assertEqual(candidates[0].name, "h264_nvenc")
        vaapi = [item for item in candidates if item.name == "h264_vaapi"]
        self.assertTrue(vaapi)
        self.assertTrue(any(option.endswith(f":{device}") for item in vaapi for option in item.input_options))

        with patch.object(video_encoding.Path, "glob", return_value=[]):
            names = [item.name for item in video_encoding.hardware_candidates("linux")]
        self.assertEqual(names, ["h264_nvenc"])

    def test_cpu_mode_skips_hardware_discovery_and_trial(self):
        with (
            patch.object(video_encoding, "hardware_candidates") as candidates,
            patch.object(video_encoding, "_available") as available,
            patch.object(video_encoding, "_trial") as trial,
        ):
            encoder, reason = video_encoding.choose("cpu", 1280, 720, 24)
        self.assertEqual(encoder, video_encoding.CPU)
        self.assertIsNone(reason)
        candidates.assert_not_called()
        available.assert_not_called()
        trial.assert_not_called()

    def test_conversion_and_tags_apply_to_software_and_hardware(self):
        with patch.object(video_encoding.Path, "glob", return_value=[PurePosixPath("/dev/dri/renderD128")]):
            for platform in ("win32", "darwin", "linux"):
                for encoder in (video_encoding.CPU, *video_encoding.hardware_candidates(platform)):
                    with self.subTest(encoder=encoder.name):
                        args = video_encoding.encoding_options(encoder)
                        self.assertEqual(args[-len(video_encoding.COLOR_OPTIONS):], list(video_encoding.COLOR_OPTIONS))
                        expected = f"scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format={encoder.pixel_format}"
                        if encoder.name == "h264_vaapi":
                            expected += ",hwupload"
                        self.assertEqual(args[args.index("-vf") + 1], expected)

    def test_auto_selects_nvidia_first_and_probes_the_requested_format(self):
        candidates = video_encoding.hardware_candidates("win32")
        with (
            patch.object(video_encoding, "hardware_candidates", return_value=candidates),
            patch.object(video_encoding, "_available", return_value={item.name for item in candidates}),
            patch.object(video_encoding, "_trial", return_value=True) as trial,
        ):
            encoder, reason = video_encoding.choose("auto", 3840, 2160, 60)
        self.assertEqual(encoder.name, "h264_nvenc")
        self.assertIsNone(reason)
        trial.assert_called_once_with(encoder, 3840, 2160, 60)

    def test_auto_skips_an_unlisted_encoder_and_continues_after_a_failed_trial(self):
        candidates = {item.name: item for item in video_encoding.hardware_candidates("win32")}
        order = [candidates[name] for name in ("h264_nvenc", "h264_qsv", "h264_amf")]
        with (
            patch.object(video_encoding, "hardware_candidates", return_value=order),
            patch.object(video_encoding, "_available", return_value={"h264_nvenc", "h264_amf"}),
            patch.object(video_encoding, "_trial", side_effect=[False, True]) as trial,
        ):
            encoder, _ = video_encoding.choose("auto", 1280, 720, 24)
        self.assertEqual(encoder.name, "h264_amf")
        self.assertEqual([call.args[0].name for call in trial.call_args_list], ["h264_nvenc", "h264_amf"])

    def test_auto_falls_back_to_cpu_when_no_hardware_encoder_works(self):
        candidates = video_encoding.hardware_candidates("win32")
        for listed in (set(), {item.name for item in candidates}):
            with (
                self.subTest(listed=listed),
                patch.object(video_encoding, "hardware_candidates", return_value=candidates),
                patch.object(video_encoding, "_available", return_value=listed),
                patch.object(video_encoding, "_trial", return_value=False) as trial,
            ):
                encoder, reason = video_encoding.choose("auto", 1280, 720, 24)
                self.assertEqual(encoder, video_encoding.CPU)
                self.assertTrue(reason)
                self.assertEqual(trial.call_count, len(listed))

    def test_auto_uses_system_ffmpeg_when_managed_build_cannot_open_the_gpu(self):
        with (
            patch.object(video_encoding, "hardware_candidates", return_value=(video_encoding.NVENC,)),
            patch.object(video_encoding.media_tools, "path", side_effect=["managed-ffmpeg.exe", "system-ffmpeg.exe"]),
            patch.object(video_encoding, "_available", return_value={"h264_nvenc"}),
            patch.object(video_encoding, "_trial", side_effect=[False, True]) as trial,
        ):
            encoder, reason = video_encoding.choose("auto", 1280, 720, 24)
        self.assertEqual(encoder.name, "h264_nvenc")
        self.assertEqual(encoder.executable, "system-ffmpeg.exe")
        self.assertIsNone(reason)
        self.assertEqual([call.args[0].executable for call in trial.call_args_list], [None, "system-ffmpeg.exe"])

    def test_trial_requires_an_encoded_frame_and_handles_timeout(self):
        encoder = video_encoding.hardware_candidates("win32")[0]
        with (
            patch.object(video_encoding.media_tools, "command", return_value="ffmpeg"),
            patch.object(video_encoding.subprocess, "run") as run,
        ):
            run.side_effect = [
                SimpleNamespace(returncode=0, stdout=b"h264"),
                SimpleNamespace(returncode=0, stdout=b""),
                subprocess.TimeoutExpired("ffmpeg", 10),
            ]
            self.assertTrue(video_encoding._trial(encoder, 4, 2, 60))
            self.assertFalse(video_encoding._trial(encoder, 4, 2, 60))
            self.assertFalse(video_encoding._trial(encoder, 4, 2, 60))

        args, kwargs = run.call_args_list[0]
        command = args[0]
        self.assertEqual(kwargs["input"], bytes(4 * 2 * 3))
        self.assertEqual(command[command.index("-video_size") + 1], "4x2")
        self.assertEqual(command[command.index("-framerate") + 1], "60")
        self.assertEqual(command[command.index("-c:v") + 1], encoder.name)
