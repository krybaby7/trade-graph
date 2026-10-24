"""The coin scanner's rules (T31, D170-D173): dated, versioned data whose digest is the ranking version."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trade_graph.contracts.coin_scanner import (
    DEFAULT_HOLDS,
    EXCLUSION_REASONS,
    FACTORS,
    FLAGS,
    AssetHold,
    IndicatorChoice,
    ScannerRules,
)
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY


def test_the_default_rules_are_the_recorded_defaults() -> None:
    rules = ScannerRules()
    assert rules.interval_minutes == 60 and rules.live_source == "kraken_public_ohlc"
    assert rules.volume_hours == 24 and rules.min_volume_usd == Decimal("500000")
    assert rules.max_spread == Decimal("0.005")
    assert (rules.swing.indicator, dict(rules.swing.parameters)) == ("NATR", {"timeperiod": 14})
    assert (rules.trend.indicator, dict(rules.trend.parameters)) == ("ROCP", {"timeperiod": 24})
    assert rules.peg_hours == 72
    assert (rules.usd_peg_low, rules.usd_peg_high, rules.pinned_range) == (
        Decimal("0.98"), Decimal("1.02"), Decimal("0.02"))
    assert (rules.cost_profile_id, rules.cost_liquidity) == (KRAKEN_STARTING, "maker")
    assert rules.shortlist_size == 10 and rules.max_candle_lag_hours == 2
    assert rules.ranking_method == "rank-sum-v1"
    assert FACTORS == ("volume_usd_24h", "swing_to_cost", "spread", "trend")


def test_the_minimum_volume_lets_one_full_position_fill_within_an_hour_of_typical_trading() -> None:
    """D138 caps a coin at 20% of the USD10,000 paper account; the practice range lets one fill take at most 10% of a
    bar's volume (D148). A whole position then needs USD20,000 traded per hour, USD480,000 a day."""
    per_coin = Decimal("10000") * Decimal("0.20")
    per_day = per_coin / BAR_REPLAY_POLICY.participation * 24
    assert per_day == Decimal("480000")
    assert ScannerRules().min_volume_usd >= per_day


def test_the_hold_list_names_the_coins_the_pair_catalog_left_for_an_owner_look() -> None:
    held = {hold.asset for hold in DEFAULT_HOLDS}
    assert held == {"U", "STBL", "UNITAS", "CAP", "DEBIT", "RE", "ST"}
    assert all(hold.decision == "D133" for hold in DEFAULT_HOLDS)
    assert ScannerRules().holds == DEFAULT_HOLDS


def test_the_ranking_version_is_a_digest_of_every_rule() -> None:
    first, again = ScannerRules(), ScannerRules()
    assert first.ranking_version == again.ranking_version
    assert first.ranking_version.startswith("coin-scanner-r1-2026-10-16/")
    changed = [
        ScannerRules(min_volume_usd=Decimal("500001")),
        ScannerRules(max_spread=Decimal("0.004")),
        ScannerRules(shortlist_size=11),
        ScannerRules(swing=IndicatorChoice(indicator="NATR", parameters={"timeperiod": 24})),
        ScannerRules(trend=IndicatorChoice(indicator="ROCP", parameters={"timeperiod": 48})),
        ScannerRules(cost_profile_id="okx-eea-spot"),
        ScannerRules(holds=DEFAULT_HOLDS[1:]),
        ScannerRules(pinned_range=Decimal("0.03")),
    ]
    versions = {rules.ranking_version for rules in changed}
    assert len(versions) == len(changed) and first.ranking_version not in versions


def test_an_equal_decimal_written_differently_is_the_same_rule() -> None:
    assert ScannerRules(min_volume_usd=Decimal("500000.00")).ranking_version == ScannerRules().ranking_version


def test_money_thresholds_refuse_binary_floats_and_impossible_values() -> None:
    with pytest.raises(ValidationError):
        ScannerRules(min_volume_usd=500000.0)
    with pytest.raises(ValidationError):
        ScannerRules(max_spread=Decimal("0"))
    with pytest.raises(ValidationError):
        ScannerRules(max_spread=Decimal("1"))
    with pytest.raises(ValidationError):
        ScannerRules(usd_peg_low=Decimal("1.05"), usd_peg_high=Decimal("1.02"))
    with pytest.raises(ValidationError):
        ScannerRules(shortlist_size=0)
    with pytest.raises(ValidationError):
        ScannerRules(unknown_setting=1)


def test_indicator_choices_come_from_the_menu_and_are_resolved_with_their_defaults() -> None:
    choice = IndicatorChoice(indicator="NATR", parameters={})
    assert dict(choice.parameters) == {"timeperiod": 14}
    with pytest.raises(ValidationError):
        IndicatorChoice(indicator="VWAP", parameters={})
    with pytest.raises(ValidationError):
        IndicatorChoice(indicator="NATR", parameters={"timeperiod": 1})
    with pytest.raises(ValidationError):
        IndicatorChoice(indicator="NATR", parameters={"period": 14})
    with pytest.raises(ValidationError):  # the swing and trend inputs are one number each
        ScannerRules(swing=IndicatorChoice(indicator="MACD", parameters={}))


def test_the_cost_profile_must_exist_in_the_dated_book() -> None:
    assert load_cost_profiles().effective(ScannerRules().cost_profile_id, date(2026, 10, 16)).ref == \
        "kraken-spot-starting-tier@1"
    with pytest.raises(ValidationError):
        ScannerRules(cost_profile_id="made-up-venue")


def test_holds_are_unique_upper_case_assets_with_a_reason() -> None:
    with pytest.raises(ValidationError):
        AssetHold(asset="u", reason="lower case", decision="D133")
    with pytest.raises(ValidationError):
        AssetHold(asset="U", reason="", decision="D133")
    with pytest.raises(ValidationError):
        ScannerRules(holds=(DEFAULT_HOLDS[0], DEFAULT_HOLDS[0]))


def test_reason_and_flag_codes_are_short_fixed_words() -> None:
    for code in (*EXCLUSION_REASONS, *FLAGS):
        assert code.replace("_", "").isalpha() and code.islower() and len(code) <= 32
    assert len(set(EXCLUSION_REASONS)) == len(EXCLUSION_REASONS)
    assert not set(EXCLUSION_REASONS) & set(FLAGS)
