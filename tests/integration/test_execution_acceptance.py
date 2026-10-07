"""Offline A10–A13 timelines through real authority, outbox, paper matching and ledger."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.market.public import KrakenPublicFeed, KrakenPublicRest
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import (
    CancelResult,
    Decision,
    FillPage,
    FillRecord,
    InstrumentRules,
    Observation,
    OrderLookupResult,
    Quantity,
    SubmitResult,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState, UncertainExternal, ValidationFailure
from trade_graph.domain.money import Money


def _quote(clock, observation_id, *, size="1", bid="100", ask="100") -> Observation:
    return Observation(
        observation_id=observation_id,
        venue="paper",
        symbol="BTC/USD",
        event_time_utc=clock.now(),
        available_at_utc=clock.now(),
        bid=bid,
        ask=ask,
        bid_size=size,
        ask_size=size,
        kind="quote",
        source="synthetic-acceptance",
    )


def _decision(clock, portfolio, record_id, *, amount="0.03", action="enter", **updates) -> Decision:
    return Decision(
        record_id=record_id,
        created_at_utc=clock.now(),
        run_id="acceptance",
        task_id=record_id,
        root_task_id="acceptance",
        portfolio_id=portfolio,
        mode="paper",
        system_version_id="v1",
        trace_id=record_id,
        action=action,
        symbol="BTC/USD",
        quantity=Quantity(amount=amount, asset="BTC"),
        rationale="synthetic execution acceptance",
        invalidation="below 95",
        horizon_seconds=3600,
        strategy_id="slow-trend",
        snapshot_id="obs:seed",
        mandate_revision="1",
        policy_revision="1",
        **updates,
    )


def _stack(tmp_path, *, capital="10000", fee_rate="0.008"):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "acceptance.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock, taker_rate=Decimal(fee_rate))
    execution = Execution(database, ledger, clock, broker, fee_reserve_rate=Decimal(fee_rate))
    execution.register_instrument(InstrumentRules(
        venue="paper", symbol="BTC/USD", base_asset="BTC", quote_asset="USD",
        price_increment="0.1", quantity_increment="0.0001", min_quantity="0.0001",
        min_notional="1", synthetic=True,
    ))
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal(capital), "synthetic-capital")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    authority = AuthorityRecord(database, clock)
    authority.install_policy(paper_owner_policy(gross="1", asset="1"), role="owner")
    authority.install_mandate(paper_mandate(portfolio, gross="1", asset="1"), role="owner")
    execution.save_observation(_quote(clock, "seed"))
    return clock, database, ledger, broker, execution, portfolio


def _payload(database, intent_id):
    return json.loads(database.execute(
        "SELECT payload_json FROM order_intents WHERE intent_id = ?", (intent_id,),
    ).fetchone()["payload_json"])


def _fills(database, intent_id):
    return [FillRecord.model_validate_json(row["document_json"]) for row in database.execute(
        "SELECT document_json FROM fills WHERE intent_id = ?", (intent_id,),
    ).fetchall()]


def _held(database, intent_id):
    return sum((Decimal(row["amount"]) for row in database.execute(
        "SELECT amount FROM position_reservations WHERE intent_id = ? AND state = 'held'", (intent_id,),
    ).fetchall()), Decimal("0"))


def _ledger_fill_count(database):
    return database.execute("SELECT count(*) FROM ledger_events WHERE kind = 'fill'").fetchone()[0]


def _entry(clock, ledger, execution, portfolio):
    entry = execution.authorize(portfolio, _decision(clock, portfolio, "entry"))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "entry-fill"))
    assert execution.intent_state(entry) == "FILLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.03")
    ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="synthetic-acceptance")
    return entry


class _Wrapper:
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.mark.parametrize("lost_ack", ["exception", "uncertain_result"])
def test_a10_filled_before_lost_ack_recovers_without_resubmission(tmp_path, lost_ack) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)

    class FilledBeforeAck(_Wrapper):
        timeline = []
        hide_fills = True

        async def submit(self, intent):
            assert execution.intent_state(intent.intent_id) == "SUBMITTING"
            assert database.execute("SELECT count(*) FROM order_attempts").fetchone()[0] == 1
            await self.inner.submit(intent)
            self.timeline.append("accepted")
            clock.advance(1)
            fills = self.inner.match(_quote(clock, "fill-before-response"))
            assert sum((fill.quantity for fill in fills), Decimal("0")) == intent.quantity
            assert (await self.inner.open_orders()) == []
            self.timeline.append("filled")
            assert _ledger_fill_count(database) == 0
            self.timeline.append("ack-lost")
            if lost_ack == "exception":
                raise UncertainExternal("response lost after complete fill")
            return SubmitResult(status="uncertain", error="timeout_uncertain")

        async def fills_since(self, cursor):
            if self.hide_fills:
                return FillPage(fills=[], next_cursor=None)
            return await self.inner.fills_since(cursor)

    wrapper = FilledBeforeAck(inner)
    execution.broker = wrapper
    intent = execution.authorize(portfolio, _decision(clock, portfolio, "lost-ack"))
    asyncio.run(execution.dispatch())
    assert wrapper.timeline == ["accepted", "filled", "ack-lost"]
    assert execution.intent_state(intent) == "UNKNOWN"
    assert _held(database, intent) == Decimal("3.024")
    assert execution.owned_quantity(portfolio, "BTC") == 0
    with pytest.raises(StaleState, match="unresolved execution"):
        execution.authorize(portfolio, _decision(clock, portfolio, "conflicting-increase"))
    # History is already terminal but eventual fill visibility must retain uncertainty.
    asyncio.run(execution.startup())
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "UNKNOWN"
    assert _held(database, intent) == Decimal("3.024")
    assert inner.submit_count == 1
    wrapper.hide_fills = False
    asyncio.run(execution.startup())
    asyncio.run(execution.reconcile())
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "FILLED"
    assert inner.submit_count == 1
    assert len(_fills(database, intent)) == _ledger_fill_count(database) == 1
    assert _held(database, intent) == 0
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.03")
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("9996.976")
    assert ledger.journal_balanced(portfolio)


def test_a10_preauthorized_pending_increase_is_held_until_unknown_reconciles(tmp_path) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)

    class FirstFilledAckLost(_Wrapper):
        hide_fills = True
        uncertain_intent_id = None

        async def submit(self, intent):
            result = await self.inner.submit(intent)
            if self.inner.submit_count == 1:
                self.uncertain_intent_id = intent.intent_id
                clock.advance(1)
                assert len(self.inner.match(_quote(clock, "filled-before-first-response"))) == 1
                raise UncertainExternal("first order filled before acknowledgement was lost")
            return result

        async def fills_since(self, cursor):
            if self.hide_fills:
                return FillPage(fills=[], next_cursor=None)
            return await self.inner.fills_since(cursor)

    wrapper = FirstFilledAckLost(inner)
    execution.broker = wrapper
    intents = [
        execution.authorize(portfolio, _decision(clock, portfolio, record_id))
        for record_id in ("first", "pending-before-submit")
    ]
    assert all(execution.intent_state(intent) == "SUBMISSION_PENDING" for intent in intents)
    asyncio.run(execution.dispatch())
    # Identify the first actual submission; both intents were authorized before dispatch.
    first = wrapper.uncertain_intent_id
    assert first in intents
    pending = next(intent for intent in intents if intent != first)
    assert execution.intent_state(first) == "UNKNOWN"
    asyncio.run(execution.startup())
    asyncio.run(execution.dispatch())
    assert execution.intent_state(first) == "UNKNOWN"
    assert execution.intent_state(pending) == "SUBMISSION_PENDING"
    assert inner.submit_count == 1
    assert _held(database, first) == _held(database, pending) == Decimal("3.024")
    assert database.execute("SELECT count(*) FROM order_attempts WHERE intent_id = ?", (pending,)).fetchone()[0] == 0
    wrapper.hide_fills = False
    ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="synthetic-acceptance")
    asyncio.run(execution.startup())
    asyncio.run(execution.dispatch())
    assert execution.intent_state(first) == "FILLED"
    assert execution.intent_state(pending) == "OPEN"
    assert inner.submit_count == 2
    for intent in (first, pending):
        assert database.execute("SELECT count(*) FROM order_attempts WHERE intent_id = ?", (intent,)).fetchone()[0] == 1
    assert _ledger_fill_count(database) == 1
    assert ledger.journal_balanced(portfolio)


def test_a11_partial_fill_cancel_replace_uses_reconciled_remainder(tmp_path) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    _entry(clock, ledger, execution, portfolio)
    original = execution.authorize(portfolio, _decision(clock, portfolio, "exit", action="exit"))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "partial-exit", size="0.01"))
    assert execution.intent_state(original) == "PARTIALLY_FILLED"
    assert _held(database, original) == execution.owned_quantity(portfolio, "BTC") == Decimal("0.02")
    with pytest.raises(ValidationFailure, match="insufficient available balance"):
        execution.authorize(portfolio, _decision(clock, portfolio, "competing-exit", action="exit", amount="0.02"))

    class FillDuringCancel(_Wrapper):
        async def cancel(self, request):
            clock.advance(1)
            fills = self.inner.match(_quote(clock, "raced-partial", size="0.005"))
            assert [fill.quantity for fill in fills] == [Decimal("0.005")]
            # The fill is durable at the broker before cancellation, absent locally until reconciliation.
            assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.02")
            return await self.inner.cancel(request)

    execution.broker = FillDuringCancel(inner)
    replacement = asyncio.run(execution.replace(
        original, _decision(clock, portfolio, "replacement", action="exit", amount="0.015"),
    ))
    assert execution.intent_state(original) == "CANCELLED"
    assert [fill.quantity for fill in _fills(database, original)] == [Decimal("0.01"), Decimal("0.005")]
    assert _held(database, original) == 0
    assert Decimal(_payload(database, replacement)["quantity"]) == Decimal("0.015")
    assert _held(database, replacement) == execution.owned_quantity(portfolio, "BTC") == Decimal("0.015")
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "replacement-fill"))
    asyncio.run(execution.reconcile())
    sells = _fills(database, original) + _fills(database, replacement)
    assert sum((fill.quantity for fill in sells), Decimal("0")) == Decimal("0.03")
    assert execution.owned_quantity(portfolio, "BTC") == _held(database, replacement) == 0
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("9999.952")
    assert ledger.journal_balanced(portfolio)
    assert _ledger_fill_count(database) == 4
    assert inner.submit_count == 3


@pytest.mark.parametrize("race", ["extra_partial", "original_full", "final_fill", "uncertain"])
def test_a11_cancel_race_refuses_stale_or_uncertain_replacement(tmp_path, race) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    _entry(clock, ledger, execution, portfolio)
    original = execution.authorize(portfolio, _decision(clock, portfolio, "exit", action="exit"))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "partial", size="0.01"))

    class Race(_Wrapper):
        async def cancel(self, request):
            if race == "uncertain":
                return CancelResult(status="uncertain", error="timeout_uncertain")
            clock.advance(1)
            size = "0.02" if race == "final_fill" else "0.005"
            assert [fill.quantity for fill in self.inner.match(_quote(clock, "race", size=size))] == [Decimal(size)]
            result = await self.inner.cancel(request)
            assert result.status == ("filled" if race == "final_fill" else "cancelled")
            return result

        async def order_status(self, key):
            if race == "uncertain":
                return OrderLookupResult(status="unknown")
            return await self.inner.order_status(key)

    execution.broker = Race(inner)
    error = UncertainExternal if race == "uncertain" else ValidationFailure
    with pytest.raises(error):
        asyncio.run(execution.replace(
            original, _decision(
                clock, portfolio, "stale-replacement", action="exit",
                amount="0.03" if race == "original_full" else "0.02",
            ),
        ))
    asyncio.run(execution.dispatch())
    assert inner.submit_count == 2
    assert database.execute("SELECT count(*) FROM order_intents").fetchone()[0] == 2
    assert _ledger_fill_count(database) == (2 if race == "uncertain" else 3)
    remaining = {"extra_partial": "0.015", "original_full": "0.015", "final_fill": "0", "uncertain": "0.02"}[race]
    assert execution.owned_quantity(portfolio, "BTC") == Decimal(remaining)
    assert _held(database, original) == (Decimal("0.02") if race == "uncertain" else 0)
    assert execution.intent_state(original) == {
        "extra_partial": "CANCELLED", "original_full": "CANCELLED",
        "final_fill": "FILLED", "uncertain": "CANCEL_PENDING",
    }[race]


@pytest.mark.parametrize("feature", ["ioc", "combined_stop", "stop_unsupported", "stop_untested"])
def test_a12_unsupported_features_are_typed_refusals_before_submission(tmp_path, feature) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    if feature == "ioc":
        intent = execution.authorize(portfolio, _decision(clock, portfolio, "ioc", time_in_force="ioc"))
        assert _payload(database, intent)["time_in_force"] == "ioc"
    elif feature == "combined_stop":
        intent = execution.authorize(portfolio, _decision(
            clock, portfolio, "combined-stop", stop_price=Money(amount="95", currency="USD"),
        ))
    else:
        _entry(clock, ledger, execution, portfolio)

        class NoStop(_Wrapper):
            async def capabilities(self):
                caps = await self.inner.capabilities()
                return caps.model_copy(update={
                    "native_stop": feature != "stop_unsupported", "native_stop_tested": False,
                })

        execution.broker = NoStop(inner)
        intent = execution.place_protection(portfolio, "BTC/USD", Decimal("0.03"), Decimal("95"), "seed")
    before = inner.submit_count
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "REJECTED"
    assert _payload(database, intent)["error"] == "rejected"
    assert "unsupported" in _payload(database, intent)["message"]
    assert _held(database, intent) == 0
    assert inner.submit_count == before
    assert (asyncio.run(inner.open_orders())) == []
    assert database.execute("SELECT status FROM outbox WHERE payload_ref = ?", (intent,)).fetchone()[0] == "cancelled"
    assert database.execute("SELECT count(*) FROM order_attempts WHERE intent_id = ?", (intent,)).fetchone()[0] == 0


def test_a12_unsupported_refusal_is_atomic_on_journal_failure(tmp_path) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    intent = execution.authorize(portfolio, _decision(clock, portfolio, "ioc", time_in_force="ioc"))
    database.execute("""CREATE TRIGGER fail_outbox_update BEFORE UPDATE ON outbox
        BEGIN SELECT RAISE(ABORT, 'synthetic outbox write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic outbox write failure"):
        asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "SUBMISSION_PENDING"
    assert _held(database, intent) == Decimal("3.024")
    assert "error" not in _payload(database, intent)
    assert database.execute("SELECT status FROM outbox WHERE payload_ref = ?", (intent,)).fetchone()[0] == "pending"
    assert inner.submit_count == _ledger_fill_count(database) == 0
    assert (asyncio.run(inner.open_orders())) == []


def test_a12_missing_client_id_lookup_refuses_dispatch_without_effects(tmp_path) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    intent = execution.authorize(portfolio, _decision(clock, portfolio, "lookup-required"))

    class NoLookup(_Wrapper):
        async def capabilities(self):
            return (await self.inner.capabilities()).model_copy(update={"client_id_lookup": False})

    execution.broker = NoLookup(inner)
    with pytest.raises(AuthorityDenied, match="cannot look up orders by client id") as refusal:
        asyncio.run(execution.dispatch())
    assert refusal.value.code == "authority_denied"
    assert execution.intent_state(intent) == "SUBMISSION_PENDING"
    assert _held(database, intent) == Decimal("3.024")
    assert inner.submit_count == _ledger_fill_count(database) == 0
    assert (asyncio.run(inner.open_orders())) == []


@pytest.mark.parametrize("feature", ["ioc", "combined_stop"])
def test_a12_paper_broker_returns_typed_unsupported_refusal(tmp_path, feature) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    updates = {"time_in_force": "ioc"} if feature == "ioc" else {"stop_price": Money(amount="95", currency="USD")}
    intent = execution.authorize(portfolio, _decision(clock, portfolio, "unsupported", **updates))
    result = asyncio.run(inner.submit(execution._intent_model(intent)))
    assert isinstance(result, SubmitResult)
    assert result.status == result.error == "rejected"
    assert "unsupported" in result.message
    assert database.execute("SELECT count(*) FROM broker_orders").fetchone()[0] == 0
    assert _ledger_fill_count(database) == 0
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("10000")
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "REJECTED"
    assert _held(database, intent) == 0
    assert inner.submit_count == 1


@pytest.mark.parametrize("minimum", ["quantity", "notional", "rounded_notional"])
def test_a12_minimum_refusal_never_resizes_or_creates_outbox(tmp_path, minimum) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    rules = execution.instrument("paper", "BTC/USD")
    if minimum == "quantity":
        execution.register_instrument(rules.model_copy(update={"min_quantity": Decimal("0.02")}))
        amount = "0.01"
    elif minimum == "notional":
        amount = "0.0099"
    else:
        execution.register_instrument(rules.model_copy(update={"min_notional": Decimal("1.005")}))
        amount = "0.01009"  # Raw 1.009 qualifies; flooring gives 1.00, which does not.
    decision = _decision(clock, portfolio, "too-small", amount=amount)
    with pytest.raises(ValidationFailure, match="below venue minimum; size was not increased") as refusal:
        execution.authorize(portfolio, decision)
    assert refusal.value.code == "validation"
    assert decision.quantity.amount == Decimal(amount)
    for table in ("order_intents", "position_reservations", "outbox", "decisions"):
        assert database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert inner.submit_count == _ledger_fill_count(database) == 0
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("10000")


@pytest.mark.parametrize("fee_rate", ["0.008", "0.02"])
def test_a12_fee_reserve_refuses_notional_only_funds_and_posts_exact_fee(tmp_path, fee_rate) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path, capital="100", fee_rate=fee_rate)
    decision = _decision(clock, portfolio, "fee-short", amount="1")
    with pytest.raises(ValidationFailure, match="insufficient available balance") as refusal:
        execution.authorize(portfolio, decision)
    assert refusal.value.code == "validation"
    assert database.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert inner.submit_count == 0
    fee = Decimal("100") * Decimal(fee_rate)
    ledger.deposit(portfolio, "USD", fee, "fee-top-up")
    intent = execution.authorize(portfolio, _decision(clock, portfolio, "fee-funded", amount="1"))
    assert _held(database, intent) == Decimal("100") + fee
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "fee-fill"))
    asyncio.run(execution.reconcile())
    assert _fills(database, intent)[0].fee_amount == fee
    assert ledger.books(portfolio).cash_amount("USD") == _held(database, intent) == 0
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("1")
    assert _ledger_fill_count(database) == 1
    assert ledger.journal_balanced(portfolio)


@pytest.mark.parametrize("fee_kind", ["taker", "maker", "wrapped_taker"])
def test_a12_fee_reserve_configuration_must_cover_broker_fees(tmp_path, fee_kind) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    if fee_kind == "maker":
        inner.maker_rate = Decimal("0.02")
    else:
        inner.taker_rate = Decimal("0.02")
    broker = DropAckBroker(inner) if fee_kind == "wrapped_taker" else inner
    with pytest.raises(ValidationFailure, match="below the broker's configured fees") as refusal:
        Execution(database, ledger, clock, broker, fee_reserve_rate=Decimal("0.008"))
    assert refusal.value.code == "validation"
    with pytest.raises(ValidationFailure, match="below the broker's configured fees"):
        execution.authorize(portfolio, _decision(clock, portfolio, "fee-configuration-shortfall"))
    assert database.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert inner.submit_count == 0
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("10000")


def test_a12_fee_increase_before_dispatch_refuses_unfunded_intent(tmp_path) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    intent = execution.authorize(portfolio, _decision(clock, portfolio, "old-fee"))
    inner.taker_rate = Decimal("0.02")
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "REJECTED"
    assert _payload(database, intent)["error"] == "rejected"
    assert "configured fees" in _payload(database, intent)["message"]
    assert _held(database, intent) == 0
    assert inner.submit_count == _ledger_fill_count(database) == 0


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_a12_rounding_stays_within_quantity_and_limit_authorization(tmp_path, side) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    if side == "sell":
        _entry(clock, ledger, execution, portfolio)
    decision = _decision(
        clock, portfolio, "rounded", amount="0.01009", action="enter" if side == "buy" else "exit",
        limit_price=Money(amount="100.06", currency="USD"),
    )
    intent = execution.authorize(portfolio, decision)
    payload = _payload(database, intent)
    assert Decimal(payload["quantity"]) == Decimal("0.01") < decision.quantity.amount
    assert Decimal(payload["limit_price"]) == Decimal("100" if side == "buy" else "100.1")
    asyncio.run(execution.dispatch())
    clock.advance(1)
    bid, ask = ("99.8", "99.9") if side == "buy" else ("100.2", "100.3")
    execution.on_observation(_quote(clock, "rounded-fill", bid=bid, ask=ask).model_copy(
        update={"volume": Decimal("1")},
    ))
    fill = _fills(database, intent)[0]
    assert fill.quantity == Decimal("0.01")
    assert fill.price <= decision.limit_price.amount if side == "buy" else fill.price >= decision.limit_price.amount
    assert fill.fee_amount == fill.price * fill.quantity * Decimal("0.004")
    assert execution.intent_state(intent) == "FILLED"
    assert _held(database, intent) == 0


def test_a13_fresh_quote_with_known_feed_gap_blocks_increase_but_allows_protection(tmp_path) -> None:
    clock, database, ledger, inner, execution, portfolio = _stack(tmp_path)
    _entry(clock, ledger, execution, portfolio)

    def ticker(timestamp):
        return json.dumps({"channel": "ticker", "type": "update", "data": [{
            "symbol": "BTC/USD", "timestamp": timestamp, "bid": "100", "ask": "100",
            "bid_qty": "1", "ask_qty": "1",
        }]})

    class Session:
        messages = [ticker(clock.now().isoformat()), ticker((clock.now() - timedelta(seconds=1)).isoformat())]

        def send_text(self, payload):
            pass

        def recv_text(self):
            return self.messages.pop(0)

    class NoNetwork:
        def get_text(self, url):
            raise AssertionError("This out-of-order timeline must not call REST")

    session = Session()
    feed = KrakenPublicFeed(lambda: session, KrakenPublicRest(NoNetwork()), clock, ["BTC/USD"])
    fresh = feed.poll()[0]
    assert not feed.blocks_increase("BTC/USD")
    # Rebind public observations to the synthetic paper instrument at the fixture boundary.
    execution.save_observation(fresh.model_copy(update={"venue": "paper"}))
    execution = Execution(database, ledger, clock, inner, blocks_increase=feed.blocks_increase)
    queued = execution.authorize(portfolio, _decision(clock, portfolio, "queued-before-gap"))
    protection = execution.place_protection(portfolio, "BTC/USD", Decimal("0.03"), Decimal("95"), "seed")
    assert feed.poll() == []  # Existing public behavior: out-of-order input marks a gap.
    assert "out_of_order:BTC/USD" in feed.gaps
    assert feed.blocks_increase("BTC/USD")
    assert feed.latest("BTC/USD") == fresh
    assert execution.quote_fresh("BTC/USD", 30, "paper")  # Fresh age alone is insufficient.
    with pytest.raises(StaleState, match="feed") as refusal:
        execution.authorize(portfolio, _decision(clock, portfolio, "blocked-by-gap"))
    assert refusal.value.code == "stale_state"
    assert database.execute("SELECT count(*) FROM decisions WHERE decision_id = 'blocked-by-gap'").fetchone()[0] == 0
    asyncio.run(execution.dispatch())
    assert execution.intent_state(queued) == "SUBMISSION_PENDING"
    assert execution.intent_state(protection) == "OPEN"
    assert inner.submit_count == 2  # Entry and protection only.
    clock.advance(1)
    # Existing deterministic protection still executes on an observed gap-through price.
    assert len(inner.match(_quote(clock, "protective-fill", bid="90", ask="91"))) == 1
    asyncio.run(execution.reconcile())
    assert execution.intent_state(protection) == "FILLED"
    assert _fills(database, protection)[0].price == Decimal("90")
    assert execution.owned_quantity(portfolio, "BTC") == 0
    asyncio.run(execution.dispatch())
    assert inner.submit_count == 2
    # Existing feed semantics clear degradation on the next accepted update.
    session.messages.append(ticker(clock.now().isoformat()))
    recovered = feed.poll()[0]
    assert not feed.blocks_increase("BTC/USD")
    execution.save_observation(recovered.model_copy(update={"venue": "paper"}))
    asyncio.run(execution.dispatch())
    assert execution.intent_state(queued) == "OPEN"
    assert inner.submit_count == 3
