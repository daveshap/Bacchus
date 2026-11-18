"""Find FFmpeg and provision an app-local Windows build on request."""

from functools import lru_cache
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


WINDOWS_RELEASE = "9.0.2"
WINDOWS_BUILD = f"ffmpeg-{WINDOWS_RELEASE}-essentials_build"
WINDOWS_URL = f"https://github.com/GyanD/codexffmpeg/releases/download/{WINDOWS_RELEASE}/{WINDOWS_BUILD}.zip"
WINDOWS_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
MAX_ARCHIVE_BYTES = 130 * 1024 * 1024
TOOLS = ("ffmpeg", "ffprobe")
INSTALL_LOCK = Lock()
_managed = None


class MediaToolsError(Exception):
    pass


def configure(root):
    global _managed
    _managed = root / "tools" / WINDOWS_BUILD if sys.platform == "win32" else None


def path(name):
    if _managed and all((_managed / f"{tool}.exe").is_file() for tool in TOOLS):
        return str(_managed / f"{name}.exe")
    found = shutil.which(name)
    if not found and sys.platform == "darwin":
        for folder in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            candidate = folder / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return found


def command(name):
    return path(name) or name


def available():
    encoder, probe = (path(name) for name in TOOLS)
    if not encoder or not probe:
        return False
    try:
        return _supports_bacchus(encoder, Path(encoder).stat().st_mtime_ns)
    except OSError:
        return False


@lru_cache(maxsize=8)
def _supports_bacchus(encoder, modified):
    requirements = {
        "-encoders": ("libx264", "aac", "pcm_s16le"),
        "-filters": ("sidechaincompress", "alimiter"),
    }
    try:
        for flag, needed in requirements.items():
            result = subprocess.run(
                [encoder, "-hide_banner", flag], check=True, timeout=10, capture_output=True,
                encoding="utf-8", errors="replace", **process_options(),
            )
            if any(not re.search(rf"\b{name}\b", result.stdout) for name in needed):
                return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def can_install():
    return _managed is not None and platform.machine().casefold() in ("amd64", "x86_64", "arm64", "aarch64")


def process_options():
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    if sys.platform == "win32":
        return {"env": env, "creationflags": subprocess.CREATE_NO_WINDOW}
    if getattr(sys, "frozen", False):
        for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            original = env.pop(f"{key}_ORIG", None)
            if original is None:
                env.pop(key, None)
            else:
                env[key] = original
    return {"env": env}


def _download(destination):
    checksum = hashlib.sha256()
    total = 0
    deadline = monotonic() + 360
    request = Request(WINDOWS_URL, headers={"User-Agent": "Bacchus media setup"})
    with urlopen(request, timeout=30) as response, destination.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES or monotonic() > deadline:
                raise MediaToolsError("The FFmpeg download exceeded its size or time limit.")
            checksum.update(chunk)
            output.write(chunk)
    if checksum.hexdigest() != WINDOWS_SHA256:
        raise MediaToolsError("The FFmpeg download failed verification. Try again later.")


def _unpack(archive_path, destination):
    destination.mkdir()
    with ZipFile(archive_path) as archive:
        for tool in TOOLS:
            name = f"{tool}.exe"
            member = f"{WINDOWS_BUILD}/bin/{name}"
            with archive.open(member) as source, (destination / name).open("xb") as output:
                shutil.copyfileobj(source, output)
        for archive_name in archive.namelist():
            parts = PurePosixPath(archive_name).parts
            if len(parts) == 2 and parts[0] == WINDOWS_BUILD and parts[1].casefold() in (
                "license", "license.txt", "copying", "copying.txt", "readme.txt",
            ):
                with archive.open(archive_name) as source, (destination / parts[1]).open("xb") as output:
                    shutil.copyfileobj(source, output)
    for name in TOOLS:
        subprocess.run(
            [str(destination / f"{name}.exe"), "-version"], check=True, timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options(),
        )
    encoder = destination / "ffmpeg.exe"
    if not _supports_bacchus(str(encoder), encoder.stat().st_mtime_ns):
        raise MediaToolsError("The downloaded FFmpeg build lacks required video or audio capabilities.")


def install():
    if not can_install():
        raise MediaToolsError("Automatic media setup is available in the Windows release.")
    if not INSTALL_LOCK.acquire(blocking=False):
        raise MediaToolsError("Media tools are already being installed.")
    try:
        if available():
            return
        _managed.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="media-install-", dir=_managed.parent) as directory:
            work = Path(directory)
            archive = work / "ffmpeg.zip"
            _download(archive)
            unpacked = work / "bin"
            _unpack(archive, unpacked)
            unpacked.rename(_managed)
    except (URLError, TimeoutError) as error:
        raise MediaToolsError("Bacchus could not download FFmpeg. Check your connection and try again.") from error
    except (OSError, KeyError, BadZipFile, subprocess.SubprocessError) as error:
        raise MediaToolsError("Bacchus could not finish installing the media tools.") from error
    finally:
        INSTALL_LOCK.release()
