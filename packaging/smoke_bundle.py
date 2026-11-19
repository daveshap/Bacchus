"""Start and verify a frozen Bacchus executable over localhost."""

import argparse
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from runpy import run_path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import wave
from zipfile import ZipFile

from PIL import Image


SOURCE_ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = run_path(str(SOURCE_ROOT / "bacchus" / "runtime.py"))["APP_VERSION"]


def request(url, path, data=None, *, content_type="image/png"):
    options = {}
    if isinstance(data, bytes):
        options = {"data": data, "headers": {"Content-Type": content_type}}
    elif data is not None:
        options = {"data": json.dumps(data).encode(), "headers": {"Content-Type": "application/json"}}
    with urlopen(Request(url + path, **options), timeout=25) as response:
        return response.read()


def export_deadline(project):
    # Allow the Canvas encode, fallback and full-file verification to finish on CPU runners.
    return time.monotonic() + 240 + 36 * project["input"]["duration"]


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


def png(size, color):
    with BytesIO() as buffer:
        Image.new("RGBA", size, color).save(buffer, format="PNG")
        return buffer.getvalue()


def check_styles(url):
    defaults = json.loads(request(url, "/api/styles"))["styles"]
    expected = [("professional-corporate", "Professional Corporate"), ("hand-drawn-explainer", "Hand-drawn Explainer")]
    if [(style["id"], style["name"]) for style in defaults[:2]] != expected or any(
        not style["builtIn"] or not style["guide"].strip() or style["assetCount"] != 0 for style in defaults
    ):
        raise RuntimeError("Fresh setup did not load its guided built-in styles and empty libraries.")
    if len({style["libraryName"] for style in defaults}) != len(defaults):
        raise RuntimeError("Built-in styles did not have independently named libraries.")

    image = json.loads(request(url, "/api/style-references?name=mood-board.png", png((1280, 640), "#548369")))
    normalized = request(url, image["url"])
    if hashlib.sha256(normalized).hexdigest() != image["sha256"]:
        raise RuntimeError("The saved style reference did not match its fingerprint.")
    with Image.open(BytesIO(normalized)) as picture:
        if picture.size != (image["width"], image["height"]) or max(picture.size) > 1024:
            raise RuntimeError("The style reference was not normalized to the configured size.")
    del image["url"]
    answers = {
        "visualNotes": "  Wide margins.\r\nSoft green lines.  ", "colorNotes": "Keep the warm paper.",
        "sceneNotes": "Reveal a diagram on the spoken cue.", "music": False, "musicNotes": "Speech only.  ",
        "musicPreset": "pulse", "soundEffects": False, "effectsNotes": "",
    }
    guide = "# Smoke style\n\nQuiet margins and clear labels. Leave music off.\n"
    custom = json.loads(request(url, "/api/styles", {
        "name": "Smoke style", "libraryName": "Smoke style artwork", "description": "Local smoke fixture.",
        "visualPreset": "warm-editorial", "palette": {"accent": "#548369"}, "answers": answers,
        "guide": guide, "referenceImages": [image],
    }))
    style_id = custom["id"]
    path = f"/api/styles/{style_id}"
    saved = json.loads(request(url, path))
    if saved != custom or saved["guide"] != guide or saved["answers"] != answers or saved["revision"] != 1:
        raise RuntimeError("The style did not retain its written guide and verbatim answers.")
    if saved["referenceImages"][0]["sha256"] != image["sha256"] or saved["assetCount"] != 0:
        raise RuntimeError("The style did not bind its reference or opened a prepopulated art library.")
    revised = json.loads(request(url, path, {"revision": saved["revision"], "guide": "# Revised Smoke style\n"}))
    if revised["revision"] != 2 or revised["guide"] == guide:
        raise RuntimeError("Editing the style did not create a new guide revision.")

    project = json.loads(request(url, "/api/projects", {
        "title": "Smoke style project", "styleId": style_id, "styleRevision": saved["revision"],
    }))
    if project.get("autoMode") is not True:
        raise RuntimeError("A new project did not default to Auto mode.")
    project = json.loads(request(url, f"/api/projects/{project['id']}/mode", {
        "revision": project["revision"], "autoMode": False,
    }))
    if project["autoMode"]:
        raise RuntimeError("The project could not switch to manual review.")
    pinned = project["styleSnapshot"]
    if (pinned["id"], pinned["revision"], pinned["guide"], pinned["answers"]) != (style_id, 1, guide, answers):
        raise RuntimeError("The project did not retain its selected revision and guide preferences.")
    for scope, name in ((style_id, saved["libraryName"]), ("warm-editorial", "Warm Editorial Artwork")):
        library = json.loads(request(url, f"/api/library?styleId={scope}"))
        if library != {"assets": [], "issues": [], "storageError": None, "styleId": scope, "libraryName": name}:
            raise RuntimeError("An empty project did not have its own named style library.")
    return project


def check_style_mix_and_captions(url, project):
    route = f"/api/projects/{project['id']}"
    spoken = "Some captions fit quite a lot of speech into one second."

    def action(name, **data):
        return json.loads(request(url, f"{route}/{name}", {"revision": project["revision"], **data}))

    with BytesIO() as buffer:
        with wave.open(buffer, "wb") as recording:
            recording.setparams((1, 2, 16_000, 0, "NONE", "PCM"))
            recording.writeframes(b"\x00\x01" * 16_000)
        wav = buffer.getvalue()
    project = json.loads(request(
        url, f"{route}/audio?revision={project['revision']}&name=test.wav", wav, content_type="audio/wav",
    ))
    cues = {
        "audio": {"sha256": hashlib.sha256(wav).hexdigest()}, "duration": 1,
        "words": [
            {"text": word, "start": index * 0.08, "end": index * 0.08 + 0.07}
            for index, word in enumerate(spoken.split())
        ],
    }
    project = json.loads(request(
        url, f"{route}/timings?revision={project['revision']}", json.dumps(cues).encode(), content_type="application/json",
    ))
    project = action("input", script=spoken, intention="", confirmTiming=True)
    for name in ("plan/starter", "plan/approve"):
        project = action(name)
    mix = project["mix"]
    if mix["musicMode"] != "none" or mix["effects"] or mix["approved"]:
        raise RuntimeError("Approving a plan did not apply the pinned style's speech-only mix preference.")
    review = project["captionReview"]
    if not review or review["method"] != "timed-words-v1" or not review["flaggedCount"] or not review["issues"]:
        raise RuntimeError("The caption review missed the deliberately fast spoken fixture.")
    project = action("mix/prepare")
    project = action("mix/approve", reviewed=True)
    project = action("animation/prepare")
    project = action("qc")
    project = verify_checks(url, route, project, "animation", "animationChecks")
    project = action("candidate/accept")
    action("export")
    deadline = export_deadline(project)
    while time.monotonic() < deadline:
        output = json.loads(request(url, route))["export"]
        if output["status"] == "failed":
            raise RuntimeError(output["error"])
        if output["status"] == "complete":
            receipt = json.loads(request(url, output["receiptUrl"]))
            raw = request(url, output["captionsUrl"])
            blocks = raw.decode("utf-8").strip().split("\n\n")
            exported_words = [word for block in blocks for line in block.splitlines()[2:] for word in line.split()]
            if (output["captionReview"] != review or receipt["captionReview"] != review
                    or receipt["captionsHash"] != hashlib.sha256(raw).hexdigest()
                    or len(blocks) != review["cueCount"] or exported_words != spoken.split()):
                raise RuntimeError("The narrated delivery lost approved words or its caption review evidence.")
            print("Verified narrated SRT export and caption review.")
            return
        time.sleep(0.25)
    raise RuntimeError("The captioned export exceeded its time limit.")


def check_style_archive(url, style_id):
    scope = f"/api/styles/{style_id}"
    original = json.loads(request(url, scope))
    raw = request(url, f"{scope}/archive")
    with ZipFile(BytesIO(raw)) as archive:
        if archive.read("style.md").decode("utf-8") != original["guide"] or "manifest.json" not in archive.namelist():
            raise RuntimeError("The ZIP did not include its active Markdown guide and manifest.")
    imported = json.loads(request(url, "/api/styles/import", raw, content_type="application/zip"))
    if imported["id"] == style_id or imported["builtIn"] or any(
        imported[key] != original[key] for key in ("guide", "name", "referenceImages", "answers", "revision", "assetCount")
    ):
        raise RuntimeError("The imported ZIP did not reproduce its guide, references, revisions, and separate library.")
    original_library = json.loads(request(url, f"/api/library?styleId={style_id}"))
    imported_library = json.loads(request(url, f"/api/library?styleId={imported['id']}"))
    original_hashes = {v["hash"] for asset in original_library["assets"] for v in asset["versions"]}
    imported_urls = {v["hash"]: v["url"] for asset in imported_library["assets"] for v in asset["versions"]}
    if original_hashes != imported_urls.keys() or any(
        hashlib.sha256(request(url, path)).hexdigest() != digest for digest, path in imported_urls.items()
    ):
        raise RuntimeError("The imported library did not keep its original artwork bytes.")


def run_export(url, root, project, custom_style_id):
    route = f"/api/projects/{project['id']}"

    def action(name, **data):
        return json.loads(request(url, f"{route}/{name}", {"revision": project["revision"], **data}))

    scenes = project["plan"]["scenes"]
    artwork = "smoke-art"
    scenes[0].update(layout="illustration", artId=artwork, artBrief="A sample blue square.")
    project = action("plan", scenes=scenes)
    project = action("plan/approve")
    project = json.loads(request(
        url, f"{route}/assets/{artwork}?revision={project['revision']}&name=smoke-art.png",
        png((96, 96), "#386e9e"),
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
    warm = json.loads(request(url, "/api/library?styleId=warm-editorial"))
    custom = json.loads(request(url, f"/api/library?styleId={custom_style_id}"))
    published, = project["assets"]
    published_ids = {entry["id"] for entry in warm["assets"]}
    if published_ids != {published["libraryId"]} or custom["assets"]:
        raise RuntimeError("The published demo artwork did not stay in its Warm Editorial library.")
    check_library_isolation(url, root, project, published["libraryId"])
    check_style_archive(url, "warm-editorial")
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
    deadline = export_deadline(project)
    while time.monotonic() < deadline:
        project = json.loads(request(url, route))
        export = project["export"]
        if export["status"] == "complete":
            if export.get("verification") != "verified":
                raise RuntimeError("The completed delivery did not report file verification.")
            if request(url, export["url"])[4:8] != b"ftyp":
                raise RuntimeError("Export downloaded but lacked an MP4 file header.")
            receipt = json.loads(request(url, export["receiptUrl"]))
            media = export["media"]
            hash_at_review = receipt["qc"].get("buildHash")
            picture_check = media.get("visualVerification") or {}
            if (media.get("renderer") != "canvas" or not hash_at_review
                    or receipt["candidate"].get("buildHash") != hash_at_review
                    or picture_check.get("buildHash") != hash_at_review or not picture_check.get("frames")):
                raise RuntimeError("The packaged renderer did not verify encoded frames against the reviewed Canvas film.")
            if (media != receipt["media"] or media.get("encoderMode") != "auto" or not media.get("encoder")
                or media.get("hardwareEncoding") != (media["encoder"] != "libx264")
                or (not media["hardwareEncoding"] and not media.get("encoderFallback"))):
                raise RuntimeError("The default export did not identify its actual encoder and any fallback.")
            if receipt["candidate"]["mix"]["rendered"]["sha256"] != prepared["sha256"]:
                raise RuntimeError("The exported receipt did not bind the approved WAV.")
            style = receipt["candidate"].get("styleGuide", {})
            if (style.get("id"), style.get("revision")) != ("warm-editorial", project["styleSnapshot"]["revision"]):
                raise RuntimeError("The exported receipt did not retain the selected style revision.")
            for scope, key in (("assets", "assetChecks"), ("animation", "animationChecks")):
                checks = receipt["review"][scope]
                if set(checks) != {check["id"] for check in project[key]} or any(
                    check["status"] != "passed" or not check["proofHash"] for check in checks.values()
                ):
                    raise RuntimeError(f"The exported receipt did not bind the passed {scope} proofs.")
            return project
        if export["status"] == "failed":
            raise RuntimeError(export["error"])
        time.sleep(0.25)
    raise RuntimeError("The example export exceeded its time limit.")


def check_library_isolation(url, root, project, healthy_id):
    damaged_id = "f" * 32
    directory = root / "style-libraries" / "warm-editorial" / "entries"
    damaged = directory / f"{damaged_id}.json"
    damaged.write_bytes(b"broken library metadata")
    styles = json.loads(request(url, "/api/styles"))["styles"]
    warm = next(style for style in styles if style["id"] == "warm-editorial")
    if warm["assetCount"] != 1 or warm["libraryIssueCount"] != 1:
        raise RuntimeError("A damaged entry hid usable artwork from the style picker.")
    catalog = json.loads(request(url, "/api/library?styleId=warm-editorial"))
    if [asset["id"] for asset in catalog["assets"]] != [healthy_id] or [issue["id"] for issue in catalog["issues"]] != [damaged_id]:
        raise RuntimeError("The library failed to isolate its damaged entry.")
    request(url, f"/api/projects/{project['id']}")
    recovery = json.loads(request(url, "/api/library/quarantine", {"styleId": "warm-editorial", "entryId": damaged_id}))
    if damaged.exists() or Path(recovery["retainedPath"]).read_bytes() != b"broken library metadata":
        raise RuntimeError("Quarantine did not preserve the damaged entry’s original bytes.")


def check_export_recovery(url, root, project):
    route = f"/api/projects/{project['id']}"
    previous = project["export"]
    old_video = root / project["id"] / "exports" / previous["deliveryId"] / "video.mp4"
    old_video.write_bytes(b"broken")
    project = json.loads(request(url, route))
    if project["export"]["status"] != "invalid" or project["export"].get("url") or project.get("lastDelivery"):
        raise RuntimeError("A replaced MP4 was still presented as a completed delivery.")
    expect_conflict(url, previous["url"])
    request(url, "/api/settings", {"videoEncoder": "cpu"})
    request(url, f"{route}/export", {"revision": project["revision"], "rerender": True})
    deadline = export_deadline(project)
    while time.monotonic() < deadline:
        repaired = json.loads(request(url, route))["export"]
        if repaired["status"] == "failed":
            raise RuntimeError(repaired["error"])
        if repaired["status"] == "complete":
            if (
                repaired["verification"] != "verified" or repaired["deliveryId"] == previous["deliveryId"]
                or repaired["candidateHash"] != previous["candidateHash"] or repaired["media"]["encoder"] != "libx264"
                or request(url, repaired["url"])[4:8] != b"ftyp" or old_video.read_bytes() != b"broken"
            ):
                raise RuntimeError("A repaired render did not keep its approval, use the requested encoder, and create a new MP4.")
            return
        time.sleep(0.25)
    raise RuntimeError("The repair render exceeded its time limit.")


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
    if config["plannerAvailable"] or config["transcriberAvailable"] or config["imageAvailable"] or config["styleDraftAvailable"]:
        raise RuntimeError("An OpenAI key alone enabled an AI stage.")
    request(url, "/api/settings", {"anthropicKey": "test-local-anthropic-key"})
    config = json.loads(request(url, "/api/config"))
    if config["plannerProvider"] != "anthropic" or not config["plannerAvailable"]:
        raise RuntimeError("A Claude key alone must enable the default planner.")
    if not config["styleDraftAvailable"]:
        raise RuntimeError("A Claude key did not make guide drafting available.")
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


def smoke_environment(root):
    home = root / "test user"
    local = home / "AppData" / "Local"
    xdg = home / ".local" / "share"
    temp = root / "temp"
    temp.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("BACCHUS_") or key == "BACCHUS_CHROME"}
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONHOME", "PYTHONPATH"):
        env.pop(name, None)
    env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(local),
               APPDATA=str(home / "AppData" / "Roaming"), XDG_DATA_HOME=str(xdg),
               XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"),
               TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    search = [Path(item) for item in os.defpath.split(os.pathsep) if Path(item).is_absolute()]
    if os.name == "nt":
        windows = Path(os.environ["SystemRoot"])
        search.extend((windows / "System32", windows))
    for tool in ("ffmpeg", "ffprobe"):
        if found := shutil.which(tool):
            directory = Path(found).resolve().parent
            if not directory.is_relative_to(SOURCE_ROOT):
                search.append(directory)
    env["PATH"] = os.pathsep.join(str(path) for path in dict.fromkeys(search))
    if os.name == "nt":
        return env, local / "Bacchus"
    if sys.platform == "darwin":
        return env, home / "Library" / "Application Support" / "Bacchus"
    return env, xdg / "bacchus"


@contextmanager
def running(executable, env, cwd, workspace, *flags):
    command = [str(executable), "--no-browser", "--port", "0", *flags]
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = endpoint(workspace, process)
        yield url
        request(url, "/api/quit", {})
        if process.wait(15) != 0:
            raise RuntimeError("Bacchus returned a failure status after quitting.")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)


def verify_workspace(settings, active, preferred, default, override=False):
    info = settings["workspace"]
    expected = {"active": active, "preferred": preferred, "systemDefault": default}
    if info["override"] is not override or any(Path(info[key]) != value for key, value in expected.items()):
        raise RuntimeError(f"Unexpected workspace settings: {info!r}. Expected {expected} with override={override}.")


def check_storage(executable, env, cwd, root, default):
    chosen = root / "Selected workspace"
    override = root / "One-time workspace"
    denied = cwd / "should stay absent"
    with running(executable, env, cwd, default) as url:
        settings = json.loads(request(url, "/api/settings"))
        verify_workspace(settings, default, default, default)
        for path, data in (("/api/workspace", {"directory": str(denied)}),
                           ("/api/settings", {"apiLogDirectory": str(denied)})):
            try:
                request(url, path, data)
            except HTTPError as error:
                if not 400 <= error.code < 500:
                    raise
            else:
                raise RuntimeError(f"The packaged app accepted a Git destination for {path}.")
        after = json.loads(request(url, "/api/settings"))
        if after["workspace"] != settings["workspace"] or after["apiLogPath"] != settings["apiLogPath"] or denied.exists():
            raise RuntimeError("Rejecting a Git destination changed storage or preferences.")
        verify_workspace(json.loads(request(url, "/api/workspace", {"directory": str(chosen)})),
                         default, chosen, default)

    refused = subprocess.run([str(executable), "--no-browser", "--data", str(denied)],
                             cwd=cwd, env=env, capture_output=True, timeout=20)
    if refused.returncode == 0 or denied.exists():
        raise RuntimeError("Launching with a Git workspace did not refuse before writing.")

    with running(executable, env, cwd, chosen) as url:
        verify_workspace(json.loads(request(url, "/api/settings")), chosen, chosen, default)
        example = json.loads(request(url, "/api/demo", {}))
    with running(executable, env, cwd, override, "--data", str(override)) as url:
        verify_workspace(json.loads(request(url, "/api/settings")), override, chosen, default, override=True)
        if (override / example["id"]).exists():
            raise RuntimeError("The one-launch override unexpectedly copied the project.")
    with running(executable, env, cwd, chosen) as url:
        verify_workspace(json.loads(request(url, "/api/settings")), chosen, chosen, default)
        if json.loads(request(url, "/api/projects/" + example["id"]))["id"] != example["id"]:
            raise RuntimeError("The saved workspace did not reopen its project.")
        verify_workspace(json.loads(request(url, "/api/workspace", {"directory": ""})), chosen, default, default)
    print("Verified workspace selection, reopening, one-launch override, reset and Git destination rejection.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--render", action="store_true", help="Export the example through FFmpeg and FFprobe.")
    args = parser.parse_args()
    executable = args.executable.resolve()
    with TemporaryDirectory(prefix="bacchus-bundle-smoke-") as temp:
        sandbox = Path(temp).resolve()
        env, root = smoke_environment(sandbox)
        cwd = sandbox / "checkout"
        (cwd / ".git").mkdir(parents=True)
        check_storage(executable, env, cwd, sandbox, root)
        with running(executable, env, cwd, root) as url:
            verify_workspace(json.loads(request(url, "/api/settings")), root, root, root)
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
            timing = json.loads(request(url, "/api/timing-model"))
            if timing["ready"] or timing["existingDirectory"] or not timing["installation"]:
                raise RuntimeError("Fresh setup did not report that Parakeet model files were needed.")
            custom = check_styles(url)
            check_style_archive(url, custom["styleSnapshot"]["id"])
            project = json.loads(request(url, "/api/demo", {}))
            if not project["input"] or not project["plan"]["approved"]:
                raise RuntimeError("The packaged app could not create the demo project.")
            if args.render:
                check_style_mix_and_captions(url, custom)
                project = run_export(url, root, project, custom["styleSnapshot"]["id"])
                print(f"Verified default export encoder: {project['export']['media']['encoder']}")
                check_export_recovery(url, root, project)
        if set(cwd.iterdir()) != {cwd / ".git"} or any((cwd / ".git").iterdir()):
            raise RuntimeError("The packaged app left files in its Git launch directory.")
    print("Verified packaged startup, Claude-first setup, style guides and libraries, offline example, and clean shutdown"
          + (", including evidence gates and MP4 export." if args.render else "."))


if __name__ == "__main__":
    main()
