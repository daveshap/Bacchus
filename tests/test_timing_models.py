import hashlib
from io import BytesIO
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bacchus import timing_models as models


class TimingModelsTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="bacchus-models-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.current = {name: name.encode() for name in models.MODEL_FILES}
        self.previous = {name: data + b" previous model" for name, data in self.current.items()}
        patcher = patch.multiple(
            models,
            MANAGED_SIZES=tuple(map(len, self.current.values())),
            EXISTING_SIZES=tuple(map(len, self.previous.values())),
            MANAGED_HASHES=tuple(hashlib.sha256(data).hexdigest() for data in self.current.values()),
            EXISTING_HASHES=tuple(hashlib.sha256(data).hexdigest() for data in self.previous.values()),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.setup = models.TimingModels(self.root / "data")

    def fixture_archive(self, *, duplicate=False):
        with BytesIO() as out:
            with tarfile.open(fileobj=out, mode="w:bz2") as archive:
                def add(name, data):
                    record = tarfile.TarInfo(name)
                    record.size = len(data)
                    archive.addfile(record, BytesIO(data))

                for name, data in self.current.items():
                    add(f"{models.MODEL_NAME}/{name}", data)
                add(f"{models.MODEL_NAME}/test_wavs/test.wav", b"sample")
                add("../outside-file", b"bad path")
                if duplicate:
                    first = models.MODEL_FILES[0]
                    add(f"{models.MODEL_NAME}/{first}", self.current[first])
            return out.getvalue()

    def opener(self, raw, *, gate=None, looked_up=None):
        class Response(BytesIO):
            headers = {"Content-Length": str(len(raw))}

            def read(self, length):  # noqa: A003
                if gate is not None and not self.tell():
                    looked_up.set()
                    if not gate.wait(5):
                        raise TimeoutError
                return super().read(length)

        return SimpleNamespace(open=lambda request, timeout: Response(raw))

    def install(self, archive, *, sha256=None):
        checksum = hashlib.sha256(archive).hexdigest() if sha256 is None else sha256
        with patch.multiple(models, ARCHIVE_BYTES=len(archive), ARCHIVE_SHA256=checksum), patch.object(
            models, "build_opener", return_value=self.opener(archive),
        ) as opener:
            self.setup.start_install()
            self.setup._worker.join(5)
            self.assertFalse(self.setup._worker.is_alive())
            return self.setup.status(), opener.call_count

    def test_existing_model_verifies_and_changed_bytes_invalidate(self):
        old = self.root / "old"
        old.mkdir()
        for name, data in self.previous.items():
            (old / name).write_bytes(data)
        self.assertEqual(self.setup.validate_existing(str(old)), old)
        self.assertEqual(self.setup.status(old)["source"], "existing")
        self.assertFalse(self.setup.directory.exists())
        target = old / models.MODEL_FILES[0]
        old_details = target.stat()
        target.write_bytes(target.read_bytes().upper())
        os.utime(target, ns=(old_details.st_atime_ns, old_details.st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(models.TimingModelError, "do not match"):
            self.setup.validate_existing(old)
        self.assertFalse(self.setup.status(old)["ready"])

    def test_managed_install_publishes_only_expected_files_and_reuses_them(self):
        raw = self.fixture_archive()
        status, downloads = self.install(raw)
        self.assertEqual(downloads, 1)
        self.assertTrue(status["ready"])
        self.assertEqual(status["source"], "managed")
        self.assertEqual(status["installation"]["downloadedBytes"], len(raw))
        self.assertEqual({file.name for file in self.setup.directory.iterdir()}, set(models.MODEL_FILES))
        self.assertFalse((self.root / "data" / "outside-file").exists())
        missing = self.root / "missing-model"
        self.assertIsNotNone(self.setup.status(missing)["error"])
        self.assertEqual(self.setup.resolve(missing), self.setup.directory)
        with patch.object(models, "build_opener") as opener:
            self.assertTrue(self.setup.start_install()["ready"])
            opener.assert_not_called()

    def test_install_reports_active_download_and_prevents_duplicate_workers(self):
        data = self.fixture_archive()
        gate, looked_up = Event(), Event()
        with patch.multiple(models, ARCHIVE_BYTES=len(data), ARCHIVE_SHA256=hashlib.sha256(data).hexdigest()), patch.object(
            models, "build_opener", return_value=self.opener(data, gate=gate, looked_up=looked_up),
        ) as opener:
            self.setup.start_install()
            try:
                self.assertTrue(looked_up.wait(5))
                self.assertTrue(self.setup.installing)
                self.assertEqual(self.setup.start_install()["installation"]["state"], "downloading")
                opener.assert_called_once()
            finally:
                gate.set()
                self.setup._worker.join(5)
            self.assertTrue(self.setup.status()["ready"])

    def test_bad_checksum_or_duplicate_member_leaves_existing_files_intact(self):
        target = self.setup.directory
        target.mkdir(parents=True)
        (target / "before.txt").write_bytes(b"preserve on failure")
        status, _ = self.install(self.fixture_archive(), sha256="0" * 64)
        self.assertFalse(status["ready"])
        self.assertIn("SHA-256", status["installation"]["error"])
        status, _ = self.install(self.fixture_archive(duplicate=True))
        self.assertEqual(status["installation"]["state"], "failed")
        self.assertIn("unexpected model file", status["installation"]["error"])
        self.assertEqual((target / "before.txt").read_bytes(), b"preserve on failure")
        self.assertEqual({path.name for path in target.parent.iterdir()}, {target.name})


if __name__ == "__main__":
    unittest.main()
