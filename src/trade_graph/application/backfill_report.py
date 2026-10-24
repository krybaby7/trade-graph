"""The JSON documents of a Trades backfill run: per interval, per pair, totals, what was not reached, and the status."""

from __future__ import annotations

from typing import Any

from trade_graph.application.backfill_model import (
    FAILURE_REASONS,
    TRADES_SOURCE,
    Budget,
    PairState,
    Target,
    iso,
)
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import from_unix

MAX_RANGES_LISTED = 25
MAX_COVERAGE_SLOTS = 5_000_000
NOTES = (
    "time_unit: Kraken's page does not state the unit of since. Unix seconds (first request) and the nanosecond "
    "cursor returned as last (later requests) were both accepted when observed on 2026-10-13.",
    "since_inclusive: a request whose since is the previous page's last cursor returned that page's final trade "
    "again as its first row, so since is treated as inclusive and repeated trades are removed by trade id.",
    "sequence_ids: trade ids were consecutive within every recorded page and are assumed consecutive per pair; a "
    "skipped id withholds the candles it could have changed instead of storing a possibly wrong candle. A gap just "
    "before the first trade a run receives cannot be seen, except that a resumed run carries its cursor candle's flag.",
    "slot_rule: a trade exactly on a candle's close belongs to the next candle, assumed to match Kraken's OHLC "
    "rule; reconcile-candles shows any difference against the collector and the archive.",
    "trade_count: a candle counts rows of the Trades list; Kraken's own OHLC count may be defined differently.",
    "missing_slots: a missing candle means no trades in that slot or not reached yet; unreached_slots says which.",
    "survivorship: delisted pairs have no Trades history here, so coin-selection tests on it remain optimistic.",
)


def coverage_document(store: CandleStore, state: PairState, target: Target, next_open: int) -> dict[str, Any] | None:
    """The store's own coverage of the decided part of the range, with the largest empty ranges listed."""
    assert target.start is not None
    if next_open <= target.start:
        return None
    if (next_open - target.start) // target.step > MAX_COVERAGE_SLOTS:
        return {"note": "the decided range is too large for a coverage listing"}
    report = store.coverage(state.symbol, target.interval, TRADES_SOURCE, start_open=from_unix(target.start),
                            end_open=from_unix(next_open), mode="replay")
    document = report.to_document()
    ranges = document.pop("missing_ranges")
    keep = sorted(range(len(ranges)), key=lambda index: (-ranges[index]["slots"], ranges[index]["first_open_utc"]))
    keep = sorted(keep[:MAX_RANGES_LISTED])
    document["missing_ranges_total"] = len(ranges)
    document["missing_ranges_truncated"] = len(keep) < len(ranges)
    document["missing_ranges"] = [ranges[index] for index in keep]
    return document


def interval_document(store: CandleStore, state: PairState, target: Target) -> dict[str, Any]:
    if target.state != "ready":
        return {"status": target.state, "reason": target.reason, "range_start_unix": target.start,
                "range_end_unix": target.end, "range_start_utc": iso(target.start), "range_end_utc": iso(target.end)}
    assert target.start is not None and target.end is not None
    outcome = state.outcomes[target.interval]
    next_open = state.committed.get(target.interval, target.start)
    cursor = state.cursors.get(target.interval)
    unreached = max(0, (target.end - next_open) // target.step)
    began = target.start if cursor is None else min(cursor, target.end)
    decided_now = (next_open - began) // target.step
    first = state.first_trade
    # Seconds before the first trade this run received. Only a run that began at the range start can say it: a resumed
    # run's first trade is near its cursor. Slots before that trade are empty, or Kraken no longer serves them.
    leading_gap = None if cursor is not None or first is None else max(0, int(first.time) - target.start)
    return {
        "status": "complete" if unreached == 0 else "partial",
        "range_start_unix": target.start, "range_end_unix": target.end,
        "range_start_utc": iso(target.start), "range_end_utc": iso(target.end),
        "range_source": {"start": target.start_source, "end": target.end_source},
        "target_slots": target.slots, "resumed_from_unix": cursor,
        "next_open_unix": next_open, "next_open_utc": iso(next_open),
        "open_candle_has_id_gap": bool(state.open_tainted.get(target.interval, False)),
        "decided_slots": (next_open - target.start) // target.step,
        "decided_this_run": decided_now, "unreached_slots": unreached,
        "unreached_from_utc": iso(next_open) if unreached else None,
        "unreached_to_utc": iso(target.end) if unreached else None,
        "leading_gap_seconds": leading_gap,
        # Slots were decided this run, yet no candle came of them and none was withheld: every one of them read as
        # empty, which looks the same as history that Kraken does not serve (how far back it reaches is unconfirmed).
        "no_candle_built": decided_now > 0 and not (outcome.built or outcome.withheld_gap or outcome.withheld_receipt),
        "candles": {"built": outcome.built, "saved_inserted": outcome.inserted, "duplicates": outcome.duplicates,
                    "revisions": outcome.revised, "withheld_trade_id_gap": outcome.withheld_gap,
                    "withheld_receipt_before_close": outcome.withheld_receipt},
        "decided_coverage": coverage_document(store, state, target, next_open),
    }


def _empty_range_message(state: PairState, empty: dict[str, dict[str, Any]]) -> str:
    leading = next(document["leading_gap_seconds"] for document in empty.values())
    first = state.first_trade
    hours, minutes = divmod((leading or 0) // 60, 60)
    where = "" if first is None or leading is None else (
        f" The first trade Kraken returned is at {iso(int(first.time))}, {hours} h {minutes} min after the range "
        "start.")
    return (f"no candle was built for the {', '.join(empty)} minute interval(s): every slot read as empty.{where} "
            "That cannot be told apart from history Kraken no longer serves or an outage, so check how far back the "
            "Trades list reaches before treating those slots as having no trades.")


def pair_document(store: CandleStore, state: PairState) -> dict[str, Any]:
    intervals = {str(interval): interval_document(store, state, target)
                 for interval, target in sorted(state.targets.items())}
    states = {document["status"] for document in intervals.values()}
    empty = {interval: document for interval, document in intervals.items() if document.get("no_candle_built")}
    message = state.message
    if state.not_started:
        status = "not_started"
    elif state.stop_reason in FAILURE_REASONS:
        status = "failed"
    elif states <= {"no_target"}:
        status = "no_target"
    elif states <= {"complete", "already_covered"}:
        status = "partial" if empty else "complete"
        if empty and message is None:
            message = _empty_range_message(state, empty)
    else:
        status = "partial"
    first, last = state.first_trade, state.last_trade
    return {
        "pair_code": state.pair_code, "status": status, "stop_reason": state.stop_reason, "message": message,
        "refusal_reason": state.refusal_reason, "requests": state.requests, "pages": state.pages,
        "trades_received": state.received, "trades_used": state.used, "duplicate_trades": state.duplicates,
        "unexpected_overlap": state.unexpected_overlap,
        "ignored_before_range": 0 if state.aggregator is None else state.aggregator.ignored_before_range,
        "ignored_after_end": 0 if state.aggregator is None else state.aggregator.ignored_after_end,
        "id_gaps": state.gap_count, "id_gap_examples": state.gap_examples,
        "first_trade_id": None if first is None else first.trade_id,
        "first_trade_time": None if first is None else str(first.time),
        "last_trade_id": None if last is None else last.trade_id,
        "last_trade_time": None if last is None else str(last.time),
        "resume_since_unix": state.resume_since, "intervals": intervals,
    }


def totals(pairs: dict[str, dict[str, Any]], budget: Budget) -> dict[str, Any]:
    candles = [doc["candles"] for pair in pairs.values() for doc in pair["intervals"].values() if "candles" in doc]
    return {
        "pairs": len(pairs), "requests_made": budget.used,
        "trades_received": sum(p["trades_received"] for p in pairs.values()),
        "trades_used": sum(p["trades_used"] for p in pairs.values()),
        "duplicate_trades": sum(p["duplicate_trades"] for p in pairs.values()),
        "id_gaps": sum(p["id_gaps"] for p in pairs.values()),
        "candles_built": sum(c["built"] for c in candles),
        "candles_saved": sum(c["saved_inserted"] for c in candles),
        "candles_duplicate": sum(c["duplicates"] for c in candles),
        "candles_revised": sum(c["revisions"] for c in candles),
        "candles_withheld_trade_id_gap": sum(c["withheld_trade_id_gap"] for c in candles),
        "candles_withheld_receipt_before_close": sum(c["withheld_receipt_before_close"] for c in candles),
    }


def not_reached(pairs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every pair and interval whose range was not fully decided, or could not be planned, with the span left."""
    listed = []
    for symbol, pair in pairs.items():
        for interval, document in pair["intervals"].items():
            if document.get("unreached_slots"):
                listed.append({"symbol": symbol, "interval_minutes": int(interval),
                               "unreached_slots": document["unreached_slots"],
                               "from_utc": document["unreached_from_utc"], "to_utc": document["unreached_to_utc"],
                               "stop_reason": pair["stop_reason"]})
            elif document["status"] == "no_target":
                listed.append({"symbol": symbol, "interval_minutes": int(interval), "unreached_slots": None,
                               "from_utc": None, "to_utc": None, "stop_reason": "no_target",
                               "reason": document["reason"]})
    return listed


def run_status(pairs: dict[str, dict[str, Any]], run_totals: dict[str, Any]) -> str:
    """``failed`` when nothing could be planned or a pair broke down and no range moved forward in this run, ``ok``
    when every range is complete and nothing was withheld, otherwise ``partial``."""
    if all(pair["status"] == "no_target" for pair in pairs.values()):
        return "failed"
    moved = any(document.get("decided_this_run") for pair in pairs.values() for document in pair["intervals"].values())
    if not moved and any(pair["stop_reason"] in FAILURE_REASONS for pair in pairs.values()):
        return "failed"
    clean = all(pair["status"] == "complete" for pair in pairs.values())
    withheld = run_totals["candles_withheld_trade_id_gap"] or run_totals["candles_withheld_receipt_before_close"]
    return "ok" if clean and not withheld else "partial"
