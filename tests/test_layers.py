from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image, ImageChops

from bacchus import planning, render, review


def layer(identifier, kind, box, **changes):
    return {
        "id": identifier, "kind": kind, "box": dict(zip(("x", "y", "w", "h"), box, strict=True)),
        "text": "", "artId": "", "artBrief": "", "items": [], "unit": "",
        "enter": {"line": 0, "word": 0, "effect": "show", "duration": 0},
        "leave": {"line": 0, "word": 0},
        **changes,
    }


def scene_project():
    source = {
        "duration": 5.0,
        "lines": [
            {"id": 1, "text": "The readings change as the system responds.", "start": 0.0, "end": 2.12},
            {"id": 2, "text": "An alert leads to a repair and a verified result.", "start": 2.13, "end": 4.9},
        ],
        "words": [
            {"id": index, "text": text, "start": at, "end": at + .15}
            for index, (text, at) in enumerate(
                [("readings", .5), ("change", 1.0), ("alert", 2.25), ("verified", 3.1), ("result", 4.25)],
                start=1,
            )
        ],
    }
    scene = {
        "startLine": 1, "endLine": 2, "layout": "layered", "motion": "still",
        "title": "From signal to repair", "caption": "The chart and process stay editable.",
        "artId": "", "artBrief": "",
        "layers": [
            layer(
                "readings", "bar-chart", (0, 0, 53, 49), text="Change in demand", unit="kW",
                items=[{"label": "Stage A", "value": 8.5}, {"label": "Stage B", "value": -3}],
                enter={"line": 0, "word": 2, "effect": "wipe", "duration": 0.5},
            ),
            layer(
                "sensor", "art", (58, 0, 42, 49), artId="sensor", artBrief="An approved cutout.",
                enter={"line": 0, "word": 1, "effect": "fade", "duration": .4},
                leave={"line": 0, "word": 5},
            ),
            layer(
                "response", "flow", (0, 58, 100, 42), text="Response",
                items=[{"label": label, "value": 0} for label in ("Alert", "Repair", "Verify")],
                enter={"line": 0, "word": 3, "effect": "rise", "duration": .33},
            ),
        ],
    }
    return {"input": source, "plan": {"scenes": [scene]}, "assets": [], "audio": None}


class LayeredScenesTests(unittest.TestCase):
    def create_asset(self, folder, project):
        with BytesIO() as buffer:
            Image.new("RGBA", (96, 92), "#127843").save(buffer, format="PNG")
            raw = buffer.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        (folder / "assets").mkdir()
        (folder / "assets" / f"{digest}.png").write_bytes(raw)
        project["assets"] = [{"id": "sensor", "hash": digest, "approved": True}]

    def test_word_timed_layers_and_native_charts_are_visibly_distinct(self):
        with TemporaryDirectory(prefix="bacchus-layered-") as directory:
            folder = Path(directory)
            project = scene_project()
            self.create_asset(folder, project)
            composition = render._Composition(project, folder)
            prepared = composition.prepare(0)

            def frame(time):
                return composition.draw_prepared(0, time, prepared)

            chart, art, flow = prepared.layers
            pt = (art.position[0] + art.image.width // 2, art.position[1] + art.image.height // 2)
            self.assertEqual(frame(.25).getpixel(pt), prepared.frame.getpixel(pt))
            self.assertNotEqual(frame(.70).getpixel(pt), prepared.frame.getpixel(pt))
            self.assertNotEqual(frame(.70).getpixel(pt), frame(.95).getpixel(pt))
            self.assertEqual(frame(.95).getpixel(pt), (18, 120, 67))
            self.assertEqual(frame(4.25).getpixel(pt), prepared.frame.getpixel(pt))
            self.assertIsNotNone(ImageChops.difference(frame(1.25), frame(1.6)).getbbox())
            self.assertIsNotNone(ImageChops.difference(frame(2.1), frame(2.7)).getbbox())
            self.assertEqual((chart.start, flow.start), (1, 2.25))
            self.assertEqual(render.layout_findings(project, folder), [])

            encoded_frames = render._video_frames(composition, 25)
            encoded = list(encoded_frames)[24]
            with Image.open(BytesIO(render.preview(project, folder, 1))) as expected:
                self.assertEqual(expected.tobytes(), encoded)
            changed = deepcopy(project)
            changed["plan"]["scenes"][0]["layers"][0]["items"][0]["value"] = 5.25
            original = render.preview(project, folder, 1.7)
            self.assertNotEqual(original, render.preview(changed, folder, 1.7))

    def test_per_use_proof_shows_isolated_action_and_library_publication_keeps_identity(self):
        with TemporaryDirectory(prefix="bacchus-layered-proof-") as directory:
            folder = Path(directory)
            project = scene_project()
            self.create_asset(folder, project)
            scene = project["plan"]["scenes"][0]
            self.assertEqual(list(planning.scene_art(scene)), [
                {"artId": "sensor", "artBrief": "An approved cutout.", "layerId": "sensor"},
            ])
            [use] = review.asset_checks(project)
            [motion] = review.animation_checks(project)
            labels = " ".join(sample["label"] for sample in motion["samples"])
            for marker in ("sensor enters", "sensor moves", "sensor settles", "sensor leaves", "response settles"):
                self.assertIn(marker, labels)
            with Image.open(BytesIO(review.asset_proof_png(project, folder, use))) as proof:
                self.assertEqual(proof.format, "PNG")
                self.assertGreater(proof.height, render.HEIGHT + 300)
            with Image.open(BytesIO(review.animation_proof_png(project, folder, motion))) as proof:
                self.assertEqual(proof.format, "PNG")
            project["assets"][0].update(libraryId="published", libraryVersion=1)
            self.assertEqual(review.asset_checks(project), [use])
            self.assertEqual(review.animation_checks(project), [motion])

    def test_invalid_layer_capabilities_placement_or_cues_are_rejected(self):
        for key, value, message in (
            ("kind", "rigged-character", "Unsupported layer kind"),
            ("box", {"x": 80, "y": 80, "w": 40, "h": 40}, "box must fit"),
            ("enter", {"line": 1, "word": 1, "effect": "show", "duration": 0}, "must select"),
            ("leave", {"line": 0, "word": 1}, "motion must fit"),
        ):
            with self.subTest(key=key, value=value):
                project = scene_project()
                scene = project["plan"]["scenes"][0]
                scene["layers"][0][key] = value
                with self.assertRaisesRegex(planning.PlannerError, message):
                    planning.validate_layers(project, scene)


if __name__ == "__main__":
    unittest.main()
