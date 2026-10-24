"""A news alarm only adds wake-ups (T45, D116, D228), over random sequences of news alarms, price alarms, wake
requests, turns, failures, day changes and restarts: every trigger, news or not, ends delivered by exactly one
completed wake; a wake made only by news alarms never records a check-in, so it never moves a department's own next
look; and a news alarm never changes another trigger. Synthetic account; scripted departments; no network or model.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter
from datetime import timedelta

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.alarm_support import quote_request
from tests.integration.test_execution import _stack
from tests.wake_support import run_one, scripted

from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.events import EventEngine
from trade_graph.application.scheduler import Scheduler

DEPARTMENTS = ("trader", "leader")
OPERATIONS = st.one_of(
    st.tuples(st.just("news"), st.sampled_from(DEPARTMENTS)),
    st.just(("fire",)),
    st.tuples(st.just("request"), st.sampled_from(DEPARTMENTS)),
    st.just(("step",)),
    st.tuples(st.just("run"), st.booleans()),
    st.tuples(st.just("advance"), st.sampled_from((60, 3600, 6 * 3600, 25 * 3600))),
    st.just(("restart",)),
)
_counter = itertools.count()


def _answer(clock):
    """A department that names a time when woken for anything but news, and names none for a news-only wake."""

    def outcome(task):
        reasons = set(task["input"]["wake"]["reasons"])
        if reasons <= {"news_alarm"}:
            return {"summary": "Synthetic: read the news."}
        return {"check_in": {"at_utc": (clock.now() + timedelta(hours=2)).isoformat()}}

    return outcome


@settings(max_examples=50, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow,
                                                                                      HealthCheck.function_scoped_fixture])
@given(operations=st.lists(OPERATIONS, min_size=3, max_size=40))
@example(operations=[("request", "leader"), ("step",), ("run", True), ("news", "leader"), ("step",), ("run", True)])
@example(operations=[("fire",), ("news", "trader"), ("step",), ("run", False), ("news", "trader"), ("step",),
                     ("restart",), ("run", True)])
@example(operations=[("news", "leader"), ("step",), ("news", "leader"), ("request", "leader"), ("step",),
                     ("advance", 25 * 3600), ("run", True), ("step",), ("run", True)])
def test_news_alarms_only_add_wake_ups_and_never_move_a_check_in(tmp_path, operations) -> None:
    folder = tmp_path / f"case-{next(_counter)}"
    folder.mkdir()
    clock, ledger, execution, _broker, portfolio = _stack(folder)
    database = ledger.database
    book = AlarmBook(database, clock)

    def fresh():
        scheduler = Scheduler(database, clock)
        return scheduler, EventEngine(execution, scheduler, departments=DEPARTMENTS, daily_cycle=False)

    scheduler, engine = fresh()
    handlers = {department: scripted(_answer(clock)) for department in DEPARTMENTS}
    failing = {department: scripted(fail=True) for department in DEPARTMENTS}
    added = news = 0
    for operation in operations:
        if operation[0] == "news":
            engine.notify(portfolio, operation[1], kind="news_alarm", source_ref=f"news-alarm:case-{news}",
                          note=f"News alarm: synthetic headline {news}.")
            news += 1
        elif operation[0] == "fire":
            alarm = book.create(quote_request(str(60000 + added), portfolio_id=portfolio))
            book.fire(alarm.alarm_id, alarm.generation, observed={"price": "1"})
            added += 1
        elif operation[0] == "request":
            engine.request_wake(portfolio, operation[1], f"request {added}", requested_by="software")
            added += 1
        elif operation[0] == "step":
            engine.step([portfolio])
        elif operation[0] == "run":
            run_one(scheduler, engine, handlers if operation[1] else failing)
        elif operation[0] == "advance":
            clock.advance(operation[1])
        else:
            scheduler, engine = fresh()
    for department in DEPARTMENTS:
        engine.request_wake(portfolio, department, "drain", requested_by="software")
    for _ in range(4 * len(operations) + 10):
        engine.step([portfolio])
        if run_one(scheduler, engine, handlers) is None:
            engine.step([portfolio])
            if run_one(scheduler, engine, handlers) is None:
                break
    # Every trigger, news or not, was delivered by exactly one completed wake; nothing was dropped or repeated.
    # (Check-in triggers come from the times the departments named; they are counted, not planted.)
    kinds = Counter(row[0] for row in database.execute("SELECT kind FROM wake_triggers"))
    assert kinds["news_alarm"] == news
    assert sum(number for kind, number in kinds.items() if kind not in ("news_alarm", "check_in")) == \
        added + len(DEPARTMENTS)
    statuses = Counter(row[0] for row in database.execute("SELECT status FROM wake_triggers"))
    assert statuses == Counter({"delivered": sum(kinds.values())})
    deliveries = Counter()
    for (context,) in database.execute("SELECT context_json FROM wakes WHERE status = 'completed'"):
        deliveries.update(trigger["trigger_id"] for trigger in json.loads(context)["triggers"])
    assert set(deliveries.values()) == {1} and len(deliveries) == sum(kinds.values())
    # A wake made only by news alarms never recorded a check-in; every other completed engine wake did.
    for context, outcome in database.execute(
            "SELECT context_json, outcome_json FROM wakes WHERE status = 'completed' AND origin = 'engine'"):
        reasons = set(json.loads(context)["reasons"])
        recorded = json.loads(outcome).get("check_in_id") is not None
        assert recorded is not (reasons <= {"news_alarm"}), reasons
