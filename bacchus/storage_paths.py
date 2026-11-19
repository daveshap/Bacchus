"""Check runtime write destinations without depending on Git or the launch directory."""

import os
from pathlib import Path
import sys
import tempfile


class StoragePathError(ValueError):
    """The selected destination cannot be used for Bacchus runtime data."""


def _resolve_destination(path):
    # Resolve the existing prefix strictly: a missing child is normal, a dangling
    # symlink/junction or an inaccessible parent is not a writable destination.
    for parent in (path, *path.parents):
        try:
            parent.lstat()
        except FileNotFoundError:
            continue
        return parent.resolve(strict=True) / path.relative_to(parent)
    raise StoragePathError("The storage drive is unavailable.")


def writable_path(path: Path | str) -> Path:
    """Return the absolute canonical destination, rejecting protected locations."""
    if not isinstance(path, (Path, str)) or not Path(path).is_absolute():
        raise StoragePathError("Choose an absolute path for Bacchus storage.")
    lexical = Path(path)
    if ".." in lexical.parts:
        raise StoragePathError("Use the full storage path without parent-directory shortcuts.")
    if any(part.casefold() == ".git" for part in lexical.parts):
        raise StoragePathError("Choose a storage folder outside Git metadata.")
    try:
        target = _resolve_destination(lexical)
        if any(part.casefold() == ".git" for part in target.parts):
            raise StoragePathError("Choose a storage folder outside Git metadata.")
        install = []
        if getattr(sys, "frozen", False):
            program = Path(sys.executable).parent
            bundle = next((p for p in program.parents if p.suffix.lower() == ".app"), program)
            install = [bundle, bundle.resolve()]
            if extracted := getattr(sys, "_MEIPASS", None):
                install.extend((Path(extracted), Path(extracted).resolve()))
        directories = {lexical, *lexical.parents, target, *target.parents}
        if lexical != target:
            # A chain of links can enter a worktree subdirectory and leave
            # through a second link. Check those intermediate targets as well.
            for part in (lexical, *lexical.parents):
                if part.is_symlink() or part.is_junction():
                    resolved = part.resolve(strict=True)
                    directories.update((resolved, *resolved.parents))
        if any(folder in directories for folder in install):
            raise StoragePathError("Choose a storage folder outside the Bacchus installation.")
        for directory in directories:
            try:
                (directory / ".git").lstat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            raise StoragePathError(f"Choose a storage folder outside the Git checkout at {directory}.")
    except StoragePathError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise StoragePathError(f"Bacchus could not verify the storage location {lexical}.") from error
    return target


def scratch_directory() -> Path:
    """Choose a system temporary folder without probing or falling back to cwd."""
    candidates = [tempfile.tempdir, *(os.environ.get(key) for key in ("TMPDIR", "TEMP", "TMP"))]
    if os.name == "nt":
        if local := os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(local) / "Temp")
        if profile := os.environ.get("USERPROFILE"):
            candidates.append(Path(profile) / "AppData" / "Local" / "Temp")
    else:
        candidates.extend((Path("/tmp"), Path("/var/tmp"), Path("/usr/tmp")))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = writable_path(os.fsdecode(candidate))
        except StoragePathError:
            continue
        if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
            return path
    raise StoragePathError("Choose a writable system temporary folder outside Git checkouts and the Bacchus installation.")
