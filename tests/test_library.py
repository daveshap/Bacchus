import hashlib
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

    def publish(self, color, *, project_id="first-project", alias="ceramic-cup", name="Cup"):
        Image.new("RGBA", (32, 32), color).save(self.source)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        return self.library.publish(
            project_id=project_id, alias=alias, name=name, brief="Ceramic cup from the side",
            style="warm-editorial", source="upload", content_hash=digest, path=self.source,
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


if __name__ == "__main__":
    unittest.main()
