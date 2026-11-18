"""Desktop storage and reopening an existing local Bacchus instance."""

import http.client
import json
import os
from pathlib import Path
import re
import sys
import webbrowser


LOOPBACK = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})")
APP_VERSION = "0.3.1"


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


def open_running(data):
    try:
        instance = json.loads((data / "runtime.json").read_bytes())
        url, identity = instance["url"], instance["instance"]
        match = LOOPBACK.fullmatch(url)
        if not match or not 0 < (port := int(match[1])) < 65536:
            return False
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/api/instance")
            response = connection.getresponse()
            details = json.loads(response.read(2048)) if response.status == 200 else {}
        finally:
            connection.close()
        if details.get("instance") != identity or not isinstance(identity, str):
            return False
    except (OSError, ValueError, AttributeError, KeyError, TypeError, http.client.HTTPException):
        return False
    return webbrowser.open(url)
