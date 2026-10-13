"""Fill the gap between the archive and the collector from Kraken's public Trades list (T26 slice B).

Design (docs/KRAKEN-TRADES-BACKFILL.md has the reasoning and the measured numbers):

* The target range of one pair and interval is ``[archive series end, collector series start)`` read from the
  store's series summaries, or the caller's explicit ``start``/``end``. A missing bound is reported, never guessed.
* One time-ordered trade stream per pair feeds every requested interval (``trade_aggregation``). A candle is built
  only when a later trade proves its close has passed; its receipt time is the receipt of the page that completed it.
* Kraken's ``since`` was observed to be inclusive, so the page after a full page repeats the trades that share the
  final timestamp. Trades are consumed once, by id: an id at or below the highest consumed id is a duplicate, and a
  repeated trade that differs from what was consumed refuses the page. A skipped id marks the candles it could
  have changed as incomplete and they are withheld, not stored.
* A run is bounded by ``max_requests``. Its report records, per pair and interval, ``next_open_unix``: every slot
  before it was decided (stored, empty or withheld). The next run reads that cursor from ``store.runs()`` and
  re-fetches from the oldest open bucket, so no bucket state has to be persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from trade_graph.adapters.market.kraken_trades import (
    MAX_TRADES_PER_PAGE,
    PublicTrade,
    TradesPage,
    TradesPageRefused,
    TradesRateLimited,
    TradesThrottled,
    pair_code_for_symbol,
)
from trade_graph.adapters.market.trades_rate_limit import RateLimitWaitTooLong, RequestLimiter
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.trade_aggregation import CompletedBucket, TradeAggregator
from trade_graph.contracts.candles import Candle, from_unix, to_unix
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

BACKFILL_VERSION = "trades-backfill-v1"
TRADES_SOURCE = "kraken_public_trades"
ARCHIVE_SOURCE = "kraken_ohlcvt_archive"
COLLECTOR_SOURCE = "kraken_public_ohlc"
BACKFILL_INTERVALS = (1, 5, 15, 60)
BACKFILL_EVIDENCE_LABELS = ("live_public", "live_public_smoke", "recorded_public_fixture", "synthetic_fixture")
MAX_REQUESTS_LIMIT = 100_000
MIN_PAGE_SIZE = 2
MAX_PAGE_RETRIES = 2
MAX_TRANSPORT_RETRIES = 2
MAX_GAP_EXAMPLES = 10
MAX_RANGES_LISTED = 25
MAX_COVERAGE_SLOTS = 5_000_000
# A guard against typing an obvious stablecoin by hand (D131). The authority on what counts as a stablecoin or a
# yield token is the collector's dated pair catalog, not this list.
EXCLUDED_BASES = frozenset({
    "USDT", "USDC", "DAI", "PYUSD", "TUSD", "USDG", "USDD", "FDUSD", "USDE", "USDS", "RLUSD", "GUSD", "USDP",
})
STOP_REASONS = (
    "end_reached", "max_requests", "stream_exhausted", "rate_limited", "rate_limit_wait_too_long", "transport_error",
    "page_refused", "cursor_stalled", "cursor_regressed", "boundary_mismatch", "time_regressed", "store_refused",
    "interrupted", "unexpected_error", "no_target", "already_covered",
)
FAILURE_REASONS = frozenset(STOP_REASONS) - {"end_reached", "max_requests", "stream_exhausted", "no_target",
                                            "already_covered"}
NOTES = (
    "time_unit: Kraken's page does not state the unit of since. Unix seconds (first request) and the nanosecond "
    "cursor returned as last (later requests) were both accepted when observed on 2026-10-13.",
    "since_inclusive: a request whose since is the previous page's last cursor returned that page's final trade "
    "again as its first row, so since is treated as inclusive and repeated trades are removed by trade id.",
    "sequence_ids: trade ids were consecutive within every recorded page and are assumed consecutive per pair; a "
    "skipped id withholds the candles it could have changed instead of storing a possibly wrong candle.",
    "slot_rule: a trade exactly on a candle's close belongs to the next candle, assumed to match Kraken's OHLC "
    "rule; reconcile-candles shows any difference against the collector and the archive.",
    "trade_count: a candle counts rows of the Trades list; Kraken's own OHLC count may be defined differently.",
    "missing_slots: a missing candle means no trades in that slot or not reached yet; unreached_slots says which.",
    "survivorship: delisted pairs have no Trades history here, so coin-selection tests on it remain optimistic.",
)


class TradesClient(Protocol):
    def fetch(self, pair_code: str, *, since: int | None, count: int = MAX_TRADES_PER_PAGE) -> TradesPage: ...


@dataclass(frozen=True, slots=True)
class BackfillScope:
    """USD pairs and intervals to backfill; ``start``/``end`` replace the derived bounds when given."""

    symbols: tuple[str, ...]
    intervals: tuple[int, ...] = (15,)
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        symbols = tuple(sorted(self.symbols))
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValidationFailure("name at least one pair, each once")
        for symbol in symbols:
            pair_code_for_symbol(symbol)  # refuses anything that is not like BTC/USD
            if symbol.split("/")[0] in EXCLUDED_BASES:
                raise ValidationFailure(f"{symbol} is a stablecoin pair; stablecoins are excluded (D131)")
        intervals = tuple(sorted(self.intervals))
        if (not intervals or len(set(intervals)) != len(intervals)
                or any(type(i) is not int or i not in BACKFILL_INTERVALS for i in intervals)):
            raise ValidationFailure(f"intervals must be a non-empty set chosen from {BACKFILL_INTERVALS}")
        for name in ("start", "end"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                    raise ValidationFailure(f"{name} must be a timezone-aware time")
                try:
                    to_unix(value)
                except ValueError as error:
                    raise ValidationFailure(f"{name} must be a whole second") from error
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "intervals", intervals)

    def document(self) -> dict[str, Any]:
        return {"symbols": list(self.symbols), "intervals": list(self.intervals),
                "start_utc": None if self.start is None else utc_iso(self.start),
                "end_utc": None if self.end is None else utc_iso(self.end)}


@dataclass(frozen=True, slots=True)
class BackfillResult:
    run_id: int
    status: str
    report: dict[str, Any]


def _iso(seconds: int | None) -> str | None:
    return None if seconds is None else utc_iso(from_unix(seconds))


# --- target ranges and the stored cursor ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Target:
    symbol: str
    interval: int
    start: int | None
    end: int | None
    start_source: str | None
    end_source: str | None
    state: str  # ready, no_target or already_covered
    reason: str | None

    @property
    def step(self) -> int:
        return self.interval * 60

    @property
    def slots(self) -> int:
        return 0 if self.start is None or self.end is None else max(0, (self.end - self.start) // self.step)


def _plan_target(summaries: dict, scope: BackfillScope, symbol: str, interval: int) -> _Target:
    step = interval * 60
    start = end = start_source = end_source = None
    problems: list[str] = []
    if scope.start is not None:
        start, start_source = -(-to_unix(scope.start) // step) * step, "argument"
    else:
        archive = summaries.get((symbol, ARCHIVE_SOURCE, interval))
        if archive is not None and archive.last_open_utc is not None:
            start, start_source = to_unix(archive.last_open_utc) + step, "archive_end"
        else:
            problems.append(f"there is no archive series for {symbol} at {interval} minutes in this store and no "
                            "--start was given, so the start of the gap is unknown; import the archive or pass --start")
    if scope.end is not None:
        end, end_source = to_unix(scope.end) // step * step, "argument"
    else:
        collector = summaries.get((symbol, COLLECTOR_SOURCE, interval))
        if collector is not None and collector.first_open_utc is not None:
            end, end_source = to_unix(collector.first_open_utc), "collector_start"
        else:
            problems.append(f"there is no collector series for {symbol} at {interval} minutes in this store and no "
                            "--end was given, so the end of the gap is unknown; collect first or pass --end")
    if problems:
        return _Target(symbol, interval, start, end, start_source, end_source, "no_target", "; and ".join(problems))
    assert start is not None and end is not None
    if end <= start:
        return _Target(symbol, interval, start, end, start_source, end_source, "already_covered",
                       f"the range starts at {_iso(start)} and ends at {_iso(end)}: the sources overlap or touch, "
                       "so there is no gap to fill (reconcile-candles compares them)")
    return _Target(symbol, interval, start, end, start_source, end_source, "ready", None)


def read_resume_points(store: CandleStore, symbol: str, pair_code: str, interval: int, *,
                       range_start: int) -> int | None:
    """The newest stored cursor for this pair and interval that can still be trusted, or ``None``.

    Read from the reports of finished ``trades_backfill`` runs (``pairs.<symbol>.intervals.<n>.next_open_unix``). A
    cursor counts only when its run covered the same venue pair code and began at or before ``range_start``, because
    only then were all slots from ``range_start`` up to the cursor decided. A run that never finished has no report
    and gives no cursor.
    """
    step = interval * 60
    best: int | None = None
    for run in store.runs():
        if run.kind != "trades_backfill" or not isinstance(run.report, dict):
            continue
        pair = (run.report.get("pairs") or {}).get(symbol)
        entry = None if not isinstance(pair, dict) else (pair.get("intervals") or {}).get(str(interval))
        if not isinstance(entry, dict) or pair.get("pair_code") != pair_code:
            continue
        run_start, next_open = entry.get("range_start_unix"), entry.get("next_open_unix")
        if (type(run_start) is not int or type(next_open) is not int or next_open % step
                or run_start > range_start or next_open <= range_start):
            continue
        best = next_open if best is None else max(best, next_open)
    return best


# --- one pair ------------------------------------------------------------------------------------------------------


@dataclass
class _IntervalOutcome:
    built: int = 0
    inserted: int = 0
    duplicates: int = 0
    revised: int = 0
    withheld_gap: int = 0
    withheld_receipt: int = 0


@dataclass
class _Budget:
    maximum: int
    used: int = 0
    rate_limited: int = 0
    throttled: int = 0
    transport_errors: int = 0

    @property
    def spent(self) -> bool:
        return self.used >= self.maximum


@dataclass
class _PairState:
    symbol: str
    pair_code: str
    targets: dict[int, _Target]
    cursors: dict[int, int | None]
    aggregator: TradeAggregator | None = None
    committed: dict[int, int] = field(default_factory=dict)
    outcomes: dict[int, _IntervalOutcome] = field(default_factory=dict)
    stop_reason: str = "end_reached"
    message: str | None = None
    refusal_reason: str | None = None
    requests: int = 0
    pages: int = 0
    received: int = 0
    used: int = 0
    duplicates: int = 0
    unexpected_overlap: int = 0
    gap_count: int = 0
    gap_examples: list[dict[str, Any]] = field(default_factory=list)
    first_trade: PublicTrade | None = None
    last_trade: PublicTrade | None = None
    tail: dict[int, PublicTrade] = field(default_factory=dict)
    last_cursor: int | None = None
    resume_since: int | None = None
    not_started: bool = False

    def stop(self, reason: str, message: str | None = None, refusal: str | None = None) -> None:
        self.stop_reason, self.message, self.refusal_reason = reason, message, refusal


def _vet(state: _PairState, page: TradesPage) -> tuple[list[tuple[PublicTrade, bool, int | None]], int, int] | str:
    """Split a page into new trades and repeats without changing any state; a string is the reason to stop."""
    last_id = None if state.last_trade is None else state.last_trade.trade_id
    last_time = None if state.last_trade is None else state.last_trade.time
    fresh: list[tuple[PublicTrade, bool, int | None]] = []
    repeats = unexpected = 0
    for trade in page.trades:
        if last_id is not None and trade.trade_id <= last_id:
            known = state.tail.get(trade.trade_id)
            if known is None:
                unexpected += 1
            elif known != trade:
                return "boundary_mismatch"
            repeats += 1
            continue
        if last_time is not None and trade.time < last_time:
            return "time_regressed"
        fresh.append((trade, last_id is not None and trade.trade_id != last_id + 1, last_id))
        last_id, last_time = trade.trade_id, trade.time
    if page.full and not fresh:
        return "cursor_stalled"
    if state.last_cursor is not None and page.last < state.last_cursor:
        return "cursor_regressed"
    return fresh, repeats, unexpected


def _apply(state: _PairState, page: TradesPage, fresh: list[tuple[PublicTrade, bool, int | None]],
           repeats: int, unexpected: int) -> list[CompletedBucket]:
    assert state.aggregator is not None
    done: list[CompletedBucket] = []
    for trade, gap, previous_id in fresh:
        if gap:
            assert previous_id is not None
            state.gap_count += 1
            if len(state.gap_examples) < MAX_GAP_EXAMPLES:
                state.gap_examples.append({
                    "after_trade_id": previous_id, "next_trade_id": trade.trade_id,
                    "missing_trades": trade.trade_id - previous_id - 1,
                    "at_utc": utc_iso(from_unix(int(trade.time))),
                })
        done.extend(state.aggregator.feed(trade, gap_before=gap))
    state.pages += 1
    state.received += len(page.trades)
    state.used += len(fresh)
    state.duplicates += repeats
    state.unexpected_overlap += unexpected
    state.last_cursor = page.last
    if fresh:
        if state.first_trade is None:
            state.first_trade = fresh[0][0]
        state.last_trade = fresh[-1][0]
        same_time = [item for item in state.tail.values() if item.time == state.last_trade.time]
        same_time += [trade for trade, _, _ in fresh if trade.time == state.last_trade.time]
        state.tail = {trade.trade_id: trade for trade in same_time}
    return done


def _candles(state: _PairState, buckets: list[CompletedBucket], page: TradesPage) -> dict[int, list[Candle]]:
    built: dict[int, list[Candle]] = {}
    for bucket in buckets:
        outcome = state.outcomes[bucket.interval]
        close = from_unix(bucket.open_unix + bucket.interval * 60)
        if bucket.tainted:
            outcome.withheld_gap += 1
        elif page.received_at < close:
            outcome.withheld_receipt += 1  # never backdate availability to make a candle look earlier
        else:
            built.setdefault(bucket.interval, []).append(Candle(
                symbol=state.symbol, source_pair=state.pair_code, interval_minutes=bucket.interval,
                open_time_utc=from_unix(bucket.open_unix), close_time_utc=close, available_at_utc=page.received_at,
                open=bucket.open, high=bucket.high, low=bucket.low, close=bucket.close, vwap=bucket.vwap,
                volume=bucket.volume, trade_count=bucket.trade_count, source=TRADES_SOURCE))
    return built


def _run_pair(store: CandleStore, run_id: int, client: TradesClient, limiter: RequestLimiter, budget: _Budget,
              state: _PairState, page_size: int) -> None:
    aggregator = state.aggregator
    assert aggregator is not None
    if aggregator.finished:
        state.stop("end_reached")
        return
    since: int | None = state.resume_since
    retries = transport_retries = 0
    while True:
        if budget.spent:
            state.not_started = state.requests == 0
            state.stop("max_requests")
            return
        limiter.acquire()
        budget.used += 1
        state.requests += 1
        try:
            page = client.fetch(state.pair_code, since=since, count=page_size)
        except (TradesRateLimited, TradesThrottled) as error:
            if isinstance(error, TradesRateLimited):
                budget.rate_limited += 1
            else:
                budget.throttled += 1
            retries += 1
            if retries > MAX_PAGE_RETRIES:
                state.stop("rate_limited", f"Kraken still limited the request after {MAX_PAGE_RETRIES} waits",
                           error.reason)
                return
            try:
                limiter.penalize(error.retry_after_unix)
            except RateLimitWaitTooLong as too_long:
                state.stop("rate_limit_wait_too_long", str(too_long), error.reason)
                return
            continue
        except TradesPageRefused as error:
            state.stop("page_refused", str(error), error.reason)
            return
        except Exception as error:  # a transport failure: keep the class name, drop any details
            budget.transport_errors += 1
            transport_retries += 1
            if transport_retries > MAX_TRANSPORT_RETRIES:
                state.stop("transport_error", f"{type(error).__name__}: {str(error)[:160]}")
                return
            try:
                limiter.penalize(None)  # back off like a rate limit answer: a failing server is not hammered
            except RateLimitWaitTooLong as too_long:
                state.stop("rate_limit_wait_too_long", str(too_long))
                return
            continue
        retries = transport_retries = 0
        vetted = _vet(state, page)
        if isinstance(vetted, str):
            state.stop(vetted, f"the page was set aside: {vetted.replace('_', ' ')}")
            return
        fresh, repeats, unexpected = vetted
        built = _candles(state, _apply(state, page, fresh, repeats, unexpected), page)
        try:
            for interval, group in built.items():
                saved = store.save(group, run_id=run_id)
                outcome = state.outcomes[interval]
                outcome.built += len(group)
                outcome.inserted += saved.inserted
                outcome.duplicates += saved.duplicates
                outcome.revised += saved.revised
        except ValidationFailure as error:
            state.stop("store_refused", str(error)[:200])
            return
        state.committed = {interval: aggregator.next_open(interval) for interval in state.committed}
        if aggregator.finished:
            state.stop("end_reached")
            return
        if not page.full:
            state.stop("stream_exhausted", "Kraken had no more trades to give yet, before the end of the range")
            return
        since = page.last


# --- reporting -----------------------------------------------------------------------------------------------------


def _coverage_document(store: CandleStore, state: _PairState, target: _Target, next_open: int) -> dict[str, Any] | None:
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


def _interval_document(store: CandleStore, state: _PairState, target: _Target) -> dict[str, Any]:
    if target.state != "ready":
        return {"status": target.state, "reason": target.reason, "range_start_unix": target.start,
                "range_end_unix": target.end, "range_start_utc": _iso(target.start),
                "range_end_utc": _iso(target.end)}
    assert target.start is not None and target.end is not None
    outcome = state.outcomes[target.interval]
    next_open = state.committed.get(target.interval, target.start)
    cursor = state.cursors.get(target.interval)
    unreached = max(0, (target.end - next_open) // target.step)
    return {
        "status": "complete" if unreached == 0 else "partial",
        "range_start_unix": target.start, "range_end_unix": target.end,
        "range_start_utc": _iso(target.start), "range_end_utc": _iso(target.end),
        "range_source": {"start": target.start_source, "end": target.end_source},
        "target_slots": target.slots, "resumed_from_unix": cursor,
        "next_open_unix": next_open, "next_open_utc": _iso(next_open),
        "decided_slots": (next_open - target.start) // target.step, "unreached_slots": unreached,
        "unreached_from_utc": _iso(next_open) if unreached else None,
        "unreached_to_utc": _iso(target.end) if unreached else None,
        "candles": {"built": outcome.built, "saved_inserted": outcome.inserted, "duplicates": outcome.duplicates,
                    "revisions": outcome.revised, "withheld_trade_id_gap": outcome.withheld_gap,
                    "withheld_receipt_before_close": outcome.withheld_receipt},
        "decided_coverage": _coverage_document(store, state, target, next_open),
    }


def _pair_document(store: CandleStore, state: _PairState) -> dict[str, Any]:
    intervals = {str(interval): _interval_document(store, state, target)
                 for interval, target in sorted(state.targets.items())}
    states = {document["status"] for document in intervals.values()}
    if state.not_started:
        status = "not_started"
    elif state.stop_reason in FAILURE_REASONS:
        status = "failed"
    elif states <= {"no_target"}:
        status = "no_target"
    elif states <= {"complete", "already_covered"}:
        status = "complete"
    else:
        status = "partial"
    first, last = state.first_trade, state.last_trade
    return {
        "pair_code": state.pair_code, "status": status, "stop_reason": state.stop_reason, "message": state.message,
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


def _totals(pairs: dict[str, dict[str, Any]], budget: _Budget) -> dict[str, Any]:
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


def _not_reached(pairs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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


def _status(pairs: dict[str, dict[str, Any]], totals: dict[str, Any]) -> str:
    if all(pair["status"] == "no_target" for pair in pairs.values()):
        return "failed"
    clean = all(pair["status"] == "complete" for pair in pairs.values())
    withheld = totals["candles_withheld_trade_id_gap"] or totals["candles_withheld_receipt_before_close"]
    return "ok" if clean and not withheld else "partial"


# --- the backfill --------------------------------------------------------------------------------------------------


def _validate(max_requests: int, page_size: int, evidence_label: str) -> None:
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS_LIMIT:
        raise ValidationFailure(f"max_requests must be a whole number from 1 to {MAX_REQUESTS_LIMIT}")
    if type(page_size) is not int or not MIN_PAGE_SIZE <= page_size <= MAX_TRADES_PER_PAGE:
        raise ValidationFailure(f"page_size must be from {MIN_PAGE_SIZE} to {MAX_TRADES_PER_PAGE}")
    if evidence_label not in BACKFILL_EVIDENCE_LABELS:
        raise ValidationFailure(f"evidence_label must be one of {BACKFILL_EVIDENCE_LABELS}")


def _prepare_pair(store: CandleStore, scope: BackfillScope, summaries: dict, symbol: str,
                  ignore_cursor: bool) -> _PairState:
    pair_code = pair_code_for_symbol(symbol)
    targets = {interval: _plan_target(summaries, scope, symbol, interval) for interval in scope.intervals}
    state = _PairState(symbol=symbol, pair_code=pair_code, targets=targets, cursors={})
    ready = {interval: target for interval, target in targets.items() if target.state == "ready"}
    state.outcomes = {interval: _IntervalOutcome() for interval in ready}
    if not ready:
        state.stop("no_target" if all(t.state == "no_target" for t in targets.values()) else "already_covered")
        return state
    floors: dict[int, int] = {}
    for interval, target in ready.items():
        assert target.start is not None and target.end is not None
        cursor = None if ignore_cursor else read_resume_points(store, symbol, pair_code, interval,
                                                               range_start=target.start)
        state.cursors[interval] = cursor
        floors[interval] = target.start if cursor is None else min(cursor, target.end)
    state.aggregator = TradeAggregator(floors, {interval: ready[interval].end for interval in ready})
    state.committed = {interval: floors[interval] for interval in ready}
    open_floors = [floors[i] for i in ready if floors[i] < ready[i].end]
    state.resume_since = min(open_floors) - 1 if open_floors else None
    return state


def backfill_trades(store: CandleStore, clock: Clock, client: TradesClient, limiter: RequestLimiter,
                    scope: BackfillScope, *, max_requests: int, evidence_label: str = "live_public",
                    page_size: int = MAX_TRADES_PER_PAGE, ignore_cursor: bool = False) -> BackfillResult:
    """Run one bounded backfill; always records a ``trades_backfill`` run whose report holds the resume cursor."""
    _validate(max_requests, page_size, evidence_label)
    summaries = {(s.symbol, s.source, s.interval_minutes): s for s in store.series()}
    states = [_prepare_pair(store, scope, summaries, symbol, ignore_cursor) for symbol in scope.symbols]
    started_at = clock.now()
    run_id = store.begin_run(
        kind="trades_backfill", source=TRADES_SOURCE, evidence_label=evidence_label, started_at=started_at,
        parameters={"backfill_version": BACKFILL_VERSION, "scope": scope.document(), "max_requests": max_requests,
                    "page_size": page_size, "ignore_cursor": ignore_cursor,
                    "limiter": {name: str(getattr(limiter.settings, name)) for name in (
                        "max_counter", "decay_per_second", "cost", "headroom", "min_interval_seconds")}})
    budget = _Budget(maximum=max_requests)
    pairs: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "kind": "trades_backfill", "backfill_version": BACKFILL_VERSION, "status": "failed",
        "evidence_label": evidence_label, "started_at_utc": utc_iso(started_at), "scope": scope.document(),
        "pairs": pairs, "notes": list(NOTES),
    }
    try:
        for state in states:
            try:
                if state.aggregator is not None:
                    _run_pair(store, run_id, client, limiter, budget, state, page_size)
            except BaseException as error:
                interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
                state.stop("interrupted" if interrupted else "unexpected_error",
                           f"{type(error).__name__}: {str(error)[:160]}")
                raise
            finally:
                pairs[state.symbol] = _pair_document(store, state)
    except BaseException as error:
        reason = "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "unexpected_error"
        for state in states:
            if state.symbol not in pairs:
                state.not_started = True
                state.stop(reason)
                pairs[state.symbol] = _pair_document(store, state)
        report.update(reason=reason, message=f"{type(error).__name__}: {str(error)[:160]}")
        _finish(store, clock, run_id, report, limiter, budget, "failed")
        raise
    status = _finish(store, clock, run_id, report, limiter, budget, None)
    return BackfillResult(run_id=run_id, status=status, report=report)


def _finish(store: CandleStore, clock: Clock, run_id: int, report: dict[str, Any], limiter: RequestLimiter,
            budget: _Budget, forced: str | None) -> str:
    pairs = report["pairs"]
    totals = _totals(pairs, budget)
    status = forced or _status(pairs, totals)
    report.update(
        status=status, finished_at_utc=utc_iso(clock.now()), totals=totals, not_reached=_not_reached(pairs),
        requests={"max": budget.maximum, "made": budget.used, "rate_limited_responses": budget.rate_limited,
                  "throttled_responses": budget.throttled, "transport_errors": budget.transport_errors,
                  "penalties": limiter.penalties,
                  "waited_seconds": str(limiter.waited_seconds)})
    try:
        store.finish_run(run_id, finished_at=clock.now(), status=status, report=report)
    except Exception:
        if forced is None:
            raise
    return status


__all__ = [
    "BACKFILL_EVIDENCE_LABELS", "BACKFILL_INTERVALS", "BACKFILL_VERSION", "BackfillResult", "BackfillScope",
    "STOP_REASONS", "TRADES_SOURCE", "TradesClient", "backfill_trades", "read_resume_points",
]
