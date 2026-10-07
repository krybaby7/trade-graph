"""Close local task/evidence gaps without claiming continuous or paid operation."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_artifact_consumers import BASE_STRATEGY, _choice, _consumer_flow
from tests.integration.test_engineering_workflow import _flow
from tests.integration.test_learning import _lesson
from tests.leadership_support import stack

from trade_graph.adapters.market.replay import PointInTimeMarket
from trade_graph.adapters.persistence.db import Database
from trade_graph.api.app import create_app
from trade_graph.api.health import health
from trade_graph.application.learning import LearningJournal, no_trade_mark
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.secretary import Secretary
from trade_graph.application.worker import RoleWorker
from trade_graph.cli import main
from trade_graph.contracts.models import ModelRequest
from trade_graph.dashboard import dashboard_runtime
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import ValidationFailure


def test_secretary_cursor_digest_and_routing_commit_together_across_restart(tmp_path):
    database = Database(tmp_path / "routing.sqlite")
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    ledger = Ledger(database, clock)
    pid = ledger.create_portfolio(reporting_currency="EUR")
    office, secretary, _, _ = stack(database, clock, ledger, pid)
    ref = secretary.report(pid, role="research", kind="incident", summary="Synthetic new evidence",
                           evidence_refs=["synthetic-source"], source_key="one", material=True)
    before = database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    database.execute("""CREATE TRIGGER fail_cursor BEFORE UPDATE ON secretary_cursors
        BEGIN SELECT RAISE(ABORT, 'cursor unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="cursor unavailable"):
        secretary.process(pid)
    assert database.execute("SELECT COUNT(*) FROM secretary_digests").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == before
    database.execute("DROP TRIGGER fail_cursor")
    digest = secretary.process(pid)
    assert ref in digest["evidence_refs"]
    count = database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    cursor = database.execute("SELECT sequence FROM secretary_cursors WHERE portfolio_id = ?", (pid,)).fetchone()[0]
    database.close()
    reopened = Database(tmp_path / "routing.sqlite")
    execution = office.execution
    execution.database = reopened
    execution.ledger = Ledger(reopened, clock)
    execution.authority.database = reopened
    routed = Secretary(execution, Scheduler(reopened, clock)).process(pid)
    assert routed["digest_id"] == digest["digest_id"]
    assert reopened.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == count
    restored_cursor = reopened.execute(
        "SELECT sequence FROM secretary_cursors WHERE portfolio_id = ?", (pid,),
    ).fetchone()[0]
    assert restored_cursor == cursor
    reopened.close()


def test_two_dashboard_lifespans_start_no_scheduler_and_shared_workers_dispatch_once(tmp_path, monkeypatch):
    work = tmp_path / "private"
    work.mkdir(mode=0o700)
    assert main(["init", "--database", str(work / "paper.sqlite")]) == 0
    runtime = dashboard_runtime(work / "paper.sqlite")
    calls = []
    original = RoleWorker.run_available

    def observed(*args, **kwargs):
        calls.append("worker")
        return original(*args, **kwargs)

    monkeypatch.setattr(RoleWorker, "run_available", observed)
    with TestClient(create_app(runtime)) as first, TestClient(create_app(runtime)) as second:
        assert first.get("/api/v1/health").status_code == second.get("/api/v1/health").status_code == 200
        assert calls == []
        assert runtime.database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
        scheduler = Scheduler(runtime.database, runtime.clock)
        task = scheduler.add_task(role="research", objective="Once", portfolio_id=runtime.portfolio_id)
        effects = []

        def handler(data):
            effects.append(data["task_id"])
            return {"ok": True}

        worker = RoleWorker(scheduler, owner="first-core", system_version_id="fixture", reconcile=lambda: None)
        duplicate = RoleWorker(scheduler, owner="second-core", system_version_id="fixture", reconcile=lambda: None)
        assert worker.run_available({"research": handler}) == 1
        assert duplicate.run_available({"research": handler}) == 0
        assert effects == [task]
    runtime.database.close()


def test_root_step_exhaustion_survives_restart_and_stops_another_descendant(tmp_path):
    path = tmp_path / "steps.sqlite"
    database = Database(path)
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = Scheduler(database, clock, max_root_steps=2)
    root = scheduler.add_task(role="leader", objective="Bounded root", portfolio_id="p")
    first = scheduler.claim("first")
    scheduler.note_attempt(first)
    scheduler.succeed(first, {})
    child = scheduler.add_task(role="research", objective="One child", portfolio_id="p", parent_id=root)
    leased = scheduler.claim("first")
    scheduler.note_attempt(leased)
    scheduler.succeed(leased, {})
    assert child == leased.task_id
    rejected = scheduler.add_task(role="learning", objective="Exhausted child", portfolio_id="p", parent_id=root)
    database.close()
    reopened = Database(path)
    restarted = Scheduler(reopened, clock, max_root_steps=2)
    lease = restarted.claim("second")
    with pytest.raises(ValidationFailure, match="root step limit"):
        restarted.note_attempt(lease)
    row = reopened.execute("SELECT status, attempts_used FROM tasks WHERE task_id = ?", (rejected,)).fetchone()
    assert tuple(row) == ("DEAD_LETTER", 0)
    assert restarted.claim("second") is None
    assert reopened.execute("SELECT SUM(attempts_used) FROM tasks WHERE root_task_id = ?", (root,)).fetchone()[0] == 2
    reopened.close()


def test_attempted_cyclic_graph_artifact_is_rejected_with_receipt_and_unchanged_version(tmp_path):
    flow = _flow(tmp_path, max_steps=1)
    flow.gateway.scripted.outputs["engineer"] = {
        "files": [{"path": "artifacts/graph.json", "content": json.dumps({
            "edges": [["leader", "research"], ["research", "leader"]], "max_root_steps": 100000,
        })}], "summary": "Deliberately rejected recursive graph fixture",
    }
    assert flow.run() == 1
    assert flow.row()["status"] == "FAILED"
    assert flow.versions.current_hash(flow.pid) == flow.baseline
    assert len(flow.receipts()) == 1
    assert flow.db.execute("SELECT COUNT(*) FROM candidates WHERE state = 'FAILED'").fetchone()[0] == 1
    flow.db.close()


@pytest.mark.parametrize("cause", ["missing-data", "budget"])
def test_unavailable_data_or_budget_cannot_create_a_hold_decision(tmp_path, cause):
    flow = _consumer_flow(tmp_path, activate=False)
    flow.gateway.scripted.outputs["trader"] = _choice("hold")
    deliberate = flow.add_turn()
    assert flow.run() == 1 and flow.row(deliberate)["status"] == "SUCCEEDED"
    holds = flow.db.execute("SELECT COUNT(*) FROM decisions WHERE action = 'hold'").fetchone()[0]
    flow.gateway.scripted.outputs["trader"] = _choice("enter")
    if cause == "missing-data":
        flow.db.execute("DELETE FROM observations")
    else:
        card = flow.gateway.budget.card("scripted-review").model_copy(update={
            "price_card_id": "deliberately-expensive", "input_per_million": Decimal("1000000"),
        })
        flow.gateway.budget.seed_card(card)
        flow.handler.price_card_id = card.price_card_id
    task_id = flow.add_turn()
    assert flow.run() == 1
    assert flow.row(task_id)["status"] in {"FAILED", "BLOCKED_BUDGET"}
    assert flow.db.execute("SELECT COUNT(*) FROM decisions WHERE action = 'hold'").fetchone()[0] == holds
    assert flow.db.execute("SELECT COUNT(*) FROM decisions WHERE task_id = ?", (task_id,)).fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert flow.office.execution.broker.submit_count == 0
    flow.db.close()


def test_retired_lesson_preserves_case_versions_and_is_not_resurrected_in_context(tmp_path):
    flow = _consumer_flow(tmp_path, activate=False)
    opening = flow.add_turn()
    assert flow.run() == 1
    decision = json.loads(flow.row(opening)["output_json"])["decision_id"]
    journal = LearningJournal(flow.db, flow.clock)
    lesson = _lesson(flow.clock, flow.pid, record_id="causal-r1", lesson_id="causal",
                     supporting_cases=[decision], linked_decisions=[decision], system_version_id=flow.baseline)
    journal.append(flow.pid, lesson)
    flow.activate()
    flow.clock.advance(1)
    retired = lesson.model_copy(update={"record_id": "causal-r2", "revision": 2, "status": "retired",
        "supersedes": lesson.record_id, "created_at_utc": flow.clock.now(),
        "system_version_id": flow.versions.current_hash(flow.pid)})
    journal.append(flow.pid, retired)
    flow.reopen()
    flow.gateway.scripted.outputs["trader"] = _choice()
    task_id = flow.add_turn()
    assert flow.run() == 1 and flow.row(task_id)["status"] == "SUCCEEDED"
    invocation = flow.db.execute("SELECT request_json FROM model_invocations WHERE task_id = ?", (task_id,)).fetchone()
    selected = ModelRequest.model_validate_json(invocation[0]).context["selected_context"]["lessons"]
    assert all(item["lesson_id"] != "causal" for item in selected)
    history = LearningJournal(flow.db, flow.clock).history("causal")
    assert [item.status for item in history] == ["tentative", "retired"]
    assert history[0].counterexamples == history[1].counterexamples
    assert history[0].system_version_id == flow.baseline != history[1].system_version_id
    assert flow.db.execute("SELECT system_version_id FROM decisions WHERE decision_id = ?", (decision,)).fetchone()[0] \
        == flow.baseline
    flow.db.close()


def test_predeclared_entry_template_and_no_trade_mark_ignore_future_evidence(tmp_path):
    flow = _consumer_flow(tmp_path, activate=False)
    execution = flow.office.execution
    visible = execution.latest_observation("BTC/USD", execution.now(), "paper")
    future = visible.model_copy(update={"observation_id": "future-high", "bid": Decimal("999"),
        "ask": Decimal("1001"), "event_time_utc": flow.clock.now() + timedelta(hours=1),
        "available_at_utc": flow.clock.now() + timedelta(hours=1)})
    execution.save_observation(future)
    future_lesson = _lesson(flow.clock, flow.pid, record_id="future-r1", lesson_id="future",
                            observation="A hindsight claim", created_at_utc=flow.clock.now() + timedelta(hours=1))
    with pytest.raises(ValidationFailure, match="future"):
        LearningJournal(flow.db, flow.clock).append(flow.pid, future_lesson)
    future_clock = FrozenClock(future_lesson.created_at_utc)
    LearningJournal(flow.db, future_clock).append(flow.pid, future_lesson)
    market = PointInTimeMarket([], [visible, future])
    mark = no_trade_mark(market, "BTC/USD", flow.clock.now())
    assert mark["mid"] == "99.5" and mark["observation_id"] == visible.observation_id
    task_id = flow.add_turn()
    assert flow.run() == 1 and flow.row(task_id)["status"] == "SUCCEEDED"
    request = ModelRequest.model_validate_json(flow.db.execute(
        "SELECT request_json FROM model_invocations WHERE task_id = ?", (task_id,),
    ).fetchone()[0])
    assert request.context["strategy_templates"]["range-reversion"] == BASE_STRATEGY
    assert all(item["lesson_id"] != "future" for item in request.context["selected_context"]["lessons"])
    assert request.context["market"]["BTC/USD"]["features"] == mark
    flow.db.close()


def test_paper_reset_does_not_rewrite_persisted_live_prerequisites(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    assert main(["init", "--database", str(directory / "paper.sqlite")]) == 0
    runtime = dashboard_runtime(directory / "paper.sqlite")
    record = {"eligibility_confirmed": True, "live_allocation": "0", "owner_confirmed": False,
              "economic_verdict": "insufficient_evidence", "operating_budget_set": False}
    runtime.database.execute("INSERT INTO live_gate VALUES (?, 0, ?, ?)",
                             ("deployment", json.dumps(record), utc_iso(runtime.clock.now())))
    before = health(runtime, authenticated=True)["live_prerequisites"]
    stored = tuple(runtime.database.execute("SELECT * FROM live_gate").fetchone())
    reset = runtime.ledger.create_portfolio(reporting_currency="EUR", reset_of=runtime.portfolio_id)
    runtime.ledger.deposit(reset, "USD", Decimal("10000"), "reset-capital")
    assert health(runtime, authenticated=True)["live_prerequisites"] == before
    assert tuple(runtime.database.execute("SELECT * FROM live_gate").fetchone()) == stored
    assert before["enabled"] is before["implementation_ready"] is False
    runtime.database.close()
