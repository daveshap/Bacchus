"""Compile a Bacchus project into a self-contained BFG/Warm HTML5 Canvas film."""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from . import RESOURCES, layer_spec


ENGINE_DIR = RESOURCES / "engine"
DIGEST = re.compile(r"[a-f0-9]{64}")


class CanvasBuildError(ValueError):
    """Project inputs or approved artwork cannot be compiled into a Canvas film."""


BFG_SOURCES = ("maps.js", "engine.js", "figures.js", "props.js", "kit.js")
SOURCE_NAMES = ("fonts.js", "fonts-course.js", "bfg-inputs.js",
                *("bfg/" + name for name in BFG_SOURCES),
                "bfg-adapter.js", "bacchus_canvas.js", "bfg/frame-contract.js")


@lru_cache(maxsize=1)
def _engine_scripts() -> str:
    return "\n".join((ENGINE_DIR / name).read_text(encoding="utf-8") for name in SOURCE_NAMES)


@lru_cache(maxsize=1)
def runtime_hash() -> str:
    return hashlib.sha256(_engine_scripts().encode("utf-8")).hexdigest()


def _safe_script(js: str) -> str:
    return re.sub(r"</(?=script\b)", lambda _: "<\\/", js, flags=re.IGNORECASE)


def _line_tokens(line: dict, words: list[dict]) -> list[list]:
    first = line.get("firstWord")
    last = line.get("lastWord")
    if not first or not last or not words:
        return []
    tokens = []
    for wd in words[max(0, first - 1):min(len(words), last)]:
        cleaned = re.sub(r"^[^a-z0-9']+|[^a-z0-9']+$", "", str(wd.get("text", "")).lower())
        if cleaned:
            tokens.append([cleaned, round(float(wd["start"]), 4), round(float(wd.get("end", wd["start"])), 4)])
    return tokens


def _read_verified_asset(asset_id: str, assets: dict[str, dict], folder: Path) -> tuple[str, bytes]:
    asset = assets.get(asset_id)
    if not asset or not asset.get("approved"):
        raise CanvasBuildError(f"Artwork {asset_id!r} needs approval.")
    content_hash = asset.get("hash", "")
    if not isinstance(content_hash, str) or not DIGEST.fullmatch(content_hash):
        raise CanvasBuildError(f"Invalid assets content hash for {asset_id!r}.")
    path = Path(folder) / "assets" / f"{content_hash}.png"
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CanvasBuildError(f"Approved artwork {asset_id!r} could not be opened.") from error
    if hashlib.sha256(raw).hexdigest() != content_hash:
        raise CanvasBuildError(f"Artwork {asset_id!r} changed after intake.")
    return content_hash, raw


def build_film(
    project: dict,
    folder: Path | None,
    *,
    palette_dict: dict,
    preset: str,
    audio_url: str = "",
    asset_url_prefix: str = "/assets/",
    asset_url_suffix: str = ".png",
    verify_assets: bool = True,
) -> tuple[bytes, dict[str, bytes], str]:
    """Build deterministic self-contained film HTML + verified asset PNG map + build SHA-256."""
    source = project.get("input")
    plan = project.get("plan")
    if not source or not plan or not plan.get("scenes"):
        raise CanvasBuildError("A locked narration and scene plan are required to build a Canvas film.")
    duration = float(source.get("duration") or 0)
    if duration <= 0:
        raise CanvasBuildError("Film duration must be positive.")

    lines = source.get("lines") or []
    words = source.get("words") or []
    assets_by_id = {asset["id"]: asset for asset in project.get("assets", [])}
    asset_urls: dict[str, str] = {}
    asset_blobs: dict[str, bytes] = {}

    def register_art(art_id: str):
        if not art_id or art_id in asset_urls:
            return
        if verify_assets:
            if folder is None:
                raise CanvasBuildError(f"Artwork {art_id!r} requires a project folder.")
            content_hash, raw = _read_verified_asset(art_id, assets_by_id, folder)
            asset_blobs[f"{content_hash}.png"] = raw
            asset_urls[art_id] = f"{asset_url_prefix}{content_hash}{asset_url_suffix}"
        else:
            asset = assets_by_id.get(art_id)
            if asset and asset.get("hash"):
                asset_urls[art_id] = f"{asset_url_prefix}{asset['hash']}{asset_url_suffix}"

    compiled_scenes = []
    draw_hooks = []
    for index, scene in enumerate(plan["scenes"], start=1):
        start, end = layer_spec.scene_bounds(source, scene)
        if scene.get("artId"):
            register_art(scene["artId"])
        compiled_layers = []
        for layer in scene.get("layers") or []:
            if layer.get("kind") == "art" and layer.get("artId"):
                register_art(layer["artId"])
            l_start, l_settled, l_end = layer_spec.schedule(source, scene, layer)
            compiled_layers.append({
                "id": layer["id"],
                "kind": layer["kind"],
                "box": layer["box"],
                "text": layer.get("text", ""),
                "artId": layer.get("artId", ""),
                "items": layer.get("items", []),
                "unit": layer.get("unit", ""),
                "enter": layer["enter"],
                "leave": layer["leave"],
                "start": round(float(l_start), 6),
                "settled": round(float(l_settled), 6),
                "end": round(float(l_end), 6),
            })
        entry = {
            "id": f"scene-{index}",
            "index": index,
            "startLine": scene["startLine"],
            "endLine": scene["endLine"],
            "start": round(float(start), 6),
            "end": round(float(end), 6),
            "layout": scene["layout"],
            "motion": scene.get("motion", "still"),
            "title": scene.get("title", ""),
            "caption": scene.get("caption", ""),
            "artId": scene.get("artId", ""),
            "layers": compiled_layers,
        }
        if code := scene.get("canvasCode"):
            draw_hooks.append(f'{json.dumps(entry["id"])}: function(g, t, s, BFG) {{\n{code}\n}}')
        compiled_scenes.append(entry)

    style_snap = project.get("styleSnapshot") or {}
    spec = {
        "title": project.get("title") or "Bacchus Film",
        "preset": preset,
        "styleId": style_snap.get("id") or project.get("style") or preset,
        "palette": palette_dict,
        "audioUrl": audio_url,
        "timing": {
            "dur": round(duration, 6),
            "lines": [
                {
                    "id": ln.get("id", idx + 1),
                    "start": round(float(ln.get("start", 0.0)), 6),
                    "end": round(float(ln.get("end", ln.get("start", 0.0))), 6),
                    "text": ln.get("text", ""),
                    "firstWord": ln.get("firstWord", 0),
                    "lastWord": ln.get("lastWord", 0),
                    "toks": _line_tokens(ln, words),
                }
                for idx, ln in enumerate(lines)
            ],
            "words": [
                {
                    "id": wd.get("id", idx + 1),
                    "text": wd.get("text", ""),
                    "start": round(float(wd.get("start", 0.0)), 6),
                    "end": round(float(wd.get("end", wd.get("start", 0.0))), 6),
                }
                for idx, wd in enumerate(words)
            ],
        },
        "assets": asset_urls,
        "scenes": compiled_scenes,
    }

    payload = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    spec_js = "window.__BACCHUS_FILM__ = " + payload + ";"
    hooks_js = "const BACCHUS_DRAW_HOOKS = {\n" + ",\n".join(draw_hooks) + "\n};"
    combined_js = _safe_script("\n".join((spec_js, hooks_js, _engine_scripts())))
    title_html = (
        str(spec["title"])
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    html = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title_html}</title></head>"
        f"<body><script>\n{combined_js}\n</script></body></html>\n"
    )
    html_bytes = html.encode("utf-8")
    return html_bytes, asset_blobs, hashlib.sha256(html_bytes).hexdigest()


def palette_to_dict(palette) -> dict:
    return asdict(palette) if hasattr(palette, "__dataclass_fields__") else dict(palette)
