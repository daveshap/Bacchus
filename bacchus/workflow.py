import hashlib
import io
import json
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps, UnidentifiedImageError

from . import ENGINE_VERSION, api_log, audio, delivery, gates, local_timing, planning, providers, render, score, styles, timing, timing_models, visual_review
from .library import Library, LibraryError
from .settings import timing_model_directory, video_encoder_mode


MAX_ART_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 384 * 1024 * 1024
MAX_DURATION = 1800
MAX_FX_BYTES = 64 * 1024 * 1024
MAX_FX_DURATION = 60
MAX_EFFECTS = 12
MAX_ART_GENERATIONS = 24
MAX_TRANSCRIPTIONS = 3
MAX_PROJECT_ASSETS = 128
MAX_LIBRARY_CHOICES = 20
LIBRARY_STOP_WORDS = set("about across also and are can for from has have how into its more that the their them then there these this those was were what when where which who will with would you your".split())
STYLES = {
    "warm-editorial": "Warm editorial",
    "studio-ink": "Studio ink",
    "night-signal": "Night signal",
}
ART_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PROJECT_ID = re.compile(r"[0-9a-f]{32}")
DIGEST = re.compile(r"[0-9a-f]{64}")
PACK_PIN = {"name": planning.PACK["name"], "version": planning.PACK["version"], "hash": planning.PACK_HASH}
PREVIOUS_PACK_HASH = "4d632b120c258a10e2da5f3ee7d9a3263f7b6415c12b1d8b81ec8022d3a6cba8"


class WorkflowError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    try:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()
    except OSError:
        return None


def write_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    write_file(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise WorkflowError(f"Expected these fields: {', '.join(expected)}.")


def text_value(value, label, limit, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise WorkflowError(f"{label} must be text of at most {limit:,} characters.")
    if any(ord(char) < 32 and char not in "\r\n\t" or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise WorkflowError(f"{label} contains an unsupported character.")
    value = value.strip()
    if required and not value:
        raise WorkflowError(f"{label} is required.")
    return value


def art_id(value):
    if not isinstance(value, str) or len(value) > 64 or not ART_ID.fullmatch(value):
        raise WorkflowError("Artwork IDs must use lowercase letters, digits, and single hyphens.")
    return value


def file_name(value):
    return text_value(Path(value.replace("\\", "/")).name, "File name", 180, True)


def assets_by_id(project):
    return {asset["id"]: asset for asset in project["assets"]}


def asset_terms(text):
    return {word for word in re.findall(r"[^\W_]{3,}", text.casefold()) if word not in LIBRARY_STOP_WORDS}


def validate_scenes(project, raw_scenes):
    source = project["input"]
    if not source:
        raise WorkflowError("Lock the narration before planning.", 409)
    if not isinstance(raw_scenes, list) or not 1 <= len(raw_scenes) <= planning.MAX_SCENES:
        raise WorkflowError(f"The plan must contain 1 to {planning.MAX_SCENES} scenes.")
    max_line = len(source["lines"])
    expected = 1
    assets = assets_by_id(project)
    briefs = {}
    scenes = []
    for index, raw in enumerate(raw_scenes, start=1):
        keys = set(planning.SCENE_FIELDS)
        if isinstance(raw, dict) and "layers" not in raw:
            keys.discard("layers")
        fields(raw, keys)
        start, end = raw["startLine"], raw["endLine"]
        if type(start) is not int or type(end) is not int or start != expected or not start <= end <= max_line:
            raise WorkflowError(f"Scene {index} must start at line {expected} and end within lines {expected}–{max_line}.")
        expected = end + 1
        if raw["layout"] not in planning.LAYOUTS or raw["motion"] not in planning.MOTIONS:
            raise WorkflowError(f"Scene {index} uses an unsupported layout or motion.")
        title = text_value(raw["title"], f"Scene {index} title", 84, True)
        caption = text_value(raw["caption"], f"Scene {index} caption", 170)
        asset_id = text_value(raw["artId"], f"Scene {index} artwork ID", 64)
        brief = text_value(raw["artBrief"], f"Scene {index} artwork brief", 380)
        if not asset_id and (brief or raw["layout"] == "illustration"):
            raise WorkflowError(f"Scene {index} needs an artwork ID for that layout or brief.")
        scene = {**raw, "title": title, "caption": caption, "artId": asset_id, "artBrief": brief}
        try:
            planning.validate_layers(project, scene)
        except planning.PlannerError as error:
            raise WorkflowError(f"Scene {index}: {error}") from error
        if not scene.get("layers"):
            scene.pop("layers", None)
        for use in planning.scene_art(scene):
            asset_id, brief = use["artId"], use["artBrief"]
            art_id(asset_id)
            if asset_id not in assets and not brief:
                raise WorkflowError(f"Scene {index} needs an art brief for {asset_id}.")
            if brief and asset_id in briefs and briefs[asset_id] != brief:
                raise WorkflowError(f"Use one art brief for every occurrence of {asset_id}.")
            if brief:
                briefs[asset_id] = brief
        scenes.append(scene)
    if expected != max_line + 1:
        raise WorkflowError(f"The plan still needs to cover lines {expected}–{max_line}.")
    return scenes


def require_source_cues(project):
    source = project["input"]
    if not source:
        raise WorkflowError("Lock the reviewed narration cues before planning.", 409)
    silent_demo = project.get("demo") and not project["audio"] and source.get("timing") == "demo"
    if not silent_demo and (source.get("timing") != "source-cues" or not source.get("words")):
        raise WorkflowError("Unlock intake and approve recording-aligned word cues to continue.", 409)
    return source


def make_input(project, script, intention):
    script = text_value(script, "Narration", 100_000, True).replace("\r\n", "\n").replace("\r", "\n")
    intention = text_value(intention, "Intention", 2_000)
    segments = [line.strip() for line in script.split("\n") if line.strip()]
    if len(segments) > planning.MAX_LINES or any(len(line) > 500 for line in segments):
        raise WorkflowError(f"Use at most {planning.MAX_LINES} nonempty narration lines and 500 characters per line.")
    audio = project["audio"]
    transcript = project.get("transcript")
    if audio:
        if (
            not transcript or transcript["timing"] != "source-cues" or script != transcript["script"]
            or not transcript.get("words") or transcript.get("audioHash") != audio["hash"]
            or transcript.get("originalHash") != audio.get("originalHash")
        ):
            raise WorkflowError("Review recording-aligned word cues before locking this narration.", 409)
        source = {"script": script, "intention": intention, "lines": transcript["lines"], "words": transcript["words"],
                  "duration": audio["duration"], "timing": "source-cues", "timingHash": transcript["timingHash"]}
        return {**source, "hash": digest(encoded([source, audio["hash"], audio["originalHash"]]))}
    if not project.get("demo") or audio:
        raise WorkflowError("Import narration and approve its word cues first.", 409)
    # The silent sample uses authored three-second picture beats and no speech.
    lines = [{"id": index + 1, "text": line, "start": index * 3, "end": (index + 1) * 3}
             for index, line in enumerate(segments)]
    source = {"script": script, "intention": intention, "lines": lines, "words": [],
              "duration": len(lines) * 3, "timing": "demo"}
    return {**source, "hash": digest(encoded([source, None]))}


def pack_matches(project):
    if project["pack"] != PACK_PIN:
        raise WorkflowError("This project needs the pack version and files recorded when it was created.", 409)


def missing_art(project):
    if not project["plan"]:
        return []
    assets = assets_by_id(project)
    missing = {}
    for index, scene in enumerate(project["plan"]["scenes"], 1):
        for use in planning.scene_art(scene):
            asset_id = use["artId"]
            asset = assets.get(asset_id)
            if asset and not asset.get("issue"):
                continue
            item = missing.setdefault(asset_id, {"id": asset_id, "brief": use["artBrief"], "uses": []})
            item["uses"].append(f"Scene {index} · {scene['title']}" + (f" · {use['layerId']}" if use["layerId"] else ""))
            if asset and asset.get("issue"):
                item["issue"] = asset["issue"]
    return list(missing.values())


def art_prompt(missing, project):
    if not missing:
        return ""
    items = []
    for item in missing:
        line = f"{item['id']}\n{item['brief']}\nPlanned uses: {', '.join(item['uses'])}."
        if item.get("issue"):
            line += f"\nRequired correction: {item['issue']['reason']}"
        items.append(line)
    style = project.get("styleSnapshot") or {}
    name = style.get("name", STYLES[project["style"]])
    guide = "\n\nStyle guide:\n" + style["guide"] if style.get("guide") else ""
    references = "\nReference images: " + ", ".join(ref["name"] for ref in style["referenceImages"]) if style.get("referenceImages") else ""
    return (
        f"Create one separate image for each item below for the same animation. Visual style: {name}. "
        "Use a coherent visual style across the set. Keep requested isolated subjects on transparent "
        "backgrounds. Keep the artwork free of lettering. Return each image with its corresponding ID.\n\n"
        + "\n\n".join(items) + guide + references
    )


def new_mix(decision=None):
    return {"musicMode": decision, "music": None, "effects": [], "musicRegions": [], "approved": False}


def number(value, label, low, high):
    if type(value) not in (float, int) or not low <= value <= high:
        raise WorkflowError(f"{label} must be between {low} and {high}.")
    return round(value, 3)


def effect_id(value):
    if not PROJECT_ID.fullmatch(value):
        raise WorkflowError("Sound effect not found.", 404)
    return value


def mix_audio(mix):
    return ([mix["music"]] if mix["musicMode"] == "track" and mix["music"] else []) + mix["effects"]


def mix_record(project, folder, *, require_approval, verify=True):
    source, mix = require_source_cues(project), project["mix"]
    if mix["musicMode"] not in ("none", "track") or (mix["musicMode"] == "track" and not mix["music"]):
        raise WorkflowError("Choose no music or add a soundtrack.", 409)
    if require_approval and not mix["approved"]:
        raise WorkflowError("Approve the sound composition before running quality checks.", 409)
    if verify and not require_approval:
        for track in mix_audio(mix):
            if file_digest(folder / "audio" / f"{track['hash']}.wav") != track["hash"]:
                raise WorkflowError(f"Sound file {track['name']} is missing or no longer matches.", 409)
        if any(effect["start"] >= source["duration"] for effect in mix["effects"]):
            raise WorkflowError("Each sound effect must begin within the narration.", 409)
    record = {
        "musicMode": mix["musicMode"],
        "music": mix["music"] if mix["musicMode"] == "track" else None,
        "effects": mix["effects"],
        "musicRegions": mix.get("musicRegions", []) if mix["musicMode"] == "track" else [],
    }
    if require_approval:
        frozen = mix.get("rendered")
        if not frozen or frozen.get("recipeHash") != mix_key(project, record):
            raise WorkflowError("Prepare and approve the exact soundtrack to continue.", 409)
        if verify:
            try:
                render.verify_mix(folder, frozen, source["duration"])
            except render.RenderError as error:
                raise WorkflowError(str(error), 409) from error
        record["rendered"] = frozen
    return record


def mix_key(project, recipe):
    return digest(encoded([ENGINE_VERSION, project["input"]["hash"], recipe]))


def candidate(project, folder, verify=True, require_mix=True):
    pack_matches(project)
    source, plan = require_source_cues(project), project["plan"]
    if not plan or not plan["approved"]:
        raise WorkflowError("Approve the scene plan first.", 409)
    assets = assets_by_id(project)
    used = {}
    audio = project["audio"]
    if verify:
        if make_input(project, source["script"], source["intention"]) != source:
            raise WorkflowError("The locked narration does not match its recorded fingerprint.", 409)
        if digest(encoded(validate_scenes(project, plan["scenes"]))) != plan["hash"]:
            raise WorkflowError("The approved scene plan does not match its recorded fingerprint.", 409)
        if audio and file_digest(folder / "audio" / f"{audio['hash']}.wav") != audio["hash"]:
            raise WorkflowError("The locked narration recording is missing or no longer matches.", 409)
        if audio and audio.get("originalHash"):
            path = folder / "source-audio" / (audio["originalHash"] + audio["sourceExtension"])
            if file_digest(path) != audio["originalHash"]:
                raise WorkflowError("The original narration recording is missing or no longer matches.", 409)
        if source.get("timingHash") and file_digest(folder / "records" / "timing" / f"{source['timingHash']}.json") != source["timingHash"]:
            raise WorkflowError("The locked cue evidence is missing or no longer matches.", 409)
    for asset_id in dict.fromkeys(use["artId"] for scene in plan["scenes"] for use in planning.scene_art(scene)):
        asset = assets.get(asset_id)
        if not asset or not asset["approved"]:
            raise WorkflowError(f"Upload and approve the artwork for {asset_id}.", 409)
        if asset.get("issue"):
            raise WorkflowError(f"Resolve the open artwork issue for {asset_id} before continuing.", 409)
        if verify and file_digest(folder / "assets" / f"{asset['hash']}.png") != asset["hash"]:
            raise WorkflowError(f"The approved artwork for {asset_id} is missing or no longer matches.", 409)
        used[asset_id] = asset["hash"]
    record = {
        "engine": ENGINE_VERSION,
        "pack": project["pack"],
        "inputHash": source["hash"],
        "audioHash": audio["hash"] if audio else None,
        "originalAudioHash": audio.get("originalHash") if audio else None,
        "planHash": plan["hash"],
        "artwork": used,
        "style": project["style"],
        "mix": mix_record(project, folder, require_approval=True, verify=verify) if require_mix else None,
    }
    if style := project.get("styleSnapshot"):
        record["styleGuide"] = {"id": style["id"], "revision": style["revision"], "hash": digest(encoded(style))}
    return record


def reset_candidate(project):
    project.update(qc=None, accepted=None, export=None)
    if "review" in project:
        project["review"]["prepared"] = None


def set_plan(project, scenes, source):
    scenes = validate_scenes(project, scenes)
    plan = {"scenes": scenes, "hash": digest(encoded(scenes)), "source": source, "approved": False}
    if project["plan"] and project["plan"]["hash"] == plan["hash"]:
        return
    project["plan"] = plan
    reset_candidate(project)


def normalized_art(raw):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG") or getattr(image, "n_frames", 1) != 1:
                raise WorkflowError("Upload a single-frame PNG or JPEG.")
            if min(image.size) < 32 or max(image.size) > 6000 or image.width * image.height > 18_000_000:
                raise WorkflowError("Artwork must be 32 to 6,000 pixels per side and at most 18 megapixels.")
            image = ImageOps.exif_transpose(image).convert("RGBA")
            if not image.getchannel("A").getbbox():
                raise WorkflowError("Artwork is fully transparent.")
            image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
            image.info.clear()
            output = io.BytesIO()
            image.save(output, "PNG")
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise WorkflowError("The uploaded artwork could not be decoded.") from error


def srt_stamp(seconds):
    minutes, milliseconds = divmod(round(seconds * 1000), 60_000)
    hours, minutes = divmod(minutes, 60)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def subtitles(project):
    return "\n\n".join(
        f"{line['id']}\n{srt_stamp(line['start'])} --> {srt_stamp(line['end'])}\n{line['text']}"
        for line in project["input"]["lines"]
    ) + "\n"


class Studio:
    def __init__(self, root, library_root=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.timing_models = timing_models.TimingModels(self.root)
        self.styles = styles.StyleCatalog(self.root)
        self.library_root = self.root / "style-libraries"
        self.libraries = {}
        legacy_roots = [self.root / "library"]
        if library_root is not None:
            legacy_roots.append(Path(library_root).resolve())
        self.library_migration_error = None
        for existing in dict.fromkeys(legacy_roots):
            try:
                self._migrate_library(existing)
            except (LibraryError, WorkflowError, OSError) as error:
                self.library_migration_error = str(error)
        self.mix_lock = threading.Lock()
        self.active_style_drafts = 0
        self.jobs = set()
        self._recover_jobs()

    @property
    def library(self):
        return self.library_for("warm-editorial")

    def library_for(self, style_id):
        if not isinstance(style_id, str) or len(style_id) > 80 or not ART_ID.fullmatch(style_id):
            raise WorkflowError("Style library was not found.", 404)
        with self.lock:
            root = self.library_root.resolve()
            directory = self.library_root / style_id
            if not root.is_relative_to(self.root) or directory.resolve() != root / style_id:
                raise WorkflowError("The style library must stay in its own Bacchus data folder.", 409)
            if style_id not in self.libraries:
                self.libraries[style_id] = Library(directory)
            return self.libraries[style_id]

    def library_catalog(self, style_id):
        try:
            return {**self.library_for(style_id).catalog(), "storageError": None}
        except (LibraryError, WorkflowError) as error:
            return {"assets": [], "issues": [], "storageError": str(error)}
        except (OSError, RuntimeError):
            return {"assets": [], "issues": [], "storageError": "This style’s artwork library could not be opened."}

    @staticmethod
    def project_style_id(project):
        return (project.get("styleSnapshot") or {}).get("id", project["style"])

    def _migrate_library(self, source):
        if not (source / "entries").is_dir():
            return
        if not self.library_root.resolve().is_relative_to(self.root):
            raise WorkflowError("Style libraries must stay inside Bacchus data.", 409)
        marker = self.library_root / f".legacy-{digest(str(source).encode('utf-8'))}.json"
        if marker.is_file() or source.is_relative_to(self.library_root):
            return
        Library(source).copy_by_style({style_id: self.library_for(style_id) for style_id in STYLES})
        write_json(marker, {"source": str(source), "copiedAt": now()})

    def style_view(self, style, *, include_count=True):
        view = {**style, "referenceImages": [{**ref, "url": f"/api/style-references/{ref['sha256']}"}
                                             for ref in style["referenceImages"]]}
        if include_count:
            catalog = self.library_catalog(style["id"])
            view["libraryIssueCount"] = len(catalog["issues"])
            view["libraryWarning"] = catalog["storageError"]
            view["assetCount"] = sum(any(version["qualified"] and not version["issue"] for version in entry["versions"])
                                     for entry in catalog["assets"])
        return view

    def _style_reference_bytes(self, project):
        style = project.get("styleSnapshot") or {}
        return tuple(self.styles.reference(reference["sha256"]).read_bytes()
                     for reference in style.get("referenceImages", []))

    def _recover_jobs(self):
        for path in self.root.glob("*/project.json"):
            if not PROJECT_ID.fullmatch(path.parent.name):
                continue
            try:
                project = self._read(path.parent.name)
            except WorkflowError:
                continue
            changed = bool(self._catalog_legacy_deliveries(project) or project["busy"])
            if project["busy"]:
                stage = project["busy"]
                project["busy"] = None
                if stage == "rendering":
                    project["export"]["status"] = "failed"
                    project["export"]["error"] = "Bacchus closed before the render completed."
                elif project["plannerRuns"]:
                    if stage == "planning":
                        project["plannerRuns"][-1].update(status="interrupted", finishedAt=now())
                for name in ("transcriptionRuns", "artRuns"):
                    if project[name] and project[name][-1]["status"] == "running":
                        project[name][-1].update(status="interrupted", finishedAt=now())
            if changed:
                self._save(project)

    def _catalog_legacy_deliveries(self, project):
        if project.get("legacyDeliveriesIndexed"):
            return False
        folder = self._folder(project["id"])
        candidates = delivery.legacy_receipts(folder)
        if not candidates:
            return False
        active = project.get("export") or {}
        active_key = active.get("candidateHash") if active.get("status") == "complete" and "deliveryId" not in active else None
        known = {item["deliveryId"]: item for item in project.get("deliveries", [])}
        for key in candidates:
            if key in known:
                continue
            prior = active if key == active_key else {"candidateHash": key, "status": "complete"}
            if record := delivery.legacy_record(folder, prior):
                known[key] = record
        if active_key in known:
            project["export"] = known[active_key]
        project["deliveries"] = sorted(known.values(), key=lambda item: item.get("finishedAt") or "")
        project["legacyDeliveriesIndexed"] = True
        return True

    def _folder(self, project_id):
        if not PROJECT_ID.fullmatch(project_id):
            raise WorkflowError("Project not found.", 404)
        return self.root / project_id

    def _read(self, project_id):
        try:
            project = json.loads((self._folder(project_id) / "project.json").read_bytes())
            if "mix" not in project:
                # Backfill the current format for saved early projects.
                project.update(style="warm-editorial", mix=new_mix("none"), demo=False,
                               transcript=None, transcriptionRuns=[], artRuns=[])
                if not project["busy"]:
                    reset_candidate(project)
            project.pop("legacy", None)
            folder = self._folder(project_id)
            if project.get("pack", {}).get("hash") == PREVIOUS_PACK_HASH:
                project["pack"] = PACK_PIN
                if not project["busy"] and (project["export"] or {}).get("status") != "complete":
                    reset_candidate(project)
                    if project.get("demo") and not project["audio"] and project["input"]:
                        source = project["input"]
                        project["input"] = make_input(project, source["script"], source["intention"])
                transcript = project.get("transcript")
                recording = project["audio"]
                if transcript and transcript.get("timingHash") and recording and recording.get("originalHash"):
                    key = transcript["timingHash"]
                    try:
                        raw = (folder / "records" / "timing" / f"{key}.json").read_bytes()
                        if digest(raw) == key:
                            records = timing.import_cues(raw, recording["originalHash"], recording["duration"])
                            project["transcript"] = {**transcript, **records}
                    except (OSError, timing.TimingError):
                        pass
            if "review" not in project:
                if not project["busy"] and (project["export"] or {}).get("status") != "complete":
                    reset_candidate(project)
                project["review"] = gates.new_review()
            return project
        except FileNotFoundError as error:
            raise WorkflowError("Project not found.", 404) from error
        except (OSError, ValueError) as error:
            raise WorkflowError("The project state could not be read.", 500) from error

    def _save(self, project):
        project["updatedAt"] = now()
        project["revision"] += 1
        folder = self._folder(project["id"])
        source = project["input"]
        if source:
            path = folder / "records" / "inputs" / f"{source['hash']}.json"
            if not path.exists():
                write_json(path, {"input": source, "audio": project["audio"]})
        plan = project["plan"]
        if plan:
            path = folder / "records" / "plans" / f"{plan['hash']}.json"
            if not path.exists():
                write_json(path, plan["scenes"])
        write_json(folder / "project.json", project)

    def _view(self, project):
        view = deepcopy(project)
        project_id = view["id"]
        base = f"/api/projects/{project_id}"
        warnings = self._apply_library_issues(view)
        for asset in view["assets"]:
            asset["url"] = f"{base}/media/art/{asset['hash']}"
            if asset["id"] in warnings:
                asset["libraryWarning"] = warnings[asset["id"]]
        if view["audio"]:
            view["audio"]["url"] = f"{base}/media/audio/{view['audio']['hash']}"
        mix = view["mix"]
        if mix.get("rendered"):
            mix["rendered"]["url"] = f"{base}/mix/preview"
        for track in ([mix["music"]] if mix["music"] else []) + mix["effects"]:
            track["url"] = f"{base}/media/audio/{track['hash']}"
        view["missing"] = missing_art(view)
        view["artPrompt"] = art_prompt(view["missing"], project)
        view["accepted"] = bool(project["accepted"])
        view["needsExactTiming"] = bool(project["input"] and not project["demo"]
                                       and (project["input"].get("timing") != "source-cues" or not project["input"].get("words")))
        folder = self._folder(project_id)
        view["assetChecks"] = gates.view(view, folder, "assets")
        view.pop("review")
        for asset in view["assets"]:
            uses = [check for check in view["assetChecks"] if check["assetId"] == asset["id"]]
            asset["publishEligible"] = bool(uses) and not (asset.get("issue") or asset.get("libraryWarning")) and all(
                check["status"] == "passed" for check in uses
            )
        view["assetReviewReady"] = (not view["missing"] and bool(project["plan"] and project["plan"]["approved"])) and all(
            check["status"] == "passed" for check in view["assetChecks"]
        )
        view["animationPrepared"] = bool(project["review"]["prepared"]) and view["assetReviewReady"]
        view["animationChecks"] = gates.view(project, folder, "animation") if view["animationPrepared"] else []
        view["animationReviewReady"] = (
            (project["qc"] or {}).get("status") == "passed"
            and bool(view["animationChecks"])
            and all(check["status"] == "passed" for check in view["animationChecks"])
        )
        chosen_style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
        view["styleSnapshot"] = self.style_view(chosen_style, include_count=False)
        planner_runs = view.pop("plannerRuns")
        view["plannerCalls"] = len(planner_runs)
        view["plannerLastRun"] = planner_runs[-1] if planner_runs else None
        transcription_runs = view.pop("transcriptionRuns")
        view["transcriptionCalls"] = len(transcription_runs)
        view["transcriptionLastRun"] = transcription_runs[-1] if transcription_runs else None
        art_runs = view.pop("artRuns")
        view["artGenerations"] = len(art_runs)
        view["requiresAudio"] = not project["demo"]
        view.pop("approvals")
        view.pop("deliveries", None)
        view.pop("legacyDeliveriesIndexed", None)
        current = self._delivery_view(folder, base, project["export"])
        view["export"] = current
        view["lastDelivery"] = current if current and current["status"] == "complete" else None
        if view["lastDelivery"] is None:
            for record in reversed(project.get("deliveries", [])):
                if current and record["deliveryId"] == current.get("deliveryId"):
                    continue
                previous = self._delivery_view(folder, base, record)
                if previous["status"] == "complete":
                    view["lastDelivery"] = previous
                    break
        return view

    @staticmethod
    def _delivery_view(folder, base, record):
        if record is None:
            return None
        output = {key: value for key, value in record.items() if key != "fileHashes"}
        if output["status"] != "complete":
            return output
        output.setdefault("deliveryId", record.get("candidateHash"))
        try:
            delivery.verify(folder, record, full=False)
        except delivery.DeliveryError as error:
            output.update(status="invalid", verification="corrupt", integrityError=str(error))
        else:
            key = record["deliveryId"]
            output.update(verification="verified", url=f"{base}/media/video/{key}",
                          captionsUrl=f"{base}/media/captions/{key}", receiptUrl=f"{base}/media/receipt/{key}")
            if output.get("legacy"):
                output["verificationNote"] = "The earlier receipt recorded the MP4 hash. Bacchus fingerprinted its captions and receipt on upgrade."
        return output

    def list_projects(self):
        with self.lock:
            projects = []
            for path in self.root.glob("*/project.json"):
                if PROJECT_ID.fullmatch(path.parent.name):
                    try:
                        project = self._read(path.parent.name)
                    except WorkflowError:
                        continue
                    chosen = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
                    projects.append({**{key: project[key] for key in ("id", "title", "updatedAt")},
                                     "styleId": chosen["id"], "styleName": chosen["name"]})
            return {"projects": sorted(projects, key=lambda item: item["updatedAt"], reverse=True)}

    def providers_in_use(self):
        with self.lock:
            if self.active_style_drafts:
                return True
            for path in self.root.glob("*/project.json"):
                if not PROJECT_ID.fullmatch(path.parent.name):
                    continue
                try:
                    if self._read(path.parent.name)["busy"] in (
                        "planning", "transcribing", "generating artwork", "reviewing proof",
                        "recognizing word cues", "preparing soundtrack",
                    ):
                        return True
                except WorkflowError:
                    continue
            return False

    def create(self, title, demo=False, *, style_id="warm-editorial", style_revision=None):
        chosen = self.styles.get(style_id, style_revision)
        project = {
            "id": uuid.uuid4().hex, "title": text_value(title, "Project title", 120, True),
            "revision": 0, "createdAt": now(), "pack": PACK_PIN, "input": None, "audio": None,
            "plan": None, "assets": [], "qc": None, "accepted": None, "export": None, "busy": None,
            "plannerRuns": [], "approvals": [],
            "style": chosen["visualPreset"], "styleSnapshot": chosen, "mix": new_mix("none") if demo else new_mix(),
            "demo": demo, "transcript": None, "transcriptionRuns": [], "artRuns": [],
            "review": gates.new_review(),
        }
        if demo:
            script = (
                "Every street changes character when the sun goes down.\n"
                "Light makes small islands of space on the sidewalk.\n"
                "Between the islands, distance feels wider than it is.\n"
                "A city at night is a different map of the same streets."
            )
            project["input"] = make_input(project, script, "A quiet editorial essay about the feeling of a night walk.")
            demo_cards = [
                ("title", "fade", "The city after dark", "A short study in light and distance"),
                ("editorial", "push-in", "Islands of light", "A lamp gives the sidewalk a new edge."),
                ("editorial", "still", "The space between", "Distance begins to feel different."),
                ("title", "fade", "A different map", "The same streets, seen at another hour."),
            ]
            scenes = [
                {"startLine": index, "endLine": index, "layout": layout, "motion": motion,
                 "title": title, "caption": caption, "artId": "", "artBrief": ""}
                for index, (layout, motion, title, caption) in enumerate(demo_cards, start=1)
            ]
            set_plan(project, scenes, "demo")
            self._approve_plan(project)
        with self.lock:
            self._save(project)
            return self._view(project)

    def get(self, project_id):
        with self.lock:
            return self._view(self._read(project_id))

    def _edit(self, project_id, revision):
        project = self._read(project_id)
        if type(revision) is not int or revision != project["revision"]:
            raise WorkflowError("This project changed. Refresh it and try again.", 409)
        if project["busy"]:
            raise WorkflowError(f"This project is currently {project['busy']}.", 409)
        pack_matches(project)
        self._apply_library_issues(project)
        return project

    def _apply_library_issues(self, project):
        warnings = {}
        for asset in project["assets"]:
            if asset.get("libraryId") and not asset.get("issue"):
                try:
                    library = self.library_for(self.project_style_id(project))
                    issue = library.issue(asset["libraryId"], asset["libraryVersion"])
                except (LibraryError, WorkflowError) as error:
                    warnings[asset["id"]] = str(error)
                    continue
                except (OSError, RuntimeError):
                    warnings[asset["id"]] = "Bacchus could not read this artwork’s library record."
                    continue
                if issue:
                    asset["issue"] = issue
        return warnings

    @staticmethod
    def _library_contains_pin(library, asset):
        if not asset.get("libraryId"):
            return False
        try:
            version = library.get(asset["libraryId"], asset["libraryVersion"])
        except LibraryError as error:
            if error.status == 404:
                return False
            raise
        return version["hash"] == asset["hash"]

    def _approve_plan(self, project):
        require_source_cues(project)
        plan = project["plan"]
        if not plan:
            raise WorkflowError("Save a scene plan first.", 409)
        if not plan["approved"]:
            plan["approved"] = True
            project["approvals"].append({"kind": "plan", "hash": plan["hash"], "at": now()})
            style = project.get("styleSnapshot")
            if style and project["mix"]["musicMode"] is None and not project["mix"]["effects"]:
                folder = self._folder(project["id"])
                answers = style["answers"]
                if answers["music"]:
                    self._preset_mix(project, folder, answers["musicPreset"])
                else:
                    mix = new_mix("none")
                    if answers["soundEffects"]:
                        mix["effects"] = self._transition_accents(project, folder)
                    self._update_mix(project, mix)

    def change(self, project_id, action, data):
        params = {"input": ("script", "intention"), "plan": ("scenes",)}.get(action, ())
        if action == "input" and isinstance(data, dict) and "style" in data:
            params += ("style",)
        if action == "input" and isinstance(data, dict) and "confirmTiming" in data:
            params += ("confirmTiming",)
        if action == "mix":
            params = ("musicMode", "musicGainDb", "effects")
            if isinstance(data, dict) and "musicRegions" in data:
                params += ("musicRegions",)
        if action == "mix/approve":
            params = ("reviewed",)
        if action == "mix/preset":
            params = ("preset",)
        if action == "export" and isinstance(data, dict) and "rerender" in data:
            params = ("rerender",)
        fields(data, ("revision", *params))
        if action == "plan/generate":
            return self._generate(project_id, data["revision"])
        if action == "transcribe":
            return self._transcribe(project_id, data["revision"])
        if action == "mix/prepare":
            return self._prepare_mix(project_id, data["revision"])
        if action == "export":
            rerender = data.get("rerender", False)
            if type(rerender) is not bool:
                raise WorkflowError("Choose whether to render a new delivery.")
            return self._queue_export(project_id, data["revision"], rerender=rerender)
        with self.lock:
            project = self._edit(project_id, data["revision"])
            folder = self._folder(project_id)
            if action == "input":
                if project["input"]:
                    raise WorkflowError("Unlock the narration before replacing it.", 409)
                if not project["audio"] and not project["demo"]:
                    raise WorkflowError("Import the narration recording before locking intake.", 409)
                if project["audio"] and data.get("confirmTiming") is not True:
                    raise WorkflowError("Review and confirm the recording-aligned cue sheet first.", 409)
                if project["audio"]:
                    recording = self._timing_recording(project)
                    if file_digest(folder / "audio" / f"{recording['hash']}.wav") != recording["hash"]:
                        raise WorkflowError("The narration WAV is missing or has changed.", 409)
                    timing_hash = (project.get("transcript") or {}).get("timingHash")
                    if not timing_hash or file_digest(folder / "records" / "timing" / f"{timing_hash}.json") != timing_hash:
                        raise WorkflowError("The recording-aligned cue evidence is missing or has changed.", 409)
                style = data.get("style", project["style"])
                if not isinstance(style, str) or style not in STYLES:
                    raise WorkflowError("Choose a supported visual style.")
                if style != project["style"] and (project.get("styleSnapshot") or project["assets"]):
                    raise WorkflowError("Choose the style when creating a new project.", 409)
                project["style"] = style
                project["input"] = make_input(project, data["script"], data["intention"])
                if project["audio"]:
                    project["approvals"].append({"kind": "narration-cues", "hash": timing_hash, "at": now()})
            elif action == "input/unlock":
                project.update(input=None, plan=None)
                project["mix"]["approved"] = False
                project["mix"].pop("rendered", None)
                project["mix"]["musicRegions"] = []
                reset_candidate(project)
            elif action == "plan":
                require_source_cues(project)
                set_plan(project, data["scenes"], "manual")
            elif action == "plan/starter":
                require_source_cues(project)
                set_plan(project, planning.starter_plan(project["input"]["lines"]), "starter")
            elif action == "plan/approve":
                self._approve_plan(project)
            elif action == "mix":
                self._edit_mix(project, data)
            elif action == "mix/approve":
                if data["reviewed"] is not True:
                    raise WorkflowError("Review and confirm the prepared soundtrack before approving it.", 409)
                already = project["mix"]["approved"]
                project["mix"]["approved"] = True
                record = mix_record(project, folder, require_approval=True)
                if not already:
                    project["approvals"].append({"kind": "mix", "hash": record["rendered"]["sha256"], "at": now()})
            elif action == "mix/preset":
                self._preset_mix(project, folder, data["preset"])
            elif action == "assets/checks":
                candidate(project, folder, require_mix=False)
                gates.run(project, folder, "assets", write_file)
                if project["review"]["prepared"]:
                    try:
                        self._require_review(project, folder, "assets")
                    except WorkflowError:
                        reset_candidate(project)
            elif action == "animation/prepare":
                record = candidate(project, folder)
                self._require_review(project, folder, "assets")
                project["review"]["prepared"] = digest(encoded(record))
            elif action == "qc":
                self._run_qc(project, folder)
            elif action == "candidate/accept":
                record = self._require_prepared(project, folder)
                candidate_hash = self._require_animation_review(project, folder, record)
                project["accepted"] = {"hash": candidate_hash, "at": now()}
                project["approvals"].append({"kind": "candidate", **project["accepted"]})
            else:
                raise WorkflowError("Action not found.", 404)
            self._save(project)
            return self._view(project)

    def upload_audio(self, project_id, revision, name, raw):
        name = file_name(name)
        try:
            normalized, duration = audio.normalize_audio(raw, name, MAX_DURATION, voice=False)
        except audio.AudioError as error:
            raise WorkflowError(str(error)) from error
        content_hash = digest(normalized)
        original_hash = digest(raw)
        extension = Path(name).suffix.lower()
        with self.lock:
            project = self._edit(project_id, revision)
            if project["input"]:
                raise WorkflowError("Unlock narration before changing the recording.", 409)
            folder = self._folder(project_id)
            write_file(folder / "source-audio" / (original_hash + extension), raw)
            write_file(folder / "audio" / f"{content_hash}.wav", normalized)
            project["audio"] = {"name": name, "hash": content_hash, "duration": duration,
                                "originalHash": original_hash, "sourceExtension": extension}
            if project["transcript"] and project["transcript"].get("originalHash") != original_hash:
                project["transcript"] = None
            self._save(project)
            return self._view(project)

    def remove_audio(self, project_id, data):
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            if project["input"]:
                raise WorkflowError("Unlock narration before removing the recording.", 409)
            project["audio"] = None
            project["transcript"] = None
            self._save(project)
            return self._view(project)

    def _store_track(self, folder, name, raw, duration, gain):
        content_hash = digest(raw)
        path = folder / "audio" / f"{content_hash}.wav"
        if file_digest(path) != content_hash:
            write_file(path, raw)
        return {"name": name, "hash": content_hash, "duration": duration, "gainDb": gain}

    def _update_mix(self, project, mix):
        if mix != project["mix"]:
            mix["approved"] = False
            mix.pop("rendered", None)
            project["mix"] = mix
            reset_candidate(project)

    def _edit_mix(self, project, data):
        source = project["input"]
        if not source:
            raise WorkflowError("Lock the narration before composing audio.", 409)
        mix = deepcopy(project["mix"])
        mode = data["musicMode"]
        if mode not in ("none", "track") or mode == "track" and not mix["music"]:
            raise WorkflowError("Choose no music or import a soundtrack.")
        gain = number(data["musicGainDb"], "Music gain", -48, 0)
        if mix["music"]:
            mix["music"]["gainDb"] = gain
        edits = data["effects"]
        if not isinstance(edits, list) or len(edits) != len(mix["effects"]):
            raise WorkflowError("Refresh the sound effects before saving the mix.", 409)
        updates = {}
        for item in edits:
            fields(item, ("id", "start", "gainDb"))
            if not isinstance(item["id"], str):
                raise WorkflowError("A sound effect ID is invalid.")
            updates[item["id"]] = {
                "start": number(item["start"], "Effect start", 0, max(0, source["duration"] - 0.001)),
                "gainDb": number(item["gainDb"], "Effect gain", -48, 0),
            }
        if set(updates) != {effect["id"] for effect in mix["effects"]}:
            raise WorkflowError("The sound effects changed. Refresh and try again.", 409)
        mix["effects"] = [{**effect, **updates[effect["id"]]} for effect in mix["effects"]]
        mix["musicMode"] = mode
        try:
            mix["musicRegions"] = render.validate_music_regions(project, data.get("musicRegions", mix.get("musicRegions", [])))
        except render.RenderError as error:
            raise WorkflowError(str(error)) from error
        self._update_mix(project, mix)

    def _preset_mix(self, project, folder, preset):
        if not isinstance(preset, str) or preset not in score.PRESETS:
            raise WorkflowError("Choose the Ambient or Pulse soundtrack.")
        if not project["input"] or not project["plan"] or not project["plan"]["approved"]:
            raise WorkflowError("Approve the scene plan before creating a soundtrack.", 409)
        raw_music, music_duration = score.soundtrack(preset)
        music = self._store_track(folder, score.PRESETS[preset], raw_music, music_duration, -16)
        style = project.get("styleSnapshot")
        effects = self._transition_accents(project, folder) if not style or style["answers"]["soundEffects"] else []
        self._update_mix(project, {"musicMode": "track", "music": music, "effects": effects,
                                   "musicRegions": [], "approved": False})

    def _transition_accents(self, project, folder):
        effects = []
        accents = project["plan"]["scenes"][1:MAX_EFFECTS + 1]
        if accents:
            raw_accent, accent_duration = score.accent()
            track = self._store_track(folder, "Transition accent", raw_accent, accent_duration, -22)
            for scene in accents:
                start = project["input"]["lines"][scene["startLine"] - 1]["start"]
                effects.append({**track, "id": uuid.uuid4().hex, "start": start})
        return effects

    def upload_mix(self, project_id, revision, kind, name, raw):
        name = file_name(name)
        if kind not in ("music", "effects"):
            raise WorkflowError("Sound track type not found.", 404)
        limit = MAX_DURATION if kind == "music" else MAX_FX_DURATION
        try:
            normalized, duration = audio.normalize_audio(raw, name, limit, voice=False)
        except audio.AudioError as error:
            raise WorkflowError(str(error)) from error
        with self.lock:
            project = self._edit(project_id, revision)
            if not project["input"]:
                raise WorkflowError("Lock the narration before composing audio.", 409)
            folder = self._folder(project_id)
            mix = deepcopy(project["mix"])
            if kind == "music":
                mix.update(musicMode="track", music=self._store_track(folder, name, normalized, duration, -24))
            else:
                if len(mix["effects"]) >= MAX_EFFECTS:
                    raise WorkflowError(f"Use at most {MAX_EFFECTS} sound effects.")
                track = self._store_track(folder, name, normalized, duration, -18)
                mix["effects"].append({**track, "id": uuid.uuid4().hex, "start": 0.0})
            self._update_mix(project, mix)
            self._save(project)
            return self._view(project)

    def remove_effect(self, project_id, identifier, data):
        effect_id(identifier)
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            mix = deepcopy(project["mix"])
            matching = [effect for effect in mix["effects"] if effect["id"] != identifier]
            if len(matching) == len(mix["effects"]):
                raise WorkflowError("Sound effect not found.", 404)
            mix["effects"] = matching
            self._update_mix(project, mix)
            self._save(project)
            return self._view(project)

    def mix_preview(self, project_id):
        with self.lock:
            project = self._read(project_id)
            folder = self._folder(project_id)
            source = project["input"]
            result = project["mix"].get("rendered")
            if not source or not result or not DIGEST.fullmatch(result.get("sha256", "")):
                raise WorkflowError("Prepare and measure the soundtrack before listening.", 409)
            path = folder / "mixes" / f"{result['sha256']}.wav"
            try:
                render.verify_mix(folder, result, source["duration"])
            except render.RenderError as error:
                raise WorkflowError(str(error), 409) from error
            return path

    def _prepare_mix(self, project_id, revision):
        with self.lock:
            project = self._edit(project_id, revision)
            folder = self._folder(project_id)
            recipe = mix_record(project, folder, require_approval=False)
            if project["audio"] and file_digest(folder / "audio" / f"{project['audio']['hash']}.wav") != project["audio"]["hash"]:
                raise WorkflowError("The narration changed before soundtrack preparation.", 409)
            key = mix_key(project, recipe)
            previous = project["mix"].get("rendered")
            if previous and previous.get("recipeHash") == key:
                try:
                    render.verify_mix(folder, previous, project["input"]["duration"])
                    return self._view(project)
                except render.RenderError:
                    pass
            project["busy"] = "preparing soundtrack"
            self._save(project)
        directory = folder / "mixes"
        temporary = directory / f".{uuid.uuid4().hex}.wav"
        try:
            directory.mkdir(exist_ok=True)
            result = render.export_mix(project, folder, temporary)
            if mix_record(project, folder, require_approval=False) != recipe or (
                project["audio"] and file_digest(folder / "audio" / f"{project['audio']['hash']}.wav") != project["audio"]["hash"]
            ):
                raise WorkflowError("An audio source changed during preparation.", 409)
            output = directory / f"{result['sha256']}.wav"
            if not output.exists() or file_digest(output) != result["sha256"]:
                temporary.replace(output)
            result["recipeHash"] = key
            render.verify_mix(folder, result, project["input"]["duration"])
            project["mix"].update(rendered=result, approved=False)
            reset_candidate(project)
        except render.RenderError as error:
            raise WorkflowError(str(error), 422) from error
        finally:
            temporary.unlink(missing_ok=True)
            with self.lock:
                project["busy"] = None
                self._save(project)
        return self._view(project)

    def _transcribe(self, project_id, revision):
        with self.lock:
            project = self._edit(project_id, revision)
            if project["input"] or not project["audio"]:
                raise WorkflowError("Import a recording and leave intake unlocked before transcribing.", 409)
            if not audio.transcriber_available():
                raise WorkflowError("Enable OpenAI transcription and add an OpenAI key in Settings to transcribe here.", 409)
            if len(project["transcriptionRuns"]) >= MAX_TRANSCRIPTIONS:
                raise WorkflowError("This project reached its transcription request limit.", 409)
            recording_hash = project["audio"]["hash"]
            path = self._folder(project_id) / "audio" / f"{recording_hash}.wav"
            if file_digest(path) != recording_hash:
                raise WorkflowError("The source recording is missing or no longer matches.", 409)
            run = {"model": audio.transcription_model(), "audioHash": recording_hash,
                   "status": "running", "startedAt": now()}
            project["transcriptionRuns"].append(run)
            project["busy"] = "transcribing"
            self._save(project)
        error = None
        try:
            with api_log.scope(project_id, "transcription"):
                result = audio.transcribe(path)
            script = text_value(result["script"], "Transcript", 100_000, True)
            if (project.get("transcript") or {}).get("timing") != "source-cues":
                project["transcript"] = {"script": script, "model": result["model"],
                                         "timing": "text-draft", "audioHash": recording_hash,
                                         "originalHash": project["audio"].get("originalHash")}
            run.update(status="complete", requestId=result.get("requestId"), usage=result.get("usage"))
        except (audio.AudioError, WorkflowError) as failure:
            error = str(failure)
        except Exception:
            error = "Bacchus could not complete the transcription."
            raise
        finally:
            self._finish_provider(project, run, error)
        if error:
            raise WorkflowError(error, 422)
        return self._view(project)

    def import_timings(self, project_id, revision, raw):
        with self.lock:
            project = self._edit(project_id, revision)
            self._record_timing(project, raw)
            self._save(project)
            return self._view(project)

    def _timing_recording(self, project):
        recording = project["audio"]
        if project["input"] or not recording or not recording.get("originalHash"):
            raise WorkflowError("Import the original narration and leave intake unlocked before working on cues.", 409)
        folder = self._folder(project["id"])
        original = folder / "source-audio" / (recording["originalHash"] + recording["sourceExtension"])
        if file_digest(original) != recording["originalHash"]:
            raise WorkflowError("The original narration file is missing or has changed.", 409)
        return recording

    def _record_timing(self, project, raw):
        recording = self._timing_recording(project)
        try:
            transcript = timing.import_cues(raw, recording["originalHash"], recording["duration"])
        except timing.TimingError as error:
            raise WorkflowError(str(error), 422) from error
        content_hash = digest(raw)
        write_file(self._folder(project["id"]) / "records" / "timing" / f"{content_hash}.json", raw)
        project["transcript"] = {**transcript, "originalHash": recording["originalHash"],
                                 "audioHash": recording["hash"], "timingHash": content_hash}

    def derive_timings(self, project_id, data):
        fields(data, ("revision", "modelDirectory") if isinstance(data, dict) and "modelDirectory" in data else ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            recording = self._timing_recording(project)
            path = self._folder(project_id) / "audio" / f"{recording['hash']}.wav"
            if file_digest(path) != recording["hash"]:
                raise WorkflowError("The narration WAV is missing or has changed.", 409)
            project["busy"] = "recognizing word cues"
            self._save(project)
        try:
            model = data.get("modelDirectory")
            if "modelDirectory" not in data:
                model = str(self.timing_models.resolve(timing_model_directory() or None))
            result = local_timing.derive(path, model)
            if file_digest(path) != recording["hash"]:
                raise WorkflowError("The narration WAV changed during cue recognition.", 409)
            raw = encoded({"audio": {"sha256": recording["originalHash"]},
                           "duration": recording["duration"], **result})
            self._record_timing(project, raw)
        except timing.TimingError as error:
            raise WorkflowError(str(error), 422) from error
        finally:
            with self.lock:
                project["busy"] = None
                self._save(project)
        return self._view(project)

    def edit_timings(self, project_id, data):
        fields(data, ("revision", "words"))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            recording = self._timing_recording(project)
            transcript = project.get("transcript") or {}
            words = data["words"]
            if not transcript.get("words") or not isinstance(words, list) or len(words) != len(transcript["words"]):
                raise WorkflowError("Supply one correction row per current word, or import a new complete cue sheet.", 409)
            for word in words:
                if not isinstance(word, dict) or set(word) not in ({"text", "start", "end"}, {"id", "text", "start", "end"}):
                    raise WorkflowError("Each correction must include word text, start and end.")
            if [dict(word, id=index) for index, word in enumerate(words, 1)] == transcript["words"]:
                return self._view(project)
            raw = encoded({
                "audio": {"sha256": recording["originalHash"]}, "duration": recording["duration"], "words": words,
                "provenance": {"method": "operator-corrected cues", "parentTimingHash": transcript["timingHash"]},
            })
            self._record_timing(project, raw)
            self._save(project)
            return self._view(project)

    def _finish_provider(self, project, run, error):
        with self.lock:
            if error is not None:
                run.update(status="failed", error=error)
            run["finishedAt"] = now()
            project["busy"] = None
            self._save(project)

    def generate_art(self, project_id, asset_id, data):
        fields(data, ("revision",))
        art_id(asset_id)
        with self.lock:
            project = self._edit(project_id, data["revision"])
            if not project["plan"] or not project["plan"]["approved"]:
                raise WorkflowError("Approve the plan before generating artwork.", 409)
            brief = next((item["brief"] for item in missing_art(project) if item["id"] == asset_id), "")
            if not brief:
                raise WorkflowError("This artwork ID has no pending brief.", 409)
            if not providers.image_available():
                raise WorkflowError("Enable OpenAI image generation and add an OpenAI key in Settings to generate here.", 409)
            if len(project["artRuns"]) >= MAX_ART_GENERATIONS:
                raise WorkflowError("This project reached its artwork generation limit.", 409)
            style = project.get("styleSnapshot") or {}
            answers = style.get("answers", {})
            options = {}
            if style:
                options = {
                    "guide": style["guide"], "visual_notes": answers["visualNotes"] + "\n" + answers["colorNotes"],
                    "palette": style["palette"], "references": self._style_reference_bytes(project),
                }
            payload = providers.image_request(style.get("name", STYLES[project["style"]]), asset_id, brief, **options)
            run = {"model": payload["model"], "id": asset_id, "status": "running", "startedAt": now(),
                   "requestHash": digest(encoded(payload)), "planHash": project["plan"]["hash"]}
            project["artRuns"].append(run)
            project["busy"] = "generating artwork"
            self._save(project)
        error = None
        try:
            with api_log.scope(project_id, "artwork generation"):
                raw, receipt = providers.request_image(payload)
            normalized = normalized_art(raw)
            self._add_art(project, asset_id, f"{asset_id}.png", normalized, "generated")
            run.update(receipt, status="complete", artHash=digest(normalized))
        except (providers.ProviderError, WorkflowError) as failure:
            error = str(failure)
        except Exception:
            error = "Bacchus could not complete the image generation request."
            raise
        finally:
            self._finish_provider(project, run, error)
        if error:
            raise WorkflowError(error, 422)
        return self._view(project)

    def upload_art(self, project_id, revision, asset_id, name, raw):
        art_id(asset_id)
        name = file_name(name)
        normalized = normalized_art(raw)
        with self.lock:
            project = self._edit(project_id, revision)
            self._add_art(project, asset_id, name, normalized, "upload")
            self._save(project)
            return self._view(project)

    def _add_art(self, project, asset_id, name, normalized, source):
        content_hash = digest(normalized)
        existing = assets_by_id(project).get(asset_id)
        if len(project["assets"]) >= MAX_PROJECT_ASSETS and existing is None:
            raise WorkflowError(f"This project already has {MAX_PROJECT_ASSETS} artwork IDs.")
        write_file(self._folder(project["id"]) / "assets" / f"{content_hash}.png", normalized)
        if existing and existing["hash"] == content_hash:
            if existing.get("issue"):
                raise WorkflowError("This is the same flagged artwork. Upload a corrected version.", 409)
            existing["name"] = name
            return
        if existing:
            project["assets"].remove(existing)
            self._resolve_art_issue(project, existing, content_hash)
        project["assets"].append({"id": asset_id, "name": name, "hash": content_hash, "approved": False, "source": source})
        if project["plan"] and any(use["artId"] == asset_id for scene in project["plan"]["scenes"]
                                   for use in planning.scene_art(scene)):
            reset_candidate(project)

    def approve_art(self, project_id, asset_id, data):
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            asset = assets_by_id(project).get(asset_id)
            if not asset:
                raise WorkflowError("Upload artwork for this ID first.", 409)
            if asset.get("issue"):
                raise WorkflowError("Replace this artwork or revise the scene requirement to resolve its issue.", 409)
            path = self._folder(project_id) / "assets" / f"{asset['hash']}.png"
            if file_digest(path) != asset["hash"]:
                raise WorkflowError("The uploaded artwork no longer matches its fingerprint.", 409)
            if not asset["approved"]:
                asset["approved"] = True
                project["approvals"].append({"kind": "artwork-candidate", "id": asset_id, "hash": asset["hash"], "at": now()})
            self._save(project)
            return self._view(project)

    def publish_art(self, project_id, asset_id, data):
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            folder = self._folder(project_id)
            library = self.library_for(self.project_style_id(project))
            candidate(project, folder, require_mix=False)
            asset = assets_by_id(project).get(asset_id)
            checks = [check for check in gates.checks(project, "assets") if check["assetId"] == asset_id]
            if not asset or not checks:
                raise WorkflowError("Artwork must pass a planned use before joining the library.", 409)
            proof_hashes = {}
            for check in checks:
                record = project["review"]["assets"].get(check["id"], {})
                if record.get("status") != "passed" or not gates.has_proof(folder, "assets", check, record):
                    raise WorkflowError("Pass every current planned use of this artwork before publishing.", 409)
                proof_hashes[check["id"]] = record["proofHash"]
            if self._library_contains_pin(library, asset):
                library.qualify(asset["libraryId"], asset["libraryVersion"], project_id, proof_hashes)
            else:
                uses = (use for scene in project["plan"]["scenes"] for use in planning.scene_art(scene))
                brief = next((use["artBrief"] for use in uses if use["artId"] == asset_id and use["artBrief"]), "")
                version = library.publish(
                    project_id=project_id, alias=asset_id, name=asset["name"], brief=brief, style=self.project_style_id(project),
                    source=asset.get("source", "project"), content_hash=asset["hash"],
                    path=folder / "assets" / f"{asset['hash']}.png", proofs=proof_hashes,
                )
                # Publication preserves the already reviewed bytes. The pinned origin is recorded separately.
                asset.update(libraryId=version["id"], libraryVersion=version["version"], brief=brief)
            asset["libraryQualified"] = True
            self._record_library_pin(project, asset)
            self._save(project)
            return self._view(project)

    def report_art_issue(self, project_id, asset_id, data):
        fields(data, ("revision", "kind", "reason"))
        if data["kind"] not in ("defective", "incomplete"):
            raise WorkflowError("Choose defective or incomplete artwork.")
        reason = text_value(data["reason"], "Artwork issue", 1000, True)
        with self.lock:
            project = self._edit(project_id, data["revision"])
            asset = assets_by_id(project).get(asset_id)
            if not asset:
                raise WorkflowError("Artwork not found in this project.", 404)
            if asset.get("issue"):
                raise WorkflowError("This version already has an open issue. Replace it with corrected artwork.", 409)
            issue = {"kind": data["kind"], "reason": reason, "at": now()}
            if asset.get("libraryId"):
                library = self.library_for(self.project_style_id(project))
                if self._library_contains_pin(library, asset):
                    library.flag(asset["libraryId"], asset["libraryVersion"], issue)
            asset.update(issue=issue, approved=False)
            project.setdefault("assetIssues", []).append({**issue, "id": asset_id, "hash": asset["hash"]})
            reset_candidate(project)
            self._save(project)
            return self._view(project)

    @staticmethod
    def _resolve_art_issue(project, asset, replacement_hash):
        for issue in project.get("assetIssues", []):
            if issue["id"] == asset["id"] and issue["hash"] == asset["hash"] and not issue.get("resolvedAt"):
                issue.update(resolvedAt=now(), replacementHash=replacement_hash)

    def _reusable_art(self, project):
        """Offer a bounded catalog and bind any selection to these exact revisions."""
        available = MAX_PROJECT_ASSETS - len(project["assets"])
        if available <= 0:
            return []
        excludes = assets_by_id(project)
        pinned_entries = {asset.get("libraryId") for asset in excludes.values()}
        narration = project["input"]
        wanted = asset_terms(f"{project['title']} {narration['intention']} {narration['script']}")
        ranked = []
        style_id = self.project_style_id(project)
        for entry in self.library_catalog(style_id)["assets"]:
            if entry["id"] in excludes or entry["id"] in pinned_entries:
                continue
            terms = asset_terms(f"{entry['alias']} {Path(entry['name']).stem} {entry['brief']}")
            matches = len(terms & wanted)
            if matches:
                latest = next((version for version in entry["versions"] if version["qualified"] and not version["issue"]), None)
                if latest:
                    ranked.append((matches, latest["createdAt"], {**latest, "alias": entry["alias"]}))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        limit = min(MAX_LIBRARY_CHOICES, available)
        seen_hashes = {asset["hash"] for asset in excludes.values() if asset["approved"]}
        reusable = []
        for _, _, version in ranked:
            if version["hash"] not in seen_hashes:
                seen_hashes.add(version["hash"])
                try:
                    self.library_for(style_id).blob(version["hash"])
                except (LibraryError, WorkflowError):
                    continue
                reusable.append(version)
                if len(reusable) == limit:
                    break
        return reusable

    @staticmethod
    def _pinned_art(alias, version):
        return {
            "id": alias, "name": version["name"], "brief": version["brief"], "hash": version["hash"],
            "approved": True, "source": "library", "libraryId": version["id"], "libraryVersion": version["version"],
            "libraryQualified": version.get("qualified", False),
        }

    def _copy_library_art(self, project, version):
        try:
            raw = self.library_for(self.project_style_id(project)).blob(version["hash"]).read_bytes()
        except OSError as error:
            raise WorkflowError("The stored artwork version could not be read.", 409) from error
        if digest(raw) != version["hash"]:
            raise WorkflowError("The stored artwork does not match its recorded fingerprint.", 409)
        folder = self._folder(project["id"]) / "assets"
        destination = folder / f"{version['hash']}.png"
        if file_digest(destination) != version["hash"]:
            write_file(destination, raw)

    @staticmethod
    def _record_library_pin(project, asset):
        project["approvals"].append({
            "kind": "library-pin", "id": asset["id"], "hash": asset["hash"],
            "libraryId": asset["libraryId"], "libraryVersion": asset["libraryVersion"], "at": now(),
        })

    def pin_library(self, project_id, data):
        fields(data, ("revision", "libraryId", "version", "alias"))
        alias = art_id(data["alias"])
        with self.lock:
            project = self._edit(project_id, data["revision"])
            version = self.library_for(self.project_style_id(project)).get(data["libraryId"], data["version"])
            existing = assets_by_id(project).get(alias)
            asset = self._pinned_art(alias, version)
            if existing:
                if existing.get("libraryId") == asset["libraryId"] and existing.get("libraryVersion") == asset["libraryVersion"]:
                    return self._view(project)
                if not existing.get("issue") or existing["hash"] == asset["hash"]:
                    raise WorkflowError("That artwork ID is already used. Choose another ID or a corrected version.", 409)
                project["assets"].remove(existing)
                self._resolve_art_issue(project, existing, asset["hash"])
                reset_candidate(project)
            elif len(project["assets"]) >= MAX_PROJECT_ASSETS:
                raise WorkflowError(f"This project already has {MAX_PROJECT_ASSETS} artwork IDs.")
            self._copy_library_art(project, version)
            project["assets"].append(asset)
            self._record_library_pin(project, asset)
            self._save(project)
            return self._view(project)

    def _apply_planned_art(self, project, scenes, reusable):
        if not reusable or not isinstance(scenes, list):
            return
        ids = {use["artId"] for scene in scenes if isinstance(scene, dict) for use in planning.scene_art(scene)}
        selected = [asset for asset in reusable if asset["id"] in ids]
        if not selected:
            return
        library = self.library_for(self.project_style_id(project))
        for version in selected:
            library.get(version["id"], version["version"])
        additions = [self._pinned_art(version["id"], version) for version in selected]
        validate_scenes({**project, "assets": project["assets"] + additions}, scenes)
        for version in selected:
            self._copy_library_art(project, version)
        for asset in additions:
            project["assets"].append(asset)
            self._record_library_pin(project, asset)

    def _generate(self, project_id, revision):
        with self.lock:
            project = self._edit(project_id, revision)
            require_source_cues(project)
            if not planning.planner_available():
                raise WorkflowError("Add an API key for the selected scene planner in Settings.", 409)
            if len(project["plannerRuns"]) >= planning.MAX_PLANNER_CALLS:
                raise WorkflowError("This project reached its AI planning request limit.", 409)
            reusable = self._reusable_art(project)
            payload = planning.plan_request(project, reusable, self._style_reference_bytes(project))
            provider = "openai" if "text" in payload else "anthropic"
            run = {
                "model": payload["model"], "requestHash": digest(encoded(payload)),
                "inputHash": project["input"]["hash"], "packHash": planning.PACK_HASH,
                "maxOutputTokens": payload.get("max_output_tokens", payload.get("max_tokens", planning.OUTPUT_TOKENS)),
                "startedAt": now(), "status": "running", "provider": provider,
                "libraryCatalog": [{"id": asset["id"], "version": asset["version"], "hash": asset["hash"]} for asset in reusable],
            }
            request_path = self._folder(project_id) / "records" / "planner" / f"{run['requestHash']}.json"
            if not request_path.exists():
                write_json(request_path, payload)
            project["plannerRuns"].append(run)
            project["busy"] = "planning"
            self._save(project)
        error = None
        try:
            with api_log.scope(project_id, "scene planning"):
                scenes, receipt = planning.request_plan(payload)
            run.update(receipt)
            self._apply_planned_art(project, scenes, reusable)
            set_plan(project, scenes, "claude" if provider == "anthropic" else "openai")
            run.update(status="complete", planHash=project["plan"]["hash"])
        except (planning.PlannerError, WorkflowError, LibraryError) as failure:
            error = str(failure)
        except Exception:
            error = "Bacchus could not complete the planning request."
            raise
        finally:
            self._finish_provider(project, run, error)
        if error:
            raise WorkflowError(error, 422)
        return self._view(project)

    @staticmethod
    def _require_review(project, folder, scope, *, verify=True):
        try:
            gates.require_approved(project, folder, scope, verify=verify)
        except gates.GateError as error:
            raise WorkflowError(str(error), error.status) from error

    def _require_prepared(self, project, folder, *, verify=True):
        record = candidate(project, folder, verify=verify)
        self._require_review(project, folder, "assets", verify=verify)
        if project["review"]["prepared"] != digest(encoded(record)):
            raise WorkflowError("Prepare the animation before reviewing or previewing it.", 409)
        return record

    def _require_animation_review(self, project, folder, record):
        key = digest(encoded(record))
        qc = project["qc"]
        if not qc or qc["status"] != "passed" or qc["candidateHash"] != key:
            raise WorkflowError("Run and pass the technical animation checks on this version.", 409)
        self._require_review(project, folder, "animation")
        return key

    def _run_qc(self, project, folder):
        record = self._require_prepared(project, folder)
        candidate_hash = digest(encoded(record))
        findings = [{"level": "info", "message": "Narration coverage, scene vocabulary, and file fingerprints passed."}]
        try:
            findings.extend({"level": "error", "message": message} for message in render.layout_findings(project, folder))
        except render.RenderError as error:
            findings.append({"level": "error", "message": str(error)})
        findings.extend({"level": "error", "message": message}
                        for message in gates.run(project, folder, "animation", write_file))
        if project["input"].get("timing") == "source-cues":
            findings.append({"level": "info", "message": "Scene cues and captions use recording-matched word timings."})
        elif not mix_audio(project["mix"]):
            findings.append({"level": "warning", "message": "This project has no recording. The video will be silent."})
        if candidate_hash != (project["qc"] or {}).get("candidateHash") or any(item["level"] == "error" for item in findings):
            project.update(accepted=None, export=None)
        elif project["accepted"]:
            try:
                self._require_review(project, folder, "animation")
            except WorkflowError:
                project.update(accepted=None, export=None)
        project["qc"] = {
            "status": "failed" if any(item["level"] == "error" for item in findings) else "passed",
            "findings": findings, "candidateHash": candidate_hash, "checkedAt": now(),
        }

    def _review_item(self, project, folder, scope, check_id):
        if scope == "assets":
            candidate(project, folder, require_mix=False)
        elif scope == "animation":
            self._require_prepared(project, folder)
            if not project["qc"]:
                raise WorkflowError("Run the animation checks first.", 409)
        else:
            raise WorkflowError("Review item not found.", 404)
        try:
            return gates.selected(project, folder, scope, check_id)
        except gates.GateError as error:
            raise WorkflowError(str(error), error.status) from error

    def record_review(self, project_id, scope, check_id, data):
        fields(data, ("revision", "passed", "reason"))
        if type(data["passed"]) is not bool:
            raise WorkflowError("Choose Pass or Fail for this check.")
        reason = text_value(data["reason"], "Review note", 1000)
        with self.lock:
            project = self._edit(project_id, data["revision"])
            folder = self._folder(project_id)
            _, review_record = self._review_item(project, folder, scope, check_id)
            try:
                gates.decide(review_record, data["passed"], reason, now())
            except gates.GateError as error:
                raise WorkflowError(str(error), error.status) from error
            if not data["passed"]:
                if scope == "assets":
                    reset_candidate(project)
                else:
                    project.update(accepted=None, export=None)
            self._save(project)
            return self._view(project)

    def review_proof(self, project_id, scope, check_id):
        with self.lock:
            project = self._read(project_id)
            folder = self._folder(project_id)
            if scope not in ("assets", "animation"):
                raise WorkflowError("Review item not found.", 404)
            if scope == "animation" and (not project["review"]["prepared"] or not project["qc"]):
                raise WorkflowError("Run the animation checks first.", 409)
            try:
                gates.selected(project, folder, scope, check_id)
            except gates.GateError as error:
                raise WorkflowError(str(error), error.status) from error
            return gates.proof_path(folder, scope, check_id)

    def advise_review(self, project_id, scope, check_id, data):
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            folder = self._folder(project_id)
            check, review_record = self._review_item(project, folder, scope, check_id)
            if review_record.get("advice"):
                return self._view(project)
            if review_record["status"] == "passed":
                raise WorkflowError("Ask Claude for advice before passing this proof.", 409)
            if not visual_review.available():
                raise WorkflowError("Add an Anthropic API key in Settings to ask Claude to review a proof.", 409)
            scene = project["plan"]["scenes"][check["scene"] - 1]
            style = project.get("styleSnapshot") or {}
            context = {
                "style": style.get("name", STYLES[project["style"]]), "scene": scene,
                "narration": [line["text"] for line in project["input"]["lines"][scene["startLine"] - 1:scene["endLine"]]],
                "intention": project["input"]["intention"], "samples": check.get("samples"),
            }
            if style.get("guide"):
                context["styleGuide"] = style["guide"][:3000]
            brief = json.dumps(context, ensure_ascii=False)
            if len(brief) > visual_review.MAX_BRIEF_CHARS:
                context["narration"] = " ".join(context["narration"])[:4000]
                brief = json.dumps(context, ensure_ascii=False)
            proof = gates.proof_path(folder, scope, check_id).read_bytes()
            if digest(proof) != review_record["proofHash"]:
                raise WorkflowError("The proof changed. Run the checks again.", 409)
            project["busy"] = "reviewing proof"
            self._save(project)
        try:
            label = "asset" if scope == "assets" else "animation"
            with api_log.scope(project_id, f"{label} review"):
                review_record["advice"] = visual_review.review(label, brief, proof)
        except visual_review.VisualReviewError as error:
            raise WorkflowError(str(error), 422) from error
        finally:
            with self.lock:
                project["busy"] = None
                self._save(project)
        return self._view(project)

    def preview(self, project_id, time):
        with self.lock:
            project = self._read(project_id)
            self._apply_library_issues(project)
            folder = self._folder(project_id)
            self._require_prepared(project, folder, verify=False)
        return render.preview(project, folder, time)

    def _queue_export(self, project_id, revision, *, rerender=False):
        with self.lock:
            project = self._edit(project_id, revision)
            folder = self._folder(project_id)
            record = candidate(project, folder)
            key = digest(encoded(record))
            if not project["accepted"] or project["accepted"]["hash"] != key:
                raise WorkflowError("Accept the current candidate before exporting.", 409)
            previous = project["export"] or {}
            encoder_mode = video_encoder_mode()
            if (not rerender and previous.get("status") == "complete" and previous.get("candidateHash") == key
                    and previous.get("encoderMode") == encoder_mode):
                try:
                    delivery.verify(folder, previous)
                except delivery.DeliveryError:
                    pass
                else:
                    return self._view(project)
            self._require_prepared(project, folder)
            self._require_animation_review(project, folder, record)
            if not render.render_available():
                raise WorkflowError("FFmpeg and ffprobe must be installed and on PATH.", 409)
            snapshot = deepcopy(project)
            delivery_id = digest(uuid.uuid4().bytes)
            project["busy"] = "rendering"
            project["export"] = {"status": "running", "candidateHash": key, "deliveryId": delivery_id,
                                 "encoderMode": encoder_mode, "startedAt": now()}
            self._save(project)
            job = threading.Thread(
                target=self._export, args=(snapshot, folder, key, record, encoder_mode, delivery_id),
                name=f"render-{project_id[:8]}",
            )
            self.jobs.add(job)
            job.start()
            return self._view(project)

    def _export(self, snapshot, folder, key, record, encoder_mode, delivery_id):
        output_dir = folder / "exports"
        result = {}
        try:
            output_dir.mkdir(exist_ok=True)
            with TemporaryDirectory(prefix=".bacchus-delivery-", dir=output_dir) as work:
                bundle = Path(work) / "bundle"
                bundle.mkdir()
                paths = delivery.bundle_paths(bundle)
                details = render.export_video(snapshot, folder, paths["video"],
                                              approved_mix=record["mix"]["rendered"], encoder_mode=encoder_mode)
                self._apply_library_issues(snapshot)
                if candidate(snapshot, folder) != record:
                    raise WorkflowError("An input changed while the candidate was rendering.", 409)
                self._require_review(snapshot, folder, "assets")
                self._require_animation_review(snapshot, folder, record)
                write_file(paths["captions"], subtitles(snapshot).encode("utf-8"))
                hashes = {kind: delivery.file_hash(paths[kind]) for kind in ("video", "captions")}
                receipt = {
                    "candidateHash": key, "deliveryId": delivery_id, "candidate": record,
                    "accepted": snapshot["accepted"], "qc": snapshot["qc"], "media": details,
                    "videoHash": hashes["video"], "captionsHash": hashes["captions"],
                    "captionsTiming": snapshot["input"].get("timing", "estimated"), "createdAt": now(),
                    "review": {
                        scope: {check["id"]: {**check, **snapshot["review"][scope][check["id"]]}
                                for check in gates.checks(snapshot, scope)}
                        for scope in ("assets", "animation")
                    },
                }
                receipt["libraryArtwork"] = {
                    asset["id"]: {"id": asset["libraryId"], "version": asset["libraryVersion"]}
                    for asset in snapshot["assets"] if "libraryId" in asset and asset["id"] in record["artwork"]
                }
                write_json(paths["receipt"], receipt)
                hashes["receipt"] = delivery.file_hash(paths["receipt"])
                bundle.rename(output_dir / delivery_id)
                result.update(status="complete", media=details, fileHashes=hashes)
        except (WorkflowError, render.RenderError) as error:
            result.update(status="failed", error=str(error))
        except Exception:
            result.update(status="failed", error="Bacchus could not finish writing the export.")
        finally:
            with self.lock:
                self.jobs.discard(threading.current_thread())
                project = self._read(snapshot["id"])
                project["busy"] = None
                project["export"].update(result, finishedAt=now())
                if result.get("status") == "complete":
                    project.setdefault("deliveries", []).append(project["export"])
                self._save(project)

    def media(self, project_id, kind, key):
        if not DIGEST.fullmatch(key):
            raise WorkflowError("Media not found.", 404)
        folder = self._folder(project_id)
        if kind in delivery.FILES:
            project = self._read(project_id)
            for record in reversed(project.get("deliveries", [])):
                if record["deliveryId"] == key:
                    try:
                        paths = delivery.verify(folder, record)
                    except delivery.DeliveryError as error:
                        raise WorkflowError(str(error), 409) from error
                    return paths[kind], delivery.FILES[kind][1]
            raise WorkflowError("Delivery not found.", 404)
        extensions = {"art": ("assets", ".png", "image/png"), "audio": ("audio", ".wav", "audio/wav")}
        if kind not in extensions:
            raise WorkflowError("Media not found.", 404)
        sub_dir, suffix, content_type = extensions[kind]
        path = folder / sub_dir / (key + suffix)
        if not path.is_file():
            raise WorkflowError("Media not found.", 404)
        return path, content_type
