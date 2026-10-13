"""The indicator menu the Trader may choose from (T27, decision D118).

This module is data and validation only. It never imports a market-analysis library: the library that
computes the numbers is named here as text (``talib_function``) and called from
``application/indicators/library.py``. No indicator formula lives in this repository, so warm-up (the
number of candles before a value exists) is not stored here either: ``WarmUp.source`` says it is asked
from the library for the chosen parameters.

Indicator values may be floats. Prices, quantities, fees and balances elsewhere stay ``Decimal``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator, model_validator

from trade_graph.domain.errors import ValidationFailure

# Names the menu as a whole; engine results also carry a digest of the entry used (engine.spec_version).
MENU_VERSION = "indicator-menu-v1"
MAX_PERIOD = 500
# How much of a recursive indicator's starting-point effect Settling guidance may leave, relative to the output scale.
SETTLE_TOLERANCE = 1e-5
CandleField = Literal["open", "high", "low", "close", "volume"]
ParameterKind = Literal["int", "float"]
IndicatorGroup = Literal["moving_average", "trend", "momentum", "volatility", "volume", "price_range"]
OutputUnit = Literal["price", "percent", "fraction", "points", "volume"]
Number = StrictInt | StrictFloat
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_MENU_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")


def _is_number(value: object) -> bool:
    return type(value) in (int, float)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IndicatorParameter(_Frozen):
    """One setting the Trader may change. ``name`` is the library's own keyword."""

    name: str
    kind: ParameterKind
    default: Number
    minimum: Number
    maximum: Number
    meaning: str = Field(min_length=3, max_length=200)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("parameter names are lowercase library keywords")
        return value

    @model_validator(mode="after")
    def _bounded(self) -> IndicatorParameter:
        for label, value in (("default", self.default), ("minimum", self.minimum), ("maximum", self.maximum)):
            if not _is_number(value):
                raise ValueError(f"{label} must be a plain number")
            if self.kind == "int" and type(value) is not int:
                raise ValueError(f"{label} of an integer parameter must be an integer")
            if self.kind == "float" and type(value) is not float:
                raise ValueError(f"{label} of a float parameter must be a float")
            if not math.isfinite(value):
                raise ValueError(f"{label} must be finite")
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("default must lie between minimum and maximum")
        if self.kind == "int" and (self.minimum < 1 or self.maximum > MAX_PERIOD):
            raise ValueError(f"integer parameters must stay between 1 and {MAX_PERIOD}")
        return self

    def resolve(self, value: object) -> Number:
        """The value as the declared type, or a ``ValidationFailure`` naming the parameter."""
        if not _is_number(value):
            raise ValidationFailure(f"{self.name} must be a plain number")
        if self.kind == "int":
            if type(value) is not int:
                raise ValidationFailure(f"{self.name} must be a whole number")
            number: Number = value
        else:
            number = float(value)  # type: ignore[arg-type]
        if not math.isfinite(number):
            raise ValidationFailure(f"{self.name} must be finite")
        if not self.minimum <= number <= self.maximum:
            raise ValidationFailure(f"{self.name} must be between {self.minimum} and {self.maximum}")
        return number


class ParameterOrder(_Frozen):
    """``smaller`` must be below ``larger`` (or equal when ``strict`` is false)."""

    smaller: str
    larger: str
    strict: bool = True
    reason: str = Field(min_length=3, max_length=200)


class IndicatorOutput(_Frozen):
    """One named series the indicator returns, in the library's return order."""

    name: str
    unit: OutputUnit
    meaning: str = Field(min_length=3, max_length=200)
    lowest: float | None = None
    highest: float | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("output names are lowercase words")
        return value

    @model_validator(mode="after")
    def _range(self) -> IndicatorOutput:
        if self.lowest is not None and self.highest is not None and self.lowest > self.highest:
            raise ValueError("lowest must not exceed highest")
        return self


class WarmUp(_Frozen):
    """How many candles an indicator needs is asked from the library, never computed here."""

    source: Literal["library_lookback"] = "library_lookback"
    driven_by: tuple[str, ...]
    note: str = Field(min_length=3, max_length=240)


class Settling(_Frozen):
    """Engineering guidance for recursively seeded indicators, measured by tests (not a guarantee).

    Supplying ``periods`` times the value of ``parameter`` candles of history beyond the library lookback
    keeps the remaining effect of the starting point below ``SETTLE_TOLERANCE`` of the output scale (the median
    price for price outputs, 1 for points and percent), as measured by tests/unit/test_indicator_settling.py.
    """

    parameter: str
    periods: int = Field(ge=1, le=100, strict=True)


class IndicatorSpec(_Frozen):
    name: str
    talib_function: str
    group: IndicatorGroup
    title: str = Field(min_length=3, max_length=60)
    description: str
    inputs: tuple[CandleField, ...]
    parameters: tuple[IndicatorParameter, ...] = ()
    fixed_parameters: dict[str, Number] = Field(default_factory=dict)
    constraints: tuple[ParameterOrder, ...] = ()
    outputs: tuple[IndicatorOutput, ...]
    warmup: WarmUp
    recursive_seed: bool
    seed_note: str | None = None
    settle: Settling | None = None

    @field_validator("name")
    @classmethod
    def _menu_name(cls, value: str) -> str:
        if not _MENU_NAME.match(value):
            raise ValueError("menu names are uppercase words")
        return value

    @field_validator("talib_function")
    @classmethod
    def _function(cls, value: str) -> str:
        if not re.match(r"^[A-Z][A-Z0-9_]{1,31}$", value):
            raise ValueError("a library function name is uppercase")
        return value

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        if not value.endswith(".") or not 20 <= len(value) <= 240:
            raise ValueError("a description is one sentence of 20 to 240 characters ending with a full stop")
        return value

    @field_validator("fixed_parameters")
    @classmethod
    def _fixed(cls, value: dict[str, Number]) -> dict[str, Number]:
        for name, item in value.items():
            if not _NAME.match(name) or not _is_number(item) or not math.isfinite(item):
                raise ValueError("fixed parameters are finite plain numbers under library keywords")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> IndicatorSpec:
        if not self.inputs or not self.outputs:
            raise ValueError("an indicator needs inputs and outputs")
        names = [parameter.name for parameter in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError("parameter names must be unique")
        if set(names) & set(self.fixed_parameters):
            raise ValueError("a parameter is either chosen by the Trader or fixed, not both")
        output_names = [output.name for output in self.outputs]
        if len(set(output_names)) != len(output_names):
            raise ValueError("output names must be unique")
        if not set(self.warmup.driven_by) <= set(names):
            raise ValueError("warm-up may only be driven by listed parameters")
        for rule in self.constraints:
            if rule.smaller not in names or rule.larger not in names:
                raise ValueError("ordering rules refer to listed parameters")
        if self.recursive_seed and not self.seed_note:
            raise ValueError("a recursively seeded indicator must say how")
        if self.settle is not None and (not self.recursive_seed or self.settle.parameter not in names):
            raise ValueError("settling guidance belongs to a recursive indicator and one of its parameters")
        return self

    def resolve_parameters(self, params: Mapping[str, object]) -> dict[str, Number]:
        """Defaults filled in; unknown names, wrong types, out-of-bounds values and bad orderings refused."""
        if not isinstance(params, Mapping):
            raise ValidationFailure("indicator parameters must be a mapping")
        known = {parameter.name: parameter for parameter in self.parameters}
        unknown = sorted(str(name) for name in params if name not in known)
        if unknown:
            raise ValidationFailure(f"{self.name} has no parameter {', '.join(unknown)}")
        resolved: dict[str, Number] = {}
        for parameter in self.parameters:
            resolved[parameter.name] = parameter.resolve(params[parameter.name]) if parameter.name in params \
                else parameter.default
        for rule in self.constraints:
            low, high = resolved[rule.smaller], resolved[rule.larger]
            if low > high or (rule.strict and low == high):
                relation = "below" if rule.strict else "at or below"
                raise ValidationFailure(f"{self.name}: {rule.smaller} must be {relation} {rule.larger}")
        return resolved

    @property
    def output_names(self) -> tuple[str, ...]:
        return tuple(output.name for output in self.outputs)

    @property
    def requires(self) -> tuple[CandleField, ...]:
        """The distinct candle fields this indicator reads, in first-use order."""
        return tuple(dict.fromkeys(self.inputs))


def _period(default: int, *, minimum: int = 2, meaning: str = "How many candles the calculation looks back over.",
            name: str = "timeperiod") -> IndicatorParameter:
    return IndicatorParameter(name=name, kind="int", default=default, minimum=minimum, maximum=MAX_PERIOD,
                              meaning=meaning)


def _factor(name: str, default: float, minimum: float, maximum: float, meaning: str) -> IndicatorParameter:
    return IndicatorParameter(name=name, kind="float", default=default, minimum=minimum, maximum=maximum,
                              meaning=meaning)


def _price(name: str, meaning: str) -> IndicatorOutput:
    return IndicatorOutput(name=name, unit="price", meaning=meaning)


def _points(name: str, meaning: str, lowest: float | None = None, highest: float | None = None) -> IndicatorOutput:
    return IndicatorOutput(name=name, unit="points", meaning=meaning, lowest=lowest, highest=highest)


def _window(note: str = "Needs one full window of candles; the value does not depend on older history.",
            *drivers: str) -> WarmUp:
    return WarmUp(driven_by=drivers or ("timeperiod",), note=note)


_AVERAGE = "How many closing prices are averaged."
_BACK = "How many candles back the change is measured."
_MENU: tuple[dict[str, Any], ...] = (
    dict(name="SMA", talib_function="SMA", group="moving_average", title="Simple moving average",
         description="The plain average of the last N closing prices, a smooth line that shows the trend direction.",
         inputs=("close",), parameters=(_period(20, meaning=_AVERAGE),),
         outputs=(_price("value", "The average closing price."),), warmup=_window(), recursive_seed=False),
    dict(name="EMA", talib_function="EMA", group="moving_average", title="Exponential moving average",
         description="An average of closing prices that weights recent candles more, so it reacts faster than the "
                     "simple average.",
         inputs=("close",), parameters=(_period(20, meaning="The smoothing length; larger is slower."),),
         outputs=(_price("value", "The exponential average of the close."),),
         warmup=_window("Needs one smoothing length of candles before the first value."), recursive_seed=True,
         seed_note="It starts from the plain average of the first N closes and then blends each new close into the "
                   "previous value, so where the history starts slightly changes every later value.",
         settle=dict(parameter="timeperiod", periods=4)),
    dict(name="WMA", talib_function="WMA", group="moving_average", title="Weighted moving average",
         description="An average of closing prices where the newest candle counts most and the weights fall in a "
                     "straight line to the oldest.",
         inputs=("close",), parameters=(_period(20, meaning=_AVERAGE),),
         outputs=(_price("value", "The weighted average closing price."),), warmup=_window(), recursive_seed=False),
    dict(name="DEMA", talib_function="DEMA", group="moving_average", title="Double exponential moving average",
         description="A fast-reacting average that removes much of the lag of an ordinary exponential average.",
         inputs=("close",), parameters=(_period(20, meaning="The smoothing length; larger is slower."),),
         outputs=(_price("value", "The double exponential average of the close."),),
         warmup=_window("Needs about two smoothing lengths of candles before the first value."), recursive_seed=True,
         seed_note="It is built from two exponential averages that both start from plain averages, so where the "
                   "history starts slightly changes every later value.",
         settle=dict(parameter="timeperiod", periods=4)),
    dict(name="BBANDS", talib_function="BBANDS", group="volatility", title="Bollinger Bands",
         description="A moving average with an upper and a lower band a set number of standard deviations away, "
                     "showing how stretched price is.",
         inputs=("close",),
         parameters=(_period(20, meaning="How many closing prices the middle average and the spread use."),
                     _factor("nbdevup", 2.0, 0.5, 5.0, "Standard deviations between the middle and the upper band."),
                     _factor("nbdevdn", 2.0, 0.5, 5.0, "Standard deviations between the middle and the lower band.")),
         fixed_parameters={"matype": 0},
         outputs=(_price("upper", "The upper band."), _price("middle", "The middle line, a simple average."),
                  _price("lower", "The lower band.")),
         warmup=_window(), recursive_seed=False),
    dict(name="SAR", talib_function="SAR", group="trend", title="Parabolic SAR",
         description="A trailing stop-and-reverse level that sits below price in an uptrend and above it in a "
                     "downtrend and flips when price crosses it.",
         inputs=("high", "low"),
         parameters=(_factor("acceleration", 0.02, 0.001, 0.5, "How fast the level tightens toward price."),
                     _factor("maximum", 0.2, 0.01, 1.0, "The most the level may tighten per candle.")),
         constraints=(ParameterOrder(smaller="acceleration", larger="maximum", strict=False,
                                     reason="The tightening step cannot exceed its maximum."),),
         outputs=(_price("value", "The stop-and-reverse level."),),
         warmup=WarmUp(driven_by=(), note="Needs only a couple of candles, but early values are unreliable."),
         recursive_seed=True,
         seed_note="The level remembers its trend and speed since the history started, so a different starting "
                   "point can give a different state until the next reversal."),
    dict(name="MIDPRICE", talib_function="MIDPRICE", group="price_range", title="Range midpoint",
         description="The midpoint between the highest high and the lowest low of the last N candles, the centre "
                     "of the recent range.",
         inputs=("high", "low"), parameters=(_period(14, meaning="How many candles define the range."),),
         outputs=(_price("value", "The midpoint of the recent high-low range."),), warmup=_window(),
         recursive_seed=False),
    dict(name="HIGHEST_HIGH", talib_function="MAX", group="price_range", title="Highest high",
         description="The highest high of the last N candles, the top of the recent range and a breakout level.",
         inputs=("high",), parameters=(_period(20, meaning="How many candles define the range."),),
         outputs=(_price("value", "The highest high in the window."),), warmup=_window(), recursive_seed=False),
    dict(name="LOWEST_LOW", talib_function="MIN", group="price_range", title="Lowest low",
         description="The lowest low of the last N candles, the bottom of the recent range and a breakdown level.",
         inputs=("low",), parameters=(_period(20, meaning="How many candles define the range."),),
         outputs=(_price("value", "The lowest low in the window."),), warmup=_window(), recursive_seed=False),
    dict(name="RSI", talib_function="RSI", group="momentum", title="Relative Strength Index",
         description="A 0 to 100 gauge of how strong recent gains are against recent losses; high values suggest "
                     "overbought and low values oversold.",
         inputs=("close",), parameters=(_period(14, meaning="How many candles of gains and losses are compared."),),
         outputs=(_points("value", "The index from 0 (all losses) to 100 (all gains).", 0.0, 100.0),),
         warmup=_window("Needs one period of candles plus one before the first value."), recursive_seed=True,
         seed_note="Gains and losses are smoothed with Wilder's method starting from a plain average of the first "
                   "N changes, so where the history starts slightly changes later values.",
         settle=dict(parameter="timeperiod", periods=20)),
    dict(name="MACD", talib_function="MACD", group="momentum", title="MACD",
         description="The gap between a fast and a slow exponential average, with a signal line and a histogram "
                     "that show momentum building or fading.",
         inputs=("close",),
         parameters=(_period(12, name="fastperiod", meaning="Length of the fast average."),
                     _period(26, name="slowperiod", meaning="Length of the slow average."),
                     _period(9, name="signalperiod", meaning="Length of the smoothing that makes the signal line.")),
         constraints=(ParameterOrder(smaller="fastperiod", larger="slowperiod", strict=True,
                                     reason="The fast average must be shorter than the slow one."),),
         outputs=(_price("macd", "Fast average minus slow average, in price units."),
                  _price("signal", "The smoothed MACD line."),
                  _price("histogram", "MACD minus signal; positive when momentum is rising.")),
         warmup=_window("Needs the slow length plus the signal length of candles before the first value.",
                        "slowperiod", "signalperiod"),
         recursive_seed=True,
         seed_note="Fast, slow and signal averages all start from plain averages, so where the history starts "
                   "slightly changes later values.",
         settle=dict(parameter="slowperiod", periods=4)),
    dict(name="STOCH", talib_function="STOCH", group="momentum", title="Slow Stochastic",
         description="Shows where the close sits inside the recent high-low range on a 0 to 100 scale, smoothed "
                     "into a K line and a D line.",
         inputs=("high", "low", "close"),
         parameters=(_period(14, name="fastk_period", meaning="How many candles define the high-low range."),
                     _period(3, minimum=1, name="slowk_period", meaning="Smoothing of the raw reading into K."),
                     _period(3, minimum=1, name="slowd_period", meaning="Smoothing of K into the D line.")),
         fixed_parameters={"slowk_matype": 0, "slowd_matype": 0},
         outputs=(_points("slow_k", "The smoothed position in the range, 0 to 100.", 0.0, 100.0),
                  _points("slow_d", "A further smoothing of K, 0 to 100.", 0.0, 100.0)),
         warmup=_window("Needs the range length plus both smoothings.", "fastk_period", "slowk_period",
                        "slowd_period"),
         recursive_seed=False),
    dict(name="CCI", talib_function="CCI", group="momentum", title="Commodity Channel Index",
         description="Measures how far the typical price has moved from its average, in units of typical "
                     "deviation; beyond plus or minus 100 is stretched.",
         inputs=("high", "low", "close"), parameters=(_period(14, meaning="How many candles the average uses."),),
         outputs=(_points("value", "Distance from the average in typical deviations; unbounded."),),
         warmup=_window(), recursive_seed=False),
    dict(name="MFI", talib_function="MFI", group="volume", title="Money Flow Index",
         description="A volume-weighted version of RSI on a 0 to 100 scale, showing whether money is flowing into "
                     "or out of the coin.",
         inputs=("high", "low", "close", "volume"),
         parameters=(_period(14, meaning="How many candles of money flow are compared."),),
         outputs=(_points("value", "The index from 0 (all outflow) to 100 (all inflow).", 0.0, 100.0),),
         warmup=_window("Needs one period of candles plus one before the first value."), recursive_seed=False),
    dict(name="WILLR", talib_function="WILLR", group="momentum", title="Williams %R",
         description="Where the close sits in the recent high-low range, from -100 at the low to 0 at the high.",
         inputs=("high", "low", "close"), parameters=(_period(14, meaning="How many candles define the range."),),
         outputs=(_points("value", "Position in the range from -100 (at the low) to 0 (at the high).", -100.0, 0.0),),
         warmup=_window(), recursive_seed=False),
    dict(name="ROC", talib_function="ROC", group="momentum", title="Rate of change",
         description="The percentage change of the close compared with N candles ago.",
         inputs=("close",), parameters=(_period(10, minimum=1, meaning=_BACK),),
         outputs=(IndicatorOutput(name="value", unit="percent", meaning="Percent change; 1.5 means up 1.5 percent."),),
         warmup=_window("Needs N candles before the first value."), recursive_seed=False),
    dict(name="ROCP", talib_function="ROCP", group="momentum", title="Rate of change (fraction)",
         description="The change of the close compared with N candles ago as a fraction of that earlier close, so "
                     "0.015 means up 1.5 percent.",
         inputs=("close",), parameters=(_period(10, minimum=1, meaning=_BACK),),
         outputs=(IndicatorOutput(name="value", unit="fraction",
                                  meaning="Fractional change; 0.015 means up 1.5 percent."),),
         warmup=_window("Needs N candles before the first value."), recursive_seed=False),
    dict(name="MOM", talib_function="MOM", group="momentum", title="Momentum",
         description="The price change of the close compared with N candles ago, in price units.",
         inputs=("close",), parameters=(_period(10, minimum=1, meaning=_BACK),),
         outputs=(_price("value", "Close now minus the close N candles ago."),),
         warmup=_window("Needs N candles before the first value."), recursive_seed=False),
    dict(name="ADX", talib_function="ADX", group="trend", title="Average Directional Index",
         description="A 0 to 100 measure of how strong a trend is regardless of its direction; readings above "
                     "about 25 are often read as a strong trend.",
         inputs=("high", "low", "close"),
         parameters=(_period(14, meaning="Smoothing length for the directional movement."),),
         outputs=(_points("value", "Trend strength from 0 to 100.", 0.0, 100.0),),
         warmup=_window("Needs about two smoothing lengths of candles before the first value."),
         recursive_seed=True,
         seed_note="Directional movement is smoothed twice with Wilder's method, each time started from the first few "
                   "values, so where the history starts slightly changes later values.",
         settle=dict(parameter="timeperiod", periods=24)),
    dict(name="PLUS_DI", talib_function="PLUS_DI", group="trend", title="Plus directional indicator",
         description="A 0 to 100 measure of upward pressure; compare it with the minus directional indicator to "
                     "see which side is winning.",
         inputs=("high", "low", "close"),
         parameters=(_period(14, meaning="Smoothing length for the directional movement."),),
         outputs=(_points("value", "Upward pressure from 0 to 100.", 0.0, 100.0),),
         warmup=_window("Needs one smoothing length of candles before the first value."), recursive_seed=True,
         seed_note="Directional movement is smoothed with Wilder's method started from the first few values, "
                   "so where the history starts slightly changes later values.",
         settle=dict(parameter="timeperiod", periods=20)),
    dict(name="MINUS_DI", talib_function="MINUS_DI", group="trend", title="Minus directional indicator",
         description="A 0 to 100 measure of downward pressure; compare it with the plus directional indicator.",
         inputs=("high", "low", "close"),
         parameters=(_period(14, meaning="Smoothing length for the directional movement."),),
         outputs=(_points("value", "Downward pressure from 0 to 100.", 0.0, 100.0),),
         warmup=_window("Needs one smoothing length of candles before the first value."), recursive_seed=True,
         seed_note="Directional movement is smoothed with Wilder's method started from the first few values, "
                   "so where the history starts slightly changes later values.",
         settle=dict(parameter="timeperiod", periods=20)),
    dict(name="AROON", talib_function="AROON", group="trend", title="Aroon",
         description="How recently the highest high and the lowest low happened within the last N candles, on a "
                     "0 to 100 scale for each.",
         inputs=("high", "low"), parameters=(_period(14, meaning="How many candles are searched."),),
         outputs=(_points("down", "100 when the lowest low is the newest candle, 0 when it is the oldest.", 0.0, 100.0),
                  _points("up", "100 when the highest high is the newest candle, 0 when it is the oldest.",
                          0.0, 100.0)),
         warmup=_window("Needs one period of candles plus one before the first value."), recursive_seed=False),
    dict(name="ATR", talib_function="ATR", group="volatility", title="Average True Range",
         description="The typical size of a candle's full range including gaps, in price units, used to size stops "
                     "and judge volatility.",
         inputs=("high", "low", "close"),
         parameters=(_period(14, meaning="Smoothing length for the true range."),),
         outputs=(_price("value", "The average true range, in price units."),),
         warmup=_window("Needs one smoothing length of candles plus one before the first value."),
         recursive_seed=True,
         seed_note="The true range is smoothed with Wilder's method starting from a plain average of the first N "
                   "values, so where the history starts slightly changes later values.",
         settle=dict(parameter="timeperiod", periods=8)),
    dict(name="NATR", talib_function="NATR", group="volatility", title="Normalised ATR",
         description="The average true range as a percent of the close, so volatility can be compared between "
                     "coins with different prices.",
         inputs=("high", "low", "close"),
         parameters=(_period(14, meaning="Smoothing length for the true range."),),
         outputs=(IndicatorOutput(name="value", unit="percent", meaning="Average true range as a percent of the close.",
                                  lowest=0.0),),
         warmup=_window("Needs one smoothing length of candles plus one before the first value."),
         recursive_seed=True,
         seed_note="The true range is smoothed with Wilder's method starting from a plain average of the first N "
                   "values, so where the history starts slightly changes later values.",
         settle=dict(parameter="timeperiod", periods=14)),
    dict(name="OBV", talib_function="OBV", group="volume", title="On-Balance Volume",
         description="A running total that adds a candle's volume when the close rises and subtracts it when the "
                     "close falls.",
         inputs=("close", "volume"), parameters=(),
         outputs=(IndicatorOutput(name="value", unit="volume", meaning="The running volume total."),),
         warmup=WarmUp(driven_by=(), note="A value exists from the first candle."), recursive_seed=True,
         seed_note="It is a running total from the first candle, so its level depends on where the history starts; "
                   "only changes in OBV are comparable between runs."),
    dict(name="STDDEV", talib_function="STDDEV", group="volatility", title="Standard deviation",
         description="The standard deviation of the closing price over the last N candles, in price units, a direct "
                     "measure of how widely price has been spread.",
         inputs=("close",), parameters=(_period(20, meaning="How many closing prices are measured."),),
         fixed_parameters={"nbdev": 1.0},
         outputs=(_price("value", "The (population) standard deviation of the close."),), warmup=_window(),
         recursive_seed=False),
)

INDICATOR_MENU: tuple[IndicatorSpec, ...] = tuple(IndicatorSpec.model_validate(item) for item in _MENU)
_BY_NAME = {spec.name: spec for spec in INDICATOR_MENU}
if len(_BY_NAME) != len(INDICATOR_MENU):
    raise RuntimeError("duplicate indicator names in the menu")


def menu_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in INDICATOR_MENU)


def get_indicator(name: str) -> IndicatorSpec:
    try:
        return _BY_NAME[name]
    except (KeyError, TypeError) as error:
        raise ValidationFailure(f"{name!r} is not on the indicator menu") from error
