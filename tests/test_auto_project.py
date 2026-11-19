import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from bacchus.workflow import Studio, WorkflowError


class AutoProjectTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.studio = Studio(self.root / "studio")

    def test_new_project_mode_and_editable_limits_survive_restart(self):
        project = self.studio.create("One video", auto_limits={"maxUsd": 0}, auto_brief="For operators")
        self.assertTrue(project["autoMode"])
        self.assertEqual(project["autoLimits"]["maxUsd"], 0)
        self.assertEqual(project["autoUsage"]["calls"], 0)
        project = self.studio.set_auto_settings(project["id"], {
            "revision": project["revision"], "autoLimits": {"maxTokens": 12000}, "autoBrief": "Explain the handoff",
        })
        self.assertEqual(Studio(self.root / "studio").get(project["id"])["autoLimits"]["maxTokens"], 12000)
        self.assertEqual(project["autoBrief"], "Explain the handoff")
        self.assertFalse(self.studio.create("Operator", auto_mode=False)["autoMode"])
        with self.assertRaises(WorkflowError):
            self.studio.set_auto_settings(project["id"], {
                "revision": project["revision"], "autoLimits": {"maxUsd": -1}, "autoBrief": "",
            })

    def test_interrupted_auto_export_is_retryable(self):
        project = self.studio.create("Interrupted export")
        record = self.studio._read(project["id"])
        record.update(busy="running auto pipeline", autoRun={"status": "running"},
                      export={"status": "running", "deliveryId": "a" * 64})
        self.studio._save(record)
        reopened = Studio(self.root / "studio").get(project["id"])
        self.assertIsNone(reopened["busy"])
        self.assertEqual(reopened["autoRun"]["status"], "interrupted")
        self.assertEqual(reopened["export"]["status"], "failed")

    def test_catalog_accounts_for_missing_and_mismatched_files(self):
        source = self.root / "source"
        source.mkdir()
        frame = Image.new("RGB", (80, 80), "teal")
        frame.save(source / "good.png")
        frame.save(source / "wrong.png")
        sha = hashlib.sha256((source / "good.png").read_bytes()).hexdigest()
        entries = [{"id": name, "files": {"png": name + ".png"}, "checksums": {name + ".png": expected}}
                   for name, expected in (("good", sha), ("wrong", "0" * 64), ("missing", sha))]
        (source / "catalog.json").write_text(json.dumps({"assets": entries}), encoding="utf-8")
        result = self.studio.import_library_folder("warm-editorial", str(source))
        self.assertEqual((result["total"], result["imported"], result["failed"]), (3, 1, 2))
        self.assertEqual(len(self.studio.library_catalog("warm-editorial")["assets"]), 1)
