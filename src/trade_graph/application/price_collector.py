"""The price collector inside the service (W6-D, D222): one collector keeps the candle store current.

It replaces the two Windows scheduled tasks of T25 (``scripts/register_candle_schedule.ps1``: 1-minute BTC/ETH every
6 hours, and 5, 15 and 60 minutes for every eligible pair once a day) and runs W2-B's coin scan every hour, so the
day start, the price watcher's candle and indicator alarms, the hourly features and HQ's charts read fresh candles.
Public Kraken data only: no key, no order, no paid call. It reuses T25's request loop (``CollectionRun``: one OHLC
request per series, each series saved on its own, the same stop rules) and the T25 pair list refresh.

What it asks for, most urgent first. Each request is one series (a coin at one candle size) and goes through the one
limiter of the process (D221):

1. **The coins the team works with now** (``HotCoins``): BTC/USD and ETH/USD, the quote feed's coins, the newest
   shortlist, today's focus coins, held coins and coins with an armed alarm or an active day plan, at 5, 15 and 60
   minutes, and at 1 minute for BTC/ETH, focus coins and coins with a 1-minute alarm. A series is due once a newer
   candle has closed and settled (``settle_seconds``, ``hourly_settle_seconds``: Kraken commits an hour 2.6 to 3.3
   minutes after it closed, D129); a 1-minute series at most every ``one_minute_every_seconds``. An answer that
   brought nothing newer is asked again after ``retry_seconds``.
2. **The ranking pass**: the 60-minute series of every eligible pair, once per hour after the hour settled
   (``ranking_every_hours``), because the hourly scan ranks every pair from its hourly candles (D170: at most two
   hours behind). Then **the hourly scan**: one Ticker request, ``CoinScanner.scan`` and ``CoinScanStore.record``;
   it runs when the pass is done, or ``scan_after_minutes`` into the hour with whatever is stored.
3. **The wide pass**: 5 and 15 minutes for every eligible pair, once every ``wide_every_hours`` (the practice range's
   history).

The pair list is refreshed every ``catalog_every_hours`` (and first, when none is stored). One ``step`` does at most
``chunk_requests`` requests and records them as one candle run with a compact report, so newly due work for the hot
coins is never stuck behind a long pass. A rate-limit answer stops the chunk, assumes Kraken's counter is full and
pauses further requests until it has drained (``KrakenRestLimiter.penalize``). A stop request interrupts the
limiter's waiting at once; the chunk's run is still recorded. Nothing here limits how often a department is woken
(D116): these are cadences of public data collection.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from trade_graph.adapters.market.kraken_candles import OHLC_URL
from trade_graph.adapters.market.kraken_ticker import KrakenTickerFetcher, TickerFetchError
from trade_graph.adapters.market.public import TextTransport
from trade_graph.adapters.market.rate_limit import KrakenRestLimiter, LimitedTransport, RateLimitWaitTooLong
from trade_graph.application.candle_collection_plan import DEFAULT_ONE_MINUTE_SYMBOLS, PlannedSeries
from trade_graph.application.candle_collection_run import CollectionRun
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.collect_candles import CATALOG_RATE_LIMIT_STOPS, latest_recorded_time, refresh_catalog
from trade_graph.contracts.candles import CandleSource, align_close_time, from_unix
from trade_graph.contracts.pair_catalog import PairRecord
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

COLLECTOR_VERSION = "service-collector-v1"
HOT_INTERVALS = (5, 15, 60)
WIDE_INTERVALS = (5, 15)
MAXIMUM_REPORTED_ENTRIES = 50
JobKind = Literal["hot", "ranking", "wide"]


class CollectorStopped(Exception):
    """The service is stopping: the limiter's wait was interrupted before the next request."""


class InterruptibleSleeper:
    """The limiter's sleeper in the service: it waits on the stop signal, so a stop never waits out a pause."""

    def __init__(self, stop: threading.Event) -> None:
        self.stop = stop

    def __call__(self, seconds: float) -> None:
        if self.stop.wait(max(0.0, seconds)):
            raise CollectorStopped("the service is stopping")


@dataclass(frozen=True, slots=True)
class CollectorSettings:
    """How the service collector paces public collection (defaults: D222). Cadences, not caps on anyone's work."""

    settle_seconds: int = 30
    hourly_settle_seconds: int = 180
    retry_seconds: int = 60
    one_minute_every_seconds: int = 300
    ranking_every_hours: int = 1
    wide_every_hours: int = 24
    catalog_every_hours: int = 24
    hourly_scan: bool = True
    scan_after_minutes: int = 50
    chunk_requests: int = 30
    idle_check_seconds: int = 15
    rate_limit_pause_seconds: int = 60
    one_minute_symbols: tuple[str, ...] = DEFAULT_ONE_MINUTE_SYMBOLS

    def __post_init__(self) -> None:
        bounds = {"settle_seconds": (0, 600), "hourly_settle_seconds": (0, 1800), "retry_seconds": (5, 3600),
                  "one_minute_every_seconds": (60, 43_200), "ranking_every_hours": (1, 24),
                  "wide_every_hours": (1, 60), "catalog_every_hours": (1, 168), "scan_after_minutes": (5, 59),
                  "chunk_requests": (1, 500), "idle_check_seconds": (1, 600), "rate_limit_pause_seconds": (0, 3600)}
        for name, (low, high) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValidationFailure(f"{name} must be a whole number from {low} to {high}")
        if type(self.hourly_scan) is not bool:
            raise ValidationFailure("hourly_scan must be true or false")
        if (not isinstance(self.one_minute_symbols, tuple)
                or any(not isinstance(item, str) or not item.endswith("/USD") for item in self.one_minute_symbols)):
            raise ValidationFailure("one_minute_symbols must be BASE/USD symbols")


@dataclass(frozen=True, slots=True)
class HotCoins:
    """The coins the team works with now: ``symbols`` at 5, 15 and 60 minutes, ``one_minute`` also at 1 minute."""

    symbols: frozenset[str] = frozenset()
    one_minute: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CollectorTransports:
    """The pair-list transport (large answers), the OHLC transport and the Ticker transport (all pairs, large)."""

    catalog: TextTransport
    ohlc: TextTransport
    ticker: TextTransport


@dataclass(frozen=True, slots=True)
class StepReport:
    kind: Literal["idle", "catalog", "candles", "scan", "paused", "stopped"]
    requests: int = 0
    status: str | None = None
    run_id: int | None = None
    scan_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _Wanted:
    record: PairRecord
    interval: int
    kind: JobKind
    newest_open: int | None


def newest_opens(store: CandleStore, source: CandleSource) -> dict[tuple[str, int], int]:
    """The newest stored open time of every series of ``source`` (one index lookup per series; no row count)."""
    rows = store.database.query(
        "SELECT s.symbol, s.interval_minutes, (SELECT MAX(m.open_time) FROM market_candles m "
        "WHERE m.series_id = s.series_id) AS newest FROM candle_series s WHERE s.source = ?", (source,))
    return {(row["symbol"], row["interval_minutes"]): row["newest"] for row in rows if row["newest"] is not None}


def _aware(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationFailure("the price collector requires an aware clock")
    return now.astimezone(UTC)


def _compact(entry: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("symbol", "interval_minutes", "status", "error", "exchange_error_class", "internal_error_class",
            "inserted", "revised", "duplicates", "last_open_utc")
    document = {key: entry.get(key) for key in keep}
    continuity = entry.get("continuity") or {}
    document["continuity"] = continuity.get("status")
    if continuity.get("status") == "gap":
        document["missing_slots"] = continuity.get("missing_slots")
        document["action"] = continuity.get("action")
    return document


class PriceCollector:
    """One collector for the service process: call ``step`` from its own thread; it never touches the books."""

    def __init__(self, store: CandleStore, clock: Clock, transports: CollectorTransports,
                 limiter: KrakenRestLimiter, *, coins: Callable[[], HotCoins],
                 settings: CollectorSettings | None = None,
                 scan: Callable[[datetime, Any, str | None], Any] | None = None,
                 last_scan_at: Callable[[], datetime | None] | None = None,
                 record: Callable[[dict[str, Any]], None] | None = None,
                 stop: threading.Event | None = None, evidence_label: str = "live_public") -> None:
        if evidence_label not in ("live_public", "synthetic_fixture", "recorded_public_fixture"):
            raise ValidationFailure("unsupported evidence label for the service collector")
        self.store = store
        self.clock = clock
        self.transports = transports
        self.limiter = limiter
        self.coins = coins
        self.settings = settings or CollectorSettings()
        self.scan = scan
        self.last_scan_at = last_scan_at
        self.record = record
        self.stop = stop or threading.Event()
        self.evidence_label = evidence_label
        self.source: CandleSource = "synthetic" if evidence_label == "synthetic_fixture" else "kraken_public_ohlc"
        self._asked: dict[tuple[str, int], datetime] = {}
        self._retry_at: dict[tuple[str, int], datetime] = {}
        self._ranked: dict[str, datetime] = {}
        self._wide_asked: dict[tuple[str, int], datetime] = {}
        self._catalog_asked: datetime | None = None
        self._catalog_cache: tuple[int, tuple[PairRecord, ...]] | None = None
        self._catalog_observed: datetime | None = None
        self._pass: dict[str, Any] = {}
        self._scanned_hour: datetime | None = None
        self._paused_until: datetime | None = None
        self._next_look: datetime | None = None
        self._latest_recorded: datetime | None = None
        self._last_status: str | None = None
        self.requests_total = 0

    # --- control ----------------------------------------------------------------------------------------------

    def request_stop(self) -> None:
        self.stop.set()

    def resume(self) -> None:
        """A service that starts again after a stop clears the signal."""
        self.stop.clear()

    # --- one step ---------------------------------------------------------------------------------------------

    def step(self) -> StepReport:
        """Do the most urgent due work (at most one chunk) and return what happened; ``idle`` when nothing was due."""
        if self.stop.is_set():
            return StepReport("stopped")
        now = _aware(self.clock)
        if self._paused_until is not None and now < self._paused_until:
            return StepReport("paused", detail={"until_utc": utc_iso(self._paused_until)})
        idle_for = timedelta(seconds=self.settings.idle_check_seconds)
        if self._next_look is not None and now < self._next_look <= now + idle_for:
            return StepReport("idle")  # (a clock that stepped back is looked at again at once, by the guard below)
        try:
            report = self._step(now)
        except CollectorStopped:
            return StepReport("stopped")
        if report.kind == "idle":
            self._next_look = now + timedelta(seconds=self.settings.idle_check_seconds)
        else:
            self._next_look = None
        return report

    def _step(self, now: datetime) -> StepReport:
        if self._latest_recorded is None:
            self._latest_recorded = latest_recorded_time(self.store, self.source) or datetime.min.replace(tzinfo=UTC)
        if now < self._latest_recorded:
            # The guard of collect-kraken-candles: a clock behind a recorded run would date receipts backwards and
            # could hide the pair list. Nothing is asked until the clock has passed that run again.
            self._paused_until = self._latest_recorded
            self._note({"job": "candles", "status": "failed", "error": "clock_regression",
                        "latest_recorded_utc": utc_iso(self._latest_recorded)}, always=True)
            return StepReport("paused", status="failed", detail={"error": "clock_regression"})
        records = self._catalog(now)
        if records is None or self._catalog_due(now):
            if self._catalog_asked is None or now - self._catalog_asked >= timedelta(minutes=15):
                return self._refresh_catalog(now)
            if records is None:
                return StepReport("idle")
        wanted = self._wanted(now, records)
        # The hourly scan comes as soon as the ranking pass of its hour is done (or late in the hour with what is
        # stored); it never waits for the wide pass. Due work for the hot coins still goes first.
        ranking_left = any(item.kind == "ranking" for item in wanted)
        late = now >= self._ranking_hour(now) + timedelta(minutes=self.settings.scan_after_minutes)
        if (not ranking_left or late) and self._scan_due(now):
            hot = [item for item in wanted if item.kind == "hot"]
            if hot:
                return self._collect(now, hot[:self.settings.chunk_requests], len(wanted))
            return self._scan(now)
        if wanted:
            return self._collect(now, wanted[:self.settings.chunk_requests], len(wanted))
        return StepReport("idle")

    # --- the pair list ----------------------------------------------------------------------------------------

    def _catalog(self, now: datetime) -> tuple[PairRecord, ...] | None:
        observation = self.store.latest_catalog(as_of=now)
        if observation is None:
            return None
        if self._catalog_cache is None or self._catalog_cache[0] != observation.observation_id:
            records = tuple(PairRecord.model_validate(pair) for pair in observation.pairs)
            self._catalog_cache = (observation.observation_id, records)
            self._catalog_observed = observation.observed_at_utc
        return self._catalog_cache[1]

    def _catalog_due(self, now: datetime) -> bool:
        observed = self._catalog_observed
        return observed is None or now - observed >= timedelta(hours=self.settings.catalog_every_hours)

    def _refresh_catalog(self, now: datetime) -> StepReport:
        self._catalog_asked = now
        report = refresh_catalog(self.store, self.clock, LimitedTransport(self.transports.catalog, self.limiter),
                                 self.evidence_label)
        self._latest_recorded = max(self._latest_recorded or now, _aware(self.clock))
        self.requests_total += 1
        if CATALOG_RATE_LIMIT_STOPS.get(report.get("error")):
            self._rate_limited(now)
        self._note({"job": "catalog", "status": report["status"], "error": report.get("error"),
                    "usd_pairs": report.get("usd_pairs"), "requests": 1}, always=True)
        return StepReport("catalog", requests=1, status=report["status"], detail={"error": report.get("error")})

    # --- what is due ------------------------------------------------------------------------------------------

    def _settle(self, interval: int) -> timedelta:
        settings = self.settings
        return timedelta(seconds=settings.hourly_settle_seconds if interval == 60 else settings.settle_seconds)

    def _hot_due(self, key: tuple[str, int], newest_open: int | None, now: datetime) -> bool:
        symbol, interval = key
        settled = align_close_time(now - self._settle(interval), interval)
        if newest_open is not None and from_unix(newest_open + interval * 60) >= settled:
            return False
        retry = self._retry_at.get(key)
        if retry is not None and now < retry:
            return False
        asked = self._asked.get(key)
        return not (interval == 1 and asked is not None
                    and now - asked < timedelta(seconds=self.settings.one_minute_every_seconds))

    def _ranking_hour(self, now: datetime) -> datetime:
        return align_close_time(now - timedelta(seconds=self.settings.hourly_settle_seconds), 60)

    def _wanted(self, now: datetime, records: Iterable[PairRecord]) -> list[_Wanted]:
        eligible = {record.symbol: record for record in records if record.usd_quoted and record.eligible_for_collection}
        newest = newest_opens(self.store, self.source)
        hot = self.coins()
        chosen: dict[tuple[str, int], _Wanted] = {}
        one_minute = set(hot.one_minute) | set(self.settings.one_minute_symbols)
        for symbol in sorted(set(hot.symbols) | one_minute):
            record = eligible.get(symbol)
            if record is None:
                continue
            for interval in ((1, *HOT_INTERVALS) if symbol in one_minute else HOT_INTERVALS):
                key = (symbol, interval)
                if self._hot_due(key, newest.get(key), now):
                    chosen[key] = _Wanted(record, interval, "hot", newest.get(key))
        hour = self._ranking_hour(now)
        oldest_wanted = hour - timedelta(hours=self.settings.ranking_every_hours - 1)
        ranking = []
        for symbol, record in eligible.items():
            key = (symbol, 60)
            stored = newest.get(key)
            if key in chosen or self._ranked.get(symbol) == hour:
                continue
            if stored is None or from_unix(stored + 3600) < oldest_wanted:
                ranking.append(_Wanted(record, 60, "ranking", stored))
        wide = []
        for symbol, record in eligible.items():
            for interval in WIDE_INTERVALS:
                key = (symbol, interval)
                stored = newest.get(key)
                asked = self._wide_asked.get(key)
                if key in chosen or (asked is not None and now - asked < timedelta(hours=1)):
                    continue
                if stored is None or now - from_unix(stored + interval * 60) >= timedelta(
                        hours=self.settings.wide_every_hours):
                    wide.append(_Wanted(record, interval, "wide", stored))
        oldest_first = lambda item: (item.newest_open is not None, item.newest_open or 0, item.record.symbol)  # noqa: E731
        hot_items = sorted(chosen.values(), key=lambda item: (item.interval, *oldest_first(item)))
        return [*hot_items, *sorted(ranking, key=oldest_first),
                *sorted(wide, key=lambda item: (item.interval, *oldest_first(item)))]

    # --- one chunk of requests --------------------------------------------------------------------------------

    def _collect(self, now: datetime, chunk: list[_Wanted], due: int) -> StepReport:
        plan = tuple(PlannedSeries(item.record, item.interval, item.newest_open) for item in chunk)
        kinds = {(item.record.symbol, item.interval): item.kind for item in chunk}
        run_id = self.store.begin_run(
            kind="public_collect", source=self.source, evidence_label=self.evidence_label, started_at=now,
            parameters={"collector": COLLECTOR_VERSION, "endpoint": OHLC_URL, "due_series": due,
                        "series": [[item.record.symbol, item.interval, item.kind] for item in chunk],
                        "limiter": {"ceiling": self.limiter.ceiling,
                                    "assumed_decay_per_second": self.limiter.snapshot()["assumed_decay_per_second"]},
                        "paid_calls_enabled": False, "live_trading_enabled": False})
        run = CollectionRun(self.store, self.clock, self.transports.ohlc, self.limiter, plan,
                            max_requests=len(plan) + 1, source=self.source, run_id=run_id)
        stopped = False
        try:
            outcome = run.execute()
        except CollectorStopped:
            outcome, stopped = run.outcome, True
            outcome.stop = {"reason": "service_stopping", "requests_made": outcome.requests_made}
        finished = _aware(self.clock)
        self.requests_total += outcome.requests_made
        hour = self._ranking_hour(now)
        for entry in outcome.entries:
            key = (entry["symbol"], entry["interval_minutes"])
            kind = kinds[key]
            self._asked[key] = now
            if kind == "ranking":
                self._ranked[entry["symbol"]] = hour
            elif kind == "wide":
                self._wide_asked[key] = now
            newer = entry["status"] == "ok" and (entry["inserted"] or entry["revised"])
            if kind == "hot" and not newer:
                self._retry_at[key] = now + timedelta(seconds=self.settings.retry_seconds)
            else:
                self._retry_at.pop(key, None)
        ok = [entry for entry in outcome.entries if entry["status"] == "ok"]
        failed = [entry for entry in outcome.entries if entry["status"] != "ok"]
        gaps = [entry for entry in ok if (entry.get("continuity") or {}).get("status") == "gap"]
        stop = outcome.stop
        status = ("failed" if not ok else "partial" if failed or stop or len(outcome.entries) < len(plan) else "ok")
        by_kind: dict[str, dict[str, int]] = {}
        for item in chunk:
            counts = by_kind.setdefault(item.kind, {"planned": 0, "attempted": 0, "ok": 0})
            counts["planned"] += 1
        for entry in outcome.entries:
            counts = by_kind[kinds[(entry["symbol"], entry["interval_minutes"])]]
            counts["attempted"] += 1
            counts["ok"] += entry["status"] == "ok"
        report = {
            "collector": COLLECTOR_VERSION, "status": status, "run_id": run_id, "evidence_label": self.evidence_label,
            "source": self.source, "started_at_utc": utc_iso(now), "finished_at_utc": utc_iso(finished),
            "stop_reason": None if stop is None else stop["reason"], "stopped": stop,
            "due_series": due, "planned_series": len(plan), "by_kind": by_kind,
            "requests": {"made": outcome.requests_made, "throttle_retries": outcome.throttle_retries,
                         "limiter": self.limiter.snapshot()},
            "totals": {name: sum(entry[name] for entry in outcome.entries)
                       for name in ("inserted", "revised", "duplicates", "no_trade_candles")},
            "failed": [_compact(entry) for entry in failed[:MAXIMUM_REPORTED_ENTRIES]],
            "gaps": [_compact(entry) for entry in gaps[:MAXIMUM_REPORTED_ENTRIES]],
            "not_reached": [[item.record.symbol, item.interval] for item in chunk[len(outcome.entries):]],
            "paid_calls_enabled": False, "live_trading_enabled": False,
        }
        self.store.finish_run(run_id, finished_at=finished, status=status, report=report)
        self._latest_recorded = max(self._latest_recorded or finished, finished)
        if stop is not None and stop["reason"] in ("rate_limited", "throttled"):
            self._rate_limited(finished, retry_at_unix=stop.get("retry_at_unix"))
        ranking = by_kind.get("ranking")
        if ranking is not None:
            if self._pass.get("hour") != hour:
                self._pass = {"hour": hour, "attempted": 0, "ok": 0, "runs": 0}
            self._pass.update(attempted=self._pass["attempted"] + ranking["attempted"],
                              ok=self._pass["ok"] + ranking["ok"], runs=self._pass["runs"] + 1)
        self._note({"job": "candles", "status": status, "run_id": run_id, "requests": outcome.requests_made,
                    "inserted": report["totals"]["inserted"], "failed_series": len(failed), "gaps": len(gaps),
                    "stop_reason": report["stop_reason"], "by_kind": by_kind}, always=bool(gaps))
        if stopped:
            raise CollectorStopped("the service is stopping")
        return StepReport("candles", requests=outcome.requests_made, status=status, run_id=run_id,
                          detail={"by_kind": by_kind, "due": due})

    def _rate_limited(self, now: datetime, *, retry_at_unix: int | None = None) -> None:
        """Kraken said too many: assume its counter is full, wait it out, then hold off a little longer."""
        try:
            self.limiter.penalize(retry_at_unix)
        except RateLimitWaitTooLong:
            pass  # the longer pause below covers it; nothing is asked meanwhile
        self._paused_until = _aware(self.clock) + timedelta(seconds=self.settings.rate_limit_pause_seconds)

    # --- the hourly scan --------------------------------------------------------------------------------------

    def _scan_due(self, now: datetime) -> bool:
        if not self.settings.hourly_scan or self.scan is None:
            return False
        hour = self._ranking_hour(now)
        if self._scanned_hour == hour:
            return False
        previous = self.last_scan_at() if self.last_scan_at else None
        if previous is not None and previous >= hour:
            self._scanned_hour = hour
            return False
        return True

    def _scan(self, now: datetime) -> StepReport:
        hour = self._ranking_hour(now)
        ticker, error = None, None
        try:
            ticker = KrakenTickerFetcher(LimitedTransport(self.transports.ticker, self.limiter), self.clock).fetch()
        except TickerFetchError as failure:
            error = failure.category
        except CollectorStopped:
            raise
        except Exception:  # noqa: BLE001 - a scan without a ticker still records its reason
            error = "request_failed"
        self.requests_total += 1
        stored = self.scan(_aware(self.clock), ticker, error)
        self._scanned_hour = hour
        if error in ("rate_limited", "throttled"):
            self._rate_limited(now)
        scan_id = getattr(stored, "scan_id", None)
        status = getattr(stored, "status", None)
        ranking = self._pass if self._pass.get("hour") == hour else {"attempted": 0, "ok": 0, "runs": 0}
        self._note({"job": "scan", "status": status, "scan_id": scan_id, "ticker_error": error,
                    "hour_utc": utc_iso(hour), "requests": 1,
                    "ranking_pass": {name: ranking[name] for name in ("attempted", "ok", "runs")}}, always=True)
        return StepReport("scan", requests=1, status=status, scan_id=scan_id, detail={"ticker_error": error})

    # --- what HQ and the owner see ----------------------------------------------------------------------------

    def _note(self, event: dict[str, Any], *, always: bool) -> None:
        """Every catalog refresh, scan, finished ranking pass, gap and change of status is recorded; an ordinary ok
        chunk for the hot coins is not, so the activity log does not fill with routine."""
        status = event.get("status")
        changed = status != self._last_status
        self._last_status = status if event["job"] == "candles" else self._last_status
        if self.record is not None and (always or changed or status != "ok"):
            self.record({**event, "collector": COLLECTOR_VERSION})


__all__ = [
    "COLLECTOR_VERSION", "CollectorSettings", "CollectorStopped", "CollectorTransports", "HotCoins",
    "InterruptibleSleeper", "PriceCollector", "StepReport", "newest_opens",
]
