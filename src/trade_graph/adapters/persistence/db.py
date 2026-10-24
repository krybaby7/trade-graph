"""SQLite transactions compose across ledger, projections and reservations."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any

from trade_graph.adapters.persistence.inode_locks import (
    DatabaseFile,
    connection_closed,
    connection_opened,
)
from trade_graph.adapters.persistence.inode_locks import (
    DatabaseOwnershipConflict as DatabaseOwnershipConflict,
)
from trade_graph.adapters.persistence.inode_locks import (
    exclusive_database_path as exclusive_database_path,
)
from trade_graph.adapters.persistence.migrate import apply_migrations
from trade_graph.domain.clock import SystemClock, utc_iso


def atomic[T](method: Callable[..., T]) -> Callable[..., T]:
    """Join application operations into one writer transaction (no awaits inside)."""
    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> T:
        with self.database.immediate():
            return method(self, *args, **kwargs)
    return wrapped


class _TrackedConnection(sqlite3.Connection):
    _inode = None

    def close(self) -> None:
        super().close()
        inode, self._inode = self._inode, None
        if inode is not None:
            connection_closed(inode)

    def __del__(self):
        try:
            self.close()
        except Exception:
            # sqlite3 may reject close after partial construction or teardown.
            pass


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lifecycle = threading.RLock()
        self._closed = False
        self._file = None
        self._file = DatabaseFile(self.path, create=True) if str(self.path) != ":memory:" else None
        self._local = threading.local()
        self._serialization = threading.RLock()
        try:
            self._connection = self._open()
            self._connection.execute("PRAGMA journal_mode=WAL")
            apply_migrations(self._connection, utc_iso(SystemClock().now()))
        except BaseException:
            self.close()
            raise

    def _open(self) -> sqlite3.Connection:
        with self._lifecycle:
            return self._open_connection()

    def _open_connection(self) -> sqlite3.Connection:
        if self._closed:
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")
        if self._file is not None:
            self._file.identity(single_link=False)
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False,
                                     factory=_TrackedConnection)
        if self._file is not None:
            connection_opened(self._file.inode)
            connection._inode = self._file.inode
        try:
            if self._file is not None:
                self._file.identity(single_link=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            return connection
        except BaseException:
            connection.close()
            raise

    def file_identity(self) -> list[int]:
        if self._file is None:
            raise ValueError("on-disk database identity required")
        return self._file.identity()

    def retained_descriptor(self, purpose: str) -> int:
        if self._file is None:
            raise ValueError("on-disk database ownership required")
        return self._file.descriptor(purpose)

    @contextmanager
    def exclusive_lock(self, purpose: str = "exclusive-controller", *, single_link: bool = True) -> Iterator[int]:
        if self._file is None:
            raise ValueError("on-disk database ownership required")
        with self._file.exclusive_lock(purpose, single_link=single_link) as descriptor:
            yield descriptor

    @property
    def connection(self) -> sqlite3.Connection:
        return (getattr(self._local, "connection", None)
                or getattr(self._local, "thread_connection", None) or self._connection)

    @contextmanager
    def thread_connection(self) -> Iterator[sqlite3.Connection]:
        """Bind an idle connection to a worker thread without opening a transaction.

        Application objects can share this Database while a synchronous model
        handler runs alongside maintenance. Writer transactions and read snapshots
        continue to bind their own connections for their usual short lifetimes.
        """
        active = getattr(self._local, "connection", None) or getattr(self._local, "thread_connection", None)
        if active is not None:
            yield active
            return
        connection = self._open()
        self._local.thread_connection = connection
        try:
            yield connection
        finally:
            self._local.thread_connection = None
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._local, "connection", None)
        if active is not None:
            yield active
            return
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def serialized(self) -> Iterator[None]:
        """Order this controller's writers and independent witness publication.

        SQLite already permits only one writer. The local reentrant lock also
        covers the committed-checkpoint/witness-publication interval, so worker
        and management threads cannot interpret their own writer as a competing
        controller. Cross-process witness ownership remains independently locked.
        """
        with self._serialization:
            yield

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        with self.serialized():
            with self._immediate() as connection:
                yield connection

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._local, "connection", None)
        if active is not None:
            savepoint = "nested_" + uuid.uuid4().hex
            active.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                # SQLITE_FULL and RAISE(ROLLBACK) can end the whole transaction,
                # including its savepoints. Preserve the original storage error.
                if active.in_transaction:
                    active.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    active.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            return
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._local.connection = connection
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._local.connection = None

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """One read snapshot for all values rendered by a dashboard request."""
        active = getattr(self._local, "connection", None)
        if active is not None:
            yield active
            return
        with self.connect() as connection:
            connection.execute("BEGIN")
            self._local.connection = connection
            try:
                yield connection
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                self._local.connection = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.immediate() as connection:
            yield connection

    def close(self) -> None:
        with self._lifecycle:
            self._close_connections()

    def _close_connections(self) -> None:
        if self._closed:
            return
        self._closed = True
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
        if self._file is not None:
            self._file.close()

    def __del__(self):
        try:
            if hasattr(self, "_closed"):
                self.close()
        except Exception:
            # Explicit close reports errors; object finalization cannot do so.
            pass
