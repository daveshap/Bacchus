import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from bacchus.library import Library, LibraryError


class LibraryTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory(prefix="bacchus-library-test-")
        self.addCleanup(work.cleanup)
        self.root = Path(work.name) / "shared-artwork"
        self.source = Path(work.name) / "project-art.png"
        self.library = Library(self.root)

    def publish(self, color, *, project_id="first-project", alias="ceramic-cup", name="Cup", style="warm-editorial"):
        Image.new("RGBA", (32, 32), color).save(self.source)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        return self.library.publish(
            project_id=project_id, alias=alias, name=name, brief="Ceramic cup from the side",
            style=style, source="upload", content_hash=digest, path=self.source, proofs={"a" * 64: "b" * 64},
        )

    def test_projects_share_catalog_but_same_alias_has_separate_identity(self):
        first = self.publish("tan")
        second = self.publish("tan", project_id="second-project")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["hash"], second["hash"])
        self.assertEqual((first["version"], second["version"]), (1, 1))
        self.assertEqual(len(list((self.root / "blobs").glob("*.png"))), 1)
        self.assertEqual(len(list((self.root / "entries").glob("*.json"))), 2)
        reopened = Library(self.root)
        self.assertEqual(reopened.get(second["id"], 1), second)
        self.assertEqual(reopened.blob(first["hash"]).read_bytes(), self.source.read_bytes())
        self.assertNotIn("projectId", reopened.list_assets()[0])

    def test_revisions_keep_old_content_and_republishing_one_keeps_latest(self):
        first = self.publish("tan")
        first_bytes = self.library.blob(first["hash"]).read_bytes()
        second = self.publish("brown", name="Dark cup")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["version"], 2)
        selected = self.publish("tan")
        self.assertEqual(selected, first)
        [entry] = self.library.list_assets()
        self.assertEqual((entry["name"], entry["latestVersion"]), ("Dark cup", 2))
        self.assertEqual(entry["versions"], [second, first])
        self.assertEqual(self.library.get(entry["id"], 1), first)
        self.assertEqual(self.library.blob(first["hash"]).read_bytes(), first_bytes)

    def test_reject_tampered_blob_and_missing_versions(self):
        version = self.publish("tan")
        blob = self.library.blob(version["hash"])
        blob.write_bytes(b"changed")
        with self.assertRaisesRegex(LibraryError, "fingerprint") as error:
            self.library.blob(version["hash"])
        self.assertEqual(error.exception.status, 409)
        with self.assertRaisesRegex(LibraryError, "fingerprint"):
            self.publish("tan")
        with self.assertRaisesRegex(LibraryError, "version was not found") as error:
            self.library.get(version["id"], 2)
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.library.list_assets()[0]["latestVersion"], 1)

    def test_catalog_isolates_damaged_metadata_and_quarantine_retains_it(self):
        bad = self.publish("tan")
        good = self.publish("red", project_id="other-project")
        damaged = self.root / "entries" / f"{bad['id']}.json"
        record = json.loads(damaged.read_bytes())
        record["issues"] = ["malformed"]
        original = json.dumps(record).encode()
        damaged.write_bytes(original)

        catalog = self.library.catalog()
        self.assertEqual([entry["id"] for entry in catalog["assets"]], [good["id"]])
        self.assertEqual([issue["id"] for issue in catalog["issues"]], [bad["id"]])
        with self.assertRaisesRegex(LibraryError, "Only damaged"):
            self.library.quarantine(good["id"])

        staged = self.library.quarantine(bad["id"])
        self.assertFalse(damaged.exists())
        self.assertEqual(Path(staged["retainedPath"]).read_bytes(), original)
        self.assertTrue(self.library.blob(bad["hash"]).is_file())
        self.assertEqual(self.library.catalog(), {"assets": catalog["assets"], "issues": []})
        with self.assertRaisesRegex(LibraryError, "in quarantine"):
            self.library.issue(bad["id"], 1)
        with self.assertRaisesRegex(LibraryError, "quarantined metadata"):
            self.publish("tan")

    def test_migration_continues_after_a_damaged_entry(self):
        bad = self.publish("tan")
        good = self.publish("red", project_id="other-project")
        damaged = self.root / "entries" / f"{bad['id']}.json"
        damaged.write_bytes(b"broken")
        target = Library(self.root.parent / "warm-editorial")
        with self.assertRaisesRegex(LibraryError, "could not copy 1"):
            self.library.copy_by_style({"warm-editorial": target})
        self.assertEqual(target.get(good["id"], 1), good)
        self.assertEqual(damaged.read_bytes(), b"broken")

    def test_migration_separates_historical_versions_and_preserves_issue_records(self):
        first = self.publish("tan")
        second = self.publish("brown", style="studio-ink")
        latest = self.publish("red")
        issue = {"kind": "defective", "reason": "Missing edge", "at": "recorded"}
        self.library.flag(first["id"], first["version"], issue)
        libraries = {name: Library(self.root.parent / name) for name in ("warm-editorial", "studio-ink")}
        self.library.copy_by_style(libraries)
        self.library.copy_by_style(libraries)
        warm, ink = (libraries[name].list_assets()[0]["versions"] for name in ("warm-editorial", "studio-ink"))
        self.assertEqual([item["version"] for item in warm], [latest["version"], first["version"]])
        self.assertEqual(ink, [second])
        self.assertEqual(warm[1]["issue"], issue)
        self.assertTrue(warm[0]["qualified"])
        with self.assertRaises(LibraryError):
            libraries["warm-editorial"].get(second["id"], second["version"])
        self.assertEqual(len(self.library.list_assets()[0]["versions"]), 3)


if __name__ == "__main__":
    unittest.main()
