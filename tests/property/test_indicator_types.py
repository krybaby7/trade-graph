"""Candle and money types stay Decimal through the indicator wrapper; only indicator outputs are floats.

Also: every in-bounds request on the menu is a valid library call, whatever the history looks like.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.application.indicators.library import columns_from_candles, compute_raw, lookback
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.indicators import INDICATOR_MENU, IndicatorSpec
from trade_graph.domain.errors import ValidationFailure

_START = datetime(2026, 3, 2, tzinfo=UTC)
_STEP = timedelta(minutes=5)


@st.composite
def candle_rows(draw) -> list[tuple[Decimal, Decimal, Decimal, Decimal, Decimal]]:
    """Valid OHLCV rows: positive prices with 4 decimals, low <= open/close <= high, non-negative volume."""
    count = draw(st.integers(0, 60))
    rows = []
    for _ in range(count):
        opened = draw(st.integers(1, 5_000_000))
        closed = draw(st.integers(1, 5_000_000))
        high = max(opened, closed) + draw(st.integers(0, 500_000))
        low = max(1, min(opened, closed) - draw(st.integers(0, 500_000)))
        volume = draw(st.integers(0, 10**9))
        rows.append((Decimal(opened).scaleb(-4), Decimal(high).scaleb(-4), Decimal(low).scaleb(-4),
                     Decimal(closed).scaleb(-4), Decimal(volume).scaleb(-3)))
    return rows


def _candles(rows) -> list[Candle]:
    candles = []
    for index, (opened, high, low, closed, volume) in enumerate(rows):
        start = _START + _STEP * index
        candles.append(Candle(
            symbol="SYN/USD", source_pair="SYNUSD", interval_minutes=5, open_time_utc=start,
            close_time_utc=start + _STEP, available_at_utc=start + _STEP + timedelta(seconds=2), open=opened,
            high=high, low=low, close=closed, volume=volume, trade_count=1, source="synthetic"))
    return candles


def _value_strategy(parameter):
    if parameter.kind == "int":
        return st.integers(parameter.minimum, parameter.maximum)
    return st.floats(parameter.minimum, parameter.maximum, allow_nan=False, allow_infinity=False)


@st.composite
def requests(draw) -> tuple[IndicatorSpec, dict]:
    """A menu entry with parameters drawn from its own bounds (bad orderings are repaired by swapping)."""
    spec = draw(st.sampled_from(INDICATOR_MENU))
    params = {parameter.name: draw(_value_strategy(parameter)) for parameter in spec.parameters}
    for rule in spec.constraints:
        low, high = sorted((params[rule.smaller], params[rule.larger]))
        if rule.strict and low == high:
            low = max(low - 1, 1) if isinstance(low, int) else low / 2
            if low == high:
                high += 1
        params[rule.smaller], params[rule.larger] = low, high
    return spec, params


def _is_valid(spec: IndicatorSpec, params: dict) -> bool:
    try:
        spec.resolve_parameters(params)
    except ValidationFailure:
        return False
    return True


@settings(max_examples=150, derandomize=True, deadline=None)
@given(rows=candle_rows(), call=requests())
def test_candles_and_columns_stay_decimal_and_outputs_are_float64(rows, call) -> None:
    spec, params = call
    if not _is_valid(spec, params):  # a repaired ordering can still leave a parameter out of its bounds
        return
    candles = _candles(rows)
    originals = [candle.model_copy() for candle in candles]
    columns = columns_from_candles(candles)
    snapshot = copy.deepcopy({name: list(values) for name, values in columns.items()})

    result = compute_raw(spec.name, params, columns)

    # Inputs: exactly the Decimals the candles carry, untouched, the very same objects.
    for name, values in columns.items():
        assert all(type(item) is Decimal for item in values)
        assert list(values) == snapshot[name]
        assert all(item is getattr(candle, name) for item, candle in zip(values, candles, strict=True))
    for candle, original in zip(candles, originals, strict=True):
        for field in ("open", "high", "low", "close", "volume"):
            assert type(getattr(candle, field)) is Decimal and getattr(candle, field) == getattr(original, field)
    # Outputs: float64 arrays, one per menu output, one value per candle, undefined only before the lookback.
    assert list(result) == [output.name for output in spec.outputs]
    start = min(lookback(spec.name, params), len(rows))
    for values in result.values():
        assert isinstance(values, np.ndarray) and values.dtype == np.float64 and values.shape == (len(rows),)
        assert np.isnan(values[:start]).all()
        assert not np.isinf(values).any()


@settings(max_examples=60, derandomize=True, deadline=None)
@given(
    rows=candle_rows().filter(lambda rows: len(rows) > 0),
    call=requests(),
    bad=st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.integers(), st.text(max_size=5),
                  st.booleans()),
)
def test_a_non_decimal_value_in_any_required_column_is_refused(rows, call, bad) -> None:
    spec, params = call
    if not _is_valid(spec, params):
        return
    columns = {name: list(values) for name, values in columns_from_candles(_candles(rows)).items()}
    field = spec.requires[0]
    columns[field][0] = bad
    try:
        compute_raw(spec.name, params, columns)
    except ValidationFailure as error:
        assert "Decimal" in str(error)
    else:  # pragma: no cover - would mean a float or int entered the computation
        raise AssertionError("a non-Decimal value was accepted")
