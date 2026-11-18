import base64
import hashlib
from io import BytesIO
import json
from unittest import TestCase, mock

from PIL import Image

from bacchus import visual_review
from bacchus.settings import DEFAULT_CLAUDE_MODEL


def proof_png():
    with BytesIO() as image_file:
        Image.new("RGB", (160, 90), "#f6efe4").save(image_file, format="PNG")
        return image_file.getvalue()


def findings_for(scope):
    return [
        {"id": check, "status": status, "evidence": "Panel A, time 00:02.000."}
        for check, status in zip(visual_review.CHECKLISTS[scope], ("pass", "fail", "unclear", "pass"), strict=True)
    ]


def provider_result(findings, model=DEFAULT_CLAUDE_MODEL):
    return {
        "stop_reason": "end_turn",
        "content": [
            {"type": "thinking", "thinking": "discarded reasoning"},
            {"type": "text", "text": json.dumps({"findings": findings})},
        ],
        "id": "msg-test",
        "model": model,
        "usage": {"input_tokens": 88, "output_tokens": 12},
    }


class VisualReviewTests(TestCase):
    def setUp(self):
        self.proof = proof_png()
        self.enterContext(mock.patch.dict("os.environ", {
            "ANTHROPIC_API_KEY": "test-credential",
            "BACCHUS_CLAUDE_MODEL": "",
        }, clear=True))

    def test_requests_one_proof_and_returns_advisory_findings(self):
        items = findings_for("asset")
        with mock.patch.object(
            visual_review, "post_json", return_value=(provider_result(items[::-1]), "request-test"),
        ) as send:
            report = visual_review.review("asset", "Bicycle enters facing left.", self.proof)
        endpoint, payload, headers, provider = send.call_args.args
        self.assertEqual(endpoint, "https://api.anthropic.com/v1/messages")
        self.assertEqual(provider, "Anthropic")
        self.assertEqual(headers["x-api-key"], "test-credential")
        self.assertNotIn("test-credential", json.dumps(payload))
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["output_config"]["effort"], "high")
        self.assertEqual(payload["model"], DEFAULT_CLAUDE_MODEL)
        [message] = payload["messages"]
        self.assertEqual(message["role"], "user")
        image, context = message["content"]
        self.assertEqual(image["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(image["source"]["data"]), self.proof)
        self.assertEqual(json.loads(context["text"])["approvedSceneContext"], "Bicycle enters facing left.")
        self.assertEqual(report["evidenceHash"], hashlib.sha256(self.proof).hexdigest())
        self.assertEqual(report["findings"], items)
        self.assertEqual(report["requestId"], "request-test")
        self.assertEqual(report["responseId"], "msg-test")
        self.assertNotIn("approved", report)

    def test_animation_scope_omits_effort_for_selected_override(self):
        model = "claude-sonnet-4-6"
        items = findings_for("animation")
        with mock.patch.dict("os.environ", {"BACCHUS_CLAUDE_MODEL": model}), mock.patch.object(
            visual_review, "post_json", return_value=(provider_result(items, model), "request-test"),
        ) as send:
            review = visual_review.review("animation", "The ball rolls down after the ramp rises.", self.proof)
        payload = send.call_args.args[1]
        self.assertEqual(payload["model"], model)
        self.assertNotIn("effort", payload["output_config"])
        self.assertEqual(review["scope"], "animation")
        self.assertEqual(review["findings"], items)

    def test_rejects_invalid_proof_before_egress(self):
        with mock.patch.object(visual_review, "post_json") as send:
            with self.assertRaisesRegex(visual_review.VisualReviewError, "readable PNG"):
                visual_review.review("animation", "Accepted scene brief.", b"not a real image")
            with self.assertRaisesRegex(visual_review.VisualReviewError, "up to 12,000"):
                visual_review.review("animation", "x" * 12_001, self.proof)
            with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
                self.assertFalse(visual_review.available())
                with self.assertRaisesRegex(visual_review.VisualReviewError, "Anthropic API key"):
                    visual_review.review("animation", "Accepted scene brief.", self.proof)
            send.assert_not_called()

    def test_rejects_missing_and_duplicate_checks_and_incomplete_response(self):
        all_items = findings_for("animation")
        invalid = (
            provider_result(all_items[:-1]),
            provider_result(all_items[:-1] + all_items[:1]),
            {**provider_result(all_items), "stop_reason": "max_tokens"},
        )
        for result in invalid:
            with self.subTest(result=result), mock.patch.object(
                visual_review, "post_json", return_value=(result, "request-test"),
            ):
                with self.assertRaises(visual_review.VisualReviewError):
                    visual_review.review("animation", "Accepted scene brief.", self.proof)
