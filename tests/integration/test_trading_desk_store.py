"""The Trading desk's records and reads (W12-A, C12; D295-D297, D299).

Playbook revisions are stored append-only with who, when, why and the revision they replaced, and read at any past
moment like lessons; stances keep the label software showed; a coin's market type is shown as current, stale or
missing, never as a guess. The reads work on a read-only connection and give nothing (not an error) on a database
older than migration 0052. Synthetic records only; no network, model, key, paid call or order.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from tests.day_plan_support import day_plan_account
from tests.trading_desk_support import insert_label, playbook, stance

from trade_graph.application.trading_desk.market_types import coin_market_types
from trade_graph.application.trading_desk.store import (
    DeskStore,
    read_day_plan_desk_records,
    read_latest_stances,
    read_playbook,
    read_playbook_revisions,
    read_stances,
)
from trade_graph.domain.errors import ValidationFailure


@pytest.fixture
def account(tmp_path):
    account = day_plan_account(tmp_path)
    yield account
    account.database.close()


def test_playbook_revisions_are_kept_with_who_when_why_and_the_one_they_replaced(account) -> None:
    store = DeskStore(account.database, account.clock)
    before = account.clock.now()
    assert store.playbook(account.portfolio) is None
    account.clock.advance(60)
    first = store.record_playbook(account.portfolio, playbook(), wake_id="w-1", task_id="t-1")
    assert (first.revision, first.previous_revision_id, first.set_by) == (1, None, "trader")
    assert (first.wake_id, first.task_id) == ("w-1", "t-1") and first.why.startswith("Fees decide")
    assert first.choice_for("falling").stance == "wait" and first.choice_for("rising") is None
    account.clock.advance(3600)
    revised = playbook(why="The dip version is confirmed now.", choices=[
        {"market_type": "sideways", "stance": "buy_dips", "strategy_refs": ["dip-rebound@1"],
         "reason": "Confirmed on untouched days.", "report_ids": ["pr-9"]}])
    second = store.record_playbook(account.portfolio, revised, wake_id="w-2", task_id="t-2")
    assert (second.revision, second.previous_revision_id) == (2, first.revision_id)
    # Read at any past moment, like lessons: before the first, between the two, after the second.
    assert store.playbook(account.portfolio, as_of=before) is None
    assert store.playbook(account.portfolio, as_of=first.created_at + timedelta(minutes=30)) == first
    assert store.playbook(account.portfolio) == second
    with account.database.connect() as connection:
        assert [item.revision for item in read_playbook_revisions(connection, account.portfolio)] == [1, 2]
        assert read_playbook_revisions(connection, account.portfolio, since=second.created_at) == [second]
        assert read_playbook(connection, "another-portfolio") is None


def test_stances_keep_the_label_the_wake_showed_and_the_newest_per_coin_is_read(account) -> None:
    store = DeskStore(account.database, account.clock)
    now = account.clock.now()
    insert_label(account.database, "BTC/USD", "falling", now - timedelta(minutes=30))
    with account.database.connect() as connection:
        shown = {item.symbol: item for item in coin_market_types(
            connection, {"BTC/USD": ("shortlist",), "SOL/USD": ("focus",)}, now)}
    revision = store.record_playbook(account.portfolio, playbook(), wake_id="w-1", task_id="t-1")
    recorded = store.record_stances(
        account.portfolio, (stance(), stance("SOL/USD", "buy_dips", "none", strategy_ref="slow-trend-pullback@1",
                                             news_refs=["nh-1", "ldr-2"])),
        wake_id="w-1", task_id="t-1", trading_day_start=now.replace(hour=0), shown=shown,
        playbook_revision_id=revision.revision_id)
    btc, sol = recorded
    assert (btc.stance, btc.market_type_seen, btc.label_status, btc.label_market_type) == (
        "wait", "falling", "current", "falling")
    assert btc.label_id == shown["BTC/USD"].label_id and btc.playbook_revision_id == revision.revision_id
    assert (sol.label_status, sol.label_market_type, sol.label_id) == ("missing", None, None)
    assert sol.news_refs == ("nh-1", "ldr-2") and sol.strategy_ref == "slow-trend-pullback@1"
    account.clock.advance(600)
    store.record_stances(account.portfolio, (stance("SOL/USD", "wait", "none"),), wake_id="w-2", task_id="t-2",
                         trading_day_start=now.replace(hour=0), shown=shown, playbook_revision_id=None)
    with account.database.connect() as connection:
        latest = read_latest_stances(connection, account.portfolio)
        assert latest["SOL/USD"].stance == "wait" and latest["BTC/USD"].stance == "wait"
        assert read_latest_stances(connection, account.portfolio, as_of=now)["SOL/USD"].stance == "buy_dips"
        assert [item.symbol for item in read_stances(connection, account.portfolio, symbols=["SOL/USD"])] == [
            "SOL/USD", "SOL/USD"]
        assert [item.wake_id for item in read_stances(connection, account.portfolio, wake_id="w-1")] == ["w-1"] * 2
    with pytest.raises(ValidationFailure, match="one stance per coin"):
        store.record_stances(account.portfolio, (stance(), stance()), wake_id="w-3", task_id=None,
                             trading_day_start=now.replace(hour=0), shown=shown, playbook_revision_id=None)


def test_a_coins_market_type_is_current_stale_or_missing_never_a_guess(account) -> None:
    now = account.clock.now()
    insert_label(account.database, "SOL/USD", "sideways", now - timedelta(minutes=50))
    insert_label(account.database, "BTC/USD", "falling", now - timedelta(hours=5))
    insert_label(account.database, "ETH/USD", "unclear", now - timedelta(minutes=10), reason="gap")
    insert_label(account.database, "SOL/USD", "rising", now + timedelta(minutes=5))  # later: never read at ``now``
    with account.database.connect() as connection:
        readings = {item.symbol: item for item in coin_market_types(connection, {
            "SOL/USD": ("held", "focus"), "BTC/USD": ("shortlist",), "ETH/USD": ("trigger",),
            "ADA/USD": ("shortlist",)}, now)}
    sol, btc, eth, ada = (readings[name] for name in ("SOL/USD", "BTC/USD", "ETH/USD", "ADA/USD"))
    assert (sol.status, sol.market_type, sol.age_minutes, sol.seen_as) == ("current", "sideways", 50,
                                                                           ("held", "focus"))
    assert (btc.status, btc.market_type, btc.newest_label, btc.age_minutes) == ("stale", None, "falling", 300)
    assert "not known" in btc.sentence and "5 hours" in btc.sentence
    assert (eth.status, eth.market_type, eth.reason) == ("current", "unclear", "gap")
    assert (ada.status, ada.market_type, ada.newest_label, ada.label_id) == ("missing", None, None, None)
    assert "No market-type label" in ada.sentence
    assert list(readings) == ["SOL/USD", "ETH/USD", "BTC/USD", "ADA/USD"]  # held first, then triggers, shortlist


def test_reads_work_read_only_and_on_a_database_before_0052(account, tmp_path) -> None:
    store = DeskStore(account.database, account.clock)
    store.record_playbook(account.portfolio, playbook(), wake_id="w-1", task_id="t-1")
    path = account.database.path
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    readonly.row_factory = sqlite3.Row
    try:
        assert read_playbook(readonly, account.portfolio).revision == 1
        assert read_day_plan_desk_records(readonly, portfolio_id=account.portfolio) == {}
    finally:
        readonly.close()
    older = sqlite3.connect(tmp_path / "older.sqlite")
    older.row_factory = sqlite3.Row
    try:
        assert read_playbook(older, account.portfolio) is None
        assert read_playbook_revisions(older, account.portfolio) == []
        assert read_stances(older, account.portfolio) == []
        assert read_latest_stances(older, account.portfolio) == {}
        assert read_day_plan_desk_records(older) == {}
        assert coin_market_types(older, {"SOL/USD": ("focus",)}, account.clock.now())[0].status == "missing"
    finally:
        older.close()
