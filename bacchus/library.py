"""Persistent, versioned artwork shared by local Bacchus projects."""

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from threading import RLock
from uuid import uuid4

from PIL import Image


ASSET_ID = re.compile(r"[a-f0-9]{32}")
CONTENT_HASH = re.compile(r"[a-f0-9]{64}")
SCENE_ALIAS = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SOURCES = {"upload", "generated", "project"}
VERSION_FIELDS = {"id", "version", "hash", "name", "brief", "style", "source", "createdAt"}


class LibraryError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _atomic_write(path, data):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class Library:
    """Store one metadata document per artwork and one immutable PNG per digest."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self._lock = RLock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._safe_path("entries").mkdir(exist_ok=True)
            self._safe_path("blobs").mkdir(exist_ok=True)
        except OSError as error:
            raise LibraryError("The artwork library folder could not be opened.", 500) from error

    def _safe_path(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise LibraryError("Artwork library storage must stay in its configured folder.", 409)
        return path

    @contextmanager
    def _exclusive(self):
        # Separate Bacchus processes may point at the same library folder.
        with self._lock:
            try:
                with self._safe_path(".library.lock").open("a+b") as lock:
                    lock.seek(0)
                    if sys.platform == "win32":
                        import msvcrt

                        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    yield
            except OSError as error:
                raise LibraryError("The artwork library could not complete the storage operation.", 500) from error

    def _entry_path(self, asset_id):
        if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
            raise LibraryError("Artwork was not found in the library.", 404)
        return self._safe_path("entries", f"{asset_id}.json")

    def _read(self, asset_id, *, optional=False):
        try:
            record = json.loads(self._entry_path(asset_id).read_bytes())
        except FileNotFoundError as error:
            if optional:
                return None
            raise LibraryError("Artwork was not found in the library.", 404) from error
        except (OSError, ValueError) as error:
            raise LibraryError("Library artwork metadata could not be read.", 409) from error
        asset = record.get("asset") if isinstance(record, dict) else None
        versions = asset.get("versions") if isinstance(asset, dict) else None
        origin = record.get("origin") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict) or record.get("schema") != 1
            or not isinstance(asset, dict) or asset.get("id") != asset_id
            or not isinstance(origin, dict) or not isinstance(origin.get("alias"), str)
            or not isinstance(versions, list) or not versions
            or not isinstance(asset.get("name"), str) or type(asset.get("latestVersion")) is not int
        ):
            raise LibraryError("Library artwork metadata needs repair.", 409)
        for offset, version in enumerate(versions):
            if (
                not isinstance(version, dict) or set(version) != VERSION_FIELDS or version["id"] != asset_id
                or type(version["version"]) is not int or version["version"] != asset["latestVersion"] - offset
                or not isinstance(version["hash"], str) or not CONTENT_HASH.fullmatch(version["hash"])
            ):
                raise LibraryError("Library artwork version metadata needs repair.", 409)
        return record

    def _blob_path(self, content_hash):
        if not isinstance(content_hash, str) or not CONTENT_HASH.fullmatch(content_hash):
            raise LibraryError("Artwork content was not found in the library.", 404)
        return self._safe_path("blobs", f"{content_hash}.png")

    def blob(self, content_hash) -> Path:
        path = self._blob_path(content_hash)
        try:
            with path.open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
        except FileNotFoundError as error:
            raise LibraryError("Artwork content was not found in the library.", 404) from error
        except OSError as error:
            raise LibraryError("Library artwork could not be read.", 409) from error
        if actual != content_hash:
            raise LibraryError("Library artwork no longer matches its recorded fingerprint.", 409)
        return path

    def publish(self, *, project_id, alias, name, brief, style, source, content_hash, path: Path, proofs) -> dict:
        if not isinstance(project_id, str) or not project_id or not isinstance(alias, str) or not SCENE_ALIAS.fullmatch(alias):
            raise LibraryError("Publishing requires a project and a valid artwork alias.")
        if not all(isinstance(value, str) for value in (name, brief, style, source)) or not name.strip():
            raise LibraryError("Publishing requires an artwork name and text metadata.")
        if (
            not isinstance(proofs, dict) or not proofs
            or not all(isinstance(key, str) and CONTENT_HASH.fullmatch(key) and isinstance(value, str)
                       and CONTENT_HASH.fullmatch(value) for key, value in proofs.items())
        ):
            raise LibraryError("Publishing requires evidence from passed artwork-use checks.", 409)
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as error:
            raise LibraryError("The approved project artwork could not be read.", 409) from error
        if hashlib.sha256(data).hexdigest() != content_hash:
            raise LibraryError("The approved project artwork no longer matches its recorded fingerprint.", 409)
        try:
            with Image.open(BytesIO(data), formats=["PNG"]) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise LibraryError("The artwork library stores single-frame PNG images.")
                image.verify()
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
            raise LibraryError("The artwork library stores valid PNG images.") from error

        origin = {"projectId": project_id, "alias": alias}
        identity = json.dumps(origin, sort_keys=True, ensure_ascii=True).encode("ascii")
        asset_id = hashlib.sha256(identity).hexdigest()[:32]
        with self._exclusive():
            record = self._read(asset_id, optional=True)
            if record and record.get("origin") != origin:
                raise LibraryError("The library artwork identity does not match its origin.", 409)
            asset = record["asset"] if record else {"id": asset_id, "latestVersion": 0, "versions": []}
            qualifications = (record or {}).get("qualifications", {})
            issues = (record or {}).get("issues", {})
            target = self._blob_path(content_hash)
            if target.exists():
                self.blob(content_hash)
            else:
                _atomic_write(target, data)
            for existing in asset["versions"]:
                if existing["hash"] == content_hash:
                    version = existing
                    break
            else:
                version = {
                    "id": asset_id, "version": asset["latestVersion"] + 1, "hash": content_hash,
                    "name": name.strip(), "brief": brief, "style": style,
                    "source": source if source in SOURCES else "project",
                    "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                asset.update(name=version["name"], brief=brief, style=style, latestVersion=version["version"],
                             versions=[version, *asset["versions"]])
            key = str(version["version"])
            if key in issues:
                raise LibraryError("This exact artwork version has an unresolved defect. Use a corrected file.", 409)
            qualifications[key] = {"projectId": project_id, "proofs": proofs}
            self._write_record(asset_id, {"schema": 1, "origin": origin, "asset": asset,
                                          "qualifications": qualifications, "issues": issues})
            return self._view_version({"qualifications": qualifications, "issues": issues}, version)

    def _write_record(self, asset_id, record):
        raw = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        _atomic_write(self._entry_path(asset_id), raw)

    def qualify(self, asset_id, version, project_id, proofs):
        with self._exclusive():
            record = self._read(asset_id)
            self._selected(record, version)
            record.setdefault("qualifications", {})[str(version)] = {"projectId": project_id, "proofs": proofs}
            self._write_record(asset_id, record)

    def flag(self, asset_id, version, issue):
        with self._exclusive():
            record = self._read(asset_id)
            self._selected(record, version, allow_issue=True)
            record.setdefault("issues", {}).setdefault(str(version), issue)
            self._write_record(asset_id, record)

    def issue(self, asset_id, version):
        with self._exclusive():
            record = self._read(asset_id, optional=True)
            return record.get("issues", {}).get(str(version)) if record else None

    @staticmethod
    def _view_version(record, version):
        key = str(version["version"])
        return {**version, "qualified": key in record.get("qualifications", {}),
                "issue": record.get("issues", {}).get(key)}

    def list_assets(self) -> list[dict]:
        """Return artwork alphabetically, with revisions newest first."""
        with self._exclusive():
            records = [
                self._read(path.stem)
                for path in self._safe_path("entries").glob("*.json")
                if ASSET_ID.fullmatch(path.stem)
            ]
            entries = [{**record["asset"], "alias": record["origin"]["alias"],
                        "versions": [self._view_version(record, item) for item in record["asset"]["versions"]]}
                       for record in records]
        return sorted(entries, key=lambda entry: (entry["name"].casefold(), entry["id"]))

    def get(self, asset_id, version: int) -> dict:
        with self._exclusive():
            return self._selected(self._read(asset_id), version)

    def _selected(self, record, version, *, allow_issue=False):
        if type(version) is not int or version < 1:
            raise LibraryError("Artwork version was not found in the library.", 404)
        for entry in record["asset"]["versions"]:
            if entry["version"] == version:
                selected = self._view_version(record, entry)
                if selected["issue"] and not allow_issue:
                    raise LibraryError("This library version has an open artwork issue. Choose a corrected version.", 409)
                return selected
        raise LibraryError("Artwork version was not found in the library.", 404)
