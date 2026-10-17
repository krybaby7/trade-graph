"""The paper service runs the day-plan management step and sends protective exits as soon as a fill is recorded
(T34). Synthetic account, scripted quotes; no network, model or key."""

from __future__ import annotations

from decimal import Decimal

from tests.day_plan_support import broker_orders, day_plan_account, entry, install, plan, protected_quantity

from trade_graph.application.paper_service import PaperService
from trade_graph.contracts.models import Observation

D = Decimal


def _service(account) -> PaperService:
    service = PaperService(account.database, account.execution, clock=account.clock,
                           portfolio_ids=[account.portfolio], automatic_schedule=False)
    service.management_portfolio_ids = (account.portfolio,)
    return service


def _quote(account, observation_id: str, bid: str, ask: str, *, volume: str | None = None) -> Observation:
    return Observation(observation_id=observation_id, venue="paper", symbol="SOL/USD",
                       event_time_utc=account.clock.now(), available_at_utc=account.clock.now(), bid=bid, ask=ask,
                       bid_size="1000", ask_size="1000", volume=volume, kind="quote", source="synthetic-day-plans")


def test_the_management_step_sends_entries_and_a_pause_cancels_them_keeping_exits(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", "104")]))
    service = _service(account)
    _management, failures = service._management()
    assert failures == () and len(broker_orders(account, side="buy", live=True)) == 1
    account.clock.advance(1)
    account.quote("SOL/USD", "99.4", "99.5", volume="2")
    _management, failures = service._management()
    assert failures == () and protected_quantity(account) == D("2")
    account.execution.set_pause(account.portfolio, "MANAGE_ONLY", "system", "synthetic role failure")
    _management, failures = service._management()
    assert failures == ()
    assert account.state(installed.entries[0].intent_id) == "CANCELLED"
    assert protected_quantity(account) == D("2") and account.held("SOL") == D("2")


def test_a_protective_exit_goes_out_with_the_fill_so_the_next_quote_of_the_same_poll_can_trigger_it(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account, plan(entries=[entry("5", "99.5", "97")]))
    service = _service(account)
    service._management()
    account.clock.advance(1)
    # One poll brings two quotes: the first fills the resting bid, the second falls through its stop.
    service._ingest([_quote(account, "fill", "99.4", "99.5", volume="100"),
                     _quote(account, "crash", "96.5", "96.6", volume="0")])
    assert account.held("SOL") == D("0") and protected_quantity(account) == D("0")
    sells = broker_orders(account, side="sell")
    assert [(order["status"], order["stop_price"]) for order in sells] == [("filled", "97")]


def test_a_failing_day_plan_step_is_reported_and_never_holds_back_dispatch(tmp_path, monkeypatch) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    service = _service(account)

    async def broken(portfolio_id):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(service.day_plans, "manage", broken)
    _management, failures = service._management()
    assert failures == ("day_plans:RuntimeError",)
    assert len(broker_orders(account, side="buy", live=True)) == 1
