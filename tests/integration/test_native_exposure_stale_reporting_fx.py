"""D114: a previous-business-day EUR reference rate flags EUR views but does not gate USD exposure.

Synthetic data only; the reference rate is written by the production feed path
through a scripted transport, so no Frankfurter or exchange call is made.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, FillRecord, InstrumentRules, Observation
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState

D = Decimal
FX_URL = "https://api.frankfurter.dev/v2/providers/ecb/rate/usd/eur"
# Friday's ECB reference, as Frankfurter serves it over a weekend and on Monday
# morning before the next publication.
FRIDAY_REFERENCE = '{"date":"2026-10-09","base":"USD","quote":"EUR","rate":"0.86"}'
CLOCKS = {
    "weekend": datetime(2026, 10, 10, 9, tzinfo=UTC),
    "weekday-before-ecb-publication": datetime(2026, 10, 12, 8, tzinfo=UTC),
}


class ScriptedTransport:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    def get_text(self, url: str) -> str:
        return self.responses[url]


class Stack(NamedTuple):
    clock: FrozenClock
    ledger: Ledger
    execution: Execution
    broker: PaperBroker
    portfolio: str


@pytest.fixture(params=list(CLOCKS.values()), ids=list(CLOCKS))
def stack(request, tmp_path):
    clock = FrozenClock(request.param)
    database = Database(tmp_path / "native.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock)
    execution = Execution(database, ledger, clock, broker)
    for symbol in ("BTC", "ETH"):
        execution.register_instrument(InstrumentRules(
            venue="paper", symbol=f"{symbol}/USD", base_asset=symbol, quote_asset="USD",
            price_increment="0.1", quantity_increment="0.001", min_quantity="0.001",
            min_notional="1", synthetic=True,
        ))
        execution.save_observation(Observation(
            observation_id=symbol, venue="paper", symbol=f"{symbol}/USD", event_time_utc=clock.now(),
            available_at_utc=clock.now(), bid="100", ask="100", bid_size="100", ask_size="100",
            kind="quote", source="fixture",
        ))
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "USD", D("10000"), "seed")
    transport = ScriptedTransport({FX_URL: FRIDAY_REFERENCE})
    PublicPaperFeed(execution, [portfolio], ["BTC/USD"], transport=transport)._reference_fx()
    seed_paper_authority(database, clock, portfolio)
    yield Stack(clock, ledger, execution, broker, portfolio)
    database.close()


def decision(stack: Stack, **updates) -> Decision:
    return Decision.model_validate({
        "record_id": "d", "created_at_utc": stack.clock.now(), "run_id": "r", "task_id": "t",
        "root_task_id": "t", "portfolio_id": stack.portfolio, "mode": "paper", "system_version_id": "v",
        "trace_id": "t", "action": "enter", "symbol": "BTC/USD", "quantity": {"amount": "1", "asset": "BTC"},
        "rationale": "fixture", "invalidation": "fixture", "horizon_seconds": 60,
        "strategy_id": "slow-trend-pullback", "snapshot_id": "snap", "mandate_revision": "1",
        "policy_revision": "1", **updates,
    })


def authorize(stack: Stack, amount: str, **updates) -> str:
    quantity = {"amount": amount, "asset": "BTC"}
    return stack.execution.authorize(stack.portfolio, decision(stack, quantity=quantity, **updates))


def hold_eth(stack: Stack) -> None:
    fill = FillRecord.model_validate({
        "venue": "paper", "account_id": "paper", "trade_id": "eth", "intent_id": None, "symbol": "ETH/USD",
        "side": "buy", "quantity": "40", "price": "100", "fee_amount": "0", "fee_asset": "USD",
        "liquidity": "taker", "filled_at_utc": stack.clock.now(), "heuristic": False,
    })
    stack.ledger.apply_fill(stack.portfolio, fill, base_asset="ETH", quote_asset="USD")


def test_euro_reporting_view_stays_flagged_stale(stack):
    view = stack.ledger.equity(stack.portfolio)
    assert view.reporting_currency == "EUR"
    assert view.equity == D("8600")
    assert view.provisional is True
    assert view.stale is True


def test_small_buy_within_caps_authorizes_with_previous_business_day_reference_fx(stack):
    intent = authorize(stack, "1")
    assert stack.execution.intent_state(intent) == "SUBMISSION_PENDING"


@pytest.mark.parametrize(("amount", "allowed"), [("48", True), ("52", False)])
def test_single_asset_cap_is_measured_in_usd(stack, amount, allowed):
    if allowed:
        assert authorize(stack, amount)  # 4,800 USD of 10,000 USD
    else:
        with pytest.raises(AuthorityDenied, match="single asset exposure"):
            authorize(stack, amount)


def test_pending_buys_count_in_usd_with_stale_reporting_fx(stack):
    authorize(stack, "30", record_id="a")
    with pytest.raises(AuthorityDenied, match="single asset exposure"):
        authorize(stack, "30", record_id="b")


@pytest.mark.parametrize(("amount", "allowed"), [("39", True), ("43", False)])
def test_gross_cap_values_other_inventory_at_usd_marks(stack, amount, allowed):
    hold_eth(stack)
    stack.ledger.observe_mark(stack.portfolio, "ETH", D("100"), "USD", source="fixture")
    if allowed:
        assert authorize(stack, amount)  # 4,000 ETH + 3,900 BTC of 10,000 USD
    else:
        with pytest.raises(AuthorityDenied, match="gross exposure"):
            authorize(stack, amount)


def test_queued_buy_is_submitted_not_held_by_stale_reporting_fx(stack):
    intent = authorize(stack, "1")
    asyncio.run(stack.execution.dispatch())
    assert stack.broker.submit_count == 1
    assert stack.execution.intent_state(intent) != "SUBMISSION_PENDING"


def test_missing_native_mark_still_blocks_new_exposure(stack):
    hold_eth(stack)
    with pytest.raises(StaleState, match="native USD valuation lacks a mark or FX for ETH"):
        authorize(stack, "1")


def test_flagged_stale_native_mark_still_blocks_new_exposure(stack):
    hold_eth(stack)
    stack.ledger.observe_mark(stack.portfolio, "ETH", D("100"), "USD", source="fixture", stale=True)
    with pytest.raises(StaleState, match="stale native USD valuation"):
        authorize(stack, "1")


def test_non_usd_cash_is_valued_by_inverting_the_reference_rate(stack):
    # Only USD->EUR reference rates are ever stored; EUR cash (as a live Kraken
    # account would hold) is valued with their inverse, never left unknown.
    stack.ledger.deposit(stack.portfolio, "EUR", D("100"), "eur")
    with pytest.raises(StaleState, match="stale native USD valuation"):
        authorize(stack, "1", record_id="weekend")  # needed FX is still the carried Friday rate
    today = stack.clock.now().date().isoformat()
    fresh = f'{{"date":"{today}","base":"USD","quote":"EUR","rate":"0.86"}}'
    PublicPaperFeed(stack.execution, [stack.portfolio], ["BTC/USD"],
                    transport=ScriptedTransport({FX_URL: fresh}))._reference_fx()
    assert authorize(stack, "1", record_id="fresh")
