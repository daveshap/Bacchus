"""Loopback HTTP interface for the desktop workspace."""

from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import RESOURCES, audio, layer_spec, local_timing, media_tools, planning, providers, render, score, style_archive, styles, timing_models, visual_review
from .library import LibraryError
from .runtime import APP_VERSION
from .settings import SettingsError, directory_preference, provider_secrets, timing_model_directory
from .workflow import (
    MAX_ART_BYTES, MAX_ART_GENERATIONS, MAX_AUDIO_BYTES, MAX_DURATION, MAX_EFFECTS,
    MAX_FX_BYTES, MAX_FX_DURATION, MAX_TRANSCRIPTIONS, PACK_PIN, STYLES, WorkflowError, fields,
)


STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
JSON_LIMIT = 4 * 1024 * 1024
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
PREVIEW_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "font-src data:; img-src 'self' blob: data:; media-src 'self' blob:; "
    "frame-ancestors 'self'; base-uri 'none'"
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
        except (WorkflowError, LibraryError, SettingsError, styles.StyleError, timing_models.TimingModelError,
                style_archive.ArchiveError) as error:
            self._json({"error": str(error)}, error.status)
        except render.RenderError as error:
            self._json({"error": str(error)}, 422)
        except ConnectionError:
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
            self._bytes((RESOURCES / "ui" / filename).read_bytes(), mime)
            return
        if path == "/api/config":
            self._json({
                "plannerAvailable": planning.planner_available(), "renderAvailable": render.render_available(),
                "renderBackend": render.render_backend(), "browserAvailable": render.browser_available(),
                "browserLabel": render.browser_label(),
                "layouts": planning.LAYOUTS, "motions": planning.MOTIONS, "pack": PACK_PIN,
                "layerKinds": list(layer_spec.KINDS), "layerEffects": list(layer_spec.EFFECTS),
                "bfgSubjects": layer_spec.BFG_SUBJECTS,
                "maxLayers": layer_spec.MAX_LAYERS,
                "maxLines": planning.MAX_LINES, "maxScenes": planning.MAX_SCENES, "maxDuration": MAX_DURATION,
                "maxArtBytes": MAX_ART_BYTES, "maxAudioBytes": MAX_AUDIO_BYTES,
                "maxPlannerCalls": planning.MAX_PLANNER_CALLS,
                "audioAvailable": audio.audio_available(), "transcriberAvailable": audio.transcriber_available(),
                "imageAvailable": providers.image_available(), "plannerProvider": planning.planner_provider(),
                "visualReviewerAvailable": visual_review.available(),
                "styleDraftAvailable": studio.styles.available(),
                "maxStyleReferenceBytes": styles.MAX_REFERENCE_BYTES,
                "maxStyleReferenceImages": styles.MAX_REFERENCE_IMAGES,
                "localTimingAvailable": self._timing_status()["ready"],
                "styles": [{"id": style, "name": label} for style, label in STYLES.items()],
                "musicStyles": [{"id": style, "name": label} for style, label in score.ARRANGEMENT_STYLES.items()],
                "maxEffects": MAX_EFFECTS, "maxFxDuration": MAX_FX_DURATION,
                "maxArtGenerations": MAX_ART_GENERATIONS, "maxTranscriptionCalls": MAX_TRANSCRIPTIONS,
                "version": APP_VERSION,
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
        if path == "/api/timing-model":
            self._json(self._timing_status())
            return
        if path == "/api/styles":
            self._json({"styles": [studio.style_view(style) for style in studio.styles.list_styles()]})
            return
        if re.fullmatch(r"/api/styles/[^/]+/archive", path):
            style_id = path.split("/")[3]
            with self._archive_transfer() as work:
                style = studio.styles.get(style_id)
                name = re.sub(r"[^a-z0-9]+", "-", style["name"].lower()).strip("-")[:72].rstrip("-")
                archive = work / f"{name or 'bacchus-style'}-library.zip"
                style_archive.export_style(studio, style_id, archive)
                self._file(archive, "application/zip", "archive")
            return
        if re.fullmatch(r"/api/styles/[^/]+", path):
            style_id = path.rsplit("/", 1)[-1]
            self._json(studio.style_view(studio.styles.get(style_id)))
            return
        if re.fullmatch(r"/api/style-references/[^/]+", path):
            image = studio.styles.reference(path.rsplit("/", 1)[-1])
            self._file(image, "image/png", "art")
            return
        if path == "/api/library":
            style_id = query.get("styleId", ["warm-editorial"])[0]
            style = studio.styles.get(style_id)
            catalog = studio.library_catalog(style_id)
            for entry in catalog["assets"]:
                for version in entry["versions"]:
                    version["url"] = f"/api/library/media/{version['hash']}?styleId={style_id}"
            self._json({**catalog, "styleId": style_id, "libraryName": style["libraryName"]})
            return
        if path.startswith("/api/library/media/"):
            style_id = query.get("styleId", ["warm-editorial"])[0]
            studio.styles.get(style_id)
            image = studio.library_for(style_id).blob(path.rsplit("/", 1)[-1])
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
            width = None
            if "width" in query:
                try:
                    width = int(query["width"][0])
                    if not 320 <= width <= 3840:
                        raise ValueError("Out of range")
                except ValueError as error:
                    raise WorkflowError("Preview width must be between 320 and 3840 pixels.") from error
            self._bytes(studio.preview(project_id, time, width=width), "image/png")
        elif tail == ["film-preview"]:
            self._bytes(studio.film_preview_html(project_id), "text/html; charset=utf-8", csp=PREVIEW_CSP)
        elif tail == ["mix", "preview"]:
            self._file(studio.mix_preview(project_id), "audio/wav", "audio")
        elif len(tail) == 2 and tail[0] == "motion-preview":
            self._file(studio.motion_preview_file(project_id, tail[1]), "video/mp4", "video")
        elif len(tail) == 4 and tail[1] == "checks" and tail[3] == "proof":
            self._file(studio.review_proof(project_id, tail[0], tail[2]), "image/png", "art")
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
            try:
                media_tools.install()
            except media_tools.MediaToolsError as error:
                raise WorkflowError(str(error), 422) from error
            self._json(self._settings_view(self.server.settings.get()))
            return
        if path == "/api/settings":
            data = self._body()
            if isinstance(data, dict) and "timingModelDirectory" in data:
                raise SettingsError("Select and verify Parakeet folders in the timing model settings.")
            with studio.lock:
                if studio.providers_in_use():
                    raise WorkflowError("Wait for the active AI request before changing provider settings.", 409)
                self._json(self._settings_view(self.server.settings.save(data)))
            return
        if path == "/api/timing-model/install":
            fields(self._body(), ())
            if not local_timing.available():
                raise WorkflowError("The local Parakeet runtime is missing from this Bacchus installation.", 409)
            with studio.lock:
                if self.server.closing:
                    raise WorkflowError("Bacchus is closing.", 409)
                studio.timing_models.start_install()
            self._json(self._timing_status(), 202)
            return
        if path == "/api/timing-model/location":
            data = self._body()
            fields(data, ("directory",))
            directory = directory_preference("timingModelDirectory", data["directory"], provider_secrets())
            if directory:
                directory = str(studio.timing_models.validate_existing(directory))
            with studio.lock:
                if self.server.closing:
                    raise WorkflowError("Bacchus is closing.", 409)
                self.server.settings.save({"timingModelDirectory": directory})
            self._json(self._timing_status())
            return
        if path == "/api/library/quarantine":
            data = self._body()
            fields(data, ("styleId", "entryId"))
            studio.styles.get(data["styleId"])
            self._json(studio.library_for(data["styleId"]).quarantine(data["entryId"]))
            return
        if path == "/api/library/import-folder":
            data = self._body()
            fields(data, ("styleId", "directory"))
            self._json(studio.import_library_folder(data["styleId"], data["directory"]))
            return
        if path == "/api/library/import":
            style_id = query.get("styleId", ["warm-editorial"])[0]
            name = query.get("name", ["artwork.png"])[0]
            alias = query.get("alias", [""])[0]
            brief = query.get("brief", [""])[0]
            self._json(
                studio.import_library_file(style_id, name, self._raw(MAX_ART_BYTES), alias=alias, brief=brief),
                201,
            )
            return
        if path == "/api/style-references":
            reference = studio.styles.store_reference(query.get("name", ["reference.png"])[0],
                                                      self._raw(styles.MAX_REFERENCE_BYTES))
            self._json({**reference, "url": f"/api/style-references/{reference['sha256']}"}, 201)
            return
        if path == "/api/styles":
            self._json(studio.style_view(studio.styles.create(self._body())), 201)
            return
        if path == "/api/styles/import":
            if self.headers.get_content_type() not in ("application/zip", "application/x-zip-compressed"):
                raise WorkflowError("Choose a Bacchus style and library ZIP file.", 415)
            length = self._request_length(style_archive.MAX_ARCHIVE_BYTES)
            with self._archive_transfer() as work:
                archive = work / "import.zip"
                with archive.open("xb") as stream:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise WorkflowError("The ZIP upload did not finish.")
                        stream.write(chunk)
                        remaining -= len(chunk)
                imported = style_archive.import_style(studio, archive)
                self._json(studio.style_view(imported), 201)
            return
        if path == "/api/styles/draft":
            spec = self._body()
            with studio.lock:
                if self.server.closing:
                    raise WorkflowError("Bacchus is closing.", 409)
                studio.active_style_drafts += 1
            try:
                self._json(studio.styles.draft_guide(spec))
            finally:
                with studio.lock:
                    studio.active_style_drafts -= 1
            return
        if re.fullmatch(r"/api/styles/[^/]+", path):
            style_id = path.rsplit("/", 1)[-1]
            data = self._body()
            if not isinstance(data, dict) or "revision" not in data:
                raise WorkflowError("Supply the revision of the style you are editing.")
            revision = data.pop("revision")
            self._json(studio.style_view(studio.styles.update(style_id, revision, data)))
            return
        if path == "/api/projects":
            data = self._body()
            expected = ("title",)
            if isinstance(data, dict):
                if "styleId" in data or "styleRevision" in data:
                    expected += ("styleId", "styleRevision")
                expected += tuple(key for key in ("musicChoice", "musicStyle", "autoMode", "autoLimits", "autoBrief") if key in data)
            fields(data, expected)
            if "styleId" in data:
                if type(data["styleRevision"]) is not int or data["styleRevision"] < 1:
                    raise WorkflowError("Choose a saved style revision.")
            self._json(studio.create(
                data["title"], style_id=data.get("styleId", "warm-editorial"), style_revision=data.get("styleRevision"),
                music_choice=data.get("musicChoice", "style"), music_style=data.get("musicStyle"),
                auto_mode=data.get("autoMode", True), auto_limits=data.get("autoLimits"),
                auto_brief=data.get("autoBrief", ""),
            ), 201)
            return
        if path == "/api/demo":
            fields(self._body(), ())
            self._json(studio.create("The City After Dark", demo=True), 201)
            return
        project_id, tail = self._project_route(path)
        if tail == ["mode"]:
            self._json(studio.set_mode(project_id, self._body()))
        elif tail == ["auto", "run"]:
            self._json(studio.start_auto_pipeline(project_id, self._body()), 202)
        elif tail == ["auto", "settings"]:
            self._json(studio.set_auto_settings(project_id, self._body()))
        elif tail == ["library", "pin"]:
            self._json(studio.pin_library(project_id, self._body()))
        elif tail == ["motion-preview"]:
            self._json(studio.motion_preview(project_id, self._body()))
        elif tail == ["audio"]:
            self._json(studio.upload_audio(
                project_id, self._revision(query), query.get("name", ["recording.wav"])[0],
                self._raw(MAX_AUDIO_BYTES),
            ))
        elif tail == ["timings"]:
            self._json(studio.import_timings(project_id, self._revision(query), self._raw(4 * 1024 * 1024)))
        elif tail == ["timings", "derive"]:
            self._json(studio.derive_timings(project_id, self._body()))
        elif tail == ["timings", "text"]:
            self._json(studio.edit_timings(project_id, self._body(4 * 1024 * 1024)))
        elif tail == ["audio", "remove"]:
            self._json(studio.remove_audio(project_id, self._body()))
        elif tail in (["mix", "music"], ["mix", "effects"]):
            limit = MAX_AUDIO_BYTES if tail[1] == "music" else MAX_FX_BYTES
            self._json(studio.upload_mix(
                project_id, self._revision(query), tail[1], query.get("name", ["track.wav"])[0], self._raw(limit),
            ))
        elif len(tail) == 4 and tail[:2] == ["mix", "effects"] and tail[3] == "remove":
            self._json(studio.remove_effect(project_id, tail[2], self._body()))
        elif len(tail) == 2 and tail[0] == "assets" and (tail[1] != "checks" or "revision" in query):
            self._json(studio.upload_art(
                project_id, self._revision(query), tail[1], query.get("name", ["artwork.png"])[0],
                self._raw(MAX_ART_BYTES),
            ))
        elif len(tail) == 3 and tail[0] == "assets" and tail[2] == "approve":
            self._json(studio.approve_art(project_id, tail[1], self._body()))
        elif len(tail) == 3 and tail[0] == "assets" and tail[2] == "publish":
            self._json(studio.publish_art(project_id, tail[1], self._body()))
        elif len(tail) == 3 and tail[0] == "assets" and tail[2] == "issue":
            self._json(studio.report_art_issue(project_id, tail[1], self._body()))
        elif len(tail) == 3 and tail[0] == "assets" and tail[2] == "generate":
            self._json(studio.generate_art(project_id, tail[1], self._body()))
        elif len(tail) == 4 and tail[1] == "checks" and tail[3] == "result":
            self._json(studio.record_review(project_id, tail[0], tail[2], self._body()))
        elif len(tail) == 4 and tail[1] == "checks" and tail[3] == "advise":
            self._json(studio.advise_review(project_id, tail[0], tail[2], self._body()))
        else:
            self._json(studio.change(project_id, "/".join(tail), self._body()))

    def _settings_view(self, settings):
        render_available = render.render_available()
        return {
            **settings,
            "dataPath": str(self.server.studio.root),
            "libraryPath": str(self.server.studio.library_root),
            "libraryMigrationError": self.server.studio.library_migration_error,
            "audioAvailable": audio.audio_available(),
            "renderAvailable": render_available,
            "renderBackend": render.render_backend(),
            "browserAvailable": render.browser_available(),
            "browserLabel": render.browser_label(),
            "mediaInstallAvailable": media_tools.can_install() and not media_tools.available(),
        }

    def _timing_status(self):
        directory = timing_model_directory()
        status = self.server.studio.timing_models.status(directory or None)
        runtime_available = local_timing.available()
        return {**status, "ready": status["ready"] and runtime_available, "runtimeAvailable": runtime_available,
                "existingDirectory": directory}

    def _quit(self):
        studio = self.server.studio
        with studio.lock:
            if (studio.jobs or studio.motion_jobs or studio.providers_in_use() or media_tools.INSTALL_LOCK.locked()
                    or studio.timing_models.installing or self.server.active_archive_transfers):
                raise WorkflowError("Wait for the current operation to finish before quitting.", 409)
            self.server.closing = True
        try:
            self._json({"quitting": True})
        finally:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    @contextmanager
    def _archive_transfer(self):
        studio = self.server.studio
        with studio.lock:
            if self.server.closing:
                raise WorkflowError("Bacchus is closing.", 409)
            self.server.active_archive_transfers += 1
        try:
            with TemporaryDirectory(prefix=".style-transfer-", dir=studio.root) as folder:
                yield Path(folder)
        finally:
            with studio.lock:
                self.server.active_archive_transfers -= 1

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

    def _request_length(self, limit):
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as error:
            raise WorkflowError("Invalid content length.") from error
        if length < 1 or length > limit:
            raise WorkflowError(f"Request size must be between 1 and {limit:,} bytes.", 413)
        return length

    def _raw(self, limit):
        length = self._request_length(limit)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise WorkflowError("Request data was incomplete.")
        return raw

    def _body(self, limit=JSON_LIMIT):
        if self.headers.get_content_type() != "application/json":
            raise WorkflowError("Expected a JSON request.", 415)
        try:
            return json.loads(self._raw(limit))
        except (ValueError, RecursionError, UnicodeError) as error:
            raise WorkflowError("The request body must be valid JSON.") from error

    def _json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._bytes(data, "application/json; charset=utf-8", status)

    def _headers(self, status, mime, length, cache="no-store", csp=CSP):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")

    def _bytes(self, data, mime, status=200, csp=CSP):
        self._headers(status, mime, len(data), csp=csp)
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
            cache = "no-store" if kind in ("video", "captions", "receipt", "archive") else "private, max-age=3600"
            self._headers(206 if requested else 200, mime, remaining, cache=cache)
            if kind in ("audio", "video"):
                self.send_header("Accept-Ranges", "bytes")
            if requested:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            if kind in ("captions", "receipt", "archive"):
                self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
            self.end_headers()
            source.seek(first)
            while remaining:
                chunk = source.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


class StudioServer(ThreadingHTTPServer):
    def __init__(self, port, studio, settings):
        self.studio = studio
        self.settings = settings
        self.closing = False
        self.active_archive_transfers = 0
        self.instance_id = uuid.uuid4().hex
        super().__init__(("127.0.0.1", port), Handler)
