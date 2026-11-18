"""Portable style and artwork snapshots. ZIP members are never extracted by name."""

from datetime import datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import stat
import struct
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
import zlib

from PIL import Image

from . import library, styles


FORMAT = "bacchus-style"
VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ART_BYTES = 80 * 1024 * 1024
MAX_REVISIONS = 256
MAX_MEMBERS = 8_192
MAX_ART_VERSIONS = 16_384
MAX_CENTRAL_BYTES = 4 * 1024 * 1024
MAX_GUIDE_BYTES = styles.MAX_GUIDE_CHARS * 4
MEMBER = re.compile(r"manifest\.json|style\.md|guides/[1-9][0-9]{0,2}\.md|(?:references|artwork)/[a-f0-9]{64}\.png")
STYLE_FIELDS = styles.SPEC_KEYS - {"guide"}
VERSION_FIELDS = {"version", "hash", "name", "brief", "source", "createdAt", "proofs", "issue"}
ZIP_ERRORS = (BadZipFile, EOFError, OSError, RuntimeError, ValueError, RecursionError, zlib.error)


class ArchiveError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _fields(value, expected, label):
    if not isinstance(value, dict) or value.keys() != expected:
        raise ArchiveError(f"The archive has invalid {label} fields.")
    return value


def _hash(value):
    if not isinstance(value, str) or not library.CONTENT_HASH.fullmatch(value):
        raise ArchiveError("The archive has an invalid SHA-256 fingerprint.")
    return value


def _timestamp(value):
    try:
        if not isinstance(value, str) or len(value) > 48 or datetime.fromisoformat(value).tzinfo is None:
            raise ValueError
    except ValueError as error:
        raise ArchiveError("The archive has an invalid revision date.") from error
    return value


def _guide_path(number, latest):
    return "style.md" if number == latest else f"guides/{number}.md"


def _png(raw, label, *, reference=False):
    edge, pixels = (styles.MAX_REFERENCE_EDGE, styles.MAX_REFERENCE_EDGE ** 2) if reference else (6_000, 18_000_000)
    try:
        with Image.open(BytesIO(raw), formats=("PNG",)) as image:
            if (getattr(image, "n_frames", 1) != 1 or min(image.size) < 1
                    or max(image.size) > edge or image.width * image.height > pixels):
                raise ArchiveError(f"{label} must be a single PNG within the image size limit.")
            size = image.size
            image.verify()
        with Image.open(BytesIO(raw), formats=("PNG",)) as image:
            image.load()
    except ArchiveError:
        raise
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise ArchiveError(f"{label} is not a readable PNG.") from error
    return size


def _parse_manifest(manifest, style_id):
    _fields(manifest, {"format", "version", "style", "artwork"}, "manifest")
    if manifest["format"] != FORMAT or type(manifest["version"]) is not int or manifest["version"] != VERSION:
        raise ArchiveError("This ZIP uses an unsupported Bacchus style archive format or version.")
    style = _fields(manifest["style"], {"latestRevision", "revisions"}, "style history")
    latest, revisions = style["latestRevision"], style["revisions"]
    if (type(latest) is not int or not 1 <= latest <= MAX_REVISIONS
            or not isinstance(revisions, list) or len(revisions) != latest):
        raise ArchiveError("The archive needs a complete, ordered style revision history.")
    refs = {}
    for number, row in enumerate(revisions, start=1):
        _fields(row, STYLE_FIELDS | {"revision", "createdAt", "guide"}, "style revision")
        if type(row["revision"]) is not int or row["revision"] != number:
            raise ArchiveError("The archive style revisions are missing, duplicated or out of order.")
        _timestamp(row["createdAt"])
        guide = _fields(row["guide"], {"path", "sha256"}, "Markdown guide")
        if guide["path"] != _guide_path(number, latest):
            raise ArchiveError("A Markdown guide does not match its style revision.")
        _hash(guide["sha256"])
        ref_list = row["referenceImages"]
        if not isinstance(ref_list, list) or len(ref_list) > styles.MAX_REFERENCE_IMAGES:
            raise ArchiveError("A style revision exceeds the reference image limit.")
        for ref in ref_list:
            _fields(ref, {"sha256", "name", "width", "height"}, "reference image")
            digest = _hash(ref["sha256"])
            size = (ref["width"], ref["height"])
            if (any(type(value) is not int or not 1 <= value <= styles.MAX_REFERENCE_EDGE for value in size)
                    or refs.setdefault(digest, size) != size):
                raise ArchiveError("A reference image has inconsistent recorded dimensions.")

    artwork = manifest["artwork"]
    if not isinstance(artwork, list) or len(artwork) > MAX_MEMBERS:
        raise ArchiveError("The archive has too many artwork entries.", 413)
    records, artwork_hashes = {}, set()
    count = 0
    for entry in artwork:
        _fields(entry, {"id", "alias", "versions"}, "artwork entry")
        asset_id, alias = entry["id"], entry["alias"]
        if not isinstance(asset_id, str) or not library.ASSET_ID.fullmatch(asset_id) or asset_id in records:
            raise ArchiveError("An artwork entry has an invalid or duplicate ID.")
        if not isinstance(alias, str) or len(alias) > 64 or not library.SCENE_ALIAS.fullmatch(alias):
            raise ArchiveError(f"Artwork {asset_id} has an invalid alias.")
        version_list = entry["versions"]
        if not isinstance(version_list, list) or not version_list:
            raise ArchiveError(f"Artwork {alias} needs its version history.")
        count += len(version_list)
        if count > MAX_ART_VERSIONS:
            raise ArchiveError("The archive has too many artwork versions.", 413)
        versions, qualifications, issues = [], {}, {}
        previous = None
        for row in version_list:
            _fields(row, VERSION_FIELDS, "artwork version")
            number = row["version"]
            if type(number) is not int or not 1 <= number <= 2**31 - 1 or (previous is not None and number >= previous):
                raise ArchiveError(f"Artwork {alias} has unordered or repeated versions.")
            previous = number
            digest = _hash(row["hash"])
            artwork_hashes.add(digest)
            styles._text(row["name"], "Artwork name", 180, required=True)
            styles._text(row["brief"], "Artwork brief", 380)
            _timestamp(row["createdAt"])
            if not isinstance(row["source"], str) or row["source"] not in library.SOURCES:
                raise ArchiveError(f"Artwork {alias} has an invalid source label.")
            proofs = row["proofs"]
            if proofs is not None:
                if not library._proofs_valid(proofs):
                    raise ArchiveError(f"Artwork {alias} has invalid qualification fingerprints.")
                qualifications[str(number)] = {"projectId": f"archive-{style_id}", "proofs": proofs}
            issue = row["issue"]
            if issue is not None:
                _fields(issue, {"kind", "reason", "at"}, "artwork issue")
                if issue["kind"] not in ("defective", "incomplete"):
                    raise ArchiveError(f"Artwork {alias} has an invalid issue type.")
                styles._text(issue["reason"], "Artwork issue reason", 1_000, required=True)
                styles._text(issue["at"], "Artwork issue date", 48, required=True)
                issues[str(number)] = issue
            versions.append({
                **{key: row[key] for key in library.VERSION_FIELDS - {"id", "style"}},
                "id": asset_id, "style": style_id,
            })
        newest = versions[0]
        records[asset_id] = {
            "schema": 1, "origin": {"projectId": f"archive-{style_id}-{asset_id}", "alias": alias},
            "asset": {**{key: newest[key] for key in ("id", "name", "brief", "style")},
                      "latestVersion": newest["version"], "versions": versions},
            "qualifications": qualifications, "issues": issues,
        }
    return revisions, refs, records, artwork_hashes


def _style_rows(catalog, style_id):
    revisions = catalog.history(style_id, maximum=MAX_REVISIONS)
    guides = {}
    rows = []
    for record in revisions:
        path = _guide_path(record["revision"], len(revisions))
        raw = record["guide"].encode("utf-8")
        guides[path] = raw
        rows.append({**{key: record[key] for key in STYLE_FIELDS | {"revision", "createdAt"}},
                     "guide": {"path": path, "sha256": _sha(raw)}})
    return rows, guides


def _art_rows(source):
    rows = []
    for record in source.archive_records():
        asset = record["asset"]
        versions = []
        valid_keys = {str(item["version"]) for item in asset["versions"]}
        if any(record.get(field, {}).keys() - valid_keys for field in ("qualifications", "issues")):
            raise ArchiveError(f"Artwork {record['origin']['alias']} has metadata for an unknown version.", 409)
        for item in asset["versions"]:
            key = str(item["version"])
            qualification = record.get("qualifications", {}).get(key)
            versions.append({**{field: item[field] for field in library.VERSION_FIELDS - {"id", "style"}},
                             "proofs": qualification["proofs"] if qualification else None,
                             "issue": record.get("issues", {}).get(key)})
        rows.append({"id": asset["id"], "alias": record["origin"]["alias"], "versions": versions})
    return rows


def _source_png(path, digest, limit, label, *, reference=False, size=None):
    try:
        if path.stat().st_size > limit:
            raise ArchiveError(f"{label} exceeds the archive image size limit.", 413)
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as error:
        raise ArchiveError(f"{label} is missing or unreadable. Restore the saved image before exporting.", 409) from error
    if len(raw) > limit or _sha(raw) != digest:
        raise ArchiveError(f"{label} changed after it was recorded. Restore the original image before exporting.", 409)
    dimensions = _png(raw, label, reference=reference)
    if size and size != dimensions:
        raise ArchiveError(f"{label} does not match its recorded dimensions.", 409)
    return raw


def _snapshot_spec(catalog, row, guide, reference_lookup):
    return catalog._spec({**{key: row[key] for key in STYLE_FIELDS}, "guide": guide}, reference_lookup=reference_lookup)


def export_style(studio, style_id, destination: Path):
    """Write a complete, verified archive and publish it only after the ZIP closes."""
    try:
        rows, guides = _style_rows(studio.styles, style_id)
    except styles.StyleError as error:
        raise ArchiveError(str(error), error.status) from error
    except (UnicodeError, AttributeError) as error:
        raise ArchiveError("A saved style guide could not be read. Repair the style before exporting.", 409) from error
    try:
        source = studio.library_for(style_id)
        manifest = {"format": FORMAT, "version": VERSION,
                    "style": {"latestRevision": len(rows), "revisions": rows}, "artwork": _art_rows(source)}
        _, refs, _, artwork = _parse_manifest(manifest, style_id)
        if 1 + len(guides) + len(refs) + len(artwork) > MAX_MEMBERS:
            raise ArchiveError("This style archive has too many files.", 413)
        raw_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise ArchiveError("This style archive has too much metadata.", 413)
        destination = Path(destination)
        with TemporaryDirectory(prefix=".bacchus-style-export-", dir=destination.parent) as temporary:
            output = Path(temporary) / "style.zip"
            with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=3, allowZip64=False) as archive:
                total = len(raw_manifest) + sum(map(len, guides.values()))
                archive.writestr("manifest.json", raw_manifest)
                for name, raw in guides.items():
                    if len(raw) > MAX_GUIDE_BYTES:
                        raise ArchiveError(f"{name} exceeds the style Markdown size limit.", 413)
                    archive.writestr(name, raw)
                for digest, size in sorted(refs.items()):
                    label = f"Style reference {digest[:12]}"
                    path = studio.styles._path("references", f"{digest}.png")
                    raw = _source_png(path, digest, styles.MAX_REFERENCE_BYTES, label, reference=True, size=size)
                    total += len(raw)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ArchiveError("This style archive exceeds 512 MiB.", 413)
                    archive.writestr(f"references/{digest}.png", raw, compress_type=ZIP_STORED)
                for row in rows:
                    _snapshot_spec(studio.styles, row, guides[row["guide"]["path"]].decode("utf-8"),
                                   lambda digest: studio.styles._path("references", f"{digest}.png"))
                for digest in sorted(artwork):
                    label = f"Artwork {digest[:12]}"
                    raw = _source_png(source._blob_path(digest), digest, MAX_ART_BYTES, label)
                    total += len(raw)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ArchiveError("This style archive exceeds 512 MiB.", 413)
                    archive.writestr(f"artwork/{digest}.png", raw, compress_type=ZIP_STORED)
            if output.stat().st_size > MAX_ARCHIVE_BYTES:
                raise ArchiveError("This style archive exceeds 512 MiB.", 413)
            output.replace(destination)
    except ArchiveError as error:
        if error.status == 400:
            raise ArchiveError(str(error), 409) from error
        raise
    except (styles.StyleError, library.LibraryError) as error:
        raise ArchiveError(f"The style archive could not include every saved file. {error}", 409) from error
    except OSError as error:
        raise ArchiveError("Bacchus could not write the complete style archive.", 500) from error


def _zip_limits(source):
    """Bound the central directory before ZipFile loads every entry into memory."""
    file_size = source.stat().st_size
    if file_size > MAX_ARCHIVE_BYTES:
        raise ArchiveError("This style archive exceeds 512 MiB.", 413)
    tail_size = min(file_size, 65_535 + 22)
    with source.open("rb") as stream:
        stream.seek(file_size - tail_size)
        tail = stream.read(tail_size)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < 22:
        raise ArchiveError("This is not a readable Bacchus style ZIP.")
    _, disk, central_disk, entries_here, entries, size, offset, comment = struct.unpack_from("<4s4H2LH", tail, end)
    if entries > MAX_MEMBERS or size > MAX_CENTRAL_BYTES:
        raise ArchiveError("This style archive has too many files or too much ZIP metadata.", 413)
    if (disk or central_disk or entries != entries_here or offset + size != file_size - tail_size + end
            or comment != len(tail) - end - 22):
        raise ArchiveError("This style ZIP has an unsupported structure.")


def _members(archive):
    result, total = {}, 0
    for info in archive.infolist():
        name = info.filename
        mode = stat.S_IFMT(info.external_attr >> 16)
        if (name != info.orig_filename or not MEMBER.fullmatch(name) or name in result
                or mode not in (0, stat.S_IFREG) or info.is_dir() or info.flag_bits & 1
                or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED)):
            raise ArchiveError("The style ZIP contains an unexpected, unsafe, or duplicate file entry.")
        total += info.file_size
        if len(result) >= MAX_MEMBERS or total > MAX_ARCHIVE_BYTES:
            raise ArchiveError("This style archive exceeds its unpacked size or file limit.", 413)
        result[name] = info
    return result


def _read(archive, member, limit, expected=None):
    if member.file_size > limit:
        raise ArchiveError(f"{member.filename} exceeds the archive entry size limit.", 413)
    with archive.open(member) as stream:
        raw = stream.read(limit + 1)
    if len(raw) != member.file_size:
        raise ArchiveError(f"{member.filename} does not match its ZIP size.")
    if expected is not None and _sha(raw) != expected:
        raise ArchiveError(f"{member.filename} does not match its recorded SHA-256 fingerprint.")
    return raw


def _json_object(entries):
    result = dict(entries)
    if len(result) != len(entries):
        raise ArchiveError("The archive manifest contains duplicate JSON fields.")
    return result


def _parse_zip(studio, source, stage, style_id):
    _zip_limits(source)
    with ZipFile(source) as archive:
        members = _members(archive)
        if "manifest.json" not in members:
            raise ArchiveError("This style ZIP is missing manifest.json.")
        manifest = json.loads(_read(archive, members["manifest.json"], MAX_MANIFEST_BYTES).decode("utf-8"),
                              object_pairs_hook=_json_object)
        rows, refs, records, artwork = _parse_manifest(manifest, style_id)
        required = {"manifest.json", *(row["guide"]["path"] for row in rows),
                    *(f"references/{digest}.png" for digest in refs), *(f"artwork/{digest}.png" for digest in artwork)}
        missing = required - members.keys()
        if missing:
            raise ArchiveError(f"This style ZIP is missing {min(missing)}.")
        if members.keys() - required:
            raise ArchiveError("The style ZIP contains files that are not listed in the manifest.")

        ref_folder = stage / "references"
        ref_folder.mkdir()
        ref_paths = {}
        for digest, size in refs.items():
            name = f"references/{digest}.png"
            raw = _read(archive, members[name], styles.MAX_REFERENCE_BYTES, digest)
            if _png(raw, name, reference=True) != size:
                raise ArchiveError(f"{name} does not match its recorded dimensions.")
            path = ref_folder / f"{digest}.png"
            library._atomic_write(path, raw)
            ref_paths[digest] = path

        snapshots = stage / "snapshots"
        snapshots.mkdir()
        for row in rows:
            guide = row["guide"]
            raw = _read(archive, members[guide["path"]], MAX_GUIDE_BYTES, guide["sha256"])
            spec = _snapshot_spec(studio.styles, row, raw.decode("utf-8"), ref_paths.__getitem__)
            current = {"id": style_id, "revision": row["revision"], **spec, "builtIn": False, "createdAt": row["createdAt"]}
            library._atomic_write(snapshots / f"{row['revision']}.json", styles._encode_snapshot(current))

        destination = library.Library(stage / "library")
        for digest in artwork:
            name = f"artwork/{digest}.png"
            raw = _read(archive, members[name], MAX_ART_BYTES, digest)
            _png(raw, name)
            library._atomic_write(destination._blob_path(digest), raw)
        for asset_id, record in records.items():
            destination._write_record(asset_id, record)
            destination._read(asset_id)
    return current, ref_paths


def _publish_import(studio, stage, style_id, refs):
    with studio.lock, studio.styles._exclusive():
        library_root = studio.library_root.resolve()
        final_library = studio.library_root / style_id
        final_style = studio.styles._path("snapshots", style_id)
        if (not library_root.is_relative_to(studio.root) or final_library.resolve() != library_root / style_id
                or not final_style.resolve().is_relative_to(studio.root)):
            raise ArchiveError("Imported style data must stay inside Bacchus's data folder.", 409)
        if final_library.exists() or final_style.exists():
            raise ArchiveError("Another style already uses the generated storage ID. Try importing again.", 409)
        library_root.mkdir(exist_ok=True)
        pending = []
        for digest, path in refs.items():
            target = studio.styles._path("references", f"{digest}.png")
            if target.exists():
                try:
                    _source_png(target, digest, styles.MAX_REFERENCE_BYTES, f"Saved style reference {digest[:12]}", reference=True)
                except ArchiveError as error:
                    raise ArchiveError("A shared style reference is damaged. Repair it before importing this archive.", 409) from error
            else:
                pending.append((path, target))
        installed = []
        staged_library = stage / "library"
        try:
            for path, target in pending:
                path.rename(target)
                installed.append(target)
            staged_library.rename(final_library)
            (stage / "snapshots").rename(final_style)
        except OSError:
            if final_library.is_dir():
                final_library.rename(staged_library)
            for path in installed:
                path.unlink(missing_ok=True)
            raise


def import_style(studio, source: Path):
    """Validate an entire ZIP before making its new local style visible."""
    style_id = uuid4().hex
    try:
        with TemporaryDirectory(prefix=".bacchus-style-import-", dir=studio.root) as path:
            stage = Path(path)
            try:
                current, refs = _parse_zip(studio, Path(source), stage, style_id)
            except ArchiveError:
                raise
            except styles.StyleError as error:
                raise ArchiveError(str(error), error.status) from error
            except ZIP_ERRORS as error:
                raise ArchiveError("The style ZIP or its manifest could not be read. Export the style again.") from error
            _publish_import(studio, stage, style_id, refs)
            return current
    except ArchiveError:
        raise
    except (library.LibraryError, styles.StyleError) as error:
        raise ArchiveError(f"Bacchus could not install the style archive. {error}", error.status) from error
    except OSError as error:
        raise ArchiveError("Bacchus could not install the new style in its data folder.", 500) from error
