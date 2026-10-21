"""Learning reads the scorecard's split (W13-A, C13; D301): which choice earned or lost in which market.

A Learning turn on the subscription path (the scripted Claude subscription CLI of ``test_subscription_departments``)
gets the paper scorecard since the account opened under the context key ``scorecard``: the totals and, by strategy
version, market type at entry, stance and their combinations, the rows that hold a trade or a position still held,
with the newest trades. The key is in Learning's protected context allowlist; an account that can't be scored says
why instead. Everything is synthetic (``synthetic_fixture``): no network, real model, key or order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.integration.test_subscription_departments import flow
from tests.trading_desk_support import insert_label

from trade_graph.application.scorecard import (
    LEARNING_CHOICES_SHOWN,
    LEARNING_TRADES_SHOWN,
    compute_scorecard,
    learning_view,
    scorecard_for_learning,
)
from trade_graph.application.subscription_runtime import ROLE_CONTEXT_KEYS
from trade_graph.contracts.models import FillRecord

D = Decimal
SCORED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _fill(setup, number: int, symbol: str, side: str, quantity: str, price: str) -> None:
    base, quote = symbol.split("/")
    fee = D(quantity) * D(price) * D("0.004")
    record = FillRecord(venue="paper", account_id="paper", trade_id=f"synthetic-learning-{number}", intent_id=None,
                        symbol=symbol, side=side, quantity=quantity, price=price, fee_amount=str(fee),
                        fee_asset=quote, liquidity="maker", filled_at_utc=setup.clock.now())
    setup.office.execution.ledger.apply_fill(setup.pid, record, base_asset=base, quote_asset=quote)


def _mark(setup, asset: str, price: str) -> None:
    setup.office.execution.ledger.observe_mark(setup.pid, asset, D(price), "USD", source="synthetic_fixture")


def _scored_days(setup) -> None:
    """The flow's clock starts in January; the scorecard's fee table (BTC comparison) is in force from October."""
    setup.clock.advance((SCORED - setup.clock.now()).total_seconds())


def _trades(setup) -> None:
    """A won SOL trade bought while SOL was labelled sideways and a lost ETH trade with no label (synthetic)."""
    _scored_days(setup)
    ledger = setup.office.execution.ledger
    ledger.deposit(setup.pid, "USD", D("10000"), "synthetic-opening")
    for asset, price in (("SOL", "100"), ("ETH", "3000"), ("BTC", "60000")):
        _mark(setup, asset, price)
    insert_label(setup.db, "SOL/USD", "sideways", setup.clock.now())
    setup.clock.advance(60)
    _fill(setup, 1, "SOL/USD", "buy", "10", "100")
    setup.clock.advance(60)
    _mark(setup, "SOL", "110")
    _fill(setup, 2, "SOL/USD", "sell", "10", "110")
    setup.clock.advance(60)
    _fill(setup, 3, "ETH/USD", "buy", "1", "3000")
    setup.clock.advance(60)
    _mark(setup, "ETH", "2900")
    _fill(setup, 4, "ETH/USD", "sell", "1", "2900")
    setup.clock.advance(60)
    for asset, price in (("SOL", "110"), ("ETH", "2900"), ("BTC", "60000")):
        _mark(setup, asset, price)


def test_a_learning_turn_reads_which_choice_earned_or_lost_in_which_market(tmp_path):
    assert "scorecard" in ROLE_CONTEXT_KEYS["learning"]
    setup, _protected, cli = flow(tmp_path)
    _trades(setup)
    identity = setup.add("learning")
    assert setup.run("learning") == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    scorecard = cli.requests[-1].context["scorecard"]
    assert scorecard["available"] is True and scorecard["split_available"] is True
    assert scorecard["totals"]["trades"] == 2 and scorecard["totals"]["wins"] == 1
    assert {row["market_type"]: (row["trades"], row["wins"], row["losses"]) for row in scorecard["by_market_type"]} \
        == {"sideways": (1, 1, 0), "missing": (1, 0, 1)}  # only rows with a trade or a position still held
    assert [row["strategy_ref"] for row in scorecard["by_strategy"]] == ["no_day_plan"]
    assert [(row["strategy_ref"], row["market_type"], row["stance"]) for row in scorecard["by_choice"]] == [
        ("no_day_plan", "sideways", "not_recorded"), ("no_day_plan", "missing", "not_recorded")]
    assert [item["symbol"] for item in scorecard["recent_trades"]] == ["SOL/USD", "ETH/USD"]
    # 10 x (110 - 100) - fees 4 and 4.4 = 91.6; 2900 - 3000 - fees 12 and 11.6 = -123.6.
    assert [item["result_after_fees"] for item in scorecard["recent_trades"]] == ["91.6", "-123.6"]
    assert sum(D(row["result_after_fees"]) for row in scorecard["by_stance"]) == D(
        scorecard["totals"]["result_after_fees"]) == D("-32")
    assert "not a pass mark" in scorecard["note"]
    json.dumps(scorecard)  # plain data, as the subscription context requires


def test_learning_is_told_why_when_the_account_cannot_be_scored(tmp_path):
    setup, _protected, cli = flow(tmp_path)  # no opening deposit yet
    identity = setup.add("learning")
    assert setup.run("learning") == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    scorecard = cli.requests[-1].context["scorecard"]
    assert scorecard["available"] is False and "opening deposit" in scorecard["reason"]


def test_a_long_history_is_shown_within_its_bounds_and_counted(tmp_path):
    setup, _protected, _cli = flow(tmp_path)
    _scored_days(setup)
    ledger = setup.office.execution.ledger
    ledger.deposit(setup.pid, "USD", D("10000"), "synthetic-opening")
    _mark(setup, "BTC", "60000")
    for number in range(LEARNING_TRADES_SHOWN + 5):
        setup.clock.advance(60)
        _mark(setup, "SOL", "100")
        insert_label(setup.db, "SOL/USD", ("rising", "sideways", "falling")[number % 3], setup.clock.now())
        _fill(setup, 2 * number, "SOL/USD", "buy", "1", "100")
        setup.clock.advance(60)
        _fill(setup, 2 * number + 1, "SOL/USD", "sell", "1", "101")
    setup.clock.advance(60)
    _mark(setup, "SOL", "101")
    _mark(setup, "BTC", "60000")
    scorecard = scorecard_for_learning(setup.db, setup.pid, end=setup.clock.now() + timedelta(seconds=1))
    assert scorecard["totals"]["trades"] == LEARNING_TRADES_SHOWN + 5
    assert len(scorecard["recent_trades"]) == LEARNING_TRADES_SHOWN
    assert scorecard["recent_trades_omitted"] == 5
    assert len(scorecard["by_choice"]) <= LEARNING_CHOICES_SHOWN and scorecard["by_choice_omitted"] == 0
    end = setup.clock.now() + timedelta(seconds=1)
    assert scorecard == learning_view(compute_scorecard(setup.db, portfolio_id=setup.pid, end=end))
