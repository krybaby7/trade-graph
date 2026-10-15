"""Every menu entry against two independent indicator libraries on two fixed synthetic series.

The comparison rules and every documented difference live in ``tests/fixtures/indicators/cross_check.json``
(rendered into docs/INDICATOR-MENU.md). A difference is never patched here: it is either inside the stated
bound, converges after the stated warm-up, or the test fails.
"""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import talib
from tests.indicator_scale import output_scale
from tests.indicator_second_library import pandas_ta_outputs, talipp_outputs
from tests.indicator_series import SERIES_SPECS, build_candles

from trade_graph.application.indicators.library import columns_from_candles, compute_raw, lookback
from trade_graph.contracts.indicators import INDICATOR_MENU, IndicatorSpec, get_indicator

_ROOT = Path(__file__).resolve().parents[2]
_TABLE = json.loads((_ROOT / "tests" / "fixtures" / "indicators" / "cross_check.json").read_text(encoding="utf-8"))
_LIBRARIES = {"pandas_ta_classic": pandas_ta_outputs, "talipp": talipp_outputs}
_DISTRIBUTIONS = {"pandas_ta_classic": "pandas-ta-classic", "talipp": "talipp"}
_MODES = {"match", "converges", "state_dependent", "not_covered"}
_COLUMNS = {name: columns_from_candles(build_candles(name)) for name in SERIES_SPECS}
_FLOATS = {name: {field: np.array([float(item) for item in values]) for field, values in columns.items()}
           for name, columns in _COLUMNS.items()}
_DEFAULT_TOLERANCE = _TABLE["default_tolerance"]
_SERIES = tuple(_TABLE["series"])
_DOCUMENTED = [(library, entry) for library, body in _TABLE["libraries"].items()
               for entry, rule in body["entries"].items() if rule["mode"] in ("converges", "state_dependent")]


def _rule(library: str, entry: str) -> dict:
    return _TABLE["libraries"][library]["entries"].get(entry, {"mode": "match"})


def _requests(library: str, spec: IndicatorSpec) -> list[tuple[dict, bool]]:
    """Parameter sets to compare, each flagged as the default set; honours ``applies_when``."""
    wanted = _rule(library, spec.name).get("applies_when", {})
    found = []
    for index, variant in enumerate([{}, *_TABLE["variants"][spec.name]]):
        params = spec.resolve_parameters(variant)
        if all(params[name] == value for name, value in wanted.items()):
            found.append((params, index == 0))
    return found


def _no_holes(values: np.ndarray) -> None:
    first = int(np.argmax(~np.isnan(values)))
    assert not np.isnan(values[first:]).any(), "a library left a gap after its first value"


def _differences(library: str, spec: IndicatorSpec, params: dict, series: str):
    """Per output: TA-Lib values, the other library's values, scaled differences, and where both are defined."""
    ours = compute_raw(spec.name, params, _COLUMNS[series])
    theirs = _LIBRARIES[library](spec.name, params, _FLOATS[series])
    assert theirs is not None and set(theirs) == set(ours)
    for output in spec.outputs:
        a, b = ours[output.name], np.asarray(theirs[output.name], dtype=np.float64)
        assert a.shape == b.shape
        _no_holes(a)
        _no_holes(b)
        both = ~np.isnan(a) & ~np.isnan(b)
        assert both.sum() >= len(a) // 2, f"{library} {spec.name}.{output.name} overlaps TA-Lib on too few candles"
        yield output, a, b, np.abs(a - b) / output_scale(output.unit, a), both


def _longest_run(flags: np.ndarray) -> int:
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def _check(library: str, spec: IndicatorSpec, params: dict, series: str, is_default: bool) -> None:
    rule = _rule(library, spec.name)
    mode = rule["mode"]
    start = lookback(spec.name, params)
    for output, a, b, diff, both in _differences(library, spec, params, series):
        where = np.where(both)[0]
        label = f"{library} {spec.name}.{output.name} {params} {series}"
        if mode == "match":
            tolerance = rule.get("tolerance", _DEFAULT_TOLERANCE)
            assert diff[where].max() <= tolerance, f"{label}: {diff[where].max():.3e} > {tolerance}"
        elif mode == "converges":
            settle = start + rule["settled_periods"] * params[rule["parameter"]]
            early, late = where[where < settle], where[where >= settle]
            if early.size:
                assert diff[early].max() <= rule["early_bound"], f"{label}: early {diff[early].max():.3e}"
            if late.size:
                assert diff[late].max() <= rule["settled_tolerance"], f"{label}: settled {diff[late].max():.3e}"
            if is_default:
                assert late.size >= 50, f"{label}: the settled region is too short to mean anything"
        else:
            agree = diff[where] <= _DEFAULT_TOLERANCE
            assert agree.mean() >= rule["min_agreement"], f"{label}: agreement {agree.mean():.3f}"
            assert _longest_run(~agree) <= rule["max_disagreement_run"], f"{label}: run {_longest_run(~agree)}"
            assert diff[where].max() <= rule["max_relative_difference"], f"{label}: {diff[where].max():.3e}"
            close = _FLOATS[series]["close"]
            same_side = ((a < close)[where] == (b < close)[where]).mean()
            assert same_side >= rule["min_same_side"], f"{label}: same side {same_side:.3f}"


@pytest.mark.parametrize("series", _SERIES)
@pytest.mark.parametrize("library", sorted(_LIBRARIES))
@pytest.mark.parametrize("spec", INDICATOR_MENU, ids=lambda spec: spec.name)
def test_talib_agrees_with_an_independent_library(spec, library, series) -> None:
    if _rule(library, spec.name)["mode"] == "not_covered":
        pytest.skip(_rule(library, spec.name)["reason"])
    requests = _requests(library, spec)
    assert requests, f"no parameter set is compared for {library} {spec.name}"
    for params, is_default in requests:
        _check(library, spec, params, series, is_default)


@pytest.mark.parametrize("spec", INDICATOR_MENU, ids=lambda spec: spec.name)
def test_every_entry_is_covered_by_the_second_library_of_record(spec) -> None:
    assert _rule("pandas_ta_classic", spec.name)["mode"] != "not_covered"
    assert _requests("pandas_ta_classic", spec)


@pytest.mark.parametrize("library, entry", _DOCUMENTED)
def test_each_documented_difference_really_exists(library, entry) -> None:
    """A table row that no longer describes a difference is stale and must be removed from the docs."""
    spec = get_indicator(entry)
    rule = _rule(library, entry)
    params, _ = _requests(library, spec)[0]
    seen = False
    for series in _SERIES:
        for output, a, b, diff, both in _differences(library, spec, params, series):
            where = np.where(both)[0]
            if rule["mode"] == "converges":
                settle = lookback(entry, params) + rule["settled_periods"] * params[rule["parameter"]]
                where = where[where < settle]
            seen = seen or bool(where.size and diff[where].max() > _DEFAULT_TOLERANCE)
    assert seen, f"{library} {entry} no longer differs from TA-Lib; drop the documented difference"


def test_table_is_well_formed_and_complete() -> None:
    names = {spec.name for spec in INDICATOR_MENU}
    assert set(_TABLE["variants"]) == names
    for spec in INDICATOR_MENU:
        for variant in _TABLE["variants"][spec.name]:
            assert spec.resolve_parameters(variant)
    assert set(_TABLE["libraries"]) == set(_LIBRARIES)
    assert set(_SERIES) == set(SERIES_SPECS)
    for library, body in _TABLE["libraries"].items():
        assert body["role"] and body["version_checked"]
        assert importlib.metadata.version(_DISTRIBUTIONS[library]) == body["version_checked"], (
            f"{library} changed version; re-run the cross-check and update the table and docs")
        for entry, rule in body["entries"].items():
            spec = get_indicator(entry)
            assert rule["mode"] in _MODES and len(rule["reason"]) > 20, (library, entry)
            if rule["mode"] == "converges":
                assert rule["parameter"] in {parameter.name for parameter in spec.parameters}
                assert rule["settled_periods"] >= 1 and 0 < rule["settled_tolerance"] < 1e-3
                assert rule["early_bound"] > rule["settled_tolerance"]
            if rule["mode"] == "state_dependent":
                assert 0 < rule["min_agreement"] < 1 and rule["max_disagreement_run"] >= 1
            for name in rule.get("applies_when", {}):
                assert name in {parameter.name for parameter in spec.parameters}
    assert {entry for entry, rule in _TABLE["libraries"]["pandas_ta_classic"]["entries"].items()
            if rule["mode"] == "not_covered"} == set()


def test_second_libraries_have_no_hard_dependency_on_talib() -> None:
    for distribution in _DISTRIBUTIONS.values():
        for requirement in importlib.metadata.requires(distribution) or []:
            if "ta-lib" in requirement.lower() or "talib" in requirement.lower():
                assert "extra ==" in requirement, f"{distribution} requires TA-Lib outright: {requirement}"


def test_second_libraries_give_identical_numbers_with_talib_made_unimportable() -> None:
    """Run every adapter in a fresh process where ``import talib`` fails; the numbers must not change."""
    series = _SERIES[0]
    requests = []
    for library in _LIBRARIES:
        for spec in INDICATOR_MENU:
            if _rule(library, spec.name)["mode"] == "not_covered":
                continue
            requests.extend({"library": library, "name": spec.name, "params": params}
                            for params, _ in _requests(library, spec))
    payload = {"columns": {field: values.tolist() for field, values in _FLOATS[series].items()},
               "requests": requests}
    result = subprocess.run([sys.executable, "-m", "tests.indicator_second_library"], input=json.dumps(payload),
                            capture_output=True, text=True, cwd=_ROOT, timeout=300, check=False)
    assert result.returncode == 0, result.stderr[-3000:]
    child = json.loads(result.stdout)
    assert len(child) == len(requests)
    for item in requests:
        key = f"{item['library']}:{item['name']}:{json.dumps(item['params'], sort_keys=True)}"
        here = _LIBRARIES[item["library"]](item["name"], item["params"], _FLOATS[series])
        assert here is not None and set(child[key]) == set(here)
        for output, values in here.items():
            assert np.array_equal(np.array(child[key][output], dtype=np.float64), values, equal_nan=True), key


def test_talib_macd_fast_average_is_seeded_where_the_slow_average_starts() -> None:
    """Rebuild TA-Lib's MACD from TA-Lib's own EMA to show why it differs from two ordinary EMAs early on."""
    closes = _FLOATS["trend_pullback"]["close"]
    fast, slow, signal = 12, 26, 9
    fast_line = np.full(len(closes), np.nan)
    fast_line[slow - fast:] = talib.EMA(closes[slow - fast:], fast)  # starts at index slow - 1
    line = fast_line - talib.EMA(closes, slow)
    signal_line = np.full(len(closes), np.nan)
    signal_line[slow - 1:] = talib.EMA(line[slow - 1:], signal)
    got = compute_raw("MACD", {}, _COLUMNS["trend_pullback"])
    start = lookback("MACD", {})
    scale = output_scale("price", got["macd"])  # the MACD line is judged against its own size, not the price
    assert np.nanmax(np.abs(got["macd"][start:] - line[start:])) <= 1e-9 * scale
    assert np.nanmax(np.abs(got["signal"][start:] - signal_line[start:])) <= 1e-9 * scale
    # Two ordinary EMAs (what the other libraries run) start the fast average at index fast - 1 and differ early.
    ordinary = talib.EMA(closes, fast) - talib.EMA(closes, slow)
    assert np.nanmax(np.abs(got["macd"][start:start + 20] - ordinary[start:start + 20])) > 1e-9 * scale
