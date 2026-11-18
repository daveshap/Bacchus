"""Paths and byte verification for a published render bundle."""

from functools import lru_cache
import hashlib
import json
import re
import shutil
import stat


FILES = {
    "video": (".mp4", "video/mp4"),
    "captions": (".srt", "text/plain; charset=utf-8"),
    "receipt": (".json", "application/json"),
}
LABELS = {"video": "MP4", "captions": "captions", "receipt": "receipt"}
SHA256 = re.compile(r"[0-9a-f]{64}")
STAGING_PREFIX = ".bacchus-delivery-"
# Match only directories named by tempfile.TemporaryDirectory.
_STAGING_NAME = re.compile(re.escape(STAGING_PREFIX) + r"[a-z0-9_]{8}")


class DeliveryError(ValueError):
    """A published file is missing or differs from its recorded bytes."""


def bundle_paths(directory):
    return {kind: directory / (kind + suffix) for kind, (suffix, _) in FILES.items()}


def _plain_stat(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    return info


def _ordinary_tree(root, device):
    pending = [root]
    while pending:
        path = pending.pop()
        info = _plain_stat(path)
        if info is None or info.st_dev != device:
            return False
        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif not stat.S_ISREG(info.st_mode):
            return False
    return True


def remove_abandoned_staging(data_root, folder):
    """Discard interrupted render scratch while startup owns the data instance lock."""
    exports = folder / "exports"
    try:
        if folder.parent != data_root or folder.resolve(strict=True) != folder or exports.resolve(strict=True) != exports:
            return
        project_info = _plain_stat(folder / "project.json")
        folder_info = _plain_stat(folder)
        exports_info = _plain_stat(exports)
        if (project_info is None or not stat.S_ISREG(project_info.st_mode)
                or folder_info is None or not stat.S_ISDIR(folder_info.st_mode)
                or exports_info is None or not stat.S_ISDIR(exports_info.st_mode)
                or exports_info.st_dev != folder_info.st_dev):
            return
        entries = tuple(exports.iterdir())
    except (OSError, RuntimeError):
        return

    for entry in entries:
        if not _STAGING_NAME.fullmatch(entry.name):
            continue
        try:
            info = _plain_stat(entry)
            if info is None or not stat.S_ISDIR(info.st_mode):
                continue
            target = entry.resolve(strict=True)
            if target == entry and target.parent == exports and _ordinary_tree(target, exports_info.st_dev):
                shutil.rmtree(target)
        except (OSError, RuntimeError):
            # A locked or inaccessible entry can be retried next time Bacchus starts.
            continue


def _paths(folder, record):
    key = record.get("deliveryId", record.get("candidateHash"))
    if not isinstance(key, str) or not SHA256.fullmatch(key):
        raise DeliveryError("The delivery has no valid recorded identifier. Render again to repair it.")
    directory = folder / "exports"
    if record.get("legacy"):
        return {kind: directory / (key + suffix) for kind, (suffix, _) in FILES.items()}
    return bundle_paths(directory / key)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=128)
def _view_hash(path, signature):
    return file_hash(path)


def _signature(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def verify(folder, record, *, full=True):
    """Verify every artifact before reuse or serving. Cache hashes only in views."""
    paths = _paths(folder, record)
    expected = record.get("fileHashes")
    if not isinstance(expected, dict) or expected.keys() != FILES.keys():
        raise DeliveryError("The delivery has no verified file record. Render again to repair it.")
    for kind, path in paths.items():
        try:
            signature = _signature(path)
            actual = file_hash(path) if full else _view_hash(path, signature)
            if actual == expected[kind] and signature == _signature(path):
                continue
        except OSError:
            pass
        raise DeliveryError(f"The delivered {LABELS[kind]} is missing or has changed. Render again to repair the delivery.")
    return paths


def legacy_record(folder, record):
    """Adopt an intact older bundle whose original receipt proves its MP4 hash."""
    migrated = {**record, "legacy": True, "deliveryId": record["candidateHash"]}
    try:
        paths = _paths(folder, migrated)
        raw = paths["receipt"].read_bytes()
        receipt = json.loads(raw)
        video = file_hash(paths["video"])
        if (receipt["videoHash"] != video or receipt["candidateHash"] != migrated["candidateHash"]
                or receipt["accepted"]["hash"] != migrated["candidateHash"]):
            return None
        migrated["fileHashes"] = {
            "video": video, "captions": file_hash(paths["captions"]), "receipt": hashlib.sha256(raw).hexdigest(),
        }
        migrated.setdefault("media", receipt.get("media", {}))
        if isinstance(receipt.get("createdAt"), str):
            migrated.setdefault("finishedAt", receipt["createdAt"])
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None
    if not isinstance(migrated.get("media"), dict):
        migrated["media"] = {}
    migrated["encoderMode"] = migrated["media"].get("encoderMode")
    return migrated


def legacy_receipts(folder):
    """Return candidate keys used by pre-delivery flat bundles."""
    return sorted(
        path.stem for path in (folder / "exports").glob("*.json")
        if SHA256.fullmatch(path.stem) and path.is_file()
    )
