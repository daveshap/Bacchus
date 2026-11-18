import argparse
import json
import math
import re
import sys
import threading
import traceback
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import audio, media_tools, planning, providers, render
from .library import LibraryError
from .runtime import APP_VERSION, default_data_directory, open_running
from .settings import Settings, SettingsError
from .workflow import (
    MAX_ART_BYTES, MAX_ART_GENERATIONS, MAX_AUDIO_BYTES, MAX_DURATION, MAX_EFFECTS,
    MAX_FX_BYTES, MAX_FX_DURATION, MAX_TRANSCRIPTIONS, PACK_PIN, STYLES, Studio, WorkflowError, fields, write_json,
)


ROOT = Path(__file__).resolve().parent.parent
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
JSON_LIMIT = 512 * 1024
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


class Handler(BaseHTTPRequestHandler):
    server_version = f"Bacchus/{APP_VERSION}"
    sys_version = ""

    def do_GET(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    def log_request(self, code="-", size="-"):
        if str(code).startswith("5"):
            super().log_request(code, size)

    def _dispatch(self, mutation):
        try:
            self._check_request(mutation)
            target = urlsplit(self.path)
            if target.scheme or target.netloc:
                raise WorkflowError("Invalid request target.")
            if mutation:
                self._post(target.path, parse_qs(target.query))
            else:
                self._get(target.path, parse_qs(target.query))
        except (WorkflowError, LibraryError, SettingsError) as error:
            self._json({"error": str(error)}, error.status)
        except render.RenderError as error:
            self._json({"error": str(error)}, 422)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.log_error("Request failed unexpectedly.")
            self._json({"error": "Bacchus could not complete this request."}, 500)

    def _check_request(self, mutation):
        expected_host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected_host:
            raise WorkflowError(f"Open Bacchus at http://{expected_host}.", 403)
        if mutation:
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{expected_host}":
                raise WorkflowError("Changes must come from the local Bacchus app.", 403)
            if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                raise WorkflowError("Changes must come from the local Bacchus app.", 403)

    def _get(self, path, query):
        studio = self.server.studio
        if path in STATIC_FILES:
            filename, mime = STATIC_FILES[path]
            self._bytes((ROOT / "static" / filename).read_bytes(), mime)
            return
        if path == "/api/config":
            self._json({
                "plannerAvailable": planning.planner_available(), "renderAvailable": render.render_available(),
                "layouts": planning.LAYOUTS, "motions": planning.MOTIONS, "pack": PACK_PIN,
                "maxLines": planning.MAX_LINES, "maxScenes": planning.MAX_SCENES, "maxDuration": MAX_DURATION,
                "maxArtBytes": MAX_ART_BYTES, "maxAudioBytes": MAX_AUDIO_BYTES,
                "maxPlannerCalls": planning.MAX_PLANNER_CALLS,
                "audioAvailable": audio.audio_available(), "transcriberAvailable": audio.transcriber_available(),
                "imageAvailable": providers.image_available(), "plannerProvider": planning.planner_provider(),
                "styles": [{"id": style, "name": label} for style, label in STYLES.items()],
                "maxEffects": MAX_EFFECTS, "maxFxDuration": MAX_FX_DURATION,
                "maxArtGenerations": MAX_ART_GENERATIONS, "maxTranscriptionCalls": MAX_TRANSCRIPTIONS,
                "desktopMode": self.server.desktop, "version": APP_VERSION,
            })
            return
        if path == "/api/instance":
            self._json({"instance": self.server.instance_id})
            return
        if path == "/api/projects":
            self._json(studio.list_projects())
            return
        if path == "/api/settings":
            self._json(self._settings_view(self.server.settings.get()))
            return
        if path == "/api/library":
            assets = studio.library.list_assets()
            for entry in assets:
                for version in entry["versions"]:
                    version["url"] = f"/api/library/media/{version['hash']}"
            self._json({"assets": assets})
            return
        if path.startswith("/api/library/media/"):
            image = studio.library.blob(path.rsplit("/", 1)[-1])
            self._file(image, "image/png", "art")
            return
        project_id, tail = self._project_route(path)
        if not tail:
            self._json(studio.get(project_id))
        elif tail == ["preview"]:
            try:
                time = float(query.get("time", ["0"])[0])
                if not math.isfinite(time) or time < 0 or time > MAX_DURATION:
                    raise ValueError("Out of range")
            except ValueError as error:
                raise WorkflowError("Preview time must be a valid number of seconds.") from error
            self._bytes(studio.preview(project_id, time), "image/png")
        elif tail == ["mix", "preview"]:
            self._file(studio.mix_preview(project_id), "audio/wav", "audio")
        elif len(tail) == 3 and tail[0] == "media":
            file, mime = studio.media(project_id, tail[1], tail[2])
            self._file(file, mime, tail[1])
        else:
            raise WorkflowError("Page not found.", 404)

    def _post(self, path, query):
        studio = self.server.studio
        if self.server.closing:
            raise WorkflowError("Bacchus is closing.", 409)
        if path == "/api/quit":
            fields(self._body(), ())
            self._quit()
            return
        if path == "/api/media-tools/install":
            fields(self._body(), ())
            if not self.server.desktop:
                raise WorkflowError("Automatic media setup is available in the desktop release.", 409)
            try:
                media_tools.install()
            except media_tools.MediaToolsError as error:
                raise WorkflowError(str(error), 422) from error
            self._json(self._settings_view(self.server.settings.get()))
            return
        if path == "/api/settings":
            data = self._body()
            with studio.lock:
                if studio.providers_in_use():
                    raise WorkflowError("Wait for the active AI request before changing provider settings.", 409)
                self._json(self._settings_view(self.server.settings.save(data)))
            return
        if path == "/api/projects":
            data = self._body()
            fields(data, ("title",))
            self._json(studio.create(data["title"]), 201)
            return
        if path == "/api/demo":
            fields(self._body(), ())
            self._json(studio.create("The City After Dark", demo=True), 201)
            return
        project_id, tail = self._project_route(path)
        if tail == ["library", "pin"]:
            self._json(studio.pin_library(project_id, self._body()))
        elif tail == ["audio"]:
            self._json(studio.upload_audio(
                project_id, self._revision(query), query.get("name", ["recording.wav"])[0],
                self._raw(MAX_AUDIO_BYTES),
            ))
        elif tail == ["timings"]:
            self._json(studio.import_timings(project_id, self._revision(query), self._raw(4 * 1024 * 1024)))
        elif tail == ["audio", "remove"]:
            self._json(studio.remove_audio(project_id, self._body()))
        elif tail in (["mix", "music"], ["mix", "effects"]):
            limit = MAX_AUDIO_BYTES if tail[1] == "music" else MAX_FX_BYTES
            self._json(studio.upload_mix(
                project_id, self._revision(query), tail[1], query.get("name", ["track.wav"])[0], self._raw(limit),
            ))
        elif len(tail) == 4 and tail[:2] == ["mix", "effects"] and tail[3] == "remove":
            self._json(studio.remove_effect(project_id, tail[2], self._body()))
        elif len(tail) == 2 and tail[0] == "assets":
            self._json(studio.upload_art(
                project_id, self._revision(query), tail[1], query.get("name", ["artwork.png"])[0],
                self._raw(MAX_ART_BYTES),
            ))
        elif len(tail) == 3 and tail[0] == "assets" and tail[2] == "approve":
            self._json(studio.approve_art(project_id, tail[1], self._body()))
        elif len(tail) == 3 and tail[0] == "assets" and tail[2] == "generate":
            self._json(studio.generate_art(project_id, tail[1], self._body()))
        else:
            self._json(studio.change(project_id, "/".join(tail), self._body()))

    def _settings_view(self, settings):
        return {
            **settings,
            "dataPath": str(self.server.studio.root),
            "libraryPath": str(self.server.studio.library.root),
            "audioAvailable": audio.audio_available(),
            "renderAvailable": render.render_available(),
            "mediaInstallAvailable": self.server.desktop and media_tools.can_install() and not render.render_available(),
        }

    def _quit(self):
        if not self.server.desktop:
            raise WorkflowError("Close Bacchus from the terminal that started it.", 409)
        studio = self.server.studio
        with studio.lock:
            if studio.jobs or studio.providers_in_use() or media_tools.INSTALL_LOCK.locked():
                raise WorkflowError("Wait for the current operation to finish before quitting.", 409)
            self.server.closing = True
        try:
            self._json({"quitting": True})
        finally:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _project_route(self, path):
        segments = path.split("/")
        if segments[:3] != ["", "api", "projects"] or len(segments) < 4:
            raise WorkflowError("Page not found.", 404)
        return segments[3], segments[4:]

    def _revision(self, query):
        try:
            return int(query["revision"][0])
        except (KeyError, ValueError) as error:
            raise WorkflowError("The project revision is required.") from error

    def _raw(self, limit):
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as error:
            raise WorkflowError("Invalid content length.") from error
        if length < 1 or length > limit:
            raise WorkflowError(f"Request size must be between 1 and {limit:,} bytes.", 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise WorkflowError("Request data was incomplete.")
        return raw

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise WorkflowError("Expected a JSON request.", 415)
        try:
            return json.loads(self._raw(JSON_LIMIT))
        except (ValueError, RecursionError, UnicodeError) as error:
            raise WorkflowError("The request body must be valid JSON.") from error

    def _json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._bytes(data, "application/json; charset=utf-8", status)

    def _headers(self, status, mime, length, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _bytes(self, data, mime, status=200):
        self._headers(status, mime, len(data))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, file, mime, kind):
        with file.open("rb") as source:
            size = file.stat().st_size
            first, last = 0, size - 1
            requested = self.headers.get("Range") if kind in ("audio", "video") else None
            if requested:
                match = re.fullmatch(r"bytes=(\d{0,16})-(\d{0,16})", requested)
                if match and any(match.groups()):
                    begin, end = match.groups()
                    if begin:
                        first, last = int(begin), min(int(end), last) if end else last
                    else:
                        first = max(0, size - int(end))
                if not match or not any(match.groups()) or not 0 <= first <= last < size:
                    self._headers(416, mime, 0)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            remaining = last - first + 1
            self._headers(206 if requested else 200, mime, remaining, cache="private, max-age=3600")
            if kind in ("audio", "video"):
                self.send_header("Accept-Ranges", "bytes")
            if requested:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            if kind in ("captions", "receipt"):
                self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
            self.end_headers()
            source.seek(first)
            while remaining:
                chunk = source.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


@contextmanager
def data_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".instance.lock").open("a+b") as file:
        file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise AlreadyRunning(f"Another Bacchus process already uses {root}.") from error
        yield


class AlreadyRunning(Exception):
    pass


@contextmanager
def desktop_log(data, enabled):
    if not enabled:
        yield
        return
    data.mkdir(parents=True, exist_ok=True)
    previous = sys.stdout, sys.stderr
    path = data / "bacchus.log"
    mode = "w" if path.is_file() and path.stat().st_size >= 1024 * 1024 else "a"
    with path.open(mode, encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
        try:
            yield
        except Exception:
            traceback.print_exc(file=log)
            raise
        finally:
            sys.stdout, sys.stderr = previous


def _serve(options, data, desktop):
    with data_lock(data), desktop_log(data, desktop):
        media_tools.configure(data)
        with ThreadingHTTPServer(("127.0.0.1", options.port), Handler) as server:
            library = options.library.expanduser() if options.library is not None else None
            server.studio = Studio(data, library)
            server.settings = Settings(data)
            server.desktop = desktop
            server.closing = False
            server.instance_id = uuid.uuid4().hex
            url = f"http://127.0.0.1:{server.server_port}"
            runtime = data / "runtime.json"
            write_json(runtime, {"url": url, "instance": server.instance_id})
            print(f"Bacchus {APP_VERSION} is running at {url}", flush=True)
            if not options.no_browser:
                browser = threading.Timer(0.3, webbrowser.open, args=(url,))
                browser.daemon = True
                browser.start()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nClosing Bacchus.", flush=True)
            finally:
                runtime.unlink(missing_ok=True)
            for job in tuple(server.studio.jobs):
                job.join()


def main(desktop=False):
    parser = argparse.ArgumentParser(description="Run the local Bacchus animation studio.")
    parser.add_argument("--port", type=int, default=0 if desktop else 8755)
    parser.add_argument("--data", type=Path, default=default_data_directory(),
                        help="Choose where Bacchus stores videos, settings, and its default library.")
    parser.add_argument("--library", type=Path, help="Set a separate directory for shared versioned artwork.")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    options = parser.parse_args()
    data = options.data.expanduser().resolve()
    try:
        _serve(options, data, desktop)
    except AlreadyRunning as error:
        if desktop and not options.no_browser and open_running(data):
            return
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
