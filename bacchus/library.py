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
SOURCES = {"upload", "generated", "project", "import"}
VERSION_FIELDS = {"id", "version", "hash", "name", "brief", "style", "source", "createdAt"}
ASSET_FIELDS = ("id", "name", "brief", "style", "latestVersion")
SURROGATE = re.compile(r"[\ud800-\udfff]")


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


def _metadata_text(value):
    return isinstance(value, str) and not SURROGATE.search(value)


def _proofs_valid(proofs):
    return isinstance(proofs, dict) and bool(proofs) and all(
        isinstance(key, str) and CONTENT_HASH.fullmatch(key) and isinstance(value, str) and CONTENT_HASH.fullmatch(value)
        for key, value in proofs.items()
    )


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
        except (OSError, ValueError, RecursionError) as error:
            raise LibraryError("Library artwork metadata could not be read.", 409) from error
        asset = record.get("asset") if isinstance(record, dict) else None
        versions = asset.get("versions") if isinstance(asset, dict) else None
        origin = record.get("origin") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict) or record.get("schema") != 1
            or not isinstance(asset, dict) or asset.get("id") != asset_id
            or not isinstance(origin, dict) or not _metadata_text(origin.get("alias"))
            or not isinstance(origin.get("projectId"), str)
            or not isinstance(versions, list) or not versions
            or not all(_metadata_text(asset.get(key)) for key in ("name", "brief", "style"))
            or type(asset.get("latestVersion")) is not int
        ):
            raise LibraryError("Library artwork metadata needs repair.", 409)
        previous = asset["latestVersion"] + 1
        for version in versions:
            if (
                not isinstance(version, dict) or set(version) != VERSION_FIELDS or version["id"] != asset_id
                or type(version["version"]) is not int or not 0 < version["version"] < previous
                or not isinstance(version["hash"], str) or not CONTENT_HASH.fullmatch(version["hash"])
                or not all(_metadata_text(version[key]) for key in ("name", "brief", "style", "source", "createdAt"))
            ):
                raise LibraryError("Library artwork version metadata needs repair.", 409)
            previous = version["version"]
        if versions[0]["version"] != asset["latestVersion"]:
            raise LibraryError("Library artwork version metadata needs repair.", 409)
        qualifications = record.get("qualifications", {})
        if not isinstance(qualifications, dict) or any(
            not isinstance(entry, dict) or not _metadata_text(entry.get("projectId")) or not _proofs_valid(entry.get("proofs"))
            for entry in qualifications.values()
        ):
            raise LibraryError("Library artwork qualification metadata needs repair.", 409)
        issues = record.get("issues", {})
        if not isinstance(issues, dict) or any(
            not isinstance(issue, dict) or set(issue) != {"kind", "reason", "at"}
            or not all(_metadata_text(value) for value in issue.values())
            for issue in issues.values()
        ):
            raise LibraryError("Library artwork issue metadata needs repair.", 409)
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
        if not _proofs_valid(proofs):
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
            if record is None and next(self._safe_path("quarantine").glob(f"{asset_id}-*.json"), None):
                raise LibraryError("This artwork has quarantined metadata. Restore the entry before publishing to it.", 409)
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

    def import_asset(self, *, alias, name, brief, style, png_bytes: bytes, origin_key="studio-import", source_hash=None) -> tuple[dict, bool]:
        """Import a verified pre-approved studio artwork PNG directly into this style library."""
        if not isinstance(alias, str) or not SCENE_ALIAS.fullmatch(alias):
            raise LibraryError("Imported artwork needs a lowercase hyphenated alias.")
        if not all(_metadata_text(value) for value in (name, brief, style)) or not name.strip():
            raise LibraryError("Imported artwork requires a valid name and text metadata.")
        try:
            with Image.open(BytesIO(png_bytes), formats=["PNG"]) as image:
                if getattr(image, "n_frames", 1) != 1:
                    raise LibraryError("The artwork library stores single-frame PNG images.")
                image.verify()
        except LibraryError:
            raise
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
            raise LibraryError("The artwork library stores valid PNG images.") from error

        content_hash = hashlib.sha256(png_bytes).hexdigest()
        if source_hash is not None and (not isinstance(source_hash, str) or not CONTENT_HASH.fullmatch(source_hash)):
            raise LibraryError("The imported source image needs a valid SHA-256 fingerprint.")
        origin = {"projectId": origin_key, "alias": alias}
        identity = json.dumps(origin, sort_keys=True, ensure_ascii=True).encode("ascii")
        asset_id = hashlib.sha256(identity).hexdigest()[:32]
        with self._exclusive():
            record = self._read(asset_id, optional=True)
            if record is None and next(self._safe_path("quarantine").glob(f"{asset_id}-*.json"), None):
                raise LibraryError("This artwork has quarantined metadata. Restore the entry before importing to it.", 409)
            asset = record["asset"] if record else {"id": asset_id, "latestVersion": 0, "versions": []}
            qualifications = (record or {}).get("qualifications", {})
            issues = (record or {}).get("issues", {})
            target = self._blob_path(content_hash)
            if target.exists():
                self.blob(content_hash)
            else:
                _atomic_write(target, png_bytes)
            for existing in asset["versions"]:
                if existing["hash"] == content_hash:
                    return self._view_version({"qualifications": qualifications, "issues": issues}, existing), False
            version = {
                "id": asset_id, "version": asset["latestVersion"] + 1, "hash": content_hash,
                "name": name.strip(), "brief": brief.strip(), "style": style,
                "source": "import",
                "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            asset.update(name=version["name"], brief=version["brief"], style=style,
                         latestVersion=version["version"], versions=[version, *asset["versions"]])
            qualifications[str(version["version"])] = {
                "projectId": origin_key,
                "proofs": {source_hash or content_hash: content_hash},
            }
            self._write_record(asset_id, {"schema": 1, "origin": origin, "asset": asset,
                                          "qualifications": qualifications, "issues": issues})
            return self._view_version({"qualifications": qualifications, "issues": issues}, version), True

    def _write_record(self, asset_id, record):
        raw = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        _atomic_write(self._entry_path(asset_id), raw)

    def copy_by_style(self, libraries):
        """Copy a legacy shared collection without changing its source files."""
        failures = set()
        for path in self._safe_path("entries").iterdir():
            if path.suffix != ".json" or not ASSET_ID.fullmatch(path.stem):
                continue
            try:
                record = self._read(path.stem)
            except LibraryError:
                failures.add(path.stem)
                continue
            grouped = {}
            for version in record["asset"]["versions"]:
                style = version["style"] if version["style"] in libraries else "warm-editorial"
                grouped.setdefault(style, []).append(version)
            for style, versions in grouped.items():
                try:
                    libraries[style]._copy_versions(self, record, versions)
                except LibraryError:
                    failures.add(path.stem)
        if failures:
            raise LibraryError(f"Bacchus could not copy {len(failures)} legacy artwork entries. Their source files were kept.", 409)

    def _copy_versions(self, source, record, versions):
        asset_id = record["asset"]["id"]
        with self._exclusive():
            current = self._read(asset_id, optional=True)
            if current and current["origin"] != record["origin"]:
                raise LibraryError("An existing style library entry conflicts with the old library.", 409)
            existing = {item["version"]: item for item in current["asset"]["versions"]} if current else {}
            for version in versions:
                number = version["version"]
                if number in existing and existing[number] != version:
                    raise LibraryError("An existing style library version conflicts with the old library.", 409)
                target = self._blob_path(version["hash"])
                if not target.exists():
                    _atomic_write(target, source.blob(version["hash"]).read_bytes())
                else:
                    self.blob(version["hash"])
                existing[number] = version
            merged = sorted(existing.values(), key=lambda item: item["version"], reverse=True)
            latest = merged[0]
            asset = {**record["asset"], "name": latest["name"], "brief": latest["brief"], "style": latest["style"],
                     "latestVersion": latest["version"], "versions": merged}
            metadata = {}
            for key in ("qualifications", "issues"):
                retained = {str(item["version"]): record.get(key, {}).get(str(item["version"])) for item in versions}
                metadata[key] = {version: value for version, value in retained.items() if value is not None}
                metadata[key].update((current or {}).get(key, {}))
            self._write_record(asset_id, {"schema": 1, "origin": record["origin"], "asset": asset, **metadata})

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
            if record is None and next(self._safe_path("quarantine").glob(f"{asset_id}-*.json"), None):
                raise LibraryError("The pinned library entry is in quarantine.", 409)
            return record.get("issues", {}).get(str(version)) if record else None

    @staticmethod
    def _view_version(record, version):
        key = str(version["version"])
        return {**version, "qualified": key in record.get("qualifications", {}),
                "issue": record.get("issues", {}).get(key)}

    def catalog(self):
        """Read each entry independently so damaged metadata cannot hide healthy artwork."""
        entries, issues = [], []
        with self._exclusive():
            for path in sorted(self._safe_path("entries").iterdir()):
                if path.suffix != ".json" or not ASSET_ID.fullmatch(path.stem):
                    continue
                try:
                    record = self._read(path.stem)
                except LibraryError as error:
                    issues.append({"id": path.stem, "message": str(error)})
                    continue
                entries.append({**{key: record["asset"][key] for key in ASSET_FIELDS}, "alias": record["origin"]["alias"],
                                "versions": [self._view_version(record, item) for item in record["asset"]["versions"]]})
        return {"assets": sorted(entries, key=lambda entry: (entry["name"].casefold(), entry["id"])), "issues": issues}

    def list_assets(self) -> list[dict]:
        return self.catalog()["assets"]

    def archive_records(self):
        """Return active records for an exact archive, failing on damaged metadata."""
        with self._exclusive():
            records = []
            for path in sorted(self._safe_path("entries").iterdir()):
                if path.suffix != ".json":
                    continue
                if not ASSET_ID.fullmatch(path.stem):
                    raise LibraryError("The artwork library contains entry metadata with an invalid ID.", 409)
                try:
                    records.append(self._read(path.stem))
                except LibraryError as error:
                    raise LibraryError(f"Artwork entry {path.stem} needs repair before export. {error}", 409) from error
            return records

    def quarantine(self, asset_id):
        with self._exclusive():
            source = self._entry_path(asset_id)
            try:
                self._read(asset_id)
            except LibraryError as error:
                if error.status != 409:
                    raise
            else:
                raise LibraryError("Only damaged artwork metadata can be quarantined.", 409)
            if not source.is_file() or source.is_symlink():
                raise LibraryError("Bacchus could not safely move this artwork metadata.", 409)
            folder = self._safe_path("quarantine")
            folder.mkdir(exist_ok=True)
            destination = self._safe_path("quarantine", f"{asset_id}-{uuid4().hex}.json")
            source.replace(destination)
            return {"id": asset_id, "retainedPath": str(destination)}

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
