"""The scorecard's split from the real records (W13-A, C13; D300): day plans, the desk's records and the labels.

A synthetic paper account (W4-A's day-plan account: USD10,000, SOL near 100) makes three trades' worth of records the
way the installation makes them: a stance and a playbook stored by ``DeskStore`` (W12-A), market-type labels as the
hourly scan stores them (W11-A), day plans installed by ``DayPlans`` (which records the desk record), resting buys
filled by the paper broker and closed by their safety exits. The scorecard splits them by the strategy version of
the plan that bought, the label at the first buy and the stance recorded with the plan, and every list sums to the
totals. Older databases (before 0034, 0050 or 0052) still score, with the parts they can't name said as such.
Everything is synthetic (``synthetic_fixture``); no network, model, key or real order.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from tests.day_plan_action_support import fill_quote
from tests.day_plan_support import ROOT, day_plan_account, install, manage, plan, record_report
from tests.trading_desk_support import insert_label, playbook, stance

from trade_graph.application.scorecard import (
    MARKET_TYPES_AT_ENTRY,
    STANCES_AT_ENTRY,
    compute_scorecard,
    format_scorecard_text,
)
from trade_graph.application.trading_desk.store import DeskStore
from trade_graph.contracts.models import FillRecord
from trade_graph.contracts.practice_range import PracticeStrategy
from trade_graph.domain.clock import utc_iso
from trade_graph.kernel.money_limits import trading_day

D = Decimal
TREND, RANGE = "slow-trend-pullback@1", "range-reversion@1"
RANGE_STRATEGY = PracticeStrategy.model_validate_json(
    (ROOT / "strategies" / "practice" / "range-reversion.v1.json").read_text())


def _week(tmp_path):
    """Three entries: a won SOL trade (sideways, buy_dips, the trend version), a lost SOL trade (a stale label, no
    stance, the range version) and ADA still held, bought outside any day plan with no label."""
    account = day_plan_account(tmp_path)
    record_report(account, strategy=RANGE_STRATEGY)
    desk = DeskStore(account.database, account.clock)
    now = account.clock.now()
    label = insert_label(account.database, "SOL/USD", "sideways", now)
    revision = desk.record_playbook(account.portfolio, playbook(), wake_id="wake-1", task_id="task-1")
    (given,) = desk.record_stances(
        account.portfolio, [stance("SOL/USD", "buy_dips", "sideways", strategy_ref=TREND)], wake_id="wake-1",
        task_id="task-1", trading_day_start=trading_day(now, "00:00").start, shown={},
        playbook_revision_id=revision.revision_id)
    first = install(account, plan(), request_id="plan-1", task_id="task-1")
    account.dispatch()
    fill_quote(account)  # the resting buy at 99.5 fills: 5 SOL
    manage(account)
    account.clock.advance(60)
    account.quote("SOL/USD", "104.1", "104.3")  # the target sells them: a win
    account.clock.advance(3 * 3600)  # the label is now more than 2 hours old
    account.quote("SOL/USD", "99.9", "100.1")
    second = install(account, plan(strategy_ref=RANGE), request_id="plan-2")
    account.dispatch()
    fill_quote(account)
    manage(account)
    account.clock.advance(60)
    account.quote("SOL/USD", "96.9", "97.1")  # through the stop: a loss
    account.clock.advance(60)
    record = FillRecord(venue="paper", account_id="paper", trade_id="synthetic-ada-1", intent_id=None,
                        symbol="ADA/USD", side="buy", quantity="1000", price="0.5", fee_amount="2", fee_asset="USD",
                        liquidity="maker", filled_at_utc=account.clock.now())
    account.ledger.apply_fill(account.portfolio, record, base_asset="ADA", quote_asset="USD")
    account.clock.advance(60)
    for symbol, bid, ask in (("SOL/USD", "96.9", "97.1"), ("ADA/USD", "0.5099", "0.5101"),
                             ("BTC/USD", "49990", "50010")):
        account.quote(symbol, bid, ask)
    return account, {"label": label, "labelled_at": now, "revision": revision, "stance": given, "first": first,
                     "second": second}


def _card(account, **options):
    return compute_scorecard(account.database, portfolio_id=account.portfolio, end=account.clock.now(), **options)


def _non_empty(rows, key):
    return {row[key]: (row["trades"], row["wins"], row["losses"], row["open_positions"]) for row in rows
            if row["trades"] or row["open_positions"]}


def _sums(card) -> None:
    metrics, document = card.metrics, card.split.document()
    for name in ("by_strategy", "by_market_type", "by_stance", "by_choice"):
        rows = document[name]
        assert sum(row["trades"] for row in rows) == metrics.trades, name
        assert sum(row["wins"] for row in rows) == metrics.wins, name
        assert sum(row["losses"] for row in rows) == metrics.losses, name
        assert sum(row["open_positions"] for row in rows) == len(metrics.open_positions), name
        assert sum((D(row["result_after_fees"]) for row in rows), D("0")) == metrics.result, name
    assert card.split.outside_trades == D("0")


def test_each_trade_counts_by_its_plans_version_the_label_at_its_first_buy_and_the_recorded_stance(tmp_path):
    account, made = _week(tmp_path)
    card = _card(account)
    assert card.split.available and card.metrics.trades == 2 and len(card.metrics.open_positions) == 1
    document = card.document()["split"]
    assert document == card.split.document()
    assert _non_empty(document["by_strategy"], "strategy_ref") == {
        RANGE: (1, 0, 1, 0), TREND: (1, 1, 0, 0), "no_day_plan": (0, 0, 0, 1)}
    assert [row["strategy_ref"] for row in document["by_strategy"]] == [RANGE, TREND, "no_day_plan"]
    assert [row["market_type"] for row in document["by_market_type"]] == list(MARKET_TYPES_AT_ENTRY)
    assert _non_empty(document["by_market_type"], "market_type") == {
        "sideways": (1, 1, 0, 0), "stale": (1, 0, 1, 0), "missing": (0, 0, 0, 1)}
    assert [row["stance"] for row in document["by_stance"]] == list(STANCES_AT_ENTRY)
    assert _non_empty(document["by_stance"], "stance") == {"buy_dips": (1, 1, 0, 0), "not_recorded": (1, 0, 1, 1)}
    assert [(row["strategy_ref"], row["market_type"], row["stance"], row["trades"]) for row in document["by_choice"]] \
        == [(RANGE, "stale", "not_recorded", 1), (TREND, "sideways", "buy_dips", 1),
            ("no_day_plan", "missing", "not_recorded", 0)]
    _sums(card)
    won, lost, held = document["entries"]
    assert (won["kind"], won["plan_id"], won["label_id"], won["stance_id"], won["playbook_revision"]) == (
        "trade", made["first"].plan_id, made["label"], made["stance"].stance_id, 1)
    assert won["labelled_at_utc"] == utc_iso(made["labelled_at"])
    assert (lost["plan_id"], lost["market_type"], lost["label_id"]) == (made["second"].plan_id, "stale", made["label"])
    assert (held["kind"], held["symbol"], held["plan_id"], held["label_id"]) == ("open", "ADA/USD", None, None)
    # ADA: 1000 bought at 0.5 with a 2 USD fee, valued at 0.51 less the 0.8% selling fee: 510 - 4.08 - 500 - 2.
    assert held["result_after_fees"] == "3.92"


def test_the_owners_text_reads_the_split_in_plain_words(tmp_path):
    account, _ = _week(tmp_path)
    text = format_scorecard_text(_card(account))
    assert "By strategy version: range-reversion@1 1 trade (0 won, 1 lost)" in text
    assert "slow-trend-pullback@1 1 trade (1 won, 0 lost)" in text
    assert "By market type at entry: sideways 1 trade (1 won, 0 lost)" in text
    assert "stale (label older than 2 hours) 1 trade" in text and "missing (no label) 0 trades, 1 still held" in text
    assert "By the desk's stance: buy dips 1 trade" in text


def test_a_coin_held_at_the_start_of_the_period_counts_as_carried_in(tmp_path):
    account, _ = _week(tmp_path)
    fills = account.database.execute("SELECT created_at FROM fills ORDER BY rowid").fetchall()
    start = datetime.fromisoformat(fills[0]["created_at"]) + timedelta(seconds=5)  # SOL bought, not yet sold
    card = _card(account, start=start)
    document = card.split.document()
    assert card.carried_in == ("SOL",)
    assert _non_empty(document["by_strategy"], "strategy_ref")["carried_in"] == (1, 1, 0, 0)
    assert _non_empty(document["by_market_type"], "market_type")["carried_in"] == (1, 1, 0, 0)
    _sums(card)


def _drop(account, *tables: str) -> None:
    with account.database.immediate() as connection:
        for table in tables:
            connection.execute(f"DROP TABLE {table}")


DESK_TABLES = ("day_plan_desk_records", "desk_stances", "desk_playbook_revisions")
PLAN_TABLES = ("day_plan_action_orders", "day_plan_action_steps", "day_plan_actions", "day_plan_events",
               "day_plan_exits", "day_plan_entries", "day_plans")


@pytest.mark.parametrize("dropped, strategies, markets, stances", [
    # Before 0052: the plans still name their versions; no stance was recorded.
    (DESK_TABLES, {RANGE, TREND, "no_day_plan"}, {"sideways", "stale", "missing"}, {"not_recorded"}),
    # Before 0050 as well: no label was recorded either.
    (DESK_TABLES + ("market_type_labels",), {RANGE, TREND, "no_day_plan"}, {"missing"}, {"not_recorded"}),
    # Before 0034 as well: no day plans, so no strategy version.
    (DESK_TABLES + ("market_type_labels",) + PLAN_TABLES, {"no_day_plan"}, {"missing"}, {"not_recorded"}),
])
def test_an_older_database_still_scores_and_names_what_it_cannot_split_as_such(tmp_path, dropped, strategies,
                                                                                 markets, stances):
    account, _ = _week(tmp_path)
    before = _card(account).document()
    _drop(account, *dropped)
    card = _card(account)
    after = card.document()
    assert after["metrics"] == before["metrics"] and after["status"] == before["status"]
    document = after["split"]
    assert document["available"] is True
    assert set(_non_empty(document["by_strategy"], "strategy_ref")) == strategies
    assert set(_non_empty(document["by_market_type"], "market_type")) == markets
    assert set(_non_empty(document["by_stance"], "stance")) == stances
    _sums(card)


def test_an_edited_label_makes_the_split_unavailable_but_never_the_scorecard(tmp_path):
    account, _ = _week(tmp_path)
    text = json.dumps({"symbol": "SOL/USD", "market_type": "rising", "evidence": "synthetic_fixture"})
    second_buy = account.database.execute("SELECT created_at FROM fills ORDER BY rowid").fetchall()[2]["created_at"]
    at = utc_iso(datetime.fromisoformat(second_buy) - timedelta(minutes=1))  # the newest label at the second buy
    with account.database.immediate() as connection:  # a row whose hash no longer matches its document
        connection.execute(
            """INSERT INTO market_type_labels (label_id, symbol, as_of, market_type, reason, candle_close, lag_bars,
               interval_minutes, source, rules_ref, rules_sha256, scan_id, evidence, document_json, document_sha256,
               recorded_at) VALUES ('mt-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'SOL/USD', ?, 'rising', NULL, ?, 0, 60,
               'synthetic', 'market-type-rules@1', ?, NULL, 'synthetic_fixture', ?, ?, ?)""",
            (at, at, "0" * 64, text, hashlib.sha256(b"something else").hexdigest(), at))
    card = _card(account)
    assert card.metrics.trades == 2
    document = card.document()["split"]
    assert document["available"] is False and "StaleState" in document["reason"]
    assert document["by_strategy"] == [] and document["totals"] is None
    assert "split by strategy version" in format_scorecard_text(card)
