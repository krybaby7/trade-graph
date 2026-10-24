"""Whether the first candle of a response connects to the newest stored one, at every boundary of the interval."""

from __future__ import annotations

import pytest
from tests.candle_support import at, pair_records

from trade_graph.application.candle_collection_plan import PlannedSeries
from trade_graph.application.candle_collection_run import GAP_ACTION, continuity
from trade_graph.contracts.candles import Candle
from trade_graph.domain.clock import utc_iso

NEWEST = 1_791_900_000  # a multiple of every collected interval
RECORD = pair_records()["BTC/USD"]


def candle_at(open_unix: int, interval: int) -> Candle:
    step = interval * 60
    return Candle(
        symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=interval, open_time_utc=at(open_unix),
        close_time_utc=at(open_unix + step), available_at_utc=at(open_unix + step + 3), open=100, high=101, low=99,
        close=100, vwap=100, volume=1, trade_count=1, source="synthetic")


@pytest.mark.parametrize("interval", [1, 5, 15, 60])
@pytest.mark.parametrize(("offset_steps", "status", "missing"), [
    (-1, "connected", 0), (0, "connected", 0), (1, "connected", 0), (2, "gap", 1), (5, "gap", 4),
])
def test_a_first_candle_is_a_gap_only_when_at_least_one_slot_lies_between(
        interval: int, offset_steps: int, status: str, missing: int) -> None:
    step = interval * 60
    item = PlannedSeries(RECORD, interval, NEWEST)
    result = continuity(item, (candle_at(NEWEST + offset_steps * step, interval),))
    assert (result["status"], result["missing_slots"]) == (status, missing)
    assert result["action"] == (GAP_ACTION if status == "gap" else None)


def test_the_gap_size_is_the_number_of_slots_strictly_between_the_two_candles() -> None:
    item = PlannedSeries(RECORD, 15, NEWEST)
    result = continuity(item, (candle_at(NEWEST + 4 * 900, 15),))
    assert result["status"] == "gap" and result["missing_slots"] == 3
    assert result["stored_newest_open_utc"] == utc_iso(at(NEWEST))
    assert result["first_returned_open_utc"] == utc_iso(at(NEWEST + 4 * 900))


def test_a_response_without_a_committed_candle_cannot_confirm_a_connection() -> None:
    result = continuity(PlannedSeries(RECORD, 15, NEWEST), ())
    assert result["status"] == "unverified" and result["missing_slots"] == 0 and result["action"] is None
    assert continuity(PlannedSeries(RECORD, 15, None), ())["status"] == "no_data"
    assert continuity(PlannedSeries(RECORD, 15, None), (candle_at(NEWEST, 15),))["status"] == "first_collection"
