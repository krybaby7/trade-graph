"""Bounded, receipt-aware hourly features from immutable completed candles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal

FEATURE_VERSION = "hourly-price-features-v1"
MAX_INGEST_CANDLES = 720
MAX_LOOKBACK_CANDLES = 50
_ARITHMETIC = Context(prec=50, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class _Feature:
    lookback: int
    definition: str


_FEATURES = {
    "sma_20": _Feature(20, "Arithmetic mean of the last 20 completed hourly closes, in USD."),
    "sma_50": _Feature(50, "Arithmetic mean of the last 50 completed hourly closes, in USD."),
    "pullback_from_high": _Feature(20, "(Maximum high of the last 20 completed hours - latest close) / maximum high."),
    "pullback_depth": _Feature(20, "Alias of pullback_from_high: 20-hour high-to-latest-close fractional pullback."),
    "range_high": _Feature(24, "Maximum high of the last 24 completed hourly candles, in USD."),
    "range_low": _Feature(24, "Minimum low of the last 24 completed hourly candles, in USD."),
    "midpoint": _Feature(24, "(24-hour range_high + 24-hour range_low) / 2, in USD."),
    "midpoint_distance": _Feature(24, "(Latest hourly close - 24-hour range midpoint) / range midpoint."),
    "hourly_return": _Feature(2, "Latest completed hourly close / previous hourly close - 1 (fractional return)."),
    "four_hour_drift": _Feature(5, "Latest completed hourly close / close four hours earlier - 1 (fractional return)."),
}


def _value(name: str, candles: list[HourlyCandle]) -> str:
    """One pinned Decimal context applies to all operations, including sums."""
    with localcontext(_ARITHMETIC):
        latest = candles[-1].close
        if name in {"sma_20", "sma_50"}:
            value = sum((candle.close for candle in candles), Decimal(0)) / Decimal(len(candles))
        elif name in {"pullback_from_high", "pullback_depth"}:
            high = max(candle.high for candle in candles)
            value = (high - latest) / high
        elif name == "hourly_return" or name == "four_hour_drift":
            value = latest / candles[0].close - Decimal(1)
        else:
            high = max(candle.high for candle in candles)
            low = min(candle.low for candle in candles)
            midpoint = (high + low) / Decimal(2)
            value = {"range_high": high, "range_low": low, "midpoint": midpoint}.get(name)
            if value is None:
                value = (latest - midpoint) / midpoint
        return canonical_decimal(value)


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

    def snapshot(
        self, symbol: str, as_of: datetime, requested_features: list[str], source: str = "kraken_public_ohlc",
    ) -> dict[str, Any]:
        if symbol not in {"BTC/USD", "ETH/USD"}:
            raise ValueError("unsupported hourly history symbol")
        if source not in {"kraken_public_ohlc", "synthetic"}:
            raise ValueError("unsupported hourly history source")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if (len(requested_features) > 32
                or any(not isinstance(name, str) or len(name) > 64 for name in requested_features)):
            raise ValueError("at most 32 feature names of at most 64 characters are allowed")
        as_of = as_of.astimezone(UTC)
        expected_close = as_of.replace(minute=0, second=0, microsecond=0)
        names = list(dict.fromkeys(requested_features))
        lookback = max((_FEATURES[name].lookback for name in names if name in _FEATURES), default=1)
        start = expected_close - timedelta(hours=lookback - 1)
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
            (symbol, source, utc_iso(start), utc_iso(expected_close), utc_iso(as_of)),
        ).fetchall()
        candles = [HourlyCandle.model_validate_json(row["document_json"]) for row in rows]
        revision_ids = {candle.close_time_utc: row["candle_id"] for row, candle in zip(rows, candles, strict=True)}
        by_close = {candle.close_time_utc: candle for candle in candles}
        earliest = min(by_close, default=None)
        values: dict[str, str] = {}
        features: dict[str, Any] = {}
        for name in names:
            spec = _FEATURES.get(name)
            if spec is None:
                features[name] = {
                    "status": "unsupported", "definition": "No supported hourly definition for this feature.",
                    "lookback_candles": None, "available_candles": 0,
                    "missing_close_times_utc": [], "candle_ids": [],
                    "window_start_close_time_utc": None, "window_end_close_time_utc": None,
                    "event_time_utc": None, "available_at_utc": None,
                }
                continue
            expected = [expected_close - timedelta(hours=offset) for offset in reversed(range(spec.lookback))]
            selected = [by_close[at] for at in expected if at in by_close]
            missing = [at for at in expected if at not in by_close]
            status = "ready"
            if missing:
                status = ("gapped_history" if earliest is not None and any(at >= earliest for at in missing)
                          else "insufficient_history")
            if status == "ready":
                values[name] = _value(name, selected)
            features[name] = {
                "status": status, "definition": spec.definition, "lookback_candles": spec.lookback,
                "available_candles": len(selected), "missing_close_times_utc": [utc_iso(at) for at in missing],
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
            "provenance": [_provenance(candle, revision_ids[candle.close_time_utc]) for candle in candles],
            "values": values, "features": features,
            "event_time_utc": utc_iso(latest) if latest else None,
            "available_at_utc": utc_iso(max(candle.available_at_utc for candle in candles)) if candles else None,
        }
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        result["source_ref"] = "hourly-features:" + hashlib.sha256(encoded).hexdigest()
        return result
