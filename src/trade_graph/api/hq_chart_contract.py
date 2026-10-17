"""The HQ price chart document, ``trade-graph.hq.chart.v1`` (T41, D197).

``GET /api/v1/hq/chart`` returns one of these; the page (``web/static/hq-charts.js``) only draws it. It is served on
its own, never inside the HQ snapshot, so the snapshot's 80 KB budget is unchanged.

Every price, quantity and level is exact decimal text, as saved. Indicator values are the T27 engine's numbers (D118:
analytical, may be floats), rounded to eight significant digits for drawing; they line up one for one with the window's
candle slots. Kinds, reasons, panes and statuses are fixed vocabularies, so nothing in the document can name a
colour, a script or an address: the page maps each kind to its own look.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from trade_graph.contracts.alarms import SYMBOL_PATTERN
from trade_graph.contracts.candles import COLLECTED_INTERVALS

SCHEMA = "trade-graph.hq.chart.v1"
INTERVALS: tuple[int, ...] = COLLECTED_INTERVALS
DEFAULT_INTERVAL = 15
WINDOW_SLOTS = 300  # candles per chart: 5 hours at 1 minute, 12.5 days at 1 hour
CHART_SOURCE = "kraken_public_ohlc"  # the collector's candles, the ones the Trader's indicators and alarms read
REFERENCE_SYMBOLS: tuple[str, ...] = ("BTC/USD", "ETH/USD")  # the market weather's two coins
VALUE_DIGITS = 8  # significant digits of a drawn indicator value (a picture needs no more)
MAX_COINS = 60  # a display bound on the coin picker, not a limit on any department's activity (D116)
MAX_MARKERS = 200  # the newest fills and alarm fires drawn on one chart; the legend says when there were more

Status = Literal["ok", "no_price_history", "coin_not_listed"]
CoinReason = Literal["day_plan", "held", "alarm", "shortlist", "reference"]
COIN_REASONS: tuple[str, ...] = ("day_plan", "held", "alarm", "shortlist", "reference")
Pane = Literal["price", "own"]
IndicatorStatus = Literal["ready", "warming_up", "gapped", "unavailable"]
ChosenBy = Literal["day_plan", "alarm"]
LineKind = Literal["alarm", "entry", "stop", "take_profit", "order"]
LineState = Literal["armed", "open", "unclear"]
MarkerKind = Literal["buy", "sell", "alarm"]
OutputStyle = Literal["line", "histogram"]
_DECIMAL = re.compile(r"^[0-9]{1,40}(?:\.[0-9]{1,30})?$")
_SIGNED_DECIMAL = re.compile(r"^-?[0-9]{1,40}(?:\.[0-9]{1,30})?$")
_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,23}\|[0-9]{1,2}\|[a-z0-9_=,.\-]{0,200}$")
_ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


def _decimal_text(value: object) -> str:
    """Exact non-negative decimal text: no float, sign, exponent or thousands separator."""
    if not isinstance(value, str) or not _DECIMAL.match(value):
        raise ValueError("prices, quantities and levels are exact decimal text")
    return value


def _level_text(value: object) -> str:
    """An alarm or order level: exact decimal text; an oscillator level may be negative (Williams %R, MACD)."""
    if not isinstance(value, str) or not _SIGNED_DECIMAL.match(value) or value == "-0":
        raise ValueError("levels are exact decimal text")
    return value


def _iso(value: object) -> str:
    if not isinstance(value, str) or not _ISO.match(value):
        raise ValueError("times are UTC ISO instants with milliseconds")
    return value


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChartCoin(_Frozen):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    reasons: tuple[CoinReason, ...] = Field(min_length=1, max_length=len(COIN_REASONS))


class ChartWindow(_Frozen):
    """``slots`` consecutive candle slots from ``first_open`` (open times, unix seconds); ``now_open`` is the slot
    forming now, which the page leaves empty up to the right edge."""

    first_open: StrictInt = Field(ge=0)
    step_seconds: StrictInt
    slots: StrictInt = Field(ge=1, le=WINDOW_SLOTS)
    now_open: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def _grid(self) -> ChartWindow:
        if self.step_seconds not in {interval * 60 for interval in INTERVALS}:
            raise ValueError("the step is a collected candle interval")
        if self.first_open % self.step_seconds or self.now_open % self.step_seconds:
            raise ValueError("slot times sit on the interval grid")
        if self.now_open < self.first_open + (self.slots - 1) * self.step_seconds:
            raise ValueError("the forming slot is at or after the window's last slot")
        return self


# A candle is ``[open time (unix seconds), open, high, low, close, volume]``: compact, because a chart has 300.
ChartCandle = tuple[StrictInt, str, str, str, str, str]


class ChartOutput(_Frozen):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: StrictStr = Field(min_length=1, max_length=40)
    style: OutputStyle
    values: tuple[StrictFloat | StrictInt, ...] = Field(max_length=WINDOW_SLOTS)

    @field_validator("values")
    @classmethod
    def _finite(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not all(math.isfinite(value) for value in values):
            raise ValueError("indicator values are finite numbers")
        return values


class ChartIndicator(_Frozen):
    key: str
    indicator: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,23}$")
    title: StrictStr = Field(min_length=3, max_length=60)
    label: StrictStr = Field(min_length=1, max_length=40)
    parameters: dict[str, StrictInt | StrictFloat]
    interval_minutes: StrictInt
    pane: Pane
    status: IndicatorStatus
    status_text: StrictStr | None = Field(default=None, max_length=300)
    chosen_by: tuple[ChosenBy, ...] = Field(min_length=1, max_length=2)
    outputs: tuple[ChartOutput, ...] = Field(max_length=4)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not _KEY.match(value):
            raise ValueError("an indicator key is name|interval|settings")
        return value

    @model_validator(mode="after")
    def _status(self) -> ChartIndicator:
        if self.interval_minutes not in INTERVALS:
            raise ValueError("indicators read the collected intervals")
        if self.status == "ready" and not self.outputs:
            raise ValueError("a ready indicator carries its values")
        if self.status != "ready" and self.outputs:
            raise ValueError("an indicator that is not ready has its values withheld")
        if self.status != "ready" and not self.status_text:
            raise ValueError("an indicator that is not drawn says why")
        return self


class ChartLine(_Frozen):
    kind: LineKind
    price: str
    title: StrictStr = Field(min_length=1, max_length=12)
    text: StrictStr = Field(min_length=1, max_length=300)
    pane: str = Field(min_length=1, max_length=240)  # "price" or the key of the indicator whose pane it is drawn on
    state: LineState

    @field_validator("price", mode="before")
    @classmethod
    def _exact(cls, value: object) -> str:
        return _level_text(value)


class ChartMarker(_Frozen):
    kind: MarkerKind
    t: StrictInt = Field(ge=0)  # the open time of the candle slot it happened in
    at: str
    price: str | None  # where it is drawn; None for an alarm on an indicator's own scale
    text: StrictStr = Field(min_length=1, max_length=300)

    @field_validator("at", mode="before")
    @classmethod
    def _when(cls, value: object) -> str:
        return _iso(value)

    @field_validator("price", mode="before")
    @classmethod
    def _price(cls, value: object) -> str | None:
        return None if value is None else _decimal_text(value)


class ChartDocument(_Frozen):
    schema_: Literal["trade-graph.hq.chart.v1"] = Field(alias="schema")
    as_of: str
    status: Status
    status_text: StrictStr | None = Field(default=None, max_length=300)
    coins: tuple[ChartCoin, ...] = Field(max_length=MAX_COINS)
    symbol: str | None = Field(default=None, pattern=SYMBOL_PATTERN)
    interval_minutes: StrictInt | None = None
    intervals: tuple[StrictInt, ...]
    source: str = Field(min_length=1, max_length=40)
    price_precision: StrictInt | None = Field(default=None, ge=0, le=12)
    window: ChartWindow | None = None
    newest_close: str | None = None
    candles: tuple[ChartCandle, ...] = Field(max_length=WINDOW_SLOTS)

    @field_validator("candles")
    @classmethod
    def _candles(cls, candles: tuple[ChartCandle, ...]) -> tuple[ChartCandle, ...]:
        for candle in candles:
            if candle[0] < 0:
                raise ValueError("a candle's open time is unix seconds")
            for value in candle[1:]:
                _decimal_text(value)
        return candles
    missing: tuple[StrictInt, ...] = Field(max_length=WINDOW_SLOTS)
    indicators: tuple[ChartIndicator, ...] = Field(max_length=16)
    lines: tuple[ChartLine, ...]
    markers: tuple[ChartMarker, ...] = Field(max_length=MAX_MARKERS)
    elsewhere: tuple[StrictStr, ...]
    notes: tuple[StrictStr, ...]

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of(cls, value: object) -> str:
        return _iso(value)

    @field_validator("newest_close", mode="before")
    @classmethod
    def _newest(cls, value: object) -> str | None:
        return None if value is None else _iso(value)

    @model_validator(mode="after")
    def _consistent(self) -> ChartDocument:
        if tuple(self.intervals) != INTERVALS:
            raise ValueError("the intervals are the collected candle intervals")
        if self.interval_minutes is not None and self.interval_minutes not in INTERVALS:
            raise ValueError("a chart uses a collected candle interval")
        if (self.status_text is None) != (self.status == "ok"):
            raise ValueError("a chart that is not drawn says why, and only then")
        keys = {indicator.key for indicator in self.indicators if indicator.pane == "own"}
        if any(line.pane != "price" and line.pane not in keys for line in self.lines):
            raise ValueError("a line sits on the price pane or on a drawn indicator's own pane")
        if self.status != "ok":
            if self.candles or self.missing or self.indicators or self.window is not None or self.markers:
                raise ValueError("a chart that is not drawn carries no candles, indicators or markers")
            return self
        if self.symbol is None or self.interval_minutes is None or self.window is None:
            raise ValueError("a drawn chart names its coin, interval and window")
        window = self.window
        if window.step_seconds != self.interval_minutes * 60:
            raise ValueError("the window step is the chart's interval")
        slots = {window.first_open + index * window.step_seconds for index in range(window.slots)}
        times = [candle[0] for candle in self.candles]
        if times != sorted(set(times)) or not set(times) <= slots:
            raise ValueError("candles are distinct window slots, oldest first")
        if set(self.missing) & set(times) or not set(self.missing) <= slots or len(times) + len(self.missing) != len(
                slots):
            raise ValueError("every window slot is a candle or missing")
        for indicator in self.indicators:
            if indicator.interval_minutes != self.interval_minutes:
                raise ValueError("indicators drawn on a chart use its interval")
            if any(len(output.values) != window.slots for output in indicator.outputs):
                raise ValueError("indicator values line up with the window's slots")
        for marker in self.markers:
            if marker.t % window.step_seconds:
                raise ValueError("a marker sits on a candle slot")
        return self


def validate_chart(document: dict) -> ChartDocument:
    """Validate a chart document (raises ``pydantic.ValidationError``)."""
    return ChartDocument.model_validate(document)


__all__ = ["CHART_SOURCE", "COIN_REASONS", "DEFAULT_INTERVAL", "INTERVALS", "MAX_COINS", "MAX_MARKERS",
           "REFERENCE_SYMBOLS", "SCHEMA", "VALUE_DIGITS", "WINDOW_SLOTS", "ChartDocument", "validate_chart"]
