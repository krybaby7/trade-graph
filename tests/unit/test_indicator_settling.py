"""Recursively seeded indicators change with the length of history; the contract's settling guidance bounds how much.

TA-Lib is run on the full history and again on histories that start later, by a few candles and by a good
fraction of the setting itself. Past the guided number of extra candles the runs must agree to
``SETTLE_TOLERANCE`` of the size of the output being read: its own typical size, or absolute points for the 0 to
100 readings (``tests/indicator_scale.py``). A tolerance stated against the price would let the MACD histogram or
the ATR be wrong by as much as their own size. This measures TA-Lib against itself, so it needs no second library
and no in-house formula.
"""

from __future__ import annotations

import numpy as np
import pytest
from tests.indicator_scale import output_scale
from tests.indicator_series import SERIES_SPECS, build_candles

from trade_graph.application.indicators.library import columns_from_candles, compute_raw, lookback
from trade_graph.contracts.indicators import INDICATOR_MENU, SETTLE_TOLERANCE, IndicatorSpec

_ROWS = 4200
_COLUMNS = {name: columns_from_candles(build_candles(name, count=_ROWS)) for name in SERIES_SPECS}
# Parameter sets per recursive entry: the default, small and medium settings, and one large setting (the seed of a
# long average is further from its value, so a guide that holds for small settings can fail for large ones).
_VARIANTS = {
    "EMA": [{}, {"timeperiod": 7}, {"timeperiod": 50}, {"timeperiod": 200}],
    "DEMA": [{}, {"timeperiod": 7}, {"timeperiod": 40}, {"timeperiod": 200}],
    "RSI": [{}, {"timeperiod": 7}, {"timeperiod": 30}, {"timeperiod": 100}],
    "MACD": [{}, {"fastperiod": 5, "slowperiod": 13, "signalperiod": 4},
             {"fastperiod": 24, "slowperiod": 52, "signalperiod": 18},
             {"fastperiod": 60, "slowperiod": 120, "signalperiod": 40}],
    "ADX": [{}, {"timeperiod": 7}, {"timeperiod": 30}, {"timeperiod": 60}],
    "PLUS_DI": [{}, {"timeperiod": 7}, {"timeperiod": 30}, {"timeperiod": 60}],
    "MINUS_DI": [{}, {"timeperiod": 7}, {"timeperiod": 30}, {"timeperiod": 60}],
    "ATR": [{}, {"timeperiod": 7}, {"timeperiod": 50}, {"timeperiod": 150}],
    "NATR": [{}, {"timeperiod": 7}, {"timeperiod": 30}, {"timeperiod": 150}],
}
_SETTLED = [spec for spec in INDICATOR_MENU if spec.settle is not None]


def _starts(setting: int) -> tuple[int, ...]:
    """Later starts: a few candles, and fractions of the setting, since a long average's seed moves with its length."""
    return tuple(sorted({1, 13, 61, 97, int(0.37 * setting) + 1, int(0.83 * setting) + 1, int(1.3 * setting) + 1,
                         2 * setting + 7}))


def _worst_gap(spec: IndicatorSpec, params: dict, series: str, periods: float) -> tuple[float, int]:
    """Largest scaled difference between a full run and later-starting runs once `periods` have settled."""
    columns = _COLUMNS[series]
    full = compute_raw(spec.name, params, columns)
    setting = spec.resolve_parameters(params)[spec.settle.parameter]
    extra = int(round(periods * setting))
    worst, compared = 0.0, 0
    for start in _starts(setting):
        part = compute_raw(spec.name, params, {name: values[start:] for name, values in columns.items()})
        first = start + lookback(spec.name, params) + extra
        assert first < _ROWS - 200, "the guided span leaves too little of the series to compare"
        for output in spec.outputs:
            scale = output_scale(output.unit, full[output.name])
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


def _sar_agreement(series: str, extra: int, shift: int) -> float:
    """Share of sampled end candles where two SAR readings, one starting `shift` candles later, give the same number."""
    columns = _COLUMNS[series]
    scale = float(np.median(np.array([float(item) for item in columns["close"]])))
    span = lookback("SAR", {}) + extra + 1
    agreed = total = 0
    for end in range(1500, 4000, 11):
        window = {name: values[end - span + 1: end + 1] for name, values in columns.items()}
        later = {name: values[end - span + 1 + shift: end + 1] for name, values in columns.items()}
        gap = abs(compute_raw("SAR", {}, window)["value"][-1] - compute_raw("SAR", {}, later)["value"][-1])
        agreed += gap / scale <= 1e-9
        total += 1
    return agreed / total


@pytest.mark.parametrize("series", sorted(SERIES_SPECS))
def test_the_fixed_history_given_to_parabolic_sar_makes_nearby_starts_agree(series) -> None:
    """The one number without a Settling entry is measured here: SAR forgets its start at a reversal, so with
    START_DEPENDENT_HISTORY candles of extra history two readings of one candle that start a few candles apart
    give the same level on every sampled candle, and a quarter of that history does not."""
    from trade_graph.application.indicators.engine import START_DEPENDENT_HISTORY

    for shift in (1, 13, 61):
        assert _sar_agreement(series, START_DEPENDENT_HISTORY, shift) == 1.0, (series, shift)
    assert min(_sar_agreement(series, START_DEPENDENT_HISTORY // 4, shift) for shift in (1, 13)) < 0.995


@pytest.mark.parametrize("series", sorted(SERIES_SPECS))
def test_on_balance_volume_has_no_settled_level_only_changes_agree_between_starts(series) -> None:
    """OBV is a running total, so its level depends on where the history began by a fixed offset. It never settles
    (hence no Settling entry); what is start-independent is the change from one candle to the next."""
    columns = _COLUMNS[series]
    full = compute_raw("OBV", {}, columns)["value"]
    part = compute_raw("OBV", {}, {name: values[61:] for name, values in columns.items()})["value"]
    offset = full[61:] - part
    assert abs(offset).max() > 0.0  # the level differs between starts ...
    assert np.ptp(offset) <= 1e-6 * np.max(np.abs(full))  # ... by one constant, so the changes are the same


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
        scale = output_scale(output.unit, full[output.name])
        gap = np.abs(full[output.name][first:] - part[output.name][first - 100:]) / scale
        assert gap.max() <= 1e-9, (name, output.name, gap.max())
