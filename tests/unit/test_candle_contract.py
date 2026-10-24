"""The shared multi-interval candle contract: completed candles, exact decimals, aligned slots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.contracts.candles import (
    CANDLE_INTERVALS,
    COLLECTED_INTERVALS,
    Candle,
    align_close_time,
    expected_close_times,
    from_unix,
    to_unix,
)

OPEN = datetime(2026, 10, 6, 12, 15, tzinfo=UTC)


def _candle(**changes) -> Candle:
    values = dict(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=15,
        open_time_utc=OPEN, close_time_utc=OPEN + timedelta(minutes=15),
        available_at_utc=OPEN + timedelta(minutes=15, seconds=4),
        open=Decimal("100.10"), high=Decimal("101"), low=Decimal("99.5"), close=Decimal("100.75"),
        vwap=Decimal("100.4"), volume=Decimal("12.50000000"), trade_count=7, source="synthetic",
    )
    values.update(changes)
    return Candle(**values)


def test_intervals_are_kraken_sizes_and_collector_set_is_a_subset() -> None:
    assert CANDLE_INTERVALS == (1, 5, 15, 30, 60, 240, 720, 1440)
    assert COLLECTED_INTERVALS == (1, 5, 15, 60)
    assert set(COLLECTED_INTERVALS) <= set(CANDLE_INTERVALS)


def test_valid_candle_keeps_exact_decimals_and_serializes_as_text() -> None:
    candle = _candle()
    document = candle.model_dump(mode="json")
    assert document["open"] == "100.1"
    assert document["volume"] == "12.5"
    assert document["open_time_utc"].endswith("Z")
    assert isinstance(candle.close, Decimal)


def test_binary_floats_are_refused() -> None:
    with pytest.raises(ValidationError):
        _candle(open=100.1)


@pytest.mark.parametrize("interval", [2, 7, 59, 0, -1, 10080])
def test_unknown_interval_is_refused(interval: int) -> None:
    with pytest.raises(ValidationError):
        _candle(interval_minutes=interval)


def test_open_time_must_sit_on_the_interval_grid() -> None:
    off_grid = OPEN + timedelta(minutes=5)
    with pytest.raises(ValidationError):
        _candle(open_time_utc=off_grid, close_time_utc=off_grid + timedelta(minutes=15),
                available_at_utc=off_grid + timedelta(minutes=16))


def test_close_time_must_be_one_interval_after_open() -> None:
    with pytest.raises(ValidationError):
        _candle(close_time_utc=OPEN + timedelta(minutes=30))


def test_a_candle_not_yet_received_after_its_close_is_refused() -> None:
    """The still-forming candle: availability cannot precede the close."""
    with pytest.raises(ValidationError):
        _candle(available_at_utc=OPEN + timedelta(minutes=14, seconds=59))


def test_price_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        _candle(high=Decimal("100"))  # below the close
    with pytest.raises(ValidationError):
        _candle(low=Decimal("100.2"))  # above the open
    with pytest.raises(ValidationError):
        _candle(open=Decimal("0"), low=Decimal("0"))
    with pytest.raises(ValidationError):
        _candle(volume=Decimal("-1"))


def test_vwap_is_optional_and_may_be_zero_only_without_volume() -> None:
    assert _candle(vwap=None).vwap is None
    with pytest.raises(ValidationError):
        _candle(vwap=Decimal("0"))
    quiet = _candle(vwap=Decimal("0"), volume=Decimal("0"), trade_count=0)
    assert quiet.vwap == 0
    with pytest.raises(ValidationError):
        _candle(vwap=Decimal("200"))


def test_content_hash_ignores_receipt_time_and_changes_with_content() -> None:
    first = _candle()
    later = _candle(available_at_utc=OPEN + timedelta(hours=3))
    changed = _candle(close=Decimal("100.80"))
    assert first.content_hash == later.content_hash
    assert first.content_hash != changed.content_hash
    assert len(first.content_hash) == 64


def test_equal_decimals_in_different_spellings_hash_alike() -> None:
    assert _candle(volume=Decimal("12.5")).content_hash == _candle(volume=Decimal("12.50000000")).content_hash


def test_source_ref_names_the_source_pair_interval_and_open_second() -> None:
    ref = _candle(source="kraken_public_ohlc").source_ref
    assert ref == f"kraken:/0/public/OHLC:XBTUSD:15:{to_unix(OPEN)}"
    assert _candle(source="kraken_ohlcvt_archive").source_ref.startswith("kraken:ohlcvt-archive:XBTUSD:15:")
    assert _candle(source="kraken_public_trades").source_ref.startswith("kraken:/0/public/Trades:XBTUSD:15:")


def test_unix_round_trip_is_exact() -> None:
    assert from_unix(to_unix(OPEN)) == OPEN
    assert to_unix(datetime(1970, 1, 1, 0, 1, tzinfo=UTC)) == 60


def test_expected_close_times_end_at_the_given_close_and_run_oldest_first() -> None:
    end = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
    slots = expected_close_times(end, 3, 15)
    assert slots == [end - timedelta(minutes=30), end - timedelta(minutes=15), end]


def test_expected_close_times_refuse_an_unaligned_end_or_a_bad_count() -> None:
    with pytest.raises(ValueError):
        expected_close_times(datetime(2026, 10, 6, 12, 31, tzinfo=UTC), 3, 15)
    with pytest.raises(ValueError):
        expected_close_times(datetime(2026, 10, 6, 12, 30, tzinfo=UTC), 0, 15)


def test_align_close_time_returns_the_last_boundary_at_or_before() -> None:
    assert align_close_time(datetime(2026, 10, 6, 12, 44, 59, tzinfo=UTC), 15) == datetime(
        2026, 10, 6, 12, 30, tzinfo=UTC)
    assert align_close_time(datetime(2026, 10, 6, 12, 45, tzinfo=UTC), 15) == datetime(
        2026, 10, 6, 12, 45, tzinfo=UTC)
