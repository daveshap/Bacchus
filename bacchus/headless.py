"""Connect MCP clients to the single local studio that owns the project workspace."""

import argparse
from contextlib import ExitStack
from functools import cached_property
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .runtime import APP_VERSION, running_instance
from .workspace import WorkspaceLocation


def _launch_host(workspace):
    if getattr(sys, "frozen", False):
        desktop = Path(sys.executable).with_name("Bacchus.exe" if os.name == "nt" else "Bacchus")
        command = [str(desktop)]
        cwd = desktop.parent
    else:
        command = [sys.executable, "-B", "-m", "bacchus"]
        cwd = Path(__file__).resolve().parent.parent
    command.append("--no-browser")
    if workspace.override:
        command.extend(("--data", str(workspace.active)))
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    # A long render belongs to the workspace. The host must not hold the client's MCP pipes open.
    return subprocess.Popen(
        command, cwd=cwd, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
    )


class StudioClient:
    def __init__(self, directory=None):
        self.directory = directory
        self.host = None

    @cached_property
    def workspace(self):
        return WorkspaceLocation(self.directory)

    def _instance(self):
        directory = self.workspace.active
        if instance := running_instance(directory):
            return instance
        self.host = _launch_host(self.workspace)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if instance := running_instance(directory):
                return instance
            # Another simultaneous client can win the desktop's workspace lock.
            if self.host.poll() not in (None, 75):
                raise RuntimeError(f"The studio could not start. Check the Bacchus log in {directory}.")
            time.sleep(0.1)
        raise RuntimeError(f"The studio has not become ready. Check the Bacchus log in {directory}.")

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self._instance()[0]}"

    def request(self, method, path, *, data=None, file=None, content_type=None):
        return self._request(self._instance(), method, path, data=data, file=file, content_type=content_type)

    def preview_frame(self, project_id, seconds):
        path = f"/api/projects/{project_id}/preview?time={seconds}&width=640"
        return self._request(self._instance(), "GET", path, png=True)

    @staticmethod
    def _request(instance, method, path, *, data=None, file=None, content_type=None, png=False):
        port, identity = instance
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=900)
        headers = {"X-Bacchus-Instance": identity}
        with ExitStack() as stack:
            stack.callback(conn.close)
            body = None
            if file is not None:
                body = stack.enter_context(file.open("rb"))
                headers.update({"Content-Length": str(os.fstat(body.fileno()).st_size),
                                "Content-Type": content_type or "application/octet-stream"})
            elif data is not None:
                body = json.dumps(data, allow_nan=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            try:
                conn.request(method, path, body=body, headers=headers)
                response = conn.getresponse()
                if png and response.status == 200:
                    image = response.read(2 * 1024 * 1024 + 1)
                    if (response.getheader("Content-Type") != "image/png" or len(image) > 2 * 1024 * 1024
                            or not image.startswith(b"\x89PNG\r\n\x1a\n")):
                        raise RuntimeError("The studio returned an invalid preview image.")
                    return image
                result = json.loads(response.read())
            except (OSError, ValueError, http.client.HTTPException) as exc:
                raise RuntimeError("Lost the studio response. Check project status before retrying.") from exc
        if not 200 <= response.status < 300:
            reason = result.get("error") if isinstance(result, dict) else None
            raise RuntimeError(reason or f"The studio returned HTTP {response.status}.")
        return result

    def stop(self):
        instance = running_instance(self.workspace.active)
        if instance:
            self._request(instance, "POST", "/api/quit", data={})


def main():
    parser = argparse.ArgumentParser(description="Serve Bacchus tools over MCP stdio.")
    parser.add_argument("--data", type=Path, help="Use this workspace in place of the saved studio workspace.")
    parser.add_argument("--stop", action="store_true", help="Quit the studio for this workspace when it is idle.")
    parser.add_argument("--version", action="version", version=APP_VERSION)
    args = parser.parse_args()
    client = StudioClient(args.data)
    try:
        if args.stop:
            client.stop()
        else:
            from .mcp_server import serve
            serve(client, sys.stdin.buffer, sys.stdout.buffer)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"Bacchus MCP: {error}\n")
