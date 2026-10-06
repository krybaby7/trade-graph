"""Exclusive Kraken service with independent deterministic account maintenance.

The shared controller owns an OS inode lock through draining role/feed work.
Unknown submissions are reconciled by the existing durable Execution protocol;
the graph cannot bypass the protected pilot or choose an external account.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, localcontext

from trade_graph.application.paper_service import PaperService
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure
from trade_graph.domain.money import parse_decimal
from trade_graph.kernel.live_commission import PinnedLiveCommission
from trade_graph.live_pilot import ProtectedPilotLifecycle


class LiveService(PaperService):
    def __init__(self, runtime, *, service_run_id=None):
        execution = runtime.execution
        if (execution.mode != "live" or type(runtime.lifecycle) is not ProtectedPilotLifecycle
                or execution._pilot_lifecycle is not runtime.lifecycle
                or type(runtime.commission) is not PinnedLiveCommission
                or runtime.lifecycle.upstream.commission != runtime.commission
                or execution._pilot_authorization_id != runtime.authorization_id):
            raise AuthorityDenied("exact protected live service assembly required")
        self.runtime = runtime
        self._account_ready = False
        super().__init__(runtime.database, execution, clock=runtime.clock, portfolio_ids=[runtime.portfolio_id],
                         handlers={}, artifact_runtime=runtime.artifact_runtime, public_feed=runtime.public_feed,
                         secretary=runtime.secretary, tick_interval_seconds=runtime.config.tick_interval_seconds,
                         recover_commands=self._recover_live, prepare_runtime=self._prepare_live,
                         runtime_ready=self._runtime_ready, service_mode="live", service_run_id=service_run_id,
                         subscription_provider=getattr(runtime, "subscription_provider", None))

    def _recover_live(self):
        lifecycle = self.runtime.lifecycle
        # The OS flock is held before the first financial/lifecycle mutation.
        prior = self.database.execute("SELECT 1 FROM live_pilot_grants WHERE authorization_id=?",
                                      (self.runtime.authorization_id,)).fetchone()
        if prior is None:
            lifecycle.prepare()
        else:
            lifecycle._grant(self.runtime.authorization_id)
        with self._execution_lock:
            asyncio.run(self.execution.startup())

    def _prepare_live(self):
        runtime = self.runtime
        grant = runtime.database.execute("SELECT state FROM live_pilot_grants WHERE authorization_id=?",
                                         (runtime.authorization_id,)).fetchone()
        if grant and grant["state"] == "PENDING":
            result = runtime.lifecycle.activate(runtime.authorization_id)
            if not result["activated"]:
                raise AuthorityDenied("live startup blocked: current_reconciled_admission_failed")
            # Retain exactly which independently reviewed original account
            # observation commissioned this financial history. Future current
            # account refresh belongs to this trusted service, not owner clicks.
            runtime.ledger._activity(runtime.portfolio_id, "live_commission_admitted", {
                "commission_sha256": runtime.commission.profile_sha256,
                "authorization_id": runtime.authorization_id,
                "scope_sha256": runtime.commission.load().scope_sha256,
            })
        return runtime.prepare_runtime()

    def _runtime_ready(self):
        return self._account_ready and self.runtime.runtime_ready()

    async def _account(self):
        broker, runtime = self.execution.broker, self.runtime
        await self.execution._require_broker_binding()
        for rules in await broker.instruments():
            self.execution.register_instrument(rules)
        fees = getattr(broker, "fee_schedule", None)
        if fees:
            await fees(account_specific=True)
        snapshot = await broker.balances()
        if (snapshot.venue != self.execution.venue or snapshot.account_id != self.execution.account_id
                or snapshot.as_of_utc > self.clock.now()):
            raise ValidationFailure("live account balance binding is invalid")
        books = runtime.ledger.books(runtime.portfolio_id)
        with localcontext() as context:
            context.prec = 128
            expected = dict(books.cash)
            for lot in books.lots:
                expected[lot.asset] = expected.get(lot.asset, Decimal(0)) + lot.open_quantity()
            actual = {asset: parse_decimal(amount) for asset, amount in snapshot.amounts.items()}
            if any(actual.get(asset, Decimal(0)) != expected.get(asset, Decimal(0))
                   for asset in set(actual) | set(expected)):
                raise ValidationFailure("live account ledger balance discrepancy requires owner review")
        opened = await broker.open_orders()
        identity = getattr(broker, "intent_resolver", None)
        for order in opened:
            if not identity or identity(order.client_order_id, order.venue_order_id) is None:
                raise ValidationFailure("external live account order requires owner review")
        health = self.execution._reconciliation_health()
        if health and health["state"] != "complete":
            raise ValidationFailure("live owned order history remains uncertain")
        basis = getattr(getattr(broker, "transport", None), "observation_basis", "synthetic")
        with runtime.database.immediate():
            runtime.ledger._activity(runtime.portfolio_id, "live_account_health", {
                "state": "complete", "basis": basis,
                "commission_sha256": runtime.commission.profile_sha256,
            })
            # The protected lifecycle can distinguish an actual full-account
            # balance/order refresh from an owned-intent-only history scan.
            if basis == "owned_https":
                runtime.ledger._activity(runtime.portfolio_id, "execution_reconciliation_health", {
                    "venue": self.execution.venue, "account_id": self.execution.account_id, "mode": "live",
                    "state": "complete", "reason": "verified full account balances orders and owned history",
                    "observed_at": self.execution.now(), "observation_scope": "complete_account",
                    "commission_sha256": runtime.commission.profile_sha256,
                })

    def _management(self):
        management, failures = {}, []
        with self._execution_lock:
            try:
                asyncio.run(self.execution.reconcile())
            except Exception as exc:
                failures.append("reconciliation:" + type(exc).__name__)
            self._account_ready = False
            if not failures:
                try:
                    asyncio.run(self._account())
                    self._account_ready = True
                except Exception as exc:
                    failures.append("account:" + type(exc).__name__)
                    self.runtime.ledger._activity(self.runtime.portfolio_id, "live_account_health", {
                        "state": "incomplete", "basis": "unverified",
                        "commission_sha256": self.runtime.commission.profile_sha256,
                        "error_type": type(exc).__name__,
                    })
            for pid in self.management_portfolio_ids:
                try:
                    management[pid] = asyncio.run(self.execution.advance_pause(pid))
                except Exception as exc:
                    failures.append("pause:" + type(exc).__name__)
            if not failures:
                try:
                    asyncio.run(self.execution.dispatch())
                except Exception as exc:
                    failures.append("dispatch:" + type(exc).__name__)
        return management, tuple(failures)

    async def _shutdown(self):
        await super()._shutdown()
        transport = getattr(self.runtime, "private_transport", None)
        if transport is not None:
            await transport.aclose()

    def status(self):
        return {"mode": "live", "started": self._started, "account_ready": self._account_ready,
                "graph_ready": self._runtime_ready() if self._ready else False,
                "stop_behaviour": "drain work; protection requires this service until verified flat",
                "commission_sha256": self.runtime.commission.profile_sha256}
