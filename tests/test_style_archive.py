import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from bacchus import styles
from bacchus.library import LibraryError
from bacchus.style_archive import ArchiveError, export_style, import_style
from bacchus.workflow import Studio


def png(color):
    with BytesIO() as buffer:
        Image.new("RGBA", (64, 48), color).save(buffer, "PNG")
        return buffer.getvalue()


class StyleArchiveTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory(prefix="bacchus-style-archive-")
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.source = Studio(self.root / "source")
        self.receiving = Studio(self.root / "receiving")
        old_reference = self.source.styles.store_reference("Early sketch.png", png("peachpuff"))
        good_reference = self.source.styles.store_reference("Current palette.png", png("darkolivegreen"))
        self.early = self.source.styles.create({
            "name": "Studio transfer test", "libraryName": "Portability library", "description": "Local fixtures",
            "guide": "# Early guide\r\n\r\nLeave white space.  \r\n",
            "palette": {"accent": "#7B421C"},
            "answers": {**styles.ANSWER_DEFAULTS, "visualNotes": "  Keep edges clear.\r\nKeep margins.  "},
            "referenceImages": [old_reference],
        })
        self.current = self.source.styles.update(self.early["id"], 1, {
            "guide": "# Current guide\n\nKeep illustrated edges readable.\n", "referenceImages": [good_reference],
        })
        self.library = self.source.library_for(self.current["id"])
        self.project = self.source.create("A project that stays local", style_id=self.current["id"])
        self.proofs = {"a" * 64: "b" * 64}
        first = self.publish("tan", "Original ceramic cup")
        second = self.publish("brown", "Dark ceramic cup")
        self.latest = self.publish("coral", "Corrected ceramic cup")
        self.open_issue = {
            "kind": "defective", "reason": "The rim disappears against the paper.", "at": "2026-11-18T12:15:00+00:00",
        }
        self.library.flag(first["id"], 1, self.open_issue)
        metadata = self.library.root / "entries" / f"{second['id']}.json"
        legacy = json.loads(metadata.read_bytes())
        del legacy["qualifications"]["2"]
        metadata.write_text(json.dumps(legacy), encoding="utf-8")
        self.archive = self.root / "style.zip"
        export_style(self.source, self.current["id"], self.archive)

    def publish(self, color, name):
        raw = png(color)
        path = self.root / "art-fixture.png"
        path.write_bytes(raw)
        return self.library.publish(
            project_id=self.project["id"], alias="cup", name=name, brief="Ceramic cup beside a window",
            style=self.current["id"], source="upload", content_hash=hashlib.sha256(raw).hexdigest(),
            path=path, proofs=self.proofs,
        )

    def rewrite_archive(self, label, *, change=None, append=None):
        bad = self.root / f"{label}.zip"
        with ZipFile(self.archive) as original, ZipFile(bad, "w", compression=ZIP_DEFLATED) as changed:
            for entry in original.infolist():
                data = original.read(entry)
                if change:
                    data = change(entry.filename, data)
                if data is not None:
                    changed.writestr(entry.filename, data)
            if append:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
                    changed.writestr(*append)
        return bad

    def assert_rejected_without_writes(self, archive, *, status=400):
        initial_styles = self.receiving.styles.list_styles()
        initial_files = {path.relative_to(self.root): path.read_bytes()
                         for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaises(ArchiveError) as failure:
            import_style(self.receiving, archive)
        self.assertEqual(failure.exception.status, status)
        self.assertEqual(self.receiving.styles.list_styles(), initial_styles)
        resulting_files = {path.relative_to(self.root): path.read_bytes()
                           for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(resulting_files, initial_files)

    def test_round_trip_preserves_style_history_art_and_isolated_libraries(self):
        with ZipFile(self.archive) as archive:
            guide = archive.read("style.md")
            older = archive.read("guides/1.md")
            manifest_bytes = archive.read("manifest.json")
            manifest = json.loads(manifest_bytes)
        self.assertEqual(guide, self.current["guide"].encode())
        self.assertEqual(older, self.early["guide"].encode())
        self.assertNotIn(self.project["id"].encode(), manifest_bytes)
        self.assertNotIn(self.project["title"].encode(), manifest_bytes)
        record, = manifest["artwork"]
        proofs = {version["version"]: version["proofs"] for version in record["versions"]}
        self.assertEqual(proofs, {1: self.proofs, 2: None, 3: self.proofs})

        first = import_style(self.receiving, self.archive)
        second = import_style(self.receiving, self.archive)
        self.assertEqual(len({self.current["id"], first["id"], second["id"]}), 3)
        self.assertEqual(first["revision"], self.current["revision"])
        source_entry, = self.library.list_assets()
        for imported in (first, second):
            self.assertFalse(imported["builtIn"])
            for original in (self.early, self.current):
                copy = self.receiving.styles.get(imported["id"], original["revision"])
                self.assertEqual(copy["createdAt"], original["createdAt"])
                self.assertEqual({key: copy[key] for key in styles.SPEC_KEYS}, {key: original[key] for key in styles.SPEC_KEYS})
                for reference in copy["referenceImages"]:
                    digest = reference["sha256"]
                    self.assertEqual(self.receiving.styles.reference(digest).read_bytes(),
                                     self.source.styles.reference(digest).read_bytes())
            imported_library = self.receiving.library_for(imported["id"])
            entry, = imported_library.list_assets()
            self.assertEqual(entry["id"], self.latest["id"])
            self.assertEqual(entry["latestVersion"], 3)
            self.assertEqual([item["version"] for item in entry["versions"]], [3, 2, 1])
            self.assertEqual([item["qualified"] for item in entry["versions"]], [True, False, True])
            self.assertEqual([item["issue"] for item in entry["versions"]], [None, None, self.open_issue])
            for version, original in zip(entry["versions"], source_entry["versions"], strict=True):
                self.assertEqual(version, {**original, "style": imported["id"]})
                digest = version["hash"]
                self.assertEqual(imported_library.blob(digest).read_bytes(), self.library.blob(digest).read_bytes())
            with self.assertRaisesRegex(LibraryError, "open artwork issue"):
                imported_library.get(entry["id"], 1)
        first_library = self.receiving.library_for(first["id"])
        first_library.flag(self.latest["id"], 3, self.open_issue)
        self.assertIsNone(self.receiving.library_for(second["id"]).issue(self.latest["id"], 3))
        self.assertIsNone(self.library.issue(self.latest["id"], 3))

    def test_rejects_unsafe_and_duplicate_zip_entries_before_writing(self):
        cases = (
            ("outside", ("../outside.png", png("white"))),
            ("duplicate", ("manifest.json", b"{}")),
        )
        for name, entry in cases:
            with self.subTest(name=name):
                self.assert_rejected_without_writes(self.rewrite_archive(name, append=entry))

    def test_rejects_missing_metadata_and_tampered_content_before_writing(self):
        latest_art = f"artwork/{self.latest['hash']}.png"
        cases = (
            ("missing-manifest", "manifest.json", None),
            ("missing-guide", "style.md", None),
            ("missing-art", latest_art, None),
            ("changed-art", latest_art, png("blue")),
        )
        for label, item, replacement in cases:
            with self.subTest(label=label):
                invalid = self.rewrite_archive(label, change=lambda name, data: replacement if name == item else data)
                self.assert_rejected_without_writes(invalid)

    def test_failure_at_final_publish_rolls_back_library_and_new_references(self):
        shared = self.receiving.styles.store_reference("Shared sketch.png", png("peachpuff"))
        self.assertEqual(shared["sha256"], self.early["referenceImages"][0]["sha256"])
        latest_reference = self.current["referenceImages"][0]["sha256"]
        existing_libraries = list(self.receiving.library_root.iterdir()) if self.receiving.library_root.exists() else []
        rename = Path.rename
        snapshot_root = self.receiving.styles.root / "snapshots"
        failed_style_ids = []

        def fail_final_snapshot(source, destination):
            if source.name == "snapshots" and destination.parent == snapshot_root:
                failed_style_ids.append(destination.name)
                self.assertTrue((self.receiving.library_root / destination.name).is_dir())
                self.assertTrue(self.receiving.styles.reference(latest_reference).is_file())
                raise PermissionError("Final style directory is unavailable.")
            return rename(source, destination)

        with patch.object(Path, "rename", new=fail_final_snapshot):
            self.assert_rejected_without_writes(self.archive, status=500)
        self.assertEqual(len(failed_style_ids), 1)
        self.assertEqual(set(self.receiving.library_root.iterdir()), set(existing_libraries))
        self.assertTrue(self.receiving.styles.reference(shared["sha256"]).is_file())

    def test_export_with_damaged_art_does_not_publish_partial_zip(self):
        self.library.blob(self.latest["hash"]).write_bytes(png("blue"))
        unavailable = self.root / "incomplete.zip"
        with self.assertRaises(ArchiveError) as failure:
            export_style(self.source, self.current["id"], unavailable)
        self.assertEqual(failure.exception.status, 409)
        self.assertFalse(unavailable.exists())


if __name__ == "__main__":
    unittest.main()
