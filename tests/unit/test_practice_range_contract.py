"""The practice-range strategy grammar and study windows (T29): data only, menu indicators only, long only."""

from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trade_graph.contracts.practice_range import (
    REPORT_LABEL,
    PracticeStrategy,
    ReplayWindow,
    StudyPlan,
)

ROOT = Path(__file__).resolve().parents[2]


def strategy_document(**changes) -> dict:
    document = {
        "schema_version": 1,
        "strategy_id": "trend-pullback-test",
        "version": 1,
        "title": "Trend pullback for tests",
        "hypothesis": "A shallow pullback inside an uptrend tends to resume.",
        "interval_minutes": 15,
        "indicators": {
            "fast": {"indicator": "SMA", "parameters": {"timeperiod": 20}},
            "slow": {"indicator": "SMA", "parameters": {"timeperiod": 50}},
            "bands": {"indicator": "BBANDS", "parameters": {"timeperiod": 20}},
        },
        "entry": {
            "all_of": [
                {"left": {"indicator": "fast"}, "relation": "above", "right": {"indicator": "slow"}},
                {"left": {"candle": "close"}, "relation": "below", "right": {"indicator": "bands", "output": "middle"}},
            ],
            "order": {"kind": "limit", "price": {"candle": "close"}, "offset_fraction": "0.001",
                      "expires_after_bars": 4},
        },
        "exit": {
            "stop_loss_fraction": "0.02",
            "take_profit_fraction": "0.03",
            "any_of": [{"left": {"candle": "close"}, "relation": "crosses_below", "right": {"indicator": "slow"}}],
            "max_holding_bars": 96,
        },
        "sizing": {"equity_fraction": "0.2"},
    }
    document.update(changes)
    return document


def test_a_valid_strategy_is_data_with_a_stable_reference_and_content_hash() -> None:
    strategy = PracticeStrategy.model_validate(strategy_document())
    assert strategy.ref == "trend-pullback-test@1"
    assert strategy.exit.stop_loss_fraction == Decimal("0.02")
    reordered = json.loads(json.dumps(strategy_document(), sort_keys=True))
    assert PracticeStrategy.model_validate(reordered).content_sha256 == strategy.content_sha256
    changed = strategy_document(sizing={"equity_fraction": "0.1"})
    assert PracticeStrategy.model_validate(changed).content_sha256 != strategy.content_sha256
    assert strategy.indicator_uses()["bands"].indicator == "BBANDS"


@pytest.mark.parametrize("mutate", [
    lambda d: d["indicators"]["fast"].update(indicator="MY_FORMULA"),  # not on the menu
    lambda d: d["indicators"]["fast"].update(parameters={"timeperiod": 0}),  # out of the menu's bounds
    lambda d: d["indicators"]["fast"].update(parameters={"window": 5}),  # not a parameter of SMA
    lambda d: d["entry"]["all_of"][0]["left"].update(indicator="missing"),  # names no declared indicator
    lambda d: d["entry"]["all_of"][1]["right"].pop("output"),  # BBANDS has three outputs: one must be named
    lambda d: d["entry"]["all_of"][0]["left"].update(output="upper"),  # SMA has no such output
    lambda d: d["entry"]["all_of"].append({"left": {"level": "1"}, "relation": "above", "right": {"level": "2"}}),
    lambda d: d["entry"]["all_of"][0].update(relation="equals"),
    lambda d: d.update(interval_minutes=60),  # D119: 15-minute bars or finer
    lambda d: d["sizing"].update(equity_fraction="1.5"),  # no leverage (D127)
    lambda d: d["sizing"].update(equity_fraction=0.2),  # a binary float is never money
    lambda d: d["exit"].pop("stop_loss_fraction"),  # every position carries a protective exit (D117)
    lambda d: d["exit"].update(stop_loss_fraction="0.9"),
    lambda d: d["entry"]["order"].update(kind="limit", price=None),
    lambda d: d["entry"]["order"].update(kind="market"),  # a market order has no price or offset
    lambda d: d["indicators"].update(unused={"indicator": "RSI", "parameters": {}}),  # declared but never read
    lambda d: d.update(side="sell"),  # long only: there is no side to choose
    lambda d: d.update(code="import os"),  # no extra fields at all
    lambda d: d.update(strategy_id="Bad Name"),
    lambda d: d.update(version=0),
])
def test_anything_outside_the_grammar_is_refused(mutate) -> None:
    document = copy.deepcopy(strategy_document())
    mutate(document)
    with pytest.raises(ValidationError):
        PracticeStrategy.model_validate(document)


def test_a_market_entry_needs_no_price() -> None:
    document = strategy_document()
    document["entry"]["order"] = {"kind": "market", "expires_after_bars": 1}
    assert PracticeStrategy.model_validate(document).entry.order.kind == "market"


def test_windows_are_whole_utc_days_with_the_confirmation_strictly_later() -> None:
    plan = StudyPlan(study_id="study-1", strategy_id="trend-pullback-test",
                     tune=ReplayWindow(start=date(2026, 4, 1), end=date(2026, 6, 1)),
                     confirm=ReplayWindow(start=date(2026, 6, 1), end=date(2026, 7, 1)))
    assert plan.tune.days == 61 and plan.confirm.days == 30
    assert plan.replayed_days == 91 and plan.meets_three_month_minimum
    assert plan.tune.start_utc.isoformat() == "2026-04-01T00:00:00+00:00"
    short = StudyPlan(study_id="study-2", strategy_id="trend-pullback-test",
                      tune=ReplayWindow(start=date(2026, 5, 1), end=date(2026, 6, 1)),
                      confirm=ReplayWindow(start=date(2026, 6, 1), end=date(2026, 7, 1)))
    assert not short.meets_three_month_minimum
    with pytest.raises(ValidationError, match="later"):
        StudyPlan(study_id="study-3", strategy_id="trend-pullback-test",
                  tune=ReplayWindow(start=date(2026, 4, 1), end=date(2026, 6, 2)),
                  confirm=ReplayWindow(start=date(2026, 6, 1), end=date(2026, 7, 1)))
    with pytest.raises(ValidationError):
        ReplayWindow(start=date(2026, 6, 1), end=date(2026, 6, 1))


def test_the_report_label_says_what_a_report_is_not() -> None:
    assert REPORT_LABEL["kind"] == "evidence_for_trader_discretion"
    statement = REPORT_LABEL["statement"].lower()
    for words in ("not a pass mark", "not a claim", "not permission"):
        assert words in statement


@pytest.mark.parametrize("path", sorted((ROOT / "strategies" / "practice").glob("*.json")))
def test_packaged_example_strategies_follow_the_grammar(path: Path) -> None:
    strategy = PracticeStrategy.model_validate_json(path.read_text(encoding="utf-8"))
    assert path.name == f"{strategy.strategy_id}.v{strategy.version}.json"


def test_there_are_packaged_example_strategies() -> None:
    assert len(list((ROOT / "strategies" / "practice").glob("*.json"))) >= 2
