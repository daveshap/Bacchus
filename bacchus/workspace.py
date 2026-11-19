"""Remember the workspace used by a normal desktop launch."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .runtime import default_data_directory
from .storage_paths import StoragePathError, writable_path


class WorkspaceError(ValueError):
    pass


def _absolute_directory(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 32767
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise WorkspaceError("Enter a full workspace folder path.")
    try:
        path = Path(value.strip()).expanduser()
    except RuntimeError as error:
        raise WorkspaceError("Enter a full workspace folder path.") from error
    if not path.is_absolute():
        raise WorkspaceError("Enter a full workspace folder path.")
    return path


def _verify_access(directory):
    try:
        with NamedTemporaryFile(dir=writable_path(directory), prefix=".bacchus-write-"):
            pass
    except OSError as error:
        raise WorkspaceError(f"The workspace folder cannot be written. {error.strerror or error}") from error


class WorkspaceLocation:
    def __init__(self, override=None, *, default=None):
        self.default = Path(default) if default is not None else default_data_directory()
        self.preference_file = self.default / "workspace-location.json"
        self.override = override is not None
        self.warning = ""
        if self.override:
            self.active = writable_path(Path(override).expanduser().absolute())
            return
        try:
            selected = self._read()
            if selected is not None:
                directory = writable_path(selected)
                if not directory.is_dir():
                    raise WorkspaceError("The saved workspace folder is unavailable.")
                _verify_access(directory)
                self.active = directory
                return
        except (WorkspaceError, StoragePathError, OSError) as error:
            self.warning = f"{error} Opened the default workspace. Choose a folder in Settings."
        self.active = writable_path(self.default)

    def _read(self):
        try:
            with self.preference_file.open(encoding="utf-8") as stream:
                raw = stream.read(128 * 1024)
                if stream.read(1):
                    raise WorkspaceError("The workspace preference file is too large.")
        except FileNotFoundError:
            return None
        except UnicodeError as error:
            raise WorkspaceError("The saved workspace preference could not be read.") from error
        try:
            data = json.loads(raw)
            return _absolute_directory(data["directory"])
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            raise WorkspaceError("The saved workspace preference could not be read.") from error

    def view(self):
        message = self.warning
        try:
            preferred = self._read() or self.default
        except (ValueError, OSError) as error:
            preferred = self.default
            message = message or str(error)
        return {"active": str(self.active), "preferred": str(preferred), "systemDefault": str(self.default),
                "override": self.override, "warning": message}

    def choose(self, value):
        if not isinstance(value, str):
            raise WorkspaceError("Choose a workspace folder or use the default.")
        destination = writable_path(_absolute_directory(value) if value.strip() else self.default)
        preference = writable_path(self.preference_file)
        temporary = None
        try:
            destination.mkdir(parents=True, exist_ok=True)
            _verify_access(destination)
            if destination == writable_path(self.default):
                preference.unlink(missing_ok=True)
            else:
                preference.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=preference.parent,
                                        prefix=".workspace-", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump({"directory": str(destination)}, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(preference)
        except OSError as error:
            raise WorkspaceError(f"Could not save the workspace folder. {error.strerror or error}") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.warning = ""
