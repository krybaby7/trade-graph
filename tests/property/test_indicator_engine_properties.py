"""Properties of the engine: money stays Decimal, a missing slot always withholds values, comparisons are exact."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.indicator_results import make_result

from trade_graph.application.indicators import engine
from trade_graph.application.indicators.engine import (
    RELATIONS,
    CandleSelectionError,
    compare_to_level,
    compute_from_window,
    plan_history,
)
from trade_graph.application.indicators.library import SNAPSHOT_DIGITS, rounded_decimal, rounded_significant
from trade_graph.contracts.candles import Candle, CandleSlot, CandleWindow, StoredCandle
from trade_graph.contracts.indicators import INDICATOR_MENU, IndicatorSpec
from trade_graph.domain.errors import ValidationFailure

_START = datetime(2026, 4, 6, tzinfo=UTC)
_INTERVALS = (1, 5, 15, 60)


def _small_parameter(parameter):
    if parameter.kind == "int":
        return st.integers(parameter.minimum, min(parameter.maximum, 12))
    high = min(parameter.maximum, parameter.minimum + 1.0)
    return st.floats(parameter.minimum, high, allow_nan=False, allow_infinity=False)


@st.composite
def small_requests(draw) -> tuple[IndicatorSpec, dict]:
    spec = draw(st.sampled_from(INDICATOR_MENU))
    params = {parameter.name: draw(_small_parameter(parameter)) for parameter in spec.parameters}
    try:
        spec.resolve_parameters(params)
    except ValidationFailure:
        params = {}  # a drawn ordering broke a rule: fall back to the defaults, which always satisfy it
    return spec, params


def prices(seed: int, size: int) -> list[tuple[Decimal, Decimal, Decimal, Decimal, Decimal]]:
    """Valid OHLCV rows from a seed, so a long window costs one hypothesis draw, not thousands."""
    rng = random.Random(seed)
    rows = []
    for _ in range(size):
        closed = rng.randint(10_000, 400_000)
        opened = max(1, closed + rng.randint(-3_000, 3_000))
        high = max(opened, closed) + rng.randint(0, 2_000)
        low = max(1, min(opened, closed) - rng.randint(0, 2_000))
        rows.append((Decimal(opened).scaleb(-2), Decimal(high).scaleb(-2), Decimal(low).scaleb(-2),
                     Decimal(closed).scaleb(-2), Decimal(rng.randint(0, 10**6)).scaleb(-3)))
    return rows


def _window(rows, missing: set[int], interval: int, *, as_of: datetime | None = None,
            mode: str = "point_in_time") -> CandleWindow:
    step = timedelta(minutes=interval)
    slots = []
    for index, (opened, high, low, closed, volume) in enumerate(rows):
        start = _START + step * index
        stored = None
        if index not in missing:
            candle = Candle(
                symbol="SYN/USD", source_pair="SYNUSD", interval_minutes=interval, open_time_utc=start,
                close_time_utc=start + step, available_at_utc=start + step + timedelta(seconds=2), open=opened,
                high=high, low=low, close=closed, volume=volume, trade_count=1, source="synthetic")
            stored = StoredCandle(candle=candle, revision=1, run_id=1)
        slots.append(CandleSlot(close_time_utc=start + step, stored=stored))
    reference = as_of or slots[-1].close_time_utc + timedelta(seconds=2)
    return CandleWindow(symbol="SYN/USD", interval_minutes=interval, source="synthetic", as_of_utc=reference,
                        mode=mode, slots=tuple(slots))


@st.composite
def scenarios(draw):
    spec, params = draw(small_requests())
    interval = draw(st.sampled_from(_INTERVALS))
    points = draw(st.integers(1, 4))
    span = plan_history(spec.name, params, points).span
    rows = prices(draw(st.integers(0, 2**32)), span)
    missing = draw(st.one_of(
        st.just(frozenset()),
        st.sets(st.integers(0, span - 1), min_size=1, max_size=min(span, 6)).map(frozenset),
        st.integers(1, span).map(lambda k: frozenset(range(k))),  # the oldest k slots: history not there yet
    ))
    return spec, params, interval, points, rows, missing, draw(st.booleans())


@settings(max_examples=120, derandomize=True, deadline=None)
@given(scenario=scenarios())
def test_a_missing_slot_always_withholds_values_and_is_named_exactly(scenario) -> None:
    spec, params, interval, points, rows, missing, older = scenario
    window = _window(rows, set(missing), interval)
    with mock.patch.object(engine, "compute_raw", wraps=engine.compute_raw) as library:
        outcome = compute_from_window(spec.name, params, window, count=points, older_history=older)

    assert outcome.required_candles == len(rows)
    assert outcome.missing_close_times_utc == tuple(slot.close_time_utc for index, slot in enumerate(window.slots)
                                                   if index in missing)
    assert outcome.available_candles == len(rows) - len(missing)
    if not missing:
        assert outcome.status == "ready" and library.call_count == 1
        assert list(outcome.values) == [output.name for output in spec.outputs]
        for series in outcome.values.values():
            assert len(series) == points and all(type(x) is float and math.isfinite(x) for x in series)
        assert outcome.close_times_utc == tuple(slot.close_time_utc for slot in window.slots[-points:])
    else:
        assert library.call_count == 0  # the library is never even called on a window with a hole
        assert outcome.values == {} and outcome.close_times_utc == ()
        leading_only = missing == frozenset(range(len(missing)))
        # Only the oldest slots missing is a young history, unless candles exist before the window: then it is a hole.
        # A window with no candle at all shows nothing about when the history began, so that is a hole too.
        young = leading_only and not older and len(missing) < len(rows)
        assert outcome.status == ("warming_up" if young else "gapped")


@settings(max_examples=60, derandomize=True, deadline=None)
@given(scenario=scenarios())
def test_candles_stay_decimal_and_unchanged_through_the_engine(scenario) -> None:
    spec, params, interval, points, rows, missing, _ = scenario
    window = _window(rows, set(missing), interval)
    before = [(slot.stored.candle, slot.stored.candle.model_dump()) for slot in window.slots if slot.stored]
    outcome = compute_from_window(spec.name, params, window, count=points)
    for candle, dump in before:
        assert candle.model_dump() == dump
        assert all(type(getattr(candle, field)) is Decimal for field in ("open", "high", "low", "close", "volume"))
    for series in outcome.values.values():
        assert all(type(x) is float for x in series)
    for use in outcome.candles_used:
        assert type(use.revision) is int and type(use.run_id) is int


@settings(max_examples=40, derandomize=True, deadline=None)
@given(scenario=scenarios(), mode=st.sampled_from(["point_in_time", "replay"]))
def test_a_candle_that_closes_after_as_of_is_never_used(scenario, mode) -> None:
    spec, params, interval, points, rows, _, _ = scenario
    # In replay mode only the close time can be wrong; in point-in-time mode the receipt time is wrong as well.
    window = _window(rows, set(), interval, as_of=_START + timedelta(minutes=interval * len(rows) - 1), mode=mode)
    with pytest.raises(CandleSelectionError):
        compute_from_window(spec.name, params, window, count=points)


# --- exact comparison ----------------------------------------------------------------------------------------


_floats = st.floats(allow_nan=False, allow_infinity=False, width=64)
_levels = st.one_of(
    # The engine refuses levels with more than MAX_LEVEL_DIGITS digits on purpose; those are not alarm levels.
    st.decimals(allow_nan=False, allow_infinity=False, places=None, min_value=Decimal("-1e12"),
                max_value=Decimal("1e12")).filter(lambda x: len(x.as_tuple().digits) <= engine.MAX_LEVEL_DIGITS),
    st.integers(-10**6, 10**6).map(Decimal),
)


@settings(max_examples=300, derandomize=True, deadline=None)
@given(value=_floats, level=_levels)
def test_exactly_one_of_above_below_equal_at_twelve_significant_digits(value, level) -> None:
    shown, limit = rounded_decimal(value), rounded_significant(level)
    assert len(shown.normalize().as_tuple().digits) <= SNAPSHOT_DIGITS
    above = compare_to_level(make_result([value]), level, "above").satisfied
    below = compare_to_level(make_result([value]), level, "below").satisfied
    assert above == (shown > limit) and below == (shown < limit)
    assert [above, below, shown == limit].count(True) == 1
    assert compare_to_level(make_result([value]), level, "above").value == shown


@settings(max_examples=300, derandomize=True, deadline=None)
@given(digits=st.integers(-10**SNAPSHOT_DIGITS + 1, 10**SNAPSHOT_DIGITS - 1), exponent=st.integers(-18, 12))
def test_a_level_with_at_most_twelve_digits_equals_the_nearest_float(digits, exponent) -> None:
    level = Decimal(digits).scaleb(exponent)
    value = float(level)
    if not math.isfinite(value) or (value == 0.0 and level != 0):
        return
    assert rounded_decimal(value) == level
    assert not compare_to_level(make_result([value]), level, "above").satisfied
    assert not compare_to_level(make_result([value]), level, "below").satisfied


@settings(max_examples=300, derandomize=True, deadline=None)
@given(low=_floats, high=_floats, level=_levels)
def test_comparison_is_monotonic_in_the_value(low, high, level) -> None:
    low, high = sorted((low, high))
    if compare_to_level(make_result([low]), level, "above").satisfied:
        assert compare_to_level(make_result([high]), level, "above").satisfied
    if compare_to_level(make_result([high]), level, "below").satisfied:
        assert compare_to_level(make_result([low]), level, "below").satisfied


@settings(max_examples=300, derandomize=True, deadline=None)
@given(previous=_floats, latest=_floats, level=_levels)
def test_crossings_are_defined_by_the_two_points_and_the_same_exact_comparison(previous, latest, level) -> None:
    both = make_result([previous, latest])
    before, now, limit = rounded_decimal(previous), rounded_decimal(latest), rounded_significant(level)
    assert compare_to_level(both, level, "crosses_above").satisfied == (before <= limit < now)
    assert compare_to_level(both, level, "crosses_below").satisfied == (before >= limit > now)
    assert not (compare_to_level(both, level, "crosses_above").satisfied
                and compare_to_level(both, level, "crosses_below").satisfied)


@settings(max_examples=60, derandomize=True, deadline=None)
@given(status=st.sampled_from(["warming_up", "gapped"]), relation=st.sampled_from(RELATIONS), level=_levels)
def test_an_unready_indicator_never_satisfies_any_comparison(status, relation, level) -> None:
    assert not compare_to_level(make_result([], status=status), level, relation).satisfied
