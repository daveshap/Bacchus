"""Local, append-only provider call records with credential redaction."""

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile, gettempdir
from uuid import uuid4


_SCOPE = ContextVar("bacchus_api_scope", default=None)
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class ApiLogError(Exception):
    pass


def default_directory() -> Path:
    temporary = Path(gettempdir())
    if os.name == "nt":
        return temporary / "Bacchus API Logs"
    return temporary / f"bacchus-api-logs-{os.getuid()}"


@contextmanager
def scope(project_id, stage):
    token = _SCOPE.set({"projectId": project_id, "stage": stage})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _secret_patterns(secrets):
    patterns = set()
    for secret in secrets:
        if secret:
            patterns.add(secret.encode("utf-8"))
            for ascii_only in (False, True):
                patterns.add(json.dumps(secret, ensure_ascii=ascii_only)[1:-1].encode("utf-8"))
    return tuple(sorted(patterns, key=len, reverse=True))


def _redact(body, patterns):
    for pattern in patterns:
        body = body.replace(pattern, b"[REDACTED]")
    return body


def _redact_text(value, patterns):
    if value is None:
        return None
    return _redact(value.encode("utf-8"), patterns).decode("utf-8")


def _timestamp():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _root(directory):
    value = Path(directory).expanduser().absolute()
    value.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and value == default_directory().absolute():
        details = value.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o077:
            raise ApiLogError("Choose a private folder for API logs. The default folder is not private.")
    return value


def _write(path, data):
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".api-log-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _metadata(path, record):
    _write(path, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _body_file(path, body, patterns):
    clean = _redact(body, patterns)
    _write(path, clean)
    return {"bodyFile": path.name, "bytes": len(clean), "redacted": clean != body}


class Call:
    def __init__(self, path, record, patterns):
        self.path = path
        self._record = record
        self._patterns = patterns

    def finish(self, status: int | None, body: bytes | None = None, *, content_type: str | None = None,
               request_id: str | None = None, error: str | None = None, truncated: bool = False):
        error_code = error if error is None or _ERROR_CODE.fullmatch(error) else "provider_request_failed"
        successful = status is not None and 200 <= status < 300 and error_code is None and not truncated
        response = {
            "status": status,
            "requestId": _redact_text(request_id, self._patterns),
            "contentType": _redact_text(content_type, self._patterns),
            "truncated": truncated,
            "error": _redact_text(error_code, self._patterns),
        }
        try:
            if body is not None:
                response.update(_body_file(self.path / "response.body", body, self._patterns))
            _metadata(self.path / "metadata.json", {
                **self._record, "state": "completed" if successful else "failed",
                "finishedAt": _timestamp(), "response": response,
            })
        except OSError as failure:
            raise ApiLogError("Bacchus could not finish the provider audit log.") from failure


def start(directory: Path, provider: str, url: str, request_body: bytes, content_type: str,
          secrets: tuple[str, ...] = ()) -> Call:
    """Record the request before it is sent."""
    identity = uuid4().hex
    now = datetime.now(UTC)
    patterns = _secret_patterns(secrets)
    source = _SCOPE.get()
    record = {
        "id": identity,
        "startedAt": now.isoformat(timespec="milliseconds"),
        "state": "pending",
        "provider": _redact_text(provider, patterns),
        "url": _redact_text(url, patterns),
        "method": "POST",
        "scope": {key: _redact_text(value, patterns) for key, value in source.items()} if source else None,
        "request": {"contentType": _redact_text(content_type, patterns)},
        "response": None,
    }
    try:
        path = _root(directory) / f"{now:%Y%m%dT%H%M%S%fZ}-{identity}"
        path.mkdir(mode=0o700)
        _metadata(path / "metadata.json", record)
        record["request"].update(_body_file(path / "request.body", request_body, patterns))
        _metadata(path / "metadata.json", record)
    except OSError as failure:
        raise ApiLogError("Bacchus could not start the provider audit log.") from failure
    return Call(path, record, patterns)
