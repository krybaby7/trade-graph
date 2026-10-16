"""Contracts for the shared cautious fill engine (T28): dated cost profiles, fill policies, orders, exits and fills.

A ``CostProfile`` is a dated data record of one venue's fee tier with its sources. A ``FillPolicy`` holds the
simulation assumptions (volume share, slippage) that are not venue facts. ``SimOrder`` and ``ProtectiveExit``
are what the engine evaluates; ``SimFill`` is what it returns. Every fill names the exact profile and policy
revision it was computed with, so a later profile revision never changes an earlier result (D126, D145-D149).
Money, prices, rates and quantities are exact decimals; nothing here depends on a provider SDK.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal, parse_decimal

Side = Literal["buy", "sell"]
Liquidity = Literal["maker", "taker"]
OrderKind = Literal["market", "limit"]
FillReason = Literal[
    "market",              # taker entry at the touch (quotes) or the next open (bars)
    "marketable_limit",    # a limit at or through the placement price: a taker, never beyond its limit
    "limit_book_cross",    # quotes: the opposite best price reached the resting limit
    "limit_trade_through",  # bars: trades printed beyond the resting limit, not merely at it
    "stop",                # the stop level was reached
    "stop_gap",            # the price opened (bars) or was quoted (quotes) beyond the stop: the worse price
    "stop_both_hit",       # one bar reached both exits: the stop is assumed
    "stop_entry_bar",      # the stop was reached in the bar the position was entered
    "take_profit",         # trades printed beyond the take-profit level
]
CheckResult = Literal["read_official_page", "unreachable_from_build_environment"]
ProfileBasis = Literal["published_schedule", "explicit_configuration"]

_SYMBOL = r"^[A-Z0-9]{1,16}/[A-Z0-9]{2,8}$"
_IDENTIFIER = r"^[a-z0-9][a-z0-9.-]{2,95}$"
_REFERENCE = r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$"
_DECIMAL_TEXT_LIMIT = 64
_DECIMAL_DIGIT_LIMIT = 60
_DECIMAL_EXPONENT_LIMIT = 30
MAX_FEE_RATE = Decimal("0.05")
MAX_SLIPPAGE_RATE = Decimal("0.05")


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


def _content_sha256(model: BaseModel) -> str:
    document = model.model_dump(mode="json")
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeeSource(_Record):
    """Where a fee figure comes from, when it was published and when this build last checked the page."""

    url: str = Field(pattern=r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~%/?#=&+-]*)?$", max_length=300)
    title: str = Field(min_length=3, max_length=200)
    figure_date: date | None
    checked_on: date
    check_result: CheckResult
    note: str = Field(min_length=10, max_length=800)


class CostProfile(_Record):
    """One venue fee tier as a dated, sourced, immutable revision. Rates are per side, charged in the quote asset."""

    profile_id: str = Field(pattern=_IDENTIFIER)
    revision: int = Field(ge=1, strict=True)
    venue: str = Field(pattern=r"^[a-z0-9-]{2,32}$")
    market: Literal["spot"]
    title: str = Field(min_length=3, max_length=200)
    tier: str = Field(min_length=3, max_length=200)
    maker_rate: Decimal
    taker_rate: Decimal
    fee_asset: Literal["quote"]
    effective_from: date
    recorded_on: date
    basis: ProfileBasis
    sources: tuple[FeeSource, ...] = Field(max_length=8)
    decisions: tuple[str, ...] = Field(max_length=12)
    note: str = Field(max_length=1200)

    @field_validator("maker_rate", "taker_rate", mode="before")
    @classmethod
    def _rate(cls, value: Any) -> Decimal:
        rate = _bounded_decimal(value)
        # No rebates: a negative maker fee would let a simulation earn from its own orders.
        if not Decimal("0") <= rate <= MAX_FEE_RATE:
            raise ValueError("fee rates lie between 0 and 5% per side")
        return rate

    @field_validator("decisions")
    @classmethod
    def _decisions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.startswith("D") or not item[1:].isdigit() for item in value):
            raise ValueError("decision references look like D126")
        return value

    @field_serializer("maker_rate", "taker_rate")
    def _serialize_rate(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @model_validator(mode="after")
    def _sourced(self) -> CostProfile:
        if self.basis == "published_schedule":
            if not self.sources:
                raise ValueError("a published fee schedule names at least one source")
            if any(source.figure_date is not None and source.figure_date > self.recorded_on
                   for source in self.sources):
                raise ValueError("a source figure cannot postdate its record")
        elif self.sources:
            raise ValueError("explicitly configured rates cite no published source")
        return self

    @property
    def ref(self) -> str:
        return f"{self.profile_id}@{self.revision}"

    @property
    def content_sha256(self) -> str:
        return _content_sha256(self)

    def fee_rate(self, liquidity: Liquidity) -> Decimal:
        return self.maker_rate if liquidity == "maker" else self.taker_rate

    def round_trip_rate(self, liquidity: Liquidity) -> Decimal:
        """Cost of one buy and one sell as a fraction of notional, both sides at the same liquidity."""
        return 2 * self.fee_rate(liquidity)

    @classmethod
    def explicit(cls, *, maker_rate: Decimal, taker_rate: Decimal, venue: str = "paper",
                 on: date | None = None) -> CostProfile:
        """Rates passed in configuration rather than read from a schedule; labelled so, never mistaken for one."""
        maker, taker = parse_decimal(maker_rate), parse_decimal(taker_rate)
        day = on or date(2026, 1, 1)
        return cls(
            profile_id=f"explicit-{venue}-maker-{canonical_decimal(maker)}-taker-{canonical_decimal(taker)}",
            revision=1, venue=venue, market="spot", title="Explicitly configured rates",
            tier="explicit configuration", maker_rate=maker, taker_rate=taker, fee_asset="quote",
            effective_from=day, recorded_on=day, basis="explicit_configuration", sources=(), decisions=("D14",),
            note="Rates passed to the simulator directly, not read from a dated venue schedule.",
        )


class FillPolicy(_Record):
    """Simulation assumptions that are not venue facts. Revised by a new policy id or revision, never in place."""

    policy_id: str = Field(pattern=_IDENTIFIER)
    revision: int = Field(ge=1, strict=True)
    # Share of the observed volume (bars, quote volume) or displayed size (quotes) one order may take.
    participation: Decimal
    # Quotes without any volume figure: a resting limit takes at most this share of its remaining quantity.
    unknown_volume_fill_share: Decimal = Decimal("0.5")
    # Taker entries move this fraction against the order from the reference price.
    entry_slippage_rate: Decimal
    # Triggered protective exits (taker sells) move this fraction against the seller.
    exit_slippage_rate: Decimal

    @field_validator("participation", "unknown_volume_fill_share", mode="before")
    @classmethod
    def _share(cls, value: Any) -> Decimal:
        share = _bounded_decimal(value)
        if not Decimal("0") < share <= Decimal("1"):
            raise ValueError("shares lie above 0 and at most 1")
        return share

    @field_validator("entry_slippage_rate", "exit_slippage_rate", mode="before")
    @classmethod
    def _slippage(cls, value: Any) -> Decimal:
        rate = _bounded_decimal(value)
        if not Decimal("0") <= rate <= MAX_SLIPPAGE_RATE:
            raise ValueError("slippage lies between 0 and 5%")
        return rate

    @field_serializer("participation", "unknown_volume_fill_share", "entry_slippage_rate", "exit_slippage_rate")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @property
    def ref(self) -> str:
        return f"{self.policy_id}@{self.revision}"

    @property
    def content_sha256(self) -> str:
        return _content_sha256(self)


class SimOrder(_Record):
    """An entry or a deliberate sell. ``remaining`` is what is still unfilled; the caller tracks it between events.

    ``placement_price`` is the latest price known when the order was placed. On bars it decides whether a limit is
    resting (a maker) or marketable (a taker). ``eligible_after_utc`` excludes every event that began before the
    order existed, so the bar or quote a decision was made on can never fill it.
    """

    order_id: str = Field(pattern=_REFERENCE)
    symbol: str = Field(pattern=_SYMBOL)
    side: Side
    kind: OrderKind
    quantity: Decimal
    remaining: Decimal
    limit_price: Decimal | None = None
    placement_price: Decimal | None = None
    eligible_after_utc: AwareDatetime
    excluded_event_ref: str | None = Field(default=None, pattern=_REFERENCE)

    @field_validator("quantity", "remaining", "limit_price", "placement_price", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Decimal | None:
        return None if value is None else _bounded_decimal(value)

    @field_serializer("quantity", "remaining", "limit_price", "placement_price")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)

    @field_validator("eligible_after_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_serializer("eligible_after_utc", when_used="json")
    def _serialize_time(self, value: datetime) -> str:
        return utc_iso(value)

    @model_validator(mode="after")
    def _consistent(self) -> SimOrder:
        if self.quantity <= 0 or not Decimal("0") <= self.remaining <= self.quantity:
            raise ValueError("quantity is positive and remaining lies between 0 and quantity")
        if (self.kind == "limit") != (self.limit_price is not None):
            raise ValueError("a limit order, and only a limit order, has a limit price")
        if any(price is not None and price <= 0 for price in (self.limit_price, self.placement_price)):
            raise ValueError("prices are positive")
        return self

    @property
    def marketable(self) -> bool:
        """A limit at or through the placement price would cross the book on arrival."""
        if self.kind != "limit" or self.placement_price is None or self.limit_price is None:
            return False
        if self.side == "buy":
            return self.limit_price >= self.placement_price
        return self.limit_price <= self.placement_price


class ProtectiveExit(_Record):
    """A stop-loss and optional take-profit on one long spot position; whichever fills first cancels the other.

    Spot and long only (D127): an exit always sells what is held. Both legs fill as triggered taker sells with
    slippage. ``eligible_after_utc`` is when the exit was armed (the entry fill).
    """

    exit_id: str = Field(pattern=_REFERENCE)
    symbol: str = Field(pattern=_SYMBOL)
    side: Literal["sell"] = "sell"
    quantity: Decimal
    stop_price: Decimal
    take_profit_price: Decimal | None = None
    eligible_after_utc: AwareDatetime
    excluded_event_ref: str | None = Field(default=None, pattern=_REFERENCE)

    @field_validator("quantity", "stop_price", "take_profit_price", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Decimal | None:
        return None if value is None else _bounded_decimal(value)

    @field_serializer("quantity", "stop_price", "take_profit_price")
    def _serialize(self, value: Decimal | None) -> str | None:
        return None if value is None else canonical_decimal(value)

    @field_validator("eligible_after_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_serializer("eligible_after_utc", when_used="json")
    def _serialize_time(self, value: datetime) -> str:
        return utc_iso(value)

    @model_validator(mode="after")
    def _consistent(self) -> ProtectiveExit:
        if self.quantity <= 0 or self.stop_price <= 0:
            raise ValueError("quantity and stop are positive")
        if self.take_profit_price is not None and self.take_profit_price <= self.stop_price:
            raise ValueError("a take-profit sits above the stop on a long position")
        return self


class SimFill(_Record):
    """One simulated fill with the exact terms it was computed under."""

    order_id: str = Field(pattern=_REFERENCE)
    symbol: str = Field(pattern=_SYMBOL)
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str = Field(pattern=r"^[A-Z0-9]{2,8}$")
    liquidity: Liquidity
    reason: FillReason
    # The price slippage was applied to: the limit, the touch, the open, the stop or the take-profit.
    reference_price: Decimal
    filled_at_utc: AwareDatetime
    event_ref: str = Field(pattern=_REFERENCE)
    heuristic: bool
    cost_profile_ref: str = Field(pattern=_REFERENCE)
    cost_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fill_policy_ref: str = Field(pattern=_REFERENCE)
    fill_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("quantity", "price", "fee", "reference_price", mode="before")
    @classmethod
    def _decimal(cls, value: Any) -> Decimal:
        return parse_decimal(value)

    @field_validator("filled_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_serializer("quantity", "price", "fee", "reference_price")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @field_serializer("filled_at_utc", when_used="json")
    def _serialize_time(self, value: datetime) -> str:
        return utc_iso(value)

    @model_validator(mode="after")
    def _positive(self) -> SimFill:
        if self.quantity <= 0 or self.price <= 0 or self.fee < 0:
            raise ValueError("a fill has positive quantity and price and a nonnegative fee")
        return self

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price
