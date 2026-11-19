import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from bacchus.mcp_tools import TOOLS, ToolError, call_tool


PID = "a" * 32
DELIVERY = "b" * 64
ROUTE = f"/api/projects/{PID}"
EDIT = {"project_id": PID, "expected_revision": 7}
PROJECT = {"id": PID, "title": "One video", "autoMode": True, "revision": 8}


class McpToolTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(base_url="http://127.0.0.1:9001")
        self.client.request.return_value = PROJECT
        temporary = TemporaryDirectory(prefix="bacchus-mcp-tools-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def tool(self, name, **args):
        return call_tool(self.client, name, args)

    def test_creation_pins_requested_style_without_starting_a_provider(self):
        self.client.request.side_effect = [{"revision": 3}, PROJECT]
        result = self.tool("create_project", title="One video", style_id="warm-editorial", brief="Show motion.",
                           limits={"maxTokens": 0})
        self.assertEqual((result["id"], result["revision"]), (PID, 8))
        self.assertEqual(len(self.client.request.call_args_list), 2)
        self.assertEqual(self.client.request.call_args_list[0].args, ("GET", "/api/styles/warm-editorial"))
        self.client.request.assert_called_with("POST", "/api/projects", data={
            "title": "One video", "autoMode": True, "autoBrief": "Show motion.", "autoLimits": {"maxTokens": 0},
            "styleId": "warm-editorial", "styleRevision": 3,
        })

    def test_tools_reject_unsupported_arguments_and_invalid_revisions_before_request(self):
        self.assertEqual(len({tool["name"] for tool in TOOLS}), len(TOOLS))
        for tool in TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        for name, args in (
            ("run_auto", {"project_id": PID}), ("run_auto", {**EDIT, "expected_revision": True}),
            ("run_auto", {**EDIT, "project_id": "other"}), ("create_project", {"title": "x", "style_revision": 1}),
            ("create_project", {"title": "x", "limits": {"maxUsd": -1}}),
            ("list_projects", {"limit": 200}), ("run_auto", {**EDIT, "approve": True}),
            ("get_settings", {}), ("set_auto_settings", EDIT),
        ):
            with self.subTest(tool=name, args=args), self.assertRaises(ToolError):
                self.tool(name, **args)
        self.client.request.assert_not_called()

    def test_audio_and_timing_use_regular_file_stream_and_expected_revision(self):
        for tool, filename, route, mime in (
            ("attach_narration", "Scene #1 & 2.MP3", "audio", "audio/mpeg"),
            ("import_timings", "timing.json", "timings", "application/json"),
        ):
            path = self.folder / filename
            path.write_bytes(b"example upload")
            self.tool(tool, **EDIT, path=str(path))
            method, url = self.client.request.call_args.args
            parts = urlsplit(url)
            self.assertEqual((method, parts.path), ("POST", f"{ROUTE}/{route}"))
            self.assertEqual(parse_qs(parts.query), {"revision": ["7"], **({"name": [filename]} if route == "audio" else {})})
            self.assertEqual(self.client.request.call_args.kwargs, {"file": path.resolve(), "content_type": mime})

    def test_unsupported_or_empty_uploads_never_reach_the_studio(self):
        folder = self.folder / "folder.wav"
        folder.mkdir()
        empty = self.folder / "empty.mp3"
        empty.touch()
        big = self.folder / "big.json"
        with big.open("wb") as stream:
            stream.truncate(4 * 1024 * 1024 + 1)
        for tool, path in (("attach_narration", "relative.mp3"), ("attach_narration", str(folder)),
                           ("attach_narration", str(empty)), ("import_timings", str(big)),
                           ("attach_narration", "https://example.invalid/voice.mp3"),
                           ("attach_narration", r"\\host\share\voice.mp3"),
                           ("attach_narration", r"\\?\C:\work\voice.mp3"), ("attach_narration", str(big))):
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.tool(tool, **EDIT, path=path)
        if hasattr(os, "mkfifo"):
            pipe = self.folder / "pipe.wav"
            os.mkfifo(pipe)
            with self.assertRaises(ToolError):
                self.tool("attach_narration", **EDIT, path=str(pipe))
        if os.name == "nt":
            # A drive letter or link can resolve to a network share. Do not open that target.
            with patch.object(Path, "resolve", return_value=Path(r"\\host\share\remote.wav")), \
                    self.assertRaises(ToolError):
                self.tool("attach_narration", **EDIT, path=str(self.folder / "mapped.wav"))
        self.client.request.assert_not_called()

    def test_manual_export_and_auto_run_remain_separate(self):
        with self.assertRaisesRegex(ToolError, "run_auto"):
            self.tool("render_accepted", **EDIT)
        self.client.request.assert_called_once_with("GET", ROUTE)
        self.client.request.return_value = {**PROJECT, "autoMode": False}
        self.tool("render_accepted", **EDIT)
        self.client.request.assert_called_with("POST", ROUTE + "/export", data={"revision": 7})
        self.tool("run_auto", **EDIT)
        self.client.request.assert_called_with("POST", ROUTE + "/auto/run", data={"revision": 7})

    def test_settings_preserve_omitted_fields_and_pass_the_original_revision(self):
        self.client.request.side_effect = [{**PROJECT, "autoBrief": "Keep this brief"}, PROJECT]
        self.tool("set_auto_settings", **EDIT, limits={"maxUsd": 1})
        self.client.request.assert_called_with("POST", ROUTE + "/auto/settings",
                                              data={"revision": 7, "autoBrief": "Keep this brief", "autoLimits": {"maxUsd": 1}})

    def test_summaries_and_pagination_exclude_credentials_and_proof_payloads(self):
        for provider, model in (("openai", "gpt-model"), ("anthropic", "claude-model")):
            self.client.request.side_effect = [
                {"version": "0.8", "plannerAvailable": False, "plannerProvider": None, "apiKey": "private"},
                {"plannerProvider": provider, "imageModel": "image-model", "openAIModel": "gpt-model",
                 "claudeModel": "claude-model", "openAIKey": "private", "dataPath": str(self.folder)}]
            self.assertEqual(self.tool("get_readiness"), {
                "version": "0.8", "plannerAvailable": False, "plannerProvider": provider, "plannerModel": model,
                "imageModel": "image-model", "dataPath": str(self.folder)})
        self.client.request.side_effect = None
        self.client.request.return_value = {"projects": [PROJECT, {**PROJECT, "id": "c" * 32, "raw": "private"}]}
        page = self.tool("list_projects", limit=1)
        self.assertEqual((page["total"], page["nextOffset"], len(page["projects"])), (2, 1, 1))
        self.client.request.return_value = {"styles": [{"id": "style", "revision": 4, "guide": "private"}]}
        self.assertEqual(self.tool("list_styles")["styles"], [{"id": "style", "revision": 4}])
        self.client.request.return_value = {**PROJECT, "input": {"script": "private"},
            "audio": {"originalHash": "a" * 64, "url": "private"}, "autoRun": {"checks": [
                {"stage": "assets", "item": "cup", "status": "failed", "proof": "private",
                 "findings": [{"id": "fit", "status": "fail", "evidence": "Tiny title"}]}]}}
        summary = self.tool("get_project", project_id=PID)
        self.assertNotIn("private", str(summary))
        self.assertEqual(summary["autoRun"]["lastCheck"]["findings"][0]["evidence"], "Tiny title")
        self.assertEqual(summary["audio"]["originalHash"], "a" * 64)

    def test_preview_returns_renderer_png_and_validates_time(self):
        self.client.preview_frame.return_value = b"preview png"
        self.assertEqual(self.tool("preview_frame", project_id=PID, seconds=0.125), b"preview png")
        self.client.preview_frame.assert_called_once_with(PID, 0.125)
        for invalid in (False, float("nan"), float("inf"), -1, 4000):
            with self.assertRaises(ToolError):
                self.tool("preview_frame", project_id=PID, seconds=invalid)

    def test_only_verified_delivery_routes_and_files_are_returned(self):
        files = {kind: str(self.folder / filename) for kind, filename in
                 (("video", "video.mp4"), ("captions", "captions.srt"), ("receipt", "receipt.json"))}
        latest = {"deliveryId": DELIVERY, "status": "complete", "verification": "verified",
                  "url": f"{ROUTE}/media/video/{DELIVERY}", "captionsUrl": f"{ROUTE}/media/captions/{DELIVERY}",
                  "receiptUrl": f"{ROUTE}/media/receipt/{DELIVERY}"}
        project = {**PROJECT, "export": {"status": "running"}, "lastDelivery": latest}
        self.client.request.side_effect = [project, {"files": files}]
        result = self.tool("get_delivery", project_id=PID)
        self.assertFalse(result["current"])
        self.assertEqual(result["files"], files)
        self.assertEqual(result["urls"]["video"], self.client.base_url + latest["url"])
        self.client.request.assert_called_with("GET", f"{ROUTE}/delivery-files/{DELIVERY}")
        self.client.request.side_effect = [{**PROJECT, "export": {**latest, "verification": "corrupt"}}]
        self.assertFalse(self.tool("get_delivery", project_id=PID)["available"])
        for wrong in ("https://example.invalid/movie.mp4", f"/api/projects/{'c' * 32}/media/video/{DELIVERY}"):
            with self.subTest(url=wrong), self.assertRaises(ToolError):
                self.client.request.side_effect = [{**project, "lastDelivery": {**latest, "url": wrong}}]
                self.tool("get_delivery", project_id=PID)
