"""Stable review check IDs and visual evidence for planned uses and animation."""

import base64
from dataclasses import asdict
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from . import ENGINE_VERSION, browser_cdp, canvas_engine, canvas_qc, layer_spec, render
from .render import RenderError


EVIDENCE_VERSION = 4
SHEET_BACKGROUND = "#f5f4ef"
SHEET_INK = "#222b26"
SHEET_SECONDARY = "#44514a"
MAX_PROOF_PNG_BYTES = 4_500_000
REVIEW_FRAME_WIDTH = 1280
REVIEW_FRAME_HEIGHT = 720


def _check_id(kind, inputs):
    backend = render.render_backend()
    encoded = json.dumps(
        [EVIDENCE_VERSION, kind, ENGINE_VERSION, backend,
         canvas_engine.runtime_hash() if backend == "canvas" else None, inputs],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scenes(project):
    return (project.get("plan") or {}).get("scenes", [])


def _style(project):
    style = {
        "id": render.project_visual_preset(project),
        "palette": asdict(render.project_palette(project)),
        "size": [render.WIDTH, render.HEIGHT],
    }
    snapshot = project.get("styleSnapshot")
    if snapshot:
        style["guide"] = {"id": snapshot["id"], "revision": snapshot["revision"]}
    return style


def _visual(scene, assets):
    asset_id = scene.get("artId", "")
    visual = {
        "layout": scene["layout"],
        "title": scene["title"],
        "caption": scene.get("caption", ""),
        "asset": [asset_id, assets.get(asset_id, {}).get("hash")],
        "brief": scene.get("artBrief", ""),
    }
    if scene.get("canvasCode"):
        visual["canvasCode"] = hashlib.sha256(scene["canvasCode"].encode("utf-8")).hexdigest()
    if scene.get("layers"):
        visual["layers"] = [
            {"layer": layer, "contentHash": assets.get(layer["artId"], {}).get("hash")}
            for layer in scene["layers"]
        ]
    return visual


def _layer_times(source, scene):
    return {layer["id"]: layer_spec.schedule(source, scene, layer) for layer in scene.get("layers", [])}


def asset_checks(project) -> list[dict]:
    """Describe the static proof of each planned use of artwork."""
    scenes = _scenes(project)
    if not scenes:
        return []
    style = _style(project)
    assets = {asset["id"]: asset for asset in project["assets"]}
    source = project.get("input") or {}
    lines = [line["text"] for line in source.get("lines", [])]
    checks = []
    for index, scene in enumerate(scenes, start=1):
        visual = _visual(scene, assets)
        bounds = [scene["startLine"], scene["endLine"]]
        words = lines[bounds[0] - 1:bounds[1]]
        times = _layer_times(source, scene)
        for use in layer_spec.scene_art(scene):
            layer_id = use["layerId"]
            overlap = []
            if layer_id:
                settled = times[layer_id][1]
                overlap = [name for name, (start, _, end) in times.items() if start <= settled < end]
            checks.append({
                "id": _check_id("asset", [style, bounds, visual, layer_id, overlap, source.get("intention"), words]),
                "scene": index, "assetId": use["artId"], "title": scene["title"], "layerId": layer_id,
            })
    return checks


def _samples(composition, index, lines):
    start = composition.positions[index]
    end = composition.positions[index + 1] if index + 1 < len(composition.scenes) else composition.duration
    first = render.frame_at_or_after(start)
    last = min(render.frame_at_or_after(end), render._frame_count(composition.duration)) - 1
    if first > last:
        return []

    samples = {}

    def add(frame, label):
        samples.setdefault(frame, []).append(label)

    def within_scene(time):
        return min(last, max(first, render.frame_at_or_after(time)))

    if first:
        add(first - 1, "Before entrance")
    add(first, "Entrance")
    motion = composition.motion_length(index)
    if motion:
        add(within_scene(start + motion / 2), "Motion")
        add(within_scene(start + motion), "Settled")
    for name, (enter, settle, leave) in _layer_times(composition.source, composition.scenes[index]).items():
        enter_frame = within_scene(enter)
        if enter_frame > first:
            add(enter_frame - 1, f"{name} before")
        add(enter_frame, f"{name} enters")
        if settle > enter:
            add(within_scene((enter + settle) / 2), f"{name} moves")
            add(within_scene(settle), f"{name} settles")
        if leave < end:
            add(within_scene(leave), f"{name} leaves")
    cue = lines[(len(lines) - 1) // 2]
    add(within_scene(cue["start"]), f"Cue {cue['id']} starts")
    add(within_scene((cue["start"] + cue["end"]) / 2), f"Cue {cue['id']}")
    add(last, "Exit")
    if index + 1 < len(composition.scenes) and (last + 1) / render.FPS < composition.duration:
        add(last + 1, "Next entrance")
    return [
        {"time": frame / render.FPS, "label": " · ".join(labels)}
        for frame, labels in sorted(samples.items())
    ]


def animation_checks(project) -> list[dict]:
    """Describe what to inspect for each scene, on the actual output frame grid."""
    if not project.get("input") or not _scenes(project):
        return []
    timeline = render._Composition(project, None)
    style = _style(project)
    lines = project["input"]["lines"]
    visual = [
        {
            "scene": _visual(scene, timeline.assets),
            "motion": scene["motion"],
            "start": start,
            "end": end,
            "layerTimes": _layer_times(project["input"], scene),
        }
        for scene, start, end in zip(
            timeline.scenes, timeline.positions, [*timeline.positions[1:], timeline.duration], strict=True,
        )
    ]

    checks = []
    for index, scene in enumerate(timeline.scenes):
        segment = lines[scene["startLine"] - 1:scene["endLine"]]
        samples = _samples(timeline, index, segment)
        bounds = visual[index]
        visible_scenes = sorted({index, *(timeline.at(sample["time"])[0] for sample in samples)})
        deps = [style, render.FPS, project["input"].get("intention"), [visual[i] for i in visible_scenes], segment, samples]
        checks.append({
            "id": _check_id("animation", deps),
            "scene": index + 1,
            "title": scene["title"],
            "start": bounds["start"],
            "end": bounds["end"],
            "samples": samples,
        })
    return checks


def _current_check(check, available, label):
    identifier = check.get("id") if isinstance(check, dict) else None
    for candidate in available:
        if candidate["id"] == identifier:
            return candidate
    raise RenderError(f"This {label} proof no longer matches the current project.")


def _bounded_png(image: Image.Image, max_bytes: int = MAX_PROOF_PNG_BYTES) -> bytes:
    """Encode PNG cleanly, downscaling only if needed to stay safely below API byte limits."""
    current = image
    for _ in range(5):
        with BytesIO() as buffer:
            current.save(buffer, format="PNG", optimize=True)
            data = buffer.getvalue()
        if len(data) <= max_bytes:
            return data
        new_w = max(640, int(current.width * 0.8))
        new_h = max(360, int(current.height * 0.8))
        current = current.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return data


def _text(draw, position, text, size=23, *, muted=False, width=None):
    font = render._font("body", size)
    text = " ".join(text.split())
    if width is not None and font.getlength(text) > width:
        while text and font.getlength(text + "…") > width:
            text = text[:-1]
        text += "…"
    draw.text(position, text, font=font, fill=SHEET_SECONDARY if muted else SHEET_INK)


def _reference_panel(art, size):
    panel = Image.new("RGB", size, "#e9e8e4")
    draw = ImageDraw.Draw(panel)
    step = 22
    for row, y in enumerate(range(0, size[1], step)):
        for x in range((row % 2) * step, size[0], step * 2):
            draw.rectangle((x, y, x + step - 1, y + step - 1), fill="#c9cbc6")
    reference = ImageOps.contain(art, (size[0] - 24, size[1] - 24), Image.Resampling.LANCZOS)
    panel.paste(reference, ((size[0] - reference.width) // 2, (size[1] - reference.height) // 2), reference)
    return panel


def asset_proof_png(project, folder: Path, check) -> bytes:
    """Render an HD (1280x720) static asset-use proof alongside the reference artwork."""
    selected = _current_check(check, asset_checks(project), "artwork")
    assets = {asset["id"]: asset for asset in project["assets"]}
    asset_id = selected["assetId"]
    art = render._read_asset(asset_id, assets, folder)
    index = selected["scene"] - 1
    scene = project["plan"]["scenes"][index]
    layer_id = selected["layerId"]
    target_layer = next((item for item in scene.get("layers", []) if item["id"] == layer_id), None)
    animated = target_layer is not None and float(target_layer["enter"]["duration"]) > 0

    phase_tiles: list[tuple[str, Image.Image]] = []
    if render.render_backend() == "canvas":
        html_bytes, asset_blobs, _ = render.build_canvas_film(project, folder)
        scale = REVIEW_FRAME_WIDTH / 1920.0

        def _capture(browser, session_id, _ready, page_errors):
            b64 = browser.eval_js(
                session_id,
                f"(() => {{ window.__film.renderSceneStatic({index}, {json.dumps(layer_id)}, null); "
                "return window.__film.frameB64(); })()",
            )
            phases = []
            if animated:
                for phase_val, label in ((0.0, "Before"), (0.5, "During"), (1.0, "Settled")):
                    pb64 = browser.eval_js(
                        session_id,
                        f"(() => {{ window.__film.renderSceneStatic({index}, {json.dumps(layer_id)}, {phase_val}); "
                        "return window.__film.frameB64(); })()",
                    )
                    phases.append((label, base64.b64decode(pb64)))
            if page_errors:
                raise browser_cdp.BrowserError("; ".join(page_errors[:5]))
            return base64.b64decode(b64), phases

        try:
            frame_bytes, raw_phases = browser_cdp.run_with_film(html_bytes, asset_blobs, scale, _capture)
        except browser_cdp.BrowserError as error:
            raise RenderError(str(error)) from error
        with Image.open(BytesIO(frame_bytes)) as img:
            frame = img.convert("RGB")
        for label, raw in raw_phases:
            with Image.open(BytesIO(raw)) as img:
                phase_tiles.append((label, img.convert("RGB").resize((448, 252), Image.Resampling.LANCZOS)))
    else:
        composition = render._Composition(project, folder, scale=1)
        prepared = composition.prepare(index)
        frame = composition.asset_frame(index, layer_id, prepared)
        if animated:
            layer_obj = next((item for item in prepared.layers if item.id == layer_id), None)
            if layer_obj is not None:
                for phase_val, label in ((0, "Before"), (0.5, "During"), (1, "Settled")):
                    sample = prepared.frame.copy()
                    layer_obj.paste(sample, phase_val)
                    phase_tiles.append((label, sample.resize((448, 252), Image.Resampling.LANCZOS)))

    reference_width, margin, header = 380, 24, 88
    sheet = Image.new(
        "RGB", (
            frame.width + reference_width + margin * 3,
            frame.height + header + margin + (338 if phase_tiles else 0),
        ),
        SHEET_BACKGROUND,
    )
    draw = ImageDraw.Draw(sheet)
    _text(draw, (margin, 18), selected["title"], 27, width=sheet.width - 2 * margin)
    _text(draw, (margin, 53), "Planned composition", 19, muted=True)
    _text(draw, (frame.width + margin * 2, 53), "Reference artwork", 19, muted=True)
    sheet.paste(frame, (margin, header))
    sheet.paste(
        _reference_panel(art, (reference_width, frame.height)),
        (frame.width + margin * 2, header),
    )
    if phase_tiles:
        y = header + frame.height + 26
        _text(draw, (margin, y), f"Isolated entrance: {layer_id}", 20)
        for step_idx, (label, tile) in enumerate(phase_tiles):
            x = margin + 471 * step_idx
            sheet.paste(tile, (x, y + 32))
            _text(draw, (x, y + 288), label, 18, muted=True)
    return _bounded_png(sheet)


def _timecode(seconds):
    total = round(seconds * 1000)
    minutes, remainder = divmod(total, 60_000)
    return f"{minutes:02d}:{remainder / 1000:06.3f}"


def animation_proofs_batch(project, folder: Path, checks: list[dict]) -> dict[str, bytes]:
    """Render multiple animation contact-sheet PNGs in a single Chrome Canvas session."""
    if not checks:
        return {}
    available = {c["id"]: c for c in animation_checks(project)}
    validated = []
    for check in checks:
        selected = available.get(check.get("id"))
        if not selected:
            raise RenderError("This animation proof no longer matches the current project.")
        if not selected["samples"]:
            raise RenderError(f"Scene {selected['scene']}. This scene is too short to appear in the exported video.")
        validated.append(selected)
    if render.render_backend() == "canvas":
        try:
            preset = render.project_visual_preset(project)
            palette = asdict(render.project_palette(project))
            raw_map = canvas_qc.render_animation_proofs_batch(
                project, folder, validated, palette_dict=palette, preset=preset,
            )
            bounded = {}
            for cid, data in raw_map.items():
                with Image.open(BytesIO(data)) as img:
                    if img.mode == "RGB" and len(data) <= MAX_PROOF_PNG_BYTES:
                        bounded[cid] = data
                    else:
                        bounded[cid] = _bounded_png(img.convert("RGB"))
            return bounded
        except (browser_cdp.BrowserError, ValueError) as error:
            raise RenderError(str(error)) from error
    return {check["id"]: animation_proof_png(project, folder, check) for check in validated}


def animation_proof_png(project, folder: Path, check) -> bytes:
    selected = _current_check(check, animation_checks(project), "animation")
    if not selected["samples"]:
        raise RenderError(f"Scene {selected['scene']}. This scene is too short to appear in the exported video.")
    if render.render_backend() == "canvas":
        return animation_proofs_batch(project, folder, [selected])[selected["id"]]
    # Pillow fallback: render directly at review scale=1 (1280x720) instead of 4K
    composition = render._Composition(project, folder, scale=1)
    detailed = len(selected["samples"]) > 10
    width, height = (544, 306) if detailed else (768, 432)
    margin, header, caption = 20, 64, 39
    columns = min(3 if detailed else 2, len(selected["samples"]))
    rows = math.ceil(len(selected["samples"]) / columns)
    sheet = Image.new(
        "RGB", (columns * width + (columns + 1) * margin, header + rows * (height + caption + margin)),
        SHEET_BACKGROUND,
    )
    draw = ImageDraw.Draw(sheet)
    _text(draw, (margin, 18), selected["title"], 27, width=sheet.width - 2 * margin)
    previous_index = None
    for tile, sample in enumerate(selected["samples"]):
        row, column = divmod(tile, columns)
        x = margin + column * (width + margin)
        y = header + row * (height + caption + margin)
        index, elapsed = composition.at(sample["time"])
        if index != previous_index:
            prepared = composition.prepare(index)
            previous_index = index
        frame = composition.draw_prepared(index, elapsed, prepared)
        sheet.paste(frame.resize((width, height), Image.Resampling.LANCZOS), (x, y))
        _text(
            draw, (x, y + height + 7), f"{_timecode(sample['time'])}  {sample['label']}", 20,
            muted=True, width=width,
        )
    return _bounded_png(sheet)
