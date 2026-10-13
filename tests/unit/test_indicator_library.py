"""The thin TA-Lib wrapper: validate against the menu, convert Decimal to float64, call, label."""

from __future__ import annotations

import ast
import importlib.metadata
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
import talib
from talib import abstract
from tests.indicator_series import SERIES_SPECS, build_candles

from trade_graph.application.indicators import library
from trade_graph.application.indicators.library import (
    MAX_ROWS,
    IndicatorComputationError,
    columns_from_candles,
    compute_raw,
    library_versions,
    lookback,
)
from trade_graph.contracts.indicators import INDICATOR_MENU
from trade_graph.domain.errors import ValidationFailure

_LIBRARY_SOURCE = Path(library.__file__)
_SERIES = {name: columns_from_candles(build_candles(name)) for name in SERIES_SPECS}
# Longer than the largest warm-up any bound can ask for (STOCH at its maximum settings needs about 1,500).
_LONG = columns_from_candles(build_candles("range_burst", count=1600))
_EDGE = 1e-9


def _specs():
    return pytest.mark.parametrize("spec", INDICATOR_MENU, ids=lambda spec: spec.name)


@_specs()
def test_menu_entry_matches_the_library_signature(spec) -> None:
    assert hasattr(talib, spec.talib_function), spec.talib_function
    function = abstract.Function(spec.talib_function)
    library_inputs = []
    for value in function.input_names.values():
        library_inputs.extend(value if isinstance(value, list) else [value])
    assert len(library_inputs) == len(spec.inputs)
    assert list(function.output_names) and len(function.output_names) == len(spec.outputs)
    exposed = {parameter.name for parameter in spec.parameters}
    assert exposed | set(spec.fixed_parameters) == set(function.parameters)
    for name, value in spec.fixed_parameters.items():
        assert function.parameters[name] == value, (spec.name, name)
    for parameter in spec.parameters:
        assert type(function.parameters[parameter.name]) is type(parameter.default)


def _settings(spec, which):
    """All-minimum, all-default or all-maximum parameters, repaired so ordering rules still hold."""
    params = {parameter.name: getattr(parameter, which) for parameter in spec.parameters}
    for rule in spec.constraints:
        if rule.strict and params[rule.smaller] >= params[rule.larger]:
            params[rule.smaller] = next(p.minimum for p in spec.parameters if p.name == rule.smaller)
            params[rule.larger] = next(p.maximum for p in spec.parameters if p.name == rule.larger)
    return params


@_specs()
@pytest.mark.parametrize("which", ["minimum", "default", "maximum"])
def test_every_bound_is_a_valid_call_and_warmup_is_the_library_lookback(spec, which) -> None:
    params = _settings(spec, which)
    resolved = spec.resolve_parameters(params)
    function = abstract.Function(spec.talib_function)
    function.parameters = {**resolved, **spec.fixed_parameters}
    start = lookback(spec.name, params)
    assert start == function.lookback
    assert start < len(_LONG["close"]), "the long series must outlast the largest warm-up"
    result = compute_raw(spec.name, params, _LONG)
    for name, values in result.items():
        assert values.shape == (len(_LONG["close"]),), (spec.name, name)
        assert np.isnan(values[:start]).all() and np.isfinite(values[start:]).all(), (spec.name, name, which)


@_specs()
def test_declared_warmup_drivers_are_the_parameters_that_move_the_lookback(spec) -> None:
    base = {parameter.name: parameter.default for parameter in spec.parameters}
    reference = lookback(spec.name, base)
    for parameter in spec.parameters:
        candidates = [parameter.default + 1] if parameter.kind == "int" else [parameter.minimum, parameter.maximum]
        for value in candidates:
            changed = {**base, parameter.name: value}
            try:
                moved = lookback(spec.name, changed) != reference
            except ValidationFailure:
                continue  # that value breaks an ordering rule; try the next one
            assert moved == (parameter.name in spec.warmup.driven_by), (spec.name, parameter.name)
            break
        else:
            pytest.fail(f"no valid alternative value for {spec.name}.{parameter.name}")


@_specs()
@pytest.mark.parametrize("series", sorted(SERIES_SPECS))
def test_first_value_appears_exactly_at_the_library_lookback(spec, series) -> None:
    result = compute_raw(spec.name, {}, _SERIES[series])
    start = lookback(spec.name, {})
    count = len(_SERIES[series]["close"])
    assert list(result) == [output.name for output in spec.outputs]
    for name, values in result.items():
        assert values.dtype == np.float64 and values.shape == (count,), (spec.name, name)
        assert np.isnan(values[:start]).all(), (spec.name, name)
        assert np.isfinite(values[start:]).all(), (spec.name, name)


@_specs()
@pytest.mark.parametrize("series", sorted(SERIES_SPECS))
def test_outputs_stay_inside_their_declared_ranges(spec, series) -> None:
    result = compute_raw(spec.name, {}, _SERIES[series])
    for output in spec.outputs:
        values = result[output.name]
        values = values[np.isfinite(values)]
        assert values.size
        if output.lowest is not None:
            assert values.min() >= output.lowest - _EDGE, (spec.name, output.name)
        if output.highest is not None:
            assert values.max() <= output.highest + _EDGE, (spec.name, output.name)


def test_short_history_gives_undefined_values_not_an_error() -> None:
    columns = {name: values[:5] for name, values in _SERIES["trend_pullback"].items()}
    result = compute_raw("SMA", {"timeperiod": 20}, columns)
    assert result["value"].shape == (5,) and np.isnan(result["value"]).all()
    empty = {name: () for name in columns}
    assert compute_raw("RSI", {}, empty)["value"].shape == (0,)


def test_unneeded_columns_are_ignored_and_inputs_are_not_modified() -> None:
    columns = {name: list(values) for name, values in _SERIES["range_burst"].items()}
    snapshot = {name: list(values) for name, values in columns.items()}
    only_close = {"close": columns["close"]}
    assert compute_raw("SMA", {"timeperiod": 5}, only_close)["value"].shape == (600,)
    compute_raw("ATR", {}, columns)
    assert columns == snapshot
    assert all(type(item) is Decimal for values in columns.values() for item in values)


@pytest.mark.parametrize("name, params", [
    ("NOPE", {}), ("SMA", {"timeperiod": 1}), ("SMA", {"timeperiod": 14.0}), ("SMA", {"periods": 14}),
    ("MACD", {"fastperiod": 26, "slowperiod": 12}), ("BBANDS", {"nbdevup": float("nan")}),
])
def test_requests_outside_the_menu_are_refused_before_the_library_is_called(name, params, monkeypatch) -> None:
    monkeypatch.setattr(talib, "SMA", lambda *a, **k: pytest.fail("library called"))
    with pytest.raises(ValidationFailure):
        compute_raw(name, params, _SERIES["trend_pullback"])
    with pytest.raises(ValidationFailure):
        lookback(name, params)


def test_missing_ragged_and_oversized_inputs_are_refused() -> None:
    columns = _SERIES["trend_pullback"]
    with pytest.raises(ValidationFailure, match="volume"):
        compute_raw("MFI", {}, {key: columns[key] for key in ("high", "low", "close")})
    with pytest.raises(ValidationFailure, match="same length"):
        compute_raw("ATR", {}, {**columns, "low": columns["low"][:-1]})
    with pytest.raises(ValidationFailure):
        compute_raw("SMA", {}, [Decimal(1)])  # type: ignore[arg-type]
    with pytest.raises(ValidationFailure, match="rows"):
        compute_raw("SMA", {}, {"close": (Decimal(1),) * (MAX_ROWS + 1)})


@pytest.mark.parametrize("bad", [1.5, 3, "3.5", None, float("nan"), True])
def test_only_decimal_values_may_enter(bad) -> None:
    close = list(_SERIES["trend_pullback"]["close"][:30])
    close[7] = bad
    with pytest.raises(ValidationFailure, match="Decimal"):
        compute_raw("SMA", {}, {"close": close})


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), Decimal("1E+400"),
                                 Decimal("1E-400"), Decimal("0"), Decimal("-2")])
def test_unusable_prices_are_refused(bad) -> None:
    close = list(_SERIES["trend_pullback"]["close"][:30])
    close[7] = bad
    with pytest.raises(ValidationFailure):
        compute_raw("SMA", {}, {"close": close})


def test_volume_may_be_zero_but_not_negative() -> None:
    columns = {key: list(values[:40]) for key, values in _SERIES["trend_pullback"].items()}
    columns["volume"][3] = Decimal("0")
    assert compute_raw("OBV", {}, columns)["value"].shape == (40,)
    columns["volume"][3] = Decimal("-1")
    with pytest.raises(ValidationFailure, match="volume"):
        compute_raw("OBV", {}, columns)


def test_columns_from_candles_keeps_exact_decimals() -> None:
    candles = build_candles("trend_pullback", count=12)
    columns = columns_from_candles(candles)
    assert set(columns) == {"open", "high", "low", "close", "volume"}
    for field, values in columns.items():
        assert all(type(item) is Decimal for item in values)
        assert list(values) == [getattr(candle, field) for candle in candles]
    assert columns_from_candles([])["close"] == ()


def test_changed_library_state_is_refused_not_silently_used() -> None:
    columns = _SERIES["trend_pullback"]
    talib.set_unstable_period("EMA", 5)
    try:
        with pytest.raises(IndicatorComputationError, match="unstable"):
            compute_raw("EMA", {}, columns)
    finally:
        talib.set_unstable_period("EMA", 0)
    assert compute_raw("EMA", {}, columns)["value"].shape == (600,)


def test_unexpected_library_failures_are_not_swallowed(monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise Exception("TA_SMA function failed with error code 2")

    monkeypatch.setattr(talib, "SMA", broken)
    with pytest.raises(IndicatorComputationError, match="SMA"):
        compute_raw("SMA", {}, _SERIES["trend_pullback"])


def test_wrong_output_count_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(talib, "BBANDS", lambda *a, **k: (np.zeros(3), np.zeros(3)))
    with pytest.raises(IndicatorComputationError, match="outputs"):
        compute_raw("BBANDS", {}, {"close": (Decimal(1), Decimal(2), Decimal(3))})


def test_library_versions_are_reported_for_receipts() -> None:
    versions = library_versions()
    assert versions["ta_lib_python"] == importlib.metadata.version("TA-Lib") == talib.__version__
    assert versions["ta_lib_c"] == talib.__ta_version__.decode().split()[0]
    assert versions["numpy"] == np.__version__


def test_wrapper_contains_no_indicator_arithmetic() -> None:
    """Only conversion calls may touch numpy; any averaging, smoothing or windowing would show up here."""
    tree = ast.parse(_LIBRARY_SOURCE.read_text(encoding="utf-8"))
    allowed = {"float64", "fromiter", "asarray", "ndarray", "__version__"}
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "np"}
    assert used <= allowed, sorted(used - allowed)
    banned = {"sum", "mean", "cumsum", "convolve", "ewm", "rolling", "std", "diff", "maximum", "minimum"}
    assert not {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} & banned
