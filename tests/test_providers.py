import os
import json
from unittest import TestCase, mock

from bacchus import planning, providers
from bacchus.settings import DEFAULT_CLAUDE_MODEL


class ProviderTests(TestCase):
    def test_pinned_style_supplies_markdown_and_visual_reference_to_planner(self):
        project = {"input": {"intention": "", "lines": []}, "assets": [],
                   "styleSnapshot": {"name": "My style", "revision": 3, "guide": "# Use a small accent",
                                     "answers": {"music": False}, "palette": {}}}
        with mock.patch.object(planning, "planner_choice", return_value="anthropic"):
            content = planning.plan_request(project, references=[b"example"])["messages"][0]["content"]
        self.assertEqual(content[0]["source"]["data"], "ZXhhbXBsZQ==")
        profile = json.loads(content[1]["text"])["styleProfile"]
        self.assertEqual(profile["guide"], "# Use a small accent")
        self.assertEqual(profile["revision"], 3)

    def test_image_generation_with_style_reference_uses_image_edits(self):
        payload = providers.image_request("My style", "lamp", "A lamp.", guide="Muted colors.", references=[b"example"])
        result = {"data": [{"b64_json": "YXJ0"}]}
        with mock.patch.object(providers, "openai_images_enabled", return_value=True), mock.patch.object(
            providers, "openai_json", return_value=(result, "request"),
        ) as call:
            providers.request_image(payload)
        self.assertEqual(call.call_args.args[0], "images/edits")
        self.assertEqual(payload["quality"], "high")
        self.assertIn("Muted colors.", payload["prompt"])
        self.assertEqual(payload["images"], [{"image_url": "data:image/png;base64,ZXhhbXBsZQ=="}])

    def test_default_planning_uses_claude_with_no_openai_fallback(self):
        project = {"input": {"intention": "Hold on the main subject.", "lines": []}, "assets": []}
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "openai-test"}, clear=True):
            self.assertIsNone(planning.planner_provider())
            self.assertFalse(planning.planner_available())
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "anthropic-test", "OPENAI_API_KEY": "openai-test"}, clear=True):
            self.assertEqual(planning.planner_provider(), "anthropic")
            payload = planning.plan_request(project)
            self.assertEqual(payload["model"], DEFAULT_CLAUDE_MODEL)
            self.assertEqual(payload["output_config"]["effort"], "high")
            self.assertEqual(payload["output_config"]["format"]["type"], "json_schema")
            response = {"stop_reason": "end_turn", "id": "response", "model": payload["model"],
                        "content": [{"type": "text", "text": '{"scenes":[]}'}]}
            with (
                mock.patch.object(planning, "post_json", return_value=(response, "request")) as claude,
                mock.patch.object(planning, "openai_json") as openai,
            ):
                scenes, receipt = planning.request_plan(payload)
        openai.assert_not_called()
        self.assertEqual(claude.call_args.args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(scenes, [])
        self.assertEqual(receipt["model"], DEFAULT_CLAUDE_MODEL)

    def test_openai_planner_request_and_result(self):
        project = {"input": {"intention": "A measured pace.", "lines": []}, "assets": [], "style": "studio-ink"}
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test", "BACCHUS_PLANNER_PROVIDER": "openai"}, clear=True):
            payload = planning.plan_request(project)
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertIn("studio-ink", payload["input"])
        result = {"status": "completed", "id": "response", "model": payload["model"], "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"scenes":[]}'}]},
        ]}
        with mock.patch.object(planning, "openai_json", return_value=(result, "request")) as call:
            scenes, receipt = planning.request_plan(payload)
        call.assert_called_once_with("responses", payload)
        self.assertEqual(scenes, [])
        self.assertEqual(receipt["requestId"], "request")

    def test_image_adapter_decodes_bytes_and_returns_usage(self):
        payload = providers.image_request("Studio ink", "light-pool", "An isolated lamp in profile.")
        self.assertEqual(payload["n"], 1)
        self.assertIn("An isolated lamp", payload["prompt"])
        result = {"data": [{"b64_json": "YXJ0"}], "usage": {"total_tokens": 1}}
        with mock.patch.object(providers, "openai_images_enabled", return_value=True), mock.patch.object(
            providers, "openai_json", return_value=(result, "request"),
        ):
            image, receipt = providers.request_image(payload)
        self.assertEqual(image, b"art")
        self.assertEqual(receipt["usage"], {"total_tokens": 1})
