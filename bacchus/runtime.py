"""Desktop storage and reopening an existing local Bacchus instance."""

import http.client
import json
import os
from pathlib import Path
import re
import sys
import webbrowser


LOOPBACK = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})")
APP_VERSION = "0.8.3"


def _absolute_override(name, fallback):
    value = os.environ.get(name)
    if value:
        path = Path(value)
        if path.is_absolute():
            return path
    return fallback


def default_data_directory():
    home = Path.home()
    if sys.platform == "win32":
        return _absolute_override("LOCALAPPDATA", home / "AppData" / "Local") / "Bacchus"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Bacchus"
    return _absolute_override("XDG_DATA_HOME", home / ".local" / "share") / "bacchus"


def running_instance(data):
    """Identify the live studio that owns this workspace."""
    try:
        with (data / "runtime.json").open("rb") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            return None
        instance = json.loads(raw)
        url, identity = instance["url"], instance["instance"]
        match = LOOPBACK.fullmatch(url)
        if not match or not 0 < (port := int(match[1])) < 65536:
            return None
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/api/instance")
            response = connection.getresponse()
            details = json.loads(response.read(2048)) if response.status == 200 else {}
        finally:
            connection.close()
        if details.get("instance") != identity or not isinstance(identity, str):
            return None
    except (OSError, ValueError, AttributeError, KeyError, TypeError, RecursionError, http.client.HTTPException):
        return None
    return port, identity


def open_running(data):
    instance = running_instance(data)
    return bool(instance and webbrowser.open(f"http://127.0.0.1:{instance[0]}"))
