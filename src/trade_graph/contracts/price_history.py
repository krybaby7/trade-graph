"""Immutable completed hourly candles, independent of provider SDKs."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal, parse_decimal

HistorySource = Literal["kraken_public_ohlc", "synthetic"]
HistorySymbol = Literal["BTC/USD", "ETH/USD"]


class HourlyCandle(BaseModel):
    """A received, completed hour; availability is receipt time, never backdated."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    symbol: HistorySymbol
    interval_minutes: Literal[60] = 60
    open_time_utc: AwareDatetime
    close_time_utc: AwareDatetime
    available_at_utc: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vwap: Decimal
    volume: Decimal
    trade_count: int = Field(ge=0, strict=True)
    source: HistorySource
    source_ref: str = Field(min_length=1, max_length=512)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def _hourly(cls, value: Any) -> int:
        if type(value) is not int or value != 60:
            raise ValueError("interval must be exactly 60 integer minutes")
        return value

    @field_validator("open", "high", "low", "close", "vwap", "volume", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Decimal:
        if isinstance(value, str) and len(value) > 128:
            raise ValueError("decimal input exceeds 128 characters")
        try:
            parsed = parse_decimal(value)
        except InvalidOperation as error:
            raise ValueError("invalid decimal") from error
        digits = len(parsed.as_tuple().digits)
        exponent = int(parsed.as_tuple().exponent)
        fixed_span = max(digits + max(exponent, 0), 2 - exponent if exponent < 0 else digits)
        if fixed_span > 128:
            raise ValueError("decimal fixed-point representation exceeds 128 characters")
        return parsed

    @field_validator("open_time_utc", "close_time_utc", "available_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_serializer("open", "high", "low", "close", "vwap", "volume")
    def _serialize_decimal(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @field_serializer("open_time_utc", "close_time_utc", "available_at_utc", when_used="json")
    def _serialize_timestamp(self, value: datetime) -> str:
        return utc_iso(value)

    @model_validator(mode="after")
    def _completed_hour(self) -> HourlyCandle:
        for at in (self.open_time_utc, self.close_time_utc):
            if at.minute or at.second or at.microsecond:
                raise ValueError("candle boundaries must be exact UTC hours")
        if self.close_time_utc - self.open_time_utc != timedelta(hours=1):
            raise ValueError("candle duration must be exactly one hour")
        if self.available_at_utc < self.close_time_utc:
            raise ValueError("incomplete candle: availability precedes close")
        if min(self.open, self.high, self.low, self.close, self.vwap) <= 0:
            raise ValueError("prices must be positive")
        if self.volume < 0:
            raise ValueError("volume must be nonnegative")
        if self.low > min(self.open, self.close, self.vwap) or self.high < max(self.open, self.close, self.vwap):
            raise ValueError("price values must lie inside the low/high range")
        return self

    @property
    def candle_id(self) -> str:
        """Content identity excludes receipt time, preserving the first receipt."""
        document = self.model_dump(mode="json", exclude={"available_at_utc"})
        content = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(content.encode("ascii")).hexdigest()
