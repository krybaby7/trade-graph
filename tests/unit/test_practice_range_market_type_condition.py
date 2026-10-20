"""The practice-range grammar's market-type condition (W11-A, C10; D288).

A strategy may require the coin's market type at entry: ``{"market_type": "rising", "relation": "is"}`` or
``"is_not"``, among its entry conditions. It is data like every other condition; existing documents keep their hashes.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.market_type_support import busy_strategy
from tests.practice_range_support import cross_strategy

from trade_graph.contracts.practice_range import MarketTypeCondition, PracticeStrategy


def _document(**changes) -> dict:
    document = json.loads(json.dumps(busy_strategy().canonical_document()))
    document.update(changes)
    return document


def test_a_strategy_may_require_a_market_type_at_entry() -> None:
    strategy = busy_strategy(("is", "rising"), ("is_not", "unclear"))
    conditions = strategy.entry.all_of
    assert conditions[1] == MarketTypeCondition(market_type="rising", relation="is")
    assert conditions[2] == MarketTypeCondition(market_type="unclear", relation="is_not")
    assert strategy.uses_market_type and not busy_strategy().uses_market_type
    assert not cross_strategy().uses_market_type
    assert strategy.canonical_document()["entry"]["all_of"][1] == {"market_type": "rising", "relation": "is"}
    assert strategy.content_sha256 != busy_strategy().content_sha256
    assert PracticeStrategy.model_validate_json(json.dumps(strategy.canonical_document())) == strategy


def test_a_market_type_condition_alone_is_a_whole_entry() -> None:
    document = _document()
    document["entry"]["all_of"] = [{"market_type": "sideways", "relation": "is"}]
    assert PracticeStrategy.model_validate(document).uses_market_type


@pytest.mark.parametrize("condition", [
    {"market_type": "bullish", "relation": "is"},
    {"market_type": "rising", "relation": "above"},
    {"market_type": "rising"},
    {"market_type": "rising", "relation": "is", "level": "1"},
    {"left": {"market_type": "rising"}, "relation": "is", "right": {"level": "1"}},
    {"market_type": ["rising"], "relation": "is"},
])
def test_only_the_four_types_with_is_or_is_not(condition) -> None:
    document = _document()
    document["entry"]["all_of"].append(condition)
    with pytest.raises(ValidationError):
        PracticeStrategy.model_validate(document)


@pytest.mark.parametrize("pair", [
    [{"market_type": "rising", "relation": "is"}, {"market_type": "falling", "relation": "is"}],
    [{"market_type": "rising", "relation": "is"}, {"market_type": "rising", "relation": "is_not"}],
])
def test_conditions_that_can_never_hold_together_are_refused(pair) -> None:
    document = _document()
    document["entry"]["all_of"].extend(pair)
    with pytest.raises(ValidationError, match="one market type at a time"):
        PracticeStrategy.model_validate(document)


def test_exits_do_not_read_the_market_type() -> None:
    document = _document()
    document["exit"]["any_of"] = [{"market_type": "falling", "relation": "is"}]
    with pytest.raises(ValidationError):
        PracticeStrategy.model_validate(document)


def test_documents_without_the_condition_serialise_exactly_as_before() -> None:
    document = cross_strategy().canonical_document()
    assert document["entry"]["all_of"] == [{"left": {"candle": "close"}, "relation": "crosses_above",
                                            "right": {"level": "100"}}]
    assert "market_type" not in json.dumps(document)
