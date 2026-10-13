"""Append-only candle revisions with receipt-time availability (contract: docs/CANDLE-STORE.md)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.contracts.candles import (
    CANDLE_INTERVALS,
    CANDLE_SOURCES,
    EVIDENCE_LABELS,
    RUN_KINDS,
    Candle,
    CandleSlot,
    CandleWindow,
    CatalogObservation,
    CoverageReport,
    MissingRange,
    ReadMode,
    RunRecord,
    SaveResult,
    SeriesSummary,
    StoredCandle,
    expected_close_times,
    from_micros,
    from_unix,
    to_micros,
    to_unix,
)
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal

MAX_SAVE_CANDLES = 50_000
MAX_WINDOW_SLOTS = 100_000
_COLUMNS = "open_time, revision, available_at, run_id, open, high, low, close, vwap, volume, trade_count"


def _series_key(symbol: str, source: str, interval_minutes: int) -> None:
    if not isinstance(symbol, str) or not symbol:
        raise ValidationFailure("a symbol is required")
    if source not in CANDLE_SOURCES:
        raise ValidationFailure("unsupported candle source")
    if interval_minutes not in CANDLE_INTERVALS:
        raise ValidationFailure("unsupported candle interval")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


class CandleStore:
    """Writers add revisions; readers choose a mode. Nothing here updates or deletes a row."""

    def __init__(self, database: MarketDataDatabase) -> None:
        self.database = database

    # --- runs -------------------------------------------------------------------------------------------------

    def begin_run(self, *, kind: str, source: str, evidence_label: str, started_at: datetime,
                  parameters: dict[str, Any]) -> int:
        if kind not in RUN_KINDS or source not in CANDLE_SOURCES or evidence_label not in EVIDENCE_LABELS:
            raise ValidationFailure("unsupported run kind, source or evidence label")
        document = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        with self.database.immediate() as connection:
            cursor = connection.execute(
                "INSERT INTO candle_runs (kind, source, evidence_label, started_at, parameters_json) "
                "VALUES (?, ?, ?, ?, ?)", (kind, source, evidence_label, to_micros(started_at), document))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, finished_at: datetime, status: str, report: dict[str, Any]) -> None:
        document = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        try:
            with self.database.immediate() as connection:
                connection.execute(
                    "INSERT INTO candle_run_results (run_id, finished_at, status, report_json) VALUES (?, ?, ?, ?)",
                    (run_id, to_micros(finished_at), status, document))
        except sqlite3.IntegrityError as error:
            raise ValidationFailure("the run is unknown or already has a result") from error

    def runs(self) -> list[RunRecord]:
        rows = self.database.query(
            "SELECT r.run_id, r.kind, r.source, r.evidence_label, r.started_at, r.parameters_json, "
            "x.finished_at, x.status, x.report_json FROM candle_runs r "
            "LEFT JOIN candle_run_results x ON x.run_id = r.run_id ORDER BY r.run_id")
        return [RunRecord(
            run_id=row["run_id"], kind=row["kind"], source=row["source"], evidence_label=row["evidence_label"],
            started_at_utc=from_micros(row["started_at"]), parameters=json.loads(row["parameters_json"]),
            finished_at_utc=None if row["finished_at"] is None else from_micros(row["finished_at"]),
            status="interrupted" if row["status"] is None else row["status"],
            report=None if row["report_json"] is None else json.loads(row["report_json"]),
        ) for row in rows]

    # --- candles ----------------------------------------------------------------------------------------------

    def save(self, candles: Sequence[Candle], *, run_id: int) -> SaveResult:
        """Add revisions in one transaction. Identical content is a duplicate; backdated receipts are refused."""
        if len(candles) > MAX_SAVE_CANDLES:
            raise ValidationFailure(f"at most {MAX_SAVE_CANDLES} candles may be saved per call")
        # model_copy(update=...) is not validation: revalidate everything before writing.
        validated = [Candle.model_validate(candle) for candle in candles]
        inserted = revised = duplicates = 0
        grouped: dict[tuple[str, str, int], list[Candle]] = {}
        for candle in validated:
            grouped.setdefault((candle.symbol, candle.source, candle.interval_minutes), []).append(candle)
        for group in grouped.values():
            if any(candle.source_pair != group[0].source_pair for candle in group):
                raise ValidationFailure("one series cannot mix venue pair codes")
        try:
            with self.database.immediate() as connection:
                for (symbol, source, interval), group in grouped.items():
                    series_id = self._series_id(connection, symbol, source, interval, group[0].source_pair, create=True)
                    group.sort(key=lambda candle: (candle.open_unix, candle.available_at_utc))
                    latest = self._latest_revisions(connection, series_id, group[0].open_unix, group[-1].open_unix)
                    for candle in group:
                        known = latest.get(candle.open_unix)
                        digest = bytes.fromhex(candle.content_hash)
                        received = to_micros(candle.available_at_utc)
                        if known is not None and known[2] == digest:
                            duplicates += 1
                            continue
                        if known is not None and received < known[1]:
                            raise ValidationFailure("a candle receipt cannot be earlier than its stored revision")
                        revision = 1 if known is None else known[0] + 1
                        connection.execute(
                            "INSERT INTO market_candles (series_id, open_time, revision, available_at, run_id, "
                            "open, high, low, close, vwap, volume, trade_count, content_hash) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (series_id, candle.open_unix, revision, received, run_id, _decimal_text(candle.open),
                             _decimal_text(candle.high), _decimal_text(candle.low), _decimal_text(candle.close),
                             _decimal_text(candle.vwap), _decimal_text(candle.volume), candle.trade_count, digest))
                        latest[candle.open_unix] = (revision, received, digest)
                        if known is None:
                            inserted += 1
                        else:
                            revised += 1
        except sqlite3.IntegrityError as error:
            raise ValidationFailure("candle write refused: unknown run or conflicting identity") from error
        return SaveResult(inserted=inserted, revised=revised, duplicates=duplicates)

    def window(self, symbol: str, interval_minutes: int, source: str, *, end_close_time: datetime, count: int,
               as_of: datetime | None = None, mode: ReadMode = "point_in_time") -> CandleWindow:
        """``count`` slots ending at ``end_close_time``, oldest first; missing slots stay missing."""
        _series_key(symbol, source, interval_minutes)
        if mode not in ("point_in_time", "replay"):
            raise ValidationFailure("unsupported read mode")
        if mode == "point_in_time" and as_of is None:
            raise ValidationFailure("point-in-time reads require as_of")
        if count > MAX_WINDOW_SLOTS:
            raise ValidationFailure(f"at most {MAX_WINDOW_SLOTS} slots may be read at once")
        try:
            closes = expected_close_times(end_close_time, count, interval_minutes)
        except ValueError as error:
            raise ValidationFailure(str(error)) from error
        reference = as_of if as_of is not None else end_close_time
        if closes[-1] > reference:
            raise ValidationFailure("a candle that closes after as_of is not complete")
        step = timedelta(minutes=interval_minutes)
        by_open = self._visible(symbol, interval_minutes, source, to_unix(closes[0] - step),
                                to_unix(closes[-1] - step), reference, mode)
        slots = tuple(CandleSlot(close_time_utc=close, stored=by_open.get(to_unix(close - step))) for close in closes)
        return CandleWindow(symbol=symbol, interval_minutes=interval_minutes, source=source, as_of_utc=reference,
                            mode=mode, slots=slots)

    def coverage(self, symbol: str, interval_minutes: int, source: str, *, start_open: datetime,
                 end_open: datetime, as_of: datetime | None = None, mode: ReadMode = "point_in_time",
                 ) -> CoverageReport:
        """Present and missing slots for open times in [start_open, end_open)."""
        _series_key(symbol, source, interval_minutes)
        if mode == "point_in_time" and as_of is None:
            raise ValidationFailure("point-in-time coverage requires as_of")
        step = interval_minutes * 60
        first, stop = to_unix(start_open), to_unix(end_open)
        if first % step or stop % step or stop <= first:
            raise ValidationFailure("coverage bounds must sit on the interval grid and span at least one slot")
        expected = (stop - first) // step
        if expected > 5_000_000:
            raise ValidationFailure("coverage window is too large")
        present = self._present_opens(symbol, interval_minutes, source, first, stop - step, as_of, mode)
        missing: list[MissingRange] = []
        longest = run = 0
        gap_start: int | None = None
        slot = first
        pointer = 0

        def close_gap(last_missing: int) -> None:
            missing.append(MissingRange(from_unix(gap_start), from_unix(last_missing),
                                        (last_missing - gap_start) // step + 1))

        while slot < stop:
            if pointer < len(present) and present[pointer] == slot:
                pointer += 1
                run += 1
                longest = max(longest, run)
                if gap_start is not None:
                    close_gap(slot - step)
                    gap_start = None
            else:
                run = 0
                gap_start = slot if gap_start is None else gap_start
            slot += step
        if gap_start is not None:
            close_gap(stop - step)
        return CoverageReport(
            symbol=symbol, interval_minutes=interval_minutes, source=source, start_open_utc=start_open,
            end_open_utc=end_open, expected=expected, present=len(present),
            first_open_utc=from_unix(present[0]) if present else None,
            last_close_utc=from_unix(present[-1] + step) if present else None,
            longest_run=longest, missing_ranges=tuple(missing), mode=mode)

    def revisions(self, symbol: str, interval_minutes: int, source: str, open_time: datetime) -> list[StoredCandle]:
        _series_key(symbol, source, interval_minutes)
        row = self.database.query(
            "SELECT series_id, source_pair FROM candle_series WHERE symbol=? AND source=? AND interval_minutes=?",
            (symbol, source, interval_minutes))
        if not row:
            return []
        rows = self.database.query(
            f"SELECT {_COLUMNS} FROM market_candles WHERE series_id=? AND open_time=? ORDER BY revision",
            (row[0]["series_id"], to_unix(open_time)))
        return [self._hydrate(r, symbol, row[0]["source_pair"], interval_minutes, source) for r in rows]

    def series(self) -> list[SeriesSummary]:
        rows = self.database.query(
            "SELECT s.symbol, s.source, s.interval_minutes, s.source_pair, "
            "(SELECT COUNT(*) FROM (SELECT 1 FROM market_candles m WHERE m.series_id = s.series_id "
            " GROUP BY m.open_time)) AS candle_count, "
            "(SELECT MIN(open_time) FROM market_candles m WHERE m.series_id = s.series_id) AS first_open, "
            "(SELECT MAX(open_time) FROM market_candles m WHERE m.series_id = s.series_id) AS last_open "
            "FROM candle_series s ORDER BY s.symbol, s.source, s.interval_minutes")
        return [SeriesSummary(
            symbol=r["symbol"], source=r["source"], interval_minutes=r["interval_minutes"],
            source_pair=r["source_pair"], candle_count=r["candle_count"],
            first_open_utc=None if r["first_open"] is None else from_unix(r["first_open"]),
            last_open_utc=None if r["last_open"] is None else from_unix(r["last_open"])) for r in rows]

    # --- pair catalog -----------------------------------------------------------------------------------------

    def save_catalog(self, pairs: list[dict[str, Any]], *, observed_at: datetime, run_id: int,
                     rules_version: str) -> bool:
        """Keep a dated pair list. An unchanged list adds no row. Returns whether a row was added."""
        document = json.dumps(pairs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(f"{rules_version}\n{document}".encode("ascii")).digest()
        try:
            with self.database.immediate() as connection:
                latest = connection.execute(
                    "SELECT content_hash FROM pair_catalog_observations ORDER BY observed_at DESC, observation_id DESC "
                    "LIMIT 1").fetchone()
                if latest is not None and bytes(latest["content_hash"]) == digest:
                    return False
                connection.execute(
                    "INSERT INTO pair_catalog_observations (observed_at, run_id, rules_version, content_hash, "
                    "document_json) VALUES (?, ?, ?, ?, ?)",
                    (to_micros(observed_at), run_id, rules_version, digest, document))
                return True
        except sqlite3.IntegrityError as error:
            raise ValidationFailure("catalog write refused: unknown run") from error

    def latest_catalog(self, *, as_of: datetime) -> CatalogObservation | None:
        rows = self.database.query(
            "SELECT observation_id, observed_at, run_id, rules_version, document_json FROM pair_catalog_observations "
            "WHERE observed_at <= ? ORDER BY observed_at DESC, observation_id DESC LIMIT 1", (to_micros(as_of),))
        if not rows:
            return None
        row = rows[0]
        return CatalogObservation(
            observation_id=row["observation_id"], observed_at_utc=from_micros(row["observed_at"]),
            run_id=row["run_id"], rules_version=row["rules_version"], pairs=json.loads(row["document_json"]))

    # --- internals --------------------------------------------------------------------------------------------

    def _series_id(self, connection: sqlite3.Connection, symbol: str, source: str, interval: int,
                   source_pair: str, *, create: bool) -> int:
        row = connection.execute(
            "SELECT series_id, source_pair FROM candle_series WHERE symbol=? AND source=? AND interval_minutes=?",
            (symbol, source, interval)).fetchone()
        if row is not None:
            if row["source_pair"] != source_pair:
                raise ValidationFailure("the venue pair code of a stored series cannot change")
            return int(row["series_id"])
        if not create:
            raise ValidationFailure("unknown series")
        cursor = connection.execute(
            "INSERT INTO candle_series (symbol, source, interval_minutes, source_pair) VALUES (?, ?, ?, ?)",
            (symbol, source, interval, source_pair))
        return int(cursor.lastrowid)

    @staticmethod
    def _latest_revisions(connection: sqlite3.Connection, series_id: int, first: int, last: int,
                          ) -> dict[int, tuple[int, int, bytes]]:
        rows = connection.execute(
            "SELECT m.open_time, m.revision, m.available_at, m.content_hash FROM market_candles m "
            "WHERE m.series_id=? AND m.open_time BETWEEN ? AND ? AND m.revision = "
            "(SELECT MAX(revision) FROM market_candles WHERE series_id=m.series_id AND open_time=m.open_time)",
            (series_id, first, last)).fetchall()
        return {row["open_time"]: (row["revision"], row["available_at"], bytes(row["content_hash"])) for row in rows}

    def _visible(self, symbol: str, interval: int, source: str, first_open: int, last_open: int,
                 reference: datetime, mode: ReadMode) -> dict[int, StoredCandle]:
        series = self.database.query(
            "SELECT series_id, source_pair FROM candle_series WHERE symbol=? AND source=? AND interval_minutes=?",
            (symbol, source, interval))
        if not series:
            return {}
        # Replay ignores receipt time; the newest revision stands in for what is known today.
        ignore_receipt = 1 if mode == "replay" else 0
        rows = self.database.query(
            f"SELECT {_COLUMNS} FROM market_candles m WHERE m.series_id=? AND m.open_time BETWEEN ? AND ? "
            "AND (? OR m.available_at <= ?) AND m.revision = (SELECT MAX(revision) FROM market_candles "
            "WHERE series_id=m.series_id AND open_time=m.open_time AND (? OR available_at <= ?)) "
            "ORDER BY m.open_time",
            (series[0]["series_id"], first_open, last_open, ignore_receipt, to_micros(reference), ignore_receipt,
             to_micros(reference)))
        return {row["open_time"]: self._hydrate(row, symbol, series[0]["source_pair"], interval, source)
                for row in rows}

    def _present_opens(self, symbol: str, interval: int, source: str, first_open: int, last_open: int,
                       as_of: datetime | None, mode: ReadMode) -> list[int]:
        series = self.database.query(
            "SELECT series_id FROM candle_series WHERE symbol=? AND source=? AND interval_minutes=?",
            (symbol, source, interval))
        if not series:
            return []
        ignore_receipt = 1 if mode == "replay" else 0
        bound = 0 if as_of is None else to_micros(as_of)
        rows = self.database.query(
            "SELECT DISTINCT open_time FROM market_candles WHERE series_id=? AND open_time BETWEEN ? AND ? "
            "AND (? OR available_at <= ?) ORDER BY open_time",
            (series[0]["series_id"], first_open, last_open, ignore_receipt, bound))
        return [row["open_time"] for row in rows]

    @staticmethod
    def _hydrate(row: sqlite3.Row, symbol: str, source_pair: str, interval: int, source: str) -> StoredCandle:
        opened = from_unix(row["open_time"])
        candle = Candle(
            symbol=symbol, source_pair=source_pair, interval_minutes=interval, open_time_utc=opened,
            close_time_utc=opened + timedelta(minutes=interval), available_at_utc=from_micros(row["available_at"]),
            open=Decimal(row["open"]), high=Decimal(row["high"]), low=Decimal(row["low"]),
            close=Decimal(row["close"]), vwap=None if row["vwap"] is None else Decimal(row["vwap"]),
            volume=Decimal(row["volume"]), trade_count=row["trade_count"], source=source)
        return StoredCandle(candle=candle, revision=row["revision"], run_id=row["run_id"])
