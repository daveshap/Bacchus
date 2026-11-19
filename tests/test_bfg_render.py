import unittest
from pathlib import Path
from unittest.mock import patch

from bacchus import browser_cdp, layer_spec, render, review


def film():
    scenes = [
        {"layout": "editorial", "motion": "still", "title": "First",
         "canvasCode": "g.c.fillStyle = '#ed1631'; g.c.fillRect(0, 0, BFG.W, BFG.H);"},
        {"layout": "layered", "motion": "glide", "title": "Second"},
        {"layout": "editorial", "motion": "fade", "title": "Third"},
    ]
    lines = []
    for index, scene in enumerate(scenes, 1):
        scene.update(startLine=index, endLine=index, artId="", caption="", artBrief="")
        lines.append({"id": index, "start": 2 * (index - 1), "end": 2 * index, "text": scene["title"]})
    template = {"text": "", "artId": "", "artBrief": "", "items": [], "unit": "",
                "enter": {"line": 0, "word": 0, "effect": "show", "duration": 0},
                "leave": {"line": 0, "word": 0}}
    scenes[1]["layers"] = [
        {**template, "id": "worker", "kind": "figure", "text": "walk", "box": {"x": 0, "y": 0, "w": 24, "h": 90}},
        {**template, "id": "device", "kind": "prop", "text": "laptop", "box": {"x": 29, "y": 3, "w": 24, "h": 50}},
        {**template, "id": "number", "kind": "meter", "box": {"x": 56, "y": 2, "w": 40, "h": 80},
         "items": [{"label": "Employees", "value": 25}]},
    ]
    return {"input": {"duration": 6, "lines": lines}, "assets": [], "plan": {"scenes": scenes}}


class BfgRenderTests(unittest.TestCase):
    def test_missing_browser_does_not_change_backend_and_qualified_primitives(self):
        project = film()
        with patch.dict("os.environ", {"BACCHUS_RENDER_BACKEND": "canvas"}), patch.object(
            browser_cdp, "browser_available", return_value=False
        ):
            self.assertEqual(render.render_backend(), "canvas")
            self.assertFalse(render.render_available())
        scene = project["plan"]["scenes"][1]
        self.assertEqual(len(layer_spec.validate_layers(project, scene)), 3)
        scene["layers"][0]["text"] = "unknown gesture"
        with self.assertRaisesRegex(layer_spec.LayerError, "Choose a BFG figure"):
            layer_spec.validate_layers(project, scene)

    def test_renderer_change_invalidates_review_and_legacy_fails_on_unsupported_scenes(self):
        project = film()
        with patch.dict("os.environ", {"BACCHUS_RENDER_BACKEND": "canvas"}):
            first = review.animation_checks(project)
        with patch.dict("os.environ", {"BACCHUS_RENDER_BACKEND": "pillow"}):
            self.assertNotEqual(first[0]["id"], review.animation_checks(project)[0]["id"])
            self.assertTrue(any("requires the BFG" in finding for finding in render.layout_findings(project, Path("."))))

    @unittest.skipUnless(browser_cdp.browser_available(), "BFG picture check needs Chromium")
    def test_authored_and_structured_scenes_use_the_same_deterministic_bfg_film(self):
        html, assets, _ = render.build_canvas_film(film(), Path("."))
        policy = b'<meta http-equiv="Content-Security-Policy" content="script-src \'unsafe-inline\'; font-src data:">'
        html = html.replace(b"<head>", b"<head>" + policy)

        def capture(browser, session, ready, errors):
            result = browser.eval_js(session, """(() => {
                const f = window.__film;
                function frame(t) { f.render(t); return f.frameB64(); }
                const atGlide = frame(2.33);
                frame(5.1);
                const sameAfterSeek = frame(2.33) === atGlide;
                const sameAfterRepeat = frame(2.33) === atGlide;
                const meter = f.audit(3.5).map(item => item.str);
                const outgoing = f.audit(4).map(item => item.str);
                return { engine: f.engine, profile: f.profile, sameAfterSeek, sameAfterRepeat, meter, outgoing };
            })()""")
            self.assertFalse(errors)
            return result

        result = browser_cdp.run_with_film(html, assets, 0.25, capture)
        self.assertEqual((result["engine"], result["profile"]), ("canvas", "bacchus"))
        self.assertTrue(result["sameAfterSeek"])
        self.assertTrue(result["sameAfterRepeat"])
        self.assertTrue({"walk", "laptop", "Employees", "25"}.issubset(result["meter"]))
        self.assertIn("25", result["outgoing"])
        self.assertNotIn("25%", result["meter"])
