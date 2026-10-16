"""The coin scanner's rules and the words its results use (T31; D120, D127, D131, D170-D174).

Software, not a model, ranks every Kraken USD spot pair once an hour by traded volume, typical swing relative to
the cost of one buy and one sell, spread and trend, drops thin pairs and the pairs the owner excluded, and keeps a
shortlist of about ten for Research. These rules are dated, versioned data: the digest of their canonical content
is the ranking version, so a changed threshold, indicator setting, cost profile or hold makes a new version and an
earlier shortlist keeps the version it was made with.

Nothing here imports an indicator library, a provider SDK or the network. Thresholds are exact decimals.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.contracts.candles import CandleSource
from trade_graph.contracts.indicators import get_indicator
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal

RULES_NAME = "coin-scanner"
RANKING_METHOD = "rank-sum-v1"
# The four things D120 ranks by. Volume, swing relative to cost and trend rank higher-is-better; spread lower.
FACTORS = ("volume_usd_24h", "swing_to_cost", "spread", "trend")
FactorName = Literal["volume_usd_24h", "swing_to_cost", "spread", "trend"]
ScanMode = Literal["live", "replay"]
SCAN_MODES = ("live", "replay")
SCAN_STATUSES = ("ok", "no_candidates", "failed")
SCAN_EVIDENCE = ("synthetic_fixture", "recorded_public_fixture", "live_public", "local_archive_file")

# Why a pair is not ranked. Each is one fixed word; a pair can have several.
EXCLUSION_REASONS = (
    "not_normal_status",       # the catalog status is not "online" (D133)
    "stablecoin",              # marked in the pair catalog (D131)
    "yield_token",             # marked in the pair catalog: staking, lending, rebasing (D131, D127)
    "fiat_currency",           # a currency listed against USD, not a coin (D133)
    "held_for_review",         # named for an owner's look and held until reviewed (D133, D171)
    "price_peg",               # an unreviewed coin that sat within the USD band for the peg window (D172)
    "price_pinned",            # an unreviewed coin whose price barely moved over the peg window (D172)
    "peg_check_unavailable",   # an unreviewed coin whose peg check had no complete candles (D172)
    "missing_candles",         # no stored completed candle in the last few hours
    "missing_volume",          # a slot of the volume window has no candle
    "missing_swing",           # the swing indicator is warming up or gapped
    "missing_trend",           # the trend indicator is warming up or gapped
    "missing_spread",          # no usable bid and ask for the pair (live scans only)
    "thin_volume",             # less traded volume than the threshold
    "wide_spread",             # a wider spread than the threshold
)
# Facts worth knowing that do not stop a pair from being ranked.
FLAGS = (
    "not_individually_reviewed",  # no catalog entry; passed the price-peg check, which is not a review
    "candles_lagging",            # the newest stored candle is one or more hours behind the scan hour
)

_ASSET = r"^[A-Z0-9]{1,16}$"


def _bounded(value: Any) -> Decimal:
    try:
        parsed = parse_decimal(value)
    except (ArithmeticError, ValueError) as error:
        raise ValueError("an exact decimal is required (no binary floats)") from error
    if len(parsed.as_tuple().digits) > 40:
        raise ValueError("decimal has too many digits")
    return parsed


class IndicatorChoice(BaseModel):
    """One entry of the indicator menu (D118) with its settings, resolved with the menu's defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    indicator: str = Field(pattern=r"^[A-Z_]{2,24}$")
    parameters: Mapping[str, int | float] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _on_the_menu(cls, data: Any) -> Any:
        """Unknown entries, unknown or out-of-bounds settings are refused; omitted settings get the menu default."""
        if not isinstance(data, Mapping):
            return data
        name, parameters = data.get("indicator"), data.get("parameters", {})
        if not isinstance(name, str) or not isinstance(parameters, Mapping):
            return data
        try:
            resolved = get_indicator(name).resolve_parameters(dict(parameters))
        except ValidationFailure as error:
            raise ValueError(str(error)) from None
        return {**data, "parameters": dict(sorted(resolved.items()))}

    @property
    def single_output(self) -> bool:
        return len(get_indicator(self.indicator).output_names) == 1

    def document(self) -> dict[str, Any]:
        return {"indicator": self.indicator, "parameters": dict(self.parameters)}


class AssetHold(BaseModel):
    """A coin the scanner does not rank until someone has reviewed it, with the reason and the decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset: str = Field(pattern=_ASSET)
    reason: str = Field(min_length=1, max_length=200)
    decision: str = Field(pattern=r"^D\d{1,4}$")


_HOLD_REASON = ("named in D133 as a call that needs the owner's eye (stablecoin or yield status unclear); "
                "held until it is reviewed")
DEFAULT_HOLDS: tuple[AssetHold, ...] = tuple(
    AssetHold(asset=asset, reason=_HOLD_REASON, decision="D133")
    for asset in ("U", "STBL", "UNITAS", "CAP", "DEBIT", "RE", "ST"))


class ScannerRules(BaseModel):
    """Everything that decides a ranking. Defaults are the recorded defaults (D170-D173)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(default=1, ge=1, strict=True)
    dated: date = date(2026, 10, 16)
    interval_minutes: Literal[60] = 60
    # Live scans read this candle series point-in-time; replays name their own source.
    live_source: CandleSource = "kraken_public_ohlc"
    # A pair's window ends at the newest completed hour that is stored, at most this many hours behind the scan hour.
    max_candle_lag_hours: int = Field(default=2, ge=0, le=6, strict=True)
    volume_hours: int = Field(default=24, ge=1, le=168, strict=True)
    min_volume_usd: Decimal = Decimal("500000")
    max_spread: Decimal = Decimal("0.005")
    max_ticker_age_seconds: int = Field(default=300, ge=1, le=3600, strict=True)
    swing: IndicatorChoice = IndicatorChoice(indicator="NATR", parameters={"timeperiod": 14})
    trend: IndicatorChoice = IndicatorChoice(indicator="ROCP", parameters={"timeperiod": 24})
    peg_hours: int = Field(default=72, ge=2, le=500, strict=True)
    usd_peg_low: Decimal = Decimal("0.98")
    usd_peg_high: Decimal = Decimal("1.02")
    pinned_range: Decimal = Decimal("0.02")
    cost_profile_id: str = Field(default="kraken-spot-starting-tier", pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    cost_liquidity: Literal["maker", "taker"] = "maker"
    shortlist_size: int = Field(default=10, ge=1, le=50, strict=True)
    holds: tuple[AssetHold, ...] = DEFAULT_HOLDS
    ranking_method: Literal["rank-sum-v1"] = RANKING_METHOD

    @field_validator("min_volume_usd", "max_spread", "usd_peg_low", "usd_peg_high", "pinned_range", mode="before")
    @classmethod
    def _exact(cls, value: Any) -> Decimal:
        return _bounded(value)

    @field_validator("min_volume_usd")
    @classmethod
    def _volume(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("the volume threshold must be positive")
        return value

    @field_validator("max_spread", "pinned_range")
    @classmethod
    def _fraction(cls, value: Decimal) -> Decimal:
        if not Decimal("0") < value < Decimal("1"):
            raise ValueError("a spread or range limit is a fraction above 0 and below 1")
        return value

    @field_serializer("min_volume_usd", "max_spread", "usd_peg_low", "usd_peg_high", "pinned_range")
    def _text(self, value: Decimal) -> str:
        return canonical_decimal(value)

    @model_validator(mode="after")
    def _consistent(self) -> ScannerRules:
        if not Decimal("0") < self.usd_peg_low < Decimal("1") < self.usd_peg_high < Decimal("2"):
            raise ValueError("the USD peg band must lie around 1 (low below 1, high above 1)")
        if not self.swing.single_output or not self.trend.single_output:
            raise ValueError("the swing and trend inputs must be menu entries with one output")
        assets = [hold.asset for hold in self.holds]
        if len(set(assets)) != len(assets):
            raise ValueError("a held asset appears twice")
        # Late import: the dated fee book lives in the protected kernel, which imports the contracts.
        from trade_graph.kernel.cost_profiles import load_cost_profiles

        if self.cost_profile_id not in load_cost_profiles().ids():
            raise ValueError(f"unknown cost profile {self.cost_profile_id}")
        return self

    def document(self) -> dict[str, Any]:
        """Canonical content: equal rules give equal documents, however their decimals were written."""
        return {
            "name": RULES_NAME, "revision": self.revision, "dated": self.dated.isoformat(),
            "interval_minutes": self.interval_minutes, "live_source": self.live_source,
            "max_candle_lag_hours": self.max_candle_lag_hours, "volume_hours": self.volume_hours,
            "min_volume_usd": canonical_decimal(self.min_volume_usd), "max_spread": canonical_decimal(self.max_spread),
            "max_ticker_age_seconds": self.max_ticker_age_seconds,
            "swing": self.swing.document(), "trend": self.trend.document(), "peg_hours": self.peg_hours,
            "usd_peg_low": canonical_decimal(self.usd_peg_low), "usd_peg_high": canonical_decimal(self.usd_peg_high),
            "pinned_range": canonical_decimal(self.pinned_range), "cost_profile_id": self.cost_profile_id,
            "cost_liquidity": self.cost_liquidity, "shortlist_size": self.shortlist_size,
            "holds": [hold.model_dump() for hold in self.holds], "ranking_method": self.ranking_method,
            "factors": list(FACTORS),
        }

    @property
    def digest(self) -> str:
        text = json.dumps(self.document(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    @property
    def ranking_version(self) -> str:
        return f"{RULES_NAME}-r{self.revision}-{self.dated.isoformat()}/{self.digest[:12]}"

    def held(self, asset: str) -> AssetHold | None:
        return next((hold for hold in self.holds if hold.asset == asset), None)


__all__ = [
    "DEFAULT_HOLDS", "EXCLUSION_REASONS", "FACTORS", "FLAGS", "RANKING_METHOD", "RULES_NAME", "SCAN_EVIDENCE",
    "SCAN_MODES", "SCAN_STATUSES", "AssetHold", "FactorName", "IndicatorChoice", "ScanMode", "ScannerRules",
]
