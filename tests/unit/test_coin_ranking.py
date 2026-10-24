"""The ranking step of the coin scanner (T31, D173): pure, deterministic, exact decimals, no model."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trade_graph.application.coin_scanner.ranking import Candidate, rank, shortlist
from trade_graph.contracts.coin_scanner import FACTORS
from trade_graph.domain.errors import ValidationFailure

D = Decimal


def candidate(symbol: str, volume: str, swing: str, spread: str | None, trend: str) -> Candidate:
    return Candidate(symbol=symbol, volume_usd_24h=D(volume), swing_to_cost=D(swing),
                     spread=None if spread is None else D(spread), trend=D(trend))


FIELD = [
    candidate("AAA/USD", "9000000", "1.5", "0.0010", "0.02"),
    candidate("BBB/USD", "2000000", "3.0", "0.0020", "0.05"),
    candidate("CCC/USD", "700000", "2.0", "0.0040", "-0.01"),
    candidate("DDD/USD", "5000000", "0.5", "0.0005", "0.00"),
]


def test_each_factor_ranks_in_its_own_direction_and_the_score_is_the_sum_of_places() -> None:
    ranked = {item.symbol: item for item in rank(FIELD, factors=FACTORS)}
    assert dict(ranked["AAA/USD"].ranks) == {"volume_usd_24h": 1, "swing_to_cost": 3, "spread": 2, "trend": 2}
    assert dict(ranked["BBB/USD"].ranks) == {"volume_usd_24h": 3, "swing_to_cost": 1, "spread": 3, "trend": 1}
    assert dict(ranked["CCC/USD"].ranks) == {"volume_usd_24h": 4, "swing_to_cost": 2, "spread": 4, "trend": 4}
    assert dict(ranked["DDD/USD"].ranks) == {"volume_usd_24h": 2, "swing_to_cost": 4, "spread": 1, "trend": 3}
    assert {symbol: item.score for symbol, item in ranked.items()} == {
        "AAA/USD": 8, "BBB/USD": 8, "CCC/USD": 14, "DDD/USD": 10}


def test_equal_scores_go_to_the_busier_pair_then_the_symbol() -> None:
    order = [item.symbol for item in rank(FIELD, factors=FACTORS)]
    assert order == ["AAA/USD", "BBB/USD", "DDD/USD", "CCC/USD"]  # AAA and BBB both score 8; AAA trades more
    twins = [candidate("ZZZ/USD", "1000000", "1", "0.001", "0"), candidate("YYY/USD", "1000000", "1", "0.001", "0")]
    assert [item.symbol for item in rank(twins, factors=FACTORS)] == ["YYY/USD", "ZZZ/USD"]
    assert [item.position for item in rank(twins, factors=FACTORS)] == [1, 2]


def test_ties_share_a_place_and_the_next_place_is_skipped() -> None:
    field = [candidate("A/USD", "1000000", "2", "0.001", "0.01"), candidate("B/USD", "1000000", "1", "0.001", "0.01"),
             candidate("C/USD", "900000", "1", "0.002", "0.01")]
    ranks = {item.symbol: dict(item.ranks) for item in rank(field, factors=FACTORS)}
    assert [ranks[s]["volume_usd_24h"] for s in ("A/USD", "B/USD", "C/USD")] == [1, 1, 3]
    assert [ranks[s]["swing_to_cost"] for s in ("A/USD", "B/USD", "C/USD")] == [1, 2, 2]
    assert [ranks[s]["trend"] for s in ("A/USD", "B/USD", "C/USD")] == [1, 1, 1]


def test_the_order_of_the_input_list_does_not_matter() -> None:
    forward = rank(FIELD, factors=FACTORS)
    backward = rank(list(reversed(FIELD)), factors=FACTORS)
    assert forward == backward


def test_a_factor_left_out_is_not_ranked_and_cannot_be_missing_otherwise() -> None:
    without_spread = tuple(name for name in FACTORS if name != "spread")
    field = [candidate("AAA/USD", "9000000", "1.5", None, "0.02"), candidate("BBB/USD", "2000000", "3", None, "0.05")]
    ranked = rank(field, factors=without_spread)
    assert all("spread" not in item.ranks for item in ranked)
    with pytest.raises(ValidationFailure, match="spread"):
        rank(field, factors=FACTORS)


def test_the_shortlist_is_the_top_places_and_never_padded() -> None:
    ranked = rank(FIELD, factors=FACTORS)
    assert [item.symbol for item in shortlist(ranked, 2)] == ["AAA/USD", "BBB/USD"]
    assert len(shortlist(ranked, 10)) == 4
    assert shortlist((), 10) == ()


def test_bad_input_is_refused_instead_of_ranked() -> None:
    with pytest.raises(ValidationFailure, match="twice"):
        rank([FIELD[0], FIELD[0]], factors=FACTORS)
    with pytest.raises(ValidationFailure):
        rank([Candidate(symbol="X/USD", volume_usd_24h=1.0, swing_to_cost=D(1), spread=D(0), trend=D(0))],
             factors=FACTORS)
    with pytest.raises(ValidationFailure):
        rank([candidate("X/USD", "NaN", "1", "0.001", "0")], factors=FACTORS)
    with pytest.raises(ValidationFailure, match="factor"):
        rank(FIELD, factors=("volume_usd_24h", "colour"))
    with pytest.raises(ValidationFailure):
        shortlist(rank(FIELD, factors=FACTORS), 0)
