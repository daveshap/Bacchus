import os
from unittest import TestCase, mock

from bacchus import planning, providers


class ProviderTests(TestCase):
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
        with mock.patch.object(providers, "openai_json", return_value=(result, "request")):
            image, receipt = providers.request_image(payload)
        self.assertEqual(image, b"art")
        self.assertEqual(receipt["usage"], {"total_tokens": 1})
