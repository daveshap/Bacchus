"""Render the approved scene plan to previews, proofs and a verified H.264 video."""

import base64
from bisect import bisect_right
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import Timer

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont, ImageOps, ImageStat

from . import browser_cdp, canvas_engine, canvas_qc, layer_spec, media_tools, mixing, video_encoding
from .storage_paths import writable_path

# Measure layouts in one coordinate space and draw at the destination's pixel density.
DESIGN_WIDTH, DESIGN_HEIGHT = 1280, 720
DELIVERY_SCALE = 3
WIDTH, HEIGHT, FPS = DESIGN_WIDTH * DELIVERY_SCALE, DESIGN_HEIGHT * DELIVERY_SCALE, 60
MAX_PREVIEW_SECONDS = 12
SAMPLE_RATE = mixing.SAMPLE_RATE
CHANNELS = mixing.CHANNELS
MOTION_DURATION = {"still": 0, "fade": 0.48, "push-in": 1.15, "slide": 0.55, "glide": 0.65}


@dataclass(frozen=True)
class Palette:
    paper: str
    ink: str
    muted: str
    accent: str
    rule: str
    matte: str
    title_font: str = "title"


PALETTES = {
    "warm-editorial": Palette("#f8efe0", "#26372d", "#505c50", "#a65235", "#d8c6ac", "#eee0ca"),
    "studio-ink": Palette("#f8f7f3", "#202120", "#494b48", "#634f89", "#b9b7b3", "#e8e6e1"),
    "night-signal": Palette("#141d2c", "#f1f6ff", "#b1c2d9", "#6ce9c1", "#405371", "#20324a", "signal"),
}
PALETTE_ROLES = frozenset(("paper", "ink", "muted", "rule", "accent"))
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

FONT_ROOTS = (
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
)
FONT_FILES = {
    "title": ("georgiab.ttf", "Georgia Bold.ttf", "DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"),
    "signal": ("segoeuib.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
    "warm-heading": ("seguisb.ttf", "segoeuib.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
    "body": ("segoeui.ttf", "Arial.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf"),
}


class RenderError(ValueError):
    """A scene cannot be drawn or its delivery could not be verified."""


def validate_style_palette(value):
    """Normalize explicit colors without changing the creator's palette choices."""
    if not isinstance(value, dict) or value.keys() - PALETTE_ROLES:
        raise RenderError("Style palette colors must use paper, ink, muted, rule or accent.")
    normalized = {}
    for role, color in value.items():
        if not isinstance(color, str) or not HEX_COLOR.fullmatch(color):
            raise RenderError(f"Style palette {role} must be a six-digit hex color such as #26372d.")
        normalized[role] = color.lower()
    return normalized


def project_visual_preset(project):
    stored = project.get("style", "warm-editorial")
    snapshot = project.get("styleSnapshot")
    preset = snapshot.get("visualPreset", stored) if snapshot else stored
    if not isinstance(preset, str) or preset not in PALETTES:
        raise RenderError(f"Unsupported project style {preset!r}.")
    return preset


def project_palette(project):
    """Resolve the colors actually rendered by the project's pinned style guide."""
    base = PALETTES[project_visual_preset(project)]
    snapshot = project.get("styleSnapshot") or {}
    overrides = validate_style_palette(snapshot.get("palette", {}))
    overrides = {role: color for role, color in overrides.items() if color != getattr(base, role)}
    if not overrides:
        return base
    if "paper" in overrides:
        paper = ImageColor.getrgb(overrides["paper"])
        ink = ImageColor.getrgb(overrides.get("ink", base.ink))
        channels = (round(.92 * background + .08 * foreground) for background, foreground in zip(paper, ink, strict=True))
        overrides["matte"] = "#" + "".join(f"{channel:02x}" for channel in channels)
    return replace(base, **overrides)


@lru_cache(maxsize=64)
def _font(role, size):
    for directory in FONT_ROOTS:
        for name in FONT_FILES[role]:
            path = directory / name
            if path.is_file():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


def _wrap(text, font, width):
    lines = []
    line = ""
    for word in text.split():
        combined = f"{line} {word}" if line else word
        if font.getlength(combined) <= width:
            line = combined
            continue
        if line:
            lines.append(line)
        line = ""
        for char in word:
            if line and font.getlength(line + char) > width:
                lines.append(line)
                line = ""
            line += char
    if line:
        lines.append(line)
    return lines


def _text_block(text, role, max_size, min_size, width, height):
    for size in range(max_size, min_size - 1, -2):
        font = _font(role, size)
        lines = _wrap(text, font, width)
        ascent, descent = font.getmetrics()
        step = ascent + descent + 2
        if len(lines) * step <= height:
            return font, lines, step
    label = "Caption" if role == "body" else "Title"
    raise RenderError(f"{label} text does not fit this layout.")


def _scaled(values, scale):
    return tuple(value * scale for value in values)


def _paint_text(draw, scene, palette, rectangle, title_size, title_height, scale, *, center=False):
    x0, y0, x1, y1 = rectangle
    width = x1 - x0
    title = _text_block(scene["title"], palette.title_font, title_size, 32, width, title_height)
    title_height_used = len(title[1]) * title[2]
    caption = scene.get("caption", "")
    gap = 18 if caption else 0
    if caption:
        body = _text_block(caption, "body", 27, 19, width, y1 - y0 - title_height_used - gap)
        body_height = len(body[1]) * body[2]
    else:
        body = None
        body_height = 0
    y = y0 + (y1 - y0 - title_height_used - gap - body_height) / 2
    x = (x0 + x1) / 2 if center else x0
    anchor = "ma" if center else "la"
    for block, color, role in ((title, palette.ink, palette.title_font), (body, palette.muted, "body")):
        if block is None:
            continue
        font, lines, step = block
        font = _font(role, font.size * scale)
        for line in lines:
            draw.text(_scaled((x, y), scale), line, font=font, fill=color, anchor=anchor)
            y += step
        y += gap


def _media_path(folder, directory, digest, suffix):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise RenderError(f"Invalid {directory} content hash.")
    return folder / directory / (digest + suffix)


def _read_asset(asset_id, assets, folder):
    asset = assets.get(asset_id)
    if not asset or not asset["approved"]:
        raise RenderError(f"Artwork {asset_id!r} needs approval.")
    path = _media_path(folder, "assets", asset["hash"], ".png")
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset["hash"]:
            raise RenderError(f"Artwork {asset_id!r} changed after intake.")
        with Image.open(BytesIO(raw), formats=["PNG"]) as image:
            return image.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as error:
        raise RenderError(f"Approved artwork {asset_id!r} could not be opened.") from error


def _paste_art(frame, art, palette, area, scale):
    x0, y0, x1, y1 = _scaled(area, scale)
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=24 * scale, fill=palette.matte,
                           outline=palette.rule, width=2 * scale)
    art = ImageOps.contain(art, (x1 - x0 - 36 * scale, y1 - y0 - 36 * scale), Image.Resampling.LANCZOS)
    frame.paste(art, ((x0 + x1 - art.width) // 2, (y0 + y1 - art.height) // 2), art)


def _draw_scene(scene, assets, folder, palette, scale):
    frame = Image.new("RGB", _scaled((DESIGN_WIDTH, DESIGN_HEIGHT), scale), palette.paper)
    draw = ImageDraw.Draw(frame)
    draw.rectangle(_scaled((44, 44, DESIGN_WIDTH - 44, DESIGN_HEIGHT - 44), scale),
                   outline=palette.rule, width=2 * scale)
    draw.rounded_rectangle(_scaled((98, 82, 162, 88), scale), radius=3 * scale, fill=palette.accent)
    art_id = scene.get("artId")
    art = _read_asset(art_id, assets, folder) if art_id else None

    if scene["layout"] == "illustration":
        if art is None:
            raise RenderError("This illustration needs approved artwork.")
        _paste_art(frame, art, palette, (230, 106, 1050, 461), scale)
        _paint_text(draw, scene, palette, (145, 478, 1135, 647), 46, 105, scale, center=True)
    elif scene["layout"] in {"title", "editorial"}:
        if art is not None:
            _paste_art(frame, art, palette, (700, 114, 1170, 610), scale)
            _paint_text(
                draw, scene, palette, (100, 116, 644, 594), 62, 294, scale,
                center=scene["layout"] == "title",
            )
        elif scene["layout"] == "title":
            _paint_text(draw, scene, palette, (158, 120, 1122, 600), 82, 260, scale, center=True)
        else:
            _paint_text(draw, scene, palette, (102, 120, 1040, 600), 76, 254, scale)
    else:
        raise RenderError(f"Unsupported layout {scene['layout']!r}.")
    return frame


def _draw_block(image, text, box, color, role="body", maximum=33, minimum=18, *, scale, center=False):
    x, y, width, height = box
    font, lines, step = _text_block(text, role, maximum, minimum, width, height)
    draw = ImageDraw.Draw(image)
    x = x + width / 2 if center else x
    y += (height - len(lines) * step) / 2
    font = _font(role, font.size * scale)
    for line in lines:
        draw.text(_scaled((x, y), scale), line, fill=color, font=font, anchor="ma" if center else "la")
        y += step


def _layered_background(scene, palette, preset, scale):
    frame = Image.new("RGB", _scaled((DESIGN_WIDTH, DESIGN_HEIGHT), scale), palette.paper)
    draw = ImageDraw.Draw(frame)
    draw.rectangle(_scaled((47, 43, DESIGN_WIDTH - 47, DESIGN_HEIGHT - 43), scale),
                   outline=palette.rule, width=2 * scale)
    draw.line(_scaled((91, 140, DESIGN_WIDTH - 91, 140), scale), fill=palette.rule, width=2 * scale)
    draw.rounded_rectangle(_scaled((92, 74, 128, 79), scale), radius=2 * scale, fill=palette.accent)
    heading = "warm-heading" if preset == "warm-editorial" else palette.title_font
    _draw_block(frame, scene["title"], (149, 65, 1039, 67), palette.ink, heading, 44, 23, scale=scale)
    caption = scene.get("caption", "")
    if caption:
        draw.line(_scaled((91, 599, DESIGN_WIDTH - 91, 599), scale), fill=palette.rule, width=2 * scale)
        _draw_block(frame, caption, (94, 606, 1092, 56), palette.muted, "body", 23, 17, scale=scale)
    return frame


def _draw_chart(tile, layer, palette, preset, scale):
    draw = ImageDraw.Draw(tile)
    width, height = tile.width // scale, tile.height // scale
    items = layer["items"]
    header = 38 if layer["text"] else 6
    row_height = (height - header - 18) // len(items)
    if width < 240 or row_height < 25:
        raise RenderError("The chart needs more room for its bars, labels and values.")
    draw.rounded_rectangle((0, 0, tile.width - 1, tile.height - 1), radius=13 * scale, fill=palette.matte,
                           outline=palette.rule, width=scale)
    if layer["text"]:
        _draw_block(tile, layer["text"], (14, 3, width - 28, 34), palette.ink, maximum=23, minimum=16, scale=scale)
    label_width, value_width = round(width * .27), round(width * .22)
    bar_left, bar_right = label_width + 24, width - value_width - 22
    values = [item["value"] for item in items]
    low, high = min(0, *values), max(0, *values)
    span = high - low or 1

    def position(value):
        return round(bar_left + (value - low) / span * (bar_right - bar_left))

    baseline = position(0)
    draw.line(_scaled((baseline, header, baseline, height - 18), scale), fill=palette.muted, width=scale)
    colors = (palette.accent, palette.muted)
    warm = PALETTES["warm-editorial"]
    if preset == "warm-editorial" and colors == (warm.accent, warm.muted):
        colors = ("#5f8377", "#a95d44", "#658095", "#a88039")
    for index, item in enumerate(items):
        top = header + row_height * index
        _draw_block(tile, item["label"], (12, top, label_width, row_height), palette.ink,
                    maximum=20, minimum=13, scale=scale)
        value = item["value"]
        label = str(int(value)) if value == int(value) else str(value)
        if layer["unit"]:
            label += f" {layer['unit']}"
        _draw_block(tile, label, (bar_right + 11, top, value_width, row_height), palette.ink,
                    maximum=20, minimum=13, scale=scale)
        if value:
            x0, x1 = sorted((baseline, position(value)))
            inset = max(4, row_height // 4)
            draw.rounded_rectangle(
                _scaled((x0, top + inset, max(x0 + 1, x1), top + row_height - inset), scale),
                radius=4 * scale, fill=colors[index % len(colors)],
            )


def _draw_flow(tile, layer, palette, scale):
    draw = ImageDraw.Draw(tile)
    width, height = tile.width // scale, tile.height // scale
    header = 42 if layer["text"] else 4
    if layer["text"]:
        _draw_block(tile, layer["text"], (4, 2, width - 8, 36), palette.ink, maximum=25, minimum=16, scale=scale)
    count = len(layer["items"])
    space = 22
    horizontal = width >= (height - header) * 1.7
    if horizontal:
        node_width = (width - 4 - (count - 1) * space) // count
        node_height = min(110, height - header - 4)
        y = header + (height - header - node_height) // 2
        boxes = [(2 + i * (node_width + space), y, node_width, node_height) for i in range(count)]
    else:
        node_width = width - 6
        node_height = (height - header - 4 - (count - 1) * space) // count
        boxes = [(3, header + i * (node_height + space), node_width, node_height) for i in range(count)]
    if node_width < 53 or node_height < 31:
        raise RenderError("The flow diagram needs more room for its steps and arrows.")
    for index, ((x, y, w, h), item) in enumerate(zip(boxes, layer["items"], strict=True)):
        draw.rounded_rectangle(
            (x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1), radius=12 * scale,
            fill=palette.matte, outline=palette.rule, width=2 * scale,
        )
        _draw_block(tile, item["label"], (x + 8, y + 3, w - 16, h - 6), palette.ink,
                    maximum=23, minimum=14, scale=scale, center=True)
        if index == count - 1:
            continue
        if horizontal:
            cx, cy = x + w + space - 4, y + h // 2
            draw.line(_scaled((x + w + 3, cy, cx, cy), scale), fill=palette.accent, width=2 * scale)
            draw.polygon(_scaled((cx, cy, cx - 6, cy - 4, cx - 6, cy + 4), scale), fill=palette.accent)
        else:
            cx, cy = x + w // 2, y + h + space - 4
            draw.line(_scaled((cx, y + h + 3, cx, cy), scale), fill=palette.accent, width=2 * scale)
            draw.polygon(_scaled((cx, cy, cx - 4, cy - 6, cx + 4, cy - 6), scale), fill=palette.accent)


CONTENT = (92, 154, DESIGN_WIDTH - 92, 584)


@dataclass
class _PaintedLayer:
    id: str
    image: Image.Image
    position: tuple[int, int]
    start: float
    settled: float
    end: float
    effect: str
    scale: int

    def phase(self, elapsed):
        if not self.start <= elapsed < self.end:
            return None
        if elapsed >= self.settled:
            return 1
        return (elapsed - self.start) / (self.settled - self.start)

    def paste(self, frame, phase):
        if phase is None or phase <= 0:
            return
        image = self.image
        x, y = self.position
        if phase >= 1:
            frame.paste(image, (x, y), image)
        elif self.effect == "fade":
            frame.paste(image, (x, y), image.getchannel("A").point(lambda alpha: round(alpha * phase)))
        elif self.effect == "wipe":
            shown = max(1, round(image.width * phase))
            visible = image.crop((0, 0, shown, image.height))
            frame.paste(visible, (x, y), visible)
        elif self.effect == "rise":
            offset = round(min(52 * self.scale, image.height // 4) * (1 - phase) ** 2)
            visible = image.crop((0, 0, image.width, image.height - offset))
            frame.paste(visible, (x, y + offset), visible)


@dataclass
class _PreparedScene:
    frame: Image.Image
    layers: tuple[_PaintedLayer, ...] = ()


def frame_at_or_after(time):
    """First integer output frame whose renderer timestamp is at or after time."""
    frame = math.ceil(time * FPS)
    if frame > 0 and (frame - 1) / FPS >= time:
        return frame - 1
    return frame + int(frame / FPS < time)


class _Composition:
    def __init__(self, project, folder, *, scale=DELIVERY_SCALE):
        self.source = project["input"]
        self.duration = self.source["duration"]
        self.folder = folder
        self.scale = scale
        self.size = _scaled((DESIGN_WIDTH, DESIGN_HEIGHT), scale)
        self.scenes = project["plan"]["scenes"]
        self.preset = project_visual_preset(project)
        self.palette = project_palette(project)
        # Include the recording's lead-in in the opening scene.
        self.positions = [0.0] + [
            self.source["lines"][scene["startLine"] - 1]["start"]
            for scene in self.scenes[1:]
        ]
        self.assets = {asset["id"]: asset for asset in project["assets"]}

    def _draw(self, index):
        prepared = self.prepare(index)
        if prepared.layers:
            return self.draw_prepared(index, 0, prepared)
        return prepared.frame

    def prepare(self, index):
        try:
            scene = self.scenes[index]
            if scene.get("canvasCode") or scene["motion"] not in ("still", "fade", "push-in") or any(
                layer["kind"] not in ("text", "art", "bar-chart", "flow")
                or layer["enter"]["effect"] not in ("show", "fade", "wipe", "rise")
                for layer in scene.get("layers", [])
            ):
                raise RenderError("This scene requires the BFG Canvas renderer.")
            if scene["layout"] == "layered":
                return self._prepare_layers(scene, self.positions[index])
            return _PreparedScene(_draw_scene(scene, self.assets, self.folder, self.palette, self.scale))
        except (RenderError, layer_spec.LayerError) as error:
            raise RenderError(f"Scene {index + 1}. {error}") from error

    def _prepare_layers(self, scene, scene_start):
        layers = []
        x0, y0, x1, y1 = CONTENT
        for layer in layer_spec.validate_layers({"input": self.source}, scene):
            kind, box = layer["kind"], layer["box"]
            x = round(x0 + box["x"] * (x1 - x0) / 100)
            y = round(y0 + box["y"] * (y1 - y0) / 100)
            width = round(box["w"] * (x1 - x0) / 100)
            height = round(box["h"] * (y1 - y0) / 100)
            tile = Image.new("RGBA", _scaled((width, height), self.scale))
            start, settled, end = layer_spec.schedule(self.source, scene, layer)
            try:
                if frame_at_or_after(settled) / FPS >= end:
                    raise RenderError("The layer leaves before any fully visible output frame.")
                if kind == "text":
                    inset = min(12, width // 5, height // 5)
                    _draw_block(tile, layer["text"], (inset, inset, width - 2 * inset, height - 2 * inset),
                                self.palette.ink, scale=self.scale)
                elif kind == "art":
                    image = _read_asset(layer["artId"], self.assets, self.folder)
                    fitted = ImageOps.contain(image, tile.size, Image.Resampling.LANCZOS)
                    tile.alpha_composite(fitted, ((tile.width - fitted.width) // 2, (tile.height - fitted.height) // 2))
                elif kind == "bar-chart":
                    _draw_chart(tile, layer, self.palette, self.preset, self.scale)
                elif kind == "flow":
                    _draw_flow(tile, layer, self.palette, self.scale)
            except RenderError as error:
                raise RenderError(f"Layer {layer['id']!r}. {error}") from error
            layers.append(_PaintedLayer(
                layer["id"], tile, _scaled((x, y), self.scale), start - scene_start, settled - scene_start,
                end - scene_start, layer["enter"]["effect"], self.scale,
            ))
        return _PreparedScene(_layered_background(scene, self.palette, self.preset, self.scale), tuple(layers))

    def visual_state(self, index, elapsed, prepared):
        if prepared.layers:
            return tuple(layer.phase(elapsed) for layer in prepared.layers)
        return min(elapsed, self.motion_length(index))

    def draw_prepared(self, index, elapsed, prepared):
        if not prepared.layers:
            return self.apply_motion(prepared.frame, index, elapsed)
        frame = prepared.frame.copy()
        state = self.visual_state(index, elapsed, prepared)
        for layer, phase in zip(prepared.layers, state, strict=True):
            layer.paste(frame, phase)
        return frame

    def asset_frame(self, index, layer_id, prepared=None):
        prepared = prepared if prepared is not None else self.prepare(index)
        if not prepared.layers:
            return prepared.frame
        selected = next((layer for layer in prepared.layers if layer.id == layer_id), None)
        if not selected:
            raise RenderError("The planned artwork layer no longer exists.")
        frame = prepared.frame.copy()
        for layer in prepared.layers:
            if layer.phase(selected.settled) is not None:
                layer.paste(frame, 1)
        return frame

    def at(self, time):
        if not math.isfinite(time):
            raise RenderError("Preview time must be finite.")
        time = min(max(0, time), self.duration)
        index = max(0, bisect_right(self.positions, time) - 1)
        elapsed = max(0, time - self.positions[index])
        return index, elapsed

    def motion_length(self, index):
        motion = self.scenes[index]["motion"]
        if motion not in MOTION_DURATION:
            raise RenderError(f"Scene {index + 1}. Unsupported motion {motion!r}.")
        end = self.positions[index + 1] if index + 1 < len(self.scenes) else self.duration
        return min(MOTION_DURATION[motion], (end - self.positions[index]) * 0.4)

    def apply_motion(self, frame, index, elapsed):
        motion = self.scenes[index]["motion"]
        length = self.motion_length(index)
        if length <= 0:
            return frame
        progress = min(1, elapsed / length)
        if motion == "fade":
            if progress < 1:
                return Image.blend(Image.new("RGB", frame.size, self.palette.paper), frame, progress)
            return frame
        eased = 1 - (1 - progress) ** 3
        width, height = frame.size
        inset = round(width * eased * 0.012)
        if inset:
            top = inset * height / width
            return frame.resize(
                frame.size, Image.Resampling.BICUBIC,
                box=(inset, top, width - inset, height - top),
            )
        return frame


def render_backend() -> str:
    requested = os.environ.get("BACCHUS_RENDER_BACKEND", "canvas").strip().lower()
    if requested == "pillow":
        return "pillow"
    return "canvas"


def browser_available() -> bool:
    return browser_cdp.browser_available()


def browser_label() -> str | None:
    return browser_cdp.browser_label()


def build_canvas_film(project, folder: Path | None, *, audio_url: str = "", asset_url_prefix: str = "/assets/",
                      asset_url_suffix: str = ".png", verify_assets: bool = True):
    try:
        preset = project_visual_preset(project)
        palette = canvas_engine.palette_to_dict(project_palette(project))
        return canvas_engine.build_film(
            project, folder, palette_dict=palette, preset=preset,
            audio_url=audio_url, asset_url_prefix=asset_url_prefix,
            asset_url_suffix=asset_url_suffix, verify_assets=verify_assets,
        )
    except canvas_engine.CanvasBuildError as error:
        raise RenderError(str(error)) from error


def canvas_qc_report(project, folder: Path, *, run_jumps: bool = True, jumps_fps: int = 30) -> dict:
    if render_backend() != "canvas":
        return {"findings": [], "summary": None, "buildHash": None}
    try:
        preset = project_visual_preset(project)
        palette = canvas_engine.palette_to_dict(project_palette(project))
        return canvas_qc.run_canvas_qc(
            project, folder, palette_dict=palette, preset=preset,
            run_jumps=run_jumps, jumps_fps=jumps_fps,
        )
    except (canvas_engine.CanvasBuildError, browser_cdp.BrowserError) as error:
        raise RenderError(str(error)) from error


def preview(project, folder: Path, time: float, *, width: int | None = None) -> bytes:
    if not math.isfinite(time):
        raise RenderError("Preview time must be finite.")
    if render_backend() == "canvas":
        scale = (width / 1920.0) if width else 2.0
        html_bytes, assets, _ = build_canvas_film(project, folder)
        try:
            raw_png = browser_cdp.render_still_png(html_bytes, assets, float(time), scale=scale)
        except browser_cdp.BrowserError as error:
            raise RenderError(str(error)) from error
        # Normalize Chrome RGBA PNG to deterministic RGB PNG so byte comparisons & RGB callers match identically.
        with Image.open(BytesIO(raw_png)) as frame:
            rgb = frame.convert("RGB")
            with BytesIO() as out:
                rgb.save(out, format="PNG", compress_level=3)
                return out.getvalue()
    scale = max(1, round((width or WIDTH) / DESIGN_WIDTH)) if width else DELIVERY_SCALE
    composition = _Composition(project, folder, scale=scale)
    index, elapsed = composition.at(time)
    frame = composition.draw_prepared(index, elapsed, composition.prepare(index))
    with BytesIO() as buffer:
        frame.save(buffer, format="PNG")
        return buffer.getvalue()


def layout_findings(project, folder: Path) -> list[str]:
    """Return concrete draw errors from every scene for the deterministic QC gate."""
    composition = _Composition(project, folder)
    findings = []
    if render_backend() == "canvas":
        for index, scene in enumerate(composition.scenes):
            try:
                composition.motion_length(index)
                if scene["layout"] == "layered":
                    for layer in layer_spec.validate_layers({"input": composition.source}, scene):
                        _, settled, end = layer_spec.schedule(composition.source, scene, layer)
                        if frame_at_or_after(settled) / FPS >= end:
                            raise RenderError(f"Scene {index + 1}. Layer {layer['id']!r}. The layer leaves before any fully visible output frame.")
            except (RenderError, layer_spec.LayerError) as error:
                findings.append(str(error))
        if not findings:
            try:
                build_canvas_film(project, folder)
            except RenderError as error:
                findings.append(str(error))
        return findings
    for index in range(len(composition.scenes)):
        try:
            composition.motion_length(index)
            composition._draw(index)
        except RenderError as error:
            findings.append(str(error))
    return findings


def render_available() -> bool:
    return media_tools.available() and (render_backend() == "pillow" or browser_available())


def validate_music_regions(project, regions):
    try:
        return mixing.validate_music_regions(project, regions)
    except mixing.MixError as error:
        raise RenderError(str(error)) from error


def verify_mix(folder, metadata, narration_duration):
    try:
        return mixing.verify_mix(folder, metadata, _frame_count(narration_duration), FPS)
    except mixing.MixError as error:
        raise RenderError(str(error)) from error


def _video_frames(composition, frame_count, first_frame=0):
    last_index = None
    last_phase = None
    for number in range(first_frame, first_frame + frame_count):
        index, elapsed = composition.at(number / FPS)
        if index != last_index:
            prepared = composition.prepare(index)
            last_index = index
            last_phase = None
        phase = composition.visual_state(index, elapsed, prepared)
        if phase != last_phase:
            data = composition.draw_prepared(index, elapsed, prepared).tobytes()
            last_phase = phase
        yield data


class _EncodingFailure(RenderError):
    pass


def _encode(project, folder, composition, frame_count, scratch, encoder, approved_mix, *, first_frame=0, workers=None):
    output = scratch / f"{encoder.name}.mp4"
    log = scratch / f"{encoder.name}.log"
    duration = frame_count / FPS
    mix = project.get("mix") or {}
    if approved_mix is None and (project.get("audio") or mix.get("musicMode") == "track" or mix.get("effects")):
        raise RenderError("Prepare and approve the soundtrack before rendering.")
    audio_options = []
    if approved_mix is not None:
        audio_path = verify_mix(folder, approved_mix, composition.duration)
        audio_options = [
            "-f", "wav", "-i", str(audio_path), "-map", "1:a:0", "-c:a", "aac", "-b:a", mixing.AAC_BITRATE,
        ]
        if first_frame or frame_count != _frame_count(composition.duration):
            first_sample = first_frame * SAMPLE_RATE // FPS
            end_sample = (first_frame + frame_count) * SAMPLE_RATE // FPS
            audio_options.extend(("-af", f"atrim=start_sample={first_sample}:end_sample={end_sample},asetpts=PTS-STARTPTS"))
    backend = render_backend()
    video_in = video_encoding.png_input(FPS) if backend == "canvas" else video_encoding.raw_input(*composition.size, FPS)
    command = [
        encoder.executable or media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-filter_threads", "4", *encoder.input_options, *video_in,
        *audio_options, "-map", "0:v:0", *video_encoding.encoding_options(encoder),
        "-movflags", "+faststart+write_colr",
        str(output),
    ]
    if backend == "canvas":
        html_bytes, assets, _ = build_canvas_film(project, folder)
        canvas_scale = composition.size[0] / 1920.0
        worker_count = workers if workers is not None else (browser_cdp.DEFAULT_WORKERS if frame_count >= 24 else 1)
        with log.open("w+b") as errors:
            try:
                return_code = browser_cdp.render_frames_to_ffmpeg(
                    html_bytes, assets, scale=canvas_scale, fps=FPS, first_frame=first_frame,
                    frame_count=frame_count, ffmpeg_command=command, stderr_file=errors,
                    workers=worker_count, timeout_seconds=duration * 18 + 120,
                )
            except browser_cdp.BrowserError as error:
                raise _EncodingFailure(f"Canvas renderer failed: {error}") from error
            if return_code:
                errors.seek(0)
                detail = errors.read(1200).decode("utf-8", "replace").strip()
                raise _EncodingFailure(f"FFmpeg could not encode the video. {detail or 'The encoder exited.'}")
        return output, bool(audio_options)

    with log.open("w+b") as errors:
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors,
                                       **media_tools.process_options())
        except OSError as error:
            raise RenderError("FFmpeg could not start.") from error
        watchdog = Timer(duration * 12 + 90, process.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            with process.stdin:
                for frame in _video_frames(composition, frame_count, first_frame):
                    process.stdin.write(frame)
            return_code = process.wait()
        except BrokenPipeError:
            return_code = process.wait()
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()
        if return_code:
            errors.seek(0)
            detail = errors.read(1200).decode("utf-8", "replace").strip()
            raise _EncodingFailure(f"FFmpeg could not encode the video. {detail or 'The encoder exited.'}")
    return output, bool(audio_options)


def _probe(output):
    result = subprocess.run(
        [media_tools.command("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
        check=True, capture_output=True, text=True, timeout=20, **media_tools.process_options(),
    )
    return json.loads(result.stdout)


def _inspect(output, frame_count, has_audio, size=(WIDTH, HEIGHT)):
    try:
        probe = _probe(output)
        video = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
        audio = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
        duration = float(probe["format"]["duration"])
        valid = (
            len(video) == 1 and len(audio) == int(has_audio)
            and (video[0]["width"], video[0]["height"], video[0]["codec_name"]) == (*size, "h264")
            and video[0].get("pix_fmt") == "yuv420p"
            and all(video[0].get(field) == value for field, value in video_encoding.COLOR_METADATA.items())
            and video[0]["avg_frame_rate"] == f"{FPS}/1"
            and int(video[0]["nb_frames"]) == frame_count
            and abs(duration - frame_count / FPS) < 0.08
            and (not has_audio or (
                audio[0]["codec_name"] == "aac"
                and audio[0]["sample_rate"] == str(SAMPLE_RATE) and audio[0]["channels"] == CHANNELS
                and abs(float(audio[0]["duration"]) - duration) < 0.08
            ))
        )
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
        raise RenderError("FFprobe could not verify the encoded video.") from error
    if not valid:
        raise RenderError("The encoded file did not match the approved video and audio settings.")
    return {"width": size[0], "height": size[1], "fps": FPS, "frames": frame_count,
            "duration": duration, "codec": "h264", "audio": "aac" if has_audio else None,
            "audioSampleRate": SAMPLE_RATE if has_audio else None,
            "audioChannels": CHANNELS if has_audio else None,
            "color": dict(video_encoding.COLOR_METADATA)}


def _frame_count(duration):
    if not math.isfinite(duration) or duration <= 0:
        raise RenderError("An export needs a positive duration.")
    return math.ceil(duration * FPS)


def _decode(output, frame_count, has_audio):
    """Decode the complete delivery and require an output frame for every authored frame."""
    streams = ["-map", "0:v:0"]
    if has_audio:
        streams += ["-map", "0:a:0"]
    command = [
        media_tools.command("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-nostats",
        "-xerror", "-err_detect", "explode", "-i", str(output), *streams,
        "-fps_mode", "passthrough", "-abort_on", "empty_output_stream", "-progress", "pipe:1", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True,
                                timeout=frame_count / FPS * 12 + 90,
                                **media_tools.process_options())
    except subprocess.CalledProcessError as error:
        details = (error.stderr or "").strip().splitlines()
        suffix = f" {details[0][:240]}" if details else ""
        raise RenderError(f"The delivered video or soundtrack could not be decoded.{suffix}") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise RenderError("FFmpeg could not finish decoding the delivery.") from error
    frames = re.findall(r"^frame=\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    if not frames or int(frames[-1]) != frame_count or "progress=end" not in result.stdout.splitlines():
        raise RenderError("The decoded delivery does not contain every approved picture frame.")


def _encode_verified(project, folder, composition, frame_count, scratch, encoder, approved_mix, *, first_frame=0):
    output, has_audio = _encode(
        project, folder, composition, frame_count, scratch, encoder, approved_mix, first_frame=first_frame,
    )
    try:
        media = _inspect(output, frame_count, has_audio, composition.size)
        _decode(output, frame_count, has_audio)
        media.update(decodedFrames=frame_count, decodedAudio=has_audio)
        return output, media
    except RenderError as error:
        raise _EncodingFailure(str(error)) from error


def _verify_canvas_pictures(project, folder, composition, path, count):
    """Compare decoded pictures with fresh renders of the approved, full-resolution BFG film."""
    html, assets, build_hash = build_canvas_film(project, folder)
    positions = {0, count // 4, count // 2, 3 * count // 4, count - 1}
    if len(composition.positions) > 1:
        boundary = frame_at_or_after(composition.positions[len(composition.positions) // 2])
        positions.update((max(0, boundary - 1), min(count - 1, boundary + 1)))
    width, height = 320, 180

    def compare(browser, session, _ready, errors):
        samples = []
        for index in sorted(positions):
            # Seek to an integral second, then select the exact presentation-order frame.
            second, within = divmod(index, FPS)
            command = [
                media_tools.command("ffmpeg"), "-v", "error", "-nostdin", "-ss", str(second),
                "-i", str(path), "-map", "0:v:0", "-an", "-vf",
                f"select=eq(n\\,{within}),scale={width}:{height}:flags=area,format=rgb24",
                "-frames:v", "1", "-f", "rawvideo", "pipe:1",
            ]
            try:
                decoded = subprocess.run(command, check=True, capture_output=True, timeout=60,
                                         **media_tools.process_options()).stdout
                actual = Image.frombytes("RGB", (width, height), decoded)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                raise RenderError(f"Could not verify decoded picture {index}.") from error
            data = browser.eval_js(session, f"window.__film.render({index / FPS!r}); window.__film.frameB64()")
            if errors:
                raise RenderError(f"BFG reference frame {index} failed: {errors[0]}")
            with Image.open(BytesIO(base64.b64decode(data))) as image:
                expected = image.convert("RGB").resize((width, height), Image.Resampling.BOX)
            delta = ImageChops.difference(expected, actual)
            mean = sum(ImageStat.Stat(delta).mean) / 3
            # Compare the worst local area as well; an omitted diagram can occupy a small part of a shot.
            worst = max(channel[1] for channel in delta.reduce(16).getextrema())
            if mean > 5 or worst > 45:
                raise RenderError(f"Encoded picture {index} differs from the approved BFG film "
                                  f"(mean channel error {mean:.2f}, worst area {worst}).")
            samples.append({"frame": index, "meanChannelError": round(mean, 3), "maxTileChannelError": worst})
        return samples

    try:
        samples = browser_cdp.run_with_film(html, assets, composition.size[0] / 1920, compare)
    except browser_cdp.BrowserError as error:
        raise RenderError(str(error)) from error
    return {"buildHash": build_hash, "frames": samples, "sampleWidth": width, "sampleHeight": height}


def export_video(project, folder: Path, destination: Path, *, approved_mix=None, encoder_mode="auto") -> dict:
    if not render_available():
        raise RenderError("Video export needs FFmpeg and ffprobe, plus a Chromium browser for the BFG Canvas renderer.")
    composition = _Composition(project, folder)
    frame_count = _frame_count(composition.duration)
    destination = writable_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoder, fallback = video_encoding.choose(encoder_mode, WIDTH, HEIGHT, FPS)
    with TemporaryDirectory(prefix=".bacchus-render-", dir=destination.parent) as work:
        args = (project, folder, composition, frame_count, Path(work))
        try:
            output, media = _encode_verified(*args, encoder, approved_mix)
        except _EncodingFailure:
            if encoder == video_encoding.CPU:
                raise
            fallback = f"{encoder.label} failed the export or its output checks. Bacchus retried on the CPU."
            encoder = video_encoding.CPU
            output, media = _encode_verified(*args, encoder, approved_mix)
        if render_backend() == "canvas":
            media["visualVerification"] = _verify_canvas_pictures(project, folder, composition, output, frame_count)
        if approved_mix is not None:
            try:
                media["audioLevels"] = mixing.verify_delivery(output, media["duration"], approved_mix)
            except mixing.MixError as error:
                raise RenderError(str(error)) from error
        output.replace(destination)
    media.update(encoder=encoder.name, hardwareEncoding=encoder != video_encoding.CPU,
                 encoderMode=encoder_mode, encoderFallback=fallback, renderer=render_backend())
    return media


def export_motion_preview(project, folder: Path, destination: Path, start: float, end: float, *, approved_mix=None) -> dict:
    """Export a bounded review clip on the same frame and audio clocks as the finished film."""
    if not render_available():
        raise RenderError("Motion preview needs FFmpeg and ffprobe, plus a Chromium browser for the BFG Canvas renderer.")
    composition = _Composition(project, folder, scale=1)
    if (
        any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end))
        or not 0 <= start < end <= composition.duration
        or end - start > MAX_PREVIEW_SECONDS + 1e-9
    ):
        raise RenderError(f"Select a motion preview range of up to {MAX_PREVIEW_SECONDS} seconds within the cut.")
    first = min(math.floor(start * FPS), _frame_count(composition.duration) - 1)
    last = min(frame_at_or_after(end), first + MAX_PREVIEW_SECONDS * FPS)
    count = max(1, last - first)
    destination = writable_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".bacchus-render-", dir=destination.parent) as work:
        output, media = _encode_verified(
            project, folder, composition, count, Path(work), video_encoding.CPU, approved_mix, first_frame=first,
        )
        output.replace(destination)
    return {**media, "start": first / FPS, "end": (first + count) / FPS}


def export_mix(project, folder: Path, destination: Path) -> dict:
    """Prepare the PCM soundtrack that the operator previews and approves."""
    try:
        frame_count = _frame_count(project["input"]["duration"])
        return mixing.prepare_mix(project, folder, destination, frame_count, FPS)
    except mixing.MixError as error:
        raise RenderError(str(error)) from error
    except OSError as error:
        raise RenderError("The prepared soundtrack could not be saved.") from error
