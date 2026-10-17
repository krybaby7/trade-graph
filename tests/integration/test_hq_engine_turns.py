"""W6-C's follow-up (assigned to W7-A): with the event engine on, HQ's rooms and calendar take each department's next
turn from its own check-in, as ``EventEngine.next_check_ins`` reads it (D123), instead of the old fixed timers, which
stop moving once the engine runs and would show old turns as overdue. With the engine off nothing changes.

Isolated synthetic dashboard databases (``tests/integration/test_hq_projection.py``'s harness); check-ins are written
through the event engine's own store. Reading HQ starts nothing. No network, key, model, paid call or order.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from tests.integration.test_dashboard import _client
from tests.integration.test_hq_projection import LIVE_PID, quiet, room, snap, text

from trade_graph.api import hq_wakes, resources
from trade_graph.api.hq_contract import iso_ms
from trade_graph.application import service_controller
from trade_graph.application.events.store import WakeStore
from trade_graph.domain.clock import utc_iso
from trade_graph.kernel.money_limits import trading_day
from trade_graph.paper_runtime import PaperRuntimeConfig

ENGINE_ON = PaperRuntimeConfig(public_data_enabled=True, event_engine=True)
SCHEDULED = ("trader", "research", "learning", "leader")


@pytest.fixture
def board(tmp_path, monkeypatch):
    """The HQ projection tests' dashboard database and runtime (``test_hq_projection.env``)."""
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(2 * 86400 + 3600)
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    yield SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    database.close()


def _check_in(board, department: str, *, hours: float | None, note: str | None = None):
    store = WakeStore(board.db, board.runtime.clock)
    now = board.runtime.clock.now()
    due = now + timedelta(hours=hours) if hours is not None else trading_day(now, "00:00").end
    return store.record_check_in(portfolio_id=board.pid, department=department, due_at=due,
                                 basis="named" if hours is not None else "day_start", note=note, wake_id=None,
                                 task_id=None)


def _stale_timers(board) -> None:
    """The old fixed timers as they stand once the engine runs: their rows stopped moving hours ago."""
    quiet(board)
    for name in SCHEDULED:
        board.db.execute("UPDATE schedules SET next_due_at = ? WHERE schedule_id = ?",
                       (iso_ms(board.runtime.clock.now() - timedelta(hours=5)), f"s-{name}"))


def _day_end(board) -> str:
    return iso_ms(trading_day(board.runtime.clock.now(), "00:00").end)


def _turns(data) -> dict[str, list[dict]]:
    turns: dict[str, list[dict]] = {}
    for item in data["calendar"]["items"]:
        if item["kind"] == "dept_turn" and not item["past"]:
            turns.setdefault(item["dept"], []).append(item)
    return turns


def test_with_the_engine_off_rooms_and_calendar_keep_the_fixed_timers(board) -> None:
    _stale_timers(board)
    _check_in(board, "trader", hours=2)
    data = snap(board)
    trader = room(data, "trader")
    assert trader["next"]["overdue"] is True and trader["next"]["every_seconds"] == 14400
    assert any(item["status"] == "estimated" for item in data["calendar"]["items"])


def test_with_the_engine_on_rooms_take_the_next_turn_from_each_departments_check_in(board) -> None:
    _stale_timers(board)
    board.runtime.config = ENGINE_ON
    named = _check_in(board, "trader", hours=2, note="Synthetic: after the US open.")
    _check_in(board, "research", hours=None)
    data = snap(board)
    trader = room(data, "trader")
    assert trader["next"]["at"] == iso_ms(named.due_at)
    assert trader["next"]["overdue"] is False and trader["next"]["status"] == "planned"
    assert trader["next"]["every_seconds"] is None
    for department in ("research", "learning", "leader"):  # none named a time: the next day start (D123)
        assert room(data, department)["next"]["at"] == _day_end(board)
        assert room(data, department)["next"]["overdue"] is False
    assert {room(data, name)["next"]["status"] for name in SCHEDULED} == {"planned"}


def test_with_the_engine_on_the_calendar_lists_one_exact_turn_per_department_and_no_old_estimates(board) -> None:
    _stale_timers(board)
    board.runtime.config = ENGINE_ON
    named = _check_in(board, "trader", hours=2)
    data = snap(board)
    turns = _turns(data)
    assert set(turns) >= set(SCHEDULED)
    assert all(len(items) == 1 for items in turns.values())
    (trader,) = turns["trader"]
    assert trader["at"] == iso_ms(named.due_at) and trader["exact"] is True and trader["status"] == "planned"
    assert trader["series"] is None and trader["id"].startswith("turn:check-in:trader:")
    assert turns["leader"][0]["at"] == _day_end(board)
    assert not [item for item in data["calendar"]["items"] if item["status"] == "estimated"]
    assert data["calendar"]["up_next"][0] == trader["id"]
    assert not [note for note in data["calendar"]["notes"] if "once the team has been started" in text(note)]


def test_a_check_in_whose_time_came_and_was_recorded_moves_to_the_next_day_start(board) -> None:
    _stale_timers(board)
    board.runtime.config = ENGINE_ON
    passed = _check_in(board, "trader", hours=-1)
    data = snap(board)
    assert room(data, "trader")["next"]["at"] == iso_ms(passed.due_at)
    assert room(data, "trader")["next"]["overdue"] is True  # due, and the engine has not recorded it yet
    WakeStore(board.db, board.runtime.clock).add_trigger(
        kind="check_in", department="trader", portfolio_id=board.pid, source_ref=f"check-in:{passed.check_in_id}",
        occurred_at=passed.due_at, check_in_id=passed.check_in_id)
    data = snap(board)
    assert room(data, "trader")["next"]["at"] == _day_end(board) and room(data, "trader")["next"]["overdue"] is False


def test_the_rooms_and_the_wake_log_read_check_ins_by_one_rule(board) -> None:
    _stale_timers(board)
    board.runtime.config = ENGINE_ON
    _check_in(board, "trader", hours=3)
    _check_in(board, "learning", hours=-2)
    data = snap(board)
    log = hq_wakes.wake_log(board.runtime)
    for view in log["day"]["check_ins"]:
        if view["dept"] in SCHEDULED:
            assert room(data, view["dept"])["next"]["at"] == view["at"], view["dept"]


def test_with_the_engine_on_the_snapshot_is_no_bigger_than_with_the_timers(board) -> None:
    _stale_timers(board)
    for name in SCHEDULED:
        _check_in(board, name, hours=1, note="Synthetic note " * 15)
    timers = len(json.dumps(snap(board)).encode())
    board.runtime.config = ENGINE_ON
    engine = len(json.dumps(snap(board)).encode())
    assert engine <= timers
