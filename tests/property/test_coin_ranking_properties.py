"""Property tests for the coin scanner's ranking step (T31, D173): random fields of candidates.

- The ranking depends only on the candidates, never on their order in the list.
- Positions are 1..n, sorted by score, then the busier pair, then the symbol.
- A better value in one factor, all else equal, never moves a pair down.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from trade_graph.application.coin_scanner.ranking import Candidate, rank, shortlist
from trade_graph.contracts.coin_scanner import FACTORS

_amount = st.integers(min_value=0, max_value=50).map(lambda value: Decimal(value) * 100000)
_ratio = st.integers(min_value=0, max_value=40).map(lambda value: Decimal(value) / 10)
_spread = st.integers(min_value=1, max_value=50).map(lambda value: Decimal(value) / 10000)
_trend = st.integers(min_value=-20, max_value=20).map(lambda value: Decimal(value) / 100)


@st.composite
def _fields(draw) -> list[Candidate]:
    count = draw(st.integers(min_value=1, max_value=25))
    names = draw(st.lists(st.text(alphabet="ABCDEFGHIJ", min_size=1, max_size=3), min_size=count, max_size=count,
                          unique=True))
    return [Candidate(symbol=f"{name}/USD", volume_usd_24h=draw(_amount), swing_to_cost=draw(_ratio),
                      spread=draw(_spread), trend=draw(_trend)) for name in names]


@settings(max_examples=150, derandomize=True, deadline=None)
@given(_fields(), st.randoms(use_true_random=False))
def test_the_ranking_ignores_the_order_of_the_list(field, shuffler) -> None:
    shuffled = list(field)
    shuffler.shuffle(shuffled)
    assert rank(field, factors=FACTORS) == rank(shuffled, factors=FACTORS)


@settings(max_examples=150, derandomize=True, deadline=None)
@given(_fields(), st.integers(min_value=1, max_value=30))
def test_positions_follow_score_then_volume_then_symbol(field, size) -> None:
    ranked = rank(field, factors=FACTORS)
    assert [item.position for item in ranked] == list(range(1, len(field) + 1))
    keys = [(item.score, -item.candidate.volume_usd_24h, item.symbol) for item in ranked]
    assert keys == sorted(keys)
    for item in ranked:
        assert item.score == sum(item.ranks.values())
        assert all(1 <= place <= len(field) for place in item.ranks.values())
    assert shortlist(ranked, size) == ranked[:size]


@settings(max_examples=150, derandomize=True, deadline=None)
@given(_fields(), st.data())
def test_a_better_value_never_moves_a_pair_down(field, data) -> None:
    index = data.draw(st.integers(min_value=0, max_value=len(field) - 1))
    factor = data.draw(st.sampled_from(FACTORS))
    before = {item.symbol: item.position for item in rank(field, factors=FACTORS)}
    chosen = field[index]
    improved = {
        "volume_usd_24h": {"volume_usd_24h": chosen.volume_usd_24h + 100000},
        "swing_to_cost": {"swing_to_cost": chosen.swing_to_cost + 1},
        "spread": {"spread": chosen.spread / 2},
        "trend": {"trend": chosen.trend + Decimal("0.05")},
    }[factor]
    changed = [*field[:index], replace(chosen, **improved), *field[index + 1:]]
    after = {item.symbol: item.position for item in rank(changed, factors=FACTORS)}
    assert after[chosen.symbol] <= before[chosen.symbol]
