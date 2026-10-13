"""The pair record: engine symbols, trading status, exclusion marks and exact decimals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.network_guard import block_network

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.pair_catalog import (
    NORMAL_STATUSES,
    PAIR_STATUSES,
    PairRecord,
    engine_symbol,
    is_normal_status,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _record(**changes) -> PairRecord:
    values = dict(
        symbol="BTC/USD", source_pair="XBTUSD", ws_name="XBT/USD", base="XXBT", quote="ZUSD", status="online",
        ordermin=Decimal("0.00005"), costmin=Decimal("0.5"), tick_size=Decimal("0.1"), pair_decimals=1,
        cost_decimals=5, lot_decimals=8, stablecoin=False, yield_token=False, exclusion_reasons=(),
        rules_version="rules-test",
    )
    values.update(changes)
    return PairRecord(**values)


@pytest.mark.parametrize(("wire", "engine"), [
    ("XBT/USD", "BTC/USD"),
    ("XDG/USD", "DOGE/USD"),
    ("ETH/XBT", "ETH/BTC"),
    ("XBT/USDT", "BTC/USDT"),
    ("ETH/USD", "ETH/USD"),
    ("ZCHF/USD", "ZCHF/USD"),
    ("DOG/USD", "DOG/USD"),
    ("DOGS/USD", "DOGS/USD"),
    ("AIXBT/USD", "AIXBT/USD"),
    ("XBTC/USD", "XBTC/USD"),
])
def test_only_whole_xbt_and_xdg_codes_are_renamed(wire: str, engine: str) -> None:
    assert engine_symbol(wire) == engine


@pytest.mark.parametrize("wire", ["", "BTCUSD", "XBT/", "/USD", "XBT/USD/EUR", "xbt/usd", "XBT USD"])
def test_a_malformed_wire_name_has_no_symbol(wire: str) -> None:
    with pytest.raises(ValueError):
        engine_symbol(wire)


def test_a_record_is_frozen_and_refuses_unknown_fields() -> None:
    record = _record()
    with pytest.raises(ValidationError):
        record.status = "cancel_only"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _record(surprise="x")


def test_the_symbol_must_match_the_wire_name() -> None:
    with pytest.raises(ValidationError):
        _record(symbol="XBT/USD")
    with pytest.raises(ValidationError):
        _record(symbol="ETH/USD")
    assert _record(symbol="DOGE/USD", ws_name="XDG/USD", source_pair="XDGUSD", base="XXDG").symbol == "DOGE/USD"


def test_a_record_symbol_and_pair_code_can_become_a_candle() -> None:
    record = _record()
    opened = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    candle = Candle(
        symbol=record.symbol, source_pair=record.source_pair, interval_minutes=15, open_time_utc=opened,
        close_time_utc=opened + timedelta(minutes=15), available_at_utc=opened + timedelta(minutes=16),
        open=Decimal("1"), high=Decimal("2"), low=Decimal("1"), close=Decimal("2"), volume=Decimal("1"),
        trade_count=1, source="synthetic")
    assert candle.symbol == "BTC/USD"


def test_the_status_set_is_krakens_and_only_online_is_normal() -> None:
    assert PAIR_STATUSES == ("online", "cancel_only", "post_only", "limit_only", "reduce_only")
    assert NORMAL_STATUSES == frozenset({"online"})
    assert [is_normal_status(status) for status in PAIR_STATUSES] == [True, False, False, False, False]
    assert not is_normal_status("maintenance")
    for status in PAIR_STATUSES:
        record = _record(status=status)
        assert record.in_normal_status is (status == "online")


def test_an_unknown_status_is_refused_not_guessed() -> None:
    with pytest.raises(ValidationError):
        _record(status="maintenance")


def test_collection_eligibility_needs_normal_status_and_no_exclusion() -> None:
    assert _record().eligible_for_collection
    assert not _record(status="cancel_only").eligible_for_collection
    assert not _record(status="post_only").eligible_for_collection
    stable = _record(stablecoin=True, exclusion_reasons=("stablecoin",))
    assert stable.in_normal_status and not stable.eligible_for_collection
    fiat = _record(exclusion_reasons=("fiat_currency",))
    assert not fiat.eligible_for_collection


def test_marks_and_exclusion_reasons_must_agree() -> None:
    with pytest.raises(ValidationError):
        _record(stablecoin=True)
    with pytest.raises(ValidationError):
        _record(yield_token=True, exclusion_reasons=("stablecoin",))
    with pytest.raises(ValidationError):
        _record(exclusion_reasons=("stablecoin",))
    both = _record(stablecoin=True, yield_token=True, exclusion_reasons=("stablecoin", "yield_token"))
    assert both.exclusion_reasons == ("stablecoin", "yield_token")


def test_exclusion_reasons_are_short_unique_sorted_codes() -> None:
    for bad in (("yield_token", "stablecoin"), ("fiat_currency", "fiat_currency"), ("Not A Code",), ("",)):
        with pytest.raises(ValidationError):
            _record(exclusion_reasons=bad, stablecoin="stablecoin" in bad, yield_token="yield_token" in bad)
    assert _record(exclusion_reasons=("fiat_currency", "thin_volume")).exclusion_reasons == (
        "fiat_currency", "thin_volume")


def test_new_records_are_not_marked_as_reviewed_by_default() -> None:
    assert _record().base_asset_review == "not_individually_reviewed"
    assert _record(base_asset_review="reviewed").base_asset_review == "reviewed"
    with pytest.raises(ValidationError):
        _record(base_asset_review="verified_clean")


@pytest.mark.parametrize("field", ["ordermin", "costmin", "tick_size"])
@pytest.mark.parametrize("bad", [0.5, float("nan"), "NaN", "Infinity", None, True, "1" * 100, "1E+999", "1E-999", "x"])
def test_decimal_fields_refuse_floats_non_numbers_and_unbounded_values(field: str, bad) -> None:
    if field == "tick_size" and bad is None:
        assert _record(tick_size=None).tick_size is None
        return
    with pytest.raises(ValidationError):
        _record(**{field: bad})


def test_order_limits_must_be_positive_and_cost_may_be_zero() -> None:
    with pytest.raises(ValidationError):
        _record(ordermin=Decimal("0"))
    with pytest.raises(ValidationError):
        _record(ordermin=Decimal("-1"))
    with pytest.raises(ValidationError):
        _record(tick_size=Decimal("0"))
    with pytest.raises(ValidationError):
        _record(costmin=Decimal("-0.1"))
    assert _record(costmin=Decimal("0")).costmin == Decimal("0")


@pytest.mark.parametrize("field", ["pair_decimals", "cost_decimals", "lot_decimals"])
@pytest.mark.parametrize("bad", [-1, 21, 2.0, Decimal("5"), "5", True])
def test_decimal_places_are_small_exact_integers(field: str, bad) -> None:
    with pytest.raises(ValidationError):
        _record(**{field: bad})


def test_decimals_serialize_to_exact_canonical_text_and_round_trip() -> None:
    record = _record(ordermin=Decimal("5E-5"), costmin=Decimal("0.50"), tick_size=Decimal("0.10"))
    document = record.model_dump(mode="json")
    assert (document["ordermin"], document["costmin"], document["tick_size"]) == ("0.00005", "0.5", "0.1")
    assert document["exclusion_reasons"] == []
    assert PairRecord.model_validate(document) == _record()
    assert _record(tick_size=None).model_dump(mode="json")["tick_size"] is None


def test_usd_quoted_means_an_engine_quote_of_usd() -> None:
    assert _record().usd_quoted
    assert not _record(symbol="BTC/USDT", ws_name="XBT/USDT", source_pair="XBTUSDT", quote="USDT").usd_quoted
    assert not _record(symbol="ETH/PYUSD", ws_name="ETH/PYUSD", source_pair="ETHPYUSD", quote="PYUSD").usd_quoted


@pytest.mark.parametrize("field", ["symbol", "source_pair", "ws_name", "base", "quote", "rules_version"])
def test_identifier_fields_refuse_unsafe_text(field: str) -> None:
    for bad in ("", "a b", "x" * 80, "../etc", "BTC/USD\n"):
        with pytest.raises(ValidationError):
            _record(**{field: bad})
