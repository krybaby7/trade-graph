"""Kraken spot pairs as the engine sees them: listing facts plus stablecoin and yield-token marks.

A ``ListedPair`` holds what the exchange says about a pair. A ``PairRecord`` adds the classification
of ``application/pair_classification.py`` (D131 stablecoins and yield tokens, D127 riba products).
Neither depends on a provider SDK.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.domain.money import canonical_decimal, parse_decimal

PAIR_STATUSES = ("online", "cancel_only", "post_only", "limit_only", "reduce_only")
PairStatus = Literal["online", "cancel_only", "post_only", "limit_only", "reduce_only"]
# Only "online" is normal trading. post_only and limit_only refuse the market and protective exits this engine
# relies on, reduce_only refuses new buying, and cancel_only accepts no new orders and usually comes before a
# delisting. Such pairs stay in the catalog with their status and are not collected until they are online again.
NORMAL_STATUSES = frozenset({"online"})

EXCLUSION_STABLECOIN = "stablecoin"
EXCLUSION_YIELD_TOKEN = "yield_token"
EXCLUSION_FIAT_CURRENCY = "fiat_currency"

AssetReview = Literal["reviewed", "not_individually_reviewed"]

# Kraken names two coins differently from the rest of the engine. Whole codes only: AIXBT, XBTC and DOGS are
# different assets and stay as they are.
_ENGINE_ASSET_NAMES = {"XBT": "BTC", "XDG": "DOGE"}
_CODE = r"[A-Z0-9][A-Z0-9._-]{0,23}"
_CODE_PATTERN = rf"^{_CODE}$"
_WS_NAME_PATTERN = rf"^{_CODE}/{_CODE}$"
_WS_NAME = re.compile(rf"({_CODE})/({_CODE})")
_DECIMAL_TEXT_LIMIT = 64
_DECIMAL_DIGIT_LIMIT = 40
_DECIMAL_EXPONENT_LIMIT = 30


def is_normal_status(status: object) -> bool:
    return isinstance(status, str) and status in NORMAL_STATUSES


def engine_symbol(ws_name: str) -> str:
    """``XBT/USD`` becomes ``BTC/USD`` and ``XDG/USD`` becomes ``DOGE/USD``; other names are unchanged."""
    match = _WS_NAME.fullmatch(ws_name) if isinstance(ws_name, str) else None
    if match is None:
        raise ValueError("a Kraken wire pair name is BASE/QUOTE in upper case")
    base, quote = match.groups()
    return f"{_ENGINE_ASSET_NAMES.get(base, base)}/{_ENGINE_ASSET_NAMES.get(quote, quote)}"


def _bounded_decimal(value: Any) -> Decimal:
    if isinstance(value, str) and len(value) > _DECIMAL_TEXT_LIMIT:
        raise ValueError("decimal input is too long")
    try:
        parsed = parse_decimal(value)
    except ArithmeticError as error:
        raise ValueError("invalid decimal") from error
    if (len(parsed.as_tuple().digits) > _DECIMAL_DIGIT_LIMIT
            or not -_DECIMAL_EXPONENT_LIMIT <= parsed.adjusted() <= _DECIMAL_EXPONENT_LIMIT):
        raise ValueError("decimal is outside the supported range")
    return parsed


class ListedPair(BaseModel):
    """What Kraken lists for one spot pair. Exact decimals; unknown stays unknown (``tick_size`` may be None)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(pattern=r"^[A-Z0-9]{1,16}/[A-Z0-9]{2,8}$")
    source_pair: str = Field(pattern=_CODE_PATTERN)
    ws_name: str = Field(pattern=_WS_NAME_PATTERN)
    base: str = Field(pattern=_CODE_PATTERN)
    quote: str = Field(pattern=_CODE_PATTERN)
    status: PairStatus
    ordermin: Decimal
    costmin: Decimal
    tick_size: Decimal | None = None
    pair_decimals: int = Field(strict=True, ge=0, le=20)
    cost_decimals: int = Field(strict=True, ge=0, le=20)
    lot_decimals: int = Field(strict=True, ge=0, le=20)

    @field_validator("ordermin", "costmin", mode="before")
    @classmethod
    def _required_decimal(cls, value: Any) -> Decimal:
        return _bounded_decimal(value)

    @field_validator("tick_size", mode="before")
    @classmethod
    def _optional_decimal(cls, value: Any) -> Decimal | None:
        return None if value is None else _bounded_decimal(value)

    @field_validator("ordermin")
    @classmethod
    def _ordermin_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("the minimum order size must be positive")
        return value

    @field_validator("costmin")
    @classmethod
    def _costmin_not_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("the minimum order cost cannot be negative")
        return value

    @field_validator("tick_size")
    @classmethod
    def _tick_positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("the price tick must be positive")
        return value

    @field_serializer("ordermin", "costmin")
    def _serialize_required(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @field_serializer("tick_size")
    def _serialize_optional(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)

    @model_validator(mode="after")
    def _symbol_follows_the_wire_name(self) -> ListedPair:
        if engine_symbol(self.ws_name) != self.symbol:
            raise ValueError("the symbol must be the engine form of the wire name")
        return self

    @property
    def usd_quoted(self) -> bool:
        return self.symbol.rpartition("/")[2] == "USD"

    @property
    def in_normal_status(self) -> bool:
        return is_normal_status(self.status)


class PairRecord(ListedPair):
    """A listed pair with its classification. Exclusion codes are short, sorted and unique."""

    stablecoin: bool = Field(strict=True)
    yield_token: bool = Field(strict=True)
    exclusion_reasons: tuple[str, ...]
    rules_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    # "reviewed" only when the rules table names the base asset. Anything else is not verified clean.
    base_asset_review: AssetReview = "not_individually_reviewed"

    @field_validator("exclusion_reasons")
    @classmethod
    def _codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(re.fullmatch(r"[a-z][a-z0-9_]{0,31}", code) is None for code in value):
            raise ValueError("exclusion reasons are short lower-case codes")
        if list(value) != sorted(set(value)):
            raise ValueError("exclusion reasons must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _marks_agree_with_reasons(self) -> PairRecord:
        if self.stablecoin != (EXCLUSION_STABLECOIN in self.exclusion_reasons):
            raise ValueError("the stablecoin mark and its exclusion reason must agree")
        if self.yield_token != (EXCLUSION_YIELD_TOKEN in self.exclusion_reasons):
            raise ValueError("the yield-token mark and its exclusion reason must agree")
        return self

    @property
    def eligible_for_collection(self) -> bool:
        """Normal trading status and no exclusion. This is the list slice B of the collector gathers."""
        return self.in_normal_status and not self.exclusion_reasons
