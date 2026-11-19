import base64
import io
import json
import unittest
from unittest.mock import Mock, patch

from bacchus import mcp_server


def request(method, request_id, params=None):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {} if params is None else params}


def initialize(version):
    return request("initialize", 0, {
        "protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
    })


READY = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class McpStdioTests(unittest.TestCase):
    def exchange(self, *messages):
        raw = b"".join(
            item if isinstance(item, bytes) else (json.dumps(item) + "\n").encode("utf-8") for item in messages
        )
        out = io.BytesIO()
        mcp_server.serve(self.client, io.BytesIO(raw), out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def setUp(self):
        self.client = Mock()
        self.tool = {"name": "echo", "description": "Return a project", "inputSchema": {"type": "object"}}
        self.enterContext(patch.object(mcp_server.mcp_tools, "TOOLS", [self.tool]))
        self.call = self.enterContext(patch.object(mcp_server.mcp_tools, "call_tool"))

    def test_initialization_negotiates_versions_without_opening_the_studio(self):
        for version in (*mcp_server.PROTOCOL_VERSIONS, "2099-01-01"):
            with self.subTest(version=version):
                handshake, listing, ping = self.exchange(
                    initialize(version), READY, request("tools/list", "list"), request("ping", 2)
                )
                expected = version if version in mcp_server.PROTOCOL_VERSIONS else mcp_server.PROTOCOL_VERSIONS[0]
                self.assertEqual(handshake["result"]["protocolVersion"], expected)
                self.assertEqual(handshake["result"]["capabilities"], {"tools": {}})
                self.assertEqual(handshake["id"], 0)
                self.assertTrue(handshake["result"]["serverInfo"]["version"])
                self.assertEqual(listing, {"jsonrpc": "2.0", "id": "list", "result": {"tools": [self.tool]}})
                self.assertEqual(ping, {"jsonrpc": "2.0", "id": 2, "result": {}})
        self.assertEqual(self.client.mock_calls, [])
        self.call.assert_not_called()

    def test_calls_need_handshake_and_ready_notification(self):
        self.call.return_value = {"title": "A line\nbreak and café"}
        # ID-less calls (and unknown notifications) never run a tool or emit a reply.
        notification = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "echo"}}
        call = request("tools/call", "run", {"name": "echo", "arguments": {"project_id": "p"}})
        early, _, waiting, final = self.exchange(
            READY, request("tools/list", 1), initialize("2025-11-25"), {**call, "id": "waiting"}, READY,
            notification, {"jsonrpc": "2.0", "method": "notifications/cancelled"}, call,
        )
        self.assertEqual(early["error"]["code"], -32002)
        self.assertEqual(waiting["error"]["code"], -32002)
        self.assertEqual(final["result"]["structuredContent"], self.call.return_value)
        self.assertEqual(json.loads(final["result"]["content"][0]["text"]), self.call.return_value)
        self.call.assert_called_once_with(self.client, "echo", {"project_id": "p"})

    def test_execution_errors_are_tool_results_and_protocol_errors_preserve_id(self):
        self.call.side_effect = ValueError("Choose an existing project.")
        _, bad_name, bad_args, tool_error, missing, cursor = self.exchange(
            initialize("2024-11-05"), READY,
            request("tools/call", 1, {"name": "missing"}),
            request("tools/call", 2, {"name": "echo", "arguments": []}),
            request("tools/call", 3, {"name": "echo"}), request("resources/list", "x"),
            request("tools/list", 4, {"cursor": "missing-page"}),
        )
        self.assertEqual([(r["id"], r["error"]["code"]) for r in (bad_name, bad_args, missing, cursor)],
                         [(1, -32602), (2, -32602), ("x", -32601), (4, -32602)])
        self.assertEqual(tool_error["result"], {
            "isError": True, "content": [{"type": "text", "text": "Choose an existing project."}],
        })
        self.call.assert_called_once_with(self.client, "echo", {})

    def test_bad_frames_are_bounded_and_the_next_frame_is_read(self):
        with patch.object(mcp_server, "MAX_FRAME_BYTES", 128):
            responses = self.exchange(
                b" " * 4096 + b"\n", b"\xff\n", b'{"field":NaN}\n',
                {"jsonrpc": "2.0", "method": "ping", "id": False}, {"jsonrpc": "2.0", "method": "ping", "id": None},
                b'{"jsonrpc":"2.0","method":"ping","id":1}\r\n', b'{"jsonrpc":"2.0","method":"ping","id":2}',
            )
        self.assertEqual([msg.get("error", {}).get("code") for msg in responses],
                         [-32600, -32700, -32700, -32600, -32600, None, -32700])
        self.assertEqual(responses[5]["id"], 1)
        self.assertTrue(all(msg["id"] is None for msg in responses if "error" in msg))
        self.assertEqual(self.exchange(), [])

    def test_preview_frame_is_native_mcp_image_content(self):
        self.tool["name"] = "preview_frame"
        self.call.return_value = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
        _, result = self.exchange(initialize("2024-11-05"), READY, request("tools/call", 1, {"name": "preview_frame"}))
        image, = result["result"]["content"]
        self.assertEqual((image["type"], image["mimeType"]), ("image", "image/png"))
        self.assertEqual(base64.b64decode(image["data"], validate=True), self.call.return_value)

    def test_only_march_session_accepts_batches_and_skips_notifications(self):
        ping = request("ping", 4)
        notice = {"jsonrpc": "2.0", "method": "notifications/cancelled"}
        rejected, _, replies = self.exchange(
            [initialize("2025-03-26")], initialize("2025-03-26"), READY,
            [notice, ping, request("tools/list", "all")], [notice, notice],
        )
        self.assertEqual(rejected["error"]["code"], -32600)
        self.assertEqual([item["id"] for item in replies], [4, "all"])
        with patch.object(mcp_server, "MAX_BATCH_ITEMS", 1):
            rejected = self.exchange(initialize("2025-03-26"), READY, [notice, ping])[-1]
            self.assertEqual(rejected["error"]["code"], -32600)
        for version in ("2024-11-05", "2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                self.assertEqual(self.exchange(initialize(version), READY, [ping])[-1]["error"]["code"], -32600)

    def test_malformed_initialization_can_be_retried_and_internal_errors_are_sanitized(self):
        self.call.side_effect = KeyError("private details")
        malformed, _, already, failed = self.exchange(
            request("initialize", "first", {"protocolVersion": "2025-11-25"}),
            initialize("2025-11-25"), READY, {**initialize("2025-06-18"), "id": "again"},
            request("tools/call", "call", {"name": "echo"}),
        )
        self.assertEqual(malformed["error"]["code"], -32602)
        self.assertEqual(already["error"]["code"], -32600)
        self.assertEqual(failed["error"]["code"], -32603)
        self.assertNotIn("private details", str(failed))
