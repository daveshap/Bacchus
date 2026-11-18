import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory, mkdtemp
import unittest
from unittest.mock import patch

from bacchus import delivery
from bacchus.desktop import data_lock
from bacchus.workflow import Studio


class DeliveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="bacchus-delivery-recovery-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.studio = self.reopen()

    def reopen(self):
        with data_lock(self.root):
            return Studio(self.root)

    def make_exports(self, title):
        project = self.studio.create(title)
        exports = self.root / project["id"] / "exports"
        exports.mkdir()
        return project["id"], exports

    @staticmethod
    def make_staging(exports):
        stage = Path(mkdtemp(prefix=delivery.STAGING_PREFIX, dir=exports))
        nested = stage / "bundle" / ".bacchus-render-abcdef12"
        nested.mkdir(parents=True)
        (nested / "ffmpeg.log").write_bytes(b"interrupted render")
        return stage

    def directory_link(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Directory symlinks are unavailable.")
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                self.skipTest("Directory symlinks and junctions are unavailable.")
            self.addCleanup(link.rmdir)
        else:
            self.addCleanup(link.unlink, missing_ok=True)

    def test_startup_discards_partial_staging_and_keeps_deliveries_and_recovery(self):
        project_id, exports = self.make_exports("Interrupted project")
        stage = self.make_staging(exports)
        unrelated_dir = exports / (delivery.STAGING_PREFIX + "notes-to-keep")
        unrelated_dir.mkdir()
        unrelated_file = exports / (delivery.STAGING_PREFIX + "abcdef12")
        unrelated_file.write_bytes(b"not a directory")

        candidate = "1" * 64
        published_key = "2" * 64
        published = exports / published_key
        published.mkdir()
        paths = delivery.bundle_paths(published)
        for kind, path in paths.items():
            path.write_bytes(f"published {kind}".encode())
        record = {"status": "complete", "candidateHash": candidate, "deliveryId": published_key,
                  "fileHashes": {kind: delivery.file_hash(path) for kind, path in paths.items()},
                  "finishedAt": "2026-11-18T00:00:00+00:00"}

        legacy_key = "3" * 64
        legacy_video = exports / f"{legacy_key}.mp4"
        legacy_video.write_bytes(b"legacy video")
        (exports / f"{legacy_key}.srt").write_bytes(b"legacy captions")
        (exports / f"{legacy_key}.json").write_text(json.dumps({
            "candidateHash": legacy_key, "accepted": {"hash": legacy_key},
            "videoHash": delivery.file_hash(legacy_video), "createdAt": "2026-11-17T00:00:00+00:00",
        }), encoding="utf-8")

        project = self.studio._read(project_id)
        project.update(busy="rendering", deliveries=[record], accepted={"hash": candidate},
                       export={"status": "running", "candidateHash": candidate, "deliveryId": "4" * 64})
        self.studio._save(project)
        clean_id, clean_exports = self.make_exports("Already recovered project")
        clean_stage = self.make_staging(clean_exports)
        clean_revision = self.studio.get(clean_id)["revision"]
        unowned = self.root / ("5" * 32) / "exports"
        unowned.mkdir(parents=True)
        unowned_stage = self.make_staging(unowned)

        reopened = self.reopen()
        view = reopened.get(project_id)
        self.assertIsNone(view["busy"])
        self.assertTrue(view["accepted"])
        self.assertEqual(view["export"]["status"], "failed")
        self.assertEqual(view["lastDelivery"]["deliveryId"], published_key)
        self.assertEqual(reopened.media(project_id, "video", published_key)[0].read_bytes(), b"published video")
        self.assertEqual(reopened.media(project_id, "video", legacy_key)[0].read_bytes(), b"legacy video")
        self.assertFalse(stage.exists())
        self.assertFalse(clean_stage.exists())
        self.assertEqual(reopened.get(clean_id)["revision"], clean_revision)
        self.assertTrue(unowned_stage.is_dir())
        self.assertTrue(unrelated_dir.is_dir())
        self.assertEqual(unrelated_file.read_bytes(), b"not a directory")

    def test_startup_skips_linked_exports_linked_scratch_and_nested_links(self):
        outside = self.root / "outside"
        outside.mkdir()
        outside_stage = self.make_staging(outside)
        project = self.studio.create("Linked exports")
        self.directory_link(self.root / project["id"] / "exports", outside)
        _, exports = self.make_exports("Linked staging")
        linked_stage = exports / (delivery.STAGING_PREFIX + "abcdef12")
        self.directory_link(linked_stage, outside_stage)
        nested_stage = self.make_staging(exports)
        nested_link = nested_stage / "bundle" / "external"
        self.directory_link(nested_link, outside)
        removable = self.make_staging(exports)

        self.reopen()
        self.assertTrue(outside_stage.is_dir())
        self.assertTrue(linked_stage.is_dir())
        self.assertTrue(nested_link.is_dir())
        self.assertTrue(nested_stage.is_dir())
        self.assertFalse(removable.exists())

    def test_locked_files_leave_scratch_for_the_next_startup(self):
        project_id, exports = self.make_exports("Locked scratch")
        stage = self.make_staging(exports)
        with patch.object(delivery.shutil, "rmtree", side_effect=PermissionError("File is in use")):
            self.assertEqual(self.reopen().get(project_id)["id"], project_id)
        self.assertTrue(stage.is_dir())
        self.reopen()
        self.assertFalse(stage.exists())
