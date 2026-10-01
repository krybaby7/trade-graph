"""Activated schedule/report settings are consumed, persist and revert on rollback."""

import json

from tests.integration.test_version_lifecycle import POLICY, activate, candidate, lifecycle_stack
from trade_graph.application.artifact_runtime import ArtifactRuntime

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import utc_iso


def _schedule_row(database, portfolio, name):
    return database.execute("SELECT * FROM schedules WHERE portfolio_id = ? AND name = ?", (portfolio, name)).fetchone()


def test_active_schedule_changes_due_tasks_and_rollback_restores_baseline_after_restart(tmp_path):
    stack = lifecycle_stack(
        tmp_path,
        additional_files={
            "artifacts/schedules.json": json.dumps({"schema_version": 1, "interval_seconds": {"research": 60}}),
        },
    )
    scheduler = Scheduler(stack.database, stack.clock)
    runtime = ArtifactRuntime(stack.versions, scheduler)
    scheduler.ensure_schedule(stack.portfolio, "owner-review", 120, "coalesce")
    baseline = stack.versions.begin(stack.portfolio, "schedule-fixture", reconcile=lambda: None)
    runtime.apply_schedules(stack.portfolio, baseline)
    assert _schedule_row(stack.database, stack.portfolio, "artifact-research-review")["interval_seconds"] == 60
    first = runtime.coalesce_due(stack.portfolio, "research")
    assert first is not None
    assert runtime.coalesce_due(stack.portfolio, "research") is None
    result = candidate(
        stack, tmp_path,
        files={
            "artifacts/context_policy.json": json.dumps({**POLICY, "max_general_lessons": 5}),
            "artifacts/schedules.json": json.dumps({"schema_version": 1, "interval_seconds": {"research": 10}}),
        },
    )
    bundle = activate(stack, result)
    runtime.apply_schedules(stack.portfolio, bundle)
    updated = _schedule_row(stack.database, stack.portfolio, "artifact-research-review")
    assert updated["interval_seconds"] == 10
    assert _schedule_row(stack.database, stack.portfolio, "owner-review")["interval_seconds"] == 120
    # Start checking at the declared active schedule deadline, independent of wall clock.
    stack.database.execute(
        "UPDATE schedules SET next_due_at = ? WHERE schedule_id = ?",
        (utc_iso(stack.clock.now()), updated["schedule_id"]),
    )
    current_task = runtime.coalesce_due(stack.portfolio, "research")
    assert current_task is not None
    row = stack.database.execute("SELECT expected_version FROM tasks WHERE task_id = ?", (current_task,)).fetchone()
    assert row[0] == result.content_hash
    stack.database.close()
    reopened = Database(tmp_path / "lifecycle.sqlite")
    versions = VersionController(reopened, stack.clock)
    restored_runtime = ArtifactRuntime(versions, Scheduler(reopened, stack.clock))
    reloaded = versions.begin(stack.portfolio, "restarted-schedule", reconcile=lambda: None)
    restored_runtime.apply_schedules(stack.portfolio, reloaded)
    assert _schedule_row(reopened, stack.portfolio, "artifact-research-review")["interval_seconds"] == 10
    versions.rollback(stack.portfolio, stack.baseline, "v1")
    reverted = versions.begin(stack.portfolio, "restarted-schedule", reconcile=lambda: None)
    restored_runtime.apply_schedules(stack.portfolio, reverted)
    assert _schedule_row(reopened, stack.portfolio, "artifact-research-review")["interval_seconds"] == 60
    assert _schedule_row(reopened, stack.portfolio, "owner-review")["interval_seconds"] == 120
    reopened.close()


def test_report_layout_uses_activated_bytes_and_returns_to_baseline_after_rollback(tmp_path):
    stack = lifecycle_stack(
        tmp_path,
        additional_files={
            "artifacts/report_layout.json": json.dumps(
                {"schema_version": 1, "sections": ["summary", "costs", "risks"]},
            ),
        },
    )
    runtime = ArtifactRuntime(stack.versions, Scheduler(stack.database, stack.clock))
    sections = {"summary": "State", "costs": {"receipts": 1}, "risks": ["unproven"], "engineering": "ready"}
    assert list(runtime.render_report(stack.portfolio, sections)) == ["summary", "costs", "risks"]
    result = candidate(
        stack, tmp_path,
        files={
            "artifacts/context_policy.json": json.dumps({**POLICY, "max_general_lessons": 5}),
            "artifacts/report_layout.json": json.dumps(
                {"schema_version": 1, "sections": ["costs", "risks", "summary"]},
            ),
        },
    )
    activate(stack, result)
    ordered = runtime.render_report(stack.portfolio, sections)
    assert list(ordered) == ["costs", "risks", "summary"]
    assert ordered["costs"] == {"receipts": 1}
    assert ordered["risks"] == ["unproven"]
    stack.versions.rollback(stack.portfolio, stack.baseline, "v1")
    assert list(runtime.render_report(stack.portfolio, sections)) == ["summary", "costs", "risks"]


def test_rollback_removes_only_candidate_schedule_namespace(tmp_path):
    stack = lifecycle_stack(
        tmp_path, allowed_paths=["artifacts/context_policy.json", "artifacts/schedules.json"],
    )
    scheduler = Scheduler(stack.database, stack.clock)
    scheduler.ensure_schedule(stack.portfolio, "owner-review", 120, "coalesce")
    runtime = ArtifactRuntime(stack.versions, scheduler)
    result = candidate(
        stack, tmp_path,
        files={
            "artifacts/context_policy.json": json.dumps({**POLICY, "max_general_lessons": 5}),
            "artifacts/schedules.json": json.dumps({"schema_version": 1, "interval_seconds": {"research": 10}}),
        },
    )
    bundle = activate(stack, result)
    runtime.apply_schedules(stack.portfolio, bundle)
    assert _schedule_row(stack.database, stack.portfolio, "artifact-research-review") is not None
    stack.versions.rollback(stack.portfolio, stack.baseline, "v1")
    restored = stack.versions.begin(stack.portfolio, "schedule-fixture", reconcile=lambda: None)
    runtime.apply_schedules(stack.portfolio, restored)
    assert _schedule_row(stack.database, stack.portfolio, "artifact-research-review") is None
    assert _schedule_row(stack.database, stack.portfolio, "owner-review")["interval_seconds"] == 120
