from http.client import IncompleteRead
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
from urllib.error import HTTPError, URLError

from bacchus import api_log, providers, settings


URL = "https://api.anthropic.com/v1/messages"


class Response(BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.headers = {"Content-Type": "application/json", "request-id": "req-test"}

    def getcode(self):
        return 200


class TransportTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="bacchus-transport-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.opener = mock.Mock()
        self.enterContext(mock.patch.object(settings, "api_log_directory", return_value=self.root))
        self.enterContext(mock.patch.object(settings, "provider_secrets", return_value=("other-known-secret",)))
        self.enterContext(mock.patch.object(providers, "build_opener", return_value=self.opener))

    def result(self):
        [folder] = list(self.root.iterdir())
        info = json.loads((folder / "metadata.json").read_bytes())
        return folder, info

    def test_audits_before_egress_without_auth_headers(self):
        def outgoing(request, *, timeout):
            folder, info = self.result()
            self.assertEqual(info["state"], "pending")
            self.assertEqual(info["scope"]["stage"], "visual-review")
            self.assertEqual(request.get_header("X-api-key"), "current-key")
            self.assertNotIn("current-key", (folder / "metadata.json").read_text())
            self.assertEqual((folder / "request.body").read_bytes(), b'{"prompt": "[REDACTED]"}')
            return Response(b'{"answer":"other-known-secret"}')

        self.opener.open.side_effect = outgoing
        with api_log.scope("project", "visual-review"):
            body, receipt = providers.post_json(
                URL, {"prompt": "current-key"}, {"x-api-key": "current-key"}, "Anthropic",
            )
        self.assertEqual(body, {"answer": "other-known-secret"})
        self.assertEqual(receipt, "req-test")
        folder, info = self.result()
        self.assertEqual(info["state"], "completed")
        self.assertEqual(info["request"]["contentType"], "application/json")
        self.assertEqual(info["response"]["status"], 200)
        self.assertEqual(info["response"]["contentType"], "application/json")
        self.assertEqual((folder / "response.body").read_bytes(), b'{"answer":"[REDACTED]"}')

    def test_http_error_retains_bounded_response_body(self):
        response = Response(b'{"error":"too long"}')
        self.opener.open.side_effect = HTTPError(URL, 400, "Bad request", response.headers, response)
        with self.assertRaisesRegex(providers.ProviderError, "HTTP 400"):
            providers.post_json(URL, {}, {}, "Anthropic", limit=6)
        folder, info = self.result()
        self.assertEqual(info["state"], "failed")
        self.assertEqual(info["response"]["status"], 400)
        self.assertEqual(info["response"]["error"], "http_error")
        self.assertTrue(info["response"]["truncated"])
        self.assertEqual((folder / "response.body").read_bytes(), b'{"erro')

    def test_read_failure_retains_available_partial_bytes(self):
        response = Response(b'{"partial"')
        response.headers["Content-Length"] = "100"
        self.opener.open.return_value = response
        with self.assertRaisesRegex(providers.ProviderError, "response ended"):
            providers.post_json(URL, {}, {}, "Anthropic")
        folder, info = self.result()
        self.assertEqual(info["state"], "failed")
        self.assertEqual(info["response"]["status"], 200)
        self.assertEqual(info["response"]["error"], "response_read_error")
        self.assertTrue(info["response"]["truncated"])
        self.assertEqual((folder / "response.body").read_bytes(), b'{"partial"')

    def test_chunked_read_failure_retains_partial_bytes(self):
        response = Response(b"")
        self.opener.open.return_value = response
        with mock.patch.object(response, "read", side_effect=IncompleteRead(b"{", 100)) as read:
            with self.assertRaisesRegex(providers.ProviderError, "response ended"):
                providers.post_json(URL, {}, {}, "Anthropic")
        read.assert_called_once_with(512 * 1024 + 1)
        folder, info = self.result()
        self.assertTrue(info["response"]["truncated"])
        self.assertEqual((folder / "response.body").read_bytes(), b"{")

    def test_network_failure_has_no_raw_exception(self):
        self.opener.open.side_effect = URLError("unexpected raw secret text")
        with self.assertRaisesRegex(providers.ProviderError, "Provider status is unknown"):
            providers.post_json(URL, {}, {}, "Anthropic")
        folder, info = self.result()
        self.assertEqual(info["response"]["error"], "network_error")
        self.assertIsNone(info["response"]["status"])
        self.assertNotIn("unexpected raw secret text", (folder / "metadata.json").read_text())

    def test_audit_failure_blocks_provider_network_request(self):
        with mock.patch.object(api_log, "start", side_effect=api_log.ApiLogError):
            with self.assertRaisesRegex(providers.ProviderError, "writable API log"):
                providers.post_json(URL, {}, {}, "Anthropic")
        self.opener.open.assert_not_called()
