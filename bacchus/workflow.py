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

from PIL import Image, ImageOps, UnidentifiedImageError

from . import ENGINE_VERSION, audio, planning, providers, render, score, timing
from .library import Library, LibraryError


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
        fields(raw, planning.SCENE_FIELDS)
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
        if asset_id:
            art_id(asset_id)
            if asset_id not in assets and not brief:
                raise WorkflowError(f"Scene {index} needs an art brief for {asset_id}.")
            if brief and asset_id in briefs and briefs[asset_id] != brief:
                raise WorkflowError(f"Use one art brief for every occurrence of {asset_id}.")
            if brief:
                briefs[asset_id] = brief
        elif brief or raw["layout"] == "illustration":
            raise WorkflowError(f"Scene {index} needs an artwork ID for that layout or brief.")
        scenes.append({**raw, "title": title, "caption": caption, "artId": asset_id, "artBrief": brief})
    if expected != max_line + 1:
        raise WorkflowError(f"The plan still needs to cover lines {expected}–{max_line}.")
    return scenes


def make_input(project, script, intention):
    script = text_value(script, "Narration", 100_000, True).replace("\r\n", "\n").replace("\r", "\n")
    intention = text_value(intention, "Intention", 2_000)
    segments = [line.strip() for line in script.split("\n") if line.strip()]
    if len(segments) > planning.MAX_LINES or any(len(line) > 500 for line in segments):
        raise WorkflowError(f"Use at most {planning.MAX_LINES} nonempty narration lines and 500 characters per line.")
    weights = [max(1.6, len(line.split()) * 0.42) for line in segments]
    total = sum(weights)
    audio = project["audio"]
    duration = audio["duration"] if audio else round(total, 3)
    if duration > MAX_DURATION:
        raise WorkflowError(f"This pack supports excerpts up to {MAX_DURATION} seconds.")
    transcript = project.get("transcript")
    if transcript and transcript["timing"] == "source-cues" and script == transcript["script"]:
        source = {"script": script, "intention": intention, "lines": transcript["lines"],
                  "duration": duration, "timing": "source-cues", "timingHash": transcript["timingHash"]}
        return {**source, "hash": digest(encoded([source, audio["hash"], audio["originalHash"]]))}
    end = 0.0
    cumulative = 0.0
    lines = []
    for index, (line, weight) in enumerate(zip(segments, weights, strict=True), start=1):
        start = end
        cumulative += weight
        end = duration if index == len(segments) else round(cumulative * duration / total, 3)
        if end - start < 0.2:
            raise WorkflowError("The recording is too short for the number of narration segments.")
        lines.append({"id": index, "text": line, "start": start, "end": end})
    source = {"script": script, "intention": intention, "lines": lines, "duration": duration}
    original = audio.get("originalHash") if audio else None
    keys = [source, audio["hash"] if audio else None]
    if original:
        source["timing"] = "estimated"
        keys.append(original)
    return {**source, "hash": digest(encoded(keys))}


def pack_matches(project):
    if project["pack"] != PACK_PIN:
        raise WorkflowError("This project needs the pack version and files recorded when it was created.", 409)


def missing_art(project):
    if not project["plan"]:
        return []
    assets = assets_by_id(project)
    missing = {}
    for scene in project["plan"]["scenes"]:
        asset_id = scene["artId"]
        if asset_id and asset_id not in assets:
            missing[asset_id] = {"id": asset_id, "brief": scene["artBrief"]}
    return list(missing.values())


def art_prompt(missing, style):
    if not missing:
        return ""
    items = "\n\n".join(f"{item['id']}\n{item['brief']}" for item in missing)
    return (
        f"Create one separate image for each item below for the same animation. Visual style: {STYLES[style]}. "
        "Use a coherent visual style across the set. Keep requested isolated subjects on transparent "
        "backgrounds. Keep the artwork free of lettering. Return each image with its corresponding ID.\n\n"
        + items
    )


def new_mix(decision=None):
    return {"musicMode": decision, "music": None, "effects": [], "approved": decision is not None}


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
    source, mix = project["input"], project["mix"]
    if not source:
        raise WorkflowError("Lock the narration before composing audio.", 409)
    if mix["musicMode"] not in ("none", "track") or (mix["musicMode"] == "track" and not mix["music"]):
        raise WorkflowError("Choose no music or add a soundtrack.", 409)
    if require_approval and not mix["approved"]:
        raise WorkflowError("Approve the sound composition before running quality checks.", 409)
    if verify:
        for track in mix_audio(mix):
            if file_digest(folder / "audio" / f"{track['hash']}.wav") != track["hash"]:
                raise WorkflowError(f"Sound file {track['name']} is missing or no longer matches.", 409)
        if any(effect["start"] >= source["duration"] for effect in mix["effects"]):
            raise WorkflowError("Each sound effect must begin within the narration.", 409)
    return {
        "musicMode": mix["musicMode"],
        "music": mix["music"] if mix["musicMode"] == "track" else None,
        "effects": mix["effects"],
    }


def candidate(project, folder, verify=True, require_mix=True):
    pack_matches(project)
    source, plan = project["input"], project["plan"]
    if not source or not plan or not plan["approved"]:
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
    for scene in plan["scenes"]:
        asset_id = scene["artId"]
        if not asset_id or asset_id in used:
            continue
        asset = assets.get(asset_id)
        if not asset or not asset["approved"]:
            raise WorkflowError(f"Upload and approve the artwork for {asset_id}.", 409)
        if verify and file_digest(folder / "assets" / f"{asset['hash']}.png") != asset["hash"]:
            raise WorkflowError(f"The approved artwork for {asset_id} is missing or no longer matches.", 409)
        used[asset_id] = asset["hash"]
    return {
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


def reset_candidate(project):
    project.update(qc=None, accepted=None, export=None)


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
        self.library = Library(Path(library_root).resolve() if library_root is not None else self.root / "library")
        self.lock = threading.RLock()
        self.mix_lock = threading.Lock()
        self.jobs = set()
        self._recover_jobs()

    def _recover_jobs(self):
        for path in self.root.glob("*/project.json"):
            if not PROJECT_ID.fullmatch(path.parent.name):
                continue
            try:
                project = self._read(path.parent.name)
            except WorkflowError:
                continue
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
                self._save(project)

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
        for asset in view["assets"]:
            asset["url"] = f"{base}/media/art/{asset['hash']}"
        if view["audio"]:
            view["audio"]["url"] = f"{base}/media/audio/{view['audio']['hash']}"
        mix = view["mix"]
        for track in ([mix["music"]] if mix["music"] else []) + mix["effects"]:
            track["url"] = f"{base}/media/audio/{track['hash']}"
        view["missing"] = missing_art(project)
        view["artPrompt"] = art_prompt(view["missing"], project["style"])
        view["accepted"] = bool(project["accepted"])
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
        if view["export"] and view["export"]["status"] == "complete":
            output = view["export"]
            candidate_hash = output["candidateHash"]
            output["url"] = f"{base}/media/video/{candidate_hash}"
            output["captionsUrl"] = f"{base}/media/captions/{candidate_hash}"
            output["receiptUrl"] = f"{base}/media/receipt/{candidate_hash}"
        return view

    def list_projects(self):
        with self.lock:
            projects = []
            for path in self.root.glob("*/project.json"):
                if PROJECT_ID.fullmatch(path.parent.name):
                    try:
                        project = self._read(path.parent.name)
                    except WorkflowError:
                        continue
                    projects.append({key: project[key] for key in ("id", "title", "updatedAt")})
            return {"projects": sorted(projects, key=lambda item: item["updatedAt"], reverse=True)}

    def providers_in_use(self):
        with self.lock:
            for path in self.root.glob("*/project.json"):
                if not PROJECT_ID.fullmatch(path.parent.name):
                    continue
                try:
                    if self._read(path.parent.name)["busy"] in ("planning", "transcribing", "generating artwork"):
                        return True
                except WorkflowError:
                    continue
            return False

    def create(self, title, demo=False):
        project = {
            "id": uuid.uuid4().hex, "title": text_value(title, "Project title", 120, True),
            "revision": 0, "createdAt": now(), "pack": PACK_PIN, "input": None, "audio": None,
            "plan": None, "assets": [], "qc": None, "accepted": None, "export": None, "busy": None,
            "plannerRuns": [], "approvals": [],
            "style": "warm-editorial", "mix": new_mix("none") if demo else new_mix(),
            "demo": demo, "transcript": None, "transcriptionRuns": [], "artRuns": [],
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
        return project

    def _approve_plan(self, project):
        plan = project["plan"]
        if not plan:
            raise WorkflowError("Save a scene plan first.", 409)
        if not plan["approved"]:
            plan["approved"] = True
            project["approvals"].append({"kind": "plan", "hash": plan["hash"], "at": now()})

    def change(self, project_id, action, data):
        params = {"input": ("script", "intention"), "plan": ("scenes",)}.get(action, ())
        if action == "input" and isinstance(data, dict) and "style" in data:
            params += ("style",)
        if action == "mix":
            params = ("musicMode", "musicGainDb", "effects")
        if action == "mix/preset":
            params = ("preset",)
        fields(data, ("revision", *params))
        if action == "plan/generate":
            return self._generate(project_id, data["revision"])
        if action == "transcribe":
            return self._transcribe(project_id, data["revision"])
        if action == "export":
            return self._queue_export(project_id, data["revision"])
        with self.lock:
            project = self._edit(project_id, data["revision"])
            folder = self._folder(project_id)
            if action == "input":
                if project["input"]:
                    raise WorkflowError("Unlock the narration before replacing it.", 409)
                if not project["audio"] and not project["demo"]:
                    raise WorkflowError("Import the narration recording before locking intake.", 409)
                style = data.get("style", project["style"])
                if not isinstance(style, str) or style not in STYLES:
                    raise WorkflowError("Choose a supported visual style.")
                project["style"] = style
                project["input"] = make_input(project, data["script"], data["intention"])
            elif action == "input/unlock":
                project.update(input=None, plan=None)
                project["mix"]["approved"] = False
                reset_candidate(project)
            elif action == "plan":
                set_plan(project, data["scenes"], "manual")
            elif action == "plan/starter":
                if not project["input"]:
                    raise WorkflowError("Lock the narration before planning.", 409)
                set_plan(project, planning.starter_plan(project["input"]["lines"]), "starter")
            elif action == "plan/approve":
                self._approve_plan(project)
            elif action == "mix":
                self._edit_mix(project, data)
            elif action == "mix/approve":
                record = mix_record(project, folder, require_approval=False)
                if not project["mix"]["approved"]:
                    project["mix"]["approved"] = True
                    project["approvals"].append({"kind": "mix", "hash": digest(encoded(record)), "at": now()})
            elif action == "mix/preset":
                self._preset_mix(project, folder, data["preset"])
            elif action == "qc":
                self._run_qc(project, folder)
            elif action == "candidate/accept":
                record = candidate(project, folder)
                candidate_hash = digest(encoded(record))
                qc = project["qc"]
                if not qc or qc["status"] != "passed" or qc["candidateHash"] != candidate_hash:
                    raise WorkflowError("Run and pass QC on this version before accepting it.", 409)
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
        self._update_mix(project, mix)

    def _preset_mix(self, project, folder, preset):
        if not isinstance(preset, str) or preset not in score.PRESETS:
            raise WorkflowError("Choose the Ambient or Pulse soundtrack.")
        if not project["input"] or not project["plan"] or not project["plan"]["approved"]:
            raise WorkflowError("Approve the scene plan before creating a soundtrack.", 409)
        raw_music, music_duration = score.soundtrack(preset)
        music = self._store_track(folder, score.PRESETS[preset], raw_music, music_duration, -16)
        effects = []
        accents = project["plan"]["scenes"][1:MAX_EFFECTS + 1]
        if accents:
            raw_accent, accent_duration = score.accent()
            track = self._store_track(folder, "Transition accent", raw_accent, accent_duration, -22)
            for scene in accents:
                start = project["input"]["lines"][scene["startLine"] - 1]["start"]
                effects.append({**track, "id": uuid.uuid4().hex, "start": start})
        self._update_mix(project, {"musicMode": "track", "music": music, "effects": effects, "approved": False})

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
            record = mix_record(project, folder, require_approval=False)
            if not project["audio"] and not mix_audio(project["mix"]):
                raise WorkflowError("This cut has no audio to preview.", 409)
            key = digest(encoded([source["hash"], record, ENGINE_VERSION]))
            path = folder / "mix-preview" / f"{key}.wav"
        with self.mix_lock:
            if not path.is_file():
                render.export_mix(project, folder, path)
        return path

    def _transcribe(self, project_id, revision):
        with self.lock:
            project = self._edit(project_id, revision)
            if project["input"] or not project["audio"]:
                raise WorkflowError("Import a recording and leave intake unlocked before transcribing.", 409)
            if not audio.transcriber_available():
                raise WorkflowError("Configure the transcription API to transcribe the recording.", 409)
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
            result = audio.transcribe(path)
            script = text_value(result["script"], "Transcript", 100_000, True)
            make_input(project, script, "")
            project["transcript"] = {"script": script, "model": result["model"],
                                     "timing": result["timing"], "audioHash": recording_hash,
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
            recording = project["audio"]
            if project["input"] or not recording or not recording.get("originalHash"):
                raise WorkflowError("Import the original narration and leave intake unlocked before importing timings.", 409)
            try:
                transcript = timing.import_cues(raw, recording["originalHash"], recording["duration"])
            except timing.TimingError as error:
                raise WorkflowError(str(error)) from error
            content_hash = digest(raw)
            write_file(self._folder(project_id) / "records" / "timing" / f"{content_hash}.json", raw)
            project["transcript"] = {**transcript, "originalHash": recording["originalHash"],
                                     "audioHash": recording["hash"], "timingHash": content_hash}
            make_input(project, transcript["script"], "")
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
                raise WorkflowError("Configure the image API to generate artwork.", 409)
            if len(project["artRuns"]) >= MAX_ART_GENERATIONS:
                raise WorkflowError("This project reached its artwork generation limit.", 409)
            payload = providers.image_request(STYLES[project["style"]], asset_id, brief)
            run = {"model": payload["model"], "id": asset_id, "status": "running", "startedAt": now(),
                   "requestHash": digest(encoded(payload)), "planHash": project["plan"]["hash"]}
            project["artRuns"].append(run)
            project["busy"] = "generating artwork"
            self._save(project)
        error = None
        try:
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
            existing["name"] = name
            return
        if existing:
            project["assets"].remove(existing)
        project["assets"].append({"id": asset_id, "name": name, "hash": content_hash, "approved": False, "source": source})
        if project["plan"] and any(scene["artId"] == asset_id for scene in project["plan"]["scenes"]):
            reset_candidate(project)

    def approve_art(self, project_id, asset_id, data):
        fields(data, ("revision",))
        with self.lock:
            project = self._edit(project_id, data["revision"])
            asset = assets_by_id(project).get(asset_id)
            if not asset:
                raise WorkflowError("Upload artwork for this ID first.", 409)
            path = self._folder(project_id) / "assets" / f"{asset['hash']}.png"
            if file_digest(path) != asset["hash"]:
                raise WorkflowError("The uploaded artwork no longer matches its fingerprint.", 409)
            if not asset.get("libraryId"):
                scenes = project["plan"]["scenes"] if project["plan"] else []
                brief = next((scene["artBrief"] for scene in scenes if scene["artId"] == asset_id and scene["artBrief"]), "")
                version = self.library.publish(
                    project_id=project_id, alias=asset_id, name=asset["name"], brief=brief,
                    style=project["style"], source=asset.get("source", "project"),
                    content_hash=asset["hash"], path=path,
                )
                asset.update(libraryId=version["id"], libraryVersion=version["version"], brief=brief)
            if not asset["approved"]:
                asset["approved"] = True
                project["approvals"].append({"kind": "artwork", "id": asset_id, "hash": asset["hash"],
                                             "libraryId": asset["libraryId"], "libraryVersion": asset["libraryVersion"], "at": now()})
            self._save(project)
            return self._view(project)

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
        for entry in self.library.list_assets():
            if entry["id"] in excludes or entry["id"] in pinned_entries:
                continue
            terms = asset_terms(f"{entry['alias']} {Path(entry['name']).stem} {entry['brief']}")
            matches = len(terms & wanted)
            if matches:
                latest = next(version for version in entry["versions"] if version["version"] == entry["latestVersion"])
                ranked.append((matches, entry["style"] == project["style"], {**latest, "alias": entry["alias"]}))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]["createdAt"]), reverse=True)
        limit = min(MAX_LIBRARY_CHOICES, available)
        seen_hashes = {asset["hash"] for asset in excludes.values() if asset["approved"]}
        reusable = []
        for _, _, version in ranked:
            if version["hash"] not in seen_hashes:
                seen_hashes.add(version["hash"])
                reusable.append(version)
                if len(reusable) == limit:
                    break
        return reusable

    @staticmethod
    def _pinned_art(alias, version):
        return {
            "id": alias, "name": version["name"], "brief": version["brief"], "hash": version["hash"],
            "approved": True, "source": "library", "libraryId": version["id"], "libraryVersion": version["version"],
        }

    def _copy_library_art(self, project, version):
        try:
            raw = self.library.blob(version["hash"]).read_bytes()
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
        version = self.library.get(data["libraryId"], data["version"])
        with self.lock:
            project = self._edit(project_id, data["revision"])
            existing = assets_by_id(project).get(alias)
            asset = self._pinned_art(alias, version)
            if existing:
                if existing.get("libraryId") == asset["libraryId"] and existing.get("libraryVersion") == asset["libraryVersion"]:
                    return self._view(project)
                raise WorkflowError("That artwork ID is already used. Choose another project artwork ID.", 409)
            if len(project["assets"]) >= MAX_PROJECT_ASSETS:
                raise WorkflowError(f"This project already has {MAX_PROJECT_ASSETS} artwork IDs.")
            self._copy_library_art(project, version)
            project["assets"].append(asset)
            self._record_library_pin(project, asset)
            self._save(project)
            return self._view(project)

    def _apply_planned_art(self, project, scenes, reusable):
        if not reusable or not isinstance(scenes, list):
            return
        ids = {scene["artId"] for scene in scenes if isinstance(scene, dict) and isinstance(scene.get("artId"), str)}
        selected = [asset for asset in reusable if asset["id"] in ids]
        if not selected:
            return
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
            if not project["input"]:
                raise WorkflowError("Lock the narration before planning.", 409)
            if not planning.planner_available():
                raise WorkflowError("The AI planner has not been configured.", 409)
            if len(project["plannerRuns"]) >= planning.MAX_PLANNER_CALLS:
                raise WorkflowError("This project reached its AI planning request limit.", 409)
            reusable = self._reusable_art(project)
            payload = planning.plan_request(project, reusable)
            provider = "openai" if "text" in payload else "anthropic"
            run = {
                "model": payload["model"], "requestHash": digest(encoded(payload)),
                "inputHash": project["input"]["hash"], "packHash": planning.PACK_HASH,
                "maxOutputTokens": planning.OUTPUT_TOKENS, "startedAt": now(), "status": "running", "provider": provider,
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

    def _run_qc(self, project, folder):
        record = candidate(project, folder)
        candidate_hash = digest(encoded(record))
        findings = [{"level": "info", "message": "Narration coverage, scene vocabulary, and file fingerprints passed."}]
        try:
            findings.extend({"level": "error", "message": message} for message in render.layout_findings(project, folder))
        except render.RenderError as error:
            findings.append({"level": "error", "message": str(error)})
        if project["input"].get("timing") == "source-cues":
            findings.append({"level": "info", "message": "Scene cues and captions use recording-matched word timings."})
        elif project["audio"]:
            findings.append({"level": "warning", "message": "Line timings are estimated across your recording. Review sync before exporting."})
        elif not mix_audio(project["mix"]):
            findings.append({"level": "warning", "message": "This project has no recording. The video will be silent."})
        if candidate_hash != (project["qc"] or {}).get("candidateHash") or any(item["level"] == "error" for item in findings):
            reset_candidate(project)
        project["qc"] = {
            "status": "failed" if any(item["level"] == "error" for item in findings) else "passed",
            "findings": findings, "candidateHash": candidate_hash, "checkedAt": now(),
        }

    def preview(self, project_id, time):
        with self.lock:
            project = self._read(project_id)
            folder = self._folder(project_id)
            candidate(project, folder, verify=False, require_mix=False)
        return render.preview(project, folder, time)

    def _queue_export(self, project_id, revision):
        with self.lock:
            project = self._edit(project_id, revision)
            folder = self._folder(project_id)
            record = candidate(project, folder)
            key = digest(encoded(record))
            if not project["accepted"] or project["accepted"]["hash"] != key:
                raise WorkflowError("Accept the current candidate before exporting.", 409)
            if project["export"] and project["export"]["status"] == "complete":
                try:
                    self.media(project_id, "video", key)
                    self.media(project_id, "captions", key)
                    self.media(project_id, "receipt", key)
                    return self._view(project)
                except WorkflowError:
                    pass
            if not render.render_available():
                raise WorkflowError("FFmpeg and ffprobe must be installed and on PATH.", 409)
            snapshot = deepcopy(project)
            project["busy"] = "rendering"
            project["export"] = {"status": "running", "candidateHash": key, "startedAt": now()}
            self._save(project)
            job = threading.Thread(
                target=self._export, args=(snapshot, folder, key, record),
                name=f"render-{project_id[:8]}",
            )
            self.jobs.add(job)
            job.start()
            return self._view(project)

    def _export(self, snapshot, folder, key, record):
        output_dir = folder / "exports"
        temporary = output_dir / f".{uuid.uuid4().hex}.mp4"
        result = {}
        try:
            output_dir.mkdir(exist_ok=True)
            details = render.export_video(snapshot, folder, temporary)
            if candidate(snapshot, folder) != record:
                raise WorkflowError("An input changed while the candidate was rendering.", 409)
            receipt = {
                "candidateHash": key, "candidate": record, "accepted": snapshot["accepted"],
                "qc": snapshot["qc"], "media": details, "videoHash": file_digest(temporary),
                "captionsTiming": snapshot["input"].get("timing", "estimated"), "createdAt": now(),
            }
            receipt["libraryArtwork"] = {
                asset["id"]: {"id": asset["libraryId"], "version": asset["libraryVersion"]}
                for asset in snapshot["assets"] if "libraryId" in asset and asset["id"] in record["artwork"]
            }
            write_file(output_dir / f"{key}.srt", subtitles(snapshot).encode("utf-8"))
            write_json(output_dir / f"{key}.json", receipt)
            temporary.replace(output_dir / f"{key}.mp4")
            result.update(status="complete", media=details)
        except (WorkflowError, render.RenderError) as error:
            result.update(status="failed", error=str(error))
        except Exception:
            result.update(status="failed", error="Bacchus could not finish writing the export.")
        finally:
            temporary.unlink(missing_ok=True)
            with self.lock:
                self.jobs.discard(threading.current_thread())
                project = self._read(snapshot["id"])
                project["busy"] = None
                project["export"].update(result, finishedAt=now())
                self._save(project)

    def media(self, project_id, kind, key):
        if not DIGEST.fullmatch(key):
            raise WorkflowError("Media not found.", 404)
        folder = self._folder(project_id)
        extensions = {"art": ("assets", ".png", "image/png"), "audio": ("audio", ".wav", "audio/wav"),
                      "video": ("exports", ".mp4", "video/mp4"), "captions": ("exports", ".srt", "text/plain; charset=utf-8"),
                      "receipt": ("exports", ".json", "application/json")}
        if kind not in extensions:
            raise WorkflowError("Media not found.", 404)
        sub_dir, suffix, content_type = extensions[kind]
        path = folder / sub_dir / (key + suffix)
        if not path.is_file() or (sub_dir == "exports" and not (path.parent / f"{key}.mp4").is_file()):
            raise WorkflowError("Media not found.", 404)
        return path, content_type
