"""Activation is a quiescent compare-and-set. Rollback restores the pointer only."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.engineer import ArtifactEngineer
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ChangeTask
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import StaleState, ValidationFailure
from trade_graph.domain.money import Money

POLICY = {
    "schema_version": 1,
    "max_general_lessons": 5,
    "always_include": ["mandate_obligations", "active_safety"],
}


def _ready(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "activation.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    source = tmp_path / "source"
    (source / "artifacts").mkdir(parents=True)
    engineer = ArtifactEngineer(database, clock, source, ledger)
    versions = VersionController(database, clock)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(portfolio, "v1", baseline)
    task = ChangeTask(
        record_id="change-1",
        created_at_utc=clock.now(),
        run_id="run",
        task_id="change-1",
        root_task_id="change-1",
        portfolio_id=portfolio,
        mode="paper",
        system_version_id=baseline,
        trace_id="change-1",
        objective="cap lessons",
        baseline_version="v1",
        baseline_hash=baseline,
        allowed_classes=["artifact_config"],
        allowed_paths=["artifacts/context_policy.json"],
        invariants=["mandate_obligations"],
        max_spend=Money(amount="1", currency="EUR"),
        max_steps=3,
        test_plan="trusted checks",
        success_criteria="exit 0",
        rollback_criteria="pointer only",
        expires_at_utc=clock.now() + timedelta(days=1),
    )
    engineer.commission(portfolio, task)
    result = engineer.implement(
        portfolio,
        task.record_id,
        {"artifacts/context_policy.json": json.dumps(POLICY)},
        tmp_path / "stage",
    )
    assert result.state == "READY"
    return database, portfolio, versions, baseline, result


def _candidate(result, baseline: str) -> dict:
    return {
        "candidate_id": result.candidate_id,
        "baseline_hash": baseline,
        "content_hash": result.content_hash,
        "attestation": {"runner": "trusted-controller"},
    }


def test_busy_trader_blocks_activation_and_stale_baseline_requires_revalidation(tmp_path) -> None:
    database, portfolio, versions, baseline, result = _ready(tmp_path)
    database.execute(
        """INSERT INTO tasks
        (task_id, root_task_id, portfolio_id, role, objective, status, priority, max_steps,
         max_attempts, attempts_used, input_json, created_at)
        VALUES ('busy', 'busy', ?, 'trader', 'decide', 'RUNNING', 0, 1, 1, 0, '{}',
                '2026-01-01T00:00:00.000000Z')""",
        (portfolio,),
    )
    with pytest.raises(ValidationFailure, match="quiescent"):
        versions.activate(portfolio, _candidate(result, baseline))
    assert versions.current_hash(portfolio) == baseline
    database.execute("UPDATE tasks SET status = 'SUCCEEDED' WHERE task_id = 'busy'")
    database.execute(
        """INSERT INTO tasks
        (task_id, root_task_id, portfolio_id, role, objective, status, priority, expected_version,
         max_steps, max_attempts, attempts_used, input_json, created_at)
        VALUES ('queued', 'queued', ?, 'trader', 'later', 'QUEUED', 0, ?, 1, 1, 0, '{}',
                '2026-01-01T00:00:00.000000Z')""",
        (portfolio, baseline),
    )
    versions.activate(portfolio, _candidate(result, baseline))
    assert versions.fingerprint(portfolio) == result.content_hash
    assert database.execute("SELECT status FROM tasks WHERE task_id = 'queued'").fetchone()["status"] == "CANCELLED"
    assert database.execute(
        "SELECT state FROM candidates WHERE candidate_id = ?",
        (result.candidate_id,),
    ).fetchone()["state"] == "OBSERVING"
    with pytest.raises(StaleState, match="baseline"):
        versions.activate(portfolio, _candidate(result, baseline))


def test_rollback_restores_the_pointer_and_keeps_fills_costs_and_decision_versions(tmp_path) -> None:
    database, portfolio, versions, baseline, result = _ready(tmp_path)
    versions.activate(portfolio, _candidate(result, baseline))
    new_hash = result.content_hash
    database.execute(
        """INSERT INTO decisions
        (decision_id, portfolio_id, action, payload_json, mandate_revision, policy_revision,
         snapshot_id, system_version_id, created_at)
        VALUES ('open', ?, 'enter', '{}', '1', '1', 'snap', ?, '2026-01-01T00:00:00.000000Z')""",
        (portfolio, baseline),
    )
    database.execute(
        """INSERT INTO decisions
        (decision_id, portfolio_id, action, payload_json, mandate_revision, policy_revision,
         snapshot_id, system_version_id, created_at)
        VALUES ('close', ?, 'exit', '{}', '1', '1', 'snap', ?, '2026-01-01T00:00:01.000000Z')""",
        (portfolio, new_hash),
    )
    database.execute(
        """INSERT INTO fills
        (fill_id, venue, account_id, trade_id, portfolio_id, document_json, created_at)
        VALUES ('fill-1', 'paper', 'paper', 'trade-1', ?, '{}', '2026-01-01T00:00:00.000000Z')""",
        (portfolio,),
    )
    database.execute(
        """INSERT INTO usage_receipts
        (receipt_id, reservation_id, provider, provider_request_id, model, native_cost,
         native_currency, reporting_cost, reporting_currency, status, usage_json, synthetic, created_at)
        VALUES ('rcpt-1', 'res-1', 'scripted', 'req-1', 'scripted', '0.01', 'EUR', '0.01', 'EUR',
                'committed', '{}', 1, '2026-01-01T00:00:00.000000Z')"""
    )
    versions.rollback(portfolio, baseline, "v1")
    assert versions.current_hash(portfolio) == baseline
    assert database.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"] == 1
    assert database.execute("SELECT COUNT(*) AS n FROM usage_receipts").fetchone()["n"] == 1
    opening = database.execute(
        "SELECT system_version_id FROM decisions WHERE decision_id = 'open'"
    ).fetchone()["system_version_id"]
    closing = database.execute(
        "SELECT system_version_id FROM decisions WHERE decision_id = 'close'"
    ).fetchone()["system_version_id"]
    assert opening == baseline
    assert closing == new_hash
    assert database.execute(
        "SELECT state FROM candidates WHERE candidate_id = ?",
        (result.candidate_id,),
    ).fetchone()["state"] == "ROLLED_BACK"
    with pytest.raises(ValidationFailure, match="unknown artifact"):
        versions.rollback(portfolio, "never-activated", "nope")
    database.close()
    reopened = Database(tmp_path / "activation.sqlite")
    restored = VersionController(reopened, FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)))
    assert restored.current_hash(portfolio) == baseline
    assert reopened.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"] == 1
    reopened.close()
