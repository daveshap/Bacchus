"""Paths and byte verification for a published render bundle."""

from functools import lru_cache
import hashlib
import json
import re


FILES = {
    "video": (".mp4", "video/mp4"),
    "captions": (".srt", "text/plain; charset=utf-8"),
    "receipt": (".json", "application/json"),
}
LABELS = {"video": "MP4", "captions": "captions", "receipt": "receipt"}
SHA256 = re.compile(r"[0-9a-f]{64}")


class DeliveryError(ValueError):
    """A published file is missing or differs from its recorded bytes."""


def bundle_paths(directory):
    return {kind: directory / (kind + suffix) for kind, (suffix, _) in FILES.items()}


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
