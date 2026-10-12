"""Bounded readiness of the hourly candle a fresh decision needs; no provider imports.

The public source commits the hour that closed at HH:00 only once its next frame
exists, a few minutes later. A fresh decision may wait for that candle until a
bounded deadline. Whether it is stored, and whether the decision waited, is then
stated plainly in the snapshot. Presence is point-in-time: receipt by as_of.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import parse_utc, utc_iso

DEFAULT_DECISION_CANDLE_WAIT_SECONDS = 300
MAX_DECISION_CANDLE_WAIT_SECONDS = 3000  # A wait always ends inside the hour it waits for.
CANDLE_RECHECK_SECONDS = 5
_SECOND = timedelta(seconds=1)
_RECORD_TIMES = ("expected_close_time_utc", "waiting_since_utc", "wait_deadline_utc")


def expected_close(as_of: datetime) -> datetime:
    """Close time of the latest hourly candle that has completed at as_of."""
    return as_of.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def missing_expected_candles(database: Database, symbols: Iterable[str], as_of: datetime,
                             source: str = "kraken_public_ohlc") -> list[str]:
    """Symbols whose expected candle had not been received by as_of."""
    close, received_by = utc_iso(expected_close(as_of)), utc_iso(as_of)
    return [symbol for symbol in symbols if database.execute(
        """SELECT 1 FROM hourly_candles WHERE symbol=? AND source=? AND interval_minutes=60
           AND close_time_utc=? AND available_at_utc<=? LIMIT 1""",
        (symbol, source, close, received_by)).fetchone() is None]


def wait_record(output_json: str | None) -> dict | None:
    """The service's own wait record kept in a deferred task's output."""
    try:
        output = json.loads(output_json) if output_json else {}
    except ValueError:
        return None
    record = output.get("_candle_wait") if isinstance(output, dict) else None
    return record if isinstance(record, dict) else None


def _matching(record: dict | None, expected: datetime) -> dict | None:
    """A well-formed record counts only for the same expected close."""
    if not isinstance(record, dict) or any(type(record.get(key)) is not str for key in _RECORD_TIMES):
        return None
    try:
        times = [parse_utc(record[key]) for key in _RECORD_TIMES]
    except ValueError:
        return None
    return record if times[0] == expected else None


def candle_wait(database: Database, symbols: Iterable[str], now: datetime, *, wait_seconds: int,
                previous_output_json: str | None, source: str = "kraken_public_ohlc") -> tuple[int, dict] | None:
    """Return (recheck delay, deferral output) while the expected candle is missing before the deadline."""
    expected = expected_close(now)
    deadline = expected + timedelta(seconds=wait_seconds)
    if now >= deadline:
        return None
    missing = missing_expected_candles(database, symbols, now, source)
    if not missing:
        return None
    previous = _matching(wait_record(previous_output_json), expected)
    # Recheck every few seconds; the final recheck lands on, never before, the deadline.
    delay = max(1, min(CANDLE_RECHECK_SECONDS, -((now - deadline) // _SECOND)))
    return delay, {
        "reason": f"Waiting for the hourly candle that closed at {expected:%H:%M} UTC.",
        "_candle_wait": {
            "expected_close_time_utc": utc_iso(expected), "missing_symbols": missing,
            "waiting_since_utc": previous["waiting_since_utc"] if previous else utc_iso(now),
            "wait_deadline_utc": utc_iso(deadline),
        },
    }


def _seconds(count: int) -> str:
    return "1 second" if count == 1 else f"{count} seconds"


def _note(status: str, expected: datetime, latest: str | None, waited: int) -> str:
    candle = f"The hourly candle that closed at {expected:%H:%M} UTC"
    if status == "present":
        return candle + (f" is stored; this decision waited {_seconds(waited)} for it." if waited else " is stored.")
    if latest is None:
        earlier = "no earlier hourly candle is stored"
    else:
        at = parse_utc(latest)
        earlier = "the latest stored hourly candle closed at " + (
            f"{at:%H:%M} UTC" if at.date() == expected.date() else f"{at:%Y-%m-%d %H:%M} UTC")
    if status == "missing_after_wait":
        return f"{candle} was still not stored after waiting {_seconds(waited)}; {earlier}."
    return f"{candle} is not stored yet; {earlier}."


def expected_candle_status(database: Database, symbol: str, as_of: datetime, wait: dict | None, *,
                           source: str = "kraken_public_ohlc") -> dict:
    """Plainly state whether the latest completed hour is stored and whether this decision waited."""
    expected = expected_close(as_of)
    row = database.execute(
        """SELECT close_time_utc FROM hourly_candles WHERE symbol=? AND source=? AND interval_minutes=60
           AND close_time_utc<=? AND available_at_utc<=? ORDER BY close_time_utc DESC LIMIT 1""",
        (symbol, source, utc_iso(expected), utc_iso(as_of))).fetchone()
    latest = row["close_time_utc"] if row else None
    record = _matching(wait, expected)
    waited = max(0, (as_of - parse_utc(record["waiting_since_utc"])) // _SECOND) if record else 0
    status = "present" if latest == utc_iso(expected) else "missing_after_wait" if record else "missing"
    return {"status": status, "expected_close_time_utc": utc_iso(expected), "latest_stored_close_time_utc": latest,
            "waited_seconds": waited, "wait_deadline_utc": record["wait_deadline_utc"] if record else None,
            "note": _note(status, expected, latest, waited)}
