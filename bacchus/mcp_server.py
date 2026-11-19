"""MCP tools over UTF-8, newline-delimited JSON-RPC on binary stdio streams."""

import base64
import json

from . import mcp_tools
from .runtime import APP_VERSION


PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_BATCH_ITEMS = 64


class _ProtocolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _serialize(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant {value}")


class _Session:
    def __init__(self, client):
        self.client = client
        self.version = None
        self.ready = False

    def initialize(self, params):
        if self.version is not None:
            raise _ProtocolError(-32600, "This session has already been initialized.")
        version = params.get("protocolVersion")
        info = params.get("clientInfo")
        if not (isinstance(version, str) and version and isinstance(params.get("capabilities"), dict)
                and isinstance(info, dict) and isinstance(info.get("name"), str)
                and isinstance(info.get("version"), str)):
            raise _ProtocolError(-32602, "initialize requires protocolVersion, capabilities and clientInfo.")
        self.version = version if version in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": self.version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bacchus", "version": APP_VERSION},
        }

    def request(self, method, params):
        if method == "ping":
            return {}
        if method == "initialize":
            return self.initialize(params)
        if method not in ("tools/list", "tools/call"):
            raise _ProtocolError(-32601, f"Unknown method: {method}")
        if not self.ready:
            raise _ProtocolError(-32002, "Complete initialization before using tools.")
        if method == "tools/list":
            if "cursor" in params:
                raise _ProtocolError(-32602, "The tools list has no further pages.")
            return {"tools": mcp_tools.TOOLS}

        name = params.get("name")
        if not isinstance(name, str):
            raise _ProtocolError(-32602, "Tool name must be a string.")
        if not any(tool["name"] == name for tool in mcp_tools.TOOLS):
            raise _ProtocolError(-32602, f"Unknown tool: {name}")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            raise _ProtocolError(-32602, "Tool arguments must be an object.")
        try:
            value = mcp_tools.call_tool(self.client, name, arguments)
        except (ValueError, RuntimeError, OSError) as error:
            return {"isError": True, "content": [{"type": "text", "text": str(error) or "Tool execution failed."}]}
        if name == "preview_frame" and isinstance(value, bytes):
            image = {"type": "image", "mimeType": "image/png", "data": base64.b64encode(value).decode("ascii")}
            return {"content": [image]}
        result = {"content": [{"type": "text", "text": _serialize(value)}]}
        if self.version in ("2025-06-18", "2025-11-25") and isinstance(value, dict):
            result["structuredContent"] = value
        return result

    def message(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "Expected a JSON-RPC 2.0 message.")
        request_id = message.get("id")
        if "id" in message and type(request_id) not in (int, str):
            return _error(None, -32600, "A request ID must be a string or integer.")
        method = message.get("method")
        if not isinstance(method, str):
            # This server never sends requests to the client. Do not answer a response.
            if "method" not in message and "id" in message and ("result" in message or "error" in message):
                return None
            return _error(request_id, -32600, "A request must name a method.")
        params = message.get("params", {})
        if "id" not in message:
            if method == "notifications/initialized" and self.version and isinstance(params, dict):
                self.ready = True
            return None
        if not isinstance(params, dict):
            return _error(request_id, -32602, "MCP request parameters must be an object.")
        try:
            result = self.request(method, params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except _ProtocolError as error:
            return _error(request_id, error.code, str(error))
        except Exception:
            return _error(request_id, -32603, "Internal error while handling this request.")

    def dispatch(self, value):
        if not isinstance(value, list):
            return self.message(value)
        # Only the March 2025 revision requires batches; initialize must always stand alone.
        if self.version != "2025-03-26" or not value:
            return _error(None, -32600, "A nonempty batch requires a negotiated March 2025 session.")
        if len(value) > MAX_BATCH_ITEMS:
            return _error(None, -32600, f"A batch can contain at most {MAX_BATCH_ITEMS} items.")
        replies = [self.message(item) for item in value]
        return [reply for reply in replies if reply is not None] or None


def serve(client, reader, writer):
    """Serve one MCP session until EOF. The caller owns the binary streams and studio client."""
    session = _Session(client)
    while raw := reader.readline(MAX_FRAME_BYTES + 1):
        if len(raw) > MAX_FRAME_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = reader.readline(65536)
            reply = _error(None, -32600, "MCP message exceeds the size limit.")
        elif not raw.endswith(b"\n"):
            reply = _error(None, -32700, "Incomplete newline-delimited JSON message.")
        else:
            try:
                value = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant)
            except (ValueError, RecursionError):
                reply = _error(None, -32700, "Invalid UTF-8 JSON.")
            else:
                reply = session.dispatch(value)
        if reply is not None:
            try:
                writer.write((_serialize(reply) + "\n").encode("utf-8"))
                writer.flush()
            except BrokenPipeError:
                return
