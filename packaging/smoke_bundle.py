"""Start and verify a frozen Bacchus executable over localhost."""

import argparse
import json
from pathlib import Path
from runpy import run_path
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


APP_VERSION = run_path(str(Path(__file__).resolve().parents[1] / "bacchus" / "runtime.py"))["APP_VERSION"]


def request(url, path, data=None):
    options = {}
    if data is not None:
        options = {"data": json.dumps(data).encode(), "headers": {"Content-Type": "application/json"}}
    with urlopen(Request(url + path, **options), timeout=25) as response:
        return response.read()


def endpoint(root, process):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Bacchus exited during startup with code {process.returncode}.")
        try:
            url = json.loads((root / "runtime.json").read_bytes())["url"].rstrip("/")
        except (OSError, ValueError, KeyError):
            time.sleep(0.1)
            continue
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
            raise RuntimeError("Bacchus returned an unexpected local endpoint.")
        return url
    raise RuntimeError("Bacchus did not announce its local endpoint.")


def run_export(url, project):
    route = f"/api/projects/{project['id']}"
    for action in ("qc", "candidate/accept", "export"):
        project = json.loads(request(url, f"{route}/{action}", {"revision": project["revision"]}))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        project = json.loads(request(url, route))
        export = project["export"]
        if export["status"] == "complete":
            if request(url, export["url"])[4:8] != b"ftyp":
                raise RuntimeError("Export downloaded but lacked an MP4 file header.")
            return
        if export["status"] == "failed":
            raise RuntimeError(export["error"])
        time.sleep(0.25)
    raise RuntimeError("The example export exceeded its time limit.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--render", action="store_true", help="Export the example through FFmpeg and FFprobe.")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="bacchus-bundle-smoke-") as data:
        root = Path(data)
        command = [str(args.executable.resolve()), "--no-browser", "--data", data, "--port", "0"]
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            url = endpoint(root, process)
            config = json.loads(request(url, "/api/config"))
            if config.get("version") != APP_VERSION:
                raise RuntimeError(f"Expected Bacchus {APP_VERSION}, received {config.get('version')!r}.")
            if not config["pack"]["version"] or b"Bacchus" not in request(url, "/"):
                raise RuntimeError("The bundle failed to load its local pack or interface.")
            for asset in ("/static/app.js", "/static/styles.css"):
                if not request(url, asset):
                    raise RuntimeError(f"Missing embedded asset {asset}.")
            project = json.loads(request(url, "/api/demo", {}))
            if not project["input"] or not project["plan"]["approved"]:
                raise RuntimeError("The packaged app could not create the demo project.")
            if args.render:
                run_export(url, project)
            request(url, "/api/quit", {})
            if process.wait(15) != 0:
                raise RuntimeError("Bacchus returned a failure status after quitting.")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(10)
    print("Verified packaged startup, interface, offline example, and clean shutdown"
          + (", including MP4 export." if args.render else "."))


if __name__ == "__main__":
    main()
