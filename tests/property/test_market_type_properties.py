"""Market-type labels are point in time, and a report's split sums to its totals (W11-A, C10; D286, D289).

- A slot's verdict reads only the slots that end at it, so labelling a longer stretch never changes the label of an
  earlier slot or moment (pure kernel, many random readings).
- A live label at a moment is the same whatever arrives after it: later candles, a late candle of an earlier hour,
  a corrected revision received later (through the candle store and the indicator engine, random synthetic paths with
  random holes and delays).

Synthetic data only (``synthetic_fixture``); no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.market_type_support import FIRST_LABELLED, HOUR, closes_wandering, hourly_moment, series, store_series
from tests.practice_range_support import open_store

from trade_graph.application.market_types.labeller import label_coin
from trade_graph.kernel.market_type import MARKET_TYPE_RULES_V1, MarketTypeTimeline, SlotFacts, slot_verdicts

RULES = MARKET_TYPE_RULES_V1
T0 = datetime(2026, 9, 1, tzinfo=UTC)
D = Decimal

# close, average, ADX, +DI, -DI, and whether the slot may be missing
readings = st.lists(st.tuples(st.integers(90, 110), st.integers(95, 105), st.integers(0, 60), st.integers(0, 50),
                              st.integers(0, 50), st.booleans()), min_size=40, max_size=90)


def _facts(rows) -> list[SlotFacts]:
    facts = []
    for index, (close, average, strength, plus, minus, missing) in enumerate(rows):
        at = T0 + HOUR * index
        if missing and index % 3 == 0:  # about one slot in six is missing
            facts.append(SlotFacts(close_time=at, present=False))
        else:
            facts.append(SlotFacts(close_time=at, present=True, close=D(close), average=D(average),
                                   strength=D(strength), plus=D(plus), minus=D(minus)))
    return facts


@settings(max_examples=300, deadline=None, derandomize=True)
@given(rows=readings, cut=st.integers(1, 89), history=st.integers(25, 35), minutes=st.integers(0, 59))
def test_a_longer_stretch_never_changes_an_earlier_label(rows, cut, history, minutes) -> None:
    facts = _facts(rows)
    cut = min(cut, len(facts))
    whole = slot_verdicts(facts, RULES, history=history)
    prefix = slot_verdicts(facts[:cut], RULES, history=history)
    assert prefix == whole[:cut]
    short, long = MarketTypeTimeline(prefix, RULES), MarketTypeTimeline(whole, RULES)
    for index in range(cut):
        moment = T0 + HOUR * index + timedelta(minutes=minutes)
        assert short.at(moment) == long.at(moment)
        assert long.at(moment).candle_close is None or long.at(moment).candle_close <= moment


@settings(max_examples=12, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(seed=st.integers(0, 2**32), holes=st.lists(st.integers(820, 940), max_size=3),
       delays=st.lists(st.integers(0, 3), min_size=6, max_size=6), offset=st.integers(-2, 70),
       minutes=st.integers(0, 59))
def test_a_live_label_never_changes_when_later_candles_arrive(tmp_path_factory, seed, holes, delays, offset,
                                                             minutes) -> None:
    rows = series("SYN/USD", closes_wandering(1000, seed), skip=set(holes))
    index = FIRST_LABELLED + 60 + offset
    moment = hourly_moment(index) + timedelta(minutes=minutes - 5)
    # The newest few candles arrive a few hours late; whatever arrived after the moment is not visible to it.
    delayed = [row.model_copy(update={"available_at_utc": row.close_time_utc + HOUR * delays[k % len(delays)]})
               if k >= len(rows) - 140 else row for k, row in enumerate(rows)]
    known = [row for row in delayed if row.available_at_utc <= moment]
    later = [row for row in delayed if row.available_at_utc > moment]
    revision = known[-3].model_copy(update={"close": known[-3].close + D("7"), "high": known[-3].high + D("7"),
                                            "available_at_utc": moment + HOUR})
    folder = tmp_path_factory.mktemp("labels")
    first_db, first = open_store(folder / "then.sqlite")
    second_db, second = open_store(folder / "later.sqlite")
    try:
        store_series(first, known, at=moment)
        store_series(second, known, at=moment)
        store_series(second, later + [revision], at=moment + 4 * HOUR)
        then = label_coin(first, symbol="SYN/USD", moment=moment, source="synthetic")
        now = label_coin(second, symbol="SYN/USD", moment=moment, source="synthetic")
        assert then.to_document() == now.to_document()
        assert then.label.candle_close is None or then.label.candle_close <= moment
    finally:
        first_db.close()
        second_db.close()
