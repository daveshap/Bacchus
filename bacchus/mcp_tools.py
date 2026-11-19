"""Production tools that use the same revision checks and gates as the desktop studio."""

import os
from pathlib import Path
import re
import stat
from urllib.parse import urlencode

from . import budget


class ToolError(ValueError):
    pass


def _object(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_ID = {"type": "string", "pattern": "^[a-f0-9]{32}$", "description": "Bacchus project ID."}
_REV = {"type": "integer", "minimum": 0, "description": "Revision last returned for this project."}
_BRIEF = {"type": "string", "maxLength": 2000}
_COUNTER = {"type": "integer", "minimum": 0}
_RATE = _object({
    "inputUsdPerMillion": {"type": "number", "minimum": 0},
    "outputUsdPerMillion": {"type": "number", "minimum": 0},
    "imageMaxUsd": {"type": "number", "exclusiveMinimum": 0},
    "imageMaxTokens": {"type": "integer", "minimum": 1},
})
_LIMITS = _object({
    "maxTokens": {"type": ["integer", "null"], "minimum": 0},
    "maxUsd": {"type": ["number", "null"], "minimum": 0},
    "rates": {"type": "object", "additionalProperties": {**_RATE, "minProperties": 1},
              "description": "Rates keyed by configured model ID. Dollar caps require rates for the models used."},
})
_PROJECT = {"project_id": _ID}
_EDIT = {**_PROJECT, "expected_revision": _REV}
_FILE = {**_EDIT, "path": {"type": "string", "minLength": 1, "description": "Absolute file path on the MCP host."}}
_PAGE = {"offset": _COUNTER, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}}


def _tool(name, description, properties=None, required=(), *, read_only=False):
    return {"name": name, "description": description, "inputSchema": _object(properties or {}, required),
            "annotations": {"readOnlyHint": read_only}}


TOOLS = [
    _tool("get_readiness", "Check the studio's renderer, audio tools, planner, image provider and local word timing.",
          read_only=True),
    _tool("list_styles", "List saved style IDs and revisions with qualified artwork counts.", _PAGE, read_only=True),
    _tool("list_projects", "List projects in the active workspace, most recently updated first.", _PAGE, read_only=True),
    _tool("get_project", "Get the current revision, recording fingerprint, gate progress, usage and export status.",
          _PROJECT, _PROJECT, read_only=True),
    _tool("preview_frame", "Inspect a frame of the prepared animation at a narration timestamp in seconds. "
          "Returns a 640-pixel-wide PNG from the studio preview renderer.",
          {**_PROJECT, "seconds": {"type": "number", "minimum": 0, "maximum": 3600}},
          ("project_id", "seconds"), read_only=True),
    _tool("create_project", "Create an Auto video project without running providers. The default style is Warm Editorial. "
          "A selected style uses its latest revision unless style_revision is specified. Omitted limits leave caps unset.",
          {"title": {"type": "string", "minLength": 1, "maxLength": 120},
           "style_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
           "style_revision": {"type": "integer", "minimum": 1}, "brief": _BRIEF, "limits": _LIMITS}, ("title",)),
    _tool("create_demo", "Create the offline Manual example with its approved scene plan. "
          "Use the studio UI to review the example before rendering."),
    _tool("attach_narration", "Attach an MP3, FLAC or WAV to unlocked project intake. Returns the original SHA-256 "
          "for matching timing evidence. This does not start Auto.", _FILE, _FILE),
    _tool("import_timings", "Import a recording-matched JSON word timing file with audio.sha256, duration, "
          "and words containing text, start and end. An alternative to local timing during Auto.", _FILE, _FILE),
    _tool("set_auto_settings", "Update a project's brief or budget before a run. Omitted settings are kept. "
          "A supplied limits object replaces the full limits and model rates.", {**_EDIT, "brief": _BRIEF, "limits": _LIMITS}, _EDIT),
    _tool("run_auto", "Explicitly start the independent gated production pipeline. This can make paid provider calls. "
          "The run continues in the studio; poll get_project for progress.", _EDIT, _EDIT),
    _tool("render_accepted", "Queue a local render of a Manual project's already accepted candidate. Requires "
          "the studio's normal checks and approvals. Poll get_project, then get_delivery.", _EDIT, _EDIT),
    _tool("get_delivery", "Fetch verified local file paths and live download URLs for the latest intact MP4, "
          "SRT and receipt. Reports when an earlier delivery is the one still available.", _PROJECT, _PROJECT, read_only=True),
]

_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOLS}


def _validate(name, args):
    schema = _SCHEMAS.get(name)
    if not schema:
        raise ToolError("Unknown Bacchus tool.")
    if not isinstance(args, dict) or args.keys() - schema["properties"].keys():
        raise ToolError(f"Use only the documented arguments for {name}.")
    missing = [field for field in schema["required"] if field not in args]
    if missing:
        raise ToolError(f"Missing required arguments: {', '.join(missing)}.")
    for key, value in args.items():
        spec = schema["properties"][key]
        if spec["type"] == "integer":
            if type(value) is not int or value < spec["minimum"] or ("maximum" in spec and value > spec["maximum"]):
                raise ToolError(f"Enter a valid integer for {key}.")
        elif spec["type"] == "number":
            if type(value) not in (int, float) or not spec["minimum"] <= value <= spec["maximum"]:
                raise ToolError(f"Enter a valid number for {key}.")
        elif spec["type"] == "string":
            if (not isinstance(value, str) or len(value.strip()) < spec.get("minLength", 0)
                    or ("maxLength" in spec and len(value) > spec["maxLength"])
                    or ("pattern" in spec and not re.fullmatch(spec["pattern"], value))):
                raise ToolError(f"Enter a valid {key}.")
    if "limits" in args:
        if not isinstance(args["limits"], dict):
            raise ToolError("limits must be an object.")
        try:
            budget.validate_limits(args["limits"])
        except budget.BudgetError as error:
            raise ToolError(str(error)) from error


def _pick(record, *keys):
    return {key: record[key] for key in keys if key in record}


def _summary(project):
    result = _pick(project, "id", "title", "revision", "autoMode", "demo", "busy", "accepted")
    result["style"] = _pick(project.get("styleSnapshot") or {}, "id", "name", "revision")
    audio = project.get("audio")
    result["audio"] = _pick(audio, "name", "duration", "originalHash") if audio else None
    timing = project.get("transcript") or project.get("input") or {}
    result["timing"] = {"method": timing.get("timing"), "words": len(timing.get("words") or [])}
    result["planApproved"] = bool((project.get("plan") or {}).get("approved"))
    for field, keys in (
        ("autoRun", ("status", "currentStage", "stagesCompleted", "blocked", "error", "startedAt", "finishedAt")),
        ("autoUsage", ("calls", "tokens", "usd", "unknownTokenCalls", "unknownUsdCalls", "heldCalls")),
        ("export", ("status", "verification", "deliveryId", "error", "integrityError")),
    ):
        record = project.get(field)
        result[field] = _pick(record, *keys) if record else None
        if record and record.get("error"):
            result[field]["error"] = str(record["error"])[:1200]
    checks = (project.get("autoRun") or {}).get("checks") or []
    if checks:
        latest = checks[-1]
        result["autoRun"]["lastCheck"] = {
            **_pick(latest, "stage", "item", "status"),
            "findings": [{"id": row.get("id"), "status": row.get("status"), "evidence": str(row.get("evidence", ""))[:320]}
                         for row in latest.get("findings", []) if row.get("status") in ("fail", "unclear")][:3],
        }
    if project.get("autoMode"):
        result["autoLimits"] = _pick(project.get("autoLimits") or {}, "maxTokens", "maxUsd", "rates")
    result["latestVerifiedDeliveryId"] = (project.get("lastDelivery") or {}).get("deliveryId")
    return result


def _page(items, args, label, fields):
    offset, limit = args.get("offset", 0), args.get("limit", 25)
    return {label: [_pick(row, *fields) for row in items[offset:offset + limit]], "total": len(items),
            "nextOffset": offset + limit if offset + limit < len(items) else None}


def _upload_path(value, formats, limit):
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ToolError("The local upload path contains an invalid character.")
    path = Path(value)
    # UNC paths, Windows device names and alternate data streams are not local media files.
    def local_name(candidate):
        return (candidate.is_absolute() and not str(candidate).startswith(("//", "\\\\"))
                and (os.name != "nt" or not os.path.isreserved(candidate)))

    if not local_name(path) or path.suffix.lower() not in formats:
        raise ToolError(f"Choose an absolute local file with one of these extensions: {', '.join(formats)}.")
    try:
        target = path.resolve(strict=True)
        if not local_name(target) or target.suffix.lower() not in formats:
            raise ToolError("The selected path must resolve to a local media file of the requested type.")
        info = target.stat()
    except (OSError, RuntimeError) as error:
        raise ToolError("The selected file could not be opened on the MCP host.") from error
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ToolError(f"Select a regular file between 1 and {limit:,} bytes.")
    return target


def _delivery(client, route, project):
    current = project.get("export") or {}
    latest = project.get("lastDelivery") or current
    result = {"available": False, "projectId": project["id"], "revision": project["revision"],
              "exportStatus": current.get("status")}
    if latest.get("status") != "complete" or latest.get("verification") != "verified":
        return result
    key = latest.get("deliveryId")
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ToolError("The studio returned an invalid delivery ID.")
    urls = {}
    base_url = client.base_url.rstrip("/")
    for kind, field in (("video", "url"), ("captions", "captionsUrl"), ("receipt", "receiptUrl")):
        relative = f"{route}/media/{kind}/{key}"
        if latest.get(field) != relative:
            raise ToolError("The studio returned an unexpected delivery route.")
        urls[kind] = base_url + relative
    verified = client.request("GET", f"{route}/delivery-files/{key}")["files"]
    files = _pick(verified, "video", "captions", "receipt")
    if files.keys() != urls.keys() or not all(isinstance(p, str) and Path(p).is_absolute() for p in files.values()):
        raise ToolError("The studio did not return all three verified local file paths.")
    return {**result, "available": True, "deliveryId": key, "current": current.get("status") == "complete"
            and current.get("deliveryId") == key, "files": files, "urls": urls}


def call_tool(client, name, arguments):
    """Dispatch a named MCP tool through the studio's public loopback HTTP interface."""
    _validate(name, arguments)
    args = arguments
    if name == "get_readiness":
        config = client.request("GET", "/api/config")
        settings = client.request("GET", "/api/settings")
        model_key = "openAIModel" if settings["plannerProvider"] == "openai" else "claudeModel"
        return {**_pick(config, "version", "renderAvailable", "renderBackend", "browserAvailable",
                        "browserLabel", "audioAvailable", "plannerAvailable", "imageAvailable",
                        "localTimingAvailable", "maxDuration", "maxAudioBytes"),
                **_pick(settings, "plannerProvider", "imageModel", "dataPath"), "plannerModel": settings[model_key]}
    if name == "list_styles":
        return _page(client.request("GET", "/api/styles")["styles"], args, "styles",
                     ("id", "name", "revision", "visualPreset", "assetCount", "libraryIssueCount"))
    if name == "list_projects":
        return _page(client.request("GET", "/api/projects")["projects"], args, "projects",
                     ("id", "title", "updatedAt", "styleId", "styleName", "autoMode"))
    if name == "create_demo":
        return _summary(client.request("POST", "/api/demo", data={}))
    if name == "create_project":
        body = {"title": args["title"], "autoMode": True, "autoBrief": args.get("brief", ""),
                "autoLimits": args.get("limits", {})}
        if "style_revision" in args and "style_id" not in args:
            raise ToolError("style_revision requires style_id.")
        if style_id := args.get("style_id"):
            revision = args.get("style_revision")
            if revision is None:
                revision = client.request("GET", f"/api/styles/{style_id}")["revision"]
            body.update(styleId=style_id, styleRevision=revision)
        return _summary(client.request("POST", "/api/projects", data=body))

    route = f"/api/projects/{args['project_id']}"
    if name == "preview_frame":
        return client.preview_frame(args["project_id"], args["seconds"])
    if name == "get_project":
        return _summary(client.request("GET", route))
    if name == "get_delivery":
        return _delivery(client, route, client.request("GET", route))
    body = {"revision": args["expected_revision"]}
    if name == "set_auto_settings":
        if "brief" not in args and "limits" not in args:
            raise ToolError("Supply a brief or limits to update.")
        project = client.request("GET", route)
        body.update(autoBrief=args.get("brief", project.get("autoBrief", "")),
                    autoLimits=args.get("limits", project.get("autoLimits") or {}))
        return _summary(client.request("POST", route + "/auto/settings", data=body))
    if name == "run_auto":
        return _summary(client.request("POST", route + "/auto/run", data=body))
    if name == "render_accepted":
        if client.request("GET", route).get("autoMode") is not False:
            raise ToolError("Use run_auto to render an Auto project with its independent delivery review.")
        return _summary(client.request("POST", route + "/export", data=body))

    if name == "attach_narration":
        formats = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav"}
        path = _upload_path(args["path"], formats, 384 * 1024 * 1024)
        mime = formats[path.suffix.lower()]
        body["name"] = path.name
        endpoint = "audio"
    else:
        path = _upload_path(args["path"], (".json",), 4 * 1024 * 1024)
        endpoint, mime = "timings", "application/json"
    response = client.request("POST", f"{route}/{endpoint}?{urlencode(body)}", file=path, content_type=mime)
    return _summary(response)
