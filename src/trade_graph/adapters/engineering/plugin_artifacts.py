"""Private content-addressed pure-feature staging; no production promotion route.

Only the trusted parent writes this store. Read-only files plus verified digests
seal staged bytes against candidates, not a host administrator. Candidate child
processes receive source bytes over stdin and cannot mount or open this store.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trade_graph.adapters.engineering.artifact_files import ensure_directory, open_directory

MAX_SOURCE_BYTES = 65536
MAX_MANIFEST_BYTES = 8192
MAX_RECEIPT_BYTES = 524288
SHA256_PATTERN = r"^[0-9a-f]{64}$"


def canonical_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_digest(value: str) -> None:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("a lowercase SHA-256 digest is required")


class PluginStageManifest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal[1] = 1
    class_name: Literal["pure_feature_plugin"] = "pure_feature_plugin"
    contract: Literal["numeric_features/v1"] = "numeric_features/v1"
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    source_bytes: int = Field(ge=1, le=MAX_SOURCE_BYTES)
    build_digest: str = Field(pattern=SHA256_PATTERN)
    baseline_release_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")
    baseline_source_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    validation_corpus_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("plugin schema version must be an integer")
        return value

    def identity(self) -> dict:
        return self.model_dump(exclude={"build_digest"})


@dataclass(frozen=True)
class PluginStage:
    manifest: PluginStageManifest
    source_text: str
    manifest_sha256: str


class PluginStageStore:
    """Owner-private, no-follow, append-only fixed-file staging and receipt store.

    Directory ownership is a required deployment premise. An unrestricted host
    owner can change files, but each subsequent load checks content and identity.
    No candidate-supplied path, archive, test command, dependency or gate enters.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).absolute()
        for directory in (self.root, self.root / "stages", self.root / "receipts"):
            ensure_directory(directory)
            fd = open_directory(directory)
            try:
                self._private_directory(fd)
            finally:
                os.close(fd)

    @staticmethod
    def _private_directory(fd: int, *, sealed: bool = False) -> None:
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or (sealed and stat.S_IMODE(info.st_mode) != 0o500)):
            raise PermissionError("plugin store requires owner-private directories and sealed stage modes")

    @staticmethod
    def _read_file(fd: int, name: str, bound: int) -> bytes:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
        try:
            before = os.fstat(child)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o400):
                raise PermissionError("plugin object must be a sealed owner-only single-link regular file")
            if before.st_size > bound:
                raise ValueError("plugin object exceeds size bound")
            data = bytearray()
            while len(data) <= bound:
                chunk = os.read(child, min(65536, bound + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(child)
            if len(data) > bound or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("plugin object changed or exceeded size bound")
            return bytes(data)
        finally:
            os.close(child)

    @staticmethod
    def _write_file(fd: int, name: str, data: bytes) -> None:
        child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                        0o600, dir_fd=fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(child, view)
                view = view[written:]
            os.fchmod(child, 0o400)
            os.fsync(child)
        finally:
            os.close(child)

    def _collection(self, name: str) -> int:
        root = open_directory(self.root)
        try:
            self._private_directory(root)
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root)
            try:
                self._private_directory(fd)
            except BaseException:
                os.close(fd)
                raise
            return fd
        finally:
            os.close(root)

    def _object(self, collection: str, digest: str) -> int:
        parent = self._collection(collection)
        try:
            return os.open(digest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        finally:
            os.close(parent)

    def _publish(self, collection: str, digest: str, files: dict[str, bytes]) -> None:
        require_digest(digest)
        parent = self._collection(collection)
        temporary = ".staging-" + uuid.uuid4().hex
        child = None
        published = False
        try:
            self._private_directory(parent)
            fcntl.flock(parent, fcntl.LOCK_EX)
            try:
                existing = os.open(digest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                   dir_fd=parent)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                try:
                    self._private_directory(existing, sealed=True)
                    if set(os.listdir(existing)) != set(files):
                        raise PermissionError("existing plugin object contains unexpected files")
                    if any(self._read_file(existing, name, len(data)) != data for name, data in files.items()):
                        raise ValueError("content-addressed plugin object conflicts with existing bytes")
                finally:
                    os.close(existing)
                return
            os.mkdir(temporary, mode=0o700, dir_fd=parent)
            child = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
            for name, data in files.items():
                self._write_file(child, name, data)
            os.fchmod(child, 0o500)
            os.fsync(child)
            os.rename(temporary, digest, src_dir_fd=parent, dst_dir_fd=parent)
            published = True
            os.fsync(parent)
        except BaseException:
            if child is not None and not published:
                os.fchmod(child, 0o700)
                for name in files:
                    try:
                        os.unlink(name, dir_fd=child)
                    except FileNotFoundError:
                        pass
                try:
                    os.rmdir(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
            raise
        finally:
            if child is not None:
                os.close(child)
            os.close(parent)

    def stage(self, source_text: str, *, baseline_release_id: str, baseline_source_sha256: str,
              protected_manifest_sha256: str, validation_corpus_sha256: str) -> PluginStage:
        if type(source_text) is not str:
            raise ValueError("plugin source must be UTF-8 text")
        source = source_text.encode()
        if not 1 <= len(source) <= MAX_SOURCE_BYTES:
            raise ValueError("plugin source exceeds fixed byte bound")
        identity = {"schema_version": 1, "class_name": "pure_feature_plugin", "contract": "numeric_features/v1",
                    "source_sha256": sha256(source), "source_bytes": len(source),
                    "baseline_release_id": baseline_release_id, "baseline_source_sha256": baseline_source_sha256,
                    "protected_manifest_sha256": protected_manifest_sha256,
                    "validation_corpus_sha256": validation_corpus_sha256}
        manifest = PluginStageManifest(**identity, build_digest=sha256(canonical_bytes(identity)))
        data = canonical_bytes(manifest.model_dump())
        self._publish("stages", manifest.build_digest, {"source.py": source, "manifest.json": data})
        return self.load(manifest.build_digest)

    def load(self, build_digest: str) -> PluginStage:
        require_digest(build_digest)
        fd = self._object("stages", build_digest)
        try:
            self._private_directory(fd, sealed=True)
            if set(os.listdir(fd)) != {"source.py", "manifest.json"}:
                raise PermissionError("plugin stage contains unexpected files")
            manifest_data = self._read_file(fd, "manifest.json", MAX_MANIFEST_BYTES)
            manifest = PluginStageManifest.model_validate_json(manifest_data)
            if (canonical_bytes(manifest.model_dump()) != manifest_data
                    or manifest.build_digest != build_digest
                    or sha256(canonical_bytes(manifest.identity())) != build_digest):
                raise ValueError("plugin manifest content identity mismatch")
            source = self._read_file(fd, "source.py", MAX_SOURCE_BYTES)
            if len(source) != manifest.source_bytes or sha256(source) != manifest.source_sha256:
                raise ValueError("plugin source content identity mismatch")
            return PluginStage(manifest, source.decode("utf-8"), sha256(manifest_data))
        finally:
            os.close(fd)

    def retain_receipt(self, envelope: dict) -> str:
        data = canonical_bytes(envelope)
        if len(data) > MAX_RECEIPT_BYTES:
            raise ValueError("plugin receipt exceeds byte bound")
        digest = sha256(data)
        self._publish("receipts", digest, {"receipt.json": data})
        return digest

    def receipt(self, digest: str) -> dict:
        require_digest(digest)
        fd = self._object("receipts", digest)
        try:
            self._private_directory(fd, sealed=True)
            if set(os.listdir(fd)) != {"receipt.json"}:
                raise PermissionError("plugin receipt contains unexpected files")
            data = self._read_file(fd, "receipt.json", MAX_RECEIPT_BYTES)
            if sha256(data) != digest:
                raise ValueError("plugin receipt content identity mismatch")
            document = json.loads(data)
            if canonical_bytes(document) != data:
                raise ValueError("plugin receipt must use canonical JSON")
            return document
        finally:
            os.close(fd)
