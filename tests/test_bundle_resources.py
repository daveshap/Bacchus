from pathlib import Path
from runpy import run_path
from tempfile import TemporaryDirectory
import unittest

from bacchus import canvas_engine, server, styles


ROOT = Path(__file__).resolve().parents[1]
manifest = run_path(str(ROOT / "packaging" / "bundle_resources.py"))


class BundleResourceTests(unittest.TestCase):
    def test_manifest_covers_runtime_resources(self):
        paths = {Path(source).relative_to(ROOT / "bacchus" / "resources").as_posix()
                 for source, _ in manifest["bundle_data"](ROOT)}
        self.assertTrue({f"engine/{name}" for name in canvas_engine.SOURCE_NAMES} <= paths)
        self.assertTrue({f"style_guides/{name}.md" for name in styles.BUILT_INS} <= paths)
        self.assertTrue({f"ui/{name}" for name, _ in server.STATIC_FILES.values()} <= paths)
        self.assertTrue({"packs/editorial/pack.json", "packs/editorial/plan.md"} <= paths)

    def test_extra_files_are_excluded_from_source_build_and_rejected_in_bundle(self):
        with TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            resources = project / "bacchus" / "resources"
            for name in manifest["RESOURCE_FILES"]:
                path = resources / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            manifest["verify_bundled_resources"](resources)
            private = resources / "ui" / "private-notes.js"
            private.touch()
            self.assertNotIn(str(private), {path for path, _ in manifest["bundle_data"](project)})
            with self.assertRaisesRegex(ValueError, "private-notes"):
                manifest["verify_bundled_resources"](resources)
            (resources / "ui" / "app.js").unlink()
            with self.assertRaisesRegex(ValueError, "app.js"):
                manifest["bundle_data"](project)
