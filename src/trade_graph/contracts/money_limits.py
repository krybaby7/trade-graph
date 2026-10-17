"""The owner's money limits and paper coin list as owner-policy fields (T32; D138 items 1, 2, 3 and 6).

These are blocks of ``OwnerPolicy``, which only the owner can revise (``AuthorityRecord.install_policy``). A
mandate cannot carry them, so the Leader can neither set nor raise them. The amount per coin and the total stay
the policy's existing ``maximum_single_asset_exposure_fraction`` and ``maximum_gross_exposure_fraction``; a
revision that holds ``money_limits`` is the one in which the owner recorded all four values with the owner
command ``trade-graph money-limits --confirm``. Until such a revision exists, day-plan order installation stays
off (D138's closing paragraph).

No database, clock, network or SDK import: these are plain validated records with Decimal money fractions.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from trade_graph.domain.money import canonical_decimal, parse_decimal

# D138 item 6: the trading day starts at 00:00 UTC, matching Kraken's daily candles, until the owner chooses.
DEFAULT_TRADING_DAY_START: Final = "00:00"
# W2-B's coin rule freshness (D174): a live scan older than two hours is "unknown".
DEFAULT_SCAN_AGE_SECONDS: Final = 7200
_DAY_START = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
_CURRENCY = re.compile(r"[A-Z]{3,5}")


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OwnerMoneyLimits(_Record):
    """The daily loss limit and the trading-day start (D138 items 1 and 6)."""

    schema_version: int = 1
    # Of the day-start value, fees included. At the limit new entries stop and resting entries cancel until the
    # next day start; protective exits and reconciliation continue; nothing is sold off.
    daily_loss_limit_fraction: Decimal
    # A plain UTC "HH:MM". The day start, the day end and the loss limit's reset run then.
    trading_day_start_utc: str = DEFAULT_TRADING_DAY_START
    # The account's native currency, in which the day-start value and the loss are measured (the paper account
    # is USD; EUR reporting labels never move the limit).
    account_currency: str = "USD"

    @field_validator("daily_loss_limit_fraction", mode="before")
    @classmethod
    def _fraction(cls, value: Any) -> Decimal:
        if isinstance(value, float):
            raise ValueError("a binary float is not a money fraction; give a decimal string")
        try:
            fraction = parse_decimal(value)
        except ArithmeticError as error:
            raise ValueError("the daily loss limit is a decimal fraction") from error
        if not Decimal("0") < fraction < Decimal("1"):
            raise ValueError("the daily loss limit is a fraction above 0 and below 1")
        return fraction

    @field_validator("trading_day_start_utc")
    @classmethod
    def _day_start(cls, value: str) -> str:
        if not isinstance(value, str) or _DAY_START.fullmatch(value) is None:
            raise ValueError("the trading-day start is a UTC time written HH:MM, for example 00:00")
        return value

    @field_validator("account_currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        if _CURRENCY.fullmatch(value) is None:
            raise ValueError("the account currency is a 3–5 letter code")
        return value

    @field_serializer("daily_loss_limit_fraction")
    def _ser_fraction(self, value: Decimal) -> str:
        return canonical_decimal(value)


class CoinScope(_Record):
    """D138 item 3, the paper coin list as a rule rather than a list.

    An entry is allowed on a pair that passed every liquidity and exclusion check of the scanner's newest live
    scan at that moment (``CoinScanStore.coin_rule``, W2-B): Kraken spot pairs in ``quote_asset`` without
    stablecoins, yield-paying tokens (D131) or riba products (D127). A scan older than ``max_scan_age_seconds``
    or a failed newest scan is unknown, and unknown refuses new entries (``when_unknown``); exits, protective
    exits and reconciliation never depend on the scan.
    """

    schema_version: int = 1
    rule: Literal["scanner_checks"] = "scanner_checks"
    quote_asset: str = "USD"
    max_scan_age_seconds: int = DEFAULT_SCAN_AGE_SECONDS
    when_unknown: Literal["refuse_new_entries"] = "refuse_new_entries"

    @field_validator("quote_asset")
    @classmethod
    def _quote(cls, value: str) -> str:
        if _CURRENCY.fullmatch(value) is None:
            raise ValueError("the quote asset is a 3–5 letter code")
        return value

    @field_validator("max_scan_age_seconds")
    @classmethod
    def _age(cls, value: int) -> int:
        if not 60 <= value <= 86400:
            raise ValueError("the scan age is between one minute and one day")
        return value


__all__ = ["DEFAULT_SCAN_AGE_SECONDS", "DEFAULT_TRADING_DAY_START", "CoinScope", "OwnerMoneyLimits"]
