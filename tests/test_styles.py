import base64
from io import BytesIO
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from bacchus import styles


class StyleCatalogTests(unittest.TestCase):
    def setUp(self):
        data = TemporaryDirectory(prefix="bacchus-styles-")
        self.addCleanup(data.cleanup)
        self.data = data.name
        self.catalog = styles.StyleCatalog(self.data)

    def reference_image(self):
        info = PngInfo()
        info.add_text("Comment", "Reference-only metadata.")
        with BytesIO() as output:
            Image.new("RGBA", (1300, 650), (89, 101, 107, 183)).save(output, "PNG", pnginfo=info)
            return self.catalog.store_reference("C:\\Artwork\\Mood Board.PNG", output.getvalue())

    def test_built_ins_use_separate_libraries_and_can_be_revised(self):
        snapshots = self.catalog.list_styles()
        self.assertEqual([item["id"] for item in snapshots], [
            "professional-corporate", "hand-drawn-explainer", "warm-editorial", "studio-ink", "night-signal",
        ])
        self.assertEqual(len({item["libraryName"] for item in snapshots}), len(snapshots))
        self.assertTrue(all(item["builtIn"] and item["guide"].startswith("# ") for item in snapshots))
        original = snapshots[0]
        changed = self.catalog.update(original["id"], 1, {"description": "Revised description."})
        self.assertEqual(changed["revision"], 2)
        reopened = styles.StyleCatalog(self.data)
        self.assertEqual(reopened.get(original["id"], 1), original)
        self.assertEqual(reopened.get(original["id"]), changed)

    def test_revisions_preserve_verbatim_answers_guide_and_references(self):
        answers = {**styles.ANSWER_DEFAULTS, "visualNotes": "  Ink and paper.  \r\nKeep the marks.\n"}
        image = self.reference_image()
        self.assertEqual((image["name"], image["width"], image["height"]), ("Mood Board.PNG", 1024, 512))
        with Image.open(self.catalog.reference(image["sha256"])) as saved:
            self.assertEqual(saved.mode, "RGBA")
            self.assertNotIn("Comment", saved.info)
        initial = self.catalog.create({
            "name": "My style", "libraryName": "My reference library", "guide": "# Guide\n\nA second line.\n",
            "answers": answers, "referenceImages": [image], "palette": {"ink": "#102030"},
        })
        updated = self.catalog.update(initial["id"], 1, {"name": "Renamed", "referenceImages": []})
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["answers"], answers)
        self.assertEqual(updated["guide"], initial["guide"])
        self.assertEqual(self.catalog.get(initial["id"], 1)["referenceImages"], [image])
        with self.assertRaisesRegex(styles.StyleError, "Reload"):
            self.catalog.update(initial["id"], 1, {"name": "Stale edit"})

    def test_checks_reference_bytes_and_palette_format(self):
        image = self.reference_image()
        spec = {"name": "My style", "libraryName": "Library", "referenceImages": [image]}
        with self.assertRaisesRegex(styles.StyleError, "#RRGGBB"):
            self.catalog.create({**spec, "palette": {"ink": "black"}})
        with self.assertRaisesRegex(styles.StyleError, "distinct"):
            self.catalog.create({**spec, "referenceImages": [image, image]})
        self.catalog.reference(image["sha256"]).write_bytes(b"changed after upload")
        with self.assertRaisesRegex(styles.StyleError, "changed after upload"):
            self.catalog.create(spec)

    def test_manual_style_needs_no_provider_and_claude_returns_an_unsaved_draft(self):
        picture = self.reference_image()
        notes = {**styles.ANSWER_DEFAULTS, "music": False, "musicNotes": "Leave the bed out. "}
        spec = {"name": "Quiet style", "libraryName": "Quiet art", "answers": notes, "referenceImages": [picture]}
        with patch.object(styles, "provider_value", side_effect=lambda name, default="": default):
            self.assertFalse(self.catalog.available())
            manual = self.catalog.create({**spec, "guide": "# Quiet\nUse generous space."})
            with self.assertRaisesRegex(styles.StyleError, "Anthropic API key"):
                self.catalog.draft_guide(spec)
        response = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "# Draft\nUse a narrow palette."}]}
        with (
            patch.object(styles, "provider_value", side_effect=lambda name, default="": (
                "test-local-placeholder" if name == "ANTHROPIC_API_KEY" else default
            )),
            patch.object(styles.providers, "post_json", return_value=(response, "request-id")) as send,
            patch.object(styles.api_log, "scope", wraps=styles.api_log.scope) as scope,
        ):
            draft = self.catalog.draft_guide(spec, context_id=manual["id"])
        scope.assert_called_once_with(manual["id"], "style guide drafting")
        endpoint, payload, _, provider = send.call_args.args
        self.assertEqual((endpoint, provider), ("https://api.anthropic.com/v1/messages", "Anthropic"))
        self.assertNotIn("tools", payload)
        text, inline_image = payload["messages"][0]["content"]
        self.assertEqual(json.loads(text["text"])["answers"], notes)
        self.assertEqual(base64.b64decode(inline_image["source"]["data"]),
                         self.catalog.reference(picture["sha256"]).read_bytes())
        self.assertEqual(draft["guide"], "# Draft\nUse a narrow palette.")
        self.assertEqual(draft["requestId"], "request-id")
        self.assertEqual(self.catalog.get(manual["id"]), manual)


if __name__ == "__main__":
    unittest.main()
