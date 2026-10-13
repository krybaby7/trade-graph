"""One bounded public Trades page: exact decimals, strict shape, classified errors. Scripted transport only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.trades_support import ScriptedTransport, recorded, wire_page, wire_row

from trade_graph.adapters.market.kraken_trades import (
    MAX_TRADES_PER_PAGE,
    KrakenTrades,
    TradesPageRefused,
    TradesRateLimited,
    TradesThrottled,
    pair_code_for_symbol,
    parse_trades_page,
    trades_url,
)
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 10, 13, 13, 20, tzinfo=UTC)
ROW = wire_row(100, "83131.70000", "0.00005000", "1791897312.3299400")


def _fetch(raw: str, *, pair: str = "XBTUSD", since: int | None = None, count: int = 1000, delay: float = 2):
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([raw], clock, delay_seconds=delay)
    page = KrakenTrades(transport, clock).fetch(pair, since=since, count=count)
    return page, transport, clock


# --- recorded pages ------------------------------------------------------------------------------------------------


def test_a_recorded_busy_page_keeps_every_decimal_exactly() -> None:
    page, transport, _ = _fetch(recorded("trades_xbtusd_recent_tail.json"))
    assert len(page.trades) == 40 and page.result_key == "XXBTZUSD" and page.pair_code == "XBTUSD"
    last = page.trades[-1]
    assert last.trade_id == 111249195 and last.price == Decimal("83108.10000") and last.volume == Decimal("0.00863334")
    assert last.time == Decimal("1791897312.32994")
    assert page.trades[0].time == Decimal("1791897263.0982802")  # seven fraction digits survive, nothing is a float
    assert page.last == 1791897312329940052
    assert transport.calls == ["https://api.kraken.com/0/public/Trades?pair=XBTUSD&count=1000"]


def test_the_recorded_pages_of_two_pairs_and_two_eras_parse() -> None:
    quiet, _, _ = _fetch(recorded("trades_batusd_recent_tail.json"), pair="BATUSD")
    assert quiet.result_key == "BATUSD" and quiet.trades[-1].trade_id == 1542099
    old, _, _ = _fetch(recorded("trades_xbtusd_since_2020.json"), since=1577836800, count=5)
    assert [trade.trade_id for trade in old.trades] == list(range(19217603, 19217608))
    assert old.trades[0].time == Decimal("1577836801.9486852") and old.requested_since == 1577836800
    single, _, _ = _fetch(recorded("trades_xbtusd_one_trade_24h_before.json"), since=1791811008, count=1)
    assert single.trades[0].trade_id == 111084931 and single.full


def test_the_cursor_page_repeats_the_final_trade_of_the_page_before_it() -> None:
    """Observed 2026-10-13: Kraken's since is inclusive, so the cursor's own trade comes back as the first row."""
    first, _, _ = _fetch(recorded("trades_xbtusd_recent_tail.json"))
    second, transport, _ = _fetch(recorded("trades_xbtusd_since_cursor.json"), since=first.last, count=10)
    assert second.trades[0] == first.trades[-1]
    assert transport.queries()[0] == {"pair": "XBTUSD", "since": "1791897312329940052", "count": "10"}
    assert second.last == 1791897320119568270 and second.full


def test_receipt_time_is_taken_after_the_network_call() -> None:
    page, _, clock = _fetch(recorded("trades_xbtusd_since_cursor.json"), since=1, count=10, delay=9)
    assert page.received_at == NOW.replace(second=9) and clock.now() == page.received_at


def test_a_short_page_is_not_full() -> None:
    page, _, _ = _fetch(recorded("trades_xbtusd_recent_tail.json"))
    assert not page.full


def test_the_receipt_clock_cannot_run_backwards() -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([recorded("trades_xbtusd_since_2020.json")], clock, delay_seconds=-5)
    with pytest.raises(TradesPageRefused) as refused:
        KrakenTrades(transport, clock).fetch("XBTUSD", since=1, count=5)
    assert refused.value.reason == "receipt_clock_backwards"


# --- request building ----------------------------------------------------------------------------------------------


def test_urls_carry_only_the_pair_the_cursor_and_the_count() -> None:
    assert trades_url("XBTUSD", since=None, count=1000).endswith("/Trades?pair=XBTUSD&count=1000")
    assert trades_url("ETHUSD", since=1577836800, count=5).endswith("/Trades?pair=ETHUSD&since=1577836800&count=5")


@pytest.mark.parametrize("pair", ["", "xbtusd", "XBT/USD", "XBTUSD&x=1", "XBTUSD ", "A", "X" * 25, "XBTUSD\n"])
def test_unsafe_pair_codes_never_reach_a_url(pair) -> None:
    with pytest.raises(ValidationFailure):
        trades_url(pair, since=None, count=10)


@pytest.mark.parametrize("since", [-1, True, 1.5, "5", 10**20])
def test_bad_cursors_are_refused(since) -> None:
    with pytest.raises(ValidationFailure):
        trades_url("XBTUSD", since=since, count=10)


@pytest.mark.parametrize("count", [0, -1, MAX_TRADES_PER_PAGE + 1, True, 10.0, "10"])
def test_the_count_stays_between_one_and_the_documented_maximum(count) -> None:
    with pytest.raises(ValidationFailure):
        trades_url("XBTUSD", since=None, count=count)


def test_symbols_map_to_the_archive_pair_names() -> None:
    assert pair_code_for_symbol("BTC/USD") == "XBTUSD"
    assert pair_code_for_symbol("DOGE/USD") == "XDGUSD"
    assert pair_code_for_symbol("ETH/USD") == "ETHUSD"
    assert pair_code_for_symbol("SOL/USD") == "SOLUSD"
    for bad in ("BTC/EUR", "BTCUSD", "btc/usd", "BTC/USD/X", ""):
        with pytest.raises(ValidationFailure):
            pair_code_for_symbol(bad)


# --- refusals ------------------------------------------------------------------------------------------------------


def _refusal(raw: str, **kwargs) -> TradesPageRefused:
    with pytest.raises(TradesPageRefused) as refused:
        parse_trades_page(raw, pair_code=kwargs.pop("pair", "XBTUSD"), since=None, count=kwargs.pop("count", 1000),
                          received_at=NOW)
    return refused.value


def test_rate_limit_errors_are_classified_and_carry_no_wait_hint() -> None:
    error = _refusal('{"error":["EAPI:Rate limit exceeded"],"result":{}}')
    assert isinstance(error, TradesRateLimited) and error.reason == "rate_limited"
    assert error.retry_after_unix is None and error.kraken_errors == ("EAPI:Rate limit exceeded",)


def test_a_throttle_error_carries_the_retry_timestamp() -> None:
    error = _refusal('{"error":["EService: Throttled: 1791897500"],"result":{}}')
    assert isinstance(error, TradesThrottled) and error.reason == "throttled"
    assert error.retry_after_unix == 1791897500


def test_a_throttle_without_a_timestamp_is_still_a_throttle() -> None:
    error = _refusal('{"error":["EService: Throttled: soon"],"result":{}}')
    assert isinstance(error, TradesThrottled) and error.retry_after_unix is None


def test_other_exchange_errors_are_refused_with_bounded_text() -> None:
    error = _refusal(json.dumps({"error": ["EQuery:Unknown asset pair" + "x" * 500, "second", "third", "fourth"],
                                 "result": {}}))
    assert error.reason == "kraken_error" and len(error.kraken_errors) == 3
    assert all(len(text) <= 120 for text in error.kraken_errors)


@pytest.mark.parametrize("raw, reason", [
    ("not json", "invalid_json"),
    ("[]", "shape"),
    ('{"result":{}}', "shape"),
    ('{"error":{},"result":{}}', "shape"),
    ('{"error":[],"result":[]}', "shape"),
    ('{"error":[],"result":{"last":"1"}}', "shape"),
    ('{"error":[],"result":{"XXBTZUSD":[],"XETHZUSD":[],"last":"1"}}', "shape"),
    ('{"error":[],"result":{"XXBTZUSD":[]}}', "shape"),
    ('{"error":[],"error":[],"result":{"XXBTZUSD":[],"last":"1"}}', "duplicate_keys"),
    ('{"error":[],"result":{"XXBTZUSD":[],"last":"1","last":"2"}}', "duplicate_keys"),
    ('{"error":[],"result":{"XETHZUSD":[],"last":"1"}}', "pair_mismatch"),
    ('{"error":[],"result":{"XXBTZUSD":NaN,"last":"1"}}', "invalid_json"),
    ('{"error":[],"result":{"XXBTZUSD":[],"last":1}}', "cursor_invalid"),
    ('{"error":[],"result":{"XXBTZUSD":[],"last":"12x"}}', "cursor_invalid"),
    ('{"error":[],"result":{"XXBTZUSD":[],"last":"-5"}}', "cursor_invalid"),
    ('{"error":[],"result":{"XXBTZUSD":[],"last":"' + "9" * 30 + '"}}', "cursor_invalid"),
])
def test_malformed_pages_are_refused_with_a_stable_reason(raw, reason) -> None:
    assert _refusal(raw).reason == reason


def test_an_oversized_response_is_refused_before_parsing() -> None:
    assert _refusal(" " * (1_048_576 + 1)).reason == "response_too_large"


def test_more_rows_than_requested_are_refused() -> None:
    rows = [wire_row(i, "10.0", "1.0", f"1791897312.{i:07d}") for i in range(3)]
    error = _refusal(wire_page(rows, last=1791897312000000002), count=2)
    assert error.reason == "too_many_rows"


@pytest.mark.parametrize("row", [
    '["83131.7","0.1",1791897312.1,"b","l",""]',                       # six fields: no trade id
    '["83131.7","0.1",1791897312.1,"b","l","",5,9]',                   # eight fields
    '[83131.7,"0.1",1791897312.1,"b","l","",5]',                       # price as a JSON number
    '["83131.7",0.1,1791897312.1,"b","l","",5]',                       # volume as a JSON number
    '["0","0.1",1791897312.1,"b","l","",5]',                           # zero price
    '["-1","0.1",1791897312.1,"b","l","",5]',                          # negative price
    '["83131.7","0",1791897312.1,"b","l","",5]',                       # zero volume
    '["1e5","0.1",1791897312.1,"b","l","",5]',                         # exponent notation
    '["83131.7","0.1","1791897312.1","b","l","",5]',                   # time as a string
    '["83131.7","0.1",-1.5,"b","l","",5]',                             # negative time
    '["83131.7","0.1",99999999999.5,"b","l","",5]',                    # time beyond 2100
    '["83131.7","0.1",1791897312.1,"b","l","","5"]',                   # id as a string
    '["83131.7","0.1",1791897312.1,"b","l","",-5]',                    # negative id
    '["83131.7","0.1",1791897312.1,"b","l","",true]',                  # boolean id
    '["83131.7","0.1",1791897312.1,"b","l","",5.5]',                   # fractional id
    '["83131.7","0.1",1791897312.1,7,"l","",5]',                       # side not a string
    '["83131.7","0.1",1791897312.1,"b","l",null,5]',                   # misc not a string
    '"row"',
])
def test_malformed_rows_are_refused(row) -> None:
    raw = '{"error":[],"result":{"XXBTZUSD":[' + row + '],"last":"1791897312100000000"}}'
    assert _refusal(raw).reason in {"row_invalid", "too_many_rows"}


def test_a_trade_time_may_carry_nine_fraction_digits_but_not_ten() -> None:
    nine = wire_row(5, "10.0", "1.0", "1791897312.123456789")
    ten = wire_row(5, "10.0", "1.0", "1791897312.1234567891")
    page = parse_trades_page(wire_page([nine], last=1791897312123456789), pair_code="XBTUSD", since=None, count=10,
                             received_at=NOW)
    assert page.trades[0].time == Decimal("1791897312.123456789")
    error = _refusal(wire_page([ten], last=1791897312123456789))
    assert error.reason == "row_invalid" and "nine fraction digits" in str(error)


def test_rows_must_rise_in_id_and_not_fall_in_time() -> None:
    same_id = [wire_row(5, "10.0", "1.0", "1791897312.1"), wire_row(5, "10.0", "1.0", "1791897312.2")]
    backwards_id = [wire_row(6, "10.0", "1.0", "1791897312.1"), wire_row(5, "10.0", "1.0", "1791897312.2")]
    backwards_time = [wire_row(5, "10.0", "1.0", "1791897312.2"), wire_row(6, "10.0", "1.0", "1791897312.1")]
    for rows in (same_id, backwards_id, backwards_time):
        assert _refusal(wire_page(rows, last=1791897312200000000)).reason == "page_not_ordered"


def test_a_cursor_that_disagrees_with_the_final_row_is_refused() -> None:
    """A cursor that stopped meaning 'time of the last trade' would make the next request fetch the wrong range."""
    rows = [wire_row(5, "10.0", "1.0", "1791897312.1000000")]
    assert _refusal(wire_page(rows, last=1791897000100000000)).reason == "cursor_inconsistent"
    ok = parse_trades_page(wire_page(rows, last=1791897312100000052), pair_code="XBTUSD", since=None, count=10,
                           received_at=NOW)
    assert ok.last == 1791897312100000052


def test_an_empty_page_is_valid_and_not_full() -> None:
    page = parse_trades_page(wire_page([], last=1791897312100000000), pair_code="XBTUSD", since=1, count=10,
                             received_at=NOW)
    assert page.trades == () and not page.full


def test_overlong_decimals_are_refused() -> None:
    row = wire_row(5, "9" * 40, "1.0", "1791897312.1")
    assert _refusal(wire_page([row], last=1791897312100000000)).reason == "row_invalid"


def test_the_refusal_text_never_contains_the_response_body() -> None:
    error = _refusal('{"error":[],"result":{"XXBTZUSD":[["BODY-MARKER","1",1.5,"b","l","",1]],"last":"1"}}')
    assert "BODY-MARKER" not in str(error) and "BODY-MARKER" not in repr(error.kraken_errors)


def test_transport_failures_propagate_to_the_caller_unchanged() -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport([TimeoutError("slow")], clock)
    with pytest.raises(TimeoutError):
        KrakenTrades(transport, clock).fetch("XBTUSD", since=None, count=10)
