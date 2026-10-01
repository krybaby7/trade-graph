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

from trade_graph.adapters.persistence.migrate import apply_migrations
from trade_graph.domain.clock import SystemClock, utc_iso


def atomic[T](method: Callable[..., T]) -> Callable[..., T]:
    """Join application operations into one writer transaction (no awaits inside)."""
    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> T:
        with self.database.immediate():
            return method(self, *args, **kwargs)
    return wrapped


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connection = self._open()
        self._connection.execute("PRAGMA journal_mode=WAL")
        apply_migrations(self._connection, utc_iso(SystemClock().now()))

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @property
    def connection(self) -> sqlite3.Connection:
        return getattr(self._local, "connection", None) or self._connection

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
    def immediate(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._local, "connection", None)
        if active is not None:
            savepoint = "nested_" + uuid.uuid4().hex
            active.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
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
        self._connection.close()
