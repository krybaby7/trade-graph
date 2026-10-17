"""Coalescing batches and never drops (T35, D116), over random sequences of events, turns, failures, day changes and
restarts: at most one open wake per department, every trigger held by at most one open wake, and in the end every
trigger delivered by exactly one completed wake. Synthetic account; scripted departments; no network or model.
"""

from __future__ import annotations

import itertools
import json
from collections import Counter

from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.alarm_support import quote_request
from tests.integration.test_execution import _stack
from tests.wake_support import run_one, scripted

from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.alarms.queue import AlarmQueue
from trade_graph.application.events import EventEngine
from trade_graph.application.scheduler import Scheduler

DEPARTMENTS = ("trader", "leader")
OPERATIONS = st.one_of(
    st.just(("fire",)),
    st.tuples(st.just("request"), st.sampled_from(DEPARTMENTS)),
    st.just(("step",)),
    st.tuples(st.just("run"), st.booleans()),
    st.tuples(st.just("advance"), st.sampled_from((60, 3600, 6 * 3600, 25 * 3600))),
    st.just(("restart",)),
)
_counter = itertools.count()


def _invariants(database) -> None:
    open_wakes = database.execute(
        "SELECT portfolio_id, department, COUNT(*) FROM wakes WHERE status IN ('queued', 'applying') "
        "AND origin = 'engine' GROUP BY portfolio_id, department").fetchall()
    assert all(row[2] == 1 for row in open_wakes)
    claimed = database.execute(
        "SELECT t.trigger_id, w.status FROM wake_triggers t LEFT JOIN wakes w ON w.wake_id = t.wake_id "
        "WHERE t.status = 'claimed'").fetchall()
    assert all(row[1] in ("queued", "applying") for row in claimed)  # a claimed trigger sits in an open wake
    delivered = database.execute(
        "SELECT t.trigger_id, w.status FROM wake_triggers t JOIN wakes w ON w.wake_id = t.wake_id "
        "WHERE t.status = 'delivered'").fetchall()
    assert all(row[1] == "completed" for row in delivered)


@settings(max_examples=60, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow,
                                                                                      HealthCheck.function_scoped_fixture])
@given(operations=st.lists(OPERATIONS, min_size=3, max_size=40))
@example(operations=[("fire",), ("step",), ("run", False), ("fire",), ("step",), ("run", True)])
@example(operations=[("fire",), ("step",), ("fire",), ("request", "trader"), ("step",), ("run", False),
                     ("restart",), ("step",), ("run", True)])
@example(operations=[("fire",), ("step",), ("advance", 25 * 3600), ("step",), ("run", True), ("fire",)])
@example(operations=[("request", "leader"), ("step",), ("restart",), ("fire",), ("step",), ("run", False),
                     ("advance", 6 * 3600), ("run", False), ("step",), ("run", True)])
def test_random_events_turns_failures_and_restarts_never_drop_or_repeat_a_trigger(tmp_path, operations) -> None:
    folder = tmp_path / f"case-{next(_counter)}"
    folder.mkdir()
    clock, ledger, execution, _broker, portfolio = _stack(folder)
    database = ledger.database
    book = AlarmBook(database, clock)

    def fresh():
        scheduler = Scheduler(database, clock)
        return scheduler, EventEngine(execution, scheduler, departments=DEPARTMENTS, daily_cycle=False)

    scheduler, engine = fresh()
    handlers = {department: scripted() for department in DEPARTMENTS}
    failing = {department: scripted(fail=True) for department in DEPARTMENTS}
    added = 0
    for operation in operations:
        if operation[0] == "fire":
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
        _invariants(database)
    # Drain: one fresh reason per department, then step and run until nothing is left.
    for department in DEPARTMENTS:
        engine.request_wake(portfolio, department, "drain", requested_by="software")
    for _ in range(4 * len(operations) + 10):
        engine.step([portfolio])
        if run_one(scheduler, engine, handlers) is None:
            engine.step([portfolio])
            if run_one(scheduler, engine, handlers) is None:
                break
    _invariants(database)
    statuses = Counter(row[0] for row in database.execute("SELECT status FROM wake_triggers"))
    assert statuses == Counter({"delivered": added + len(DEPARTMENTS)})
    deliveries = Counter()
    for (context,) in database.execute("SELECT context_json FROM wakes WHERE status = 'completed'"):
        deliveries.update(trigger["trigger_id"] for trigger in json.loads(context)["triggers"])
    assert set(deliveries.values()) == {1} and len(deliveries) == added + len(DEPARTMENTS)
    assert AlarmQueue(database, clock).waiting() == {}
