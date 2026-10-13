"""Recursively seeded indicators change with the length of history; the contract's settling guidance bounds how much.

TA-Lib is run on the full history and again on a history that starts later. Past the guided number of extra
candles the two runs must agree to ``SETTLE_TOLERANCE`` of the output scale. This measures TA-Lib against
itself, so it needs no second library and no in-house formula.
"""

from __future__ import annotations

import numpy as np
import pytest
from tests.indicator_series import SERIES_SPECS, build_candles

from trade_graph.application.indicators.library import columns_from_candles, compute_raw, lookback
from trade_graph.contracts.indicators import INDICATOR_MENU, SETTLE_TOLERANCE, IndicatorSpec

_ROWS = 1600
_STARTS = (1, 13, 61)
_COLUMNS = {name: columns_from_candles(build_candles(name, count=_ROWS)) for name in SERIES_SPECS}
_CLOSES = {name: np.array([float(item) for item in columns["close"]]) for name, columns in _COLUMNS.items()}
# Parameter sets per recursive entry: the default and two others that keep the guided span inside the series.
_VARIANTS = {
    "EMA": [{}, {"timeperiod": 7}, {"timeperiod": 50}],
    "DEMA": [{}, {"timeperiod": 7}, {"timeperiod": 40}],
    "RSI": [{}, {"timeperiod": 7}, {"timeperiod": 30}],
    "MACD": [{}, {"fastperiod": 5, "slowperiod": 13, "signalperiod": 4},
             {"fastperiod": 24, "slowperiod": 52, "signalperiod": 18}],
    "ADX": [{}, {"timeperiod": 7}, {"timeperiod": 30}],
    "PLUS_DI": [{}, {"timeperiod": 7}, {"timeperiod": 30}],
    "MINUS_DI": [{}, {"timeperiod": 7}, {"timeperiod": 30}],
    "ATR": [{}, {"timeperiod": 7}, {"timeperiod": 50}],
    "NATR": [{}, {"timeperiod": 7}, {"timeperiod": 30}],
}
_SETTLED = [spec for spec in INDICATOR_MENU if spec.settle is not None]


def _worst_gap(spec: IndicatorSpec, params: dict, series: str, periods: float) -> tuple[float, int]:
    """Largest scaled difference between a full run and later-starting runs once `periods` have settled."""
    columns = _COLUMNS[series]
    full = compute_raw(spec.name, params, columns)
    extra = int(round(periods * spec.resolve_parameters(params)[spec.settle.parameter]))
    worst, compared = 0.0, 0
    for start in _STARTS:
        part = compute_raw(spec.name, params, {name: values[start:] for name, values in columns.items()})
        first = start + lookback(spec.name, params) + extra
        assert first < _ROWS - 200, "the guided span leaves too little of the series to compare"
        for output in spec.outputs:
            scale = float(np.median(_CLOSES[series])) if output.unit == "price" else 1.0
            gap = np.abs(full[output.name][first:] - part[output.name][first - start:]) / scale
            worst, compared = max(worst, float(gap.max())), compared + gap.size
    return worst, compared


def test_every_recursive_entry_except_the_path_dependent_ones_has_guidance() -> None:
    recursive = {spec.name for spec in INDICATOR_MENU if spec.recursive_seed}
    assert {spec.name for spec in _SETTLED} == set(_VARIANTS)
    # SAR remembers its trend and OBV is a running total: neither forgets its start, so neither has guidance.
    assert recursive - {spec.name for spec in _SETTLED} == {"SAR", "OBV"}


@pytest.mark.parametrize("series", sorted(SERIES_SPECS))
@pytest.mark.parametrize("spec", _SETTLED, ids=lambda spec: spec.name)
def test_history_beyond_the_guided_span_changes_values_by_less_than_the_tolerance(spec, series) -> None:
    for params in _VARIANTS[spec.name]:
        worst, compared = _worst_gap(spec, params, series, spec.settle.periods)
        assert compared > 300
        assert worst <= SETTLE_TOLERANCE, f"{spec.name} {params} {series}: {worst:.3e}"


@pytest.mark.parametrize("spec", _SETTLED, ids=lambda spec: spec.name)
def test_the_guided_span_is_not_wildly_larger_than_needed(spec) -> None:
    """A third of the guidance must still leave a visible effect somewhere, so the numbers stay honest."""
    visible = []
    for series in sorted(SERIES_SPECS):
        worst, _ = _worst_gap(spec, {}, series, spec.settle.periods / 3)
        visible.append(worst > SETTLE_TOLERANCE)
    assert any(visible), f"{spec.name}: guidance could be shorter"


@pytest.mark.parametrize("name", ["SMA", "WMA", "STOCH", "CCI", "MFI", "ROC", "AROON", "BBANDS"])
def test_window_indicators_do_not_depend_on_how_much_history_precedes_the_window(name) -> None:
    """The contrast case: indicators marked non-recursive agree to rounding error once both runs have a value."""
    spec = next(item for item in INDICATOR_MENU if item.name == name)
    assert not spec.recursive_seed
    columns = _COLUMNS["trend_pullback"]
    full = compute_raw(name, {}, columns)
    part = compute_raw(name, {}, {field: values[100:] for field, values in columns.items()})
    first = 100 + lookback(name, {})
    for output in spec.outputs:
        scale = float(np.median(_CLOSES["trend_pullback"])) if output.unit == "price" else 1.0
        gap = np.abs(full[output.name][first:] - part[output.name][first - 100:]) / scale
        assert gap.max() <= 1e-9, (name, output.name, gap.max())
