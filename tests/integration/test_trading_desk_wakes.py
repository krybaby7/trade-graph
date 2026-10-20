"""The Trading desk's wakes (W12-A, C12; D280, D295-D298): market types in, playbook and stances out.

A desk wake shows each of its coins' market type (shortlist, focus, held, planned), current, stale or missing, never a
guess; the shortlist's coins carry their label too. The desk's answer may revise its playbook and give stances; the
event engine stores them with the answer, and a day plan records the coin's market type and the playbook revision when
it is installed. Software never refuses a plan for not following the playbook or a market type (D280 item 5).

A scripted installation: the synthetic day-plan account (USD10,000, synthetic money limits, SOL near 100), the real
event engine and day plans, synthetic labels and scripted departments. No network, model, key, paid call or real order.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.day_plan_support import day_plan_account, plan
from tests.leader_news_support import ScriptedScan
from tests.trading_desk_support import SIDEWAYS_DIPS, insert_label, stance
from tests.wake_support import engine_for, run_all, run_one, scripted, wake_input

from trade_graph.application.trading_desk.store import DeskStore


@pytest.fixture
def desk(tmp_path):
    account = day_plan_account(tmp_path)
    scheduler, engine = engine_for(account, departments=("trader", "research"),
                                   scan=ScriptedScan("SOL/USD", "BTC/USD", "ADA/USD"))
    yield account, scheduler, engine
    account.database.close()


def _focus(task: dict) -> dict:
    return {"focus": {"coins": [{"symbol": "SOL/USD", "note": "Synthetic: range-bound."}],
                      "summary": "SOL today."}, "summary": "SOL today."}


def test_a_desk_wake_shows_each_coins_market_type_current_stale_or_missing(desk) -> None:
    account, scheduler, engine = desk
    now = account.clock.now()
    insert_label(account.database, "SOL/USD", "sideways", now - timedelta(minutes=20))
    insert_label(account.database, "BTC/USD", "falling", now - timedelta(hours=5))
    seen: dict[str, list] = {"research": [], "trader": []}
    run_all(scheduler, engine, {"research": scripted(_focus, seen=seen["research"]),
                                "trader": scripted({"summary": "Looked."}, seen=seen["trader"])},
            [account.portfolio])
    (focus_task,), (plan_task,) = seen["research"], seen["trader"]
    research_wake, desk_wake = wake_input(focus_task), wake_input(plan_task)
    assert desk_wake["purpose"] == "day_plan"
    shortlist = {coin["symbol"]: coin for coin in desk_wake["scan"]["shortlist"]}
    assert (shortlist["SOL/USD"]["market_type"], shortlist["SOL/USD"]["market_type_status"]) == ("sideways",
                                                                                                 "current")
    assert (shortlist["BTC/USD"]["market_type"], shortlist["BTC/USD"]["market_type_status"]) == (None, "stale")
    assert (shortlist["ADA/USD"]["market_type"], shortlist["ADA/USD"]["market_type_status"]) == (None, "missing")
    readings = {item["symbol"]: item for item in desk_wake["market_types"]}
    assert list(readings) == ["SOL/USD", "BTC/USD", "ADA/USD"]  # the focus coin first, then the shortlist
    assert readings["SOL/USD"]["seen_as"] == ["focus", "shortlist"]
    assert readings["BTC/USD"]["newest_label"] == "falling" and "not known" in readings["BTC/USD"]["sentence"]
    assert readings["ADA/USD"]["status"] == "missing"
    # Research's wake is as before: the labels are the desk's (W12-A); its shortlist carries none.
    assert "market_types" not in research_wake or research_wake["market_types"] == []
    assert all(coin.get("market_type") is None for coin in research_wake["scan"]["shortlist"])


def test_an_event_wake_shows_held_and_shortlist_coins_too(desk) -> None:
    account, scheduler, engine = desk
    now = account.clock.now()
    insert_label(account.database, "SOL/USD", "sideways", now - timedelta(minutes=20))
    run_all(scheduler, engine, {"research": scripted(_focus), "trader": scripted({"summary": "Looked."})},
            [account.portfolio])
    from tests.news_wake_support import hold_sol

    hold_sol(account)
    seen: list = []
    engine.request_wake(account.portfolio, "trader", "Synthetic: look again.", requested_by="research")
    run_all(scheduler, engine, {"trader": scripted({"summary": "Looked."}, seen=seen)}, [account.portfolio])
    readings = {item["symbol"]: item for item in wake_input(seen[-1])["market_types"]}
    # The paper buy's fill is a trigger of this wake too (order_filled names SOL/USD).
    assert readings["SOL/USD"]["seen_as"] == ["held", "focus", "trigger", "shortlist"]
    assert readings["SOL/USD"]["market_type"] == "sideways"
    assert {"BTC/USD", "ADA/USD"} <= set(readings)


def test_the_desks_playbook_and_stances_are_stored_with_the_label_its_wake_showed(desk) -> None:
    account, scheduler, engine = desk
    now = account.clock.now()
    insert_label(account.database, "BTC/USD", "falling", now - timedelta(minutes=10))
    insert_label(account.database, "SOL/USD", "sideways", now - timedelta(minutes=10))
    answer = {"summary": "Wait on BTC; buy SOL dips.", "playbook": SIDEWAYS_DIPS,
              "stances": [stance().model_dump(mode="json"),
                          stance("SOL/USD", "buy_dips", "sideways",
                                 strategy_ref="slow-trend-pullback@1").model_dump(mode="json")]}
    run_all(scheduler, engine, {"research": scripted(_focus), "trader": scripted(answer)}, [account.portfolio])
    (wake,) = engine.wakes(account.portfolio, department="trader")
    store = DeskStore(account.database, account.clock)
    playbook = store.playbook(account.portfolio)
    assert wake.outcome.playbook_revision_id == playbook.revision_id and playbook.revision == 1
    assert (playbook.wake_id, playbook.task_id, playbook.set_by) == (wake.wake_id, wake.task_id, "trader")
    stances = {item.symbol: item for item in store.stances(account.portfolio)}
    assert set(wake.outcome.stance_ids) == {item.stance_id for item in stances.values()}
    btc = stances["BTC/USD"]
    assert (btc.stance, btc.market_type_seen, btc.label_status, btc.label_market_type) == (
        "wait", "falling", "current", "falling")
    assert btc.playbook_revision_id == playbook.revision_id and btc.wake_id == wake.wake_id

    # A later answer without a playbook leaves it as it is; a new one follows the old one.
    engine.request_wake(account.portfolio, "trader", "Synthetic: again.", requested_by="research")
    revised = {**SIDEWAYS_DIPS, "why": "Synthetic: no change of mind, a new revision all the same."}
    run_all(scheduler, engine, {"trader": scripted({"summary": "Same."})}, [account.portfolio])
    assert store.playbook(account.portfolio) == playbook
    engine.request_wake(account.portfolio, "trader", "Synthetic: once more.", requested_by="research")
    run_all(scheduler, engine, {"trader": scripted({"summary": "Revised.", "playbook": revised})},
            [account.portfolio])
    newest = store.playbook(account.portfolio)
    assert (newest.revision, newest.previous_revision_id) == (2, playbook.revision_id)


def test_software_never_refuses_a_day_plan_for_not_following_the_playbook_or_the_market_type(desk) -> None:
    """D280 item 5: the playbook says wait in falling markets and SOL is falling, yet the desk's own SOL plan with a
    version its playbook does not name is installed; the plan records the market type and the playbook revision."""
    account, scheduler, engine = desk
    now = account.clock.now()
    label_id = insert_label(account.database, "SOL/USD", "falling", now - timedelta(minutes=15))
    against = {"summary": "Against my own playbook, on purpose.", "playbook": SIDEWAYS_DIPS,
               "stances": [stance("SOL/USD", "other", "falling", reason="A one-off: the dip looks overdone.")
                           .model_dump(mode="json")],
               "day_plans": [plan().model_dump(mode="json")]}
    run_all(scheduler, engine, {"research": scripted(_focus), "trader": scripted(against)}, [account.portfolio])
    (wake,) = engine.wakes(account.portfolio, department="trader")
    (applied,) = wake.outcome.plans
    assert applied.status == "installed", applied.reason
    record = DeskStore(account.database, account.clock).day_plan_record(applied.plan_id)
    assert (record.symbol, record.market_type, record.label_status, record.label_id) == (
        "SOL/USD", "falling", "current", label_id)
    assert record.market_type_in_force == "falling"
    assert (record.playbook_revision, record.playbook_revision_id) == (1, wake.outcome.playbook_revision_id)
    assert record.stance == "other" and record.stance_id in wake.outcome.stance_ids
    assert account.database.execute("SELECT COUNT(*) FROM day_plan_entries").fetchone()[0] == 1


def test_a_plan_without_label_playbook_or_stance_still_installs_and_records_that(desk) -> None:
    account, scheduler, engine = desk
    answer = {"summary": "A plan, nothing else.", "day_plans": [plan().model_dump(mode="json")]}
    run_all(scheduler, engine, {"research": scripted(_focus), "trader": scripted(answer)}, [account.portfolio])
    (wake,) = engine.wakes(account.portfolio, department="trader")
    (applied,) = wake.outcome.plans
    assert applied.status == "installed", applied.reason
    record = DeskStore(account.database, account.clock).day_plan_record(applied.plan_id)
    assert (record.label_status, record.market_type, record.playbook_revision_id, record.stance) == (
        "missing", None, None, None)
    assert wake.outcome.playbook_revision_id is None and wake.outcome.stance_ids == ()


def test_only_the_desk_keeps_a_playbook_and_gives_stances(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    scheduler, engine = engine_for(account, departments=("research",), daily_cycle=False)
    engine.request_wake(account.portfolio, "research", "Synthetic.", requested_by="leader")
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"research": scripted({"summary": "Not mine.", "playbook": SIDEWAYS_DIPS,
                                                      "stances": [stance().model_dump(mode="json")]})})
    (wake,) = engine.wakes(account.portfolio)
    assert wake.outcome.playbook_revision_id is None and wake.outcome.stance_ids == ()
    assert "Trading desk" in wake.outcome.refused
    store = DeskStore(account.database, account.clock)
    assert store.playbook(account.portfolio) is None and store.stances(account.portfolio) == []
