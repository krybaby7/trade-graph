"""The alarm book (T33): create, fire once, re-arm, cancel; only the owning department changes its alarms."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.alarm_support import NOW, quote_request

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, DuplicateRecord, NotFound, StaleState, ValidationFailure


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


def test_a_new_alarm_is_armed_once_and_keeps_its_condition_and_references(book, database) -> None:
    record = book.create(quote_request("65000.50", "crosses_above", portfolio_id="paper-usd",
                                       day_plan_ref="plan:2026-10-16:BTC", note="Range break", task_id="task-1"))
    assert (record.status, record.generation, record.armed_at) == ("armed", 1, utc_iso(NOW))
    assert record.condition.level == Decimal("65000.50")
    assert (record.portfolio_id, record.day_plan_ref) == ("paper-usd", "plan:2026-10-16:BTC")
    assert record.note == "Range break"
    assert book.get(record.alarm_id) == record
    events = database.execute("SELECT action, generation, task_id FROM price_alarm_events").fetchall()
    assert [tuple(row) for row in events] == [("armed", 1, "task-1")]
    stored = database.execute("SELECT condition_json, contract_version FROM price_alarms").fetchone()
    assert '"level":"65000.5"' in stored["condition_json"] and stored["contract_version"] == "price-alarm-v1"


def test_a_retried_request_returns_the_same_alarm_and_a_changed_one_is_refused(book) -> None:
    first = book.create(quote_request("65000", request_id="call-7"))
    assert book.create(quote_request("65000", request_id="call-7")) == first
    with pytest.raises(DuplicateRecord):
        book.create(quote_request("66000", request_id="call-7"))
    other = book.create(quote_request("65000", request_id="call-7", department="leader"))
    assert other.alarm_id != first.alarm_id  # request ids belong to one department


def test_an_alarm_fires_once_per_arming_and_waits_to_be_rearmed(book, clock) -> None:
    alarm = book.create(quote_request())
    with pytest.raises(StaleState, match="armed"):
        book.rearm(alarm.alarm_id, department="trader")
    clock.advance(60)
    delivery = book.fire(alarm.alarm_id, 1, observed={"value": "65001"})
    assert delivery is not None and delivery.kind == "alarm_fired" and delivery.generation == 1
    assert book.fire(alarm.alarm_id, 1, observed={"value": "65002"}) is None  # spent: no second fire
    spent = book.get(alarm.alarm_id)
    assert (spent.status, spent.fired_at) == ("spent", utc_iso(clock.now()))
    clock.advance(60)
    again = book.rearm(alarm.alarm_id, department="trader", after_generation=1)
    assert (again.status, again.generation, again.armed_at, again.fired_at) == ("armed", 2, utc_iso(clock.now()), None)
    assert book.rearm(alarm.alarm_id, department="trader", after_generation=1) == again  # a retried re-arm
    assert book.fire(alarm.alarm_id, 1, observed={}) is None  # an old arming cannot fire the new one
    assert book.fire(alarm.alarm_id, 2, observed={"value": "65003"}) is not None


def test_only_the_owning_department_rearms_or_cancels(book) -> None:
    alarm = book.create(quote_request(department="trader"))
    with pytest.raises(AuthorityDenied):
        book.cancel(alarm.alarm_id, department="leader")
    book.fire(alarm.alarm_id, 1, observed={})
    with pytest.raises(AuthorityDenied):
        book.rearm(alarm.alarm_id, department="research")
    with pytest.raises(ValidationFailure):
        book.rearm(alarm.alarm_id, department="engineer")
    assert book.get(alarm.alarm_id).status == "spent"


def test_a_cancelled_alarm_never_fires_or_rearms(book) -> None:
    armed = book.create(quote_request())
    cancelled = book.cancel(armed.alarm_id, department="trader", reason="plan replaced")
    assert cancelled.status == "cancelled" and cancelled.cancelled_at is not None
    assert book.cancel(armed.alarm_id, department="trader") == cancelled  # cancelling again changes nothing
    assert book.fire(armed.alarm_id, 1, observed={}) is None
    with pytest.raises(StaleState, match="cancelled"):
        book.rearm(armed.alarm_id, department="trader")
    spent = book.create(quote_request("70000"))
    book.fire(spent.alarm_id, 1, observed={})
    assert book.cancel(spent.alarm_id, department="trader").status == "cancelled"  # a spent alarm can be retired


def test_listing_filters_by_department_status_and_series(book) -> None:
    btc = book.create(quote_request(symbol="BTC/USD"))
    eth = book.create(quote_request(symbol="ETH/USD", department="leader"))
    book.fire(eth.alarm_id, 1, observed={})
    assert [record.alarm_id for record in book.armed(basis="quote", symbol="BTC/USD")] == [btc.alarm_id]
    assert book.armed(basis="candle") == []
    assert [record.alarm_id for record in book.alarms(department="leader")] == [eth.alarm_id]
    assert [record.alarm_id for record in book.alarms(status="spent")] == [eth.alarm_id]
    with pytest.raises(NotFound):
        book.get("missing")


def test_there_is_no_limit_on_how_many_alarms_a_department_keeps(book) -> None:
    """D116: thousands of alarms on one symbol for one department are simply kept and watched."""
    created = [book.create(quote_request(str(60000 + index))) for index in range(1500)]
    assert len(book.armed(basis="quote", symbol="BTC/USD")) == 1500
    assert len({record.alarm_id for record in created}) == 1500


def test_the_book_survives_a_restart(tmp_path, clock) -> None:
    path = tmp_path / "restart.sqlite"
    first = Database(path)
    alarm = AlarmBook(first, clock).create(quote_request())
    AlarmBook(first, clock).fire(alarm.alarm_id, 1, observed={})
    first.close()
    second = Database(path)
    try:
        book = AlarmBook(second, clock)
        assert book.get(alarm.alarm_id).status == "spent"
        assert book.fire(alarm.alarm_id, 1, observed={}) is None
    finally:
        second.close()
