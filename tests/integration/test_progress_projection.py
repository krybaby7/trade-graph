"""Mission-control progress preserves acceptance, scope and evidence boundaries."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.api import progress as projection
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 2, tzinfo=UTC))
    database = Database(tmp_path / "progress.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    other = ledger.create_portfolio(reporting_currency="EUR")
    yield SimpleNamespace(database=database, clock=clock, portfolio_id=portfolio, other=other)
    database.close()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    (root / "planning").mkdir(parents=True)
    for name in ("tasks.json", "progress.json"):
        shutil.copyfile(ROOT / "planning" / name, root / "planning" / name)
    monkeypatch.setattr(projection, "_SOURCE_ROOT", root)
    return root


def test_project_counts_and_milestones_preserve_scoped_acceptance(runtime, checkout):
    data = projection.progress(runtime)
    project = data["project"]
    assert (project["completed"], project["total"], project["in_progress"], project["blocked"]) == (17, 23, 4, 2)
    assert project["percent"] == 74
    assert project["source"] == "repository"
    milestones = {milestone["id"]: milestone for milestone in data["milestones"]}
    assert milestones["foundation"]["status"] == "completed"
    assert milestones["connected-paper"]["status"] == "in_progress"
    assert milestones["kraken-checks"]["status"] == "in_progress"
    assert milestones["live-pilot"]["status"] == "blocked"
    assert "authenticated" in milestones["kraken-checks"]["description"]
    assert all(department["status"] == "idle" for department in data["departments"])
    assert data["service"]["status"] == "idle"


def test_checkout_progress_is_reloaded_for_each_poll(runtime, checkout):
    before = projection.progress(runtime)
    path = checkout / "planning/progress.json"
    states = json.loads(path.read_text())
    states["tasks"]["T17"]["status"] = "done"
    path.write_text(json.dumps(states))
    after = projection.progress(runtime)
    assert before["project"]["completed"] == 17
    assert after["project"]["completed"] == 18
    assert next(m for m in after["milestones"] if m["id"] == "connected-paper")["status"] == "completed"
    assert next(m for m in after["milestones"] if m["id"] == "kraken-checks")["status"] == "in_progress"


def test_installed_catalog_is_dated_snapshot_not_fake_runtime_evidence(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(projection, "_SOURCE_ROOT", tmp_path / "installed")
    catalog = projection.progress(runtime)["project"]
    assert catalog["source"] == "packaged_snapshot"
    assert catalog["updated_at"] == "2026-10-05"
    assert catalog["completed"] == 17
    assert catalog["total"] == 23
    assert not any("owner" in task or "branch" in task or "evidence" in task for task in catalog["tasks"])
    assert "authenticated Kraken conformance is pending" in next(
        task["evidence_summary"] for task in catalog["tasks"] if task["id"] == "T19"
    )


def test_packaged_catalog_matches_current_planning_without_private_metadata():
    catalog = json.loads((ROOT / "src/trade_graph/progress_catalog.json").read_text())
    tasks = json.loads((ROOT / "planning/tasks.json").read_text())["tasks"]
    states = json.loads((ROOT / "planning/progress.json").read_text())
    assert catalog["updated_at"] == states["updated_at"]
    assert [(t["id"], t["title"], t["dependencies"], t["status"]) for t in catalog["tasks"]] == [
        (t["id"], t["title"], t["deps"], states["tasks"][t["id"]]["status"]) for t in tasks
    ]
    serialized = json.dumps(catalog)
    assert "historical_submission" not in serialized
    assert "/workspace/" not in serialized
    assert "orchestration" not in serialized


@pytest.mark.parametrize("fault", ["missing", "malformed", "oversize", "status", "dependency", "duplicate", "date"])
def test_invalid_checkout_is_unavailable_instead_of_falling_back_to_old_success(runtime, checkout, fault):
    progress_path = checkout / "planning/progress.json"
    tasks_path = checkout / "planning/tasks.json"
    if fault == "missing":
        progress_path.unlink()
    elif fault == "malformed":
        progress_path.write_text("{unfinished")
    elif fault == "oversize":
        progress_path.write_bytes(b" " * (projection._MAX_BYTES + 1))
    elif fault == "status":
        states = json.loads(progress_path.read_text())
        del states["tasks"]["T17"]
        progress_path.write_text(json.dumps(states))
    elif fault == "date":
        states = json.loads(progress_path.read_text())
        states["updated_at"] = "2026-99-99"
        progress_path.write_text(json.dumps(states))
    else:
        tasks = json.loads(tasks_path.read_text())
        if fault == "dependency":
            tasks["tasks"][0]["deps"] = ["T99"]
        else:
            tasks["tasks"].append(tasks["tasks"][0])
        tasks_path.write_text(json.dumps(tasks))
    data = projection.progress(runtime)
    assert data["project"]["source"] == "unavailable"
    assert data["project"]["completed"] is None
    assert data["project"]["percent"] is None
    assert data["project"]["tasks"] == []
    assert all(m["status"] == "pending" for m in data["milestones"])


def test_missing_installed_catalog_is_explicitly_unavailable(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(projection, "_SOURCE_ROOT", tmp_path / "installed")
    monkeypatch.setattr(projection, "_PACKAGED_CATALOG", tmp_path / "missing.json")
    assert projection.progress(runtime)["project"]["source"] == "unavailable"


def test_runtime_departments_use_actual_scoped_queue_leases_and_completed_tasks(runtime, checkout):
    scheduler = Scheduler(runtime.database, runtime.clock)
    scheduler.add_task(role="trader", objective="Scoped trade review", portfolio_id=runtime.portfolio_id)
    scheduler.claim("worker", ttl_seconds=30, roles={"trader"})
    scheduler.add_task(role="trader", objective="Queued trade review", portfolio_id=runtime.portfolio_id)
    scheduler.add_task(role="research", objective="Global research", portfolio_id=None)
    waiting = scheduler.add_task(role="learning", objective="Waiting", portfolio_id=runtime.portfolio_id)
    done = scheduler.add_task(role="engineer", objective="Done", portfolio_id=runtime.portfolio_id)
    scheduler.add_task(role="optimisation", objective="Foreign private work", portfolio_id=runtime.other)
    runtime.database.execute("UPDATE tasks SET status = 'WAITING_EXTERNAL' WHERE task_id = ?", (waiting,))
    runtime.database.execute("UPDATE tasks SET status = 'SUCCEEDED' WHERE task_id = ?", (done,))
    data = projection.progress(runtime)
    departments = {d["id"]: d for d in data["departments"]}
    assert departments["trader"]["status"] == "running"
    assert departments["trader"]["task_count"] == 2
    assert departments["research"]["status"] == "queued"
    assert departments["learning"]["status"] == "waiting"
    assert departments["engineer"]["completed_count"] == 1
    assert departments["engineer"]["status"] == "idle"
    assert departments["optimisation"]["task_count"] == 0
    runtime.clock.advance(31)
    departments = {d["id"]: d for d in projection.progress(runtime)["departments"]}
    assert departments["trader"]["status"] == "queued"
    runtime.database.execute("UPDATE tasks SET status = 'CANCELLED' WHERE status = 'QUEUED' AND role = 'trader'")
    departments = {d["id"]: d for d in projection.progress(runtime)["departments"]}
    assert departments["trader"]["status"] == "waiting"


def test_heartbeat_requires_current_paper_service_lease_and_does_not_invent_last_seen(runtime, checkout):
    scheduler = Scheduler(runtime.database, runtime.clock)
    scheduler.acquire_process_lease("unrelated-service", "owner", 30)
    assert projection.progress(runtime)["service"]["status"] == "idle"
    scheduler.acquire_process_lease("paper-service", "actual-paper-service", 30)
    service = projection.progress(runtime)["service"]
    assert service["status"] == "running"
    assert service["last_seen"] is None
    assert service["lease_expires_at"]
    runtime.clock.advance(31)
    assert projection.progress(runtime)["service"]["status"] == "idle"


def test_activity_is_bounded_scoped_past_only_and_does_not_copy_raw_payload(runtime, checkout):
    scheduler = Scheduler(runtime.database, runtime.clock)
    scheduler.add_task(
        role="research", objective="Private objective must not enter mission feed", portfolio_id=runtime.portfolio_id
    )
    future = scheduler.add_task(role="trader", objective="Future task", portfolio_id=runtime.portfolio_id)
    runtime.database.execute("UPDATE tasks SET created_at = '2027-01-01T00:00:00Z' WHERE task_id = ?", (future,))
    for index in range(25):
        runtime.database.execute(
            "INSERT INTO activity_events (event_id, portfolio_id, kind, payload_json, created_at, hash) "
            "VALUES (?, ?, 'checked', ?, '2026-01-01T00:00:00Z', ?)",
            (f"event-{index:02}", runtime.portfolio_id, '{"account":"private-account"}', f"hash-{index}"),
        )
    for event_id, portfolio, timestamp in (
        ("foreign", runtime.other, "2026-01-01T00:00:00Z"),
        ("future", runtime.portfolio_id, "2027-01-01T00:00:00Z"),
    ):
        runtime.database.execute(
            "INSERT INTO activity_events (event_id, portfolio_id, kind, payload_json, created_at, hash) "
            "VALUES (?, ?, 'checked', '{}', ?, ?)",
            (event_id, portfolio, timestamp, event_id),
        )
    data = projection.progress(runtime)
    assert len(data["activity"]) == 20
    serialized = json.dumps(data)
    assert "private-account" not in serialized
    assert "Private objective" not in serialized
    assert "event:foreign" not in serialized
    assert "event:future" not in serialized
    assert "task:" + future not in serialized
    assert next(d for d in data["departments"] if d["id"] == "trader")["task_count"] == 0


def test_local_test_results_do_not_complete_project_or_kraken_milestone_and_projection_is_read_only(runtime, checkout):
    runs = [{"id": "software-check", "status": "passed", "scope": "offline", "summary": "Synthetic fixtures passed"}]
    before = list(runtime.database.execute("SELECT name FROM sqlite_master ORDER BY name"))
    with runtime.database.snapshot():
        data = projection.progress(runtime, runs)
    assert data["test_runs"] == runs
    assert data["project"]["completed"] == 17
    assert next(m for m in data["milestones"] if m["id"] == "kraken-checks")["status"] == "in_progress"
    assert list(runtime.database.execute("SELECT name FROM sqlite_master ORDER BY name")) == before
    assert runtime.database.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert runtime.database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
    ids = {department["id"] for department in data["departments"]}
    assert all(edge["from"] in ids and edge["to"] in ids for edge in data["connections"])
