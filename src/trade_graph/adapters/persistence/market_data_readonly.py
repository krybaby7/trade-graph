"""The candle file opened for reading only (HQ price charts, T41, D199).

HQ shows candles but must never change the collector's file: ``MarketDataDatabase`` creates a missing file and applies
pending upgrades, which a dashboard read must not do. This handle opens an existing file with SQLite's read-only mode,
checks the same privacy rules as the store (no symlinks, a private folder and file) and that every candle-file upgrade
is already applied, and refuses writes. It offers the read surface ``CandleStore`` uses (``query``, ``execute``) and
one read snapshot for a request.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from trade_graph.adapters.persistence.market_data_schema import MARKET_DATA_MIGRATIONS
from trade_graph.domain.errors import TradeGraphError, ValidationFailure

UnavailableReason = Literal["missing", "unreadable", "outdated"]


class MarketDataUnavailable(TradeGraphError):
    """The candle file can't be read for charts; ``reason`` says which way (never a path or a secret)."""

    code = "market_data_unavailable"

    def __init__(self, reason: UnavailableReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


def _check(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise MarketDataUnavailable("unreadable", "the candle file and its folder must not be symlinks")
    if not path.exists():
        raise MarketDataUnavailable("missing", "there is no candle file yet")
    if not path.is_file():
        raise MarketDataUnavailable("unreadable", "the candle file is not a regular file")
    if path.parent.stat().st_mode & 0o077 or path.stat().st_mode & 0o077:
        raise MarketDataUnavailable("unreadable", "the candle file and its folder must be private (700 and 600)")


class ReadOnlyMarketData:
    """One read-only connection to an existing, fully upgraded candle file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        _check(self.path)
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True,
                                               isolation_level=None, timeout=30, check_same_thread=False)
        except sqlite3.Error as error:
            raise MarketDataUnavailable("unreadable", "the candle file could not be opened") from error
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA query_only=ON")
            self._connection.execute("PRAGMA busy_timeout=30000")
            tables = {row[0] for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "market_data_migrations" not in tables:
                raise MarketDataUnavailable("unreadable", "the file is not a candle file")
            applied = {row[0] for row in self._connection.execute("SELECT version FROM market_data_migrations")}
            if not {version for version, _ in MARKET_DATA_MIGRATIONS} <= applied:
                raise MarketDataUnavailable("outdated", "the candle file still needs an upgrade by the collector")
        except sqlite3.DatabaseError as error:
            self._connection.close()
            raise MarketDataUnavailable("unreadable", "the candle file could not be read") from error
        except BaseException:
            self._connection.close()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._connection.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, params).fetchall()

    @contextmanager
    def snapshot(self) -> Iterator[sqlite3.Connection]:
        """One read transaction, so every read of a request sees the same file (writers keep going in WAL)."""
        with self._lock:
            connection = self._connection
            if connection.in_transaction:
                yield connection
                return
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        raise ValidationFailure("HQ reads the candle file only; it never writes it")
        yield  # pragma: no cover - a generator for the context manager protocol

    def close(self) -> None:
        with self._lock:
            self._connection.close()


__all__ = ["MarketDataUnavailable", "ReadOnlyMarketData", "UnavailableReason"]
