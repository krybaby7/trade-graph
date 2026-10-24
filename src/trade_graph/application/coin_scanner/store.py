"""The coin scanner's record in the state database (migration 0031, D174).

``CoinScanStore.record`` writes one scan in one transaction: the scan row with its document, a place for every pair
that passed every check, and the shortlist entries with every input value and source time. Reads verify the
document's hash, so an edited row is refused rather than trusted. ``latest`` returns live scans only unless a
replay is asked for by name, so Research is never handed an optimistic replay as the current shortlist.

``coin_rule`` answers D138 item 3 for the money limits (W3-A, T32): did this pair pass the scanner's liquidity and
exclusion checks at that moment? It reads the newest live scan at or before the moment. A scan older than
``max_age`` or a failed newest scan gives "unknown" (``passed`` None), never a silent yes or no.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.coin_scanner.scanner import ScanResult
from trade_graph.contracts.coin_scanner import SCAN_MODES
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import NotFound, StaleState, ValidationFailure

DEFAULT_COIN_RULE_AGE = timedelta(hours=2)
CoinRuleStatus = Literal["passed_checks", "excluded", "not_in_catalog", "no_recent_scan", "scan_failed"]
_SELECT = "SELECT * FROM coin_scans"


def canonical_json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredScan:
    scan_id: str
    sequence: int
    mode: str
    as_of: datetime
    recorded_at: datetime
    ranking_version: str
    status: str
    error: str | None
    evidence: str
    optimistic: bool
    document: dict[str, Any]
    entries: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class CoinRuleAnswer:
    symbol: str
    status: CoinRuleStatus
    passed: bool | None          # None: unknown at that moment (no recent live scan, or the newest one failed)
    scan_id: str | None = None
    scan_as_of: datetime | None = None
    age_seconds: int | None = None
    position: int | None = None
    reasons: tuple[str, ...] = ()
    error: str | None = None
    ranking_version: str | None = None


def _read(connection: sqlite3.Connection, row: sqlite3.Row) -> StoredScan:
    text = row["document_json"]
    if _sha256(text) != row["document_sha256"]:
        raise StaleState(f"coin scan {row['scan_id']} no longer matches its stored hash")
    document = json.loads(text)
    places = connection.execute(
        "SELECT position, symbol, score, ranking_json FROM coin_scan_pairs WHERE scan_sequence = ? ORDER BY position",
        (row["sequence"],)).fetchall()
    document["ranking"] = [{"position": place["position"], "symbol": place["symbol"], "score": place["score"],
                            **json.loads(place["ranking_json"])} for place in places]
    entries = [json.loads(item["inputs_json"]) for item in connection.execute(
        "SELECT inputs_json FROM coin_shortlist_entries WHERE scan_sequence = ? ORDER BY position",
        (row["sequence"],)).fetchall()]
    return StoredScan(
        scan_id=row["scan_id"], sequence=row["sequence"], mode=row["mode"], as_of=parse_utc(row["as_of"]),
        recorded_at=parse_utc(row["recorded_at"]), ranking_version=row["ranking_version"], status=row["status"],
        error=row["error"], evidence=row["evidence"], optimistic=bool(row["optimistic"]), document=document,
        entries=entries)


def _latest_row(connection: sqlite3.Connection, mode: str, as_of: datetime | None) -> sqlite3.Row | None:
    if mode not in SCAN_MODES:
        raise ValidationFailure("a scan is live or a replay")
    if as_of is None:
        return connection.execute(f"{_SELECT} WHERE mode = ? ORDER BY as_of DESC, sequence DESC LIMIT 1",
                                  (mode,)).fetchone()
    return connection.execute(
        f"{_SELECT} WHERE mode = ? AND as_of <= ? ORDER BY as_of DESC, sequence DESC LIMIT 1",
        (mode, utc_iso(as_of))).fetchone()


def read_latest_scan(connection: sqlite3.Connection, *, mode: str = "live",
                     as_of: datetime | None = None) -> StoredScan | None:
    """The newest scan of a mode on any connection, including a read-only one (``sqlite3.Row`` rows)."""
    row = _latest_row(connection, mode, as_of)
    return None if row is None else _read(connection, row)


class CoinScanStore:
    def __init__(self, database: Database, clock: Clock, *, new_id: Callable[[], str] | None = None) -> None:
        self.database = database
        self.clock = clock
        self._new_id = new_id or (lambda: str(uuid.uuid4()))

    def record(self, result: ScanResult) -> StoredScan:
        if not isinstance(result, ScanResult):
            raise ValidationFailure("a ScanResult is required")
        document = result.document()
        ranking = document.pop("ranking")
        text = canonical_json(document)
        entries = result.entries()
        shortlisted = {item["symbol"] for item in entries}
        scan_id = self._new_id()
        recorded_at = utc_iso(self.clock.now())
        with self.database.immediate() as connection:
            cursor = connection.execute(
                """INSERT INTO coin_scans (scan_id, mode, as_of, computed_at, recorded_at, ranking_version, status,
                   error, evidence, optimistic, usd_pairs, ranked_pairs, shortlisted, document_json, document_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (scan_id, result.mode, utc_iso(result.as_of), utc_iso(result.computed_at), recorded_at,
                 result.ranking_version, result.status, result.error, result.evidence, int(result.optimistic),
                 len(result.evaluations), len(result.ranked), len(result.shortlist), text, _sha256(text)))
            sequence = cursor.lastrowid
            for row in ranking:
                rest = {key: value for key, value in row.items() if key not in ("position", "symbol", "score")}
                connection.execute(
                    "INSERT INTO coin_scan_pairs (scan_sequence, position, symbol, score, shortlisted, ranking_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sequence, row["position"], row["symbol"], row["score"], int(row["symbol"] in shortlisted),
                     canonical_json(rest)))
            for entry in entries:
                connection.execute(
                    "INSERT INTO coin_shortlist_entries (scan_sequence, position, symbol, inputs_json) "
                    "VALUES (?, ?, ?, ?)", (sequence, entry["position"], entry["symbol"], canonical_json(entry)))
            return _read(connection, connection.execute(f"{_SELECT} WHERE sequence = ?", (sequence,)).fetchone())

    def get(self, scan_id: str) -> StoredScan:
        with self.database.connect() as connection:
            row = connection.execute(f"{_SELECT} WHERE scan_id = ?", (scan_id,)).fetchone()
            if row is None:
                raise NotFound(f"no coin scan {scan_id}")
            return _read(connection, row)

    def latest(self, *, mode: str = "live", as_of: datetime | None = None) -> StoredScan | None:
        """The newest scan of ``mode`` (live by default) at or before ``as_of`` (any time when omitted)."""
        with self.database.connect() as connection:
            return read_latest_scan(connection, mode=mode, as_of=as_of)

    def coin_rule(self, symbol: str, *, as_of: datetime, max_age: timedelta = DEFAULT_COIN_RULE_AGE,
                  ) -> CoinRuleAnswer:
        """Did ``symbol`` pass every liquidity and exclusion check in the newest live scan at ``as_of``?"""
        if not isinstance(symbol, str) or not symbol:
            raise ValidationFailure("a symbol is required")
        with self.database.connect() as connection:
            row = _latest_row(connection, "live", as_of)
            if row is None or as_of - parse_utc(row["as_of"]) > max_age:
                return CoinRuleAnswer(symbol, "no_recent_scan", None)
            scan = _read(connection, row)
        age = int((as_of - scan.as_of).total_seconds())
        common = {"scan_id": scan.scan_id, "scan_as_of": scan.as_of, "age_seconds": age,
                  "ranking_version": scan.ranking_version}
        if scan.status == "failed":
            return CoinRuleAnswer(symbol, "scan_failed", None, error=scan.error, **common)
        place = next((row for row in scan.document["ranking"] if row["symbol"] == symbol), None)
        if place is not None:
            return CoinRuleAnswer(symbol, "passed_checks", True, position=place["position"], **common)
        reasons = tuple(reason for reason, symbols in scan.document["excluded"].items() if symbol in symbols)
        if reasons:
            return CoinRuleAnswer(symbol, "excluded", False, reasons=reasons, **common)
        return CoinRuleAnswer(symbol, "not_in_catalog", False, **common)


__all__ = ["DEFAULT_COIN_RULE_AGE", "CoinRuleAnswer", "CoinScanStore", "StoredScan", "read_latest_scan"]
