"""Start and verify a frozen Bacchus executable over localhost."""

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from runpy import run_path
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image


APP_VERSION = run_path(str(Path(__file__).resolve().parents[1] / "bacchus" / "runtime.py"))["APP_VERSION"]
PROVIDER_SETTINGS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BACCHUS_PLANNER_PROVIDER", "BACCHUS_CLAUDE_MODEL",
    "BACCHUS_USE_OPENAI_TRANSCRIPTION", "BACCHUS_USE_OPENAI_IMAGES",
)


def request(url, path, data=None):
    options = {}
    if isinstance(data, bytes):
        options = {"data": data, "headers": {"Content-Type": "image/png"}}
    elif data is not None:
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


def expect_conflict(url, path, data=None):
    try:
        request(url, path, data)
    except HTTPError as error:
        if error.code == 409:
            return
        raise
    raise RuntimeError(f"The app accepted {path} before the required review.")


def run_export(url, project):
    route = f"/api/projects/{project['id']}"

    def action(name, **data):
        return json.loads(request(url, f"{route}/{name}", {"revision": project["revision"], **data}))

    scenes = project["plan"]["scenes"]
    artwork = "smoke-art"
    scenes[0].update(layout="illustration", artId=artwork, artBrief="A sample blue square.")
    project = action("plan", scenes=scenes)
    project = action("plan/approve")
    with BytesIO() as buffer:
        Image.new("RGBA", (96, 96), "#386e9e").save(buffer, format="PNG")
        raw = buffer.getvalue()
    project = json.loads(request(
        url, f"{route}/assets/{artwork}?revision={project['revision']}&name=smoke-art.png", raw,
    ))
    project = action(f"assets/{artwork}/approve")
    expect_conflict(url, f"{route}/assets/{artwork}/publish", {"revision": project["revision"]})
    expect_conflict(url, f"{route}/mix/approve", {"revision": project["revision"], "reviewed": True})
    project = action("mix/prepare")
    prepared = project["mix"]["rendered"]
    wav = request(url, prepared["url"])
    if not wav.startswith(b"RIFF") or hashlib.sha256(wav).hexdigest() != prepared["sha256"]:
        raise RuntimeError("The mix preview did not match the measured WAV.")
    project = action("mix/approve", reviewed=True)

    project = action("assets/checks")
    if len(project["assetChecks"]) != 1:
        raise RuntimeError("The illustrated example lacked its planned artwork use check.")
    expect_conflict(url, f"{route}/animation/prepare", {"revision": project["revision"]})
    expect_conflict(url, f"{route}/preview?time=0.5")
    project = verify_checks(url, route, project, "assets", "assetChecks")
    project = action(f"assets/{artwork}/publish")
    if project["assetChecks"][0]["status"] != "passed":
        raise RuntimeError("Publishing discarded a passed artwork use.")
    project = action("animation/prepare")
    if not request(url, f"{route}/preview?time=0.5").startswith(b"\x89PNG"):
        raise RuntimeError("The prepared example lacked a renderer preview.")
    project = action("qc")
    if not project["animationChecks"]:
        raise RuntimeError("The prepared example lacked per-scene review checks.")
    expect_conflict(url, f"{route}/candidate/accept", {"revision": project["revision"]})
    expect_conflict(url, f"{route}/export", {"revision": project["revision"]})
    project = verify_checks(url, route, project, "animation", "animationChecks")
    for name in ("candidate/accept", "export"):
        project = action(name)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        project = json.loads(request(url, route))
        export = project["export"]
        if export["status"] == "complete":
            if request(url, export["url"])[4:8] != b"ftyp":
                raise RuntimeError("Export downloaded but lacked an MP4 file header.")
            receipt = json.loads(request(url, export["receiptUrl"]))
            media = export["media"]
            if (media != receipt["media"] or media.get("encoderMode") != "auto" or not media.get("encoder")
                or media.get("hardwareEncoding") != (media["encoder"] != "libx264")
                or (not media["hardwareEncoding"] and not media.get("encoderFallback"))):
                raise RuntimeError("The default export did not identify its actual encoder and any fallback.")
            if receipt["candidate"]["mix"]["rendered"]["sha256"] != prepared["sha256"]:
                raise RuntimeError("The exported receipt did not bind the approved WAV.")
            for scope, key in (("assets", "assetChecks"), ("animation", "animationChecks")):
                checks = receipt["review"][scope]
                if set(checks) != {check["id"] for check in project[key]} or any(
                    check["status"] != "passed" or not check["proofHash"] for check in checks.values()
                ):
                    raise RuntimeError(f"The exported receipt did not bind the passed {scope} proofs.")
            return media["encoder"]
        if export["status"] == "failed":
            raise RuntimeError(export["error"])
        time.sleep(0.25)
    raise RuntimeError("The example export exceeded its time limit.")


def verify_checks(url, route, project, scope, key):
    for check in project[key]:
        if not request(url, check["proofUrl"]).startswith(b"\x89PNG"):
            raise RuntimeError(f"Scene {check['scene']} did not produce a valid {scope} proof.")
        path = f"{route}/{scope}/checks/{check['id']}/result"
        project = json.loads(request(url, path, {
            "revision": project["revision"], "passed": True, "reason": "Packaged smoke-test fixture.",
        }))
    return project


def check_provider_defaults(url):
    settings = json.loads(request(url, "/api/settings"))
    if settings["plannerProvider"] != "anthropic" or settings["claudeModel"] != "claude-sonnet-5-5":
        raise RuntimeError("Fresh setup must default to Claude Sonnet 5.5.")

    # Exercise readiness only. No test key is ever sent to a provider.
    request(url, "/api/settings", {"openAIKey": "test-local-openai-key"})
    config = json.loads(request(url, "/api/config"))
    if config["plannerAvailable"] or config["transcriberAvailable"] or config["imageAvailable"]:
        raise RuntimeError("An OpenAI key alone enabled an AI stage.")
    request(url, "/api/settings", {"anthropicKey": "test-local-anthropic-key"})
    config = json.loads(request(url, "/api/config"))
    if config["plannerProvider"] != "anthropic" or not config["plannerAvailable"]:
        raise RuntimeError("A Claude key alone must enable the default planner.")
    if config["transcriberAvailable"] or config["imageAvailable"]:
        raise RuntimeError("OpenAI media features must remain opt-in.")
    request(url, "/api/settings", {"clearAnthropicKey": True, "clearOpenAIKey": True})


def check_audit_directory(url, root):
    directory = root / "provider-audit"
    request(url, "/api/settings", {"apiLogDirectory": str(directory)})
    settings = json.loads(request(url, "/api/settings"))
    on_disk = json.loads((root / "settings.json").read_bytes())
    if any(Path(value) != directory for value in (
        settings["apiLogDirectory"], settings["apiLogPath"], on_disk["apiLogDirectory"],
    )):
        raise RuntimeError("The custom API audit folder did not persist.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--render", action="store_true", help="Export the example through FFmpeg and FFprobe.")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="bacchus-bundle-smoke-") as data:
        root = Path(data)
        command = [str(args.executable.resolve()), "--no-browser", "--data", data, "--port", "0"]
        env = os.environ.copy()
        for variable in PROVIDER_SETTINGS:
            env.pop(variable, None)
        env.pop("BACCHUS_VIDEO_ENCODER", None)
        process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            check_provider_defaults(url)
            check_audit_directory(url, root)
            project = json.loads(request(url, "/api/demo", {}))
            if not project["input"] or not project["plan"]["approved"]:
                raise RuntimeError("The packaged app could not create the demo project.")
            if args.render:
                print(f"Verified default export encoder: {run_export(url, project)}")
            request(url, "/api/quit", {})
            if process.wait(15) != 0:
                raise RuntimeError("Bacchus returned a failure status after quitting.")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(10)
    print("Verified packaged startup, Claude-first setup, offline example, and clean shutdown"
          + (", including evidence gates and MP4 export." if args.render else "."))


if __name__ == "__main__":
    main()
