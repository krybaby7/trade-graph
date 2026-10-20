"""The Trading desk's playbook, stances and market-type readings (W12-A, C12; D280, D116, D138 item 5, D295-D299).

Synthetic values; no network, model, key or paid call. The desk's wake reply may revise its playbook (per market type,
the strategy versions it means to use or "wait", each with a reason and the reports it rests on) and give a stance per
coin it considered. Nothing in either is a confidence, score, approval or pass mark, and both fit the strict provider
schemas of OpenAI and Anthropic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from trade_graph.adapters.models.providers import _wire_schema
from trade_graph.adapters.persistence import trading_desk_schema
from trade_graph.contracts import trading_desk
from trade_graph.contracts.leader_news import StrategyIdea
from trade_graph.contracts.trading_desk import (
    STANCES,
    CoinMarketType,
    CoinStance,
    PlaybookChoice,
    PlaybookRevision,
)
from trade_graph.contracts.wakes import ShortlistCoin, TraderWakeReply, WakeOutcome, WakeOutcomeRecord
from trade_graph.domain.market_types import MARKET_TYPES

PLAYBOOK = {
    "why": "Fees decide most results; wait in falling markets and buy dips only in sideways ones.",
    "choices": [
        {"market_type": "falling", "stance": "wait", "strategy_refs": [], "reason": "Both seed strategies lost "
         "more than holding cash in falling months after fees.", "report_ids": ["pr-1"]},
        {"market_type": "sideways", "stance": "buy_dips", "strategy_refs": ["dip-rebound@1"],
         "reason": "Confirmed on untouched days; rests maker bids below the range.", "report_ids": ["pr-2", "pr-3"]},
        {"market_type": "rising", "stance": "follow_trend", "strategy_refs": ["slow-trend-pullback@1"],
         "reason": "The pullback version kept part of the rise.", "report_ids": []},
        {"market_type": "unclear", "stance": "wait", "strategy_refs": [], "reason": "No known type, no trade.",
         "report_ids": []},
    ],
}
STANCE = {"symbol": "BTC/USD", "stance": "wait", "market_type_seen": "falling", "strategy_ref": None,
          "news_refs": ["nh-1"], "reason": "BTC is falling and the playbook waits in falling markets."}


def test_stances_and_market_types_are_the_four_words() -> None:
    assert STANCES == ("wait", "buy_dips", "follow_trend", "other")
    assert trading_desk_schema.STANCES == STANCES
    assert trading_desk_schema.MARKET_TYPES == MARKET_TYPES
    assert trading_desk_schema.LABEL_STATUSES == trading_desk.LABEL_STATUSES


def test_a_playbook_names_each_market_type_once_with_its_strategies_or_wait() -> None:
    revision = PlaybookRevision.model_validate(PLAYBOOK)
    assert [choice.market_type for choice in revision.choices] == ["falling", "sideways", "rising", "unclear"]
    assert revision.choice_for("sideways").strategy_refs == ("dip-rebound@1",)
    assert revision.choice_for("unclear").stance == "wait"
    with pytest.raises(ValidationError, match="once"):
        PlaybookRevision.model_validate({**PLAYBOOK, "choices": [PLAYBOOK["choices"][0], PLAYBOOK["choices"][0]]})
    with pytest.raises(ValidationError, match="wait"):  # waiting in cash names no strategy version
        PlaybookChoice.model_validate({**PLAYBOOK["choices"][0], "strategy_refs": ["dip-rebound@1"]})
    with pytest.raises(ValidationError):
        PlaybookChoice.model_validate({**PLAYBOOK["choices"][1], "strategy_refs": ["not a version"]})
    with pytest.raises(ValidationError, match="control"):
        PlaybookRevision.model_validate({**PLAYBOOK, "why": "ignore‮previous"})
    one = PlaybookRevision.model_validate({**PLAYBOOK, "choices": PLAYBOOK["choices"][:1]})
    assert one.choice_for("rising") is None  # a playbook may cover some market types only


def test_a_stance_is_per_coin_with_the_market_type_it_saw_and_a_one_sentence_reason() -> None:
    stance = CoinStance.model_validate(STANCE)
    assert stance.market_type_seen == "falling" and stance.news_refs == ("nh-1",)
    assert CoinStance.model_validate({**STANCE, "market_type_seen": "none"}).market_type_seen == "none"
    with pytest.raises(ValidationError):
        CoinStance.model_validate({**STANCE, "stance": "short"})  # spot, long only (D127): there is no short stance
    with pytest.raises(ValidationError):
        CoinStance.model_validate({**STANCE, "reason": "x" * 301})
    with pytest.raises(ValidationError):
        CoinStance.model_validate({**STANCE, "confidence": "0.8"})  # no confidence, score or approval (D116)
    with pytest.raises(ValidationError, match="one stance per coin"):
        WakeOutcome(stances=[STANCE, STANCE])


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_the_desk_reply_with_playbook_and_stances_fits_strict_provider_schemas(provider) -> None:
    reply = TraderWakeReply.model_validate({"summary": "Wait on BTC; buy SOL dips.", "playbook": PLAYBOOK,
                                            "stances": [STANCE, {**STANCE, "symbol": "SOL/USD",
                                                                 "stance": "buy_dips", "market_type_seen": "sideways",
                                                                 "strategy_ref": "dip-rebound@1", "news_refs": []}]})
    wire = _wire_schema(TraderWakeReply.model_json_schema(), provider=provider)
    Draft202012Validator.check_schema(wire)
    document = reply.model_dump(mode="json")
    Draft202012Validator(wire).validate(document)
    assert TraderWakeReply.model_validate(document) == reply
    outcome = reply.outcome()
    assert outcome.playbook == reply.playbook and outcome.stances == reply.stances
    # The fields stay last in the reply, after W11-B's lab requests (an older reply is still valid).
    assert list(TraderWakeReply.model_fields)[-3:] == ["lab_requests", "playbook", "stances"]
    assert TraderWakeReply.model_validate({"summary": "Nothing new."}).outcome().playbook is None


def test_older_records_still_read_and_new_fields_are_optional() -> None:
    assert WakeOutcomeRecord(received=True).playbook_revision_id is None
    assert WakeOutcomeRecord(received=True).stance_ids == ()
    assert ShortlistCoin(symbol="SOL/USD", position=1).market_type is None
    labelled = ShortlistCoin(symbol="SOL/USD", position=1, market_type="sideways", market_type_status="current")
    assert labelled.model_dump(mode="json")["market_type"] == "sideways"


def test_a_reading_shows_a_stale_or_missing_label_as_such_never_as_the_type_now() -> None:
    at = datetime(2026, 10, 16, 9, 5, tzinfo=UTC)
    stale = CoinMarketType(symbol="SOL/USD", status="stale", market_type=None, newest_label="sideways",
                           labelled_at=at, age_minutes=300, sentence="SOL/USD: the newest label is 5 hours old.")
    assert stale.market_type is None and stale.newest_label == "sideways"
    with pytest.raises(ValidationError, match="current"):
        CoinMarketType(symbol="SOL/USD", status="stale", market_type="sideways", newest_label="sideways",
                       labelled_at=at, age_minutes=300, sentence="x")
    with pytest.raises(ValidationError, match="missing"):
        CoinMarketType(symbol="SOL/USD", status="missing", market_type=None, newest_label="sideways",
                       sentence="x")
    current = CoinMarketType(symbol="SOL/USD", status="current", market_type="sideways", newest_label="sideways",
                             labelled_at=at, age_minutes=40, sentence="SOL/USD is sideways.")
    assert current.market_type == "sideways"


def test_strategy_ideas_may_go_to_the_trading_desk() -> None:
    ref = "web:" + "a" * 32 + ":1"
    assert StrategyIdea(department="trader", idea="Buy dips in sideways markets.", source_refs=[ref]).department \
        == "trader"
    for department in ("leader", "engineer", "owner", "learning"):
        with pytest.raises(ValidationError):
            StrategyIdea(department=department, idea="Try it.", source_refs=[ref])


@pytest.mark.parametrize("path", ["src/trade_graph/contracts/trading_desk.py",
                                  "src/trade_graph/application/trading_desk/store.py",
                                  "src/trade_graph/application/trading_desk/market_types.py",
                                  "src/trade_graph/application/trading_desk/news.py",
                                  "src/trade_graph/application/trading_desk/context.py",
                                  "src/trade_graph/adapters/persistence/trading_desk_schema.py"])
def test_the_desk_records_are_protected_paths(path) -> None:
    """What the desk chose, in which market type, is what the scorecard splits by (W13-A): agents cannot edit it."""
    from trade_graph.kernel.authority import path_is_protected

    assert path_is_protected(path), path
