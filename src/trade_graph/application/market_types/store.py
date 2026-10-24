"""The live market-type labels in the state database (migration 0050, W11-A / C10; D287).

``MarketTypeStore.record`` writes one labelling round in one transaction, each label with its document and SHA-256.
Reads verify the hash, so an edited row is refused rather than trusted.

``read_latest_market_types`` is the read the Trading desk's context uses (W12-A, ``ShortlistCoin`` and
``application/events/scan.py``): the newest label of each coin at or before a moment, on any connection, a read-only
one included. A database from before migration 0050 has no labels and gives an empty answer, never an error. A label
older than ``DEFAULT_LABEL_AGE`` (2 hours, like the coin rule's scan, D174) is not current: ``is_fresh`` says so, and a
caller shows it as such rather than as the coin's type now.

Nothing here calls a model, spends money or touches an order.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.market_types.labeller import CoinLabel, describe
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure

TABLE = "market_type_labels"
DEFAULT_LABEL_AGE = timedelta(hours=2)


def canonical_json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredMarketType:
    """One recorded label: the type at ``as_of``, the candle it rests on and everything it read (``document``)."""

    label_id: str
    sequence: int
    symbol: str
    as_of: datetime
    market_type: str
    reason: str | None
    candle_close: datetime | None
    lag_bars: int | None
    interval_minutes: int
    source: str
    rules_ref: str
    rules_sha256: str
    scan_id: str | None
    evidence: str
    document: dict[str, Any]
    recorded_at: datetime

    def age(self, at: datetime) -> timedelta:
        return at - self.as_of

    def is_fresh(self, at: datetime, max_age: timedelta = DEFAULT_LABEL_AGE) -> bool:
        """Was this the coin's label at ``at``: made at or before it, and at most ``max_age`` earlier?"""
        return timedelta(0) <= self.age(at) <= max_age

    def sentence(self) -> str:
        return describe(self.document)

    def to_document(self) -> dict[str, Any]:
        return {"label_id": self.label_id, "symbol": self.symbol, "as_of_utc": utc_iso(self.as_of),
                "market_type": self.market_type, "reason": self.reason,
                "candle_close_utc": None if self.candle_close is None else utc_iso(self.candle_close),
                "lag_bars": self.lag_bars, "interval_minutes": self.interval_minutes, "source": self.source,
                "rules_ref": self.rules_ref, "rules_sha256": self.rules_sha256, "scan_id": self.scan_id,
                "evidence": self.evidence, "sentence": self.sentence(), "document": self.document}


def _read(row: sqlite3.Row) -> StoredMarketType:
    text = row["document_json"]
    if _sha256(text) != row["document_sha256"]:
        raise StaleState(f"market-type label {row['label_id']} no longer matches its stored hash")
    return StoredMarketType(
        label_id=row["label_id"], sequence=row["sequence"], symbol=row["symbol"], as_of=parse_utc(row["as_of"]),
        market_type=row["market_type"], reason=row["reason"],
        candle_close=None if row["candle_close"] is None else parse_utc(row["candle_close"]),
        lag_bars=row["lag_bars"], interval_minutes=row["interval_minutes"], source=row["source"],
        rules_ref=row["rules_ref"], rules_sha256=row["rules_sha256"], scan_id=row["scan_id"],
        evidence=row["evidence"], document=json.loads(text), recorded_at=parse_utc(row["recorded_at"]))


def _has_table(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                              (TABLE,)).fetchone() is not None


def _aware(label: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailure(f"{label} must be a timezone-aware datetime")
    return value


def read_latest_market_types(connection: sqlite3.Connection, *, symbols: Iterable[str] | None = None,
                             as_of: datetime | None = None) -> dict[str, StoredMarketType]:
    """The newest label of each coin (or of ``symbols``) at or before ``as_of`` (any time when omitted), by symbol.

    The connection needs ``sqlite3.Row`` rows. Coins without a label are absent; a database before 0050 gives ``{}``.
    """
    if not _has_table(connection):
        return {}
    clauses, params = [], []
    if as_of is not None:
        clauses.append("as_of <= ?")
        params.append(utc_iso(_aware("as_of", as_of)))
    wanted = None if symbols is None else sorted(set(symbols))
    if wanted is not None:
        if not wanted:
            return {}
        clauses.append(f"symbol IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY as_of DESC, sequence DESC)
            AS newest FROM {TABLE} {where}) WHERE newest = 1 ORDER BY symbol""", tuple(params)).fetchall()
    return {row["symbol"]: _read(row) for row in rows}


class MarketTypeStore:
    def __init__(self, database: Database, clock: Clock, *, new_id: Callable[[], str] | None = None) -> None:
        self.database = database
        self.clock = clock
        self._new_id = new_id or (lambda: f"mt-{uuid.uuid4().hex}")

    def record(self, labels: Sequence[CoinLabel], *, scan_id: str | None = None) -> list[StoredMarketType]:
        """Store one round of labels together (all or none), each with its document and hash."""
        if any(not isinstance(item, CoinLabel) for item in labels):
            raise ValidationFailure("only CoinLabel records are stored")
        recorded = utc_iso(self.clock.now())
        ids = []
        with self.database.immediate() as connection:
            for item in labels:
                document = item.to_document()
                text = canonical_json(document)
                label = item.label
                label_id = self._new_id()
                connection.execute(
                    f"""INSERT INTO {TABLE} (label_id, symbol, as_of, market_type, reason, candle_close, lag_bars,
                       interval_minutes, source, rules_ref, rules_sha256, scan_id, evidence, document_json,
                       document_sha256, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (label_id, item.symbol, utc_iso(label.moment), label.market_type, label.reason,
                     None if label.candle_close is None else utc_iso(label.candle_close), label.lag_bars,
                     label.rules.interval_minutes, item.candles["source"], label.rules.ref,
                     label.rules.content_sha256, scan_id, item.evidence, text, _sha256(text), recorded))
                ids.append(label_id)
            rows = connection.execute(
                f"SELECT * FROM {TABLE} WHERE label_id IN ({', '.join('?' for _ in ids)}) ORDER BY sequence",
                tuple(ids)).fetchall() if ids else []
        return [_read(row) for row in rows]

    def latest(self, *, symbols: Iterable[str] | None = None,
               as_of: datetime | None = None) -> dict[str, StoredMarketType]:
        with self.database.connect() as connection:
            return read_latest_market_types(connection, symbols=symbols, as_of=as_of)

    def history(self, symbol: str, *, since: datetime | None = None,
                until: datetime | None = None) -> list[StoredMarketType]:
        """Every label of one coin in time order (for Learning and the scorecard: the type at any past moment)."""
        clauses, params = ["symbol = ?"], [symbol]
        if since is not None:
            clauses.append("as_of >= ?")
            params.append(utc_iso(_aware("since", since)))
        if until is not None:
            clauses.append("as_of <= ?")
            params.append(utc_iso(_aware("until", until)))
        with self.database.connect() as connection:
            if not _has_table(connection):
                return []
            rows = connection.execute(f"SELECT * FROM {TABLE} WHERE {' AND '.join(clauses)} "
                                      "ORDER BY as_of, sequence", tuple(params)).fetchall()
            return [_read(row) for row in rows]


__all__ = ["DEFAULT_LABEL_AGE", "MarketTypeStore", "StoredMarketType", "read_latest_market_types"]
