import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bacchus.storage_paths import StoragePathError
from bacchus.workspace import WorkspaceError, WorkspaceLocation


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.default = self.root / "default"
        self.other = self.root / "My studio"

    def launch(self, override=None):
        return WorkspaceLocation(override, default=self.default)

    def test_save_reopen_override_and_reset(self):
        first = self.launch()
        self.assertEqual(first.active, self.default)
        first.choose(str(self.other))
        self.assertEqual(first.active, self.default)
        self.assertEqual(first.view()["preferred"], str(self.other))
        self.assertEqual(self.launch().active, self.other)
        once = self.launch(self.root / "one-off")
        self.assertTrue(once.view()["override"])
        self.assertEqual(self.launch().active, self.other)
        once.choose("")
        self.assertFalse(first.preference_file.exists())
        self.assertEqual(self.launch().active, self.default)

    def test_rejected_choice_preserves_preference_and_source_files(self):
        location = self.launch()
        location.choose(str(self.other))
        repo = self.root / "checkout"
        repo.mkdir()
        (repo / ".git").write_text("gitdir: elsewhere")
        initial = location.preference_file.read_bytes()
        for value, error in ((str(repo / "new"), StoragePathError), ("relative", WorkspaceError),
                             (None, WorkspaceError)):
            with self.subTest(value=value), self.assertRaises(error):
                location.choose(value)
        self.assertEqual(set(repo.iterdir()), {repo / ".git"})
        self.assertEqual(location.preference_file.read_bytes(), initial)

    def test_remembered_directory_removed_or_converted_to_git(self):
        location = self.launch()
        location.choose(str(self.other))
        self.other.rmdir()
        for state in ("removed", "checkout"):
            if state == "checkout":
                self.other.mkdir()
                (self.other / ".git").mkdir()
            with self.subTest(state=state):
                fallback = self.launch()
                self.assertEqual(fallback.active, self.default)
                self.assertEqual(fallback.view()["preferred"], str(self.other))
                self.assertTrue(fallback.view()["warning"])
        self.assertEqual(set(self.other.iterdir()), {self.other / ".git"})
        with self.assertRaises(StoragePathError):
            self.launch(self.other)

    def test_bad_preference_can_be_repaired_from_settings(self):
        location = self.launch()
        self.default.mkdir()
        for damaged in (b"{", b"\xff", json.dumps({"directory": "relative"}).encode()):
            with self.subTest(damaged=damaged):
                location.preference_file.write_bytes(damaged)
                repaired = self.launch()
                self.assertEqual(repaired.active, self.default)
                self.assertTrue(repaired.view()["warning"])
                repaired.choose(str(self.other))
                self.assertEqual(self.launch().active, self.other)

    def test_preference_file_cannot_be_created_inside_git_on_override(self):
        self.default.mkdir()
        (self.default / ".git").mkdir()
        once = self.launch(self.other)
        with self.assertRaises(StoragePathError):
            once.choose(str(self.root / "proposed"))
        self.assertFalse(once.preference_file.exists())
        self.assertFalse((self.root / "proposed").exists())
