"""Verify reused Parakeet weights and provision a pinned managed release."""

from functools import lru_cache
import hashlib
from http.client import HTTPException
from pathlib import Path
from shutil import disk_usage
import stat
import tarfile
from tempfile import TemporaryDirectory
from threading import RLock, Thread
from time import monotonic
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .storage_paths import StoragePathError, writable_path


MODEL_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
MODEL_LABEL = "Parakeet TDT 0.6B v2 (English)"
MODEL_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
# The official release received an updated ONNX export. Accept both known versions for manually selected folders.
MANAGED_SIZES = (652282309, 7257753, 1739080, 9384)
EXISTING_SIZES = (652184296, 7257753, 1739080, 9384)
MANAGED_HASHES = (
    "92a10bd9c23936ea0566c29f963bc57c2a5df4261b6a8921c39abea66557ca8b",
    "4d097bae3bac5c96018d5293ba39f1b657296102b142affda40378f38b020f41",
    "77a29f3e5ac7d37074e0af465391b80f314015d5b97f0f90c6c7767f1d56f428",
    "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
)
EXISTING_HASHES = (
    "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab",
    "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e",
    "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2",
    "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
)
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_NAME}.tar.bz2"
ARCHIVE_BYTES = 482440461
ARCHIVE_SHA256 = "6fc4426b54aa549a0019b8a75b8dea59164e71790a37ac56a045ad57f915f714"
_CHUNK = 1024 * 1024
_ACTIVE = {"downloading", "verifying"}


class TimingModelError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class _ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if url.scheme != "https" or url.hostname not in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        }:
            raise TimingModelError("The Parakeet download redirected outside the official release host.", 502)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _identity(path):
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise TimingModelError("The Parakeet folder must contain the four model files.")
    return (details.st_size, details.st_mtime_ns, details.st_ctime_ns, details.st_dev, details.st_ino)


@lru_cache(maxsize=32)
def _fingerprint(path, identity):
    with path.open("rb") as file:
        sha256 = hashlib.file_digest(file, "sha256").hexdigest()
    if _identity(path) != identity:
        raise TimingModelError("A Parakeet model file changed during verification.")
    return sha256


def _verify(folder, manifests):
    try:
        files = tuple(folder / name for name in MODEL_FILES)
        identities = tuple(_identity(file) for file in files)
        sizes = tuple(identity[0] for identity in identities)
        hashes = [expected for lengths, expected in manifests if lengths == sizes]
        if not hashes or tuple(_fingerprint(file, identity) for file, identity in zip(files, identities, strict=True)) not in hashes:
            raise TimingModelError("The Parakeet files do not match a supported release. Select a verified folder.")
    except FileNotFoundError as error:
        raise TimingModelError("Choose a Parakeet folder containing the encoder, decoder, joiner, and tokens files.") from error
    except OSError as error:
        raise TimingModelError("Bacchus could not read the Parakeet model files.") from error
    return folder


def _download(destination, progress):
    request = Request(MODEL_URL, headers={"User-Agent": "Bacchus Parakeet model setup"})
    checksum = hashlib.sha256()
    received = 0
    deadline = monotonic() + 1800
    try:
        with build_opener(_ReleaseRedirect()).open(request, timeout=30) as response, destination.open("xb") as output:
            length = response.headers.get("Content-Length")
            if length is not None and length != str(ARCHIVE_BYTES):
                raise TimingModelError("The Parakeet release download has an unexpected size.", 502)
            while chunk := response.read(_CHUNK):
                received += len(chunk)
                if received > ARCHIVE_BYTES or monotonic() > deadline:
                    raise TimingModelError("The Parakeet download exceeded its size or time limit.", 502)
                checksum.update(chunk)
                output.write(chunk)
                progress(received)
    except (URLError, HTTPException, TimeoutError) as error:
        raise TimingModelError("The Parakeet download did not finish. Check the connection and retry.", 502) from error
    if received != ARCHIVE_BYTES or checksum.hexdigest() != ARCHIVE_SHA256:
        raise TimingModelError("The Parakeet archive failed its SHA-256 verification.", 502)


def _extract(archive, destination):
    required = {f"{MODEL_NAME}/{name}": (name, size, sha256) for name, size, sha256 in zip(
        MODEL_FILES, MANAGED_SIZES, MANAGED_HASHES, strict=True,
    )}
    with tarfile.open(archive, mode="r|bz2") as release:
        found = set()
        for member in release:
            name = member.name.removeprefix("./")
            if name not in required:
                continue
            filename, size, sha256 = required[name]
            if filename in found or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.size != size:
                raise TimingModelError("The Parakeet archive contains an unexpected model file.", 502)
            source = release.extractfile(member)
            if source is None:
                raise TimingModelError("The Parakeet archive is missing a readable model file.", 502)
            checksum = hashlib.sha256()
            with source, (destination / filename).open("xb") as output:
                while chunk := source.read(_CHUNK):
                    checksum.update(chunk)
                    output.write(chunk)
            if checksum.hexdigest() != sha256:
                raise TimingModelError("A Parakeet model file failed SHA-256 verification.", 502)
            found.add(filename)
        if found != set(MODEL_FILES):
            raise TimingModelError("The Parakeet release is missing required model files.", 502)


class TimingModels:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve()
        self.directory = self.root / "models" / MODEL_NAME
        self._lock = RLock()
        self._worker = None
        self._progress = self._installation("idle")

    @staticmethod
    def _installation(state, downloaded=0, error=None):
        return {"state": state, "downloadedBytes": downloaded, "totalBytes": ARCHIVE_BYTES, "error": error}

    @property
    def installing(self):
        with self._lock:
            return self._progress["state"] in _ACTIVE

    def _managed_directory(self):
        if not self.directory.resolve().is_relative_to(self.root) or self.directory.is_symlink():
            raise TimingModelError("Bacchus cannot use a managed Parakeet folder outside its data directory.")
        return self.directory

    def validate_existing(self, directory):
        if not isinstance(directory, (str, Path)) or not str(directory).strip() or len(str(directory)) > 2048:
            raise TimingModelError("Choose an absolute path to the existing Parakeet model folder.")
        path = Path(directory)
        if not path.is_absolute():
            raise TimingModelError("Choose an absolute path to the existing Parakeet model folder.")
        try:
            return _verify(path.resolve(), ((EXISTING_SIZES, EXISTING_HASHES), (MANAGED_SIZES, MANAGED_HASHES)))
        except TimingModelError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            raise TimingModelError("Bacchus could not open the selected Parakeet folder.") from error

    def status(self, existing_directory=None):
        path, source, error = None, None, None
        if existing_directory:
            try:
                path = self.validate_existing(existing_directory)
                source = "existing"
            except TimingModelError as failure:
                error = str(failure)
        with self._lock:
            progress = self._progress.copy()
        if source is None or progress["state"] not in _ACTIVE:
            try:
                managed = self._managed_directory()
                if managed.exists():
                    _verify(managed, ((MANAGED_SIZES, MANAGED_HASHES),))
                    if not source:
                        path, source = managed, "managed"
                    if progress["state"] not in _ACTIVE:
                        progress = self._installation("ready", ARCHIVE_BYTES)
                elif progress["state"] == "ready":
                    progress = self._installation("idle")
            except TimingModelError as failure:
                if progress["state"] not in _ACTIVE and source is None:
                    error = error or str(failure)
                    if progress["state"] == "ready":
                        progress = self._installation("failed", error=error)
        return {"ready": path is not None, "source": source, "directory": str(path) if path else None,
                "error": error, "installation": progress}

    def resolve(self, existing_directory=None):
        status = self.status(existing_directory)
        if not status["ready"]:
            raise TimingModelError(status["error"] or "Set up the Parakeet model or import recording-matched word cues.")
        return Path(status["directory"])

    def start_install(self):
        with self._lock:
            status = self.status()
            if status["ready"] or self.installing:
                return status
            self._progress = self._installation("downloading")
            self._worker = Thread(target=self._install, name="bacchus-parakeet-install", daemon=True)
            try:
                self._worker.start()
            except RuntimeError as error:
                self._progress = self._installation("failed", error="Bacchus could not start Parakeet setup.")
                raise TimingModelError(self._progress["error"], 500) from error
        return self.status()

    def _download_progress(self, received):
        with self._lock:
            self._progress["downloadedBytes"] = received

    def _install(self):
        error = "Bacchus could not finish installing Parakeet. Check the drive and retry."
        try:
            target = writable_path(self._managed_directory())
            target.parent.mkdir(parents=True, exist_ok=True)
            if disk_usage(target.parent).free < ARCHIVE_BYTES + sum(MANAGED_SIZES):
                raise TimingModelError("Free about 1.1 GB in the Bacchus data drive before installing Parakeet.")
            with TemporaryDirectory(prefix=".parakeet-", dir=target.parent) as name:
                work = Path(name)
                archive = work / "model.tar.bz2"
                staged = work / "files"
                staged.mkdir()
                _download(archive, self._download_progress)
                with self._lock:
                    self._progress = self._installation("verifying", ARCHIVE_BYTES)
                _extract(archive, staged)
                self._publish(staged, self._managed_directory())
            error = None
        except (StoragePathError, TimingModelError) as failure:
            error = str(failure)
        except (OSError, ValueError, tarfile.TarError):
            pass
        finally:
            with self._lock:
                self._progress = self._installation(
                    "failed" if error else "ready", self._progress["downloadedBytes"], error,
                )

    @staticmethod
    def _publish(staged, target):
        target = writable_path(target)
        previous = None
        if target.exists():
            try:
                _verify(target, ((MANAGED_SIZES, MANAGED_HASHES),))
                return
            except TimingModelError:
                previous = target.with_name(f".invalid-model-{uuid4().hex}")
                target.rename(previous)
        try:
            staged.rename(target)
        except OSError:
            if previous and not target.exists():
                previous.rename(target)
            raise
