"""Synthetic wire conformance and durable accounting; no private venue calls."""

import asyncio
import base64
import hashlib
import hmac
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from trade_graph.adapters.brokers.kraken_live import KrakenLiveBroker
from trade_graph.adapters.brokers.kraken_transport import KrakenApiError, KrakenRestTransport, MonotonicNonce
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.broker_identity import DurableBrokerIdentity
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import AuthorizedOrderIntent, CancelRequest, OrderLookup
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, LiveDisabled, UncertainExternal, ValidationFailure
from trade_graph.domain.protocols import Broker

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _assets():
    return {"XXBT": {"altname": "XBT"}, "ZUSD": {"altname": "USD"}, "ZEUR": {"altname": "EUR"}}


def _pairs():
    return {
        "XXBTZUSD": {
            "altname": "XBTUSD",
            "wsname": "XBT/USD",
            "base": "XXBT",
            "quote": "ZUSD",
            "pair_decimals": 1,
            "lot_decimals": 8,
            "tick_size": "0.1",
            "ordermin": "0.0001",
            "costmin": "0.5",
            "status": "online",
            "fees": [["0", "0.8"], ["50000", "0.6"]],
            "fees_maker": [["0", "0.4"], ["50000", "0.25"]],
        }
    }


def _order(**updates):
    row = {
        "status": "open",
        "vol": "0.1",
        "vol_exec": "0",
        "cl_ord_id": "client-1",
        "descr": {"pair": "XBTUSD", "type": "buy", "leverage": "none"},
    }
    row.update(updates)
    return row


def _intent(**updates):
    row = dict(
        intent_id="intent-1",
        portfolio_id="portfolio",
        account_id="synthetic-account",
        venue="kraken",
        mode="live",
        client_order_id="client-1",
        symbol="BTC/USD",
        side="buy",
        order_type="limit",
        quantity="0.1",
        limit_price="100",
        snapshot_id="snapshot",
        eligible_after_utc=NOW,
    )
    row.update(updates)
    return AuthorizedOrderIntent(**row)


def _trade(index, *, time="1767225600", side="buy", fee="0.08", maker=False):
    return {
        "ordertxid": f"order-{index}",
        "pair": "XXBTZUSD",
        "time": time,
        "type": side,
        "price": "100",
        "vol": "0.1",
        "cost": "10",
        "fee": fee,
        "maker": maker,
        "margin": "0",
        "leverage": "0",
        "ledgers": [f"base-{index}", f"quote-{index}"],
    }


def _ledgers(index, *, asset="ZUSD", fee="0.08", side="buy"):
    return {
        f"base-{index}": {
            "refid": f"trade-{index}",
            "type": "trade",
            "asset": "XXBT",
            "fee": "0",
            "amount": "0.1" if side == "buy" else "-0.1",
        },
        f"quote-{index}": {
            "refid": f"trade-{index}",
            "type": "trade",
            "asset": asset,
            "fee": fee,
            "amount": "-10" if side == "buy" else "10",
        },
    }


class ScriptedRest:
    """The whole call log and every account identifier are synthetic."""

    def __init__(self):
        self.calls = []
        self.results = {
            "Assets": _assets(),
            "AssetPairs": _pairs(),
            "BalanceEx": {
                "ZUSD": {"balance": "100", "hold_trade": "10"},
                "XXBT": {"balance": "0.2", "hold_trade": "0.1"},
                "USD.F": {"balance": "5", "hold_trade": "0"},
            },
            "OpenOrders": {"open": {}},
            "ClosedOrders": {"closed": {}, "count": 0},
            "QueryOrders": {},
            "TradesHistory": {"trades": {}, "count": 0},
            "QueryLedgers": {},
            "AddOrder": {"txid": ["order-1"]},
            "CancelOrder": {"count": 1},
        }

    def __call__(self, method, params):
        self.calls.append((method, deepcopy(params)))
        result = self.results[method]
        if callable(result):
            result = result(params)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, dict) and "error" in result:
            return result
        return {"error": [], "result": deepcopy(result)}


def _broker(transport=None, **kwargs):
    return KrakenLiveBroker(
        transport or ScriptedRest(), account_id="synthetic-account", clock=FrozenClock(NOW), **kwargs
    )


def test_metadata_normalizes_aliases_precision_minimums_and_conservative_fees():
    broker = _broker()
    assert isinstance(broker, Broker)
    assert broker.fee_reserve_rate is None
    rules = asyncio.run(broker.instruments())[0]
    assert (rules.symbol, rules.base_asset, rules.quote_asset) == ("BTC/USD", "BTC", "USD")
    assert rules.price_increment == Decimal("0.1")
    assert rules.quantity_increment == Decimal("0.00000001")
    assert rules.min_quantity == Decimal("0.0001")
    assert rules.min_notional == Decimal("0.5")
    assert rules.synthetic is False
    assert broker.fee_reserve_rate == Decimal("0.008")
    assert asyncio.run(broker.fee_schedule())["BTC/USD"]["maker"] == Decimal("0.004")
    caps = asyncio.run(broker.capabilities())
    assert caps.native_stop_tested is False
    assert caps.withdrawals is False
    assert not hasattr(broker, "withdraw")


def test_total_balances_include_held_spot_funds_and_preserve_earn_suffix_assets():
    broker = _broker()
    balance = asyncio.run(broker.balances())
    assert balance.account_id == "synthetic-account"
    assert balance.amounts == {"USD": "100", "BTC": "0.2", "USD.F": "5"}
    assert balance.as_of_utc == NOW
    assert broker.balance_holds == {"USD": "10", "BTC": "0.1", "USD.F": "0"}
    assert broker.available_balances == {"USD": "90", "BTC": "0.1", "USD.F": "5"}


def test_margin_credit_and_binary_float_values_are_not_silently_normalized():
    rest = ScriptedRest()
    rest.results["BalanceEx"]["ZUSD"]["credit_used"] = "1"
    with pytest.raises(ValidationFailure, match="margin balances"):
        asyncio.run(_broker(rest).balances())
    rest.results["AssetPairs"]["XXBTZUSD"]["tick_size"] = 0.1
    with pytest.raises(ValidationFailure, match="numeric field"):
        asyncio.run(_broker(rest).instruments())


def test_readonly_broker_never_attempts_submission_or_cancellation():
    rest = ScriptedRest()
    broker = _broker(rest)
    with pytest.raises(LiveDisabled):
        asyncio.run(broker.submit(_intent()))
    with pytest.raises(LiveDisabled):
        asyncio.run(broker.cancel(CancelRequest(intent_id="intent", client_order_id="client", symbol="BTC/USD")))
    assert rest.calls == []


def test_open_and_cancelled_orders_keep_partial_fill_quantities_and_uuid_identity():
    rest = ScriptedRest()
    client = "12345678-1234-1234-1234-123456789abc"
    rest.results["OpenOrders"] = {"open": {"order-1": _order(vol_exec="0.04", cl_ord_id=client)}}
    broker = _broker(rest)
    orders = asyncio.run(broker.open_orders())
    assert orders[0].client_order_id == client.replace("-", "")
    assert orders[0].status == "partially_filled"
    assert orders[0].remaining_quantity == Decimal("0.06")
    rest.results["OpenOrders"] = {"open": {}}
    rest.results["ClosedOrders"] = {
        "closed": {"order-1": _order(status="canceled", vol_exec="0.04", cl_ord_id=client)},
        "count": 1,
    }
    status = asyncio.run(broker.order_status(OrderLookup(client_order_id=client.replace("-", ""), symbol="BTC/USD")))
    assert status.status == "cancelled"
    assert status.filled_quantity == Decimal("0.04")
    assert status.venue_order_id == "order-1"
    assert all("cl_ord_id" not in body for method, body in rest.calls if method == "QueryOrders")


def test_venue_lookup_uses_txid_and_never_arbitrary_first_result():
    rest = ScriptedRest()
    rest.results["QueryOrders"] = {
        "unrelated": _order(),
        "wanted": _order(status="closed", vol_exec="0.1"),
    }
    broker = _broker(rest)
    status = asyncio.run(broker.order_status(OrderLookup(venue_order_id="wanted", symbol="BTC/USD")))
    assert status.status == "filled"
    assert status.venue_order_id == "wanted"
    assert rest.calls[-1] == ("QueryOrders", {"txid": "wanted", "trades": "false"})


def test_empty_sweep_unknown_id_and_incomplete_filtered_history_remain_unknown():
    rest = ScriptedRest()
    broker = _broker(rest)
    key = OrderLookup(client_order_id="client-1", symbol="BTC/USD")
    assert asyncio.run(broker.order_status(key)).status == "unknown"
    rest.results["ClosedOrders"]["count"] = 100
    status = asyncio.run(broker.order_status(key))
    assert status.status == "unknown" and status.error == "lookup_not_supported"
    rest.results["QueryOrders"] = {"error": ["EOrder:Invalid order"]}
    assert asyncio.run(broker.order_status(OrderLookup(venue_order_id="gone", symbol="BTC/USD"))).status == "unknown"
    assert asyncio.run(broker.order_status(OrderLookup(symbol="BTC/USD"))).error == "lookup_not_supported"


def test_filtered_lookup_will_not_match_another_client_or_reused_client_id():
    rest = ScriptedRest()
    rest.results["OpenOrders"] = {"open": {"foreign": _order(cl_ord_id="foreign-client")}}
    broker = _broker(rest)
    assert (
        asyncio.run(broker.order_status(OrderLookup(client_order_id="client-1", symbol="BTC/USD"))).status == "unknown"
    )
    rest.results["OpenOrders"] = {"open": {"a": _order(), "b": _order()}}
    assert (
        asyncio.run(broker.order_status(OrderLookup(client_order_id="client-1", symbol="BTC/USD"))).status == "unknown"
    )


def test_native_base_fee_comes_from_linked_ledger_not_quote_fee_division():
    rest = ScriptedRest()
    rest.results["TradesHistory"] = {"trades": {"trade-1": _trade(1)}, "count": 1}
    native = _ledgers(1)
    native["base-1"]["fee"] = "0.00079"
    native["quote-1"]["fee"] = "0"
    rest.results["QueryLedgers"] = native
    broker = _broker(rest, intent_resolver=lambda client, txid: "intent-1" if txid == "order-1" else None)
    page = asyncio.run(broker.fills_since(None))
    fill = page.fills[0]
    assert fill.trade_id == "trade-1" and fill.intent_id == "intent-1"
    assert fill.fee_asset == "BTC" and fill.fee_amount == Decimal("0.00079")
    assert fill.fee_amount != Decimal("0.08") / fill.price
    assert fill.heuristic is False
    assert rest.calls[-1] == ("QueryLedgers", {"id": "base-1,quote-1"})


@pytest.mark.parametrize(
    "problem", ["missing_ledger", "multiple_assets", "foreign_ref", "missing_maker", "rounded_cost"]
)
def test_unsupported_fee_provenance_and_cost_shapes_are_explicitly_unresolved(problem):
    rest = ScriptedRest()
    rest.results["TradesHistory"] = {"trades": {"trade-1": _trade(1)}, "count": 1}
    rest.results["QueryLedgers"] = _ledgers(1)
    if problem == "missing_ledger":
        del rest.results["QueryLedgers"]["quote-1"]
    elif problem == "multiple_assets":
        rest.results["QueryLedgers"]["base-1"]["fee"] = "0.001"
    elif problem == "foreign_ref":
        rest.results["QueryLedgers"]["quote-1"]["refid"] = "foreign"
    elif problem == "missing_maker":
        del rest.results["TradesHistory"]["trades"]["trade-1"]["maker"]
    else:
        rest.results["TradesHistory"]["trades"]["trade-1"]["cost"] = "9.99"
    with pytest.raises(ValidationFailure):
        asyncio.run(_broker(rest).fills_since(None))


def test_fills_page_chronologically_stable_ids_and_cursor_survive_adapter_restart():
    rest = ScriptedRest()
    # A full offset page in arbitrary newest-first order. Entire bounded history
    # must be sorted before it reaches the ledger's FIFO path.
    rows = {f"trade-{index:03d}": _trade(index, time=str(1767225499 + index)) for index in range(1, 52)}
    ledger_rows = {}
    for index in range(1, 52):
        for name, value in _ledgers(index).items():
            value["refid"] = f"trade-{index:03d}"
            ledger_rows[name] = value
    ids = list(reversed(rows))
    rest.results["TradesHistory"] = lambda params: {
        "count": 51,
        "trades": {txid: rows[txid] for txid in ids[int(params["ofs"]) : int(params["ofs"]) + 50]},
    }
    rest.results["QueryLedgers"] = lambda params: {key: ledger_rows[key] for key in params["id"].split(",")}
    broker = _broker(rest)
    first = asyncio.run(broker.fills_since(None))
    assert len(first.fills) == 50
    assert first.fills[0].trade_id == "trade-001"
    assert first.fills[-1].trade_id == "trade-050"
    assert first.next_cursor
    restarted = _broker(rest)
    last = asyncio.run(restarted.fills_since(first.next_cursor))
    assert [fill.trade_id for fill in last.fills] == ["trade-051"]
    assert last.next_cursor is None
    assert len({fill.trade_id for fill in first.fills + last.fills}) == 51
    for method, params in rest.calls:
        if method == "TradesHistory":
            assert params["end"] == "1767225600"
            assert params["ledgers"] == "true"
            assert params["consolidate_taker"] == "false"


def test_history_bounds_count_changes_and_cursor_garbage_never_return_partial_success():
    rest = ScriptedRest()
    rest.results["TradesHistory"] = {"trades": {}, "count": 51}
    with pytest.raises(UncertainExternal, match="page bound"):
        asyncio.run(_broker(rest, maximum_history_pages=1).fills_since(None))
    with pytest.raises(ValidationFailure, match="cursor"):
        asyncio.run(_broker(rest).fills_since("foreign-cursor"))
    token = json.dumps({"end": "1767225600", "offset": 50, "count": 51})
    cursor = "kraken-trades-v1:" + base64.urlsafe_b64encode(token.encode()).decode()
    rest.results["TradesHistory"] = {"trades": {}, "count": 52}
    with pytest.raises(UncertainExternal, match="history changed"):
        asyncio.run(_broker(rest).fills_since(cursor))


def test_writes_require_ready_precision_fee_and_untested_protection_checks():
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    assert asyncio.run(broker.submit(_intent(quantity="0.00001"))).status == "rejected"
    assert asyncio.run(broker.submit(_intent(limit_price="100.01"))).status == "rejected"
    assert asyncio.run(broker.submit(_intent(order_type="stop", stop_price="99"))).status == "rejected"
    assert asyncio.run(broker.submit(_intent(reduce_only=True))).status == "rejected"
    assert not any(method == "AddOrder" for method, _ in rest.calls)
    result = asyncio.run(broker.submit(_intent()))
    assert result.status == "acknowledged" and result.venue_order_id == "order-1"
    body = rest.calls[-1][1]
    assert body["pair"] == "XXBTZUSD" and body["timeinforce"] == "gtc"
    assert body["price"] == "100" and body["volume"] == "0.1"
    assert body["oflags"] == "fciq"
    assert "leverage" not in body and "reduce_only" not in body


def test_missing_pair_status_prevents_writes_and_lost_ack_never_becomes_rejection():
    rest = ScriptedRest()
    del rest.results["AssetPairs"]["XXBTZUSD"]["status"]
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    assert asyncio.run(broker.submit(_intent())).status == "rejected"
    assert not any(method == "AddOrder" for method, _ in rest.calls)
    rest.results["AssetPairs"]["XXBTZUSD"]["status"] = "online"
    asyncio.run(broker.instruments())
    for response in ({"txid": []}, {"error": ["EService:Unavailable"]}, TimeoutError("synthetic")):
        rest.results["AddOrder"] = response
        assert asyncio.run(broker.submit(_intent())).status == "uncertain"
    rest.results["AddOrder"] = {"error": ["EOrder:Insufficient funds"]}
    assert asyncio.run(broker.submit(_intent())).status == "rejected"


@pytest.mark.parametrize("time_in_force", ["gtc", "ioc"])
def test_time_in_force_matches_current_official_rest_sdk_form(time_in_force):
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    assert asyncio.run(broker.submit(_intent(time_in_force=time_in_force))).status == "acknowledged"
    assert rest.calls[-1][1]["timeinforce"] == time_in_force


def test_cancel_uses_one_identifier_and_always_requires_followup():
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    request = CancelRequest(intent_id="intent", client_order_id="client-1", venue_order_id="order-1", symbol="BTC/USD")
    assert asyncio.run(broker.cancel(request)).status == "cancel_pending"
    assert rest.calls[-1] == ("CancelOrder", {"txid": "order-1"})
    rest.results["CancelOrder"] = {"error": ["EOrder:Invalid order"]}
    assert asyncio.run(broker.cancel(request)).status == "uncertain"


def test_rest_auth_signs_exact_body_and_read_only_transport_denies_writes_and_withdrawals():
    requests = []
    secret = base64.b64encode(b"synthetic-private-secret").decode()

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"error": [], "result": {}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            transport = KrakenRestTransport(
                api_key="synthetic-key", api_secret=secret, client=client, nonce=lambda: 123
            )
            await transport("QueryOrders", {"txid": "synthetic-order"})
            for method in ("AddOrder", "CancelOrder", "Withdraw", "DepositMethods"):
                with pytest.raises(AuthorityDenied):
                    await transport(method, {})
            with pytest.raises(AuthorityDenied):
                await transport("BalanceEx", {"nonce": 999})

    asyncio.run(run())
    assert len(requests) == 1
    request = requests[0]
    body = request.content.decode()
    assert parse_qs(body) == {"nonce": ["123"], "txid": ["synthetic-order"]}
    digest = hashlib.sha256(b"123" + request.content).digest()
    expected = base64.b64encode(
        hmac.new(b"synthetic-private-secret", request.url.path.encode() + digest, hashlib.sha512).digest()
    ).decode()
    assert request.headers["API-Sign"] == expected
    assert request.headers["API-Key"] == "synthetic-key"


def test_rest_transport_parses_decimal_fields_and_redacts_error_context_without_retry():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("Assets"):
            return httpx.Response(200, content=b'{"error":[],"result":{"amount":0.1}}')
        raise httpx.ReadTimeout("private detail must not leave transport", request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            transport = KrakenRestTransport(
                api_key="synthetic-key",
                api_secret=base64.b64encode(b"synthetic").decode(),
                allow_order_writes=True,
                client=client,
            )
            result = await transport("Assets", {})
            assert result["result"]["amount"] == Decimal("0.1")
            with pytest.raises(KrakenApiError) as failure:
                await transport("AddOrder", {})
            assert failure.value.uncertain is True
            assert "private detail" not in str(failure.value)

    asyncio.run(run())
    assert len(requests) == 2


def test_monotonic_nonce_serializes_clock_regressions_and_repeats():
    ticks = iter([1000, 1000, 0, 10000])
    nonce = MonotonicNonce(lambda: next(ticks))
    assert [nonce() for _ in range(4)] == [1, 2, 3, 10]


def test_async_injected_transport_and_malformed_read_fields_produce_typed_failures():
    scripted = ScriptedRest()

    async def transport(method, params):
        return scripted(method, params)

    broker = _broker(transport)
    assert asyncio.run(broker.balances()).amounts["USD"] == "100"
    scripted.results["OpenOrders"] = {"open": {"order": {"private-secret": "must-not-leak"}}}
    with pytest.raises(ValidationFailure) as failure:
        asyncio.run(broker.open_orders())
    assert "private-secret" not in str(failure.value)
    assert "must-not-leak" not in str(failure.value)
    scripted.results["QueryOrders"] = {"wanted": {}}
    assert asyncio.run(broker.order_status(OrderLookup(venue_order_id="wanted", symbol="BTC/USD"))).status == "unknown"


def test_missing_and_warning_order_acknowledgements_are_uncertain_and_error_text_is_safe():
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())
    for response in (
        {"error": ["WService:private-secret must-not-leak"], "result": {"txid": ["order-1"]}},
        {"error": [], "result": {}},
        {"error": ["EGeneral:Internal error:private-secret"]},
    ):
        rest.results["AddOrder"] = response
        result = asyncio.run(broker.submit(_intent()))
        assert result.status == "uncertain"
        assert "private-secret" not in result.message
        assert "must-not-leak" not in result.message


def test_readonly_rest_transport_has_no_credential_or_endpoint_side_effect():
    requested = []

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: requested.append(request) or httpx.Response(200, json={"error": [], "result": {}}),
            )
        ) as client:
            transport = KrakenRestTransport(client=client)
            with pytest.raises(AuthorityDenied, match="credential"):
                await transport("BalanceEx", {})
            with pytest.raises(AuthorityDenied, match="allowlist"):
                await transport("Withdraw", {})

    asyncio.run(run())
    assert requested == []


@pytest.mark.parametrize("http_status", [301, 403, 429, 500])
def test_transport_does_not_follow_redirects_or_retry_http_failures(http_status):
    requested = []

    async def run():
        def respond(request):
            requested.append(request)
            return httpx.Response(http_status, headers={"location": "https://example.invalid/private"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            transport = KrakenRestTransport(client=client)
            with pytest.raises(KrakenApiError):
                await transport("Assets", {})

    asyncio.run(run())
    assert len(requested) == 1


def test_duplicate_ledger_references_cannot_double_native_fees_or_hide_a_missing_leg():
    rest = ScriptedRest()
    trade = _trade(1)
    trade["ledgers"] = ["base-1", "base-1"]
    rest.results["TradesHistory"] = {"trades": {"trade-1": trade}, "count": 1}
    native = _ledgers(1)
    native["base-1"]["fee"] = "0.00079"
    rest.results["QueryLedgers"] = native
    with pytest.raises(ValidationFailure, match="ledger references"):
        asyncio.run(_broker(rest).fills_since(None))
    trade["ledgers"] = ["base-1"]
    rest.results["QueryLedgers"] = {"base-1": native["base-1"]}
    with pytest.raises(ValidationFailure, match="native trade legs"):
        asyncio.run(_broker(rest).fills_since(None))


def test_exact_wire_chronology_precedes_dto_microsecond_truncation():
    rest = ScriptedRest()
    early = _trade(1, time="1767225599.0000001")
    later = _trade(2, time="1767225599.0000002", side="sell")
    rest.results["TradesHistory"] = {"trades": {"trade-a-sell": later, "trade-z-buy": early}, "count": 2}
    native = {**_ledgers(1), **_ledgers(2, side="sell")}
    for name, row in native.items():
        row["refid"] = "trade-z-buy" if name.endswith("-1") else "trade-a-sell"
    rest.results["QueryLedgers"] = native
    fills = asyncio.run(_broker(rest).fills_since(None)).fills
    assert [fill.side for fill in fills] == ["buy", "sell"]
    assert fills[0].filled_at_utc == fills[1].filled_at_utc


def test_native_amount_disagreement_and_duplicate_balance_aliases_are_explicit():
    rest = ScriptedRest()
    rest.results["TradesHistory"] = {"trades": {"trade-1": _trade(1)}, "count": 1}
    rest.results["QueryLedgers"] = _ledgers(1)
    rest.results["QueryLedgers"]["quote-1"]["amount"] = "-11"
    with pytest.raises(ValidationFailure, match="native trade legs"):
        asyncio.run(_broker(rest).fills_since(None))
    rest.results["BalanceEx"]["USD"] = {"balance": "100", "hold_trade": "10"}
    with pytest.raises(ValidationFailure, match="duplicate asset aliases"):
        asyncio.run(_broker(rest).balances())


def test_submission_never_loads_unknown_or_expired_fees_inside_write_path():
    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    assert asyncio.run(broker.submit(_intent())).status == "rejected"
    assert rest.calls == []
    asyncio.run(broker.instruments())
    before = list(rest.calls)
    broker.clock.advance(301)
    assert asyncio.run(broker.submit(_intent())).status == "rejected"
    assert rest.calls == before


def test_account_fee_lookup_cannot_lower_conservative_execution_reserve_bound():
    rest = ScriptedRest()
    rest.results["TradeVolume"] = {"fees": {"XXBTZUSD": {"fee": "0.2"}}, "fees_maker": {"XXBTZUSD": {"fee": "0.1"}}}
    broker = _broker(rest)
    schedule = asyncio.run(broker.fee_schedule(account_specific=True))
    assert schedule["BTC/USD"]["taker"] == Decimal("0.002")
    assert broker.fee_reserve_rate == Decimal("0.008")
    assert rest.calls[-1] == ("TradeVolume", {"pair": "XXBTZUSD", "fee-info": "true"})


def test_transport_stops_stream_before_download_limit_and_enforces_total_deadline():
    streams = []

    class Chunks(httpx.AsyncByteStream):
        def __init__(self, *, delayed=False):
            self.seen, self.closed, self.delayed = 0, False, delayed

        async def __aiter__(self):
            for _ in range(100):
                self.seen += 1
                if self.delayed:
                    await asyncio.sleep(0.005)
                yield b" " * 16

        async def aclose(self):
            self.closed = True

    async def run():
        def respond(request):
            stream = Chunks(delayed=bool(streams))
            streams.append(stream)
            return httpx.Response(200, stream=stream)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            bounded = KrakenRestTransport(client=client, maximum_response_bytes=32)
            with pytest.raises(KrakenApiError, match="normalized safely"):
                await bounded("Assets", {})
            deadline = KrakenRestTransport(client=client, timeout_seconds=0.01)
            with pytest.raises(KrakenApiError, match="unavailable"):
                await deadline("Assets", {})

    asyncio.run(run())
    assert all(stream.closed for stream in streams)
    assert all(stream.seen < 100 for stream in streams)


@pytest.mark.parametrize(
    "value",
    [
        "1e999999999",
        "0e-999999999",
        "1e-999999999",
        "1e30",
        "1e-19",
        Decimal("1e999999999"),
        Decimal("0e-999999999"),
        "1" * 97,
        True,
        False,
    ],
)
def test_wire_numeric_shape_bounds_precede_fixed_formatting(value, monkeypatch):
    import trade_graph.adapters.brokers.kraken_live as adapter

    rest = ScriptedRest()
    rest.results["BalanceEx"]["ZUSD"]["balance"] = value

    def forbidden_format(number):
        raise AssertionError("unsupported wire number reached fixed-point formatting")

    monkeypatch.setattr(adapter, "canonical_decimal", forbidden_format)
    with pytest.raises(ValidationFailure, match="numeric field"):
        asyncio.run(_broker(rest).balances())


def test_oversized_source_fields_are_refused_before_decimal_construction(monkeypatch):
    import trade_graph.adapters.brokers.kraken_live as adapter

    def forbidden_parse(value):
        raise AssertionError("oversized field reached Decimal construction")

    monkeypatch.setattr(adapter, "parse_decimal", forbidden_parse)
    for value in ("1" * 97, 1 << 4096):
        with pytest.raises(ValidationFailure):
            adapter._decimal(value)


@pytest.mark.parametrize("field", ["pair_decimals", "lot_decimals", "cost_decimals"])
@pytest.mark.parametrize("value", ["1e999999999", "0e-999999999", 19, -1, True, False])
def test_metadata_precision_is_bounded_even_with_explicit_tick_size(field, value):
    rest = ScriptedRest()
    rest.results["AssetPairs"]["XXBTZUSD"][field] = value
    with pytest.raises(ValidationFailure):
        asyncio.run(_broker(rest).instruments())


@pytest.mark.parametrize("value", [True, False, "1e999999999", 2])
def test_lot_multiplier_refuses_boolean_and_unsupported_numeric_values(value):
    rest = ScriptedRest()
    rest.results["AssetPairs"]["XXBTZUSD"]["lot_multiplier"] = value
    with pytest.raises(ValidationFailure):
        asyncio.run(_broker(rest).instruments())


@pytest.mark.parametrize("value", ["1e999999999", "0e-999999999", "18446744073709551616", True, False])
def test_history_counts_are_bounded_before_integer_materialization(value):
    rest = ScriptedRest()
    rest.results["TradesHistory"] = {"trades": {}, "count": value}
    with pytest.raises(ValidationFailure):
        asyncio.run(_broker(rest).fills_since(None))
    assert not any(method == "QueryLedgers" for method, _ in rest.calls)


@pytest.mark.parametrize(
    "updates",
    [
        {"quantity": "1e999999999"},
        {"quantity": "0e-999999999"},
        {"limit_price": "1e999999999"},
        {"limit_price": "0e-999999999"},
        {"order_type": "market", "limit_price": "0e-999999999"},
        {"order_type": "market", "limit_price": "100"},
        {"order_type": "stop", "stop_price": "1e999999999"},
    ],
)
def test_outbound_typed_intents_are_bounded_before_arithmetic_or_fixed_formatting(updates, monkeypatch):
    import trade_graph.adapters.brokers.kraken_live as adapter

    rest = ScriptedRest()
    broker = _broker(rest, live_enabled=True, key_present=True)
    asyncio.run(broker.instruments())

    def forbidden_format(number):
        raise AssertionError("unsupported typed intent reached fixed-point formatting")

    monkeypatch.setattr(adapter, "canonical_decimal", forbidden_format)
    assert asyncio.run(broker.submit(_intent(**updates))).status == "rejected"
    assert not any(method == "AddOrder" for method, _ in rest.calls)


def test_declared_native_precision_is_exact_without_changing_caller_context():
    from decimal import getcontext

    rest = ScriptedRest()
    source = "12345678901234567890.123456789012345678"
    rest.results["BalanceEx"]["ZUSD"] = {"balance": source, "hold_trade": "0.000000000000000001"}
    broker = _broker(rest)
    precision = getcontext().prec
    assert asyncio.run(broker.balances()).amounts["USD"] == source
    assert broker.available_balances["USD"] == "12345678901234567890.123456789012345677"
    rest.results["OpenOrders"] = {"open": {"order-1": _order(vol=source, vol_exec="0.000000000000000001")}}
    assert asyncio.run(broker.open_orders())[0].remaining_quantity == Decimal(broker.available_balances["USD"])
    assert getcontext().prec == precision


def test_precision_boundaries_preserve_native_quantums_and_integer_limit():
    from trade_graph.adapters.brokers.kraken_live import _decimal, _integer

    assert _decimal("1e-18") == Decimal("0.000000000000000001")
    assert _decimal("999999999999999999999999999999") == Decimal("999999999999999999999999999999")
    assert _integer("18446744073709551615") == 2**64 - 1
    rest = ScriptedRest()
    pair = rest.results["AssetPairs"]["XXBTZUSD"]
    pair.update(pair_decimals=18, lot_decimals=18, cost_decimals=18)
    del pair["tick_size"]
    rule = asyncio.run(_broker(rest).instruments())[0]
    assert rule.price_increment == Decimal("1e-18")
    assert rule.quantity_increment == Decimal("1e-18")


@pytest.mark.parametrize(
    "quantity,price,cost,fee_asset,fee,side",
    [
        (
            "12345678901234567890.123456789012345678",
            "1",
            "12345678901234567890.123456789012345678",
            "ZUSD",
            "0",
            "buy",
        ),
        ("1234567890123.45", "1234567890123.45", "1524157875323866912056239.9025", "ZUSD", "0", "buy"),
        ("1", "12345678901234567890.12345678", "12345678901234567890.12345678", "ZUSD", "0.000000001", "buy"),
        ("12345678901234567890.12345678", "1", "12345678901234567890.12345678", "XXBT", "0.000000001", "buy"),
        ("12345678901234567890.12345678", "1", "12345678901234567890.12345678", "XXBT", "0.000000001", "sell"),
    ],
    ids=["quantity", "quantity-price-product", "quote-fee-addition", "base-fee-subtraction", "base-fee-addition"],
)
def test_unrepresentable_native_fills_refuse_before_real_ledger_changes(
    tmp_path, quantity, price, cost, fee_asset, fee, side
):
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "precision-fixture.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live")
    ledger.deposit(portfolio, "USD", Decimal("1e30"), "synthetic-opening")
    intent = _intent(portfolio_id=portfolio, quantity=quantity, limit_price=price, side=side)
    database.execute(
        "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            intent.intent_id,
            portfolio,
            intent.client_order_id,
            "UNKNOWN",
            intent.symbol,
            intent.model_dump_json(),
            utc_iso(clock.now()),
            utc_iso(clock.now()),
        ),
    )
    rest = ScriptedRest()
    rest.results["ClosedOrders"] = {
        "closed": {"order-1": _order(status="closed", vol=quantity, vol_exec=quantity)},
        "count": 1,
    }
    trade = _trade(1, side=side, fee=fee if fee_asset == "ZUSD" else "0")
    trade.update(vol=quantity, price=price, cost=cost)
    rest.results["TradesHistory"] = {"trades": {"trade-1": trade}, "count": 1}
    native = _ledgers(1)
    native["base-1"].update(
        amount=quantity if side == "buy" else "-" + quantity, fee=fee if fee_asset == "XXBT" else "0"
    )
    native["quote-1"].update(amount="-" + cost if side == "buy" else cost, fee=fee if fee_asset == "ZUSD" else "0")
    rest.results["QueryLedgers"] = native
    resolutions = []

    def resolve(client, order):
        resolutions.append((client, order))
        return intent.intent_id

    broker = _broker(rest, intent_resolver=resolve)
    execution = Execution(database, ledger, clock, broker, venue="kraken", account_id="synthetic-account", mode="live")
    execution.register_instrument(asyncio.run(broker.instruments())[0])
    before = database.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
    with pytest.raises(ValidationFailure, match="native fill precision"):
        asyncio.run(execution.reconcile())
    assert database.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == before
    assert database.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("1e30")
    assert ledger.books(portfolio).lots == []
    assert resolutions == []
    assert execution.intent_state(intent.intent_id) == "UNKNOWN"
    assert not any(method in {"AddOrder", "CancelOrder"} for method, _ in rest.calls)
    database.close()


def test_native_fill_precision_guard_accepts_exact_values_with_trailing_zeros():
    rest = ScriptedRest()
    source = "12345678901234567890.123456780000000000"
    trade = _trade(1, fee="0")
    trade.update(vol="1", price=source, cost=source)
    rest.results["TradesHistory"] = {"trades": {"trade-1": trade}, "count": 1}
    native = _ledgers(1, fee="0")
    native["base-1"]["amount"] = "1"
    native["quote-1"]["amount"] = "-" + source
    rest.results["QueryLedgers"] = native
    fill = asyncio.run(_broker(rest).fills_since(None)).fills[0]
    assert fill.price == Decimal(source)
    assert fill.quantity == Decimal("1")
    assert fill.fee_amount == 0


def test_kraken_fill_reconciliation_survives_restart_through_real_ledger_without_resubmit(tmp_path):
    clock = FrozenClock(NOW)
    path = tmp_path / "live-fixture.sqlite"
    database = Database(path)
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="USD", mode="live")
    ledger.deposit(portfolio, "USD", Decimal("100"), "synthetic-opening")
    intent = _intent(portfolio_id=portfolio, intent_id="durable-intent")
    database.execute(
        "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            intent.intent_id,
            portfolio,
            intent.client_order_id,
            "UNKNOWN",
            intent.symbol,
            intent.model_dump_json(),
            utc_iso(clock.now()),
            utc_iso(clock.now()),
        ),
    )
    rest = ScriptedRest()
    rest.results["ClosedOrders"] = {"closed": {"order-1": _order(status="closed", vol_exec="0.1")}, "count": 1}
    rest.results["TradesHistory"] = {"trades": {"trade-1": _trade(1)}, "count": 1}
    rest.results["QueryLedgers"] = _ledgers(1)
    database.close()
    recovered = Database(path)
    ledger = Ledger(recovered, clock)

    identity = DurableBrokerIdentity(recovered, venue="kraken", account_id="synthetic-account", mode="live")
    broker = _broker(rest, intent_resolver=identity)
    execution = Execution(recovered, ledger, clock, broker, venue="kraken", account_id="synthetic-account", mode="live")
    execution.register_instrument(asyncio.run(broker.instruments())[0])
    asyncio.run(execution.reconcile())
    assert execution.intent_state(intent.intent_id) == "FILLED"
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("89.92")
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.1")
    asyncio.run(execution.reconcile())
    assert recovered.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    assert not any(method in {"AddOrder", "CancelOrder"} for method, _ in rest.calls)
    recovered.close()
    # A terminal intent no longer receives active-order lookups on startup.
    # Ownership must survive through its durable venue ID with a cold cache.
    recovered = Database(path)
    ledger = Ledger(recovered, clock)
    identity = DurableBrokerIdentity(recovered, venue="kraken", account_id="synthetic-account", mode="live")
    broker = _broker(rest, intent_resolver=identity)
    execution = Execution(recovered, ledger, clock, broker, venue="kraken", account_id="synthetic-account", mode="live")
    before_calls = len(rest.calls)
    asyncio.run(execution.reconcile())
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("89.92")
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.1")
    assert recovered.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    assert not any(method in {"AddOrder", "CancelOrder", "OpenOrders", "ClosedOrders", "QueryOrders"}
                   for method, _ in rest.calls[before_calls:])
    recovered.close()
