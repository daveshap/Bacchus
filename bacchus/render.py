"""Render the same constrained scene plan to a still or an H.264 video."""

from bisect import bisect_right
from dataclasses import dataclass
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

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import media_tools

WIDTH, HEIGHT, FPS = 1280, 720, 24
SAMPLE_RATE = 48_000
CHANNELS = 2
MOTION_DURATION = {"still": 0, "fade": 0.48, "push-in": 1.15}


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
    "body": ("segoeui.ttf", "Arial.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf"),
}


class RenderError(ValueError):
    """A scene cannot be drawn or its delivery could not be verified."""


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


def _paint_text(draw, scene, palette, rectangle, title_size, title_height, *, center=False):
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
    for block, color in ((title, palette.ink), (body, palette.muted)):
        if block is None:
            continue
        font, lines, step = block
        for line in lines:
            draw.text((x, y), line, font=font, fill=color, anchor=anchor)
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


def _paste_art(frame, art, palette, area):
    x0, y0, x1, y1 = area
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle(area, radius=24, fill=palette.matte, outline=palette.rule, width=2)
    art = ImageOps.contain(art, (x1 - x0 - 36, y1 - y0 - 36), Image.Resampling.LANCZOS)
    frame.paste(art, ((x0 + x1 - art.width) // 2, (y0 + y1 - art.height) // 2), art)


def _draw_scene(scene, assets, folder, palette):
    frame = Image.new("RGB", (WIDTH, HEIGHT), palette.paper)
    draw = ImageDraw.Draw(frame)
    draw.rectangle((44, 44, WIDTH - 44, HEIGHT - 44), outline=palette.rule, width=2)
    draw.rounded_rectangle((98, 82, 162, 88), radius=3, fill=palette.accent)
    art_id = scene.get("artId")
    art = _read_asset(art_id, assets, folder) if art_id else None

    if scene["layout"] == "illustration":
        if art is None:
            raise RenderError("This illustration needs approved artwork.")
        _paste_art(frame, art, palette, (230, 106, 1050, 461))
        _paint_text(draw, scene, palette, (145, 478, 1135, 647), 46, 105, center=True)
    elif scene["layout"] in {"title", "editorial"}:
        if art is not None:
            _paste_art(frame, art, palette, (700, 114, 1170, 610))
            _paint_text(
                draw, scene, palette, (100, 116, 644, 594), 62, 294,
                center=scene["layout"] == "title",
            )
        elif scene["layout"] == "title":
            _paint_text(draw, scene, palette, (158, 120, 1122, 600), 82, 260, center=True)
        else:
            _paint_text(draw, scene, palette, (102, 120, 1040, 600), 76, 254)
    else:
        raise RenderError(f"Unsupported layout {scene['layout']!r}.")
    return frame


class _Composition:
    def __init__(self, project, folder):
        self.duration = project["input"]["duration"]
        self.folder = folder
        self.scenes = project["plan"]["scenes"]
        style = project.get("style", "warm-editorial")
        if style not in PALETTES:
            raise RenderError(f"Unsupported project style {style!r}.")
        self.palette = PALETTES[style]
        # Include the recording's lead-in in the opening scene.
        self.positions = [0.0] + [
            project["input"]["lines"][scene["startLine"] - 1]["start"]
            for scene in self.scenes[1:]
        ]
        self.assets = {asset["id"]: asset for asset in project["assets"]}

    def _draw(self, index):
        try:
            return _draw_scene(self.scenes[index], self.assets, self.folder, self.palette)
        except RenderError as error:
            raise RenderError(f"Scene {index + 1}. {error}") from error

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
        inset = round(WIDTH * eased * 0.012)
        if inset:
            top = inset * HEIGHT / WIDTH
            return frame.resize(
                (WIDTH, HEIGHT), Image.Resampling.BICUBIC,
                box=(inset, top, WIDTH - inset, HEIGHT - top),
            )
        return frame


def preview(project, folder: Path, time: float) -> bytes:
    composition = _Composition(project, folder)
    index, elapsed = composition.at(time)
    frame = composition.apply_motion(composition._draw(index), index, elapsed)
    with BytesIO() as buffer:
        frame.save(buffer, format="PNG")
        return buffer.getvalue()


def layout_findings(project, folder: Path) -> list[str]:
    """Return concrete draw errors from every scene for the deterministic QC gate."""
    composition = _Composition(project, folder)
    findings = []
    for index in range(len(composition.scenes)):
        try:
            composition.motion_length(index)
            composition._draw(index)
        except RenderError as error:
            findings.append(str(error))
    return findings


def render_available() -> bool:
    return media_tools.available()


def _verified_audio(folder, entry, label):
    path = _media_path(folder, "audio", entry["hash"], ".wav")
    try:
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as error:
        raise RenderError(f"{label} could not be read.") from error
    if digest != entry["hash"]:
        raise RenderError(f"{label} changed after intake.")
    return path


def _number(value, label, minimum, maximum):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise RenderError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _sound_options(project, folder, frame_count, first_input):
    """Return the FFmpeg inputs and shared voice-first filter graph."""
    samples = frame_count * (SAMPLE_RATE // FPS)
    window = f"apad=whole_len={samples},atrim=end_sample={samples}"
    audio_format = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    options, filters, outputs = [], [], []
    input_count = 0

    def source(entry, label, gain, *, loop=False):
        nonlocal input_count
        path = _verified_audio(folder, entry, label)
        if loop:
            options.extend(("-stream_loop", "-1"))
        options.extend(("-f", "wav", "-i", str(path)))
        channel = f"[{first_input + input_count}:a:0]"
        input_count += 1
        return f"{channel}{audio_format},asetpts=PTS-STARTPTS,volume={gain:.3f}dB"

    mix = project.get("mix") or {}
    voice = project.get("audio")
    music = mix.get("music") if mix.get("musicMode") == "track" else None
    if mix.get("musicMode") == "track" and not music:
        raise RenderError("The selected music track is missing.")

    if voice:
        narration = source(voice, "Narration", -3.0)
        split = ",asplit=2[voice][sidechain]" if music else "[voice]"
        filters.append(f"{narration},{window}{split}")
        outputs.append("[voice]")

    if music:
        gain = _number(music["gainDb"], "Music gain", -48, 0)
        backing = source(music, "Music", gain, loop=True)
        fade = min(int(SAMPLE_RATE * 0.33), samples // 4)
        fades = f",afade=t=in:ns={fade},afade=t=out:ss={samples - fade}:ns={fade}"
        if voice:
            filters.append(f"{backing},{window}{fades}[music]")
            filters.append(
                "[music][sidechain]sidechaincompress=threshold=0.035:ratio=6:"
                "attack=12:release=240:link=maximum:detection=rms[backing]"
            )
        else:
            filters.append(f"{backing},{window}{fades}[backing]")
        outputs.append("[backing]")

    for index, effect in enumerate(mix.get("effects", []), start=1):
        label = f"Sound effect {index}"
        gain = _number(effect["gainDb"], f"{label} gain", -48, 0)
        start = _number(effect["start"], f"{label} start", 0, project["input"]["duration"])
        delay = round(start * SAMPLE_RATE)
        output = f"[effect{index}]"
        clip = source(effect, label, gain)
        filters.append(f"{clip},adelay={delay}S:all=1,atrim=end_sample={samples}{output}")
        outputs.append(output)

    if not outputs:
        return []

    if len(outputs) > 1:
        graph = f"{''.join(outputs)}amix=inputs={len(outputs)}:duration=longest:normalize=0,"
    else:
        graph = outputs[0]
    filters.append(
        f"{graph}{window},alimiter=limit=0.8912509:attack=5:"
        f"release=50:level=false:latency=true,{window},asetpts=N/SR/TB[mix]"
    )
    return [*options, "-filter_complex", ";".join(filters)]


def _video_frames(composition, frame_count):
    last_index = None
    last_phase = None
    for number in range(frame_count):
        index, elapsed = composition.at(number / FPS)
        if index != last_index:
            base = composition._draw(index)
            motion_length = composition.motion_length(index)
            last_index = index
            last_phase = None
        phase = min(elapsed, motion_length)
        if phase != last_phase:
            data = composition.apply_motion(base, index, phase).tobytes()
            last_phase = phase
        yield data


def _encode(project, folder, composition, frame_count, scratch):
    output = scratch / "video.mp4"
    log = scratch / "ffmpeg.log"
    duration = frame_count / FPS
    audio_options = _sound_options(project, folder, frame_count, first_input=1)
    audio_mapping = ["-map", "[mix]", "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
                     "-ac", str(CHANNELS)] if audio_options else []
    command = [
        media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "rawvideo", "-pixel_format", "rgb24",
        "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS), "-i", "pipe:0",
        *audio_options, "-map", "0:v:0", *audio_mapping,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-t", f"{duration:.9f}", "-movflags", "+faststart",
        str(output),
    ]
    with log.open("w+b") as errors:
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors,
                                       **media_tools.process_options())
        except OSError as error:
            raise RenderError("FFmpeg could not start.") from error
        watchdog = Timer(max(45, min(3600, duration * 3 + 45)), process.kill)
        watchdog.daemon = True
        watchdog.start()
        try:
            with process.stdin:
                for frame in _video_frames(composition, frame_count):
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
            raise RenderError(f"FFmpeg could not encode the video. {detail or 'The encoder exited.'}")
    return output, bool(audio_options)


def _probe(output):
    result = subprocess.run(
        [media_tools.command("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
        check=True, capture_output=True, text=True, timeout=20, **media_tools.process_options(),
    )
    return json.loads(result.stdout)


def _inspect(output, frame_count, has_audio):
    try:
        probe = _probe(output)
        video = [stream for stream in probe["streams"] if stream["codec_type"] == "video"]
        audio = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
        duration = float(probe["format"]["duration"])
        valid = (
            len(video) == 1 and len(audio) == int(has_audio)
            and (video[0]["width"], video[0]["height"], video[0]["codec_name"]) == (WIDTH, HEIGHT, "h264")
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
    return {"width": WIDTH, "height": HEIGHT, "fps": FPS, "frames": frame_count,
            "duration": duration, "codec": "h264", "audio": "aac" if has_audio else None,
            "audioSampleRate": SAMPLE_RATE if has_audio else None,
            "audioChannels": CHANNELS if has_audio else None}


def _frame_count(duration):
    if not math.isfinite(duration) or duration <= 0:
        raise RenderError("An export needs a positive duration.")
    return math.ceil(duration * FPS)


def export_video(project, folder: Path, destination: Path) -> dict:
    if not render_available():
        raise RenderError("Video export needs FFmpeg and FFprobe on PATH.")
    composition = _Composition(project, folder)
    frame_count = _frame_count(composition.duration)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".bacchus-render-", dir=destination.parent) as work:
        output, has_audio = _encode(project, folder, composition, frame_count, Path(work))
        media = _inspect(output, frame_count, has_audio)
        output.replace(destination)
    return media


def export_mix(project, folder: Path, destination: Path) -> dict:
    """Render the approved video mix's PCM source for listen-through."""
    if not render_available():
        raise RenderError("Mix preview needs FFmpeg and FFprobe on PATH.")
    frame_count = _frame_count(project["input"]["duration"])
    options = _sound_options(project, folder, frame_count, first_input=0)
    if not options:
        raise RenderError("Add narration, music, or sound effects to listen to a mix.")
    duration = frame_count / FPS
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".bacchus-mix-", dir=destination.parent) as work:
        output = Path(work) / "mix.wav"
        command = [
            media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
            *options, "-map", "[mix]", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
            "-c:a", "pcm_s16le", str(output),
        ]
        try:
            subprocess.run(command, check=True, timeout=max(45, duration), capture_output=True,
                           **media_tools.process_options())
            probe = _probe(output)
            streams = probe["streams"]
            stream_duration = float(probe["format"]["duration"])
            valid = len(streams) == 1 and (
                streams[0]["codec_type"], streams[0]["codec_name"],
                streams[0]["sample_rate"], streams[0]["channels"],
            ) == ("audio", "pcm_s16le", str(SAMPLE_RATE), CHANNELS) and abs(stream_duration - duration) < 0.001
        except (OSError, subprocess.SubprocessError, TypeError, KeyError, ValueError) as error:
            raise RenderError("FFmpeg could not produce a verified mix preview.") from error
        if not valid:
            raise RenderError("The mix preview did not match the selected sound settings.")
        output.replace(destination)
    return {"duration": stream_duration, "sampleRate": SAMPLE_RATE, "channels": CHANNELS}
