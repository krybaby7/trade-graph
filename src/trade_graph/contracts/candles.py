"""Immutable completed candles at several intervals, independent of provider SDKs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal, parse_decimal

CANDLE_INTERVALS = (1, 5, 15, 30, 60, 240, 720, 1440)
COLLECTED_INTERVALS = (1, 5, 15, 60)
CANDLE_SOURCES = ("kraken_public_ohlc", "kraken_ohlcvt_archive", "kraken_public_trades", "synthetic")
CandleSource = Literal["kraken_public_ohlc", "kraken_ohlcvt_archive", "kraken_public_trades", "synthetic"]
ReadMode = Literal["point_in_time", "replay"]
AvailabilityBasis = Literal["receipt_time", "event_time_only"]
RUN_KINDS = ("public_collect", "archive_import", "trades_backfill", "catalog")
EVIDENCE_LABELS = (
    "synthetic_fixture", "recorded_public_fixture", "live_public_smoke", "live_public", "local_archive_file",
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SOURCE_REFS = {
    "kraken_public_ohlc": "kraken:/0/public/OHLC",
    "kraken_ohlcvt_archive": "kraken:ohlcvt-archive",
    "kraken_public_trades": "kraken:/0/public/Trades",
    "synthetic": "synthetic",
}


def to_unix(value: datetime) -> int:
    """Whole UTC seconds; sub-second parts are refused rather than truncated."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    delta = value.astimezone(UTC) - _EPOCH
    if delta.microseconds:
        raise ValueError("candle times must be whole seconds")
    return delta.days * 86400 + delta.seconds


def from_unix(seconds: int) -> datetime:
    return _EPOCH + timedelta(seconds=seconds)


def to_micros(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    delta = value.astimezone(UTC) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def from_micros(micros: int) -> datetime:
    return _EPOCH + timedelta(microseconds=micros)


def align_close_time(value: datetime, interval_minutes: int) -> datetime:
    """The last interval boundary at or before ``value`` (the newest possible close)."""
    step = interval_minutes * 60
    seconds = to_unix(value.replace(microsecond=0))
    return from_unix(seconds - seconds % step)


def expected_close_times(end_close_time: datetime, count: int, interval_minutes: int) -> list[datetime]:
    """``count`` consecutive close times ending at ``end_close_time``, oldest first."""
    if interval_minutes not in CANDLE_INTERVALS:
        raise ValueError("unsupported candle interval")
    if type(count) is not int or count < 1:
        raise ValueError("at least one slot is required")
    if to_unix(end_close_time) % (interval_minutes * 60):
        raise ValueError("end close time must sit on the interval grid")
    step = timedelta(minutes=interval_minutes)
    return [end_close_time - step * offset for offset in reversed(range(count))]


class Candle(BaseModel):
    """A received, completed candle. Availability is receipt time, never backdated."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    symbol: str = Field(pattern=r"^[A-Z0-9]{1,16}/[A-Z]{3,5}$")
    source_pair: str = Field(pattern=r"^[A-Za-z0-9._/-]{1,32}$")
    interval_minutes: int = Field(strict=True)
    open_time_utc: AwareDatetime
    close_time_utc: AwareDatetime
    available_at_utc: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    vwap: Decimal | None = None
    volume: Decimal
    trade_count: int = Field(ge=0, strict=True)
    source: CandleSource

    @field_validator("interval_minutes")
    @classmethod
    def _interval(cls, value: int) -> int:
        if value not in CANDLE_INTERVALS:
            raise ValueError("unsupported candle interval")
        return value

    @field_validator("open", "high", "low", "close", "vwap", "volume", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
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

    @field_serializer("open", "high", "low", "close", "volume")
    def _serialize_decimal(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @field_serializer("vwap")
    def _serialize_vwap(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)

    @field_serializer("open_time_utc", "close_time_utc", "available_at_utc", when_used="json")
    def _serialize_timestamp(self, value: datetime) -> str:
        return utc_iso(value)

    @model_validator(mode="after")
    def _completed_candle(self) -> Candle:
        step = self.interval_minutes * 60
        try:
            opened, closed = to_unix(self.open_time_utc), to_unix(self.close_time_utc)
        except ValueError as error:
            raise ValueError("candle boundaries must be whole seconds") from error
        if opened % step:
            raise ValueError("open time must sit on the interval grid")
        if closed - opened != step:
            raise ValueError("candle duration must equal its interval")
        if self.available_at_utc < self.close_time_utc:
            raise ValueError("incomplete candle: availability precedes close")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("prices must be positive")
        if self.volume < 0:
            raise ValueError("volume must be nonnegative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("price values must lie inside the low/high range")
        if self.vwap is not None:
            quiet = self.volume == 0 and self.vwap == 0
            if not quiet and not self.low <= self.vwap <= self.high:
                raise ValueError("vwap must lie inside the low/high range")
        return self

    @property
    def open_unix(self) -> int:
        return to_unix(self.open_time_utc)

    @property
    def content_hash(self) -> str:
        """Content identity excludes receipt time, so a later identical receipt is a duplicate."""
        document = {
            "symbol": self.symbol, "source_pair": self.source_pair, "interval_minutes": self.interval_minutes,
            "open_time": self.open_unix, "open": canonical_decimal(self.open), "high": canonical_decimal(self.high),
            "low": canonical_decimal(self.low), "close": canonical_decimal(self.close),
            "vwap": None if self.vwap is None else canonical_decimal(self.vwap),
            "volume": canonical_decimal(self.volume), "trade_count": self.trade_count, "source": self.source,
        }
        content = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(content.encode("ascii")).hexdigest()

    @property
    def source_ref(self) -> str:
        return f"{_SOURCE_REFS[self.source]}:{self.source_pair}:{self.interval_minutes}:{self.open_unix}"


@dataclass(frozen=True, slots=True)
class StoredCandle:
    candle: Candle
    revision: int
    run_id: int


@dataclass(frozen=True, slots=True)
class CandleSlot:
    close_time_utc: datetime
    stored: StoredCandle | None


@dataclass(frozen=True, slots=True)
class CandleWindow:
    """Exactly the requested slots, oldest first. A missing slot is ``stored is None``."""

    symbol: str
    interval_minutes: int
    source: str
    as_of_utc: datetime
    mode: ReadMode
    slots: tuple[CandleSlot, ...]

    @property
    def availability_basis(self) -> AvailabilityBasis:
        return "receipt_time" if self.mode == "point_in_time" else "event_time_only"

    @property
    def candles(self) -> tuple[Candle, ...]:
        return tuple(slot.stored.candle for slot in self.slots if slot.stored is not None)

    @property
    def missing_close_times(self) -> tuple[datetime, ...]:
        return tuple(slot.close_time_utc for slot in self.slots if slot.stored is None)

    @property
    def complete(self) -> bool:
        return all(slot.stored is not None for slot in self.slots)


@dataclass(frozen=True, slots=True)
class MissingRange:
    first_open_utc: datetime
    last_open_utc: datetime
    slots: int


@dataclass(frozen=True, slots=True)
class CoverageReport:
    symbol: str
    interval_minutes: int
    source: str
    start_open_utc: datetime
    end_open_utc: datetime
    expected: int
    present: int
    first_open_utc: datetime | None
    last_close_utc: datetime | None
    longest_run: int
    missing_ranges: tuple[MissingRange, ...]
    mode: ReadMode

    @property
    def missing(self) -> int:
        return self.expected - self.present

    def to_document(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return None if value is None else utc_iso(value)

        return {
            "symbol": self.symbol, "interval_minutes": self.interval_minutes, "source": self.source,
            "mode": self.mode,
            "window_start_open_utc": iso(self.start_open_utc), "window_end_open_utc": iso(self.end_open_utc),
            "expected_slots": self.expected, "present_slots": self.present, "missing_slots": self.missing,
            "first_open_utc": iso(self.first_open_utc), "last_close_utc": iso(self.last_close_utc),
            "longest_run_slots": self.longest_run,
            "missing_ranges": [
                {"first_open_utc": iso(r.first_open_utc), "last_open_utc": iso(r.last_open_utc), "slots": r.slots}
                for r in self.missing_ranges
            ],
        }


@dataclass(frozen=True, slots=True)
class SeriesSummary:
    symbol: str
    source: str
    interval_minutes: int
    source_pair: str
    candle_count: int
    first_open_utc: datetime | None
    last_open_utc: datetime | None


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: int
    kind: str
    source: str
    evidence_label: str
    started_at_utc: datetime
    parameters: dict[str, Any]
    finished_at_utc: datetime | None
    status: str
    report: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class CatalogObservation:
    observation_id: int
    observed_at_utc: datetime
    run_id: int
    rules_version: str
    pairs: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class SaveResult:
    inserted: int
    revised: int
    duplicates: int
