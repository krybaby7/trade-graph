"""The request loop of a candle collection run: one request per planned series, saved one series at a time.

Rules (docs/KRAKEN-CANDLE-COLLECTION.md):

- every request takes a rate-limiter slot first and counts against the run's request limit;
- each series is saved in its own transaction, so a failure never discards another series;
- a rate-limit error stops the run; a short ``Throttled`` error is waited out once per run and the request is
  repeated once; ten failures in a row stop the run (a dead network must not cost thousands of timeouts);
- the series that were not reached are exactly the plan after the last one attempted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from trade_graph.adapters.market.kraken_candles import (
    KrakenCandleFetcher,
    OhlcFetch,
    OhlcFetchError,
    exception_class_name,
)
from trade_graph.adapters.market.public import TextTransport
from trade_graph.adapters.market.rate_limit import KrakenRestLimiter
from trade_graph.application.candle_collection_plan import PlannedSeries
from trade_graph.application.candle_store import CandleStore
from trade_graph.contracts.candles import Candle, CandleSource, SaveResult, from_unix, to_unix
from trade_graph.domain.clock import Clock, utc_iso

MAXIMUM_CONSECUTIVE_FAILURES = 10
# A throttle is waited out only when Kraken's stated end is at most this far ahead of the host clock, plus a
# margin. A longer or unreadable one stops the run: the caller can start another run later.
MAXIMUM_THROTTLE_WAIT_SECONDS = 30
THROTTLE_MARGIN_SECONDS = 2
MAXIMUM_RANGES_REPORTED = 20
GAP_ACTION = ("Fill this gap with the Trades backfill or the archive import (T26). The OHLC endpoint returns only "
              "the 720 most recent candles, so candles older than the response cannot be fetched from it.")


def _iso(unix_seconds: int | None) -> str | None:
    return None if unix_seconds is None else utc_iso(from_unix(unix_seconds))


@dataclass
class RunOutcome:
    entries: list[dict] = field(default_factory=list)
    stop: dict | None = None
    requests_made: int = 0
    throttle_retries: int = 0


class CollectionRun:
    def __init__(self, store: CandleStore, clock: Clock, transport: TextTransport, limiter: KrakenRestLimiter,
                 plan: tuple[PlannedSeries, ...], *, max_requests: int, source: CandleSource, run_id: int) -> None:
        self.store = store
        self.clock = clock
        self.limiter = limiter
        self.plan = plan
        self.max_requests = max_requests
        self.source = source
        self.run_id = run_id
        self.fetcher = KrakenCandleFetcher(transport, clock, source=source)
        self.outcome = RunOutcome()
        self._consecutive_failures = 0

    def execute(self) -> RunOutcome:
        for item in self.plan:
            if self.outcome.requests_made >= self.max_requests:
                self.outcome.stop = {"reason": "max_requests"}
                break
            self.outcome.entries.append(self._series(item))
            if self.outcome.stop is not None:
                break
        if self.outcome.stop is not None:
            self.outcome.stop["requests_made"] = self.outcome.requests_made
        return self.outcome

    # --- one series ---------------------------------------------------------------------------------------------

    def _series(self, item: PlannedSeries) -> dict:
        fetched, failure = self._request(item)
        if fetched is not None:
            try:
                saved = (self.store.save(list(fetched.candles), run_id=self.run_id) if fetched.candles
                         else SaveResult(inserted=0, revised=0, duplicates=0))
            except Exception as error:
                failure = OhlcFetchError("persistence_failed", internal_class=exception_class_name(error))
            else:
                self._consecutive_failures = 0
                return self._succeeded(item, fetched, saved)
        assert failure is not None
        self._failed(failure)
        return self._failed_entry(item, failure)

    def _request(self, item: PlannedSeries) -> tuple[OhlcFetch | None, OhlcFetchError | None]:
        while True:
            self.limiter.acquire()
            self.outcome.requests_made += 1
            try:
                return self.fetcher.fetch(item.record, item.interval_minutes, since=item.since), None
            except OhlcFetchError as error:
                wait = self._throttle_wait(error)
                if wait is None or self.outcome.requests_made >= self.max_requests:
                    return None, error
                self.outcome.throttle_retries += 1
                self.limiter.pause(wait)
            except Exception as error:
                return None, OhlcFetchError("request_failed", internal_class=exception_class_name(error))

    def _throttle_wait(self, error: OhlcFetchError) -> float | None:
        if error.category != "throttled" or self.outcome.throttle_retries >= 1 or error.retry_at_unix is None:
            return None
        remaining = error.retry_at_unix - to_unix(self._now().replace(microsecond=0))
        if remaining > MAXIMUM_THROTTLE_WAIT_SECONDS:
            return None
        return float(max(0, remaining) + THROTTLE_MARGIN_SECONDS)

    def _failed(self, failure: OhlcFetchError) -> None:
        if failure.category == "rate_limited":
            self.outcome.stop = {"reason": "rate_limited"}
        elif failure.category == "throttled":
            self.outcome.stop = {
                "reason": "throttled", "retry_at_unix": failure.retry_at_unix,
                "retry_at_utc": _iso(failure.retry_at_unix),
            }
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAXIMUM_CONSECUTIVE_FAILURES:
                self.outcome.stop = {
                    "reason": "consecutive_failures", "consecutive_failures": self._consecutive_failures,
                }

    def _now(self) -> datetime:
        return self.clock.now().astimezone(UTC)

    # --- entries ------------------------------------------------------------------------------------------------

    @staticmethod
    def _base(item: PlannedSeries) -> dict:
        return {
            "symbol": item.record.symbol, "source_pair": item.record.source_pair,
            "interval_minutes": item.interval_minutes, "since": item.since, "since_utc": _iso(item.since),
        }

    def _failed_entry(self, item: PlannedSeries, failure: OhlcFetchError) -> dict:
        return {
            **self._base(item), "status": "failed", "error": failure.category,
            "exchange_error_class": failure.error_class, "internal_error_class": failure.internal_class,
            "received_at_utc": None, "rows_received": 0,
            "candles_returned": 0, "inserted": 0, "duplicates": 0, "revised": 0, "no_trade_candles": 0,
            "first_open_utc": None, "last_open_utc": None, "continuity": None, "coverage": None,
        }

    def _succeeded(self, item: PlannedSeries, fetched: OhlcFetch, saved: SaveResult) -> dict:
        candles = fetched.candles
        return {
            **self._base(item), "status": "ok", "error": None, "exchange_error_class": None,
            "internal_error_class": None,
            "received_at_utc": utc_iso(fetched.received_at), "rows_received": fetched.rows_received,
            "candles_returned": len(candles), "inserted": saved.inserted, "duplicates": saved.duplicates,
            "revised": saved.revised, "no_trade_candles": fetched.no_trade_candles,
            "first_open_utc": _iso(candles[0].open_unix) if candles else None,
            "last_open_utc": _iso(candles[-1].open_unix) if candles else None,
            "continuity": continuity(item, candles),
            "coverage": self._coverage(item, candles),
        }

    def _coverage(self, item: PlannedSeries, candles: tuple[Candle, ...]) -> dict | None:
        """The store's own gap list for the window this response covers, back to the newest candle known before."""
        if not candles:
            return None
        step = item.interval_minutes * 60
        first, last = candles[0].open_unix, candles[-1].open_unix
        start = first if item.newest_stored_open is None else min(first, item.newest_stored_open)
        report = self.store.coverage(
            item.record.symbol, item.interval_minutes, self.source, start_open=from_unix(start),
            end_open=from_unix(last + step), as_of=self._now())
        document = report.to_document()
        ranges = document["missing_ranges"]
        return {
            "window_start_open_utc": document["window_start_open_utc"],
            "window_end_open_utc": document["window_end_open_utc"],
            "expected_slots": document["expected_slots"], "present_slots": document["present_slots"],
            "missing_slots": document["missing_slots"],
            "missing_ranges": ranges[:MAXIMUM_RANGES_REPORTED], "missing_ranges_total": len(ranges),
            "missing_ranges_truncated": len(ranges) > MAXIMUM_RANGES_REPORTED,
        }


def continuity(item: PlannedSeries, candles: tuple[Candle, ...]) -> dict:
    """Whether the first returned candle connects to what was stored before this request."""
    step = item.interval_minutes * 60
    newest = item.newest_stored_open
    first = candles[0].open_unix if candles else None
    result = {
        "status": "", "stored_newest_open_utc": _iso(newest), "first_returned_open_utc": _iso(first),
        "missing_slots": 0, "action": None,
    }
    if newest is None:
        result["status"] = "first_collection" if candles else "no_data"
    elif first is None:
        result["status"] = "unverified"
    elif first <= newest + step:  # the very next slot leaves nothing between the two candles
        result["status"] = "connected"
    else:
        result["status"] = "gap"
        result["missing_slots"] = (first - newest) // step - 1
        result["action"] = GAP_ACTION
    return result
