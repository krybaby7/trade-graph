"""The delivery queue a wake dispatcher consumes (T33 for W5-A/T35): batches per wake, nothing lost or repeated."""

from __future__ import annotations

import pytest
from tests.alarm_support import NOW, quote_request

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.alarms.queue import AlarmQueue
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import NotFound, StaleState, ValidationFailure


@pytest.fixture
def clock():
    return FrozenClock(NOW)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "state.sqlite")
    yield database
    database.close()


@pytest.fixture
def book(database, clock):
    return AlarmBook(database, clock)


@pytest.fixture
def queue(database, clock):
    return AlarmQueue(database, clock)


def _fire(book: AlarmBook, clock: FrozenClock, level: str, department: str = "trader") -> str:
    alarm = book.create(quote_request(level, department=department))
    clock.advance(1)
    assert book.fire(alarm.alarm_id, 1, observed={"level": level}) is not None
    return alarm.alarm_id


def test_alarms_that_fire_during_a_wake_arrive_together_at_the_next_wake(book, queue, clock) -> None:
    first = _fire(book, clock, "65000")
    assert queue.waiting() == {"trader": 1}
    wake_one = queue.claim("trader", "wake-1")
    assert [item.alarm_id for item in wake_one.items] == [first]
    # The Trader is awake now. Two alarms fire meanwhile; neither joins the running wake.
    second, third = _fire(book, clock, "65100"), _fire(book, clock, "65200")
    assert queue.claim("trader", "wake-1") == wake_one  # the same wake asks again: the same batch
    queue.acknowledge("wake-1")
    wake_two = queue.claim("trader", "wake-2")
    assert [item.alarm_id for item in wake_two.items] == [second, third]  # together, oldest first
    assert queue.waiting() == {}


def test_departments_have_separate_queues(book, queue, clock) -> None:
    trader = _fire(book, clock, "65000", "trader")
    leader = _fire(book, clock, "66000", "leader")
    assert queue.waiting() == {"leader": 1, "trader": 1}
    assert [item.alarm_id for item in queue.claim("leader", "wake-leader").items] == [leader]
    assert [item.alarm_id for item in queue.pending("trader")] == [trader]
    with pytest.raises(ValidationFailure, match="another department"):
        queue.claim("trader", "wake-leader")


def test_one_open_wake_per_department_until_it_is_acknowledged_or_released(book, queue, clock) -> None:
    _fire(book, clock, "65000")
    open_batch = queue.claim("trader", "wake-1")
    with pytest.raises(StaleState, match="wake-1"):
        queue.claim("trader", "wake-2")
    assert queue.open_claim("trader") == open_batch
    assert queue.open_claim("leader") is None


def test_a_released_wake_returns_its_items_in_their_original_order(book, queue, clock) -> None:
    first = _fire(book, clock, "65000")
    queue.claim("trader", "wake-1")
    second = _fire(book, clock, "65100")
    released = queue.release("wake-1")
    assert released.status == "released" and released.items == ()
    assert queue.release("wake-1") == released
    with pytest.raises(StaleState, match="released"):
        queue.acknowledge("wake-1")
    with pytest.raises(StaleState, match="released"):
        queue.claim("trader", "wake-1")  # a wake id is used once
    retry = queue.claim("trader", "wake-2")
    assert [item.alarm_id for item in retry.items] == [first, second]


def test_acknowledged_items_are_delivered_once(book, queue, clock) -> None:
    alarm = _fire(book, clock, "65000")
    queue.claim("trader", "wake-1")
    done = queue.acknowledge("wake-1")
    assert done.status == "acknowledged" and [item.alarm_id for item in done.items] == [alarm]
    assert queue.acknowledge("wake-1") == done
    with pytest.raises(StaleState, match="acknowledged"):
        queue.release("wake-1")
    assert queue.claim("trader", "wake-2").items == ()
    assert queue.pending("trader") == ()


def test_an_empty_claim_is_recorded_and_stays_empty(book, queue, clock) -> None:
    empty = queue.claim("research", "wake-r1")
    assert empty.items == () and empty.status == "claimed"
    _fire(book, clock, "65000", "research")
    assert queue.claim("research", "wake-r1").items == ()
    with pytest.raises(NotFound):
        queue.batch("never-claimed")


def test_the_queue_survives_a_restart_without_losing_or_repeating(tmp_path, clock) -> None:
    path = tmp_path / "restart.sqlite"
    first = Database(path)
    book = AlarmBook(first, clock)
    one, two = _fire(book, clock, "65000"), _fire(book, clock, "65100")
    claimed = AlarmQueue(first, clock).claim("trader", "wake-1")
    first.close()
    second = Database(path)
    try:
        queue = AlarmQueue(second, clock)
        assert queue.open_claim("trader") == claimed  # the dispatcher finds its unfinished wake
        assert [item.alarm_id for item in queue.claim("trader", "wake-1").items] == [one, two]
        queue.acknowledge("wake-1")
        assert queue.waiting() == {}
    finally:
        second.close()


def test_delivery_documents_carry_what_the_department_needs(book, queue, clock) -> None:
    alarm = book.create(quote_request("65000", portfolio_id="paper-usd", day_plan_ref="plan:1", note="Break"))
    book.fire(alarm.alarm_id, 1, observed={"basis": "quote", "value": "65001"})
    item = queue.claim("trader", "wake-1").items[0]
    assert item.portfolio_id == "paper-usd"
    document = item.document
    assert document["alarm_id"] == alarm.alarm_id and document["generation"] == 1
    assert document["day_plan_ref"] == "plan:1" and document["note"] == "Break"
    assert document["condition"]["level"] == "65000" and document["observed"]["value"] == "65001"
    assert document["contract"] == "price-alarm-v1" and document["fired_at"] == item.created_at
