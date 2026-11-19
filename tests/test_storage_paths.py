import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from bacchus import api_log, delivery, media_tools, settings, storage_paths
from bacchus.library import Library
from bacchus.storage_paths import StoragePathError, writable_path
from bacchus.workflow import Studio, WorkflowError, write_file


def link_folder(link, target):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


class StoragePathTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(dir=storage_paths.scratch_directory())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / ".git").mkdir()

    def test_absolute_paths_and_nested_markers(self):
        clean = self.root / "new" / "folder"
        self.assertEqual(writable_path(clean), clean)
        self.assertFalse(clean.parent.exists())
        for value in ("", ".", clean / ".." / "other", self.checkout, self.checkout / "new" / "file"):
            with self.subTest(path=value), self.assertRaises(StoragePathError):
                writable_path(value)
        linked = self.root / "linked-worktree"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: ../common/worktrees/linked")
        with self.assertRaisesRegex(StoragePathError, "Git checkout"):
            Studio(linked / "project data")
        self.assertEqual([entry.name for entry in linked.iterdir()], [".git"])

    def test_links_in_and_out_of_protected_directories(self):
        safe = self.root / "ordinary"
        safe.mkdir()
        inward = self.root / "points-to-checkout"
        outward = self.checkout / "points-outside"
        link_folder(inward, self.checkout)
        link_folder(outward, safe)
        nested = self.checkout / "nested"
        nested.mkdir()
        intermediate = self.root / "points-to-nested"
        link_folder(intermediate, nested)
        link_folder(nested / "out", safe)
        for link in (inward, outward, intermediate / "out"):
            with self.subTest(link=link), self.assertRaisesRegex(StoragePathError, "Git checkout"):
                write_file(link / "new" / "file", b"data")
        self.assertFalse((self.checkout / "new").exists())
        self.assertFalse((safe / "new").exists())

    def test_dangling_destinations_and_git_markers(self):
        parent = self.root / "scattered"
        target = self.root / "target"
        parent.mkdir()
        target.mkdir()
        marker = parent / ".git"
        link_folder(marker, target)
        target.rmdir()
        with self.assertRaisesRegex(StoragePathError, "Git checkout"):
            writable_path(parent / "child")
        with self.assertRaises(StoragePathError):
            writable_path(marker / "nested")
        target.mkdir()
        broken = self.root / "broken"
        link_folder(broken, target)
        target.rmdir()
        with self.assertRaises(StoragePathError):
            writable_path(broken / "new")

    def test_frozen_installation_and_extraction_root(self):
        install = self.root / "program"
        bundle = self.root / "resources"
        with mock.patch.object(storage_paths.sys, "frozen", True, create=True), \
                mock.patch.object(storage_paths.sys, "executable", str(install / "Bacchus.exe")), \
                mock.patch.object(storage_paths.sys, "_MEIPASS", str(bundle), create=True):
            for protected in (install, bundle):
                with self.assertRaisesRegex(StoragePathError, "installation"):
                    writable_path(protected / "projects")
            self.assertEqual(writable_path(self.root / "workspace"), self.root / "workspace")

    def test_nested_project_media_and_later_checkout_are_blocked(self):
        studio = Studio(self.root / "workspace")
        project = studio.create("Existing film", demo=True)
        folder = studio.root / project["id"]
        audio = folder / "audio"
        link_folder(audio, self.checkout)
        with self.assertRaisesRegex(StoragePathError, "Git checkout"):
            studio._store_track(folder, "music.wav", b"a unique file", 1.0, -16)
        self.assertEqual([p.name for p in self.checkout.iterdir()], [".git"])
        before = (folder / "project.json").read_bytes()
        (folder / ".git").write_text("gitdir: ../common")
        with self.assertRaisesRegex(WorkflowError, "Git checkout"):
            studio.set_mode(project["id"], {"revision": project["revision"], "autoMode": True})
        self.assertEqual((folder / "project.json").read_bytes(), before)

    def test_legacy_import_reads_checkout_and_does_not_initialize_source(self):
        source = self.checkout / "legacy"
        (source / "entries").mkdir(parents=True)
        studio = Studio(self.root / "workspace", source)
        self.assertIsNone(studio.library_migration_error)
        self.assertEqual([entry.name for entry in source.iterdir()], ["entries"])
        marker, = studio.library_root.glob(".legacy-*.json")
        self.assertEqual(json.loads(marker.read_bytes()).keys(), {"copiedAt"})
        with self.assertRaises(StoragePathError):
            Library(source)

    def test_logs_and_preferences_reject_a_checkout_but_model_reads_are_allowed(self):
        data = self.root / "workspace"
        data.mkdir()
        (data / "settings.json").write_text(json.dumps({"apiLogDirectory": str(self.checkout)}))
        config = settings.Settings(data)
        self.addCleanup(settings.Settings, self.root / "empty")
        with self.assertRaisesRegex(settings.SettingsError, "Git checkout"):
            config.save({"apiLogDirectory": str(self.checkout)})
        self.assertEqual(config.save({"timingModelDirectory": str(self.checkout)})["timingModelDirectory"],
                         str(self.checkout))
        with mock.patch.dict(os.environ, {"BACCHUS_API_LOG_DIR": str(self.checkout)}):
            self.assertNotEqual(settings.api_log_directory(), self.checkout)
        with self.assertRaisesRegex(api_log.ApiLogError, "Git checkout"):
            api_log.start(self.checkout, "test", "example", b"{}", "application/json")
        self.assertEqual([entry.name for entry in self.checkout.iterdir()], [".git"])
        call = api_log.start(data / "logs", "test", "example", b"{}", "application/json")
        (call.path / ".git").mkdir()
        with self.assertRaisesRegex(api_log.ApiLogError, "Git checkout"):
            call.finish(200, b"response")
        self.assertFalse((call.path / "response.body").exists())

    def test_scratch_skips_protected_override_and_child_report_destinations(self):
        with mock.patch.object(storage_paths.tempfile, "tempdir", str(self.checkout)), \
                mock.patch.dict(os.environ, {"TMPDIR": str(self.checkout), "TEMP": str(self.root),
                                             "FFREPORT": "yes", "CHROME_LOG_FILE": "log"}):
            self.assertEqual(storage_paths.scratch_directory(), self.root)
            child = media_tools.process_options()["env"]
            for key in ("TEMP", "TMP", "TMPDIR"):
                self.assertEqual(child[key], str(self.root))
            self.assertNotIn("FFREPORT", child)
            self.assertNotIn("CHROME_LOG_FILE", child)

    def test_abandoned_staging_cleanup_leaves_nested_checkout_intact(self):
        studio = Studio(self.root / "workspace")
        project = studio.create("Existing film", demo=True)
        folder = studio.root / project["id"]
        stage = folder / "exports" / (delivery.STAGING_PREFIX + "abcdefgh")
        marker = stage / "preserved" / ".git"
        marker.mkdir(parents=True)
        delivery.remove_abandoned_staging(studio.root, folder)
        self.assertTrue(marker.is_dir())
