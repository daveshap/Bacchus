import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock, skipIf

from bacchus import api_log


PROVIDER_URL = "https://api.example.com/v1/messages"


def metadata(call):
    return json.loads((call.path / "metadata.json").read_bytes())


class ApiLogTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="bacchus-api-log-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_exact_request_and_response_are_logged_with_scope(self):
        request = b"----sample\r\nContent-Type: audio/wav\r\n\r\n\x00\xff"
        response = b'{"text": "\\u00f1", "usage": 4}\n'
        with api_log.scope("abc", "transcription"):
            call = api_log.start(self.root, "OpenAI", PROVIDER_URL, request, "multipart/form-data")
        pending = metadata(call)
        self.assertEqual(pending["scope"], {"projectId": "abc", "stage": "transcription"})
        self.assertEqual(pending["state"], "pending")
        self.assertIsNone(pending["response"])
        self.assertEqual((call.path / pending["request"]["bodyFile"]).read_bytes(), request)
        call.finish(200, response, content_type="application/json", request_id="req-test")
        completed = metadata(call)
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["response"]["requestId"], "req-test")
        self.assertEqual(completed["response"]["status"], 200)
        self.assertEqual((call.path / completed["response"]["bodyFile"]).read_bytes(), response)
        another = api_log.start(self.root, "OpenAI", PROVIDER_URL, b"{}", "application/json")
        self.assertIsNone(metadata(another)["scope"])
        self.assertNotEqual(call.path, another.path)

    def test_credential_redaction_and_truncated_response(self):
        secret = 'test-"ñ"-secret'
        raw = json.dumps({"prompt": secret}, ensure_ascii=True).encode()
        call = api_log.start(self.root, "Claude", PROVIDER_URL, raw, "application/json", (secret,))
        self.assertEqual(json.loads((call.path / "request.body").read_bytes())["prompt"], "[REDACTED]")
        call.finish(500, secret.encode(), request_id=secret, error=f"Raw exception {secret}", truncated=True)
        record = metadata(call)
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["response"]["error"], "provider_request_failed")
        self.assertTrue(record["response"]["truncated"])
        self.assertTrue(record["response"]["redacted"])
        self.assertEqual(record["response"]["requestId"], "[REDACTED]")
        self.assertEqual((call.path / "response.body").read_bytes(), b"[REDACTED]")

    def test_disk_failure_preserves_pending_state(self):
        obstructed = self.root / "file"
        obstructed.write_bytes(b"existing")
        with self.assertRaises(api_log.ApiLogError):
            api_log.start(obstructed, "Claude", PROVIDER_URL, b"{}", "application/json")
        call = api_log.start(self.root, "Claude", PROVIDER_URL, b"{}", "application/json")
        with mock.patch.object(api_log, "_write", side_effect=OSError):
            with self.assertRaises(api_log.ApiLogError):
                call.finish(200, b"{}")
        self.assertEqual(metadata(call)["state"], "pending")

    @skipIf(os.name == "nt", "Unix-only ownership and mode check")
    def test_default_log_folder_is_private_and_uid_scoped(self):
        with mock.patch.object(api_log, "gettempdir", return_value=str(self.root)):
            directory = api_log.default_directory()
            self.assertIn(str(os.getuid()), directory.name)
            call = api_log.start(directory, "Claude", PROVIDER_URL, b"{}", "application/json")
            self.assertEqual(directory.stat().st_mode & 0o077, 0)
            self.assertEqual(call.path.stat().st_mode & 0o077, 0)
            directory.chmod(0o755)
            with self.assertRaisesRegex(api_log.ApiLogError, "private folder"):
                api_log.start(directory, "Claude", PROVIDER_URL, b"{}", "application/json")
