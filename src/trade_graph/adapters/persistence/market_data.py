"""The separate private SQLite file that holds candle history (see docs/CANDLE-STORE.md)."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from trade_graph.adapters.persistence.market_data_schema import MARKET_DATA_MIGRATIONS, REBUILD_VERSIONS
from trade_graph.domain.clock import SystemClock, utc_iso
from trade_graph.domain.errors import ValidationFailure

DEFAULT_MARKET_DATABASE = "runtime/market_data.sqlite"


def _prepare(path: Path) -> None:
    """Private parent directory and file; no symlinks. Candle history is public data but still runtime state."""
    if path.is_symlink() or path.parent.is_symlink():
        raise ValidationFailure("the market-data database and its directory must not be symlinks")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValidationFailure("the market-data directory must be private (mode 700)")
    if path.exists():
        if not path.is_file():
            raise ValidationFailure("the market-data database must be a regular file")
        if path.stat().st_mode & 0o077:
            raise ValidationFailure("the market-data database must be private (mode 600)")
    else:
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600))


class MarketDataDatabase:
    """One serialized connection. Writers use ``immediate()``; reads run between transactions."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._depth = 0
        if str(self.path) != ":memory:":
            _prepare(self.path)
        self._connection = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._migrate()
        except BaseException:
            self._connection.close()
            raise

    def _migrate(self) -> None:
        """Apply pending versions in one transaction. A table rebuild (``REBUILD_VERSIONS``) runs with foreign keys
        off, as SQLite documents for rebuilding a referenced table, and commits only after a full foreign-key check
        finds nothing; foreign keys are switched back on whatever happens."""
        connection = self._connection
        connection.execute("PRAGMA foreign_keys=OFF")  # only takes effect outside a transaction, so set it first
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS market_data_migrations "
                    "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
                done = {row[0] for row in connection.execute("SELECT version FROM market_data_migrations")}
                rebuilt = False
                for version, statements in MARKET_DATA_MIGRATIONS:
                    if version in done:
                        continue
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute("INSERT INTO market_data_migrations VALUES (?, ?)",
                                       (version, utc_iso(SystemClock().now())))
                    rebuilt = rebuilt or version in REBUILD_VERSIONS
                if rebuilt and connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValidationFailure("a candle-history upgrade left a broken reference; nothing was changed")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._connection.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, params).fetchall()

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        """One writer transaction; nesting joins it through a savepoint."""
        with self._lock:
            connection = self._connection
            if self._depth:
                savepoint = "nested_" + uuid.uuid4().hex
                connection.execute(f"SAVEPOINT {savepoint}")
                self._depth += 1
                try:
                    yield connection
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    raise
                finally:
                    self._depth -= 1
                return
            connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._depth = 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()
