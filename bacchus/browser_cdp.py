"""Zero-dependency headless Chromium CDP runner and multi-worker PNG frame pipe.

Mirrors the BFG_Animations / AnimationAutomationStudio Playwright+WebSocket+FFmpeg
rendering architecture using pure Python stdlib so Bacchus stays self-contained
without external Node or Playwright runtime packages.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import numpy as np

from . import media_tools
from .storage_paths import StoragePathError, scratch_directory, writable_path


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_WORKERS = min(6, max(4, (os.cpu_count() or 4) // 2))
MAX_WORKERS = 12


class BrowserError(RuntimeError):
    """Headless browser startup, page execution, or frame capture failed."""


def _candidate_browsers() -> list[Path]:
    candidates: list[Path] = []
    for env_key in ("BACCHUS_CHROME", "CHROME"):
        value = os.environ.get(env_key, "").strip()
        if value:
            path = Path(value)
            if path.is_absolute():
                candidates.append(path)
    if sys.platform == "win32":
        program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        local_app = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        candidates.extend([
            program_files / "Google" / "Chrome" / "Application" / "chrome.exe",
            program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe",
            local_app / "Google" / "Chrome" / "Application" / "chrome.exe",
            program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            program_files / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        ])
        pw_root = local_app / "ms-playwright"
        if pw_root.is_dir():
            for exe in sorted(pw_root.glob("chromium-*/chrome-win*/chrome.exe"), reverse=True):
                candidates.append(exe)
    elif sys.platform == "darwin":
        candidates.extend([
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ])
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            for directory in ("/usr/bin", "/usr/local/bin", "/snap/bin"):
                candidates.append(Path(directory) / name)
    return candidates


def find_browser() -> Path | None:
    for path in _candidate_browsers():
        if path.is_file():
            return path
    return None


def browser_available() -> bool:
    return find_browser() is not None


def browser_label() -> str | None:
    path = find_browser()
    if not path:
        return None
    name = path.name.lower()
    if "chrome" in name:
        return "Google Chrome"
    if "msedge" in name or "edge" in name:
        return "Microsoft Edge"
    if "brave" in name:
        return "Brave"
    return path.stem


# ── Minimal RFC 6455 WebSocket Framing (Client + Server) ──────────────────────────────────────

def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise EOFError("WebSocket connection closed unexpectedly.")
        chunks.extend(chunk)
    return bytes(chunks)


def _xor_mask(payload: bytes, mask_key: bytes) -> bytes:
    """Fast vectorized WebSocket mask/unmask via NumPy (~1000x faster than big-int XOR)."""
    n = len(payload)
    if not n:
        return b""
    arr = np.frombuffer(payload, dtype=np.uint8)
    rem = n & 3
    if n >= 4:
        full = n - rem
        u32 = np.frombuffer(payload, dtype=np.uint32, count=full // 4)
        k32 = np.frombuffer(mask_key, dtype=np.uint32, count=1)[0]
        head = np.bitwise_xor(u32, k32).tobytes()
    else:
        head = b""
    if rem:
        tail = bytes(arr[n - rem + i] ^ mask_key[i] for i in range(rem))
        return head + tail
    return head


def _ws_read_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Read one complete RFC 6455 message, reassembling fragmented frames."""
    message_opcode = None
    fragments = bytearray()
    while True:
        header = _recv_exact(sock, 2)
        b0, b1 = header[0], header[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        mask_key = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, length) if length else b""
        if masked and payload:
            payload = _xor_mask(payload, mask_key)
        if opcode == 0x9:  # Ping -> send Pong immediately
            _ws_write_frame(sock, 0xA, payload, mask=not masked)
            continue
        if opcode == 0xA:  # Pong
            continue
        if opcode == 0x8:  # Close
            return 0x8, payload
        if opcode != 0x0:
            message_opcode = opcode
        fragments.extend(payload)
        if fin:
            return message_opcode if message_opcode is not None else 0x1, bytes(fragments)


def _ws_write_frame(sock: socket.socket, opcode: int, payload: bytes, *, mask: bool) -> None:
    b0 = 0x80 | (opcode & 0x0F)
    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header = struct.pack("!BB", b0, mask_bit | length)
    elif length < 65536:
        header = struct.pack("!BBH", b0, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", b0, mask_bit | 127, length)
    if mask:
        mask_key = os.urandom(4)
        masked = _xor_mask(payload, mask_key) if payload else b""
        sock.sendall(header + mask_key + masked)
    else:
        sock.sendall(header + payload)


class _CDPClient:
    """Thread-safe Chrome DevTools Protocol client over loopback WebSocket."""

    def __init__(self, ws_url: str, timeout: float = 120.0):
        # Parse ws://127.0.0.1:<port><path>
        if not ws_url.startswith("ws://"):
            raise BrowserError(f"Unsupported CDP URL: {ws_url}")
        rest = ws_url[5:]
        host_port, _, path = rest.partition("/")
        host, _, port_str = host_port.partition(":")
        port = int(port_str)
        if host not in ("127.0.0.1", "localhost"):
            raise BrowserError("Refusing non-loopback CDP connection.")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(handshake)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserError("Chrome closed the DevTools handshake.")
            response.extend(chunk)
        header_bytes, _, leftover = bytes(response).partition(b"\r\n\r\n")
        if b" 101 " not in header_bytes.splitlines()[0]:
            raise BrowserError("Chrome rejected the DevTools WebSocket upgrade.")
        if leftover:
            # Rare: CDP sent a frame immediately after 101; wrap socket with prefix buffer
            self._prefix = bytearray(leftover)
        else:
            self._prefix = bytearray()
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, tuple[threading.Event, list]] = {}
        self._event_handlers: dict[str, list] = {}
        self._closed = False
        self._reader_error: Exception | None = None
        self._reader = threading.Thread(target=self._read_loop, name="bacchus-cdp", daemon=True)
        self._reader.start()

    def _recv_exact_buffered(self, count: int) -> bytes:
        chunks = bytearray()
        if self._prefix:
            take = min(count, len(self._prefix))
            chunks.extend(self._prefix[:take])
            del self._prefix[:take]
        while len(chunks) < count:
            chunk = self.sock.recv(count - len(chunks))
            if not chunk:
                raise EOFError("CDP socket closed.")
            chunks.extend(chunk)
        return bytes(chunks)

    def _read_message(self) -> tuple[int, bytes]:
        message_opcode = None
        fragments = bytearray()
        while True:
            b0, b1 = self._recv_exact_buffered(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact_buffered(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact_buffered(8))[0]
            mask_key = self._recv_exact_buffered(4) if masked else b""
            payload = self._recv_exact_buffered(length) if length else b""
            if masked and payload:
                payload = _xor_mask(payload, mask_key)
            if opcode == 0x9:
                with self._send_lock:
                    _ws_write_frame(self.sock, 0xA, payload, mask=True)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                return 0x8, payload
            if opcode != 0x0:
                message_opcode = opcode
            fragments.extend(payload)
            if fin:
                return message_opcode if message_opcode is not None else 0x1, bytes(fragments)

    def _read_loop(self):
        try:
            while not self._closed:
                opcode, payload = self._read_message()
                if opcode == 0x8:
                    break
                if opcode != 0x1:
                    continue
                msg = json.loads(payload.decode("utf-8"))
                if "id" in msg:
                    with self._pending_lock:
                        entry = self._pending.pop(msg["id"], None)
                    if entry:
                        event, box = entry
                        box.append(msg)
                        event.set()
                elif "method" in msg:
                    method = msg["method"]
                    with self._pending_lock:
                        handlers = list(self._event_handlers.get(method, ()))
                    for handler in handlers:
                        try:
                            handler(msg)
                        except Exception:
                            pass
        except Exception as error:
            self._reader_error = error
        finally:
            self._closed = True
            with self._pending_lock:
                for event, box in self._pending.values():
                    box.append({"error": {"message": str(self._reader_error or "CDP connection closed.")}})
                    event.set()
                self._pending.clear()

    def on(self, method: str, callback) -> None:
        with self._pending_lock:
            self._event_handlers.setdefault(method, []).append(callback)

    def off(self, method: str, callback) -> None:
        with self._pending_lock:
            items = self._event_handlers.get(method)
            if items and callback in items:
                items.remove(callback)

    def send_no_wait(self, method: str, params: dict | None = None, *, session_id: str | None = None) -> None:
        """Send a CDP command without waiting for a reply (safe to call from event handlers)."""
        if self._closed:
            return
        with self._pending_lock:
            self._next_id += 1
            call_id = self._next_id
        payload: dict = {"id": call_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id is not None:
            payload["sessionId"] = session_id
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            with self._send_lock:
                _ws_write_frame(self.sock, 0x1, encoded, mask=True)
        except OSError:
            pass

    def call(self, method: str, params: dict | None = None, *, session_id: str | None = None, timeout: float = 120.0) -> dict:
        if self._closed:
            raise BrowserError("Headless Chrome connection is closed.")
        event = threading.Event()
        box: list[dict] = []
        with self._pending_lock:
            self._next_id += 1
            call_id = self._next_id
            self._pending[call_id] = (event, box)
        payload: dict = {"id": call_id, "method": method}
        if params is not None:
            payload["params"] = params
        if session_id is not None:
            payload["sessionId"] = session_id
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            with self._send_lock:
                _ws_write_frame(self.sock, 0x1, encoded, mask=True)
        except OSError as error:
            with self._pending_lock:
                self._pending.pop(call_id, None)
            raise BrowserError(f"Failed sending CDP command {method}: {error}") from error
        if not event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(call_id, None)
            raise BrowserError(f"Headless Chrome timed out during {method}.")
        reply = box[0]
        if "error" in reply:
            raise BrowserError(f"Chrome CDP {method} failed: {reply['error'].get('message', reply['error'])}")
        return reply.get("result", {})

    def close(self):
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ── Loopback Single-Film Asset Server ─────────────────────────────────────────────────────────

class _FilmAssetServer:
    """Serve only the compiled film HTML and its verified SHA-256 asset PNGs on 127.0.0.1."""

    def __init__(self, html_bytes: bytes, assets: dict[str, bytes]):
        self.html_bytes = html_bytes
        self.assets = assets
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                target = self.path.split("?", 1)[0]
                if target == "/film.html":
                    body, mime = parent.html_bytes, "text/html; charset=utf-8"
                elif target.startswith("/assets/"):
                    name = target[len("/assets/"):]
                    body = parent.assets.get(name)
                    if body is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    mime = "image/png"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        class _QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                return

        self.server = _QuietServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, name="bacchus-film-http", daemon=True)
        self.thread.start()

    @property
    def film_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/film.html"

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except OSError:
            pass


# ── Loopback WebSocket Binary Frame Receiver (for Multi-Worker Render) ────────────────────────

class _FrameSocketServer:
    """Receive [uint32_be frame_index][png_bytes] binary messages from Chrome workers."""

    def __init__(self, on_frame, on_error):
        self.on_frame = on_frame
        self.on_error = on_error
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self._sock.settimeout(0.25)
        self.port = self._sock.getsockname()[1]
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()
        self._accept_thread = threading.Thread(target=self._accept_loop, name="bacchus-ws-accept", daemon=True)
        self._accept_thread.start()

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._clients_lock:
                self._clients.append(conn)
            threading.Thread(target=self._handle_client, args=(conn,), name="bacchus-ws-worker", daemon=True).start()

    def _handle_client(self, conn: socket.socket):
        try:
            conn.settimeout(60.0)
            req = bytearray()
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                req.extend(chunk)
            header_text = bytes(req).partition(b"\r\n\r\n")[0].decode("latin1")
            ws_key = None
            for line in header_text.splitlines()[1:]:
                if line.lower().startswith("sec-websocket-key:"):
                    ws_key = line.split(":", 1)[1].strip()
                    break
            if not ws_key:
                return
            accept = base64.b64encode(hashlib.sha1((ws_key + WS_GUID).encode("ascii")).digest()).decode("ascii")
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii"))
            while not self._stop.is_set():
                opcode, payload = _ws_read_frame(conn)
                if opcode == 0x8:
                    break
                if opcode == 0x2 and len(payload) > 4:
                    frame_index = struct.unpack("!I", payload[:4])[0]
                    self.on_frame(frame_index, payload[4:])
                    _ws_write_frame(conn, 0x1, b"ok", mask=False)
        except (EOFError, OSError):
            pass
        except Exception as error:
            if not self._stop.is_set():
                self.on_error(error)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        with self._clients_lock:
            for conn in self._clients:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    conn.close()
                except OSError:
                    pass
            self._clients.clear()


# ── Headless Chrome Session Manager ───────────────────────────────────────────────────────────

class CDPBrowser:
    """Managed muted headless Chrome/Edge process with isolated tabs and offline egress guard."""

    def __init__(self, executable: Path | None = None):
        self.executable = executable or find_browser()
        if not self.executable or not self.executable.is_file():
            raise BrowserError(
                "No Chrome or Edge browser was found for HTML5 Canvas rendering. "
                "Install Google Chrome or Microsoft Edge, or set CHROME."
            )
        self._profile_dir = Path(tempfile.mkdtemp(prefix="bacchus-chrome-", dir=scratch_directory()))
        self._process: subprocess.Popen | None = None
        self.client: _CDPClient | None = None
        self._tab_handlers: dict[str, tuple] = {}
        self._start()

    def _start(self):
        args = [
            str(self.executable),
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={self._profile_dir}",
            "--disk-cache-size=1",
            "--media-cache-size=1",
            "--disable-accelerated-2d-canvas",
            "--mute-audio",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-sync",
            "--disable-default-apps",
            "--disable-popup-blocking",
            "--disable-translate",
            "--metrics-recording-only",
            "about:blank",
        ]
        try:
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **media_tools.process_options(),
            )
        except OSError as error:
            self.close()
            raise BrowserError(f"Could not start headless browser: {error}") from error

        port_file = self._profile_dir / "DevToolsActivePort"
        port = None
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self.close()
                raise BrowserError("Headless browser exited during startup.")
            if port_file.is_file():
                try:
                    lines = port_file.read_text(encoding="utf-8").splitlines()
                    if lines and lines[0].strip().isdigit():
                        port = int(lines[0].strip())
                        break
                except OSError:
                    pass
            time.sleep(0.05)
        if not port:
            self.close()
            raise BrowserError("Headless browser did not publish a DevTools port.")

        version_url = f"http://127.0.0.1:{port}/json/version"
        ws_url = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(version_url, timeout=3) as response:
                    info = json.loads(response.read().decode("utf-8"))
                    ws_url = info.get("webSocketDebuggerUrl")
                    if ws_url:
                        break
            except Exception:
                time.sleep(0.05)
        if not ws_url:
            self.close()
            raise BrowserError("Headless browser did not expose a DevTools WebSocket endpoint.")
        self.client = _CDPClient(ws_url)

    def is_alive(self) -> bool:
        return (
            self._process is not None
            and self._process.poll() is None
            and self.client is not None
            and not self.client._closed
        )

    def open_film_tab(self, film_url: str, scale: float, *, init_extra: dict | None = None,
                      allowed_ports: set[int] | None = None) -> tuple[str, str, list[str], dict]:
        """Open a fresh isolated tab, block non-loopback requests, and await window.__film.ready."""
        if not self.client:
            raise BrowserError("Headless browser is not connected.")
        created = self.client.call("Target.createTarget", {"url": "about:blank"})
        target_id = created["targetId"]
        attached = self.client.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attached["sessionId"]
        page_errors: list[str] = []

        def on_exception(msg):
            if msg.get("sessionId") != session_id:
                return
            details = msg.get("params", {}).get("exceptionDetails", {})
            text = details.get("exception", {}).get("description") or details.get("text") or "JavaScript page error"
            page_errors.append(str(text))

        def on_console(msg):
            if msg.get("sessionId") != session_id:
                return
            params = msg.get("params", {})
            if params.get("type") == "error":
                text = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", ()))
                if text and "ERR_FILE" not in text and "willReadFrequently" not in text:
                    page_errors.append(text)

        def on_paused(msg):
            if msg.get("sessionId") != session_id:
                return
            params = msg.get("params", {})
            req_id = params.get("requestId")
            url = params.get("request", {}).get("url", "")
            allow = url.startswith("data:") or url == "about:blank"
            if not allow and url.startswith(("http://127.0.0.1:", "ws://127.0.0.1:")):
                try:
                    req_port = int(url.split(":", 2)[2].split("/", 1)[0])
                    allow = not allowed_ports or req_port in allowed_ports
                except (IndexError, ValueError):
                    allow = False
            if allow:
                self.client.send_no_wait("Fetch.continueRequest", {"requestId": req_id}, session_id=session_id)
            else:
                page_errors.append(f"Blocked external request: {url}")
                self.client.send_no_wait("Fetch.failRequest", {"requestId": req_id, "errorReason": "BlockedByClient"},
                                         session_id=session_id)

        self._tab_handlers[target_id] = (on_exception, on_console, on_paused)
        self.client.on("Runtime.exceptionThrown", on_exception)
        self.client.on("Runtime.consoleAPICalled", on_console)
        self.client.on("Fetch.requestPaused", on_paused)

        try:
            self.client.call("Page.enable", session_id=session_id)
            self.client.call("Runtime.enable", session_id=session_id)
            self.client.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, session_id=session_id)
            init_js = f"window.__FILM_SCALE = {json.dumps(float(scale))};"
            if init_extra:
                init_js += f"Object.assign(window, {json.dumps(init_extra)});"
            self.client.call("Page.addScriptToEvaluateOnNewDocument", {"source": init_js}, session_id=session_id)
            self.client.call("Page.navigate", {"url": f"{film_url}#render"}, session_id=session_id)
            ready = self.eval_js(
                session_id,
                """(async () => {
                  for (let i = 0; i < 600; i++) {
                    if (window.__film_error) throw new Error(window.__film_error);
                    if (window.__film) break;
                    await new Promise(r => setTimeout(r, 20));
                  }
                  if (!window.__film) throw new Error('Film runtime did not initialize.');
                  await window.__film.ready;
                  return {
                    w: window.__film.RW,
                    h: window.__film.RH,
                    dur: window.__film.DUR,
                    scenes: window.__film.scenes(),
                  };
                })()""",
                timeout=60.0,
            )
            if page_errors:
                raise BrowserError("Film page failed: " + "; ".join(page_errors[:5]))
            return target_id, session_id, page_errors, ready
        except Exception:
            self.close_tab(target_id)
            raise

    def eval_js(self, session_id: str, expression: str, *, timeout: float = 120.0):
        if not self.client:
            raise BrowserError("Headless browser is not connected.")
        result = self.client.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
            session_id=session_id,
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            msg = details.get("exception", {}).get("description") or details.get("text") or "JavaScript evaluation error"
            raise BrowserError(str(msg))
        return result.get("result", {}).get("value")

    def close_tab(self, target_id: str) -> None:
        handlers = self._tab_handlers.pop(target_id, ())
        if self.client and not self.client._closed:
            if len(handlers) == 3:
                self.client.off("Runtime.exceptionThrown", handlers[0])
                self.client.off("Runtime.consoleAPICalled", handlers[1])
                self.client.off("Fetch.requestPaused", handlers[2])
            try:
                self.client.call("Target.closeTarget", {"targetId": target_id}, timeout=5.0)
            except Exception:
                pass

    def close(self):
        if self.client:
            self.client.close()
            self.client = None
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass
            self._process = None
        for _ in range(5):
            if not self._profile_dir.exists():
                break
            try:
                writable_path(self._profile_dir)
            except StoragePathError:
                break
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            if self._profile_dir.exists():
                time.sleep(0.05)


# ── Shared Warm Browser Pool (fast interactive previews & single-session QC) ──────────────────

class _WarmPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._browser: CDPBrowser | None = None

    def acquire(self) -> CDPBrowser:
        if self._browser is None or not self._browser.is_alive():
            if self._browser is not None:
                self._browser.close()
            self._browser = CDPBrowser()
        return self._browser

    def run(self, html_bytes: bytes, assets: dict[str, bytes], scale: float, callback, *, init_extra: dict | None = None):
        with self._lock:
            browser = self.acquire()
            asset_server = _FilmAssetServer(html_bytes, assets)
            target_id = None
            try:
                target_id, session_id, page_errors, ready = browser.open_film_tab(
                    asset_server.film_url, scale, init_extra=init_extra, allowed_ports={asset_server.port},
                )
                return callback(browser, session_id, ready, page_errors)
            except BrowserError:
                if not browser.is_alive():
                    browser.close()
                    self._browser = None
                raise
            finally:
                if target_id:
                    browser.close_tab(target_id)
                asset_server.close()

    def shutdown(self):
        with self._lock:
            if self._browser is not None:
                self._browser.close()
                self._browser = None


_POOL = _WarmPool()
atexit.register(_POOL.shutdown)


def shutdown_browser_pool() -> None:
    _POOL.shutdown()


def run_with_film(html_bytes: bytes, assets: dict[str, bytes], scale: float, callback, *, init_extra: dict | None = None):
    """Open compiled film HTML in the warm Chrome pool, run callback(browser, session_id, ready, errors)."""
    return _POOL.run(html_bytes, assets, scale, callback, init_extra=init_extra)


def render_still_png(html_bytes: bytes, assets: dict[str, bytes], time_s: float, *, scale: float = 2.0) -> bytes:
    """Render one deterministic PNG frame at the requested scale (scale=2 -> 3840x2160)."""
    def _capture(browser, session_id, _ready, page_errors):
        b64 = browser.eval_js(
            session_id,
            f"(() => {{ window.__film.render({json.dumps(float(time_s))}); return window.__film.frameB64(); }})()",
        )
        if page_errors:
            raise BrowserError("; ".join(page_errors[:5]))
        return base64.b64decode(b64)
    return run_with_film(html_bytes, assets, scale, _capture)


def render_static_scene_png(html_bytes: bytes, assets: dict[str, bytes], scene_index: int,
                            layer_id: str = "", layer_phase: float | None = None, *, scale: float = 1.0) -> bytes:
    """Render a static scene composition (or isolated layer entrance phase) for asset-use proofs."""
    def _capture(browser, session_id, _ready, page_errors):
        expr = (
            f"(() => {{ window.__film.renderSceneStatic("
            f"{json.dumps(int(scene_index))}, {json.dumps(layer_id)}, {json.dumps(layer_phase)}); "
            "return window.__film.frameB64(); })()"
        )
        b64 = browser.eval_js(session_id, expr)
        if page_errors:
            raise BrowserError("; ".join(page_errors[:5]))
        return base64.b64decode(b64)
    return run_with_film(html_bytes, assets, scale, _capture)


# ── Multi-Worker Parallel Chrome -> PNG WebSocket -> FFmpeg Pipeline ──────────────────────────

def render_frames_to_ffmpeg(
    html_bytes: bytes,
    assets: dict[str, bytes],
    *,
    scale: float,
    fps: int,
    first_frame: int,
    frame_count: int,
    ffmpeg_command: list[str],
    stderr_file,
    workers: int = DEFAULT_WORKERS,
    timeout_seconds: float | None = None,
) -> int:
    """Render frames across N parallel Chrome pages, streaming ordered PNGs into FFmpeg stdin."""
    if frame_count < 1:
        raise BrowserError("Frame count must be positive.")
    worker_count = max(1, min(int(workers), MAX_WORKERS, frame_count))
    asset_server = _FilmAssetServer(html_bytes, assets)
    browser = CDPBrowser()
    held: dict[int, bytes] = {}
    cond = threading.Condition()
    next_to_write = 0
    fatal_error: list[Exception] = []
    aborted = threading.Event()
    ahead = worker_count * 3

    def record_error(error: Exception):
        with cond:
            if not fatal_error:
                fatal_error.append(error)
            aborted.set()
            cond.notify_all()

    def on_frame(k: int, png_bytes: bytes):
        with cond:
            held[k] = png_bytes
            cond.notify_all()

    ws_server = _FrameSocketServer(on_frame, record_error)
    process: subprocess.Popen | None = None
    watchdog: threading.Timer | None = None
    tabs: list[tuple[str, str]] = []

    try:
        # Open all worker tabs and establish WebSocket frame senders before starting FFmpeg
        for _ in range(worker_count):
            target_id, session_id, _errs, _ready = browser.open_film_tab(
                asset_server.film_url, scale, allowed_ports={asset_server.port, ws_server.port},
            )
            tabs.append((target_id, session_id))
            browser.eval_js(
                session_id,
                f"""(async () => {{
                  const ws = new WebSocket({json.dumps(ws_server.ws_url)});
                  ws.binaryType = 'arraybuffer';
                  await new Promise((ok, fail) => {{
                    ws.onopen = ok;
                    ws.onerror = () => fail(new Error('Frame WebSocket failed to connect.'));
                    ws.onclose = () => fail(new Error('Frame WebSocket closed before connecting.'));
                  }});
                  let ack = null, sockErr = null;
                  const onDrop = () => {{
                    sockErr ||= new Error('Frame WebSocket closed before acknowledgement.');
                    if (ack) {{ const p = ack; ack = null; p.fail(sockErr); }}
                  }};
                  ws.onerror = onDrop;
                  ws.onclose = onDrop;
                  ws.onmessage = () => {{ if (ack) {{ const p = ack; ack = null; p.ok(); }} }};
                  window.__send = async (k, t) => {{
                    window.__film.render(t);
                    const pngBuf = await window.__film.framePNG();
                    if (sockErr || ws.readyState !== WebSocket.OPEN) throw sockErr || new Error('Frame socket not open.');
                    const packet = new Uint8Array(4 + pngBuf.byteLength);
                    new DataView(packet.buffer).setUint32(0, k, false);
                    packet.set(new Uint8Array(pngBuf), 4);
                    await new Promise((ok, fail) => {{
                      ack = {{ ok, fail }};
                      try {{ ws.send(packet.buffer); }} catch (e) {{ ack = null; fail(e); }}
                    }});
                  }};
                  return true;
                }})()""",
                timeout=30.0,
            )

        try:
            process = subprocess.Popen(
                ffmpeg_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                **media_tools.process_options(),
            )
        except OSError as error:
            raise BrowserError(f"FFmpeg could not start: {error}") from error

        total_timeout = timeout_seconds if timeout_seconds is not None else (frame_count / fps * 12 + 120)
        watchdog = threading.Timer(total_timeout, lambda: (record_error(BrowserError("Render timed out.")), process.kill()))
        watchdog.daemon = True
        watchdog.start()

        # Ordered writer thread piping PNG frames 0..frame_count-1 into FFmpeg stdin
        def writer_loop():
            nonlocal next_to_write
            try:
                assert process is not None and process.stdin is not None
                with process.stdin as pipe:
                    while next_to_write < frame_count:
                        with cond:
                            while next_to_write not in held and not aborted.is_set():
                                cond.wait(timeout=0.5)
                            if aborted.is_set():
                                return
                            png_bytes = held.pop(next_to_write)
                            next_to_write += 1
                            cond.notify_all()
                        pipe.write(png_bytes)
            except BrokenPipeError:
                aborted.set()
                with cond:
                    cond.notify_all()
            except Exception as error:
                record_error(error)

        writer = threading.Thread(target=writer_loop, name="bacchus-ffmpeg-writer", daemon=True)
        writer.start()

        def worker_loop(worker_idx: int, session_id: str):
            try:
                for k in range(worker_idx, frame_count, worker_count):
                    with cond:
                        while k >= next_to_write + ahead and not aborted.is_set():
                            cond.wait(timeout=0.5)
                        if aborted.is_set():
                            return
                    t = (first_frame + k) / fps
                    browser.eval_js(session_id, f"window.__send({k}, {t!r})", timeout=60.0)
            except Exception as error:
                record_error(error)

        worker_threads = [
            threading.Thread(target=worker_loop, args=(idx, sid), name=f"bacchus-chrome-w{idx}", daemon=True)
            for idx, (_tid, sid) in enumerate(tabs)
        ]
        for thread in worker_threads:
            thread.start()
        for thread in worker_threads:
            thread.join()
        writer.join()
        return_code = process.wait()
        if fatal_error:
            raise BrowserError(str(fatal_error[0]))
        return return_code
    finally:
        aborted.set()
        if watchdog is not None:
            watchdog.cancel()
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        ws_server.close()
        browser.close()
        asset_server.close()
