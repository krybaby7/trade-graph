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
  re-fetches from the oldest open bucket, so no bucket state has to be persisted except one flag per interval:
  ``open_candle_has_id_gap``, because the trade before the open bucket is not fetched again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from trade_graph.adapters.market.kraken_archive import is_fiat_symbol
from trade_graph.adapters.market.kraken_trades import (
    MAX_TRADES_PER_PAGE,
    MAX_UNIX_TIME,
    MIN_UNIX_TIME,
    PublicTrade,
    TradesPage,
    TradesPageRefused,
    TradesRateLimited,
    TradesThrottled,
    pair_code_for_symbol,
)
from trade_graph.adapters.market.trades_rate_limit import RateLimitWaitTooLong, RequestLimiter
from trade_graph.application import backfill_report as report_documents
from trade_graph.application.backfill_model import (
    ARCHIVE_SOURCE,
    COLLECTOR_SOURCE,
    STOP_REASONS,
    TRADES_SOURCE,
    Budget,
    IntervalOutcome,
    PairState,
    Target,
    iso,
)
from trade_graph.application.backfill_report import MAX_RANGES_LISTED, NOTES
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.trade_aggregation import CompletedBucket, TradeAggregator
from trade_graph.contracts.candles import Candle, RunRecord, from_unix, to_unix
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

BACKFILL_VERSION = "trades-backfill-v3"  # v3: an id gap before the slot at a finished range's end survives an extension
BACKFILL_INTERVALS = (1, 5, 15, 60)
BACKFILL_EVIDENCE_LABELS = ("live_public", "live_public_smoke", "recorded_public_fixture", "synthetic_fixture")
MAX_REQUESTS_LIMIT = 100_000
MIN_PAGE_SIZE = 2
MAX_PAGE_RETRIES = 2
MAX_TRANSPORT_RETRIES = 2
MAX_GAP_EXAMPLES = 10
# A guard against typing an obvious stablecoin by hand (D131). The authority on what counts as a stablecoin or a
# yield token is the collector's dated pair catalog, not this list.
EXCLUDED_BASES = frozenset({
    "USDT", "USDC", "DAI", "PYUSD", "TUSD", "USDG", "USDD", "FDUSD", "USDE", "USDS", "RLUSD", "GUSD", "USDP",
})


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
            if is_fiat_symbol(symbol):
                raise ValidationFailure(f"{symbol} is a currency pair, not a coin; only coin pairs are backfilled")
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
                    seconds = to_unix(value)
                except ValueError as error:
                    raise ValidationFailure(f"{name} must be a whole second") from error
                if not MIN_UNIX_TIME <= seconds < MAX_UNIX_TIME:
                    raise ValidationFailure(f"{name} must fall from 2010-01-01 up to 2100-01-01 (Kraken has no trades "
                                            "outside that window)")
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


# --- target ranges and the stored cursor ---------------------------------------------------------------------------


def _plan_target(summaries: dict, scope: BackfillScope, symbol: str, interval: int) -> Target:
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
        return Target(symbol, interval, start, end, start_source, end_source, "no_target", "; and ".join(problems))
    assert start is not None and end is not None
    if end <= start:
        return Target(symbol, interval, start, end, start_source, end_source, "already_covered",
                      f"the range starts at {iso(start)} and ends at {iso(end)}: the sources overlap or touch, "
                      "so there is no gap to fill (reconcile-candles compares them)")
    return Target(symbol, interval, start, end, start_source, end_source, "ready", None)


@dataclass(frozen=True, slots=True)
class ResumePoint:
    """Where a resumed run starts: ``next_open`` is the oldest undecided slot, and ``open_candle_has_id_gap`` says
    the candle opening there already had a known gap in its trade ids, which the re-fetch cannot see again."""

    next_open: int
    open_candle_has_id_gap: bool


def read_resume_point(store: CandleStore, symbol: str, pair_code: str, interval: int, *,
                      range_start: int, runs: list[RunRecord] | None = None) -> ResumePoint | None:
    """The newest stored cursor for this pair and interval that can still be trusted, or ``None``.

    Read from the reports of finished ``trades_backfill`` runs (``pairs.<symbol>.intervals.<n>.next_open_unix``). A
    cursor counts only when its run covered the same venue pair code and began at or before ``range_start``, because
    only then were all slots from ``range_start`` up to the cursor decided. A run that never finished has no report
    and gives no cursor. ``runs`` may carry an already loaded ``store.runs()`` listing. The gap flag is true when any
    run that ended on the newest cursor reported it, or did not report it at all (unknown counts as a gap).
    """
    step = interval * 60
    found: list[tuple[int, bool]] = []
    for run in store.runs() if runs is None else runs:
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
        found.append((next_open, entry.get("open_candle_has_id_gap") is not False))
    if not found:
        return None
    newest = max(next_open for next_open, _ in found)
    return ResumePoint(newest, any(flag for next_open, flag in found if next_open == newest))


def read_resume_points(store: CandleStore, symbol: str, pair_code: str, interval: int, *,
                       range_start: int, runs: list[RunRecord] | None = None) -> int | None:
    """Only the cursor of ``read_resume_point``."""
    point = read_resume_point(store, symbol, pair_code, interval, range_start=range_start, runs=runs)
    return None if point is None else point.next_open


# --- one pair ------------------------------------------------------------------------------------------------------


def _vet(state: PairState, page: TradesPage) -> tuple[list[tuple[PublicTrade, bool, int | None]], int, int] | str:
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


def _apply(state: PairState, page: TradesPage, fresh: list[tuple[PublicTrade, bool, int | None]],
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


def _candles(state: PairState, buckets: list[CompletedBucket], page: TradesPage) -> dict[int, list[Candle]]:
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


def _save(store: CandleStore, run_id: int, state: PairState, built: dict[int, list[Candle]]) -> None:
    for interval, group in built.items():
        saved = store.save(group, run_id=run_id)
        outcome = state.outcomes[interval]
        outcome.built += len(group)
        outcome.inserted += saved.inserted
        outcome.duplicates += saved.duplicates
        outcome.revised += saved.revised


def _run_pair(store: CandleStore, run_id: int, client: TradesClient, limiter: RequestLimiter, budget: Budget,
              state: PairState, page_size: int) -> None:
    aggregator = state.aggregator
    assert aggregator is not None
    if aggregator.finished:
        state.stop("end_reached")
        return
    since: int | None = state.resume_since
    began = dict(state.committed)
    retries = transport_retries = 0
    while True:
        if budget.spent:
            state.not_started = state.requests == 0
            if state.requests and state.committed == began:
                # The next run restarts at the same open candle, so it would spend the same budget the same way.
                state.stop("max_requests", f"the budget of {budget.maximum} requests ended before one whole candle "
                           "had been received, so this run decided no slot and the next one would repeat it; raise "
                           "--max-requests (a busy pair needs many requests for one hour candle)")
            else:
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
        except ValidationFailure as error:  # raised before anything was sent: waiting or asking again cannot help
            state.stop("invalid_request", str(error)[:200])
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
            _save(store, run_id, state, built)
        except ValidationFailure as error:
            state.stop("store_refused", str(error)[:200])
            return
        state.committed = {interval: aggregator.next_open(interval) for interval in state.committed}
        state.open_tainted = {interval: aggregator.open_tainted(interval) for interval in state.committed}
        if aggregator.finished:
            state.stop("end_reached")
            return
        if not page.full:
            state.stop("stream_exhausted", "Kraken had no more trades to give yet, before the end of the range")
            return
        since = page.last


# --- the backfill --------------------------------------------------------------------------------------------------


def _validate(max_requests: int, page_size: int, evidence_label: str) -> None:
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS_LIMIT:
        raise ValidationFailure(f"max_requests must be a whole number from 1 to {MAX_REQUESTS_LIMIT}")
    if type(page_size) is not int or not MIN_PAGE_SIZE <= page_size <= MAX_TRADES_PER_PAGE:
        raise ValidationFailure(f"page_size must be from {MIN_PAGE_SIZE} to {MAX_TRADES_PER_PAGE}")
    if evidence_label not in BACKFILL_EVIDENCE_LABELS:
        raise ValidationFailure(f"evidence_label must be one of {BACKFILL_EVIDENCE_LABELS}")


def _prepare_pair(store: CandleStore, scope: BackfillScope, summaries: dict, symbol: str,
                  past_runs: list[RunRecord] | None) -> PairState:
    pair_code = pair_code_for_symbol(symbol)
    targets = {interval: _plan_target(summaries, scope, symbol, interval) for interval in scope.intervals}
    state = PairState(symbol=symbol, pair_code=pair_code, targets=targets, cursors={})
    ready = {interval: target for interval, target in targets.items() if target.state == "ready"}
    state.outcomes = {interval: IntervalOutcome() for interval in ready}
    if not ready:
        state.stop("no_target" if all(t.state == "no_target" for t in targets.values()) else "already_covered")
        return state
    floors: dict[int, int] = {}
    carried: set[int] = set()
    for interval, target in ready.items():
        assert target.start is not None and target.end is not None
        point = None if past_runs is None else read_resume_point(
            store, symbol, pair_code, interval, range_start=target.start, runs=past_runs)
        state.cursors[interval] = None if point is None else point.next_open
        floors[interval] = target.start if point is None else min(point.next_open, target.end)
        if point is not None and point.open_candle_has_id_gap:
            carried.add(interval)  # also at a finished range's end: the report keeps the flag for a later extension
    state.aggregator = TradeAggregator(floors, {interval: ready[interval].end for interval in ready},
                                       tainted_open=carried)
    state.committed = {interval: floors[interval] for interval in ready}
    state.open_tainted = {interval: interval in carried for interval in ready}
    open_floors = [floors[i] for i in ready if floors[i] < ready[i].end]
    state.resume_since = min(open_floors) - 1 if open_floors else None
    return state


def backfill_trades(store: CandleStore, clock: Clock, client: TradesClient, limiter: RequestLimiter,
                    scope: BackfillScope, *, max_requests: int, evidence_label: str = "live_public",
                    page_size: int = MAX_TRADES_PER_PAGE, ignore_cursor: bool = False) -> BackfillResult:
    """Run one bounded backfill; always records a ``trades_backfill`` run whose report holds the resume cursor."""
    _validate(max_requests, page_size, evidence_label)
    summaries = {(s.symbol, s.source, s.interval_minutes): s for s in store.series()}
    past_runs = None if ignore_cursor else [run for run in store.runs() if run.kind == "trades_backfill"]
    states = [_prepare_pair(store, scope, summaries, symbol, past_runs) for symbol in scope.symbols]
    started_at = clock.now()
    run_id = store.begin_run(
        kind="trades_backfill", source=TRADES_SOURCE, evidence_label=evidence_label, started_at=started_at,
        parameters={"backfill_version": BACKFILL_VERSION, "scope": scope.document(), "max_requests": max_requests,
                    "page_size": page_size, "ignore_cursor": ignore_cursor,
                    "limiter": {name: str(getattr(limiter.settings, name)) for name in (
                        "max_counter", "decay_per_second", "cost", "headroom", "min_interval_seconds")}})
    budget = Budget(maximum=max_requests)
    pairs: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "kind": "trades_backfill", "backfill_version": BACKFILL_VERSION, "status": "failed",
        "evidence_label": evidence_label, "started_at_utc": utc_iso(started_at), "scope": scope.document(),
        "pairs": pairs, "notes": list(NOTES),
    }
    paused = False  # Kraken asked for a pause longer than this run waits: no other pair gets a request either
    try:
        for state in states:
            if paused and state.aggregator is not None and not state.aggregator.finished:
                state.not_started = True
                state.stop("rate_limit_wait_too_long", "not started: an earlier pair stopped because Kraken asked "
                           "for a longer pause than this run waits")
                pairs[state.symbol] = report_documents.pair_document(store, state)
                continue
            try:
                if state.aggregator is not None:
                    _run_pair(store, run_id, client, limiter, budget, state, page_size)
            except BaseException as error:
                interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
                state.stop("interrupted" if interrupted else "unexpected_error",
                           f"{type(error).__name__}: {str(error)[:160]}")
                raise
            finally:
                pairs[state.symbol] = report_documents.pair_document(store, state)
            paused = paused or state.stop_reason == "rate_limit_wait_too_long"
    except BaseException as error:
        reason = "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "unexpected_error"
        for state in states:
            if state.symbol not in pairs:
                state.not_started = True
                state.stop(reason)
                pairs[state.symbol] = report_documents.pair_document(store, state)
        report.update(reason=reason, message=f"{type(error).__name__}: {str(error)[:160]}")
        _finish(store, clock, run_id, report, limiter, budget, "failed")
        raise
    status = _finish(store, clock, run_id, report, limiter, budget, None)
    return BackfillResult(run_id=run_id, status=status, report=report)


def _finish(store: CandleStore, clock: Clock, run_id: int, report: dict[str, Any], limiter: RequestLimiter,
            budget: Budget, forced: str | None) -> str:
    pairs = report["pairs"]
    run_totals = report_documents.totals(pairs, budget)
    status = forced or report_documents.run_status(pairs, run_totals)
    report.update(
        status=status, finished_at_utc=utc_iso(clock.now()), totals=run_totals,
        not_reached=report_documents.not_reached(pairs),
        requests={"max": budget.maximum, "made": budget.used, "rate_limited_responses": budget.rate_limited,
                  "throttled_responses": budget.throttled, "transport_errors": budget.transport_errors,
                  "penalties": limiter.penalties, "waited_seconds": str(limiter.waited_seconds)})
    try:
        store.finish_run(run_id, finished_at=clock.now(), status=status, report=report)
    except Exception:
        if forced is None:
            raise
    return status


__all__ = [
    "BACKFILL_EVIDENCE_LABELS", "BACKFILL_INTERVALS", "BACKFILL_VERSION", "MAX_PAGE_RETRIES", "MAX_RANGES_LISTED",
    "MAX_REQUESTS_LIMIT", "MAX_TRANSPORT_RETRIES", "NOTES", "STOP_REASONS", "TRADES_SOURCE", "BackfillResult",
    "BackfillScope", "ResumePoint", "TradesClient", "backfill_trades", "read_resume_point", "read_resume_points",
]
