"""The indicator menu contract: bounded parameters, explicit inputs and outputs, no library import."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.contracts.indicators import (
    INDICATOR_MENU,
    MAX_PERIOD,
    IndicatorOutput,
    IndicatorParameter,
    IndicatorSpec,
    ParameterOrder,
    WarmUp,
    get_indicator,
    menu_names,
)
from trade_graph.domain.errors import ValidationFailure

_CONTRACT = Path(__file__).resolve().parents[2] / "src" / "trade_graph" / "contracts" / "indicators.py"
_REQUIRED = {
    "SMA", "EMA", "WMA", "DEMA", "BBANDS", "SAR", "MIDPRICE", "HIGHEST_HIGH", "LOWEST_LOW", "RSI", "MACD",
    "STOCH", "CCI", "MFI", "WILLR", "ROC", "ROCP", "MOM", "ADX", "PLUS_DI", "MINUS_DI", "AROON", "ATR", "NATR", "OBV",
    "STDDEV",
}


def _parameter(**changes) -> IndicatorParameter:
    values = dict(name="timeperiod", kind="int", default=14, minimum=2, maximum=500, meaning="Candles in the window.")
    values.update(changes)
    return IndicatorParameter(**values)


def _spec(**changes) -> IndicatorSpec:
    values = dict(
        name="TEST", talib_function="SMA", group="moving_average", title="Test average",
        description="A test indicator for the contract.", inputs=("close",), parameters=(_parameter(),),
        outputs=(IndicatorOutput(name="value", unit="price", meaning="The average."),),
        warmup=WarmUp(driven_by=("timeperiod",), note="Needs one window of candles."), recursive_seed=False,
    )
    values.update(changes)
    return IndicatorSpec(**values)


def test_menu_covers_the_required_day_trading_set_and_stays_a_menu() -> None:
    assert _REQUIRED <= set(menu_names())
    assert 22 <= len(INDICATOR_MENU) <= 32
    assert len(set(menu_names())) == len(INDICATOR_MENU)
    assert get_indicator("RSI").talib_function == "RSI"
    with pytest.raises(ValidationFailure):
        get_indicator("NOT_IN_THE_MENU")


def test_every_entry_is_bounded_and_self_consistent() -> None:
    for spec in INDICATOR_MENU:
        assert spec.description.endswith(".") and len(spec.description) <= 240, spec.name
        assert spec.outputs and spec.inputs, spec.name
        names = [parameter.name for parameter in spec.parameters]
        assert len(set(names)) == len(names), spec.name
        assert not set(names) & set(spec.fixed_parameters), spec.name
        for parameter in spec.parameters:
            assert parameter.minimum <= parameter.default <= parameter.maximum, (spec.name, parameter.name)
            assert math.isfinite(parameter.minimum) and math.isfinite(parameter.maximum)
            if parameter.kind == "int":
                assert 1 <= parameter.minimum and parameter.maximum <= MAX_PERIOD, (spec.name, parameter.name)
        assert set(spec.warmup.driven_by) <= set(names), spec.name
        for rule in spec.constraints:
            assert {rule.smaller, rule.larger} <= set(names), spec.name
        if spec.recursive_seed:
            assert spec.seed_note, spec.name


def test_period_bound_is_five_hundred() -> None:
    assert MAX_PERIOD == 500
    assert get_indicator("SMA").parameters[0].minimum == 2
    assert get_indicator("SMA").parameters[0].maximum == 500


def test_recursive_entries_are_the_smoothed_and_cumulative_ones() -> None:
    recursive = {spec.name for spec in INDICATOR_MENU if spec.recursive_seed}
    assert {"EMA", "DEMA", "RSI", "MACD", "ATR", "NATR", "ADX", "PLUS_DI", "MINUS_DI", "SAR", "OBV"} <= recursive
    assert not recursive & {"SMA", "WMA", "BBANDS", "MIDPRICE", "HIGHEST_HIGH", "LOWEST_LOW", "STOCH", "CCI", "MFI",
                            "WILLR", "ROC", "ROCP", "MOM", "AROON", "STDDEV"}


def test_inputs_use_candle_field_names_in_library_order() -> None:
    assert get_indicator("SMA").inputs == ("close",)
    assert get_indicator("ATR").inputs == ("high", "low", "close")
    assert get_indicator("MFI").inputs == ("high", "low", "close", "volume")
    assert get_indicator("OBV").inputs == ("close", "volume")
    assert get_indicator("AROON").inputs == ("high", "low")
    assert get_indicator("HIGHEST_HIGH").inputs == ("high",)
    assert get_indicator("LOWEST_LOW").inputs == ("low",)
    assert get_indicator("HIGHEST_HIGH").talib_function == "MAX"
    assert get_indicator("LOWEST_LOW").talib_function == "MIN"


def test_fixed_choices_are_listed_not_exposed() -> None:
    assert get_indicator("BBANDS").fixed_parameters == {"matype": 0}
    assert get_indicator("STOCH").fixed_parameters == {"slowk_matype": 0, "slowd_matype": 0}
    assert get_indicator("STDDEV").fixed_parameters == {"nbdev": 1.0}


def test_resolve_parameters_fills_defaults_and_keeps_declared_types() -> None:
    spec = get_indicator("BBANDS")
    resolved = spec.resolve_parameters({})
    assert resolved == {"timeperiod": 20, "nbdevup": 2.0, "nbdevdn": 2.0}
    assert type(resolved["timeperiod"]) is int and type(resolved["nbdevup"]) is float
    assert spec.resolve_parameters({"timeperiod": 50, "nbdevup": 3}) == {
        "timeperiod": 50, "nbdevup": 3.0, "nbdevdn": 2.0}


@pytest.mark.parametrize("params", [
    {"timeperiod": 1}, {"timeperiod": 501}, {"timeperiod": True}, {"timeperiod": 14.0}, {"timeperiod": "14"},
    {"timeperiod": None}, {"unknown": 3}, {"nbdevup": math.nan}, {"nbdevup": math.inf}, {"nbdevup": 0.1},
    {"nbdevdn": 9}, {"nbdevup": True},
    # Integers too large for a float must be refused as a validation failure, not as a bare OverflowError.
    {"timeperiod": 10**400}, {"timeperiod": -(10**400)}, {"nbdevup": 10**400}, {"nbdevdn": -(10**309)},
    {"nbdevup": 10**200},
])
def test_resolve_parameters_refuses_anything_outside_the_menu(params) -> None:
    with pytest.raises(ValidationFailure):
        get_indicator("BBANDS").resolve_parameters(params)


def test_resolve_parameters_enforces_ordering_rules() -> None:
    macd = get_indicator("MACD")
    assert macd.resolve_parameters({"fastperiod": 5, "slowperiod": 10, "signalperiod": 3})["slowperiod"] == 10
    for bad in ({"fastperiod": 26, "slowperiod": 12}, {"fastperiod": 12, "slowperiod": 12}):
        with pytest.raises(ValidationFailure):
            macd.resolve_parameters(bad)
    sar = get_indicator("SAR")
    assert sar.resolve_parameters({"acceleration": 0.2, "maximum": 0.2})["maximum"] == 0.2
    with pytest.raises(ValidationFailure):
        sar.resolve_parameters({"acceleration": 0.3, "maximum": 0.2})


def test_resolve_parameters_rejects_non_mapping_input() -> None:
    with pytest.raises(ValidationFailure):
        get_indicator("SMA").resolve_parameters([("timeperiod", 5)])  # type: ignore[arg-type]


def test_specs_are_frozen_and_reject_unknown_fields() -> None:
    spec = get_indicator("SMA")
    with pytest.raises(ValidationError):
        spec.name = "OTHER"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _spec(surprise=1)


@pytest.mark.parametrize("changes", [
    {"name": "lowercase"},
    {"inputs": ()},
    {"inputs": ("price",)},
    {"outputs": ()},
    {"parameters": (_parameter(), _parameter())},
    {"recursive_seed": True},
    {"warmup": WarmUp(driven_by=("missing",), note="Not a listed parameter.")},
    {"parameters": (_parameter(), _parameter(name="other", kind="float", default=1.0, minimum=0.5, maximum=2.0)),
     "fixed_parameters": {"other": 1.0}},
    {"constraints": (ParameterOrder(smaller="timeperiod", larger="absent", reason="An unknown name."),)},
    {"description": "No full stop"},
    {"fixed_parameters": {"matype": True}},
])
def test_invalid_specs_are_refused(changes) -> None:
    with pytest.raises(ValidationError):
        _spec(**changes)


@pytest.mark.parametrize("changes", [
    {"default": 1}, {"default": 501}, {"minimum": 0, "default": 1}, {"maximum": 501}, {"minimum": 600, "maximum": 500},
    {"kind": "float"}, {"default": 14.0}, {"default": True}, {"name": "Bad Name"},
])
def test_invalid_integer_parameters_are_refused(changes) -> None:
    with pytest.raises(ValidationError):
        _parameter(**changes)


def test_float_parameters_must_be_finite_and_ordered() -> None:
    ok = _parameter(name="nbdevup", kind="float", default=2.0, minimum=0.5, maximum=5.0)
    assert ok.default == 2.0
    for changes in ({"default": math.nan}, {"minimum": -math.inf}, {"maximum": math.inf}, {"default": 9.0},
                    {"default": 2}):
        values = dict(name="nbdevup", kind="float", default=2.0, minimum=0.5, maximum=5.0, meaning="A multiplier.")
        values.update(changes)
        with pytest.raises(ValidationError):
            IndicatorParameter(**values)


def test_menu_serialises_to_plain_json() -> None:
    document = json.loads(json.dumps([spec.model_dump(mode="json") for spec in INDICATOR_MENU]))
    assert [item["name"] for item in document] == list(menu_names())
    assert all(item["warmup"]["source"] == "library_lookback" for item in document)


def test_contract_module_does_not_import_a_market_library() -> None:
    text = _CONTRACT.read_text(encoding="utf-8")
    for banned in ("talib", "talipp", "pandas", "numpy"):
        assert f"import {banned}" not in text and f"from {banned}" not in text
