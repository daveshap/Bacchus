import hashlib
import io
import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps, UnidentifiedImageError

from . import ENGINE_VERSION, api_log, audio, budget, captions, chart_evidence, delivery, gates, local_timing, planning, providers, render, score, styles, timing, timing_models, visual_review
from . import auto_review
from .layer_spec import MAX_CANVAS_CODE_CHARS
from .library import Library, LibraryError
from .settings import timing_model_directory, video_encoder_mode
from .storage_paths import StoragePathError, writable_path


MAX_ART_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 384 * 1024 * 1024
MAX_DURATION = 3600
MAX_TRANSCRIPT_LENGTH = audio.MAX_TRANSCRIPT_LENGTH
MAX_FX_BYTES = 64 * 1024 * 1024
MAX_FX_DURATION = 60
MAX_EFFECTS = 12
MAX_ART_GENERATIONS = 96
MAX_TRANSCRIPTIONS = 3
MAX_PROJECT_ASSETS = 512
MAX_LIBRARY_CHOICES = 80
MAX_CACHED_CAPTIONS = 8
MAX_MOTION_PREVIEWS = 4
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
PREVIOUS_PROMPT_HASH = "cb0e1e2db7f2578dd8638fe2845bc3cc374702754c3e95c08b00b93a352f3c45"
V070_PACK_HASH = "0c67a60a03c50d0347161afefba01ae99e4cd8c4436d61ea0848e2a251e3ba5d"
V080_DRAFT_PACK_HASH = "d92643f2c9be2c03dd96b582e6696b7c514908e9f86ee5202ff66ed978e8dfda"
PREVIOUS_PACK_HASHES = frozenset((PREVIOUS_PACK_HASH, PREVIOUS_PROMPT_HASH, V070_PACK_HASH, V080_DRAFT_PACK_HASH))
MUSIC_CHOICES = ("style", "none", "original", "generic", "custom", "upload")


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
    path = writable_path(path)
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


def _stem(word):
    for suffix in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ied", "ers", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def asset_terms(text):
    tokens = [word for word in re.findall(r"[a-z0-9]{2,}", text.casefold()) if word not in LIBRARY_STOP_WORDS]
    expanded = set(tokens)
    for token in tokens:
        if len(token) >= 4:
            expanded.add(_stem(token))
    return expanded


def _term_bigrams(text):
    tokens = [word for word in re.findall(r"[a-z0-9]{2,}", text.casefold()) if word not in LIBRARY_STOP_WORDS]
    return {f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)}


def _slugify_alias(value):
    cleaned = re.sub(r"\.(png|jpe?g|webp)$", "", str(value or ""), flags=re.IGNORECASE)
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)[:64].rstrip("-")
    return cleaned or "asset"


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
        if isinstance(raw, dict):
            if "layers" not in raw:
                keys.discard("layers")
            if "canvasCode" in raw:
                keys.add("canvasCode")
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
        if "canvasCode" in raw:
            code = text_value(raw["canvasCode"], f"Scene {index} Canvas script", MAX_CANVAS_CODE_CHARS)
            if code:
                scene["canvasCode"] = code
            else:
                scene.pop("canvasCode", None)
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
    script = text_value(script, "Narration", MAX_TRANSCRIPT_LENGTH, True).replace("\r\n", "\n").replace("\r", "\n")
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


def music_preference(style, choice, music_style=None):
    if not isinstance(choice, str) or choice not in MUSIC_CHOICES:
        raise WorkflowError("Choose how to use music in this project.")
    if choice == "custom":
        if not isinstance(music_style, str) or music_style not in score.ARRANGEMENT_STYLES:
            raise WorkflowError("Choose an available style for the original score.")
    else:
        music_style = None
    answers = style["answers"]
    effective = ("original" if answers["music"] else "none") if choice == "style" else choice
    return {"choice": choice, "style": music_style, "effectiveChoice": effective}


def require_chart_evidence(source, scenes):
    findings = chart_evidence.chart_findings(source, scenes)
    if findings:
        extra = f" ({len(findings) - 1} other chart items need review.)" if len(findings) > 1 else ""
        raise WorkflowError(findings[0] + extra, 422)


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
        checked = validate_scenes(project, plan["scenes"])
        if digest(encoded(checked)) != plan["hash"]:
            raise WorkflowError("The approved scene plan does not match its recorded fingerprint.", 409)
        require_chart_evidence(source, checked)
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
    record["renderer"] = render.render_backend()
    if record["renderer"] == "canvas":
        # The approved asset bytes above are addressed by hash in the compiled film.
        try:
            record["buildHash"] = render.build_canvas_film(project, folder, verify_assets=False)[2]
        except render.RenderError as error:
            raise WorkflowError(str(error), 422) from error
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
    if (project.get("musicPreference") or {}).get("effectiveChoice") in ("original", "custom"):
        mix = project["mix"]
        mix.update(music=None, approved=False)
        mix.pop("rendered", None)
    reset_candidate(project)


def normalized_art(raw):
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or getattr(image, "n_frames", 1) != 1:
                raise WorkflowError("Upload a single-frame PNG, JPEG, or WebP image.")
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
        self.root = writable_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._caption_cache = OrderedDict()
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
        self.motion_jobs = set()
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
        Library(source, read_only=True).copy_by_style({style_id: self.library_for(style_id) for style_id in STYLES})
        write_json(marker, {"copiedAt": now()})

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
            if project.get("id") == path.parent.name:
                delivery.remove_abandoned_staging(self.root, path.parent)
            changed = bool(self._catalog_legacy_deliveries(project) or project["busy"])
            # Earlier Auto runs could stop after receiving an image while leaving
            # the artwork record running, even after the project itself was idle.
            for run in project.get("artRuns") or ():
                if isinstance(run, dict) and run.get("mode") == "auto" and run.get("status") == "running":
                    run.update(status="interrupted", finishedAt=now())
                    changed = True
            if project["busy"]:
                stage = project["busy"]
                project["busy"] = None
                if stage == "rendering":
                    project["export"]["status"] = "failed"
                    project["export"]["error"] = "Bacchus closed before the render completed."
                elif stage == "running auto pipeline":
                    if (project.get("export") or {}).get("status") == "running":
                        project["export"].update(status="failed", error="Auto export was interrupted.", finishedAt=now())
                    if isinstance(project.get("autoRun"), dict):
                        project["autoRun"].update(
                            status="interrupted",
                            error="Bacchus closed before the auto pipeline completed.",
                            finishedAt=now(),
                        )
                elif project["plannerRuns"]:
                    if stage == "planning":
                        project["plannerRuns"][-1].update(status="interrupted", finishedAt=now())
                for name in ("transcriptionRuns", "artRuns"):
                    if project[name] and project[name][-1]["status"] == "running":
                        project[name][-1].update(status="interrupted", finishedAt=now())
            if project.get("renderEngine") != ENGINE_VERSION:
                active = project.get("export") or {}
                if active.get("status") == "complete" and active.get("deliveryId"):
                    previous = project.setdefault("deliveries", [])
                    if not any(record["deliveryId"] == active["deliveryId"] for record in previous):
                        previous.append(active)
                project["renderEngine"] = ENGINE_VERSION
                project["mix"].update(approved=False)
                project["mix"].pop("rendered", None)
                reset_candidate(project)
                plan = project["plan"]
                if project["input"] and plan and chart_evidence.chart_findings(project["input"], plan["scenes"]):
                    plan["approved"] = False
                changed = True
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
        folder = self.root / project_id
        if writable_path(folder) != folder:
            raise WorkflowError("The project folder must stay in the selected Bacchus workspace.", 409)
        return folder

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
            elif project.get("pack", {}).get("hash") in PREVIOUS_PACK_HASHES:
                project["pack"] = PACK_PIN
            if "review" not in project:
                if not project["busy"] and (project["export"] or {}).get("status") != "complete":
                    reset_candidate(project)
                project["review"] = gates.new_review()
            project.setdefault("autoMode", False)
            project.setdefault("autoRun", None)
            project.setdefault("autoLimits", {})
            project.setdefault("autoBrief", "")
            return project
        except StoragePathError as error:
            raise WorkflowError(str(error), 409) from error
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
        try:
            view["captionReview"] = deepcopy(self._caption_data(project)[1])
        except captions.CaptionError as error:
            view.update(captionReview=None, captionReviewError=str(error))
        folder = self._folder(project_id)
        if view["autoMode"]:
            view["autoUsage"] = budget.summary(folder, project.get("autoLimits"))
        view["assetChecks"] = gates.view(view, folder, "assets")
        for check in view["assetChecks"]:
            scene = view["plan"]["scenes"][check["scene"] - 1]
            lines = view["input"]["lines"][scene["startLine"] - 1:scene["endLine"]]
            check["narrationText"] = " ".join(line["text"] for line in lines)
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
        if not view.get("musicPreference"):
            decision = "upload" if mix["music"] else ("none" if mix["musicMode"] == "none" else "style")
            view["musicPreference"] = music_preference(chosen_style, decision)
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

    def _caption_data(self, project):
        source = project["input"]
        if not source:
            return "", None
        if not source.get("words"):
            method = "authored-lines-v1" if source.get("timing") == "demo" else "legacy-lines-v1"
            return subtitles(project), {"method": method, "cueCount": len(source["lines"]),
                                        "flaggedCount": 0, "issues": []}
        key = source.get("hash")
        with self.lock:
            if isinstance(key, str) and key in self._caption_cache:
                self._caption_cache.move_to_end(key)
                return self._caption_cache[key]
            result = captions.generate(source)
            if isinstance(key, str):
                self._caption_cache[key] = result
                if len(self._caption_cache) > MAX_CACHED_CAPTIONS:
                    self._caption_cache.popitem(last=False)
            return result

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
                                     "styleId": chosen["id"], "styleName": chosen["name"],
                                     "autoMode": bool(project.get("autoMode"))})
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
                        "recognizing word cues", "preparing soundtrack", "running auto pipeline",
                    ):
                        return True
                except WorkflowError:
                    continue
            return False

    def create(self, title, demo=False, *, style_id="warm-editorial", style_revision=None,
               music_choice="style", music_style=None, auto_mode=None, auto_limits=None, auto_brief=""):
        if auto_mode is None:
            auto_mode = not demo
        if type(auto_mode) is not bool:
            raise WorkflowError("Choose whether Auto mode is enabled for this project.")
        chosen = self.styles.get(style_id, style_revision)
        project = {
            "id": uuid.uuid4().hex, "title": text_value(title, "Project title", 120, True),
            "revision": 0, "createdAt": now(), "pack": PACK_PIN, "input": None, "audio": None,
            "plan": None, "assets": [], "qc": None, "accepted": None, "export": None, "busy": None,
            "plannerRuns": [], "approvals": [],
            "style": chosen["visualPreset"], "styleSnapshot": chosen, "mix": new_mix("none") if demo else new_mix(),
            "demo": demo, "transcript": None, "transcriptionRuns": [], "artRuns": [],
            "review": gates.new_review(), "renderEngine": ENGINE_VERSION,
            "musicPreference": music_preference(chosen, "none" if demo else music_choice, music_style),
            "autoMode": auto_mode, "autoRun": None, "autoLimits": self._auto_limits(auto_limits),
            "autoBrief": text_value(auto_brief, "Creative brief", 2000),
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
        source = require_source_cues(project)
        plan = project["plan"]
        if not plan:
            raise WorkflowError("Save a scene plan first.", 409)
        require_chart_evidence(source, plan["scenes"])
        if not plan["approved"]:
            plan["approved"] = True
            project["approvals"].append({"kind": "plan", "hash": plan["hash"], "at": now()})
            style = project.get("styleSnapshot")
            if style and project["mix"]["musicMode"] is None:
                folder = self._folder(project["id"])
                preference = project.setdefault("musicPreference", music_preference(style, "style"))
                self._select_music(project, folder, preference)
                if style["answers"]["soundEffects"] and not project["mix"]["effects"]:
                    mix = {**project["mix"], "effects": self._transition_accents(project, folder)}
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
        if action == "mix/music-choice":
            params = ("choice", "style")
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
            elif action == "mix/music-choice":
                style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
                self._select_music(project, folder, music_preference(style, data["choice"], data["style"]))
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
            original_path = writable_path(folder / "source-audio" / (original_hash + extension))
            normalized_path = writable_path(folder / "audio" / f"{content_hash}.wav")
            write_file(original_path, raw)
            write_file(normalized_path, normalized)
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
        generated = (project.get("musicPreference") or {}).get("effectiveChoice") in ("original", "custom")
        if mode not in ("none", "track") or mode == "track" and not (mix["music"] or generated):
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
        if mode == "none" and mix["musicMode"] != "none":
            style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
            project["musicPreference"] = music_preference(style, "none")
        mix["musicMode"] = mode
        try:
            mix["musicRegions"] = render.validate_music_regions(project, data.get("musicRegions", mix.get("musicRegions", [])))
        except render.RenderError as error:
            raise WorkflowError(str(error)) from error
        self._update_mix(project, mix)

    def _select_music(self, project, folder, preference):
        style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
        mix = project["mix"]
        selected = preference["effectiveChoice"]
        if selected == "generic" and not (project.get("plan") or {}).get("approved"):
            raise WorkflowError("Approve the scene plan before creating a soundtrack.", 409)
        if preference == project.get("musicPreference") and mix["musicMode"] is not None:
            return
        project["musicPreference"] = preference
        if selected == "generic":
            self._preset_mix(project, folder, style["answers"]["musicPreset"])
        else:
            self._update_mix(project, {
                **mix, "musicMode": "none" if selected == "none" else "track", "music": None, "musicRegions": [],
            })

    def _preset_mix(self, project, folder, preset):
        if not isinstance(preset, str) or preset not in score.PRESETS:
            raise WorkflowError("Choose the Ambient or Pulse soundtrack.")
        if not project["input"] or not project["plan"] or not project["plan"]["approved"]:
            raise WorkflowError("Approve the scene plan before creating a soundtrack.", 409)
        raw_music, music_duration = score.soundtrack(preset)
        music = self._store_track(folder, score.PRESETS[preset], raw_music, music_duration, -16)
        style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
        project["musicPreference"] = music_preference(style, "generic")
        self._update_mix(project, {"musicMode": "track", "music": music, "effects": project["mix"]["effects"],
                                   "musicRegions": [], "approved": False})

    def _transition_accents(self, project, folder):
        effects = []
        scenes = project["plan"]["scenes"]
        accents = scenes[1:MAX_EFFECTS + 1] or scenes[:1]
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
                style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
                project["musicPreference"] = music_preference(style, "upload")
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
            require_source_cues(project)
            mix = project["mix"]
            preference = project.get("musicPreference") or {}
            arrangement = None
            if mix["musicMode"] == "track" and preference.get("effectiveChoice") in ("original", "custom"):
                if not (project.get("plan") or {}).get("approved"):
                    raise WorkflowError("Approve the scene plan before composing an original score.", 409)
                style = project.get("styleSnapshot") or self.styles.get(project["style"], 1)
                arrangement = score.arrangement(project, preference.get("style") or style["answers"]["musicPreset"])
            track = mix["music"] or {}
            compose = arrangement is not None and (
                track.get("arrangement") != arrangement
                or file_digest(folder / "audio" / f"{track['hash']}.wav") != track["hash"]
            )
            recipe = None
            if not compose:
                recipe = mix_record(project, folder, require_approval=False)
            if project["audio"] and file_digest(folder / "audio" / f"{project['audio']['hash']}.wav") != project["audio"]["hash"]:
                raise WorkflowError("The narration changed before soundtrack preparation.", 409)
            previous = mix.get("rendered")
            if not compose and previous and previous.get("recipeHash") == mix_key(project, recipe):
                try:
                    render.verify_mix(folder, previous, project["input"]["duration"])
                    return self._view(project)
                except render.RenderError:
                    pass
            project["busy"] = "preparing soundtrack"
            self._save(project)
            snapshot = deepcopy(project)
        directory = folder / "mixes"
        staging = folder / "exports"
        committed = False
        try:
            writable_path(directory).mkdir(exist_ok=True)
            writable_path(staging).mkdir(exist_ok=True)
            composed_music = None
            with TemporaryDirectory(prefix=delivery.STAGING_PREFIX, dir=staging) as work:
                if compose:
                    track_path = Path(work) / "score.wav"
                    duration = score.write_arrangement(track_path, arrangement)
                    content_hash = file_digest(track_path)
                    if not content_hash:
                        raise WorkflowError("The composed score could not be saved.", 422)
                    target = writable_path(folder / "audio" / f"{content_hash}.wav")
                    target.parent.mkdir(exist_ok=True)
                    if file_digest(target) != content_hash:
                        track_path.replace(target)
                    name = score.ARRANGEMENT_STYLES[arrangement["style"]]
                    composed_music = {
                        "name": f"Original score · {name}", "hash": content_hash, "duration": duration,
                        "gainDb": (snapshot["mix"]["music"] or {}).get("gainDb", -22), "arrangement": arrangement,
                    }
                    snapshot["mix"]["music"] = composed_music
                    recipe = mix_record(snapshot, folder, require_approval=False)
                key = mix_key(snapshot, recipe)
                temporary = Path(work) / "mix.wav"
                result = render.export_mix(snapshot, folder, temporary)
                if mix_record(snapshot, folder, require_approval=False) != recipe or (
                    snapshot["audio"] and file_digest(folder / "audio" / f"{snapshot['audio']['hash']}.wav") != snapshot["audio"]["hash"]
                ):
                    raise WorkflowError("An audio source changed during preparation.", 409)
                output = writable_path(directory / f"{result['sha256']}.wav")
                if file_digest(output) != result["sha256"]:
                    temporary.replace(output)
            result["recipeHash"] = key
            render.verify_mix(folder, result, snapshot["input"]["duration"])
            with self.lock:
                current = self._read(project_id)
                if composed_music is not None:
                    current["mix"]["music"] = composed_music
                if mix_record(current, folder, require_approval=False) != recipe or (
                    current["audio"] and file_digest(folder / "audio" / f"{current['audio']['hash']}.wav") != current["audio"]["hash"]
                ):
                    raise WorkflowError("An audio source changed during preparation.", 409)
                current["mix"].update(rendered=result, approved=False)
                reset_candidate(current)
                current["busy"] = None
                self._save(current)
                committed = True
                return self._view(current)
        except (render.RenderError, OSError, ValueError) as error:
            raise WorkflowError(str(error), 422) from error
        finally:
            if not committed:
                with self.lock:
                    current = self._read(project_id)
                    current["busy"] = None
                    self._save(current)

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
            original_hash = project["audio"].get("originalHash")
            path = self._folder(project_id) / "audio" / f"{recording_hash}.wav"
            if file_digest(path) != recording_hash:
                raise WorkflowError("The source recording is missing or no longer matches.", 409)
            run = {"model": audio.transcription_model(), "audioHash": recording_hash,
                   "status": "running", "startedAt": now()}
            project["transcriptionRuns"].append(run)
            run_index = len(project["transcriptionRuns"]) - 1
            project["busy"] = "transcribing"
            self._save(project)
        error = None
        transcript_update = None
        run_updates = {}
        try:
            with api_log.scope(project_id, "transcription"):
                result = audio.transcribe(path)
            script = text_value(result["script"], "Transcript", audio.MAX_TRANSCRIPT_LENGTH, True)
            transcript_update = {
                "script": script, "model": result["model"],
                "timing": "text-draft", "audioHash": recording_hash,
                "originalHash": original_hash,
            }
            run_updates = {"status": "complete", "requestId": result.get("requestId"), "usage": result.get("usage")}
        except (audio.AudioError, WorkflowError) as failure:
            error = str(failure)
        except Exception:
            error = "Bacchus could not complete the transcription."
            raise
        finally:
            with self.lock:
                current = self._read(project_id)
                current_run = current["transcriptionRuns"][run_index]
                if error is None and transcript_update is not None:
                    if ((current.get("transcript") or {}).get("timing") != "source-cues"
                            and (current.get("audio") or {}).get("hash") == recording_hash):
                        current["transcript"] = transcript_update
                    current_run.update(run_updates)
                elif error is not None:
                    current_run.update(status="failed", error=error)
                current_run["finishedAt"] = now()
                current["busy"] = None
                self._save(current)
                view = self._view(current) if error is None else None
        if error:
            raise WorkflowError(error, 422)
        return view

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
            recording = deepcopy(self._timing_recording(project))
            path = self._folder(project_id) / "audio" / f"{recording['hash']}.wav"
            if file_digest(path) != recording["hash"]:
                raise WorkflowError("The narration WAV is missing or has changed.", 409)
            project["busy"] = "recognizing word cues"
            self._save(project)
        committed = False
        try:
            model = data.get("modelDirectory")
            if "modelDirectory" not in data:
                model = str(self.timing_models.resolve(timing_model_directory() or None))
            result = local_timing.derive(path, model)
            if file_digest(path) != recording["hash"]:
                raise WorkflowError("The narration WAV changed during cue recognition.", 409)
            raw = encoded({"audio": {"sha256": recording["originalHash"]},
                           "duration": recording["duration"], **result})
            with self.lock:
                current = self._read(project_id)
                self._record_timing(current, raw)
                current["busy"] = None
                self._save(current)
                committed = True
                return self._view(current)
        except timing.TimingError as error:
            raise WorkflowError(str(error), 422) from error
        finally:
            if not committed:
                with self.lock:
                    current = self._read(project_id)
                    current["busy"] = None
                    self._save(current)

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
            payload, run_index = self._start_art_run(project, asset_id, brief, auto=False)
        return self._run_art_request(project_id, payload, run_index)

    def _start_art_run(self, project, asset_id, brief, *, auto):
        if len(project["artRuns"]) >= MAX_ART_GENERATIONS:
            raise WorkflowError("This project reached its artwork generation limit.", 409)
        style = project.get("styleSnapshot") or {}
        options = {}
        if style:
            answers = style["answers"]
            options = {
                "guide": style["guide"], "visual_notes": answers["visualNotes"] + "\n" + answers["colorNotes"],
                "palette": style["palette"], "references": self._style_reference_bytes(project),
            }
        payload = providers.image_request(style.get("name", STYLES[project["style"]]), asset_id, brief, **options)
        run = {"model": payload["model"], "id": asset_id, "status": "running", "startedAt": now(),
               "requestHash": digest(encoded(payload)), "planHash": project["plan"]["hash"]}
        if auto:
            run["mode"] = "auto"
        else:
            project["busy"] = "generating artwork"
        project["artRuns"].append(run)
        self._save(project)
        return payload, len(project["artRuns"]) - 1

    def _run_art_request(self, project_id, payload, run_index):
        receipt = {}
        failure = None
        try:
            with api_log.scope(project_id, "artwork generation"):
                raw, receipt = providers.request_image(payload)
            normalized = normalized_art(raw)
        except Exception as error:
            failure = error

        with self.lock:
            current = self._read(project_id)
            if failure is None:
                asset_id = current["artRuns"][run_index]["id"]
                try:
                    self._add_art(current, asset_id, f"{asset_id}.png", normalized, "generated")
                except Exception as error:
                    failure = error
                    current = self._read(project_id)
            run = current["artRuns"][run_index]
            run.update(receipt, finishedAt=now())
            if failure is None:
                run.update(status="complete", artHash=digest(normalized))
            else:
                run.update(status="failed", error=str(failure))
            manual = run.get("mode") != "auto"
            if manual:
                current["busy"] = None
            self._save(current)
            if failure is not None:
                if manual and isinstance(failure, (providers.ProviderError, WorkflowError)):
                    raise WorkflowError(str(failure), 422) from failure
                raise failure
            if manual:
                return self._view(current)

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

    def _reusable_art(self, project, *, lines_subset=None):
        """Offer a bounded catalog ranked by stem & bigram overlap and bind selections to exact revisions."""
        available = MAX_PROJECT_ASSETS - len(project["assets"])
        if available <= 0:
            return []
        excludes = assets_by_id(project)
        pinned_entries = {asset.get("libraryId") for asset in excludes.values()}
        narration = project["input"]
        script_text = " ".join(line["text"] for line in lines_subset) if lines_subset is not None else narration["script"]
        wanted_text = f"{project['title']} {narration['intention']} {script_text}"
        wanted = asset_terms(wanted_text)
        wanted_bigrams = _term_bigrams(wanted_text)
        ranked = []
        style_id = self.project_style_id(project)
        for entry in self.library_catalog(style_id)["assets"]:
            if entry["id"] in excludes or entry["id"] in pinned_entries:
                continue
            latest = next((version for version in entry["versions"] if version["qualified"] and not version["issue"]), None)
            if not latest:
                continue
            alias_text = f"{entry['alias']} {Path(entry['name']).stem}"
            alias_terms = asset_terms(alias_text)
            brief_terms = asset_terms(entry.get("brief", ""))
            entry_bigrams = _term_bigrams(f"{alias_text} {entry.get('brief', '')}")
            score_val = (
                2 * len(alias_terms & wanted)
                + len((brief_terms - alias_terms) & wanted)
                + 2 * len(entry_bigrams & wanted_bigrams)
            )
            if score_val > 0:
                ranked.append((score_val, latest["createdAt"], {**latest, "alias": entry["alias"]}))
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

    def import_library_file(self, style_id, name, raw, *, alias="", brief=""):
        """Normalize and import a single image file directly into a style's artwork library."""
        style = self.styles.get(style_id)
        clean_name = file_name(name)
        clean_alias = art_id(alias) if alias else _slugify_alias(clean_name)
        clean_brief = text_value(brief, "Artwork description", 500) if brief else Path(clean_name).stem.replace("-", " ").replace("_", " ")
        normalized = normalized_art(raw)
        version, imported = self.library_for(style["id"]).import_asset(
            alias=clean_alias, name=clean_name, brief=clean_brief, style=style["id"], png_bytes=normalized,
        )
        version["url"] = f"/api/library/media/{version['hash']}?styleId={style['id']}"
        return {"asset": version, "imported": imported, "styleId": style["id"]}

    def import_library_folder(self, style_id, folder_path):
        """Bulk import a studio catalog (catalog.json / manifest.json) or image folder into a style library."""
        if not isinstance(folder_path, str) or not folder_path.strip():
            raise WorkflowError("Choose an absolute directory path to import.")
        target = Path(folder_path.strip()).expanduser()
        if not target.is_absolute():
            raise WorkflowError("Use an absolute folder path on this machine.")
        try:
            root_dir = target.resolve(strict=True)
        except OSError as error:
            raise WorkflowError("The chosen import directory does not exist.", 404) from error
        if not root_dir.is_dir():
            raise WorkflowError("The chosen import path must be a folder.")
        style = self.styles.get(style_id)
        library = self.library_for(style["id"])

        items = []
        errors = []
        catalog_path = next((root_dir / name for name in ("catalog.json", "manifest.json") if (root_dir / name).is_file()), None)
        if catalog_path is not None:
            try:
                catalog_data = json.loads(catalog_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise WorkflowError(f"Could not read {catalog_path.name}: {error}", 422) from error
            raw_assets = catalog_data.get("assets") if isinstance(catalog_data, dict) else catalog_data
            if not isinstance(raw_assets, list):
                raise WorkflowError(f"{catalog_path.name} must contain an assets list.", 422)
            for index, entry in enumerate(raw_assets, 1):
                if not isinstance(entry, dict):
                    errors.append(f"Catalog entry {index} is not an artwork record.")
                    continue
                files = entry.get("files") if isinstance(entry.get("files"), dict) else {}
                rel = files.get("png") or files.get("webp") or entry.get("file") or entry.get("path")
                if not isinstance(rel, str) or not rel.strip():
                    errors.append(f"Catalog entry {index} has no image path.")
                    continue
                img_path = (root_dir / rel).resolve()
                if not img_path.is_relative_to(root_dir) or not img_path.is_file():
                    errors.append(f"Catalog entry {index}: could not locate {rel} inside the selected folder.")
                    continue
                checksum = None
                if "checksums" in entry:
                    sums = entry["checksums"]
                    checksum = (sums.get(rel) or sums.get(img_path.name)) if isinstance(sums, dict) else None
                    if not isinstance(checksum, str) or not DIGEST.fullmatch(checksum.lower()):
                        errors.append(f"Catalog entry {index}: no usable checksum for {rel}.")
                        continue
                alias = _slugify_alias(entry.get("id") or entry.get("alias") or img_path.stem)
                title = str(entry.get("title") or entry.get("name") or alias).strip()
                parts = [title]
                category = str(entry.get("category") or "").strip()
                if category:
                    parts.append(category)
                tags = entry.get("tags")
                if isinstance(tags, list) and tags:
                    parts.append(", ".join(str(tag).strip() for tag in tags if str(tag).strip()))
                notes = str(entry.get("notes") or entry.get("brief") or "").strip()
                if notes:
                    parts.append(notes)
                brief = re.sub(r"[\x00-\x1f\ud800-\udfff]+", " ", " · ".join(parts)).strip()[:480]
                display_name = re.sub(r"[\x00-\x1f\ud800-\udfff]+", " ", title or img_path.name).strip()[:160] or img_path.name
                items.append((alias, display_name, brief, img_path, checksum))
        else:
            supported = {".png", ".jpg", ".jpeg", ".webp"}
            for img_path in sorted(root_dir.rglob("*")):
                if not img_path.is_file() or img_path.suffix.lower() not in supported:
                    continue
                if any(part.startswith(".") for part in img_path.relative_to(root_dir).parts):
                    continue
                rel_parent = img_path.relative_to(root_dir).parent
                stem = img_path.stem
                if stem.lower() == "asset" and rel_parent.name:
                    stem = rel_parent.name
                alias = _slugify_alias(stem)
                words = re.sub(r"[-_]+", " ", stem).strip()
                folder_hint = " ".join(part for part in rel_parent.parts if part and part != ".")
                brief = re.sub(r"[\x00-\x1f\ud800-\udfff]+", " ", f"{words} {folder_hint}".strip())[:480]
                if not img_path.resolve().is_relative_to(root_dir):
                    errors.append(f"{img_path.name} points outside the selected folder.")
                    continue
                items.append((alias, img_path.name, brief or words, img_path, None))

        if not items and not errors:
            raise WorkflowError("No PNG, JPEG, or WebP artwork files were found in that folder.", 404)

        imported = 0
        skipped = 0
        total = len(items) + len(errors)
        used_aliases = {}
        for base_alias, display_name, brief, img_path, checksum in items:
            count = used_aliases.get(base_alias, 0)
            used_aliases[base_alias] = count + 1
            alias = base_alias if count == 0 else _slugify_alias(f"{base_alias}-{count + 1}")
            try:
                with img_path.open("rb") as image_file:
                    raw = image_file.read(MAX_ART_BYTES + 1)
                if len(raw) > MAX_ART_BYTES:
                    errors.append(f"{img_path.name}: exceeds {MAX_ART_BYTES // (1024 * 1024)} MB limit.")
                    continue
                source_hash = digest(raw)
                if checksum and source_hash != checksum.lower():
                    errors.append(f"{img_path.name}: source checksum differs from the catalog.")
                    continue
                normalized = normalized_art(raw)
                _, is_new = library.import_asset(
                    alias=alias, name=display_name, brief=brief, style=style["id"],
                    png_bytes=normalized, origin_key="studio-import", source_hash=source_hash,
                )
                if is_new:
                    imported += 1
                else:
                    skipped += 1
            except (WorkflowError, LibraryError, OSError) as error:
                errors.append(f"{img_path.name}: {error}")
        return {
            "styleId": style["id"],
            "directory": str(root_dir),
            "imported": imported,
            "skipped": skipped,
            "total": total, "failed": len(errors),
            "errors": errors[:25],
        }

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
            refs = self._style_reference_bytes(project)
            use_chunked = len(project["input"]["lines"]) > planning.CHUNK_LINE_THRESHOLD
            if use_chunked:
                provider = planning.planner_provider()
                reusable = []
                payload = None
                chunks = planning.split_line_chunks(project["input"]["lines"])
                run = {
                    "model": planning.planner_model(), "requestHash": None,
                    "inputHash": project["input"]["hash"], "packHash": planning.PACK_HASH,
                    "maxOutputTokens": planning.OUTPUT_TOKENS,
                    "startedAt": now(), "status": "running", "provider": provider,
                    "chunks": len(chunks), "libraryCatalog": [],
                }
            else:
                reusable = self._reusable_art(project)
                payload = planning.plan_request(project, reusable, refs)
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
            run_index = len(project["plannerRuns"]) - 1
            project["busy"] = "planning"
            self._save(project)
            snapshot = deepcopy(project)
        error = None
        scenes = None
        receipt = {}
        first_payload = payload
        try:
            with api_log.scope(project_id, "scene planning"):
                if use_chunked:
                    scenes, receipt, reusable, first_payload = planning.request_plan_chunked(
                        snapshot,
                        reusable_for_lines=lambda subset: self._reusable_art(snapshot, lines_subset=subset),
                        references=refs,
                    )
                else:
                    scenes, receipt = planning.request_plan(payload)
        except (planning.PlannerError, WorkflowError, LibraryError) as failure:
            error = str(failure)
        except Exception:
            error = "Bacchus could not complete the planning request."
            raise
        finally:
            with self.lock:
                current = self._read(project_id)
                current_run = current["plannerRuns"][run_index]
                if error is None and scenes is not None:
                    try:
                        current_run.update(receipt)
                        if use_chunked and first_payload is not None:
                            req_hash = digest(encoded(first_payload))
                            current_run["requestHash"] = req_hash
                            current_run["libraryCatalog"] = [
                                {"id": asset["id"], "version": asset["version"], "hash": asset["hash"]} for asset in reusable
                            ]
                            request_path = self._folder(project_id) / "records" / "planner" / f"{req_hash}.json"
                            if not request_path.exists():
                                write_json(request_path, first_payload)
                        self._apply_planned_art(current, scenes, reusable)
                        set_plan(current, scenes, "claude" if provider == "anthropic" else "openai")
                        current_run.update(status="complete", planHash=current["plan"]["hash"])
                    except (WorkflowError, LibraryError) as failure:
                        error = str(failure)
                if error is not None:
                    current_run.update(status="failed", error=error)
                current_run["finishedAt"] = now()
                current["busy"] = None
                self._save(current)
                view = self._view(current) if error is None else None
        if error:
            raise WorkflowError(error, 422)
        return view

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
        if record["renderer"] == "canvas" and qc.get("buildHash") != record["buildHash"]:
            raise WorkflowError("The compiled film differs from the one checked in QC. Prepare and review it again.", 409)
        self._require_review(project, folder, "animation")
        return key

    def _run_qc(self, project, folder):
        record = self._require_prepared(project, folder)
        candidate_hash = digest(encoded(record))
        findings = [{"level": "info", "message": "Narration coverage, scene vocabulary, and file fingerprints passed."}]
        layout_errors = []
        try:
            layout_errors = render.layout_findings(project, folder)
            findings.extend({"level": "error", "message": message} for message in layout_errors)
        except render.RenderError as error:
            layout_errors = [str(error)]
            findings.append({"level": "error", "message": str(error)})
        canvas_summary = None
        build_hash = None
        if not layout_errors and render.render_backend() == "canvas":
            try:
                qc_report = render.canvas_qc_report(project, folder)
                findings.extend(qc_report.get("findings") or [])
                canvas_summary = qc_report.get("summary")
                build_hash = qc_report.get("buildHash")
                if build_hash != record["buildHash"]:
                    findings.append({"level": "error", "message": "Canvas QC inspected a different compiled film."})
                if canvas_summary:
                    findings.append({
                        "level": "info",
                        "message": (
                            f"Canvas QC checked {canvas_summary['labelsChecked']} labels, "
                            f"{canvas_summary['cueMatches']} spoken-word cues, "
                            f"and {canvas_summary['jumpsDetected']} jump anomalies."
                        ),
                    })
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
        qc_record = {
            "status": "failed" if any(item["level"] == "error" for item in findings) else "passed",
            "findings": findings, "candidateHash": candidate_hash, "checkedAt": now(),
        }
        if canvas_summary is not None:
            qc_record["canvasDiagnostics"] = canvas_summary
        if build_hash is not None:
            qc_record["buildHash"] = build_hash
        project["qc"] = qc_record

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
            proof_hash = review_record["proofHash"]
            proof = gates.proof_path(folder, scope, check_id).read_bytes()
            if digest(proof) != proof_hash:
                raise WorkflowError("The proof changed. Run the checks again.", 409)
            project["busy"] = "reviewing proof"
            self._save(project)
        advice = None
        try:
            label = "asset" if scope == "assets" else "animation"
            with api_log.scope(project_id, f"{label} review"):
                advice = visual_review.review(label, brief, proof)
        except visual_review.VisualReviewError as error:
            raise WorkflowError(str(error), 422) from error
        finally:
            with self.lock:
                current = self._read(project_id)
                if advice is not None:
                    slot = current["review"].get(scope, {}).get(check_id)
                    if slot and slot.get("proofHash") == proof_hash:
                        slot["advice"] = advice
                current["busy"] = None
                self._save(current)
                view = self._view(current) if advice is not None else None
        return view

    def preview(self, project_id, time, *, width=None):
        with self.lock:
            project = self._read(project_id)
            self._apply_library_issues(project)
            folder = self._folder(project_id)
            self._require_prepared(project, folder, verify=False)
        return render.preview(project, folder, time, width=width)

    def film_preview_html(self, project_id):
        """Build an interactive 60fps HTML5 Canvas preview document for a prepared project."""
        with self.lock:
            project = self._read(project_id)
            self._apply_library_issues(project)
            folder = self._folder(project_id)
            self._require_prepared(project, folder, verify=False)
            mix_url = f"/api/projects/{project_id}/mix/preview" if (project["mix"].get("rendered") or {}).get("sha256") else ""
            snapshot = deepcopy(project)
        html_bytes, _, _ = render.build_canvas_film(
            snapshot, folder, audio_url=mix_url,
            asset_url_prefix=f"/api/projects/{project_id}/media/art/",
            asset_url_suffix="", verify_assets=True,
        )
        return html_bytes

    def set_mode(self, project_id, data):
        """Toggle per-project Auto mode (MP3 -> verified MP4 without manual gate clicks) on or off."""
        fields(data, ("revision", "autoMode"))
        if type(data["autoMode"]) is not bool:
            raise WorkflowError("Choose whether Auto mode is enabled for this project.")
        with self.lock:
            project = self._edit(project_id, data["revision"])
            project["autoMode"] = data["autoMode"]
            self._save(project)
            return self._view(project)

    @staticmethod
    def _auto_limits(value):
        try:
            return budget.validate_limits(value)
        except ValueError as error:
            raise WorkflowError(str(error)) from error

    def set_auto_settings(self, project_id, data):
        fields(data, ("revision", "autoLimits", "autoBrief"))
        limits = self._auto_limits(data["autoLimits"])
        brief = text_value(data["autoBrief"], "Creative brief", 2000)
        with self.lock:
            project = self._edit(project_id, data["revision"])
            project.update(autoLimits=limits, autoBrief=brief)
            self._save(project)
            return self._view(project)

    def start_auto_pipeline(self, project_id, data):
        """Run the full gated verification pipeline automatically for a project with Auto mode enabled."""
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            if not project.get("autoMode"):
                raise WorkflowError("Enable Auto mode on this project before starting the auto pipeline.", 409)
            if not project["audio"] and not project["input"]:
                raise WorkflowError("Upload a narration audio file (MP3/WAV/M4A) before running Auto mode.", 409)
            if not render.render_available():
                raise WorkflowError("Auto mode needs FFmpeg and ffprobe, plus a Chromium browser for the BFG Canvas renderer.", 409)
            previous = project.get("autoRun") or {}
            unchanged = previous.get("stoppedInputs") == self._auto_inputs(project)
            if previous.get("blocked") and unchanged:
                raise WorkflowError("Correct the rejected project evidence before restarting Auto. " + previous["error"], 409)
            project["busy"] = "running auto pipeline"
            project["autoRun"] = {
                "status": "running", "currentStage": "timing", "stagesCompleted": [],
                "checks": previous.get("checks", []) if unchanged or not previous.get("stoppedInputs") else [],
                "warnings": [], "error": None, "deliveryId": None,
                "startedAt": now(), "finishedAt": None,
            }
            self._save(project)
            job = threading.Thread(
                target=self._run_auto_pipeline, args=(project_id,),
                name=f"auto-{project_id[:8]}",
            )
            self.jobs.add(job)
            job.start()
            return self._view(project)

    def _auto_mark(self, project_id, stage, *, completed=None, warnings=None):
        with self.lock:
            current = self._read(project_id)
            auto_run = current.get("autoRun") or {}
            auto_run["currentStage"] = stage
            done = auto_run.setdefault("stagesCompleted", [])
            if completed and completed not in done:
                done.append(completed)
            if warnings:
                auto_run.setdefault("warnings", []).extend(warnings)
            current["autoRun"] = auto_run
            self._save(current)

    @staticmethod
    def _auto_inputs(project):
        return digest(encoded([project.get(key) for key in (
            "audio", "input", "plan", "assets", "mix", "musicPreference", "styleSnapshot", "autoBrief", "review",
        )]))

    def _auto_check(self, project_id, stage, item, context, proof=None, *, prior_findings=()):
        proof_hash = digest(proof) if proof is not None else None
        evidence = {"version": auto_review.VERSION, "stage": stage, "context": context, "proofHash": proof_hash,
                    "priorFindings": prior_findings, "checklist": auto_review.CHECKLISTS[stage]}
        evidence_hash = digest(encoded(evidence))
        with self.lock:
            project = self._read(project_id)
            records = project["autoRun"]["checks"]
            latest = next((row for row in reversed(records) if row["item"] == item and row["stage"] == stage), None)
            if latest and latest["evidenceHash"] == evidence_hash:
                if latest["status"] == "passed":
                    return latest
                raise auto_review.Rejected(stage, item, evidence_hash, latest["findings"])
            evidence_dir = self._folder(project_id) / "records" / "checks"
            write_file(evidence_dir / f"{evidence_hash}.json", encoded(evidence))
            if stage == "delivery" and proof is not None:
                write_file(evidence_dir / f"{proof_hash}.png", proof)
        with api_log.scope(project_id, f"independent {stage} review"):
            reply = auto_review.check(stage, context, proof)
        findings = reply["findings"] + list(prior_findings)
        passed = all(f["status"] == "pass" for f in findings)
        with self.lock:
            project = self._read(project_id)
            record = {**reply, "stage": stage, "item": item, "evidenceHash": evidence_hash,
                      "status": "passed" if passed else "failed", "at": now(), "findings": findings}
            if proof_hash:
                record["proofHash"] = proof_hash
            project["autoRun"]["checks"].append(record)
            if not passed:
                project["accepted"] = None
            self._save(project)
        if not passed:
            raise auto_review.Rejected(stage, item, evidence_hash, findings)
        return record

    def _auto_proofs(self, project_id, scope):
        folder = self._folder(project_id)
        with self.lock:
            project = self._read(project_id)
            items = gates.checks(project, scope)
        for check in items:
            scene = project["plan"]["scenes"][check["scene"] - 1]
            slot = project["review"][scope][check["id"]]
            if slot["status"] == "failed" and slot.get("mode") != "auto":
                raise WorkflowError("Resolve the existing rejected proof before starting Auto: " + slot["reason"], 409)
            proof = gates.proof_path(folder, scope, check["id"]).read_bytes()
            if digest(proof) != slot["proofHash"]:
                raise WorkflowError("The review proof changed during verification.", 409)
            source = project["input"]
            style = project.get("styleSnapshot") or {}
            context = {"scene": scene, "brief": source["intention"], "guide": style.get("guide", ""),
                       "narration": source["lines"][scene["startLine"] - 1:scene["endLine"]],
                       "samples": check.get("samples"), "artId": check.get("assetId"), "layerId": check.get("layerId")}
            existing = (slot.get("advice") or {}).get("findings", [])
            outstanding = slot.get("blockingFindings", []) if slot.get("mode") == "auto" else [
                row for row in existing if row["status"] != "pass"]
            if outstanding and slot["status"] == "passed" and slot.get("reason"):
                context["humanResolution"] = {"findings": outstanding, "reason": slot["reason"]}
                outstanding = []
            item = (f"{check['assetId']}/{check['scene']}/{check.get('layerId') or 'scene'}"
                    if scope == "assets" else f"scene-{check['scene']}")
            try:
                decision = self._auto_check(project_id, scope, item, context, proof, prior_findings=outstanding)
            except auto_review.Rejected as rejection:
                with self.lock:
                    current = self._read(project_id)
                    rec = current["review"][scope][check["id"]]
                    rec.update(status="failed", mode="auto", reason=rejection.reason, reviewedAt=now(),
                               blockingFindings=outstanding)
                    self._save(current)
                raise
            with self.lock:
                current = self._read(project_id)
                rec = current["review"][scope][check["id"]]
                if rec["proofHash"] != decision["proofHash"]:
                    raise WorkflowError("The reviewed proof changed before approval.", 409)
                rec["advice"] = decision
                gates.decide(rec, True, "Independent checker passed all criteria for the bound proof.", now())
                rec["mode"] = "auto"
                self._save(current)
        if scope == "assets" and not items:
            self._auto_check(project_id, "asset-inventory", "project", {
                "brief": project["input"]["intention"],
                "scenes": [{"title": s["title"], "layout": s["layout"], "artwork": list(planning.scene_art(s)),
                            "layerKinds": [layer["kind"] for layer in s.get("layers", [])],
                            "customDrawing": bool(s.get("canvasCode"))} for s in project["plan"]["scenes"]],
            })

    def _run_auto_pipeline(self, project_id):
        folder = self._folder(project_id)
        error = None
        blocked = False
        try:
            with self.lock:
                limits = self._read(project_id).get("autoLimits")
            with budget.scope(folder, limits):
                while True:
                    try:
                        self._auto_stages(project_id, folder)
                        break
                    except auto_review.Rejected as rejection:
                        with self.lock:
                            current = self._read(project_id)
                            run = current["autoRun"]
                            # Count consecutive failures for this item, even across changed evidence.
                            prior_failures = []
                            for row in reversed(run["checks"]):
                                if (row["stage"], row["item"]) != (rejection.stage, rejection.item):
                                    continue
                                if row["status"] == "passed":
                                    break
                                prior_failures.append(row)
                            can_repair = rejection.stage in ("plan", "assets", "asset-inventory", "animation", "qc")
                            if rejection.stage == "delivery":
                                can_repair = rejection.failed_checks == {"encoded-picture"}
                            if (len(prior_failures) >= 2 or prior_failures[0].get("repairAttempted")
                                    or not can_repair):
                                blocked = True
                                raise
                            prior_failures[0]["repairAttempted"] = now()
                            self._save(current)
                        if rejection.stage == "assets":
                            asset_id = rejection.item.split("/", 1)[0]
                            scenes = current["plan"]["scenes"]
                            brief = next((use["artBrief"] for scene in scenes for use in planning.scene_art(scene)
                                          if use["artId"] == asset_id and use["artBrief"]), asset_id)
                            self._auto_generate_art(project_id, {"id": asset_id,
                                "brief": brief + "\nIndependent reviewer correction: " + rejection.reason})
                        else:
                            self._auto_generate_plan(project_id, str(rejection))
        except (WorkflowError, render.RenderError, timing.TimingError, timing_models.TimingModelError,
                planning.PlannerError, providers.ProviderError, captions.CaptionError, LibraryError, OSError, ValueError) as failure:
            error = str(failure)
        except Exception as failure:
            error = f"Auto stopped during production ({type(failure).__name__}): {failure}"
        finally:
            with self.lock:
                self.jobs.discard(threading.current_thread())
                if error is not None:
                    current = self._read(project_id)
                    current["busy"] = None
                    current["autoRun"].update(status="failed", error=error, finishedAt=now(), blocked=blocked,
                                              stoppedInputs=self._auto_inputs(current))
                    if (current.get("export") or {}).get("status") == "running":
                        current["export"].update(status="failed", error=error, finishedAt=now())
                    self._save(current)

    def _auto_generate_plan(self, project_id, feedback=None):
        folder = self._folder(project_id)
        with self.lock:
            snapshot = self._read(project_id)
            require_source_cues(snapshot)
            if not planning.planner_available():
                raise WorkflowError("Set the scene planner API key to generate or repair this plan.", 409)
            if len(snapshot["plannerRuns"]) >= planning.MAX_PLANNER_CALLS:
                raise WorkflowError("This project reached its AI planning request limit.", 409)
            refs = self._style_reference_bytes(snapshot)
            if feedback:
                snapshot["planRepair"] = feedback
        use_chunked = len(snapshot["input"]["lines"]) > planning.CHUNK_LINE_THRESHOLD
        with api_log.scope(project_id, "scene planning"):
            if use_chunked:
                scenes, receipt, reusable, first_payload = planning.request_plan_chunked(
                    snapshot,
                    reusable_for_lines=lambda subset: self._reusable_art(snapshot, lines_subset=subset),
                    references=refs,
                )
            else:
                reusable = self._reusable_art(snapshot)
                first_payload = planning.plan_request(snapshot, reusable, refs)
                scenes, receipt = planning.request_plan(first_payload)
        provider = "openai" if (first_payload and "text" in first_payload) else planning.planner_provider()
        req_hash = digest(encoded(first_payload)) if first_payload is not None else None
        if req_hash:
            request_path = folder / "records" / "planner" / f"{req_hash}.json"
            if not request_path.exists():
                write_json(request_path, first_payload)
        with self.lock:
            current = self._read(project_id)
            self._apply_planned_art(current, scenes, reusable)
            set_plan(current, scenes, "claude" if provider == "anthropic" else "openai")
            current["plannerRuns"].append({
                **receipt, "model": receipt.get("model") or first_payload["model"],
                "requestHash": req_hash, "inputHash": current["input"]["hash"],
                "packHash": planning.PACK_HASH, "provider": provider,
                "status": "complete", "planHash": current["plan"]["hash"],
                "startedAt": now(), "finishedAt": now(), "mode": "auto",
                "libraryCatalog": [{"id": a["id"], "version": a["version"], "hash": a["hash"]} for a in reusable],
            })
            self._save(current)

    def _auto_generate_art(self, project_id, item):
        if not providers.image_available():
            raise WorkflowError(f"Supply corrected artwork for {item['id']} or enable image generation.", 409)
        with self.lock:
            current = self._read(project_id)
            payload, run_index = self._start_art_run(current, item["id"], item["brief"], auto=True)
        self._run_art_request(project_id, payload, run_index)

    def _auto_stages(self, project_id, folder):
        # Stage 1: Recording-aligned word timing
        with self.lock:
            project = self._read(project_id)
            needs_timing = bool(
                not project["input"] and project["audio"] and (
                    (project.get("transcript") or {}).get("timing") != "source-cues"
                    or not (project.get("transcript") or {}).get("timingHash")
                    or file_digest(folder / "records" / "timing" / f"{(project.get('transcript') or {}).get('timingHash', '')}.json")
                    != (project.get("transcript") or {}).get("timingHash")
                )
            )
            recording = deepcopy(project["audio"]) if needs_timing else None
        if needs_timing:
            wav_path = folder / "audio" / f"{recording['hash']}.wav"
            if file_digest(wav_path) != recording["hash"]:
                raise WorkflowError("The narration WAV is missing or has changed.", 409)
            model_dir = str(self.timing_models.resolve(timing_model_directory() or None))
            result = local_timing.derive(wav_path, model_dir)
            if file_digest(wav_path) != recording["hash"]:
                raise WorkflowError("The narration WAV changed during cue recognition.", 409)
            raw = encoded({"audio": {"sha256": recording["originalHash"]},
                           "duration": recording["duration"], **result})
            with self.lock:
                current = self._read(project_id)
                self._record_timing(current, raw)
                self._save(current)
        self._auto_mark(project_id, "intake", completed="timing")

        # Stage 2: Lock narration intake
        with self.lock:
            current = self._read(project_id)
            if not current["input"]:
                if current["audio"]:
                    self._timing_recording(current)
                    timing_hash = (current.get("transcript") or {}).get("timingHash")
                    if not timing_hash or file_digest(folder / "records" / "timing" / f"{timing_hash}.json") != timing_hash:
                        raise WorkflowError("Word-level cue timing evidence is missing.", 409)
                    script = ((current.get("transcript") or {}).get("script") or "").strip()
                    if not script:
                        raise WorkflowError("Could not extract narration text from the audio recording.", 422)
                    intention = current.get("autoBrief") or f"Editorial visual explainer for {current['title']}"[:2000]
                    current["input"] = make_input(current, script, intention)
                    current["approvals"].append({"kind": "narration-cues", "hash": timing_hash, "at": now(), "mode": "auto"})
                    self._save(current)
                else:
                    raise WorkflowError("Upload a narration audio file before running Auto mode.", 409)

        with self.lock:
            current = self._read(project_id)
            source = require_source_cues(current)
        self._auto_check(project_id, "intake", "recording", {
            "brief": source["intention"], "recording": current["audio"], "timing": source["timing"],
            "duration": source["duration"], "lines": source["lines"], "timedWords": len(source.get("words", [])),
        })
        self._auto_mark(project_id, "plan", completed="intake")
        if not current["plan"]:
            self._auto_generate_plan(project_id)
        with self.lock:
            current = self._read(project_id)
            scenes = current["plan"]["scenes"]
        for index in range(0, len(scenes), 8):
            group = scenes[index:index + 8]
            chart_errors = [{"id": "source-chart", "status": "fail", "evidence": message}
                            for message in chart_evidence.chart_findings(source, group)]
            self._auto_check(project_id, "plan", f"scenes-{index + 1}", {
                "brief": source["intention"], "guide": (current.get("styleSnapshot") or {}).get("guide", ""),
                "scenes": group, "narration": source["lines"][group[0]["startLine"] - 1:group[-1]["endLine"]],
                "chartFindings": chart_errors,
            }, prior_findings=chart_errors)
        with self.lock:
            current = self._read(project_id)
            if not current["plan"]["approved"]:
                self._approve_plan(current)
                current["approvals"][-1]["mode"] = "auto"
                self._save(current)
            missing = missing_art(current)
        self._auto_mark(project_id, "assets", completed="plan")
        for item in missing:
            self._auto_generate_art(project_id, item)
        with self.lock:
            current = self._read(project_id)
            self._apply_library_issues(current)
            for asset in current["assets"]:
                if asset.get("issue"):
                    raise WorkflowError(
                        f"Artwork {asset['id']!r} has an unresolved defect. Replace it before running Auto mode.",
                        409,
                    )
                if not asset["approved"]:
                    if file_digest(folder / "assets" / f"{asset['hash']}.png") != asset["hash"]:
                        raise WorkflowError(f"Artwork {asset['id']!r} no longer matches its fingerprint.", 409)
                    asset["approved"] = True
                    current["approvals"].append({
                        "kind": "artwork-candidate", "id": asset["id"], "hash": asset["hash"],
                        "at": now(), "mode": "auto",
                    })
            candidate(current, folder, require_mix=False)
            self._save(current)
            asset_snapshot = deepcopy(current)
        asset_errors = gates.run(asset_snapshot, folder, "assets", write_file)
        if asset_errors:
            raise WorkflowError(f"Automated asset proof check failed: {asset_errors[0]}", 422)
        with self.lock:
            current = self._read(project_id)
            current["review"]["assets"] = asset_snapshot["review"]["assets"]
            self._save(current)
        self._auto_proofs(project_id, "assets")
        self._auto_mark(project_id, "composition", completed="assets")
        self._auto_compose(project_id, folder)
        self._auto_mark(project_id, "qc", completed="composition")
        warnings = self._auto_qc(project_id, folder)
        self._auto_mark(project_id, "export", completed="qc", warnings=warnings)
        self._auto_export(project_id, folder)

    def _auto_compose(self, project_id, folder):
        # Stage 5: Soundtrack mastering & animation preparation
        with self.lock:
            current = self._read(project_id)
            style = current.get("styleSnapshot") or self.styles.get(current["style"], 1)
            if current["mix"]["musicMode"] is None:
                pref = current.get("musicPreference") or music_preference(style, "style")
                if pref.get("effectiveChoice") == "upload" and not current["mix"].get("music"):
                    raise WorkflowError("Attach the selected music track before running Auto.", 409)
                self._select_music(current, folder, pref)
            elif (current["mix"]["musicMode"] == "track"
                  and not current["mix"].get("music")
                  and (current.get("musicPreference") or {}).get("effectiveChoice") not in ("original", "custom")):
                raise WorkflowError("Attach the selected music track before running Auto.", 409)
            preference = current.get("musicPreference") or {}
            arrangement = None
            if current["mix"]["musicMode"] == "track" and preference.get("effectiveChoice") in ("original", "custom"):
                arrangement = score.arrangement(current, preference.get("style") or style["answers"]["musicPreset"])
            track = current["mix"]["music"] or {}
            compose = arrangement is not None and (
                track.get("arrangement") != arrangement
                or file_digest(folder / "audio" / f"{track['hash']}.wav") != track["hash"]
            )
            need_mix = True
            if not compose:
                recipe = mix_record(current, folder, require_approval=False)
                previous = current["mix"].get("rendered")
                if previous and previous.get("recipeHash") == mix_key(current, recipe):
                    try:
                        render.verify_mix(folder, previous, current["input"]["duration"])
                        need_mix = False
                    except render.RenderError:
                        pass
            self._save(current)
            mix_snapshot = deepcopy(current)
        if need_mix:
            mix_dir = folder / "mixes"
            staging = folder / "exports"
            writable_path(mix_dir).mkdir(exist_ok=True)
            writable_path(staging).mkdir(exist_ok=True)
            composed_music = None
            with TemporaryDirectory(prefix=delivery.STAGING_PREFIX, dir=staging) as work:
                if compose:
                    score_path = Path(work) / "score.wav"
                    score_dur = score.write_arrangement(score_path, arrangement)
                    score_hash = file_digest(score_path)
                    if not score_hash:
                        raise WorkflowError("The composed score could not be saved.", 422)
                    target = writable_path(folder / "audio" / f"{score_hash}.wav")
                    target.parent.mkdir(exist_ok=True)
                    if file_digest(target) != score_hash:
                        score_path.replace(target)
                    style_name = score.ARRANGEMENT_STYLES[arrangement["style"]]
                    composed_music = {
                        "name": f"Original score · {style_name}", "hash": score_hash, "duration": score_dur,
                        "gainDb": (mix_snapshot["mix"]["music"] or {}).get("gainDb", -22),
                        "arrangement": arrangement,
                    }
                    mix_snapshot["mix"]["music"] = composed_music
                    recipe = mix_record(mix_snapshot, folder, require_approval=False)
                key = mix_key(mix_snapshot, recipe)
                temp_mix = Path(work) / "mix.wav"
                mix_result = render.export_mix(mix_snapshot, folder, temp_mix)
                out_mix = writable_path(mix_dir / f"{mix_result['sha256']}.wav")
                if file_digest(out_mix) != mix_result["sha256"]:
                    temp_mix.replace(out_mix)
            mix_result["recipeHash"] = key
            render.verify_mix(folder, mix_result, mix_snapshot["input"]["duration"])
            with self.lock:
                current = self._read(project_id)
                if composed_music is not None:
                    current["mix"]["music"] = composed_music
                current["mix"].update(rendered=mix_result, approved=False)
                reset_candidate(current)
                self._save(current)
        with self.lock:
            current = self._read(project_id)
            recipe = mix_record(current, folder, require_approval=False)
            master = current["mix"]["rendered"]
            render.verify_mix(folder, master, current["input"]["duration"])
        self._auto_check(project_id, "composition", "project", {
            "musicPreference": current["musicPreference"], "recipe": recipe, "master": master,
            "duration": current["input"]["duration"], "voiceoverPresent": bool(current["audio"]),
            "brief": current["input"]["intention"],
        })
        with self.lock:
            current = self._read(project_id)
            if not current["mix"]["approved"]:
                current["mix"]["approved"] = True
                m_rec = mix_record(current, folder, require_approval=True)
                current["approvals"].append({
                    "kind": "mix", "hash": m_rec["rendered"]["sha256"], "at": now(), "mode": "auto",
                })
            cand_record = candidate(current, folder)
            self._require_review(current, folder, "assets")
            current["review"]["prepared"] = digest(encoded(cand_record))
            self._save(current)

    def _auto_qc(self, project_id, folder):
        with self.lock:
            qc_snapshot = self._read(project_id)
        # Stage 6: Automated BFG Canvas QC & candidate acceptance
        self._run_qc(qc_snapshot, folder)
        with self.lock:
            current = self._read(project_id)
            current["qc"] = qc_snapshot["qc"]
            current["review"]["animation"] = qc_snapshot["review"]["animation"]
            if current["qc"]["status"] != "passed":
                errors = [f for f in current["qc"]["findings"] if f.get("level") == "error"]
                self._save(current)
                if not errors or any(not f.get("kind") or f["kind"] == "runtime" for f in errors):
                    raise WorkflowError("Automated Canvas QC halted Auto mode: " + "; ".join(
                        f["message"] for f in errors[:3]), 422)
            else:
                errors = []
            qc_warnings = [f["message"] for f in current["qc"]["findings"] if f.get("level") == "warning"]
            self._save(current)
        if errors:
            self._auto_check(project_id, "qc", "candidate", {
                "candidateHash": current["qc"]["candidateHash"], "sceneCount": len(current["plan"]["scenes"]),
                "findings": errors, "brief": current["input"]["intention"],
            }, prior_findings=[{"id": f["kind"], "status": "fail", "evidence": f["message"]} for f in errors])
        self._auto_proofs(project_id, "animation")
        with self.lock:
            current = self._read(project_id)
            self._require_review(current, folder, "animation")
            evidence = {"brief": current["input"]["intention"], "sceneCount": len(current["plan"]["scenes"]),
                        "diagnostics": {k: v for k, v in current["qc"].items() if k != "checkedAt"},
                        "sceneChecks": [{"item": check["scene"], "result": current["review"]["animation"][check["id"]]["advice"]["findings"]}
                                        for check in gates.checks(current, "animation")]}
        self._auto_check(project_id, "qc", "candidate", evidence)
        with self.lock:
            current = self._read(project_id)
            cand_record = self._require_prepared(current, folder)
            cand_hash = self._require_animation_review(current, folder, cand_record)
            current["accepted"] = {"hash": cand_hash, "at": now(), "mode": "auto"}
            current["approvals"].append({"kind": "candidate", **current["accepted"]})
            self._save(current)
        return qc_warnings

    def _auto_export(self, project_id, folder):
        # Stage 7: Final 4K60 H.264 MP4 + SRT + verified receipt delivery
        with self.lock:
            current = self._read(project_id)
            cand_record = self._require_prepared(current, folder)
            cand_hash = self._require_animation_review(current, folder, cand_record)
            encoder_mode = video_encoder_mode()
            caption_data = self._caption_data(current)
            delivery_id = digest(uuid.uuid4().bytes)
            current["export"] = {
                "status": "running", "candidateHash": cand_hash, "deliveryId": delivery_id,
                "encoderMode": encoder_mode, "executionMode": "auto", "startedAt": now(),
            }
            self._save(current)
            export_snapshot = deepcopy(current)
        result = self._build_delivery_bundle(
            export_snapshot, folder, cand_hash, cand_record, encoder_mode, delivery_id, caption_data, "auto",
        )
        with self.lock:
            current = self._read(project_id)
            current["export"].update(result, finishedAt=now())
            current.setdefault("deliveries", []).append(current["export"])
            auto_run = current["autoRun"]
            done = auto_run.setdefault("stagesCompleted", [])
            if "export" not in done:
                done.append("export")
            auto_run.update(status="complete", currentStage="complete", deliveryId=delivery_id, finishedAt=now())
            current["busy"] = None
            self._save(current)

    def motion_preview(self, project_id, data):
        fields(data, ("revision", "start", "end"))
        start, end = data["start"], data["end"]
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not 0 <= start < end <= MAX_DURATION or end - start > render.MAX_PREVIEW_SECONDS + 1e-6):
            raise WorkflowError("Choose up to 12 seconds of motion to preview.")
        with self.lock:
            project = self._edit(project_id, data["revision"])
            source = require_source_cues(project)
            if end > source["duration"] + 1e-6:
                raise WorkflowError("The motion preview must fit within the approved recording.")
            if project_id in self.motion_jobs:
                raise WorkflowError("Wait for the current motion preview to finish.", 409)
            folder = self._folder(project_id)
            record = self._require_prepared(project, folder)
            directory = folder / "review" / "motion"
            key = digest(uuid.uuid4().bytes)
            destination = directory / f"{key}.mp4"
            self.motion_jobs.add(project_id)
        try:
            writable_path(directory).mkdir(parents=True, exist_ok=True)
            staging = folder / "exports"
            writable_path(staging).mkdir(exist_ok=True)
            with TemporaryDirectory(prefix=delivery.STAGING_PREFIX, dir=staging) as work:
                clip = Path(work) / "preview.mp4"
                result = render.export_motion_preview(project, folder, clip, start, min(end, source["duration"]),
                                                      approved_mix=record["mix"]["rendered"])
                with self.lock:
                    current = self._edit(project_id, data["revision"])
                    if self._require_prepared(current, folder) != record:
                        raise WorkflowError("The candidate changed. Prepare a preview of the current cut.", 409)
                    clip.replace(writable_path(destination))
            return {**result, "url": f"/api/projects/{project_id}/motion-preview/{key}"}
        finally:
            with self.lock:
                self.motion_jobs.discard(project_id)
                try:
                    files = sorted(directory.glob("*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
                    for previous in files[MAX_MOTION_PREVIEWS:]:
                        writable_path(previous)
                        previous.unlink(missing_ok=True)
                except OSError:
                    pass

    def motion_preview_file(self, project_id, key):
        folder = self._folder(project_id)
        path = folder / "review" / "motion" / f"{key}.mp4"
        if not DIGEST.fullmatch(key) or not path.is_file():
            raise WorkflowError("Motion preview expired. Generate it again.", 404)
        return path

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
                raise WorkflowError("Video export needs FFmpeg and ffprobe, plus a Chromium browser for the BFG Canvas renderer.", 409)
            try:
                caption_data = self._caption_data(project)
            except captions.CaptionError as error:
                raise WorkflowError(f"Review the recording's word timings before rendering captions. {error}", 409) from error
            snapshot = deepcopy(project)
            delivery_id = digest(uuid.uuid4().bytes)
            project["busy"] = "rendering"
            project["export"] = {"status": "running", "candidateHash": key, "deliveryId": delivery_id,
                                 "encoderMode": encoder_mode,
                                 "executionMode": "manual",
                                 "startedAt": now()}
            self._save(project)
            job = threading.Thread(
                target=self._export, args=(snapshot, folder, key, record, encoder_mode, delivery_id, caption_data),
                name=f"render-{project_id[:8]}",
            )
            self.jobs.add(job)
            job.start()
            return self._view(project)

    def _build_delivery_bundle(self, snapshot, folder, key, record, encoder_mode, delivery_id, caption_data, execution_mode):
        output_dir = folder / "exports"
        caption_text, caption_review = caption_data
        writable_path(output_dir).mkdir(exist_ok=True)
        with TemporaryDirectory(prefix=delivery.STAGING_PREFIX, dir=output_dir) as work:
            bundle = Path(work) / "bundle"
            bundle.mkdir()
            paths = delivery.bundle_paths(bundle)
            details = render.export_video(snapshot, folder, paths["video"],
                                          approved_mix=record["mix"]["rendered"], encoder_mode=encoder_mode)
            if details.get("renderer") != record["renderer"]:
                raise WorkflowError("The encoded renderer differs from the accepted candidate.", 409)
            if record["renderer"] == "canvas":
                verification = details.get("visualVerification") or {}
                if verification.get("buildHash") != record["buildHash"]:
                    raise WorkflowError("The encoded Canvas picture verification differs from the accepted film build.", 409)
            self._apply_library_issues(snapshot)
            if candidate(snapshot, folder) != record:
                raise WorkflowError("An input changed while the candidate was rendering.", 409)
            self._require_review(snapshot, folder, "assets")
            self._require_animation_review(snapshot, folder, record)
            write_file(paths["captions"], caption_text.encode("utf-8"))
            hashes = {kind: delivery.file_hash(paths[kind]) for kind in ("video", "captions")}
            receipt = {
                "candidateHash": key, "deliveryId": delivery_id, "candidate": record,
                "accepted": snapshot["accepted"], "qc": snapshot["qc"], "media": details,
                "videoHash": hashes["video"], "captionsHash": hashes["captions"],
                "captionsTiming": snapshot["input"].get("timing", "estimated"),
                "executionMode": execution_mode, "createdAt": now(),
                "review": {
                    scope: {check["id"]: {**check, **snapshot["review"][scope][check["id"]]}
                            for check in gates.checks(snapshot, scope)}
                    for scope in ("assets", "animation")
                },
            }
            if caption_review is not None:
                receipt["captionReview"] = caption_review
            receipt["libraryArtwork"] = {
                asset["id"]: {"id": asset["libraryId"], "version": asset["libraryVersion"]}
                for asset in snapshot["assets"] if "libraryId" in asset and asset["id"] in record["artwork"]
            }
            if execution_mode == "auto":
                sample_context, proof = auto_review.decoded_samples(paths["video"], snapshot, details)
                self._auto_check(snapshot["id"], "delivery", "mp4", {
                    "candidateHash": key, "videoHash": hashes["video"], "captionsHash": hashes["captions"],
                    "brief": snapshot["input"]["intention"], "duration": snapshot["input"]["duration"],
                    "voiceoverRequired": bool(snapshot["audio"]), "music": snapshot["musicPreference"],
                    "master": record["mix"]["rendered"], "encodedMedia": details, "captions": caption_review,
                    "sampledScenes": sample_context,
                }, proof)
                with self.lock:
                    current = self._read(snapshot["id"])
                    if candidate(current, folder) != record or (current.get("accepted") or {}).get("hash") != key:
                        raise WorkflowError("The candidate changed during final review.", 409)
                    receipt["independentChecks"] = current["autoRun"]["checks"]
            write_json(paths["receipt"], receipt)
            hashes["receipt"] = delivery.file_hash(paths["receipt"])
            bundle.rename(writable_path(output_dir / delivery_id))
            result = {"status": "complete", "media": details, "fileHashes": hashes}
            if caption_review is not None:
                result["captionReview"] = caption_review
            return result

    def _export(self, snapshot, folder, key, record, encoder_mode, delivery_id, caption_data):
        result = {}
        try:
            result.update(self._build_delivery_bundle(
                snapshot, folder, key, record, encoder_mode, delivery_id, caption_data, "manual",
            ))
        except (WorkflowError, render.RenderError, StoragePathError) as error:
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

    def verified_delivery_paths(self, project_id, key):
        if not DIGEST.fullmatch(key):
            raise WorkflowError("Delivery not found.", 404)
        folder = self._folder(project_id)
        project = self._read(project_id)
        for record in reversed(project.get("deliveries", [])):
            if record["deliveryId"] == key:
                try:
                    return delivery.verify(folder, record)
                except delivery.DeliveryError as error:
                    raise WorkflowError(str(error), 409) from error
        raise WorkflowError("Delivery not found.", 404)

    def media(self, project_id, kind, key):
        if kind in delivery.FILES:
            return self.verified_delivery_paths(project_id, key)[kind], delivery.FILES[kind][1]
        if not DIGEST.fullmatch(key):
            raise WorkflowError("Media not found.", 404)
        folder = self._folder(project_id)
        extensions = {"art": ("assets", ".png", "image/png"), "audio": ("audio", ".wav", "audio/wav")}
        if kind not in extensions:
            raise WorkflowError("Media not found.", 404)
        sub_dir, suffix, content_type = extensions[kind]
        path = folder / sub_dir / (key + suffix)
        if not path.is_file():
            raise WorkflowError("Media not found.", 404)
        return path, content_type
