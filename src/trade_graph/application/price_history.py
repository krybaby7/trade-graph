"""Bounded, receipt-aware hourly features from immutable completed candles.

Every feature value is computed by the indicator library (D118: no indicator formula is written in this repository).
This module selects the exact window of completed hourly candles each feature needs, hands the Decimal columns to
``application/indicators/library.py`` and stores the result as exact decimal text. Which feature maps to which
menu entry is the table below; the version history is in docs/KRAKEN-PRICE-HISTORY.md and docs/INDICATOR-MENU.md.

Two stores hand over the hours (D220): ``CandleStoreHistory`` reads the candle store's 60-minute series (an
installation with the candle file: any coin, point in time), ``HistoryStore`` the financial database's
``hourly_candles`` table (BTC/USD and ETH/USD; older installations, retained snapshots and their tests). The numbers,
statuses and windows are computed by the same code from either.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any, Protocol

from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.indicators.library import SNAPSHOT_DIGITS, compute_raw, lookback, rounded_decimal
from trade_graph.contracts.candles import Candle, from_unix, to_micros, to_unix
from trade_graph.contracts.indicators import get_indicator
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal

FEATURE_VERSION = "hourly-price-features-v3"
# Snapshots retained under v2 are read as they were stored and never recomputed; the constant keeps them nameable.
FEATURE_VERSION_V2 = "hourly-price-features-v2"
MAX_INGEST_CANDLES = 720
MAX_LOOKBACK_CANDLES = 50
# Windows end at the newest available close if it is at most this far behind the expected close.
MAX_ANCHOR_LAG_HOURS = 2
# Expected hourly slots, ending at the expected close, whose available candles the Trader sees directly.
RECENT_CANDLES = 24
# A feature whose value is an input price (or half the sum of two) is stored exactly, which the 12-digit stored text can
# only do for inputs of at most this many significant digits; with more it is withheld, never silently rounded.
EXACT_INPUT_DIGITS = SNAPSHOT_DIGITS - 1
_RECENT_FIELDS = frozenset({
    "open_time_utc", "close_time_utc", "available_at_utc", "open", "high", "low", "close", "volume"})


@dataclass(frozen=True)
class _Feature:
    indicator: str  # a name on the indicator menu (contracts/indicators.py)
    parameters: Mapping[str, int | float]
    lookback: int  # completed hourly candles in the window: the library's own lookback plus the candle it ends on
    definition: str
    selects: bool = False  # the value is an input price (or half the sum of two): it must be stored exactly


def _feature(indicator: str, parameters: Mapping[str, int | float], definition: str, *,
             selects: bool = False) -> _Feature:
    return _Feature(indicator, parameters, lookback(indicator, parameters) + 1, definition, selects)


_FEATURES = {
    "sma_20": _feature("SMA", {"timeperiod": 20},
                       "Simple moving average (TA-Lib SMA) of the last 20 completed hourly closes, in USD."),
    "sma_50": _feature("SMA", {"timeperiod": 50},
                       "Simple moving average (TA-Lib SMA) of the last 50 completed hourly closes, in USD."),
    "highest_high_20": _feature(
        "HIGHEST_HIGH", {"timeperiod": 20},
        "Highest high (TA-Lib MAX) of the last 20 completed hourly candles, in USD. Compare it with latest_close "
        "to read how far price has pulled back.", selects=True),
    "range_high": _feature("HIGHEST_HIGH", {"timeperiod": 24},
                           "Highest high (TA-Lib MAX) of the last 24 completed hourly candles, in USD.",
                           selects=True),
    "range_low": _feature("LOWEST_LOW", {"timeperiod": 24},
                          "Lowest low (TA-Lib MIN) of the last 24 completed hourly candles, in USD.", selects=True),
    "midpoint": _feature("MIDPRICE", {"timeperiod": 24},
                         "Midpoint of the highest high and the lowest low (TA-Lib MIDPRICE) of the last 24 "
                         "completed hourly candles, in USD.", selects=True),
    "hourly_return": _feature("ROCP", {"timeperiod": 1},
                              "Change of the latest completed hourly close from the previous one as a fraction of "
                              "the previous close (TA-Lib ROCP over 1 hour); 0.01 is up 1 percent. Accurate to 5e-12 "
                              "of its size plus 2.5e-16, so a very small return has fewer than 12 trustworthy digits."),
    "four_hour_drift": _feature("ROCP", {"timeperiod": 4},
                                "Change of the latest completed hourly close from the close four hours earlier as "
                                "a fraction of that close (TA-Lib ROCP over 4 hours); 0.01 is up 1 percent. Accurate "
                                "to 5e-12 of its size plus 2.5e-16, so a very small return has fewer than 12 "
                                "trustworthy digits."),
}
# Ratios of library outputs that v2 computed by hand. They are not part of v3. Asking for one is answered with
# status unsupported, a pointer to the library numbers it was made of, and those numbers themselves (supplied as if
# they had been asked for, because an installation that kept a v2 template never asks for them by name), never with
# a value for the ratio and never with a hidden recomputation.
_RETIRED = {
    "pullback_from_high": ("highest_high_20",),
    "pullback_depth": ("highest_high_20",),
    "midpoint_distance": ("midpoint",),
}


def _retired_definition(name: str) -> str:
    return (f"Retired in {FEATURE_VERSION}: a ratio of two library numbers was hand-written arithmetic, which the "
            f"repository no longer does (D118). Read {' and '.join(_RETIRED[name])} with latest_close instead; "
            "it is supplied with this snapshot.")


def _significant_digits(value: Decimal) -> int:
    return len("".join(map(str, value.as_tuple().digits)).rstrip("0")) or 1


def _value(name: str, candles: list[HourlyCandle]) -> str | None:
    """The library's newest value for the window, as exact decimal text (rounding rule: library.rounded_decimal).

    None when the feature is an input price (or half the sum of two) and an input has more significant digits than
    the stored text can carry exactly: that value is withheld rather than rounded.
    """
    feature = _FEATURES[name]
    columns = {field: tuple(getattr(candle, field) for candle in candles)
               for field in get_indicator(feature.indicator).requires}
    if feature.selects and any(_significant_digits(item) > EXACT_INPUT_DIGITS
                               for values in columns.values() for item in values):
        return None
    series = compute_raw(feature.indicator, feature.parameters, columns)
    (output,) = series.values()
    return canonical_decimal(rounded_decimal(float(output[-1])))


def _provenance(candle: HourlyCandle, revision_id: str) -> dict[str, Any]:
    return {
        "candle_id": revision_id,
        "content_id": candle.candle_id,
        "source": candle.source,
        "source_ref": candle.source_ref,
        "source_hash": candle.source_hash,
        "open_time_utc": utc_iso(candle.open_time_utc),
        "close_time_utc": utc_iso(candle.close_time_utc),
        "available_at_utc": utc_iso(candle.available_at_utc),
    }


@dataclass(frozen=True)
class _Window:
    """The hours one snapshot reads: its expected close, the longest lookback and the first close searched."""

    as_of: datetime
    expected_close: datetime
    names: list[str]
    lookback: int
    recent_expected: list[datetime]
    search_start: datetime


def _window(as_of: datetime, requested_features: list[str]) -> _Window:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if (len(requested_features) > 32
            or any(not isinstance(name, str) or len(name) > 64 for name in requested_features)):
        raise ValueError("at most 32 feature names of at most 64 characters are allowed")
    as_of = as_of.astimezone(UTC)
    expected_close = as_of.replace(minute=0, second=0, microsecond=0)
    names = list(dict.fromkeys(requested_features))
    # A retired ratio brings the library numbers it was made of (see _RETIRED).
    names = list(dict.fromkeys([*names, *(item for name in names for item in _RETIRED.get(name, ()))]))
    lookback = max((_FEATURES[name].lookback for name in names if name in _FEATURES), default=1)
    recent_expected = [expected_close - timedelta(hours=offset) for offset in reversed(range(RECENT_CANDLES))]
    search_start = min(expected_close - timedelta(hours=lookback - 1 + MAX_ANCHOR_LAG_HOURS), recent_expected[0])
    return _Window(as_of, expected_close, names, lookback, recent_expected, search_start)


def _snapshot(symbol: str, source: str, window: _Window, found: list[tuple[str, Any]],
              provenance: Callable[[Any, str], dict[str, Any]], extra: Mapping[str, Any] | None = None,
              ) -> dict[str, Any]:
    """The v3 snapshot from the newest revision available at as_of of each stored hour, oldest first.

    ``found`` pairs a revision id with a candle that has the hourly fields (``open``, ``high``, ``low``, ``close``,
    ``volume``, its times and ``model_dump``); both stores hand over their own candles this way.
    """
    as_of, expected_close, names, recent_expected = (window.as_of, window.expected_close, window.names,
                                                     window.recent_expected)
    newest = found[-1][1].close_time_utc if found else None
    # A pending newest hour must not hide complete windows ending at the newest available close (D114);
    # beyond the lag bound, windows end at the expected close and withhold values as in v1.
    anchor = (newest if newest is not None and expected_close - newest <= timedelta(hours=MAX_ANCHOR_LAG_HOURS)
              else expected_close)
    start = anchor - timedelta(hours=window.lookback - 1)
    # The retained manifest commits to every candle whose values appear: feature windows and recent series.
    kept = [(revision_id, candle) for revision_id, candle in found
            if candle.close_time_utc >= min(start, recent_expected[0])]
    candles = [candle for _, candle in kept]
    revision_ids = {candle.close_time_utc: revision_id for revision_id, candle in kept}
    by_close = {candle.close_time_utc: candle for candle in candles}
    earliest = min((at for at in by_close if at >= start), default=None)
    recent = [by_close[at] for at in recent_expected if at in by_close]
    pending = [anchor + timedelta(hours=step)
               for step in range(1, (expected_close - anchor) // timedelta(hours=1) + 1)]
    values: dict[str, str] = {}
    features: dict[str, Any] = {}
    for name in names:
        spec = _FEATURES.get(name)
        if spec is None:
            features[name] = {
                "status": "unsupported",
                "definition": (_retired_definition(name) if name in _RETIRED
                               else "No supported hourly definition for this feature."),
                "lookback_candles": None, "available_candles": 0,
                "missing_close_times_utc": [], "candle_ids": [],
                "window_start_close_time_utc": None, "window_end_close_time_utc": None,
                "event_time_utc": None, "available_at_utc": None,
            }
            continue
        expected = [anchor - timedelta(hours=offset) for offset in reversed(range(spec.lookback))]
        selected = [by_close[at] for at in expected if at in by_close]
        gaps = [at for at in expected if at not in by_close]
        # "stale": the exact window is complete through the anchor while newer hours are still pending.
        status = "stale" if pending else "ready"
        if gaps:
            status = ("gapped_history" if earliest is not None and any(at >= earliest for at in gaps)
                      else "insufficient_history")
        definition = spec.definition
        if status in {"ready", "stale"}:
            text = _value(name, selected)
            if text is None:
                status = "unsupported"
                definition = (f"{spec.definition} Withheld: an input price in this window has more than "
                              f"{EXACT_INPUT_DIGITS} significant digits, which the {SNAPSHOT_DIGITS}-digit stored "
                              "text cannot carry exactly.")
            else:
                values[name] = text
        features[name] = {
            "status": status, "definition": definition, "lookback_candles": spec.lookback,
            "available_candles": len(selected), "missing_close_times_utc": [utc_iso(at) for at in gaps + pending],
            "candle_ids": [revision_ids[candle.close_time_utc] for candle in selected],
            "window_start_close_time_utc": utc_iso(expected[0]),
            "window_end_close_time_utc": utc_iso(expected[-1]),
            "event_time_utc": utc_iso(selected[-1].close_time_utc) if selected else None,
            "available_at_utc": utc_iso(max(candle.available_at_utc for candle in selected)) if selected else None,
        }
    latest = candles[-1].close_time_utc if candles else None
    result = {
        "symbol": symbol, "as_of_utc": utc_iso(as_of), "source": source, "interval_minutes": 60,
        "feature_version": FEATURE_VERSION, "latest_close_time_utc": utc_iso(latest) if latest else None,
        "latest_expected_close_time_utc": utc_iso(expected_close), "stale": latest != expected_close,
        "candle_ids": [revision_ids[candle.close_time_utc] for candle in candles],
        "provenance": [provenance(candle, revision_ids[candle.close_time_utc]) for candle in candles],
        "values": values, "features": features,
        # Completed candles available at as_of only; missing hours are listed, never filled or zeroed.
        "latest_close": {
            "close_time_utc": utc_iso(recent[-1].close_time_utc), "close": canonical_decimal(recent[-1].close),
            "available_at_utc": utc_iso(recent[-1].available_at_utc),
        } if recent else None,
        "recent_candles": [candle.model_dump(mode="json", include=_RECENT_FIELDS) for candle in recent],
        "recent_missing_close_times_utc": [utc_iso(at) for at in recent_expected if at not in by_close],
        "event_time_utc": utc_iso(latest) if latest else None,
        "available_at_utc": utc_iso(max(candle.available_at_utc for candle in candles)) if candles else None,
        **(extra or {}),
    }
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    result["source_ref"] = "hourly-features:" + hashlib.sha256(encoded).hexdigest()
    return result


class HourlyHistory(Protocol):
    """Where the hourly features read completed hours: ``hourly_candles`` (older installations) or the candle store."""

    def supports(self, symbol: str) -> bool: ...

    def snapshot(self, symbol: str, as_of: datetime, requested_features: list[str],
                 source: str = "kraken_public_ohlc") -> dict[str, Any]: ...

    def received_close(self, symbol: str, close: datetime, as_of: datetime, source: str) -> bool: ...

    def latest_close_at_or_before(self, symbol: str, close: datetime, as_of: datetime, source: str) -> str | None: ...


class HistoryStore:
    """Retain revisions and derive at most 50 explicitly expected hourly slots."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @atomic
    def save(self, candles: list[HourlyCandle]) -> dict[str, int]:
        if len(candles) > MAX_INGEST_CANDLES:
            raise ValueError("at most 720 hourly candles may be saved per batch")
        # Revalidate all input before writing; model_copy(update=...) is not validation.
        validated = [HourlyCandle.model_validate(candle) for candle in candles]
        inserted = 0
        for candle in sorted(validated, key=lambda item: item.available_at_utc):
            content_id = candle.candle_id
            latest = self.database.execute(
                """SELECT candle_id, content_id FROM hourly_candles
                   WHERE symbol=? AND source=? AND close_time_utc=? AND available_at_utc <= ?
                   ORDER BY available_at_utc DESC, sequence DESC LIMIT 1""",
                (candle.symbol, candle.source, utc_iso(candle.close_time_utc), utc_iso(candle.available_at_utc)),
            ).fetchone()
            if latest and latest["content_id"] == content_id:
                continue
            if latest is None:
                # A replay claiming an earlier receipt cannot backdate first-known content.
                if self.database.execute("SELECT 1 FROM hourly_candles WHERE content_id=?", (content_id,)).fetchone():
                    continue
                revision_id = content_id
            else:
                # A -> B -> A is a new revision, unlike repeating unchanged A.
                chain = f"{content_id}:{latest['candle_id']}".encode("ascii")
                revision_id = hashlib.sha256(chain).hexdigest()
            document = json.dumps(candle.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            result = self.database.execute(
                """INSERT INTO hourly_candles
                   (candle_id, content_id, symbol, source, interval_minutes,
                    close_time_utc, available_at_utc, document_json)
                   VALUES (?, ?, ?, ?, 60, ?, ?, ?) ON CONFLICT(candle_id) DO NOTHING""",
                (revision_id, content_id, candle.symbol, candle.source, utc_iso(candle.close_time_utc),
                 utc_iso(candle.available_at_utc), document),
            )
            inserted += result.rowcount
        return {"inserted": inserted, "duplicates": len(candles) - inserted}

    @staticmethod
    def supports(symbol: str) -> bool:
        return symbol in {"BTC/USD", "ETH/USD"}

    def snapshot(
        self, symbol: str, as_of: datetime, requested_features: list[str], source: str = "kraken_public_ohlc",
    ) -> dict[str, Any]:
        if not self.supports(symbol):
            raise ValueError("unsupported hourly history symbol")
        if source not in {"kraken_public_ohlc", "synthetic"}:
            raise ValueError("unsupported hourly history source")
        window = _window(as_of, requested_features)
        rows = self.database.execute(
            """WITH available_revisions AS (
                SELECT candle_id, document_json, close_time_utc,
                    ROW_NUMBER() OVER (
                        PARTITION BY close_time_utc ORDER BY available_at_utc DESC, sequence DESC
                    ) AS revision_rank
                FROM hourly_candles
                WHERE symbol=? AND source=? AND interval_minutes=60
                  AND close_time_utc >= ? AND close_time_utc <= ? AND available_at_utc <= ?
            ) SELECT candle_id, document_json FROM available_revisions
              WHERE revision_rank=1 ORDER BY close_time_utc""",
            (symbol, source, utc_iso(window.search_start), utc_iso(window.expected_close), utc_iso(window.as_of)),
        ).fetchall()
        found = [(row["candle_id"], HourlyCandle.model_validate_json(row["document_json"])) for row in rows]
        return _snapshot(symbol, source, window, found, _provenance)

    def received_close(self, symbol: str, close: datetime, as_of: datetime, source: str) -> bool:
        return self.database.execute(
            """SELECT 1 FROM hourly_candles WHERE symbol=? AND source=? AND interval_minutes=60
               AND close_time_utc=? AND available_at_utc<=? LIMIT 1""",
            (symbol, source, utc_iso(close), utc_iso(as_of))).fetchone() is not None

    def latest_close_at_or_before(self, symbol: str, close: datetime, as_of: datetime, source: str) -> str | None:
        row = self.database.execute(
            """SELECT close_time_utc FROM hourly_candles WHERE symbol=? AND source=? AND interval_minutes=60
               AND close_time_utc<=? AND available_at_utc<=? ORDER BY close_time_utc DESC LIMIT 1""",
            (symbol, source, utc_iso(close), utc_iso(as_of))).fetchone()
        return row["close_time_utc"] if row else None


_STORE_SYMBOL = re.compile(r"[A-Z0-9]{1,16}/[A-Z]{3,5}")
_STORE_SOURCES = frozenset({"kraken_public_ohlc", "synthetic"})


def _store_provenance(revisions: Mapping[str, tuple[int, int]], candle: Candle, revision_id: str) -> dict[str, Any]:
    """A candle-store revision: its content hash stands for both the content and the source row."""
    stored = revisions.get(revision_id)
    return {
        "candle_id": revision_id,
        "content_id": candle.content_hash,
        "source": candle.source,
        "source_ref": candle.source_ref,
        "source_hash": candle.content_hash,
        "revision": stored[0] if stored else None,
        "run_id": stored[1] if stored else None,
        "open_time_utc": utc_iso(candle.open_time_utc),
        "close_time_utc": utc_iso(candle.close_time_utc),
        "available_at_utc": utc_iso(candle.available_at_utc),
    }


class CandleStoreHistory:
    """The hourly features from the candle store's 60-minute series (D220): the collector's ``kraken_public_ohlc``
    candles (``synthetic`` for scripted quotes), read point in time, for any coin the store holds.

    The numbers and statuses are those of ``hourly-price-features-v3``; a snapshot adds ``history_store:
    "candle_store"`` and names each candle by its store revision. Kraken fills a quiet hour with a flat candle of zero
    volume; it is read as Kraken sent it (``trade_count`` 0 means no trade happened), as the scanner and the indicator
    engine read it.
    """

    def __init__(self, store: CandleStore) -> None:
        self.store = store

    @staticmethod
    def supports(symbol: str) -> bool:
        return isinstance(symbol, str) and _STORE_SYMBOL.fullmatch(symbol) is not None

    def _check(self, symbol: str, source: str) -> None:
        if not self.supports(symbol):
            raise ValidationFailure("an upper-case BASE/QUOTE symbol is required")
        if source not in _STORE_SOURCES:
            raise ValidationFailure("unsupported hourly history source")

    def snapshot(
        self, symbol: str, as_of: datetime, requested_features: list[str], source: str = "kraken_public_ohlc",
    ) -> dict[str, Any]:
        self._check(symbol, source)
        window = _window(as_of, requested_features)
        count = (window.expected_close - window.search_start) // timedelta(hours=1) + 1
        slots = self.store.window(symbol, 60, source, end_close_time=window.expected_close, count=count,
                                  as_of=window.as_of).slots
        found, revisions = [], {}
        for slot in slots:
            if slot.stored is None:
                continue
            candle = slot.stored.candle
            revision_id = f"candle-store:{source}:{symbol}:60:{candle.open_unix}:r{slot.stored.revision}"
            revisions[revision_id] = (slot.stored.revision, slot.stored.run_id)
            found.append((revision_id, candle))
        return _snapshot(symbol, source, window, found, partial(_store_provenance, revisions),
                         {"history_store": "candle_store"})

    def received_close(self, symbol: str, close: datetime, as_of: datetime, source: str) -> bool:
        self._check(symbol, source)
        if close > as_of:
            return False
        window = self.store.window(symbol, 60, source, end_close_time=close, count=1, as_of=as_of)
        return window.slots[0].stored is not None

    def latest_close_at_or_before(self, symbol: str, close: datetime, as_of: datetime, source: str) -> str | None:
        self._check(symbol, source)
        rows = self.store.database.query(
            "SELECT MAX(m.open_time) AS newest FROM market_candles m JOIN candle_series s ON s.series_id = m.series_id "
            "WHERE s.symbol = ? AND s.source = ? AND s.interval_minutes = 60 AND m.open_time <= ? "
            "AND m.available_at <= ?", (symbol, source, to_unix(close) - 3600, to_micros(as_of)))
        newest = rows[0]["newest"] if rows else None
        return None if newest is None else utc_iso(from_unix(newest + 3600))

