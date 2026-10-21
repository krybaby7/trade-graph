"""Market-type rules version 2: a short hole in the hourly candles no longer blanks a coin for 36 days (L2, D230).

L2's two-year rerun found that ``market-type-rules@1`` needs 863 unbroken hourly candles, so Kraken's three short
archive holes (3, 1 and 5 hours) left about 108 days ``unclear``. Version 2 skips a hole of at most
``max_bridged_gap_bars`` slots with a candle on both sides, and at most ``max_bridged_total_bars`` skipped slots among
the slots one label looks back over; a longer outage is still ``unclear``. Version 1 is unchanged, so stored labels
and reports keep their hash. Plain values in, labels out; synthetic only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.kernel.market_type import (
    CURRENT_MARKET_TYPE_RULES,
    KNOWN_MARKET_TYPE_RULES,
    MARKET_TYPE_RULES_V1,
    MARKET_TYPE_RULES_V2,
    MarketTypeRules,
    SlotFacts,
    bridged_slots,
    look_back_slots,
    resolve_market_type_rules,
    slot_verdicts,
)

D = Decimal
HOUR = timedelta(hours=1)
T0 = datetime(2026, 9, 1, tzinfo=UTC)
V1, V2 = MARKET_TYPE_RULES_V1, MARKET_TYPE_RULES_V2
HISTORY = 30  # short histories keep the cases readable; the kernel takes the history as given


def _facts(count: int, *, missing: set[int] = frozenset()) -> list[SlotFacts]:
    """A steady rise: every complete history reads trending, buyers ahead, close above a rising average."""
    facts = []
    for index in range(count):
        at = T0 + HOUR * index
        if index in missing:
            facts.append(SlotFacts(close_time=at, present=False))
        else:
            facts.append(SlotFacts(close_time=at, present=True, close=D(1000), average=D(100 + index),
                                   strength=D(30), plus=D(28), minus=D(12)))
    return facts


def _types(verdicts) -> list[str | None]:
    return [item.market_type if item.reason is None else f"unclear:{item.reason}" for item in verdicts]


def _hole(first: int, length: int) -> set[int]:
    return set(range(first, first + length))


def test_version_two_is_current_and_version_one_keeps_its_hash() -> None:
    assert CURRENT_MARKET_TYPE_RULES is V2 and V2.ref == "market-type-rules@2"
    assert (V2.max_bridged_gap_bars, V2.max_bridged_total_bars) == (6, 24)
    assert (V1.max_bridged_gap_bars, V1.max_bridged_total_bars) == (0, 0)
    same = ("interval_minutes", "average_period", "slope_bars", "strength_period", "trending_strength", "max_lag_bars")
    assert all(getattr(V1, name) == getattr(V2, name) for name in same)
    # Version 1's document and hash are exactly as before the bridging fields existed (D285's pin).
    assert "max_bridged_gap_bars" not in V1.canonical_document()
    assert V1.content_sha256 == "d017d69a24138f9c23e48f711a235b2fc6b2e8fbd930b53f2a676eff5718bb01"
    assert V2.canonical_document()["max_bridged_gap_bars"] == 6
    assert V2.content_sha256 != V1.content_sha256
    assert set(KNOWN_MARKET_TYPE_RULES) == {"market-type-rules@1", "market-type-rules@2"}
    assert resolve_market_type_rules("market-type-rules@1") is V1
    assert resolve_market_type_rules("market-type-rules@2") is V2
    assert look_back_slots(V1, history=863) == 863 and look_back_slots(V2, history=863) == 887


@pytest.mark.parametrize("changes", [
    {"max_bridged_gap_bars": 13}, {"max_bridged_gap_bars": -1}, {"max_bridged_total_bars": 49},
    {"max_bridged_gap_bars": 6, "max_bridged_total_bars": 0}, {"max_bridged_gap_bars": 0, "max_bridged_total_bars": 6},
    {"max_bridged_gap_bars": 6, "max_bridged_total_bars": 5}, {"max_bridged_gap_bars": True},
])
def test_the_bridging_bounds(changes) -> None:
    with pytest.raises(ValidationError):
        MarketTypeRules.model_validate({**V2.model_dump(mode="json"), **changes})


def test_which_missing_slots_are_skipped() -> None:
    present = [True, False, False, True, False, True, False, False, False, False, False, False, False, True, False]
    # A run is skipped only when it is at most 6 long with a candle right before and right after it: the run at the
    # end (no candle after yet) and a run of 7 are not.
    assert bridged_slots(present, V2) == (False, True, True, False, True, False, False, False, False, False, False,
                                          False, False, False, False)
    assert bridged_slots([False, False, True, True], V2) == (False, False, False, False)  # the series began later
    assert bridged_slots(present, V1) == (False,) * len(present)


def test_a_five_hour_hole_is_skipped_by_version_two_and_blanks_version_one() -> None:
    facts = _facts(130, missing=_hole(60, 5))
    one = _types(slot_verdicts(facts, V1, history=HISTORY))
    two = _types(slot_verdicts(facts, V2, history=HISTORY))
    # Version 1: unclear until the hole leaves the 30-slot history (the last five see it at their history's start).
    assert one[65:89] == ["unclear:gap"] * 24 and one[89:94] == ["unclear:not_enough_candles"] * 5
    assert one[94] == "rising"
    assert two[HISTORY - 1:60] == ["rising"] * (61 - HISTORY) and two[65:] == ["rising"] * 65
    assert two[60:65] == [None] * 5  # the missing slots themselves have no candle to label
    assert one[:HISTORY - 1] == two[:HISTORY - 1] == ["unclear:not_enough_candles"] * (HISTORY - 1)


def test_the_slope_reads_the_average_a_day_of_candles_earlier_across_the_hole() -> None:
    verdict = slot_verdicts(_facts(130, missing=_hole(60, 5)), V2, history=HISTORY)[65]
    # Counting candles, not clock hours: 24 candles before candle 65 is candle 36, because 60 to 64 are skipped.
    assert verdict.values["average"] == "165" and verdict.values["average_before"] == "136"


def test_a_hole_longer_than_six_hours_is_still_unclear() -> None:
    facts = _facts(130, missing=_hole(60, 7))
    assert slot_verdicts(facts, V2, history=HISTORY) == slot_verdicts(facts, V1, history=HISTORY)


def test_more_than_twenty_four_skipped_hours_in_one_look_back_is_unclear() -> None:
    holes = set().union(*(_hole(first, 5) for first in (40, 50, 60, 70, 80)))  # 25 missing hours, each run short
    verdicts = _types(slot_verdicts(_facts(150, missing=holes), V2, history=HISTORY))
    assert verdicts[85] == "unclear:gap"  # its 54-slot look-back (30 + 24) holds all five holes
    assert verdicts[149] == "rising"
    four = set().union(*(_hole(first, 5) for first in (50, 60, 70, 80)))  # 20 missing hours: within the cap
    assert _types(slot_verdicts(_facts(150, missing=four), V2, history=HISTORY))[85] == "rising"


def test_a_series_that_starts_late_is_not_bridged_back_to_its_start() -> None:
    verdicts = _types(slot_verdicts(_facts(80, missing={0, 1, 2}), V2, history=HISTORY))
    assert verdicts[HISTORY] == "unclear:not_enough_candles" and verdicts[HISTORY + 2] == "rising"
    assert verdicts == _types(slot_verdicts(_facts(80, missing={0, 1, 2}), V1, history=HISTORY))


@pytest.mark.parametrize("hole_start", [40, 55, 70, 76, 77, 80, 90, 100])
@pytest.mark.parametrize("length", [1, 3, 6, 7, 12])
def test_a_short_read_gives_the_label_a_long_read_gives(hole_start, length) -> None:
    """The live label reads only the look-back before its slot; a replay reads months. Both must agree."""
    facts = _facts(140, missing=_hole(hole_start, length))
    whole = slot_verdicts(facts, V2, history=HISTORY)
    reach = look_back_slots(V2, history=HISTORY)
    for last in range(reach, len(facts)):
        short = slot_verdicts(facts[last - reach + 1:last + 1], V2, history=HISTORY)
        assert short[-1] == whole[last]
