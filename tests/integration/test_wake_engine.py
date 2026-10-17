"""The wake dispatcher (T35): fills, exits, alarms, feed notices, wake requests and check-ins wake departments;
coalescing batches and never drops; nothing caps wakes; a restart neither loses nor repeats a wake; an obsolete wake
never becomes a trade. Synthetic account and scripted departments; no network, model, key or real order.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.alarm_support import quote_request
from tests.day_plan_support import entry, install
from tests.wake_support import engine_account, engine_for, plan, run_one, scripted, tasks, wake_input

from trade_graph.application.alarms.book import AlarmBook, FeedSeries
from trade_graph.application.alarms.queue import AlarmQueue
from trade_graph.application.events import EventEngine
from trade_graph.domain.errors import ValidationFailure

D = Decimal


def _fill_entry(account, *, bid="99.4", ask="99.5", volume="100") -> None:
    account.clock.advance(1)
    account.quote("SOL/USD", bid, ask, volume=volume)


def _wakes(engine, account, **filters):
    return engine.wakes(account.portfolio, **filters)


def _fire(account, *, level="65000", department="trader") -> str:
    book = AlarmBook(account.database, account.clock)
    alarm = book.create(quote_request(level, department=department, portfolio_id=account.portfolio))
    assert book.fire(alarm.alarm_id, alarm.generation, observed={"price": "65001"}) is not None
    return alarm.alarm_id


def test_a_day_plan_entry_fill_wakes_the_trader_once_with_its_reason(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    installed = install(account, plan(entries=[entry("5", "99.5", "97", "104")]))
    account.dispatch()
    assert engine.step([account.portfolio]).created == 0  # resting entries wake nobody
    _fill_entry(account)
    assert engine.step([account.portfolio]).created == 1
    assert engine.step([account.portfolio]).created == 0  # the same fill is never a second wake
    (wake,) = _wakes(engine, account)
    assert (wake.department, wake.purpose, wake.status, wake.reasons) == ("trader", "event", "queued",
                                                                           ("entry_filled",))
    (trigger,) = wake.triggers
    assert (trigger.plan_id, trigger.entry_id, trigger.symbol) == (installed.plan_id,
                                                                   installed.entries[0].entry_id, "SOL/USD")
    assert (trigger.quantity, trigger.price, trigger.side) == (D("5"), D("99.5"), "buy")
    assert wake.allowance.status == "unknown"  # nothing was recorded, and unknown is never shown as plenty
    (task,) = tasks(account, "trader")
    assert json.loads(task["input_json"])["wake"]["wake_id"] == wake.wake_id
    assert task["objective"] == "wake:event" and task["parent_id"] is None
    seen: list = []
    assert run_one(scheduler, engine, {"trader": scripted(seen=seen)}) == task["task_id"]
    assert wake_input(seen[0])["reasons"] == ["entry_filled"]
    assert wake_input(seen[0])["plans"][0]["plan_id"] == installed.plan_id
    assert engine.wake(wake.wake_id).status == "completed"
    # The protective exit was attached with the fill; when its stop fills, the Trader is woken again.
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "96.5", "96.6", volume="0")
    assert engine.step([account.portfolio]).created == 1
    exit_wake = _wakes(engine, account)[-1]
    assert exit_wake.reasons == ("exit_filled",)
    assert (exit_wake.triggers[0].exit_leg, exit_wake.triggers[0].side) == ("stop", "sell")


def test_triggers_that_arrive_while_a_department_is_awake_wait_and_arrive_together(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    first = _fire(account, level="65000")
    engine.step([account.portfolio])
    (awake,) = _wakes(engine, account)
    assert awake.alarm_delivery_ids and AlarmQueue(account.database, account.clock).open_claim("trader")
    later = [_fire(account, level=str(66000 + step)) for step in range(3)]
    engine.request_wake(account.portfolio, "trader", "The Leader: Kraken posted maintenance for SOL.",
                        requested_by="leader")
    assert engine.step([account.portfolio]).created == 0  # one open wake per department: the rest waits
    run_one(scheduler, engine, {"trader": scripted()})
    assert AlarmQueue(account.database, account.clock).batch(awake.wake_id).status == "acknowledged"
    assert engine.step([account.portfolio]).created == 1
    batch = _wakes(engine, account)[-1]
    assert batch.reasons == ("alarm_fired", "wake_request")
    assert {trigger.alarm_id for trigger in batch.triggers if trigger.alarm_id} == set(later)
    assert len(batch.alarm_delivery_ids) == 3
    assert first not in {trigger.alarm_id for trigger in batch.triggers}
    note = next(trigger for trigger in batch.triggers if trigger.kind == "wake_request")
    assert (note.requested_by, note.note) == ("leader", "The Leader: Kraken posted maintenance for SOL.")


def test_nothing_caps_wakes_and_every_trigger_is_delivered_exactly_once(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    fired = []
    for round_ in range(40):
        fired.append(_fire(account, level=str(60000 + round_)))
        if round_ % 3 == 0:
            fired.append(_fire(account, level=str(70000 + round_)))  # two in one round: batched, not dropped
        engine.step([account.portfolio])
        run_one(scheduler, engine, {"trader": scripted()})
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted()})
    wakes = _wakes(engine, account)
    assert len(wakes) == 40 and all(wake.status == "completed" for wake in wakes)
    delivered = [trigger.alarm_id for wake in wakes for trigger in wake.triggers]
    assert sorted(delivered) == sorted(fired)  # every fire in exactly one wake
    rows = account.database.execute("SELECT status, COUNT(*) FROM wake_triggers GROUP BY status").fetchall()
    assert {row[0]: row[1] for row in rows} == {"delivered": len(fired)}
    assert AlarmQueue(account.database, account.clock).waiting() == {}


def test_a_restart_neither_loses_nor_repeats_a_wake(tmp_path, monkeypatch) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    _fire(account)

    def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash while creating the wake's task")

    monkeypatch.setattr(scheduler, "add_task", crash)
    report = engine.step([account.portfolio])
    assert report.created == 0 and report.failures == ("dispatch:RuntimeError",)
    assert _wakes(engine, account) == [] and tasks(account) == []
    assert AlarmQueue(account.database, account.clock).waiting() == {"trader": 1}  # the claim rolled back too
    monkeypatch.undo()
    for _restart in range(3):
        scheduler, engine = engine_for(account, departments={"trader"}, daily_cycle=False)
        engine.step([account.portfolio])
    assert len(_wakes(engine, account)) == 1 and len(tasks(account)) == 1
    # A wake queued before a restart runs after it, once.
    scheduler, engine = engine_for(account, departments={"trader"}, daily_cycle=False)
    assert run_one(scheduler, engine, {"trader": scripted()}) is not None
    scheduler, engine = engine_for(account, departments={"trader"}, daily_cycle=False)
    engine.step([account.portfolio])
    assert run_one(scheduler, engine, {"trader": scripted()}) is None
    (wake,) = _wakes(engine, account)
    assert wake.status == "completed" and len(tasks(account)) == 1


def test_a_task_that_finished_before_a_crash_is_closed_at_the_next_step(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    _fire(account)
    engine.step([account.portfolio])
    lease = scheduler.claim("worker", roles={"trader"})
    scheduler.finish(lease, {"wake_outcome": {"check_in": {"at_utc": "2026-10-16T14:30:00Z"}}}, "SUCCEEDED")
    # The service stopped before it closed the wake; a new engine closes it from the saved task result.
    scheduler, engine = engine_for(account, departments={"trader"}, daily_cycle=False)
    engine.step([account.portfolio])
    (wake,) = _wakes(engine, account)
    assert wake.status == "completed" and wake.outcome.check_in_id is not None
    assert engine.next_check_ins(account.portfolio)["trader"]["due_at"] == "2026-10-16T14:30:00.000000Z"


def test_an_obsolete_wake_never_becomes_a_trade(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"})
    engine.step([account.portfolio])  # the day start: no Research here, so the Trader's day-plan step follows
    (wake,) = _wakes(engine, account)
    assert wake.purpose == "day_plan"
    intents = account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0]
    account.clock.advance(timedelta(hours=17).total_seconds())  # the trading day ended before the Trader ran
    handler = scripted({"day_plans": [plan().model_dump(mode="json")]})
    run_one(scheduler, engine, {"trader": handler})
    old = engine.wake(wake.wake_id)
    assert old.status == "obsolete" and "obsolete" in old.close_reason
    assert account.database.execute("SELECT COUNT(*) FROM day_plans").fetchone()[0] == 0
    assert account.database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == intents
    assert account.database.execute("SELECT status FROM wake_triggers WHERE kind = 'day_plan_step' "
                                    "ORDER BY sequence LIMIT 1").fetchone()[0] == "expired"
    assert engine.cycle(account.portfolio, wake.trading_day_start)["day_plan"]["status"] == "failed"
    # The new trading day starts with its own day-plan step.
    engine.step([account.portfolio])
    assert _wakes(engine, account)[-1].trading_day_start == wake.trading_day_end


def test_a_turn_that_ends_after_its_trading_day_installs_nothing(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"})
    account.clock.advance(timedelta(hours=15, minutes=59, seconds=50).total_seconds())  # 23:59:50 that day
    engine.step([account.portfolio])
    (wake,) = _wakes(engine, account)

    def slow_turn(task):
        account.clock.advance(20)  # the answer arrives after midnight (inside the task's 30-second lease)
        return {"day_plans": [plan().model_dump(mode="json")]}

    run_one(scheduler, engine, {"trader": scripted(slow_turn)})
    record = engine.wake(wake.wake_id)
    assert record.status == "completed"
    assert [(item.symbol, item.status) for item in record.outcome.plans] == [("SOL/USD", "obsolete")]
    assert account.database.execute("SELECT COUNT(*) FROM day_plans").fetchone()[0] == 0


def test_a_failed_turn_puts_its_alarms_back_and_carries_its_triggers_without_waking_again(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    fired = _fire(account)
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"trader": scripted(fail=True)})
    (failed,) = _wakes(engine, account)
    assert failed.status == "failed"
    assert AlarmQueue(account.database, account.clock).waiting() == {"trader": 1}  # released, in order
    assert engine.step([account.portfolio]).created == 0  # a failure is no new reason to wake
    install(account, plan(entries=[entry("5", "99.5", "97")]))
    account.dispatch()
    _fill_entry(account)
    assert engine.step([account.portfolio]).created == 1
    again = _wakes(engine, account)[-1]
    assert again.reasons == ("alarm_fired", "entry_filled")
    assert {trigger.alarm_id for trigger in again.triggers if trigger.alarm_id} == {fired}
    assert len(again.alarm_delivery_ids) == 1


def test_a_wake_request_wakes_the_department_with_its_note_and_grants_nothing(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader", "leader"}, daily_cycle=False)
    engine.request_wake(account.portfolio, "leader", "Owner asked for a round.", requested_by="software")
    engine.step([account.portfolio])
    leader = scripted({"wake_requests": [{"department": "trader", "note": "SOL headline worth a look."}],
                       "day_plans": [plan().model_dump(mode="json")]})
    run_one(scheduler, engine, {"leader": leader})
    (leader_wake,) = _wakes(engine, account, department="leader")
    assert leader_wake.outcome.plans == ()  # only the Trader writes day plans
    assert "only the Trader" in leader_wake.outcome.refused
    assert account.database.execute("SELECT COUNT(*) FROM day_plans").fetchone()[0] == 0
    engine.step([account.portfolio])
    (trader_wake,) = _wakes(engine, account, department="trader")
    assert trader_wake.reasons == ("wake_request",)
    assert (trader_wake.triggers[0].requested_by, trader_wake.triggers[0].note) == ("leader",
                                                                                      "SOL headline worth a look.")
    with pytest.raises(ValidationFailure):
        engine.request_wake(account.portfolio, "engineer", "nope", requested_by="leader")


def test_each_department_names_its_next_check_in_and_none_means_the_next_day_start(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"leader"})
    engine.step([account.portfolio])  # the day start: the Leader has named no time, so it is checked in now
    (first,) = _wakes(engine, account)
    assert (first.purpose, first.reasons) == ("check_in", ("day_start",))
    run_one(scheduler, engine, {"leader": scripted({"check_in": {"at_utc": "2026-10-16T14:30:00Z",
                                                                 "note": "after the US open"}})})
    assert engine.next_check_ins(account.portfolio)["leader"] == {
        "due_at": "2026-10-16T14:30:00.000000Z", "basis": "named", "note": "after the US open",
        "set_by_wake_id": first.wake_id}
    account.clock.advance(timedelta(hours=6, minutes=29).total_seconds())  # 14:29
    assert engine.step([account.portfolio]).created == 0
    account.clock.advance(60)
    assert engine.step([account.portfolio]).created == 1
    named = _wakes(engine, account)[-1]
    assert named.reasons == ("check_in",) and named.triggers[0].note == "after the US open"
    run_one(scheduler, engine, {"leader": scripted({"summary": "Nothing new."})})
    nxt = engine.next_check_ins(account.portfolio)["leader"]
    assert (nxt["due_at"], nxt["basis"]) == ("2026-10-17T00:00:00.000000Z", "day_start")
    account.clock.advance(timedelta(hours=9, minutes=30).total_seconds())  # 00:00:00 next day
    engine.step([account.portfolio])
    assert _wakes(engine, account)[-1].reasons == ("day_start",)


def test_a_department_that_names_a_later_time_is_not_woken_at_the_day_start(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"optimisation"})
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"optimisation": scripted({"check_in": {"at_utc": "2026-10-20T09:00:00Z"}})})
    account.clock.advance(timedelta(days=1).total_seconds())
    engine.step([account.portfolio])
    assert len(_wakes(engine, account)) == 1  # its chosen time is in four days; the day start leaves it be
    account.clock.advance(timedelta(days=3, hours=1).total_seconds())
    engine.step([account.portfolio])
    assert [wake.reasons for wake in _wakes(engine, account)] == [("day_start",), ("check_in",)]


def test_a_lost_feed_wakes_its_alarms_owner_and_its_return_rides_along(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    book = AlarmBook(account.database, account.clock)
    book.create(quote_request("65000", portfolio_id=account.portfolio))
    book.record_feed_transition(FeedSeries("quote", "BTC/USD"), "lost", {"note": "synthetic"})
    engine.step([account.portfolio])
    (lost,) = _wakes(engine, account)
    assert lost.reasons == ("feed_lost",)
    run_one(scheduler, engine, {"trader": scripted()})
    book.record_feed_transition(FeedSeries("quote", "BTC/USD"), "restored", {"note": "synthetic"})
    assert engine.step([account.portfolio]).created == 0  # a returning feed wakes nobody by itself
    engine.request_wake(account.portfolio, "trader", "a later reason", requested_by="leader")
    engine.step([account.portfolio])
    assert _wakes(engine, account)[-1].reasons == ("feed_restored", "wake_request")


def test_every_wake_records_the_allowance_reading_and_waits_while_ai_is_paused(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    observed = (account.clock.now() - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    account.database.execute(
        "INSERT INTO subscription_provider_state (provider, ai_paused, reason, quota_json, updated_at) "
        "VALUES ('codex_subscription', 1, 'allowance floor', ?, ?)",
        (json.dumps({"observed_at": observed, "source": "codex-app-server",
                     "weekly": {"remaining_percent": 38.5}, "five_hour": {"remaining_percent": 71}}),
         account.clock.now().isoformat()))
    _fire(account)
    assert engine.step([account.portfolio], ai_paused=True).created == 1  # recorded; the task waits for AI
    (wake,) = _wakes(engine, account)
    assert wake.allowance.status == "paused" and wake.allowance.ai_paused is True
    assert wake.allowance.lowest_remaining_percent == D("38.5")
    assert [window.window for window in wake.allowance.windows] == ["five_hour", "weekly"]
    (task,) = tasks(account, "trader")
    assert task["status"] == "QUEUED"


def test_a_software_source_may_add_a_news_alarm_and_nothing_else(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader", "leader"}, daily_cycle=False)
    first = engine.notify(account.portfolio, "leader", kind="news_alarm", source_ref="news:abc",
                          note="Kraken delists XYZ", symbol="SOL/USD")
    again = engine.notify(account.portfolio, "leader", kind="news_alarm", source_ref="news:abc",
                          note="Kraken delists XYZ", symbol="SOL/USD")
    assert first.trigger_id == again.trigger_id  # one trigger per source event
    with pytest.raises(ValidationFailure, match="software"):
        engine.notify(account.portfolio, "trader", kind="entry_filled", source_ref="fake:1", note="fake fill")
    engine.step([account.portfolio])
    assert [wake.reasons for wake in _wakes(engine, account)] == [("news_alarm",)]


def test_an_older_database_without_the_engine_tables_is_left_alone(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"})
    account.database.execute("DROP TRIGGER wakes_no_delete")
    account.database.execute("DROP TABLE wakes")
    assert engine.step([account.portfolio]).created == 0
    assert isinstance(engine, EventEngine)


def test_the_engine_refuses_departments_it_cannot_wake(tmp_path) -> None:
    account, _scheduler, _engine = engine_account(tmp_path, departments={"trader"})
    with pytest.raises(ValidationFailure, match="engineer"):
        engine_for(account, departments={"trader", "engineer"})
    assert datetime(2026, 10, 16, tzinfo=UTC) < account.clock.now()


def test_an_alarm_that_fires_between_collection_and_dispatch_still_reaches_the_wake(tmp_path, monkeypatch) -> None:
    account, scheduler, engine = engine_account(tmp_path, departments={"trader"}, daily_cycle=False)
    engine.request_wake(account.portfolio, "trader", "a reason", requested_by="leader")
    collect = engine.sources._alarms

    def late_fire(portfolios):
        added = collect(portfolios)
        _fire(account)  # the watcher fires in the feed thread just after the queue was read
        return added

    monkeypatch.setattr(engine.sources, "_alarms", late_fire)
    engine.step([account.portfolio])
    (wake,) = _wakes(engine, account)
    assert wake.reasons == ("alarm_fired", "wake_request") and len(wake.alarm_delivery_ids) == 1
    assert AlarmQueue(account.database, account.clock).waiting() == {}


def test_delegation_bounds_apply_inside_a_wake_and_never_to_the_number_of_wakes(tmp_path) -> None:
    from trade_graph.domain.errors import AuthorityDenied

    account, scheduler, engine = engine_account(tmp_path, departments={"leader"}, daily_cycle=False)
    refused: list = []

    def delegate(task):
        for number in range(scheduler.max_descendants + 1):
            try:
                scheduler.add_task(role="research", objective=f"synthetic consultation {number}",
                                   portfolio_id=account.portfolio, parent_id=task["task_id"])
            except AuthorityDenied as error:
                refused.append(str(error))
        return {}

    engine.request_wake(account.portfolio, "leader", "first", requested_by="software")
    engine.step([account.portfolio])
    run_one(scheduler, engine, {"leader": delegate})
    assert refused == ["descendant limit"]  # the root's own bound, inside this one wake
    for number in range(scheduler.max_descendants + 3):  # more wakes than one root may have descendants
        engine.request_wake(account.portfolio, "leader", f"wake {number}", requested_by="software")
        engine.step([account.portfolio])
        run_one(scheduler, engine, {"leader": scripted()})
    assert len(_wakes(engine, account)) == scheduler.max_descendants + 4
    roots = account.database.execute("SELECT COUNT(*) FROM tasks WHERE role = 'leader' AND parent_id IS NULL")
    assert roots.fetchone()[0] == scheduler.max_descendants + 4  # every wake is its own root task
