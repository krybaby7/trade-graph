"""Retain raw main-file handles until SQLite has released every local connection.

POSIX closing any descriptor for an inode releases this process's byte locks on
that inode. SQLite manages its own descriptors; auxiliary handles must outlive
all SQLite connections, including connections owned by another Database object.
"""

from __future__ import annotations

import fcntl
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from trade_graph.adapters.engineering.artifact_files import open_directory
from trade_graph.domain.errors import StaleState


class DatabaseOwnershipConflict(StaleState):
    """Another local or external controller owns the database inode."""


@dataclass
class _Inode:
    identity: tuple[int, int]
    descriptors: set[int] = field(default_factory=set)
    owners: int = 0
    connections: int = 0


_registry_lock = threading.RLock()
_registry: dict[tuple[int, int], _Inode] = {}


def _adopt(descriptor: int) -> _Inode:
    identity = os.fstat(descriptor)
    key = (identity.st_dev, identity.st_ino)
    with _registry_lock:
        inode = _registry.setdefault(key, _Inode(key))
        inode.descriptors.add(descriptor)
        return inode


def _drain(inode: _Inode) -> None:
    # Caller holds the registry lock. Never close a raw descriptor while another
    # Database owner, SQLite connection or exclusive lease can still use SQLite.
    if inode.owners or inode.connections:
        return
    _registry.pop(inode.identity, None)
    descriptors, inode.descriptors = inode.descriptors, set()
    for descriptor in descriptors:
        os.close(descriptor)


def connection_opened(inode: _Inode) -> None:
    with _registry_lock:
        inode.connections += 1


def connection_closed(inode: _Inode) -> None:
    with _registry_lock:
        inode.connections -= 1
        _drain(inode)


class DatabaseFile:
    def __init__(self, path: Path, *, create: bool = False):
        self.path = path.absolute()
        self._mutex = threading.RLock()
        self._leases: dict[str, threading.Lock] = {}
        self._descriptors: dict[str, int] = {}
        self._closed = False
        self.inode: _Inode | None = None
        directory = None
        try:
            directory = open_directory(self.path.parent)
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if create:
                try:
                    descriptor = os.open(self.path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                except FileExistsError:
                    descriptor = os.open(self.path.name, flags, dir_fd=directory)
            else:
                descriptor = os.open(self.path.name, flags, dir_fd=directory)
            self.inode = _adopt(descriptor)
            with _registry_lock:
                self.inode.owners += 1
            self._descriptors["identity"] = descriptor
            self._check(directory, single_link=False)
        except BaseException as exc:
            self.close()
            if isinstance(exc, OSError):
                raise StaleState("database secure path refused") from None
            raise
        finally:
            if directory is not None:
                os.close(directory)

    def _check(self, directory: int, *, single_link: bool = True) -> list[int]:
        if self._closed or self.inode is None:
            raise StaleState("database file owner is closed")
        identity = os.fstat(self._descriptors["identity"])
        current = os.stat(self.path.name, dir_fd=directory, follow_symlinks=False)
        if (not stat.S_ISREG(identity.st_mode) or not stat.S_ISREG(current.st_mode)
                or single_link and (identity.st_nlink != 1 or current.st_nlink != 1)
                or (identity.st_dev, identity.st_ino) != self.inode.identity
                or (current.st_dev, current.st_ino) != self.inode.identity):
            raise StaleState("database single regular file identity changed")
        return list(self.inode.identity)

    def identity(self, *, single_link: bool = True) -> list[int]:
        directory = None
        try:
            directory = open_directory(self.path.parent)
            return self._check(directory, single_link=single_link)
        except OSError:
            raise StaleState("database secure path refused") from None
        finally:
            if directory is not None:
                os.close(directory)

    def descriptor(self, purpose: str, *, single_link: bool = True) -> int:
        if not isinstance(purpose, str) or not purpose or len(purpose) > 128:
            raise ValueError("bounded descriptor purpose required")
        with self._mutex:
            self.identity(single_link=single_link)
            if purpose in self._descriptors:
                return self._descriptors[purpose]
            directory = None
            retained = None
            try:
                directory = open_directory(self.path.parent)
                descriptor = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                     dir_fd=directory)
                retained = _adopt(descriptor)
                if retained is not self.inode:
                    raise StaleState("database file identity changed during acquisition")
                self._check(directory, single_link=single_link)
                self._descriptors[purpose] = descriptor
                return descriptor
            except OSError:
                raise StaleState("database secure path refused") from None
            finally:
                if directory is not None:
                    os.close(directory)
                # A raced replacement may itself have another Database owner.
                # Its raw descriptor follows the same process-wide drain rule.
                if retained is not None and retained is not self.inode:
                    with _registry_lock:
                        _drain(retained)

    LOCK_SUFFIX = ".lock"

    def _lock_descriptor(self) -> int:
        """Open, creating it if needed, the private sidecar lock file beside the database.

        The lease is held with flock() on this file, never on the database inode. On
        macOS and the BSDs flock() and fcntl() share one lock table, so a lease on the
        inode collides with SQLite's own locks in the same process; Linux keeps the two
        families apart, so the sidecar changes nothing there. The file is never
        unlinked: unlinking a live lock file would let two holders coexist.
        """
        directory = None
        try:
            directory = open_directory(self.path.parent)
            flags = os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
            descriptor = os.open(self.path.name + self.LOCK_SUFFIX, flags, 0o600, dir_fd=directory)
        except OSError:
            raise StaleState("database lock file refused") from None
        finally:
            if directory is not None:
                os.close(directory)
        try:
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                raise StaleState("database lock file must be a single regular file")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @contextmanager
    def exclusive_lock(self, purpose: str, *, single_link: bool = True) -> Iterator[int]:
        with self._mutex:
            mutex = self._leases.setdefault(purpose, threading.Lock())
        if not mutex.acquire(blocking=False):
            raise DatabaseOwnershipConflict("another local controller owns the database")
        descriptor, acquired = -1, False
        try:
            self.identity(single_link=single_link)
            descriptor = self._lock_descriptor()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DatabaseOwnershipConflict("another service or controller owns the database") from exc
            acquired = True
            self.identity(single_link=single_link)
            yield descriptor
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            if descriptor >= 0:
                os.close(descriptor)
            mutex.release()

    def close(self) -> None:
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            if self.inode is not None:
                with _registry_lock:
                    self.inode.owners -= 1
                    _drain(self.inode)


@contextmanager
def exclusive_database_path(path: Path | str, purpose: str = "exclusive-controller") -> Iterator[int]:
    """Acquire before migrations; safely defer closure across existing owners."""
    owner = DatabaseFile(Path(path))
    try:
        with owner.exclusive_lock(purpose) as descriptor:
            yield descriptor
    finally:
        owner.close()
