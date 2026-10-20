"""Normal protected local paper graph with subscription billing and durable schedules.

Admission controls credentials and capabilities, not a diagnostic experiment.
The ordinary service continues deterministic paper management when AI is absent;
normal operation has no first-cycle prerequisite or mandatory terminal pause.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, timedelta
from pathlib import Path

from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.alarms.watcher import PriceWatcher
from trade_graph.application.candle_readiness import (
    DEFAULT_DECISION_CANDLE_WAIT_SECONDS,
    MAX_DECISION_CANDLE_WAIT_SECONDS,
    candle_wait,
)
from trade_graph.application.collect_price_history import collect_public_hourly_history
from trade_graph.application.events import EngineOptions
from trade_graph.application.news_installation import installed_news_poll
from trade_graph.application.owner_commands import recover_owner_commands
from trade_graph.application.paper_service import PaperService
from trade_graph.application.perplexity_installation import perplexity_departments
from trade_graph.application.scheduler import TaskLease
from trade_graph.application.service_market_data import market_database_beside
from trade_graph.application.strategy_lab.installation import installed_strategy_lab
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError, ValidationFailure
from trade_graph.kernel.deployment_image import assert_boot_environment, read_owner_file
from trade_graph.paper_runtime import PaperRuntimeConfig, assemble_paper_runtime

_PREVIOUS_TASK_PAUSES = {
    ("owner", "Owner requested first-paper-cycle stop; subscription login pending. "
              "Manage existing paper orders and positions; no new decisions."),
    ("system", "Bounded subscription smoke finished; manage existing paper orders and positions."),
}


class _HistoryRefreshingFeed:
    """Refresh completed hours before new inputs; retry only unresolved symbols."""
    def __init__(self, feed, runtime, *, hours: int, interval_seconds: int):
        self.feed, self.runtime = feed, runtime
        self.hours, self.interval_seconds = hours, interval_seconds
        self.next_history = None
        self.last_history = None
        self._next_by_symbol = dict.fromkeys(feed.symbols)
        self._symbol_reports = {}

    def __getattr__(self, name):
        return getattr(self.feed, name)

    @property
    def history_symbols(self) -> tuple[str, ...]:
        return tuple(self._next_by_symbol)

    def history_due(self) -> bool:
        now = self.runtime.clock.now()
        return any(due is None or now >= due for due in self._next_by_symbol.values())

    def poll(self):
        requested = self.runtime.clock.now()
        symbols = tuple(symbol for symbol, due in self._next_by_symbol.items()
                        if due is None or requested >= due)
        if symbols:
            report = collect_public_hourly_history(self.runtime.database, self.runtime.clock,
                self.feed.transport, hours=self.hours, symbols=symbols)
            finished = self.runtime.clock.now()
            next_hour = finished.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            retry_symbols = []
            for symbol in symbols:
                outcome = report["symbols"].get(symbol, {})
                # A valid response may still lack its newly completed tail. Keep
                # that absence visible and retry promptly for late publication.
                tail_missing = (outcome.get("latest_closed_utc") is not None and
                                outcome["latest_closed_utc"] < utc_iso(next_hour - timedelta(hours=1)))
                retry = outcome.get("status") != "ok" or tail_missing or outcome.get("fetched") == 0
                if retry:
                    retry_symbols.append(symbol)
                delay = min(self.interval_seconds, 30) if retry else self.interval_seconds
                self._next_by_symbol[symbol] = min(next_hour, finished + timedelta(seconds=delay))
            self.next_history = min(self._next_by_symbol.values())
            self._symbol_reports.update(report["symbols"])
            outcomes = [outcome.get("status") for outcome in self._symbol_reports.values()]
            status = report["status"]
            if len(outcomes) == len(self._next_by_symbol) and all(outcomes):
                failures = outcomes.count("failed")
                status = "failed" if failures == len(outcomes) else "partial" if failures else "ok"
            self.last_history = {**report, "status": status, "symbols": dict(self._symbol_reports),
                "requested_symbols": list(symbols), "retry_symbols": retry_symbols,
                "refresh_requested_at_utc": utc_iso(requested), "refresh_finished_at_utc": utc_iso(finished),
                "next_refresh_at_utc": utc_iso(self.next_history)}
            self.runtime.ledger._activity(self.runtime.portfolio_id, "public_history_refresh", self.last_history)
        # Acquire quotes after history, so slow historical requests do not age
        # otherwise-current quote inputs before the service can consume them.
        return self.feed.poll()

    def close(self):
        return self.feed.close()


# Wakes whose turn waits, boundedly, for the hour that just closed, like the fixed timer's decisions did: the day's
# plans and the Trader's own check-ins. Event wakes (alarms, fills, exits, requests) never wait.
_WAITING_WAKES = frozenset({"wake:day_plan", "wake:check_in"})


class _NormalSubscriptionService(PaperService):
    def __init__(self, *args, candle_wait_seconds: int = DEFAULT_DECISION_CANDLE_WAIT_SECONDS,
                 candle_history=None, history_symbols: tuple[str, ...] = (), **kwargs):
        super().__init__(*args, **kwargs)
        if type(candle_wait_seconds) is not int or not 0 <= candle_wait_seconds <= MAX_DECISION_CANDLE_WAIT_SECONDS:
            raise ValidationFailure("decision candle wait must be 0 to 3000 whole seconds")
        self.candle_wait_seconds = candle_wait_seconds
        # D220: with the price collector the wait reads the candle store (the collector stores the hour in its own
        # thread); without it, the hourly refresh below stores it in hourly_candles before inputs are captured.
        self.candle_history, self.history_symbols = candle_history, tuple(history_symbols)

    def _input_wait(self, lease: TaskLease, row) -> tuple[int, dict] | None:
        # A fresh Trader decision waits, boundedly, for the hour that just closed.
        # Started or reclaimed tasks keep their pinned snapshot and recovery path.
        if row["role"] != "trader" or lease.reclaimed or row["attempts_used"] != 0:
            return None
        if row["objective"].startswith("wake:") and row["objective"] not in _WAITING_WAKES:
            return None  # an alarm, fill or exit wake is answered at once, with the hours that are stored (D223)
        if isinstance(self.public_feed, _HistoryRefreshingFeed):
            history, candidates = self.database, self.public_feed.history_symbols
        elif self.candle_history is not None:
            history, candidates = self.candle_history, self.history_symbols
        else:
            return None
        try:
            mandate = self.execution.authority.active_mandate(row["portfolio_id"])
        except (TradeGraphError, ValueError):
            return None  # Authority errors surface at decision time, never as a wait.
        symbols = [symbol for symbol in candidates if symbol in mandate.symbols]
        return candle_wait(history, symbols, self.clock.now(), wait_seconds=self.candle_wait_seconds,
                           previous_output_json=row["output_json"])

    async def _tick(self, *, wait_roles: bool, wait_feed: bool):
        # Finish due hourly history before schedules capture new role inputs.
        # _consume_feed keeps deterministic management running while it waits.
        history_due = (isinstance(self.public_feed, _HistoryRefreshingFeed) and self.public_feed.history_due())
        return await super()._tick(wait_roles=wait_roles,
                                   wait_feed=wait_feed or self._feed_failed or history_due)

    async def _prepare_role_inputs(self) -> tuple[int, tuple[str, ...]]:
        observations, failures = 0, []
        # A quote poll started before the hour may still be in flight. Drain it,
        # then make at most one new acquisition for the now-due completed hour.
        for _ in range(2):
            if (self._stop_requested.is_set() or not isinstance(self.public_feed, _HistoryRefreshingFeed)
                    or not self.public_feed.history_due()):
                break
            if self._feed_task is None:
                self._feed_task = asyncio.create_task(asyncio.to_thread(self._thread_call, self.public_feed.poll))
            maintenance_errors = []
            try:
                acquired, failure = await self._consume_feed(self._feed_task, maintenance_errors=maintenance_errors)
                observations += acquired
                if failure:
                    failures.append(failure)
            finally:
                self._feed_task = None
            if maintenance_errors:
                raise maintenance_errors[0]
        return observations, tuple(failures)


def _attempts(database) -> int:
    has_attempts = database.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                    "AND name='subscription_attempts'").fetchone()
    if not has_attempts:
        return database.execute("SELECT COUNT(*) FROM subscription_invocations "
                                "WHERE cost_status != 'not_incurred'").fetchone()[0]
    return database.execute("SELECT COUNT(*) FROM subscription_attempts").fetchone()[0] + database.execute(
        "SELECT COUNT(*) FROM subscription_invocations i WHERE i.cost_status != 'not_incurred' "
        "AND NOT EXISTS (SELECT 1 FROM subscription_attempts a WHERE a.invocation_id=i.invocation_id)").fetchone()[0]


def _resume_previous_task_pause(runtime) -> bool:
    pause = runtime.execution.pause(runtime.portfolio_id)
    if (pause and pause["profile"] == "MANAGE_ONLY"
            and (pause["originator"], pause["reason"]) in _PREVIOUS_TASK_PAUSES):
        runtime.execution.set_pause(runtime.portfolio_id, "RUNNING", "owner",
            "Owner authorized normal local paper operation with existing subscription allowance.")
        return True
    return False


def event_engine_options(config: PaperRuntimeConfig, runtime) -> EngineOptions | None:
    """The owner's ``event_engine`` switch (D223): wakes replace the fixed timers. The price watcher reads the
    collector's candle file read-only; the day start uses the collector's newest hourly scan (no request of its own)."""
    if not config.event_engine:
        return None
    market = getattr(runtime, "market_data", None)
    if market is None:
        raise ValidationFailure("the event engine needs the price collector's candle file")
    watcher = PriceWatcher(AlarmBook(runtime.database, runtime.clock), market.candles, runtime.clock)
    return EngineOptions(scan=None, watcher=watcher, daily_cycle=True)


def run_subscription_operation(database_path: Path, protected_owner: Path, *,
                               maximum_ticks: int | None = None, portfolio_id: str | None = None,
                               service_run_id: str | None = None) -> dict:
    """Run until the owner stops the service; an optional tick limit is operational.

    This never creates a portfolio, grants paid/live authority or changes mandate
    settings. Stronger and unrelated owner/system pauses remain authoritative.
    The candle file lives beside the database (D224); the price collector keeps it
    current when public data is on (D222), and the event engine runs only when the
    owner's protected configuration says ``event_engine: true`` (D223).
    """
    if maximum_ticks is not None and (type(maximum_ticks) is not int or maximum_ticks < 1):
        raise ValueError("maximum_ticks must be a positive integer or None")
    assert_boot_environment()
    config = PaperRuntimeConfig.model_validate_json(read_owner_file(protected_owner, "paper-config.json", 262144))
    if config.models is not None or config.price_cards:
        raise PermissionError("subscription paper operation refuses separately billed API configuration")
    runtime = assemble_paper_runtime(database_path, portfolio_id=portfolio_id, config=config,
                                     protected_owner=protected_owner, api_keys={},
                                     market_database=market_database_beside(database_path))
    market = getattr(runtime, "market_data", None)
    lab: dict = {}
    try:
        before_attempts = _attempts(runtime.database)
        before_decisions = runtime.database.execute("SELECT COUNT(*) FROM decisions WHERE portfolio_id=?",
                                                    (runtime.portfolio_id,)).fetchone()[0]
        resumed = False
        def prepare():
            nonlocal resumed
            handlers = runtime.prepare_runtime() if runtime.prepare_runtime else runtime.handlers
            if handlers and (runtime.runtime_ready is None or runtime.runtime_ready()):
                resumed = _resume_previous_task_pause(runtime)
            # W9-A (D255): the 5-minute news check runs beside the service in its own thread when the owner's network
            # profile lists the reviewed news_feeds route; Jev checks only with the owner's switch and key.
            service.news_poll = installed_news_poll(runtime, protected_owner)
            # W11-B (D292): the Trading desk's strategy lab runs queued requests in its own thread, reading the candle
            # file through its own read-only handle; it needs no key or route and never places an order.
            service.lab_worker = lab["worker"] = installed_strategy_lab(runtime)
            # W8-A (D240): with the event engine on and the owner's Perplexity switch on, Research's and the Leader's
            # engine wakes run on Perplexity in this protected process; otherwise the handlers are unchanged.
            return perplexity_departments(runtime, protected_owner, handlers, engine=service.event_engine)
        feed = runtime.public_feed
        if feed is not None and config.public_history_enabled and market is None:
            # Without the price collector the hourly BTC/ETH refresh into hourly_candles goes on as before.
            feed = _HistoryRefreshingFeed(feed, runtime, hours=config.public_history_hours,
                                          interval_seconds=config.public_history_interval_seconds)
        collector = getattr(runtime, "price_collector", None)
        service = _NormalSubscriptionService(runtime.database, runtime.execution, clock=runtime.clock,
            portfolio_ids=[runtime.portfolio_id], handlers={}, artifact_runtime=runtime.artifact_runtime,
            public_feed=feed, secretary=runtime.secretary, schedule_intervals=config.schedule_intervals,
            automatic_schedule=config.automatic_schedule, optimisation_manual_only=config.optimisation_manual_only,
            tick_interval_seconds=config.tick_interval_seconds,
            candle_wait_seconds=config.decision_candle_wait_seconds,
            candle_history=market.history if market is not None else None,
            history_symbols=tuple(config.paper_symbols), price_collector=collector,
            event_engine=event_engine_options(config, runtime),
            recover_commands=lambda: recover_owner_commands(runtime), prepare_runtime=prepare,
            runtime_ready=runtime.runtime_ready,
            subscription_provider=getattr(runtime, "subscription_provider", None), service_run_id=service_run_id)
        result = asyncio.run(service.run(max_ticks=maximum_ticks))
        ready = bool(runtime.handlers) and (runtime.runtime_ready is None or runtime.runtime_ready())
        admission = getattr(runtime, "subscription_admission", None)
        return {**result, "status": "paper_degraded" if result["failures"] else "paper_stopped",
            "mode": "paper", "orders_hosted_by": "trade_graph_local_simulator",
            "ai_available": ready, "inference_attempts": _attempts(runtime.database) - before_attempts,
            "decisions_created": runtime.database.execute("SELECT COUNT(*) FROM decisions WHERE portfolio_id=?",
                (runtime.portfolio_id,)).fetchone()[0] - before_decisions,
            "position_management": runtime.execution.profile(runtime.portfolio_id),
            "previous_task_pause_resumed": resumed, "automatic_schedule": config.automatic_schedule,
            "automatic_optimisation": config.automatic_schedule and not config.optimisation_manual_only,
            "blockers": list(admission.status.get("blockers", [])) if admission else [],
            "public_history": feed.last_history if isinstance(feed, _HistoryRefreshingFeed) else None,
            "price_collection": None if collector is None else {
                "market_database": market.path.name, "public_requests": collector.requests_total},
            "event_engine": service.event_engine is not None,
            "news_polls": None if service.news_poll is None else service.news_poll.polls,
            "strategy_lab_requests": None if service.lab_worker is None else service.lab_worker.ran,
            "billing_kind": "subscription", "actual_cost_native": None, "cost_status": "unknown",
            "live_authorization": False, "paid_authorization": False}
    finally:
        if lab.get("worker") is not None:
            lab["worker"].close()
        if market is not None:
            market.close()
        runtime.database.close()
