"""Decimal money and quantities. Binary floats are rejected."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


def canonical_decimal(value: Decimal) -> str:
    if not isinstance(value, Decimal):
        raise TypeError("expected Decimal")
    if not value.is_finite():
        raise ValueError("non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def parse_decimal(value: Any) -> Decimal:
    if isinstance(value, float):
        raise ValueError("binary float is not a monetary amount")
    if isinstance(value, bool) or value is None:
        raise ValueError("invalid decimal")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, str):
        parsed = Decimal(value)
    else:
        raise ValueError("invalid decimal")
    if not parsed.is_finite():
        raise ValueError("non-finite decimal")
    return parsed


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal
    currency: str

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: Any) -> Decimal:
        return parse_decimal(value)

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        if not isinstance(value, str) or not value.isupper() or not value.isalnum():
            raise ValueError("currency must be uppercase alphanumeric")
        if not 2 <= len(value) <= 16:
            raise ValueError("currency length")
        return value

    @field_serializer("amount")
    def _ser_amount(self, value: Decimal) -> str:
        return canonical_decimal(value)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise ValueError("currency mismatch")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise ValueError("currency mismatch")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)


class Quantity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal
    asset: str

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, value: Any) -> Decimal:
        return parse_decimal(value)

    @field_validator("asset")
    @classmethod
    def _asset(cls, value: str) -> str:
        if not isinstance(value, str) or not value.isalnum() or not value.isupper():
            raise ValueError("asset code")
        return value

    @field_serializer("amount")
    def _ser_amount(self, value: Decimal) -> str:
        return canonical_decimal(value)
