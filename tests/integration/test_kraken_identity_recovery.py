"""Untrusted synthetic wire data cannot establish ownership or release exposure."""

import asyncio
import base64
import json
from decimal import Decimal

import httpx
import pytest
from tests.integration.test_kraken_live_adapter import NOW, ScriptedRest, _broker, _intent, _ledgers, _order, _trade

from trade_graph.adapters.brokers.kraken_transport import KrakenApiError, KrakenRestTransport
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import CancelRequest, OrderLookup
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import UncertainExternal, ValidationFailure


def _fill_history(rest):
    rest.results["TradesHistory"] = {"trades": {"trade-1": _trade(1)}, "count": 1}
    rest.results["QueryLedgers"] = _ledgers(1)


@pytest.mark.parametrize("problem", ["reused_client", "overlap", "count_underflow", "count_overflow", "symbol"])
def test_ambiguous_lookup_never_establishes_client_ownership(problem):
    rest = ScriptedRest()
    rest.results["ClosedOrders"] = {"closed": {"order-1": _order(status="closed", vol_exec="0.1")}, "count": 1}
    if problem == "reused_client":
        rest.results["OpenOrders"] = {"open": {"order-2": _order()}}
    elif problem == "overlap":
        rest.results["OpenOrders"] = {"open": {"order-1": _order()}}
    elif problem == "count_underflow":
        rest.results["ClosedOrders"]["count"] = 0
    elif problem == "count_overflow":
        rest.results["ClosedOrders"]["count"] = 2
    _fill_history(rest)
    seen = []

    def resolve(client, order):
        seen.append((client, order))
        return "intent-1" if client == "client-1" else None

    broker = _broker(rest, intent_resolver=resolve)
    key = OrderLookup(client_order_id="client-1", symbol="ETH/USD" if problem == "symbol" else "BTC/USD")
    assert asyncio.run(broker.order_status(key)).status == "unknown"
    assert asyncio.run(broker.fills_since(None)).fills[0].intent_id is None
    assert seen == [(None, "order-1")]


def test_malformed_open_order_batch_never_leaks_partial_client_associations():
    rest = ScriptedRest()
    rest.results["OpenOrders"] = {"open": {"order-1": _order(), "order-2": _order(descr={})}}
    _fill_history(rest)
    seen = []
    broker = _broker(rest, intent_resolver=lambda client, order: seen.append((client, order)))
    with pytest.raises(ValidationFailure):
        asyncio.run(broker.open_orders())
    assert asyncio.run(broker.fills_since(None)).fills[0].intent_id is None
    assert seen == [(None, "order-1")]


def test_reused_client_in_open_orders_never_establishes_partial_ownership():
    rest = ScriptedRest()
    rest.results["OpenOrders"] = {"open": {"order-1": _order(), "order-2": _order()}}
    _fill_history(rest)
    seen = []
    broker = _broker(rest, intent_resolver=lambda client, order: seen.append((client, order)))
    with pytest.raises(ValidationFailure, match="reused client identity"):
        asyncio.run(broker.open_orders())
    asyncio.run(broker.fills_since(None))
    assert seen == [(None, "order-1")]


def test_known_venue_client_binding_cannot_change_on_read_or_acknowledgement():
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    assert asyncio.run(broker.submit(_intent())).status == "acknowledged"
    rest.results["QueryOrders"] = {"order-1": _order(cl_ord_id="foreign-client")}
    key = OrderLookup(venue_order_id="order-1", symbol="BTC/USD")
    assert asyncio.run(broker.order_status(key)).status == "unknown"
    assert asyncio.run(broker.submit(_intent(client_order_id="foreign-client"))).status == "uncertain"
    _fill_history(rest)
    seen = []
    broker.intent_resolver = lambda client, order: seen.append((client, order))
    asyncio.run(broker.fills_since(None))
    assert seen == [("client-1", "order-1")]


@pytest.mark.parametrize("identifier", ["order-1,foreign-order", "order-1\n", "", "a" * 129])
def test_cancel_rejects_batch_or_malformed_native_identity_before_external_effect(identifier):
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    request = CancelRequest(
        intent_id="intent-1", client_order_id="client-1", venue_order_id=identifier, symbol="BTC/USD"
    )
    # Empty venue identity falls back to the separately validated client identity.
    if not identifier:
        request = request.model_copy(update={"client_order_id": "invalid,client"})
    assert asyncio.run(broker.cancel(request)).status == "rejected"
    assert rest.calls == []


@pytest.mark.parametrize("client", ["invalid,client", "a" * 129, ""])
def test_invalid_client_identity_is_a_typed_pre_submission_refusal(client):
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    before = list(rest.calls)
    assert asyncio.run(broker.submit(_intent(client_order_id=client))).status == "rejected"
    assert rest.calls == before


@pytest.mark.parametrize("problem", ["missing", "extra", "batch_identity", "too_many_refs"])
def test_native_ledger_queries_refuse_incomplete_or_unrequested_identities(problem):
    rest = ScriptedRest()
    _fill_history(rest)
    if problem == "missing":
        del rest.results["QueryLedgers"]["base-1"]
    elif problem == "extra":
        rest.results["QueryLedgers"]["unrequested"] = _ledgers(2)["base-2"]
    elif problem == "batch_identity":
        rest.results["TradesHistory"]["trades"]["trade-1"]["ledgers"][0] = "base-1,foreign"
    else:
        rest.results["TradesHistory"]["trades"]["trade-1"]["ledgers"] = [f"ledger-{index}" for index in range(21)]
    with pytest.raises(ValidationFailure):
        asyncio.run(_broker(rest).fills_since(None))
    if problem in {"batch_identity", "too_many_refs"}:
        assert not any(method == "QueryLedgers" for method, _ in rest.calls)


def test_later_ledger_batch_cannot_replace_an_earlier_native_fee_record():
    rest = ScriptedRest()
    trades = {f"trade-{index}": _trade(index) for index in range(1, 12)}
    native = {key: value for index in range(1, 12) for key, value in _ledgers(index).items()}
    rest.results["TradesHistory"] = {"trades": trades, "count": 11}

    def ledgers(params):
        batch = {key: native[key] for key in params["id"].split(",")}
        if "base-11" in batch:
            batch["quote-1"] = {**native["quote-1"], "fee": "0.01"}
        return batch

    rest.results["QueryLedgers"] = ledgers
    with pytest.raises(ValidationFailure, match="requested identities"):
        asyncio.run(_broker(rest).fills_since(None))


@pytest.mark.parametrize("problem", ["extra_movement", "offsetting_movements", "offsetting_rebate"])
def test_native_movements_or_rebates_cannot_disappear_in_normalized_totals(problem):
    rest = ScriptedRest()
    _fill_history(rest)
    row = _ledgers(1)["quote-1"]
    extras = {"extra-1": {**row, "asset": "ZEUR", "amount": "1", "fee": "0"}}
    if problem == "offsetting_movements":
        extras["extra-2"] = {**extras["extra-1"], "amount": "-1"}
    elif problem == "offsetting_rebate":
        rest.results["QueryLedgers"]["quote-1"]["fee"] = "-0.02"
        extras = {"extra-1": {**row, "amount": "0", "fee": "0.10"}}
    rest.results["TradesHistory"]["trades"]["trade-1"]["ledgers"].extend(extras)
    rest.results["QueryLedgers"].update(extras)
    with pytest.raises(ValidationFailure, match="native trade legs|rebates"):
        asyncio.run(_broker(rest).fills_since(None))


@pytest.mark.parametrize(
    "field,value", [("margin", "1"), ("leverage", "2"), ("posstatus", "open"), ("margin", None), ("leverage", None)]
)
def test_margin_or_missing_spot_provenance_never_reaches_intent_resolution(field, value):
    rest = ScriptedRest()
    _fill_history(rest)
    if value is None:
        del rest.results["TradesHistory"]["trades"]["trade-1"][field]
    else:
        rest.results["TradesHistory"]["trades"]["trade-1"][field] = value
    seen = []
    broker = _broker(rest, intent_resolver=lambda client, order: seen.append((client, order)))
    with pytest.raises(ValidationFailure):
        asyncio.run(broker.fills_since(None))
    assert seen == []


@pytest.mark.parametrize("field", ["live_enabled", "key_present"])
@pytest.mark.parametrize("value", ["false", "true", 1, 0, None])
def test_live_readiness_flags_require_actual_booleans(field, value):
    with pytest.raises(ValueError, match="explicit booleans"):
        _broker(**{field: value})


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None])
def test_transport_write_authority_requires_actual_boolean(value):
    with pytest.raises(ValueError, match="explicit boolean"):
        KrakenRestTransport(allow_order_writes=value)


@pytest.mark.parametrize(
    "wire",
    [
        b'{"error":[],"result":{"fee":"0.1","fee":"0"}}',
        b'{"error":[],"error":[],"result":{}}',
        b'{"error":[],"result":{"fee":NaN}}',
        b'{"error":[],"result":{"fee":Infinity}}',
        b'{"error":[],"result":{"fee":-Infinity}}',
        b'{"error":[],"result":' + b'{"nested":' * 2000 + b"0" + b"}" * 2000 + b"}",
    ],
    ids=["duplicate-native-field", "duplicate-envelope-field", "nan", "infinity", "negative-infinity", "nesting"],
)
@pytest.mark.parametrize("method", ["Assets", "AddOrder"])
def test_malformed_wire_json_never_returns_financial_or_successful_write_data(wire, method):
    requests = []

    async def run():
        def respond(request):
            requests.append(request)
            return httpx.Response(200, content=wire)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            transport = KrakenRestTransport(
                api_key="synthetic-key",
                api_secret=base64.b64encode(b"synthetic").decode(),
                allow_order_writes=True,
                client=client,
            )
            with pytest.raises(KrakenApiError) as failure:
                await transport(method, {})
            assert failure.value.kind == "malformed"
            assert failure.value.uncertain is (method == "AddOrder")

    asyncio.run(run())
    assert len(requests) == 1


def test_json_nesting_guard_preserves_quoted_delimiters_and_escaped_quotes():
    note = '{"' * 2000
    wire = json.dumps({"error": [], "result": {"note": note}}).encode()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=wire))
        ) as client:
            transport = KrakenRestTransport(client=client)
            assert (await transport("Assets", {}))["result"]["note"] == note

    asyncio.run(run())


def test_ambiguous_lookup_and_unowned_fill_survive_sqlite_restart_without_releasing_reservation(tmp_path):
    path = tmp_path / "identity-recovery.sqlite"
    clock = FrozenClock(NOW)
    database = Database(path)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live")
    ledger.deposit(portfolio, "USD", Decimal("100"), "synthetic-opening")
    intent = _intent(portfolio_id=portfolio)
    now = utc_iso(NOW)
    database.execute(
        "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            intent.intent_id,
            portfolio,
            intent.client_order_id,
            "UNKNOWN",
            intent.symbol,
            json.dumps(intent.model_dump(mode="json") | {"reserve_asset": "USD", "reserve_amount": "10.08"}),
            now,
            now,
        ),
    )
    database.execute(
        "INSERT INTO position_reservations VALUES (?, ?, ?, ?, ?, 'held', ?)",
        ("reservation", portfolio, intent.intent_id, "USD", "10.08", now),
    )
    database.close()
    rest = ScriptedRest()
    rest.results["OpenOrders"] = {"open": {"order-2": _order()}}
    rest.results["ClosedOrders"] = {"closed": {"order-1": _order(status="closed", vol_exec="0.1")}, "count": 1}
    _fill_history(rest)
    for _ in range(2):
        recovered = Database(path)
        ledger = Ledger(recovered, clock)

        def resolve(client, order):
            row = recovered.execute(
                "SELECT intent_id FROM order_intents WHERE client_order_id = ?", (client,)
            ).fetchone()
            return row["intent_id"] if row else None

        broker = _broker(rest, intent_resolver=resolve)
        execution = Execution(
            recovered, ledger, clock, broker, venue="kraken", account_id="synthetic-account", mode="live"
        )
        with pytest.raises(UncertainExternal, match="unowned fills"):
            asyncio.run(execution.reconcile())
        assert execution.intent_state(intent.intent_id) == "UNKNOWN"
        reservation = recovered.execute("SELECT state, amount FROM position_reservations").fetchone()
        assert (reservation["state"], reservation["amount"]) == ("held", "10.08")
        assert recovered.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
        assert ledger.books(portfolio).cash_amount("USD") == Decimal("100")
        recovered.close()
    # Once read-only evidence becomes unambiguous, a fresh process books the
    # genuine fee and fill once, releases the reservation and clears the gate.
    rest.results["OpenOrders"] = {"open": {}}
    recovered = Database(path)
    ledger = Ledger(recovered, clock)
    broker = _broker(rest, intent_resolver=resolve)
    execution = Execution(recovered, ledger, clock, broker, venue="kraken", account_id="synthetic-account", mode="live")
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent.intent_id) == "FILLED"
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("89.92")
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.1")
    assert recovered.execute("SELECT state FROM position_reservations").fetchone()["state"] == "released"
    asyncio.run(execution.reconcile())
    assert recovered.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    assert (
        recovered.execute("SELECT count(*) FROM activity_events WHERE kind='unreconciled_broker_fill'").fetchone()[0]
        == 1
    )
    recovered.close()
    assert not any(method in {"AddOrder", "CancelOrder"} for method, _ in rest.calls)
