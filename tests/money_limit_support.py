"""A synthetic paper account for the money-limit tests (T32). No network, model, key or real order.

Synthetic values only: a 4% daily loss limit, 30% per coin and 90% in total (not the owner's D138 settings).
The account opens the evening before the test day with USD10,000; SOL trades near 100 and BTC near 50,000.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import paper_mandate, paper_owner_policy
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, InstrumentRules, Observation, OwnerPolicy, Quantity
from trade_graph.contracts.money_limits import CoinScope, OwnerMoneyLimits
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.money import Money

D = Decimal
OPENED = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
MORNING = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
SYMBOLS = ["SOL/USD", "BTC/USD"]
SYNTHETIC_LIMITS = OwnerMoneyLimits(daily_loss_limit_fraction="0.04")


@dataclass
class Account:
    clock: FrozenClock
    database: Database
    ledger: Ledger
    broker: PaperBroker
    execution: Execution
    portfolio: str
    _sequence: int = 0

    def next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"

    @property
    def policy(self) -> OwnerPolicy:
        return self.execution.authority.active_policy()

    def quote(self, symbol: str, bid: str, ask: str, *, size: str = "1000", mark: bool = True,
              volume: str | None = None) -> Observation:
        """A synthetic quote. ``volume`` (traded since the last one) lets a crossed resting limit fill that share
        instead of the paper broker's half of what remains."""
        observation = Observation(
            observation_id=self.next_id("quote"), venue="paper", symbol=symbol, event_time_utc=self.clock.now(),
            available_at_utc=self.clock.now(), bid=bid, ask=ask, bid_size=size, ask_size=size, volume=volume,
            kind="quote", source="synthetic-money-limits")
        fills = self.execution.on_observation(observation)
        if mark:
            self.ledger.observe_mark(self.portfolio, symbol.split("/")[0], (D(bid) + D(ask)) / 2, "USD",
                                     source="synthetic-money-limits")
        return fills

    def decision(self, action: str, symbol: str, quantity: str, *, limit: str | None = None) -> Decision:
        record = self.next_id(action)
        policy = self.policy
        mandate = self.execution.authority.active_mandate(self.portfolio)
        return Decision(
            record_id=record, created_at_utc=self.clock.now(), run_id="money-limits", task_id=record,
            root_task_id="money-limits", portfolio_id=self.portfolio, mode="paper", system_version_id="v1",
            trace_id=record, action=action, symbol=symbol,
            quantity=Quantity(amount=quantity, asset=symbol.split("/")[0]),
            limit_price=None if limit is None else Money(amount=limit, currency="USD"),
            rationale="synthetic money-limit test", invalidation="synthetic", horizon_seconds=3600,
            strategy_id="slow-trend-pullback", snapshot_id="snap", mandate_revision=str(mandate.revision),
            policy_revision=policy.revision_id)

    def authorize(self, action: str, symbol: str, quantity: str, *, limit: str | None = None,
                  day_plan: bool = False) -> str:
        return self.execution.authorize(self.portfolio, self.decision(action, symbol, quantity, limit=limit),
                                        day_plan=day_plan)

    def dispatch(self) -> int:
        return asyncio.run(self.execution.dispatch())

    def enforce(self) -> dict:
        return asyncio.run(self.execution.enforce_money_limits(self.portfolio))

    def state(self, intent_id: str) -> str:
        return self.execution.intent_state(intent_id)

    def held(self, asset: str) -> Decimal:
        return self.execution.owned_quantity(self.portfolio, asset)

    def value(self) -> Decimal:
        return self.ledger.native_equity(self.portfolio, "USD").equity

    def install(self, policy: OwnerPolicy, *, scope: str = "listed", gross: str = "0.90", asset: str = "0.30",
                symbols: list[str] | None = None) -> None:
        authority = self.execution.authority
        authority.install_policy(policy, role="owner")
        latest = self.database.execute("SELECT MAX(revision) FROM mandates WHERE portfolio_id = ?",
                                       (self.portfolio,)).fetchone()[0] or 0
        mandate = paper_mandate(self.portfolio, revision=latest + 1, gross=gross, asset=asset,
                                symbols=symbols or SYMBOLS)
        authority.install_mandate(mandate.model_copy(update={"coin_scope": scope}), role="owner")


def synthetic_policy(revision: str, *, limits: OwnerMoneyLimits | None = SYNTHETIC_LIMITS,
                     scope: CoinScope | None = None, gross: str = "0.90", asset: str = "0.30",
                     symbols: list[str] | None = None) -> OwnerPolicy:
    return paper_owner_policy(revision_id=revision, gross=gross, asset=asset,
                              symbols=symbols or SYMBOLS).model_copy(update={"money_limits": limits,
                                                                             "coin_scope": scope})


def open_account(tmp_path, *, limits: OwnerMoneyLimits | None = SYNTHETIC_LIMITS, scope: CoinScope | None = None,
                 mandate_scope: str = "listed", coin_rule=None, symbols: list[str] | None = None,
                 opened: datetime = OPENED, now: datetime = MORNING) -> Account:
    clock = FrozenClock(opened)
    folder = tmp_path / "state"
    folder.mkdir(mode=0o700, exist_ok=True)
    database = Database(folder / "trade_graph.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock)
    execution = Execution(database, ledger, clock, broker, coin_rule=coin_rule)
    # BTCPERP/USD is registered only to show that D127 refuses it even when its rules are known.
    for symbol, increment in (("SOL/USD", "0.01"), ("BTC/USD", "0.1"), ("ADA/USD", "0.0001"),
                              ("BTCPERP/USD", "0.1")):
        base = symbol.split("/")[0]
        execution.register_instrument(InstrumentRules(
            venue="paper", symbol=symbol, base_asset=base, quote_asset="USD", price_increment=increment,
            quantity_increment="0.0001", min_quantity="0.0001", min_notional="1", synthetic=True))
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "USD", D("10000"), "opening")
    ledger.observe_fx(base="USD", quote="USD", rate=D("1"), source="identity", kind="identity", stale=False)
    account = Account(clock, database, ledger, broker, execution, portfolio)
    account.install(synthetic_policy("synthetic-1", limits=limits, scope=scope, symbols=symbols),
                    scope=mandate_scope, symbols=symbols)
    clock.advance((now - opened).total_seconds())
    account.quote("SOL/USD", "99.9", "100.1")
    account.quote("BTC/USD", "49990", "50010")
    account.quote("ADA/USD", "0.4999", "0.5001")
    return account


__all__ = ["MORNING", "OPENED", "SYMBOLS", "SYNTHETIC_LIMITS", "Account", "open_account", "synthetic_policy"]
