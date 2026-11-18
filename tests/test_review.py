from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from bacchus import render, review


def project():
    lines = [
        {"id": index, "text": f"Line {index}.", "start": start, "end": end}
        for index, (start, end) in enumerate(
            ((0.25, 0.91), (1.04, 2.12), (2.19, 3.29), (3.4, 4.4)), start=1,
        )
    ]
    return {
        "input": {"duration": 4.5, "lines": lines},
        "plan": {"scenes": [
            {
                "startLine": line["id"], "endLine": line["id"], "layout": "editorial", "motion": "fade",
                "title": f"Scene {line['id']}", "caption": "", "artId": "", "artBrief": "",
            }
            for line in lines
        ]},
        "assets": [],
    }


class ReviewTests(unittest.TestCase):
    def test_proofs_bind_relevant_art_cues_and_transitions(self):
        original = project()
        first, second, third, _ = original["plan"]["scenes"]
        first.update(artId="cup", artBrief="Ceramic cup from the side")
        third.update(artId="tree", artBrief="An oak against the light")
        original["assets"] = [
            {"id": "cup", "hash": "a" * 64, "approved": True},
            {"id": "tree", "hash": "b" * 64, "approved": True},
        ]
        art = review.asset_checks(original)
        shots = review.animation_checks(original)
        self.assertEqual([check["scene"] for check in art], [1, 3])
        self.assertEqual([check["scene"] for check in shots], [1, 2, 3, 4])
        self.assertEqual(shots[0]["start"], 0)
        self.assertEqual(shots[1]["start"], 1.04)
        self.assertEqual(shots[1]["end"], 2.19)
        labels = " ".join(sample["label"] for sample in shots[1]["samples"])
        for label in ("Before entrance", "Entrance", "Motion", "Settled", "Cue 2", "Exit", "Next entrance"):
            self.assertIn(label, labels)
        for shot in shots:
            for sample in shot["samples"]:
                self.assertGreaterEqual(sample["time"], 0)
                self.assertLess(sample["time"], original["input"]["duration"])
                self.assertAlmostEqual(sample["time"] * render.FPS, round(sample["time"] * render.FPS))

        changed = deepcopy(original)
        changed["mix"] = {"musicMode": "none"}
        changed["input"]["hash"] = "a new whole-input record"
        self.assertEqual(review.asset_checks(changed), art)
        self.assertEqual(review.animation_checks(changed), shots)
        changed["assets"][1]["hash"] = "c" * 64
        self.assertEqual(review.asset_checks(changed)[0]["id"], art[0]["id"])
        self.assertNotEqual(review.asset_checks(changed)[1]["id"], art[1]["id"])
        current = review.animation_checks(changed)
        self.assertEqual(current[0]["id"], shots[0]["id"])
        for index in (1, 2, 3):
            self.assertNotEqual(current[index]["id"], shots[index]["id"])

    def test_art_proof_contains_exact_static_frame_and_rejects_unapproved_art(self):
        with TemporaryDirectory(prefix="bacchus-review-") as directory:
            folder = Path(directory)
            (folder / "assets").mkdir()
            image = Image.new("RGBA", (64, 64), (221, 70, 27, 230))
            with BytesIO() as buffer:
                image.save(buffer, format="PNG")
                raw = buffer.getvalue()
            digest = hashlib.sha256(raw).hexdigest()
            (folder / "assets" / f"{digest}.png").write_bytes(raw)
            edit = project()
            edit["plan"]["scenes"][0].update(artId="cup", artBrief="Show a cup")
            edit["assets"] = [{"id": "cup", "hash": digest, "approved": False}]
            [check] = review.asset_checks(edit)
            with self.assertRaisesRegex(render.RenderError, "needs approval"):
                review.asset_proof_png(edit, folder, check)
            edit["assets"][0]["approved"] = True
            with patch.object(render._Composition, "apply_motion", side_effect=AssertionError("Motion used")):
                proof = review.asset_proof_png(edit, folder, check)
            frame = render._Composition(edit, folder)._draw(0)
            with Image.open(BytesIO(proof)) as sheet:
                self.assertEqual(sheet.format, "PNG")
                self.assertIsNone(ImageChops.difference(sheet.crop((24, 88, 24 + frame.width, 88 + frame.height)), frame).getbbox())
            edit["plan"]["scenes"][0]["title"] = "Changed scene"
            with self.assertRaisesRegex(render.RenderError, "no longer matches"):
                review.asset_proof_png(edit, folder, check)

    def test_animation_tile_uses_exact_rendered_motion_frame(self):
        edit = project()
        check = review.animation_checks(edit)[0]
        index = next(index for index, sample in enumerate(check["samples"]) if sample["label"] == "Motion")
        sample = check["samples"][index]
        row, column = divmod(index, 2)
        x, y = 20 + column * 788, 64 + row * 491
        with (
            Image.open(BytesIO(review.animation_proof_png(edit, Path("."), check))) as sheet,
            Image.open(BytesIO(render.preview(edit, Path("."), sample["time"]))) as frame,
        ):
            tile = sheet.crop((x, y, x + 768, y + 432))
            self.assertIsNone(ImageChops.difference(tile, frame.resize((768, 432), Image.Resampling.LANCZOS)).getbbox())


if __name__ == "__main__":
    unittest.main()
