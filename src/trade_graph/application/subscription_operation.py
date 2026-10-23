"""Normal protected local paper graph with subscription billing and durable schedules.

Admission controls credentials and capabilities, not a diagnostic experiment.
The ordinary service continues deterministic paper management when AI is absent;
normal operation has no first-cycle prerequisite or mandatory terminal pause.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from trade_graph.application.collect_price_history import collect_public_hourly_history
from trade_graph.application.owner_commands import recover_owner_commands
from trade_graph.application.paper_service import PaperService
from trade_graph.kernel.deployment_image import assert_boot_environment, read_owner_file
from trade_graph.paper_runtime import PaperRuntimeConfig, assemble_paper_runtime

_PREVIOUS_TASK_PAUSES = {
    ("owner", "Owner requested first-paper-cycle stop; subscription login pending. "
              "Manage existing paper orders and positions; no new decisions."),
    ("system", "Bounded subscription smoke finished; manage existing paper orders and positions."),
}


class _HistoryRefreshingFeed:
    """Use the same public-only transport; retain sparse/failed history truthfully."""
    def __init__(self, feed, runtime, *, hours: int, interval_seconds: int):
        self.feed, self.runtime = feed, runtime
        self.hours, self.interval_seconds = hours, interval_seconds
        self.next_history = None
        self.last_history = None

    def __getattr__(self, name):
        return getattr(self.feed, name)

    def poll(self):
        now = self.runtime.clock.now()
        if self.next_history is None or now >= self.next_history:
            self.last_history = collect_public_hourly_history(self.runtime.database, self.runtime.clock,
                self.feed.transport, hours=self.hours, symbols=tuple(self.feed.symbols))
            self.runtime.ledger._activity(self.runtime.portfolio_id, "public_history_refresh", self.last_history)
            self.next_history = self.runtime.clock.now() + timedelta(seconds=self.interval_seconds)
        # Acquire quotes after history, so slow historical requests do not age
        # otherwise-current quote inputs before the service can consume them.
        return self.feed.poll()

    def close(self):
        return self.feed.close()


class _NormalSubscriptionService(PaperService):
    async def _tick(self, *, wait_roles: bool, wait_feed: bool):
        # Complete the first successful public acquisition before model work.
        # Later ticks keep the independent feed/management concurrency.
        return await super()._tick(wait_roles=wait_roles,
                                   wait_feed=wait_feed or self._feed_failed)


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


def run_subscription_operation(database_path: Path, protected_owner: Path, *,
                               maximum_ticks: int | None = None, portfolio_id: str | None = None,
                               service_run_id: str | None = None) -> dict:
    """Run until the owner stops the service; an optional tick limit is operational.

    This never creates a portfolio, grants paid/live authority or changes mandate
    settings. Stronger and unrelated owner/system pauses remain authoritative.
    """
    if maximum_ticks is not None and (type(maximum_ticks) is not int or maximum_ticks < 1):
        raise ValueError("maximum_ticks must be a positive integer or None")
    assert_boot_environment()
    config = PaperRuntimeConfig.model_validate_json(read_owner_file(protected_owner, "paper-config.json", 262144))
    if config.models is not None or config.price_cards:
        raise PermissionError("subscription paper operation refuses separately billed API configuration")
    runtime = assemble_paper_runtime(database_path, portfolio_id=portfolio_id, config=config,
                                     protected_owner=protected_owner, api_keys={})
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
            return handlers
        feed = runtime.public_feed
        if feed is not None and config.public_history_enabled:
            feed = _HistoryRefreshingFeed(feed, runtime, hours=config.public_history_hours,
                                          interval_seconds=config.public_history_interval_seconds)
        service = _NormalSubscriptionService(runtime.database, runtime.execution, clock=runtime.clock,
            portfolio_ids=[runtime.portfolio_id], handlers={}, artifact_runtime=runtime.artifact_runtime,
            public_feed=feed, secretary=runtime.secretary, schedule_intervals=config.schedule_intervals,
            automatic_schedule=config.automatic_schedule, optimisation_manual_only=config.optimisation_manual_only,
            tick_interval_seconds=config.tick_interval_seconds,
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
            "billing_kind": "subscription", "actual_cost_native": None, "cost_status": "unknown",
            "live_authorization": False, "paid_authorization": False}
    finally:
        runtime.database.close()
