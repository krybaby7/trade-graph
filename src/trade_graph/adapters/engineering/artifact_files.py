"""Bounded no-follow artifact file proxy; production paths are never copied to checks."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from trade_graph.adapters.engineering.artifact_policy import PREFIXES, artifact_class

MAX_FILE_BYTES = 65536
MAX_TREE_BYTES = 262144
MAX_TREE_FILES = 32
MAX_TREE_ENTRIES = 128


def open_directory(path: Path) -> int:
    """Resolve each component with directory FDs and O_NOFOLLOW, including parents."""
    path = path.absolute()
    if ".." in path.parts:
        raise PermissionError("parent traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def ensure_directory(path: Path) -> None:
    """Create through verified parents, rather than following a symlink in mkdir(parents)."""
    path = path.absolute()
    if ".." in path.parts:
        raise PermissionError("parent traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)


def read_tree(root: Path, *, source: bool = False) -> dict[str, str]:
    files: dict[str, str] = {}
    entries, total = 0, 0

    def walk(fd: int, prefix: str, depth: int) -> None:
        nonlocal entries, total
        if depth > 8:
            raise ValueError("artifact directory depth exceeded")
        with os.scandir(fd) as iterator:
            for entry in iterator:
                entries += 1
                if entries > MAX_TREE_ENTRIES:
                    raise ValueError("artifact entry limit exceeded")
                info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise PermissionError("symlink artifacts are forbidden")
                if not prefix and entry.name == ".git" and stat.S_ISDIR(info.st_mode):
                    continue
                relative = prefix + entry.name
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child, relative + "/", depth + 1)
                    finally:
                        os.close(child)
                    continue
                child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    actual = os.fstat(child)
                    if not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
                        raise PermissionError("only single-link regular artifact files are permitted")
                    if actual.st_size > MAX_FILE_BYTES:
                        raise ValueError("artifact file bytes exceeded")
                    data = bytearray()
                    while len(data) <= MAX_FILE_BYTES:
                        chunk = os.read(child, min(65536, MAX_FILE_BYTES + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                    if len(data) > MAX_FILE_BYTES:
                        raise ValueError("artifact file bytes exceeded")
                finally:
                    os.close(child)
                total += len(data)
                if total > MAX_TREE_BYTES or len(files) >= MAX_TREE_FILES:
                    raise ValueError("artifact tree resource bound exceeded")
                files[relative] = data.decode("utf-8")
                if source:
                    artifact_class(relative)  # No hidden caches/code/secrets even under an allowed prefix.

    if source:
        # Never enumerate the production root, its .git, .env, database or private caches.
        root_fd = open_directory(root)
        os.close(root_fd)
        for prefix in PREFIXES:
            try:
                fd = open_directory(root / prefix)
            except FileNotFoundError:
                continue
            try:
                walk(fd, prefix, 1)
            finally:
                os.close(fd)
    else:
        fd = open_directory(root)
        try:
            walk(fd, "", 0)
        finally:
            os.close(fd)
    return dict(sorted(files.items()))


def write_file(root: Path, relative: str, content: str) -> None:
    artifact_class(relative)
    target = root / relative
    ensure_directory(target.parent)
    parent = open_directory(target.parent)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(target.name, flags, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("artifact is not a private regular file")
            data = content.encode()
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("artifact file bytes exceeded")
            os.ftruncate(fd, 0)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def content_hash(files: dict[str, str]) -> str:
    # Preserve historical baseline identity; the unambiguous manifest hash is ALSO attested.
    digest = hashlib.sha256()
    for name, text in sorted(files.items()):
        digest.update(name.encode())
        digest.update(text.encode())
    return digest.hexdigest()


def manifest(files: dict[str, str]) -> dict:
    entries = [{"path": name, "bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}
               for name, text in sorted(files.items())]
    return {"files": entries, "sha256": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()}
