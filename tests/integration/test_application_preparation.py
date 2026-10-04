"""Trusted bounded scheduling, actual confined health, durable recovery and rollback."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from tests.integration.test_application_engineering import KEY, flow
from tests.integration.test_application_projection import V2, failing_late, force_failure_nonce
from tests.integration.test_protected_financial_runtime import ENTER, activate

from trade_graph.adapters.engineering.plugin_artifacts import canonical_bytes, sha256
from trade_graph.adapters.engineering.plugin_runtime import runtime_environment_sha256
from trade_graph.application.application_preparation import (
    EXPERIMENT,
    ApplicationPreparationLoop,
    ApplicationPreparationPolicy,
    application_preparation_controller_sha256,
)
from trade_graph.application.application_projection import retain_projection_corpus
from trade_graph.domain.errors import AuthorityDenied


@pytest.fixture(scope="module")
def environment_pin():
    return runtime_environment_sha256()


@pytest.fixture(autouse=True)
def restore_private_modes(tmp_path):
    yield
    for directory, _, _ in os.walk(tmp_path, followlinks=False):
        Path(directory).chmod(0o700)


def prepare(current, *, grant_experiment=True, healthy=True):
    if grant_experiment:
        current.db.execute("UPDATE portfolios SET experiment_id=? WHERE portfolio_id=?", (EXPERIMENT, current.pid))
    cases = json.loads(canonical_bytes(current.projection._corpus()["cases"]))
    if not healthy:
        cases[0]["expected_v2"]["title"] = "Independent health requires a different plain title"
    health = retain_projection_corpus(current.engineer.store, cases)
    policy = ApplicationPreparationPolicy(portfolio_id=current.pid, worker_task_id=current.tid,
                                          commission_policy_sha256=current.engineer.policy_pin,
                                          health_corpus_sha256=health,
                                          health_case_sha256s=[sha256(canonical_bytes(case["snapshot"]))
                                                               for case in cases])
    arguments = {"policy": policy, "expected_policy_sha256": policy.sha256,
                 "expected_controller_sha256": application_preparation_controller_sha256(),
                 "receipt_key": KEY, "owner": "fixture-worker", "execution": current.execution}
    loop = ApplicationPreparationLoop(current.engineer, current.handler, **arguments)
    return loop, arguments


def step(loop):
    return asyncio.run(loop.advance())


def reopen(current, loop, arguments):
    loop.close()
    current.clock.advance(121)
    return ApplicationPreparationLoop(current.engineer, current.handler,
                                      **{**arguments, "owner": "restarted-workflow-owner"})


def test_actual_one_step_route_activates_compatible_sidecar_and_restarts_without_effect_repetition(
    tmp_path, environment_pin, monkeypatch
):
    current = flow(tmp_path, environment_pin)
    loop, arguments = prepare(current)
    before = [dict(row) for row in current.db.execute("SELECT * FROM ledger_events")]
    state = step(loop)
    assert state["status"] == "ACTIVE"
    assert current.row()["status"] == "SUCCEEDED"
    assert len(current.gateway.attempts) == len(current.receipts()) == 1
    assert current.projection.status()["phase"] == "EXPANDED"
    assert current.projection.status()["active_build"] == state["build_sha256"]
    assert len(current.projection.read(version=1)) == 2
    assert current.projection.read(version=2)[-1]["projection"]["title"] == "Grouped reports"
    samples = loop._samples()
    assert len(samples) == 1 and samples[0]["status"] == "COMPLETED" and samples[0]["matched"]
    assert samples[0]["commission_receipt_sha256"] == state["commission_receipt_sha256"]
    assert [dict(row) for row in current.db.execute("SELECT * FROM ledger_events")] == before
    assert current.versions.current_hash(current.pid) == current.baseline
    restored = reopen(current, loop, arguments)
    monkeypatch.setattr(current.projection, "_evaluate", lambda *a: pytest.fail("repeated completed health child"))
    monkeypatch.setattr(current.gateway.scripted, "complete", lambda *a: pytest.fail("repeated model generation"))
    assert step(restored)["status"] == "ACTIVE"
    assert len(current.projection.read(version=1)) == 2
    restored.close()


def test_independent_health_mismatch_rolls_back_source_retaining_actual_application_records(tmp_path, environment_pin):
    current = flow(tmp_path, environment_pin)
    loop, _ = prepare(current, healthy=False)
    state = step(loop)
    assert state["status"] == "ROLLED_BACK"
    assert state["failure"] == "independent_functional_health_rejected"
    assert current.projection.status()["active_build"] == current.engineer.policy.baseline_build_sha256
    assert len(current.projection.read(version=1)) == 2
    assert current.projection.read(version=2)[-1]["projection"]["title"] == "Grouped reports"
    assert len(current.receipts()) == 1
    assert current.versions.current_hash(current.pid) == current.baseline
    loop.close()


def test_actual_confined_runtime_failure_rolls_back_and_preserves_financial_fill_management(
    tmp_path, environment_pin, monkeypatch
):
    current = flow(tmp_path, environment_pin, source=failing_late())
    activate(current.runtime, ENTER)
    quote = current.execution.latest_observation("BTC/USD", current.execution.now())
    current.execution.save_observation(quote.model_copy(update={
        "observation_id": "fresh-commission-quote", "event_time_utc": current.clock.now(),
        "available_at_utc": current.clock.now(),
    }))
    entered = asyncio.run(current.runtime.cycle(current.pid, "BTC/USD"))
    intent = entered["response"]["intent_id"]
    quote = current.execution.latest_observation("BTC/USD", current.execution.now())
    current.execution.on_observation(quote.model_copy(update={"observation_id": "commission-projection-fill"}))
    assert current.execution.intent_state(intent) == "FILLED"
    before = {table: [dict(row) for row in current.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
              for table in ("ledger_events", "fills", "order_intents", "position_reservations")}
    validate = current.projection.validate

    def fail_after_validation(build):
        receipt = validate(build)
        force_failure_nonce(monkeypatch)
        return receipt

    monkeypatch.setattr(current.projection, "validate", fail_after_validation)
    loop, _ = prepare(current)
    state = step(loop)
    assert state["status"] == "ROLLED_BACK" and state["recovery"] == "ROLLED_BACK_ALREADY"
    assert current.projection.status()["active_build"] == current.engineer.policy.baseline_build_sha256
    assert len(current.projection.read(version=1)) == 1
    assert current.execution.owned_quantity(current.pid, "BTC") > 0
    assert step(loop)["status"] == "ROLLED_BACK", "financial reconciliation still runs for a terminal application"
    for table, rows in before.items():
        assert [dict(row) for row in current.db.execute(f"SELECT * FROM {table} ORDER BY rowid")] == rows
    assert len(current.receipts()) == 1
    loop.close()


@pytest.mark.parametrize("after_effect", [False, True])
def test_interrupted_health_never_reruns_candidate_as_success(tmp_path, environment_pin, monkeypatch, after_effect):
    current = flow(tmp_path, environment_pin)
    loop, arguments = prepare(current)
    render = current.projection.render

    class Crash(BaseException):
        pass

    def crash(snapshot):
        if after_effect:
            assert render(snapshot)["status"] == "RENDERED"
        raise Crash

    monkeypatch.setattr(current.projection, "render", crash)
    with pytest.raises(Crash):
        step(loop)
    assert loop._samples()[0]["status"] == "STARTED"
    rows = len(current.projection.read(version=1))
    assert rows == (2 if after_effect else 1)
    restored = reopen(current, loop, arguments)
    monkeypatch.setattr(current.projection, "render", lambda *a: pytest.fail("reran interrupted health candidate"))
    monkeypatch.setattr(current.gateway.scripted, "complete", lambda *a: pytest.fail("reran model generation"))
    state = step(restored)
    assert state["status"] == "ROLLED_BACK" and state["failure"] == "interrupted_or_rejected_health"
    assert len(current.projection.read(version=1)) == rows
    assert len(current.receipts()) == 1
    restored.close()


def test_activation_checkpoint_crash_recovers_exact_pointer_without_model_or_validation_repeat(
    tmp_path, environment_pin, monkeypatch
):
    current = flow(tmp_path, environment_pin)
    loop, arguments = prepare(current)
    save = loop._save_state

    class Crash(BaseException):
        pass

    def crash(state):
        if state["status"] == "HEALTH":
            raise Crash
        return save(state)

    monkeypatch.setattr(loop, "_save_state", crash)
    with pytest.raises(Crash):
        step(loop)
    assert loop.state()["status"] == "ACTIVATING" and current.projection.status()["generation"] == 2
    restored = reopen(current, loop, arguments)
    monkeypatch.setattr(current.projection, "validate", lambda *a: pytest.fail("repeated retained validation"))
    monkeypatch.setattr(current.gateway.scripted, "complete", lambda *a: pytest.fail("repeated gateway generation"))
    assert step(restored)["status"] == "ACTIVE"
    assert len(current.projection.read(version=1)) == 2
    restored.close()


@pytest.mark.parametrize("kind", ["source", "cost", "allocation"])
def test_active_source_or_complete_cost_drift_triggers_deterministic_rollback(tmp_path, environment_pin, kind):
    current = flow(tmp_path, environment_pin)
    loop, _ = prepare(current)
    state = step(loop)
    assert state["status"] == "ACTIVE"
    if kind == "source":
        path = current.engineer.store.root / "stages" / state["build_sha256"] / "source.py"
        path.chmod(0o600)
        path.write_text(V2 + "\n# changed sealed source\n")
        path.chmod(0o400)
    elif kind == "cost":
        current.db.execute("UPDATE usage_receipts SET usage_json='{}' WHERE reservation_id=("
                           "SELECT reservation_id FROM model_invocations WHERE task_id=?)", (current.tid,))
    else:
        current.db.execute("UPDATE cost_allocations SET amount='0' WHERE receipt_id IN("
                           "SELECT receipt_id FROM usage_receipts WHERE reservation_id=("
                           "SELECT reservation_id FROM model_invocations WHERE task_id=?))", (current.tid,))
    assert step(loop)["status"] == "ROLLED_BACK"
    assert current.projection.status()["active_build"] == current.engineer.policy.baseline_build_sha256
    assert len(current.projection.read(version=1)) == 2
    loop.close()


def test_scope_competition_wrong_corpus_keys_and_journal_tampering_refuse(tmp_path, environment_pin):
    current = flow(tmp_path, environment_pin)
    with pytest.raises(AuthorityDenied, match="exact synthetic"):
        prepare(current, grant_experiment=False)
    loop, arguments = prepare(current)
    current.office.scheduler.add_task(role="engineer", objective="unrelated", portfolio_id=current.pid)
    with pytest.raises(AuthorityDenied, match="competing Engineer"):
        step(loop)
    assert len(current.gateway.attempts) == 0
    with pytest.raises(PermissionError, match="authentication"):
        ApplicationPreparationLoop(current.engineer, current.handler,
                                   **{**arguments, "receipt_key": b"different-workflow-key-32bytes-value"})
    changed = loop.state()
    changed["status"] = "ACTIVE"
    loop.db.execute("UPDATE workflow_state SET payload_json=? WHERE id=1", (json.dumps(changed),))
    with pytest.raises(AuthorityDenied, match="payload changed"):
        loop.state()
    loop.close()


def test_process_lease_bounds_route_to_one_owner_and_health_case(tmp_path, environment_pin):
    current = flow(tmp_path, environment_pin)
    loop, arguments = prepare(current)
    assert step(loop)["status"] == "ACTIVE"
    other = ApplicationPreparationLoop(current.engineer, current.handler, **{**arguments, "owner": "competing-owner"})
    assert step(other)["status"] == "LEASED_ELSEWHERE"
    assert len(current.projection.read(version=1)) == 2 and len(current.receipts()) == 1
    other.close()
    loop.close()


def test_lost_gateway_response_remains_unknown_without_activation_or_reissue(tmp_path, environment_pin, monkeypatch):
    current = flow(tmp_path, environment_pin)
    loop, arguments = prepare(current)

    class Lost(BaseException):
        pass

    monkeypatch.setattr(current.gateway.scripted, "complete", lambda _: (_ for _ in ()).throw(Lost()))
    with pytest.raises(Lost):
        step(loop)
    restored = reopen(current, loop, arguments)
    monkeypatch.setattr(current.gateway.scripted, "complete", lambda *a: pytest.fail("reissued unknown generation"))
    assert step(restored)["status"] == "WAITING_EXTERNAL"
    invocation = current.db.execute("SELECT * FROM model_invocations WHERE task_id=?", (current.tid,)).fetchone()
    assert current.db.execute("SELECT state FROM budget_reservations WHERE reservation_id=?",
                              (invocation["reservation_id"],)).fetchone()[0] == "UNCERTAIN"
    assert current.projection.status()["phase"] == "LEGACY" and len(current.receipts()) == 0
    restored.close()


def test_changed_cost_allocation_blocks_activation_without_refund_or_redispatch(tmp_path, environment_pin, monkeypatch):
    current = flow(tmp_path, environment_pin)
    assert current.run() == 1 and current.output()["_status"] == "SUCCEEDED"
    receipt = current.receipts()[0]
    current.db.execute("UPDATE cost_allocations SET amount='0' WHERE receipt_id=?", (receipt["receipt_id"],))
    loop, _ = prepare(current)
    monkeypatch.setattr(current.gateway.scripted, "complete", lambda *a: pytest.fail("reissued paid generation"))
    with pytest.raises(AuthorityDenied, match="billing evidence changed"):
        step(loop)
    assert current.projection.status()["phase"] == "LEGACY"
    assert current.projection.status()["active_build"] == current.engineer.policy.baseline_build_sha256
    assert current.db.execute("SELECT state FROM budget_reservations WHERE reservation_id=?",
                              (receipt["reservation_id"],)).fetchone()[0] == "COMMITTED"
    assert len(current.receipts()) == 1
    loop.close()


def test_failed_generation_retains_its_usage_without_activation_or_automatic_refund(tmp_path, environment_pin):
    current = flow(tmp_path, environment_pin, max_steps=1)
    current.gateway.scripted.outputs["engineer"] = {"files": [], "summary": "malformed bounded output"}
    loop, _ = prepare(current)
    assert step(loop)["status"] == "FAILED"
    assert len(current.receipts()) == len(current.gateway.attempts) == 1
    receipt = current.receipts()[0]
    assert current.db.execute("SELECT state FROM budget_reservations WHERE reservation_id=?",
                              (receipt["reservation_id"],)).fetchone()[0] == "COMMITTED"
    assert current.projection.status()["phase"] == "LEGACY"
    assert step(loop)["status"] == "FAILED" and len(current.receipts()) == 1
    loop.close()
