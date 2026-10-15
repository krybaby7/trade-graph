"""Versioned, dated stablecoin and yield-token rules (D131, D127); unreviewed assets stay visible."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from tests.network_guard import block_network

from trade_graph.application.pair_classification import (
    ASSET_RULES,
    CLASSIFICATION_RULES_VERSION,
    RULES_DATE,
    AssetRule,
    class_label,
    classification_summary,
    classify_pair,
    rule_for,
    unreviewed_assets,
)
from trade_graph.contracts.pair_catalog import ListedPair, PairRecord


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _listed(wire_base: str = "XBT", *, status: str = "online", quote: str = "USD") -> ListedPair:
    from trade_graph.contracts.pair_catalog import engine_symbol

    ws_name = f"{wire_base}/{quote}"
    return ListedPair(
        symbol=engine_symbol(ws_name), source_pair=f"{wire_base}{quote}", ws_name=ws_name, base=f"X{wire_base}",
        quote=f"Z{quote}", status=status, ordermin=Decimal("1"), costmin=Decimal("0.5"), tick_size=Decimal("0.01"),
        pair_decimals=2, cost_decimals=5, lot_decimals=8)


def test_rules_are_versioned_and_dated() -> None:
    assert CLASSIFICATION_RULES_VERSION.startswith("pair-rules-") and RULES_DATE in CLASSIFICATION_RULES_VERSION
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", RULES_DATE)


def test_every_rule_has_a_one_line_reason_and_a_basis() -> None:
    assert ASSET_RULES
    seen: set[str] = set()
    for rule in ASSET_RULES:
        assert isinstance(rule, AssetRule)
        assert re.fullmatch(r"[A-Z0-9]{1,16}", rule.asset), rule.asset
        assert rule.asset not in seen, f"{rule.asset} is listed twice"
        seen.add(rule.asset)
        assert 10 <= len(rule.reason) <= 200 and "\n" not in rule.reason, rule.asset
        assert 10 <= len(rule.basis) <= 240 and "\n" not in rule.basis, rule.asset
        assert not (rule.fiat and (rule.stablecoin or rule.yield_token)), rule.asset


def test_rules_are_keyed_by_engine_codes_not_kraken_renames() -> None:
    assets = {rule.asset for rule in ASSET_RULES}
    assert "BTC" in assets and "DOGE" in assets
    assert not {"XBT", "XDG"} & assets


STABLECOINS = [
    "USDT", "USDC", "DAI", "USDS", "PYUSD", "USD1", "USDD", "USDE", "USDG", "RLUSD", "AUSD", "FIDD", "SOFID",
    "CASH", "USDGO", "USDSM", "USDPT", "USDSUI", "APXUSD", "BRL1", "TGBP", "COPM", "EURC", "EURR", "EURQ", "USDR",
    "USDQ", "EUROP", "ZCHF", "MXNB", "QCAD", "MIM", "USTABLES", "AUDX",
]
YIELD_TOKENS = ["LSETH", "LSSOL", "MSOL", "JITOSOL", "METH", "CMETH"]
FIAT = ["EUR", "GBP", "AUD"]
REVIEWED_NOT_MARKED = [
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BCH", "LINK", "DOT", "AVAX", "XLM", "XMR", "ETC", "ZEC",
    "ATOM", "TRX", "SHIB", "PEPE", "PAXG", "XAUT", "WBTC", "TBTC", "USDUC", "STABLE",
]


@pytest.mark.parametrize("asset", STABLECOINS)
def test_stablecoins_are_marked_and_excluded(asset: str) -> None:
    rule = rule_for(asset)
    assert rule is not None and rule.stablecoin and not rule.yield_token and not rule.fiat
    record = classify_pair(_listed(asset))
    assert record.stablecoin and not record.yield_token
    assert record.exclusion_reasons == ("stablecoin",)
    assert record.base_asset_review == "reviewed" and class_label(record) == "stablecoin"
    assert not record.eligible_for_collection and record.in_normal_status


@pytest.mark.parametrize("asset", YIELD_TOKENS)
def test_yield_tokens_are_marked_and_excluded(asset: str) -> None:
    record = classify_pair(_listed(asset))
    assert record.yield_token and not record.stablecoin
    assert record.exclusion_reasons == ("yield_token",)
    assert class_label(record) == "yield_token" and not record.eligible_for_collection


def test_a_yield_bearing_stablecoin_carries_both_marks() -> None:
    record = classify_pair(_listed("OUSD"))
    assert record.stablecoin and record.yield_token
    assert record.exclusion_reasons == ("stablecoin", "yield_token")
    assert class_label(record) == "stablecoin_and_yield_token"


@pytest.mark.parametrize("asset", FIAT)
def test_fiat_currencies_listed_against_usd_are_excluded_but_are_not_stablecoins(asset: str) -> None:
    record = classify_pair(_listed(asset))
    assert not record.stablecoin and not record.yield_token
    assert record.exclusion_reasons == ("fiat_currency",)
    assert class_label(record) == "fiat_currency" and not record.eligible_for_collection


@pytest.mark.parametrize("asset", REVIEWED_NOT_MARKED)
def test_reviewed_assets_without_a_mark_are_collectable_and_say_they_were_reviewed(asset: str) -> None:
    rule = rule_for(asset)
    assert rule is not None and not (rule.stablecoin or rule.yield_token or rule.fiat)
    record = classify_pair(_listed(asset))
    assert record.exclusion_reasons == () and record.base_asset_review == "reviewed"
    assert class_label(record) == "reviewed_not_marked" and record.eligible_for_collection


def test_native_proof_of_stake_coins_are_not_treated_as_yield_tokens() -> None:
    for asset in ("ETH", "SOL", "ADA", "DOT", "AVAX", "ATOM"):
        assert not rule_for(asset).yield_token
        assert "stak" in rule_for(asset).reason.lower()


def test_a_stable_looking_name_is_not_enough_to_mark_a_coin() -> None:
    usduc = rule_for("USDUC")
    assert not usduc.stablecoin and "meme" in usduc.reason.lower()
    stable = rule_for("STABLE")
    assert not stable.stablecoin and "not a dollar-pegged" in stable.reason
    # Frankencoin is a token, not the legacy "Z" fiat code that Kraken uses for CHF-like names.
    assert rule_for("ZCHF").stablecoin and not rule_for("ZCHF").fiat


def test_an_asset_without_a_rule_stays_unmarked_and_is_not_presented_as_reviewed() -> None:
    assert rule_for("NOSUCHCOIN") is None
    record = classify_pair(_listed("NOSUCHCOIN"))
    assert not record.stablecoin and not record.yield_token and record.exclusion_reasons == ()
    assert record.base_asset_review == "not_individually_reviewed"
    assert class_label(record) == "not_individually_reviewed"
    assert record.eligible_for_collection and record.rules_version == CLASSIFICATION_RULES_VERSION


def test_classification_never_changes_the_listing_facts() -> None:
    listed = _listed("XBT")
    record = classify_pair(listed)
    assert isinstance(record, PairRecord)
    for name in ListedPair.model_fields:
        assert getattr(record, name) == getattr(listed, name)


def test_classification_applies_to_the_base_asset_only() -> None:
    assert not classify_pair(_listed("ETH", quote="USDT")).stablecoin
    assert classify_pair(_listed("USDT", quote="EUR")).stablecoin


def test_a_stablecoin_that_is_not_online_is_still_marked() -> None:
    record = classify_pair(_listed("USDR", status="post_only"))
    assert record.stablecoin and not record.in_normal_status and not record.eligible_for_collection


def test_summary_counts_status_and_class_and_lists_unreviewed_assets() -> None:
    records = [
        classify_pair(_listed("XBT")),
        classify_pair(_listed("ETH")),
        classify_pair(_listed("USDT")),
        classify_pair(_listed("LSETH")),
        classify_pair(_listed("OUSD")),
        classify_pair(_listed("EUR")),
        classify_pair(_listed("ZZZ", status="cancel_only")),
        classify_pair(_listed("AAA")),
    ]
    assert unreviewed_assets(records) == ["AAA", "ZZZ"]
    summary = classification_summary(records)
    assert summary["by_status"] == {"online": 7, "cancel_only": 1, "post_only": 0, "limit_only": 0, "reduce_only": 0}
    assert summary["by_class"] == {
        "stablecoin": 1, "yield_token": 1, "stablecoin_and_yield_token": 1, "fiat_currency": 1,
        "reviewed_not_marked": 2, "not_individually_reviewed": 2,
    }
    assert summary["excluded_pairs"] == 4
    assert summary["eligible_for_collection"] == 3
    assert summary["not_normal_status"] == 1
    assert summary["unreviewed_assets"] == ["AAA", "ZZZ"]
    assert summary["unreviewed_asset_count"] == 2
    assert summary["eligible_unreviewed_pairs"] == 1  # AAA is online and unmarked; ZZZ is cancel-only
    assert summary["reviewed_asset_count"] == 6
