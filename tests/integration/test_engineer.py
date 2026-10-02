"""Trusted artifact checks. A caller-supplied runner label is not provenance."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.leadership_support import commission

from trade_graph.adapters.engineering.provenance import scrubbed_env
from trade_graph.adapters.engineering.runner import EngineerRunner
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.engineer import ArtifactEngineer
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ChangeTask
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, ValidationFailure
from trade_graph.domain.money import Money

POLICY = {
    "schema_version": 1,
    "max_general_lessons": 5,
    "always_include": ["mandate_obligations", "active_safety"],
}


def _stack(tmp_path: Path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "engineer.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    source = tmp_path / "source"
    (source / "artifacts").mkdir(parents=True)
    (source / "artifacts" / "context_policy.json").write_text(
        json.dumps({**POLICY, "max_general_lessons": 8}), encoding="utf-8",
    )
    (source / ".env").write_text("OPENAI_API_KEY=sk-should-not-copy\n", encoding="utf-8")
    kernel = source / "src" / "trade_graph" / "kernel"
    kernel.mkdir(parents=True)
    (kernel / "books.py").write_text("secret = 1\n", encoding="utf-8")
    engineer = ArtifactEngineer(database, clock, source, ledger)
    versions = VersionController(database, clock)
    return clock, database, portfolio, source, engineer, versions


def _task(clock, portfolio: str, baseline: str, **updates) -> ChangeTask:
    body = {
        "record_id": "change-1",
        "created_at_utc": clock.now(),
        "run_id": "run",
        "task_id": "change-1",
        "root_task_id": "change-1",
        "portfolio_id": portfolio,
        "mode": "paper",
        "system_version_id": baseline,
        "trace_id": "change-1",
        "objective": "cap lessons",
        "baseline_version": "v1",
        "baseline_hash": baseline,
        "allowed_classes": ["artifact_config"],
        "allowed_paths": ["artifacts/context_policy.json"],
        "invariants": ["mandate_obligations"],
        "max_spend": Money(amount="1", currency="EUR"),
        "max_steps": 3,
        "test_plan": "trusted checks",
        "success_criteria": "exit 0",
        "rollback_criteria": "pointer only",
        "expires_at_utc": clock.now() + timedelta(days=1),
    }
    body.update(updates)
    return ChangeTask(**body)


def test_scrubbed_child_does_not_see_provider_keys(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    completed = subprocess.run(
        [sys.executable, "-c", "import json,os; print(json.dumps(sorted(os.environ)))"],
        env=scrubbed_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    names = set(json.loads(completed.stdout))
    assert "OPENAI_API_KEY" not in names
    assert "ANTHROPIC_API_KEY" not in names
    assert "PYTHONPATH" not in names


def test_worktree_excludes_secrets_and_shadowed_checks_do_not_pass(tmp_path: Path) -> None:
    clock, database, portfolio, source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    staged = list(Path(tmp_path / "base").rglob("*"))
    assert not any(path.name == ".env" or "kernel" in path.parts for path in staged)
    versions.ensure(portfolio, "v1", baseline)
    task = _task(clock, portfolio, baseline)
    commission(engineer, portfolio, task)
    destination = tmp_path / "stage"
    result = engineer.implement(
        portfolio,
        task.record_id,
        {"artifacts/context_policy.json": json.dumps(POLICY)},
        destination,
    )
    assert result.state == "READY"
    shadow = destination / "trade_graph" / "adapters" / "engineering"
    shadow.mkdir(parents=True)
    (shadow / "checks.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (destination / "artifacts" / "context_policy.json").write_text(
        json.dumps({**POLICY, "note": "curl http://169.254.169.254"}),
        encoding="utf-8",
    )
    forged = EngineerRunner(source).attest(destination)
    assert forged["exit_code"] != 0
    assert "runner" not in forged
    before = versions.current_hash(portfolio)
    with pytest.raises(ValidationFailure, match="trusted attestation"):
        versions.activate(
            portfolio,
            {
                "candidate_id": "forged",
                "baseline_hash": baseline,
                "content_hash": forged["content_hash"],
                "attestation": {"runner": "trusted-controller", "exit_code": 0, "content_hash": forged["content_hash"]},
            },
        )
    versions.activate(
        portfolio,
        {
            "candidate_id": result.candidate_id,
            "baseline_hash": baseline,
            "content_hash": result.content_hash,
            "attestation": {"runner": "candidate-prose"},
        },
    )
    assert versions.current_hash(portfolio) != before


def test_advice_protected_paths_and_failed_checks_leave_the_active_hash(tmp_path: Path) -> None:
    clock, database, portfolio, _source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(portfolio, "v1", baseline)
    task = _task(clock, portfolio, baseline, max_steps=5)
    commission(engineer, portfolio, task)
    with pytest.raises(AuthorityDenied):
        engineer.commission(
            portfolio,
            _task(clock, portfolio, baseline, record_id="kernel", allowed_classes=["kernel"]),
        )
    advice = engineer.implement(portfolio, task.record_id, {}, tmp_path / "advice")
    protected = engineer.implement(
        portfolio,
        task.record_id,
        {"src/trade_graph/kernel/books.py": "cash = 1"},
        tmp_path / "protected",
    )
    gates = engineer.implement(
        portfolio,
        task.record_id,
        {"tests/test_gates.py": "assert True"},
        tmp_path / "gates",
    )
    failed = engineer.implement(
        portfolio,
        task.record_id,
        {"artifacts/context_policy.json": json.dumps({"max_general_lessons": 5, "always_include": []})},
        tmp_path / "failed",
    )
    assert advice.state == protected.state == gates.state == failed.state == "FAILED"
    assert versions.current_hash(portfolio) == baseline
    rows = database.execute("SELECT COUNT(*) AS n FROM candidates").fetchone()["n"]
    events = database.execute(
        "SELECT COUNT(*) AS n FROM activity_events WHERE kind = 'engineer_candidate'"
    ).fetchone()["n"]
    assert rows == 4
    assert events == 4
    assert failed.content_hash is not None
    stored = database.execute(
        "SELECT exit_code FROM candidate_attestations WHERE content_hash = ?",
        (failed.content_hash,),
    ).fetchone()
    assert stored["exit_code"] != 0


def test_file_limit_is_visible_and_a_wrong_checks_hash_cannot_activate(tmp_path: Path) -> None:
    clock, database, portfolio, _source, engineer, versions = _stack(tmp_path)
    baseline = engineer.baseline(tmp_path / "base")
    versions.ensure(portfolio, "v1", baseline)
    task = _task(clock, portfolio, baseline, allowed_paths=["artifacts/"])
    commission(engineer, portfolio, task)
    files = {f"artifacts/note-{index}.json": "{}" for index in range(6)}
    limited = engineer.implement(portfolio, task.record_id, files, tmp_path / "limited")
    assert limited.state == "FAILED"
    assert limited.known_limits == "file limit exceeded"
    assert versions.current_hash(portfolio) == baseline
    database.execute(
        """INSERT INTO controller_attestations
        (attestation_id, content_hash, checks_module_hash, exit_code, command,
         stdout_sha256, stderr_sha256, manifest_json, created_at)
        VALUES ('fake', 'abc', 'not-the-module', 0, 'fake', 'a', 'b', '{}', '2026-01-01T00:00:00.000000Z')"""
    )
    with pytest.raises(ValidationFailure, match="trusted attestation"):
        versions.activate(
            portfolio,
            {
                "candidate_id": "fake",
                "baseline_hash": baseline,
                "content_hash": "abc",
                "attestation": {"runner": "trusted-controller"},
            },
        )
