"""Continuous deterministic paper management without subscription login or AI.

The protected network/configuration pins still apply. Only public market data,
Secretary collection, reconciliation and existing order/position management run;
queued model tasks never acquire a lease here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.adapters.market.public import HttpxTextTransport
from trade_graph.application.paper_service import PaperService
from trade_graph.kernel.deployment_image import assert_boot_environment, read_owner_file
from trade_graph.kernel.subscription_network import (
    load_subscription_network_profile,
    load_subscription_seccomp,
    validate_subscription_configuration,
)
from trade_graph.paper_runtime import assemble_paper_runtime


class _ManagementPaperService(PaperService):
    def _management(self):
        # Base startup calls this only after acquiring OS and durable ownership,
        # before reconciliation/dispatch. Every managed portfolio retains its halt.
        with self._execution_lock:
            for portfolio_id in self.management_portfolio_ids:
                if self.execution.profile(portfolio_id) == "RUNNING":
                    self.execution.set_pause(portfolio_id, "MANAGE_ONLY", "system",
                        "Subscription management service: retain existing paper orders and positions; "
                        "no new decisions.")
            return super()._management()

    def _schedules(self) -> int:
        if self.artifact_runtime:
            self.artifact_runtime.maintain(reconcile=self._reconcile, consumer_id=self.owner)
        for portfolio_id in self.management_portfolio_ids:
            self.secretary.process(portfolio_id, route=False)
        return 0

    def _run_role(self) -> int:
        # Do not even claim queued work or consult provider readiness.
        return 0


def run_subscription_management(database_path: Path, protected_owner: Path, *,
                                maximum_ticks: int | None = None) -> dict:
    """Manage until SIGINT/SIGTERM; a finite tick limit supports synthetic tests."""
    if maximum_ticks is not None and (type(maximum_ticks) is not int or maximum_ticks < 1):
        raise ValueError("maximum_ticks must be a positive integer or None")
    manifest = assert_boot_environment()
    profile = load_subscription_network_profile(protected_owner)
    raw = read_owner_file(protected_owner, "paper-config.json", 262144)
    configured = validate_subscription_configuration(profile, manifest, raw)
    load_subscription_seccomp(protected_owner, profile)
    # The ordinary paper assembly requires persisted paper accounts and owner
    # policy, but receives neither protected_owner nor API model configuration.
    # Construct its public feed below, so it cannot create an ambient transport
    # or initialise the subscription admission/authentication path.
    runtime = assemble_paper_runtime(database_path,
        config=configured.model_copy(update={"public_data_enabled": False}), api_keys={})
    try:
        feed = None
        if configured.public_data_enabled:
            portfolio_ids = [row[0] for row in runtime.database.execute(
                "SELECT portfolio_id FROM portfolios WHERE mode='paper' ORDER BY created_at,rowid")]
            feed = PublicPaperFeed(runtime.execution, portfolio_ids, configured.paper_symbols,
                interval_seconds=configured.public_poll_interval_seconds,
                transport=HttpxTextTransport(timeout=5, proxy=profile.market_proxy_url, trust_env=False))
        service = _ManagementPaperService(runtime.database, runtime.execution, clock=runtime.clock,
            portfolio_ids=[runtime.portfolio_id], handlers={}, schedule_intervals={},
            artifact_runtime=runtime.artifact_runtime, public_feed=feed, secretary=runtime.secretary,
            tick_interval_seconds=configured.tick_interval_seconds)
        result = asyncio.run(service.run(max_ticks=maximum_ticks))
        return {**result, "status": "management_degraded" if result["failures"] else "management_stopped",
            "position_management": runtime.execution.profile(runtime.portfolio_id),
            "profile_sha256": profile.sha256, "inference_attempts": 0, "ai_enabled": False,
            "live_authorization": False, "paid_authorization": False, "automatic_optimisation": False}
    finally:
        runtime.database.close()
