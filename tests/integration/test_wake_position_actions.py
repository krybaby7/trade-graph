"""The Trader's position actions through the event engine (W8-B, C4).

A woken Trader sees its day-plan positions with their exits, answers with ``position_actions`` (close a position,
move its safety exit) and the engine applies them under the execution lock after the answer is saved, before the
turn's day plans, each idempotent by its request id. Only the Trader acts; an obsolete wake acts on nothing; a
refusal is recorded with its reason. Synthetic account and scripted departments; no network, model, key or order.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.day_plan_action_support import fill_quote, live_exits, market_sells, position_action
from tests.day_plan_support import entry, install
from tests.wake_support import engine_account, plan, run_one, scripted, tasks, wake_input

from trade_graph.application.day_plan_actions import DayPlanActions
from trade_graph.application.events.engine import INSTRUCTION
from trade_graph.application.trader_workflow import WAKE_INSTRUCTION

D = Decimal


def _filled_and_woken(tmp_path, departments=frozenset({"trader"})):
    account, scheduler, engine = engine_account(tmp_path, departments=departments, daily_cycle=False)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", "104"), entry("3", "98", "95.5")]))
    account.dispatch()
    fill_quote(account)  # the 99.5 entry fills and its exit is sent; the 98 entry keeps resting
    assert engine.step([account.portfolio]).created == 1
    return account, scheduler, engine, installed


def _answer(*actions, **extra) -> dict:
    return {"position_actions": [action.model_dump(mode="json") for action in actions], **extra}


def test_the_woken_trader_sees_its_positions_with_their_exits(tmp_path) -> None:
    account, scheduler, engine, _installed = _filled_and_woken(tmp_path)
    seen: list = []
    run_one(scheduler, engine, {"trader": scripted(seen=seen)})
    (position,) = wake_input(seen[0])["positions"]
    assert position == {"symbol": "SOL/USD", "held": "5", "protected": "5", "unprotected": "0",
                        "exits": [{"stop_price": "97", "take_profit_price": "104", "quantity": "5"}],
                        "resting_entries": 1, "last_action": None}
    assert "position_actions" in WAKE_INSTRUCTION and "position_actions" in INSTRUCTION
    assert len(INSTRUCTION) <= 1000


def test_a_close_in_the_wake_answer_is_applied_and_recorded_with_the_wake(tmp_path) -> None:
    account, scheduler, engine, installed = _filled_and_woken(tmp_path)
    run_one(scheduler, engine, {"trader": scripted(_answer(position_action("close")))})
    (wake,) = engine.wakes(account.portfolio)
    assert wake.status == "completed"
    (applied,) = wake.outcome.actions
    assert (applied.symbol, applied.action, applied.status) == ("SOL/USD", "close", "done")
    assert applied.request_id == f"wake:{wake.wake_id}:action:SOL/USD" and applied.action_id.startswith("da-")
    record = DayPlanActions(account.execution).get(applied.action_id)
    assert record.task_id == wake.task_id
    assert account.state(installed.entries[1].intent_id) == "CANCELLED"  # the resting buy went with the close
    assert live_exits(account) == [] and len(market_sells(account)) == 1
    # The Trader's next wake shows how its action ended.
    fill_quote(account, bid="99.6", ask="99.7", volume=None)  # the sell fills: the order fill wakes the Trader
    assert engine.step([account.portfolio]).created == 1
    seen: list = []
    run_one(scheduler, engine, {"trader": scripted(seen=seen)})
    assert wake_input(seen[0])["positions"] == []  # nothing is left to protect or close


def test_actions_apply_before_the_turns_day_plans(tmp_path) -> None:
    account, scheduler, engine, installed = _filled_and_woken(tmp_path)
    new_plan = plan(entries=[entry("2", "99", "97.5")], alarms=[])
    run_one(scheduler, engine, {"trader": scripted(_answer(
        position_action("move_exit", stop_price="98.5", take_profit_price="105"),
        day_plans=[new_plan.model_dump(mode="json")]))})
    (wake,) = engine.wakes(account.portfolio)
    assert [(item.action, item.status) for item in wake.outcome.actions] == [("move_exit", "done")]
    assert [item.status for item in wake.outcome.plans] == ["installed"]
    assert live_exits(account) == [(D("98.5"), D("105"), D("5"))]
    # The new plan replaced the old one (its resting buy cancelled) and its own entry rests untouched.
    new_entry = account.database.execute(
        "SELECT intent_id FROM day_plan_entries WHERE plan_id = ?", (wake.outcome.plans[0].plan_id,)).fetchone()[0]
    assert account.state(installed.entries[1].intent_id) == "CANCELLED"
    assert account.state(new_entry) == "SUBMISSION_PENDING"


def test_a_refused_action_is_recorded_with_its_reason_and_the_old_exit_stays(tmp_path) -> None:
    account, scheduler, engine, _installed = _filled_and_woken(tmp_path)
    run_one(scheduler, engine, {"trader": scripted(_answer(
        position_action("move_exit", stop_price="100", take_profit_price="106")))})
    (wake,) = engine.wakes(account.portfolio)
    (applied,) = wake.outcome.actions
    assert applied.status == "refused" and "below the bid" in applied.reason and applied.error_code == "validation"
    assert live_exits(account) == [(D("97"), D("104"), D("5"))]


def test_only_the_trader_acts_on_positions(tmp_path) -> None:
    account, scheduler, engine, _installed = _filled_and_woken(tmp_path, departments={"trader", "leader"})
    engine.request_wake(account.portfolio, "leader", "Synthetic: look at SOL.", requested_by="trader")
    run_one(scheduler, engine, {"trader": scripted({})})
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"leader": scripted(_answer(position_action("close")))})
    leader = engine.wakes(account.portfolio, department="leader")[-1]
    assert leader.status == "completed" and leader.outcome.actions == ()
    assert "only the Trader" in leader.outcome.refused
    assert live_exits(account) == [(D("97"), D("104"), D("5"))] and market_sells(account) == []


def test_an_answer_that_arrives_after_its_trading_day_acts_on_nothing(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    account.clock.advance(timedelta(hours=15, minutes=59, seconds=44).total_seconds())  # 23:59:44 that day
    account.quote("SOL/USD", "99.9", "100.1")
    install(account)
    account.dispatch()
    fill_quote(account)  # 23:59:45: filled, its exit sent
    assert engine.step([account.portfolio]).created == 1

    def late(task):
        account.clock.advance(20)  # the answer arrives after midnight (inside the task's 30-second lease)
        return {"position_actions": [position_action("close").model_dump(mode="json")]}

    run_one(scheduler, engine, {"trader": scripted(late)})
    (wake,) = engine.wakes(account.portfolio)
    assert wake.status == "completed"
    (applied,) = wake.outcome.actions
    assert applied.status == "obsolete" and applied.action_id is None
    assert market_sells(account) == [] and live_exits(account) == [(D("97"), D("104"), D("5"))]


def test_a_crash_while_applying_is_finished_once_at_the_next_step(tmp_path, monkeypatch) -> None:
    account, scheduler, engine, _installed = _filled_and_woken(tmp_path)
    calls = {"n": 0}
    apply = DayPlanActions.apply

    async def stops_once(self, *args, **options):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic: the process stopped while applying")
        return await apply(self, *args, **options)

    monkeypatch.setattr(DayPlanActions, "apply", stops_once)
    with pytest.raises(RuntimeError, match="stopped while applying"):
        run_one(scheduler, engine, {"trader": scripted(_answer(position_action("close")))})
    (wake,) = engine.wakes(account.portfolio)
    assert wake.status == "applying" and market_sells(account) == []
    report = engine.step([account.portfolio])
    assert report.closed == 1
    (wake,) = engine.wakes(account.portfolio)
    assert wake.status == "completed" and [item.status for item in wake.outcome.actions] == ["done"]
    assert len(market_sells(account)) == 1
    assert len(tasks(account, "trader")) == 1
