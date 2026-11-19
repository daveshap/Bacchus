"""Choose an H.264 encoder by trying it against the actual export format."""

from dataclasses import dataclass, replace
from pathlib import Path
import re
import subprocess
import sys

from . import media_tools


@dataclass(frozen=True)
class Encoder:
    name: str
    label: str
    options: tuple[str, ...]
    input_options: tuple[str, ...] = ()
    executable: str | None = None
    pixel_format: str = "yuv420p"
    hardware_upload: bool = False


COLOR_METADATA = {
    "color_range": "tv", "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709",
}
COLOR_OPTIONS = (
    "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
    # Write the VUI explicitly for encoders that do not preserve all stream color fields.
    "-bsf:v", "h264_metadata=video_full_range_flag=0:matrix_coefficients=1:"
    "colour_primaries=1:transfer_characteristics=1",
)

CPU = Encoder("libx264", "CPU", ("-preset", "veryfast", "-crf", "18"))
NVENC = Encoder(
    "h264_nvenc", "NVIDIA NVENC",
    ("-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "17", "-b:v", "0",
     "-spatial-aq", "1", "-rc-lookahead", "32", "-profile:v", "high"),
)
QUICK_SYNC = Encoder(
    "h264_qsv", "Intel Quick Sync",
    ("-global_quality", "18", "-preset", "slow"),
    ("-init_hw_device", "qsv=bacchus,child_device_type=d3d11va"),
    pixel_format="nv12",
)
AMF = Encoder(
    "h264_amf", "AMD AMF",
    ("-quality", "quality", "-rc", "cqp", "-qp_i", "17", "-qp_p", "18", "-qp_b", "20"),
    pixel_format="nv12",
)
VIDEO_TOOLBOX = Encoder(
    "h264_videotoolbox", "Apple VideoToolbox",
    ("-allow_sw", "0", "-b:v", "35M", "-profile:v", "high"),
)


def hardware_candidates(platform):
    if platform == "win32":
        return (NVENC, QUICK_SYNC, AMF)
    if platform == "darwin":
        return (VIDEO_TOOLBOX,)
    if platform.startswith("linux"):
        return (NVENC, *(
            Encoder("h264_vaapi", "VAAPI", ("-qp", "18"),
                    ("-init_hw_device", f"vaapi=bacchus:{device}", "-filter_hw_device", "bacchus"),
                    pixel_format="nv12", hardware_upload=True)
            for device in sorted(Path("/dev/dri").glob("renderD*"))
        ))
    return ()


def _available(executable=None):
    try:
        result = subprocess.run(
            [executable or media_tools.command("ffmpeg"), "-hide_banner", "-encoders"],
            check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=8,
            **media_tools.process_options(),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(re.findall(r"^\s*V[.A-Z]{5}\s+(\S+)", result.stdout, re.MULTILINE))


def raw_input(width, height, fps):
    return ["-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{width}x{height}", "-framerate", str(fps), "-i", "pipe:0"]


def png_input(fps):
    return ["-f", "image2pipe", "-c:v", "png", "-framerate", str(fps), "-i", "pipe:0"]


def encoding_options(encoder):
    # The renderer supplies full-range RGB. Convert to limited-range BT.709 before encoding or hardware upload.
    conversion = f"scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format={encoder.pixel_format}"
    if encoder.hardware_upload:
        conversion += ",hwupload"
    return ["-vf", conversion, "-c:v", encoder.name, *encoder.options, *COLOR_OPTIONS]


def _trial(encoder, width, height, fps):
    try:
        result = subprocess.run(
            [encoder.executable or media_tools.command("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
             *encoder.input_options, *raw_input(width, height, fps), "-frames:v", "1",
             "-an", *encoding_options(encoder), "-f", "h264", "pipe:1"],
            input=bytes(width * height * 3), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, **media_tools.process_options(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout)


def choose(mode, width, height, fps):
    if mode == "cpu":
        return CPU, None
    candidates = hardware_candidates(sys.platform)
    if not candidates:
        return CPU, "No supported hardware encoder is configured on this platform."
    primary = media_tools.path("ffmpeg")
    system = media_tools.path("ffmpeg", managed=False)
    alternative = system if primary and system and Path(primary) != Path(system) else None
    attempted = []
    available = [(None, _available())]
    if alternative:
        available.append((alternative, _available(alternative)))
    for candidate in candidates:
        for executable, installed in available:
            if candidate.name not in installed:
                continue
            selected = replace(candidate, executable=executable) if executable else candidate
            if _trial(selected, width, height, fps):
                return selected, None
            if candidate.label not in attempted:
                attempted.append(candidate.label)
    if attempted:
        return CPU, f"The hardware test encode failed for {', '.join(attempted)}."
    return CPU, "The selected FFmpeg build has no available H.264 hardware encoder for this platform."
