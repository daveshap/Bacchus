import hashlib
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from bacchus import media_tools, runtime


class RuntimeTests(TestCase):
    def test_child_processes_do_not_receive_provider_keys(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-one", "ANTHROPIC_API_KEY": "test-two"}):
            with mock.patch.object(media_tools.sys, "frozen", False, create=True):
                child_env = media_tools.process_options()["env"]
            self.assertNotIn("OPENAI_API_KEY", child_env)
            self.assertNotIn("ANTHROPIC_API_KEY", child_env)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "test-one")

    def test_platform_defaults_use_user_data(self):
        with TemporaryDirectory() as location:
            root = Path(location)
            with mock.patch.object(runtime.Path, "home", return_value=root):
                with mock.patch.object(runtime.sys, "platform", "win32"), mock.patch.dict(
                    os.environ, {"LOCALAPPDATA": str(root / "Local")},
                ):
                    self.assertEqual(runtime.default_data_directory(), root / "Local" / "Bacchus")
                with mock.patch.object(runtime.sys, "platform", "darwin"):
                    self.assertEqual(runtime.default_data_directory(), root / "Library" / "Application Support" / "Bacchus")
                with mock.patch.object(runtime.sys, "platform", "linux"), mock.patch.dict(
                    os.environ, {"XDG_DATA_HOME": "relative/path"},
                ):
                    self.assertEqual(runtime.default_data_directory(), root / ".local" / "share" / "bacchus")
                with mock.patch.object(runtime.sys, "platform", "linux"), mock.patch.dict(
                    os.environ, {"XDG_DATA_HOME": str(root / "Portable")},
                ):
                    self.assertEqual(runtime.default_data_directory(), root / "Portable" / "bacchus")

    def test_download_checks_published_digest_before_unpacking(self):
        payload = b"A small stand-in for the vendor archive."
        expected = hashlib.sha256(payload).hexdigest()
        with TemporaryDirectory() as location, mock.patch.object(media_tools, "WINDOWS_SHA256", expected):
            destination = Path(location) / "archive.zip"
            with mock.patch.object(media_tools, "urlopen", return_value=BytesIO(payload)):
                media_tools._download(destination)
            self.assertEqual(destination.read_bytes(), payload)
            with mock.patch.object(media_tools, "urlopen", return_value=BytesIO(b"unexpected data")):
                with self.assertRaisesRegex(media_tools.MediaToolsError, "failed verification"):
                    media_tools._download(Path(location) / "incorrect.zip")
