"""Private evidence navigation uses scoped, immutable journal records and real checks."""

import json
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from tests.integration.test_version_lifecycle import activate, candidate, failed_turn, lifecycle_stack

from trade_graph.adapters.persistence.db import Database
from trade_graph.api.evidence import change, changes, decision, decisions, events, lessons, organization, research
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import NotFound, ValidationFailure


@pytest.fixture
def runtime(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 2, tzinfo=UTC))
    database = Database(tmp_path / "evidence.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    other = ledger.create_portfolio(reporting_currency="EUR")
    yield SimpleNamespace(database=database, clock=clock, ledger=ledger, portfolio_id=portfolio, other=other)
    database.close()


def _insert(runtime, table, **values):
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    runtime.database.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values()))


def _finding(runtime, finding_id, *, portfolio=None, expires="2026-01-03T00:00:00Z", **doc):
    _insert(
        runtime,
        "findings",
        finding_id=finding_id,
        portfolio_id=portfolio if portfolio is not None else runtime.portfolio_id,
        document_json=json.dumps(
            {
                "record_id": finding_id,
                "claim": "Market note",
                "relevance": "source",
                "counterevidence": "A competing source disagrees",
                **doc,
            }
        ),
        source_hash=f"hash-{finding_id}",
        available_at="2026-01-01T00:00:00Z",
        expires_at=expires,
        created_at="2026-01-01T00:00:00Z",
    )


def _lesson(runtime, revision_id, *, lesson_id="lesson-1", revision=1, portfolio=None, **doc):
    _insert(
        runtime,
        "lessons",
        revision_id=revision_id,
        lesson_id=lesson_id,
        portfolio_id=portfolio if portfolio is not None else runtime.portfolio_id,
        revision=revision,
        document_json=json.dumps(
            {
                "record_id": revision_id,
                "lesson_id": lesson_id,
                "observation": "Small sample",
                "counterexamples": ["counter-case"],
                "linked_decisions": [],
                **doc,
            }
        ),
        status="active",
        created_at="2026-01-01T00:00:00Z",
    )


def _decision(runtime, decision_id="decision-1", *, portfolio=None, snapshot="snapshot-1", **doc):
    portfolio = portfolio if portfolio is not None else runtime.portfolio_id
    _insert(
        runtime,
        "decisions",
        decision_id=decision_id,
        portfolio_id=portfolio,
        action="hold",
        payload_json=json.dumps(
            {"record_id": decision_id, "action": "hold", "rationale": "Wait for evidence", "evidence_refs": [], **doc}
        ),
        mandate_revision="1",
        policy_revision="policy-1",
        snapshot_id=snapshot,
        system_version_id="artifact-1",
        created_at="2026-01-01T00:00:00Z",
        task_id="task-1",
    )


def test_organization_projects_actual_assignments_leases_pause_and_triggers(runtime):
    scheduler = Scheduler(runtime.database, runtime.clock)
    task = scheduler.add_task(
        role="research",
        objective="Refresh source evidence",
        portfolio_id=runtime.portfolio_id,
        deadline_at="2026-01-01T00:00:00Z",
        payload={"event_id": "trigger-1", "api_key": "sk-key"},
    )
    foreign = scheduler.add_task(role="engineer", objective="Foreign private work", portfolio_id=runtime.other)
    runtime.database.execute("UPDATE tasks SET status = 'BLOCKED_BUDGET' WHERE task_id = ?", (task,))
    _insert(runtime, "dashboard_control_state", scope=f"leader:{runtime.portfolio_id}", revision=3)
    leased = scheduler.add_task(role="trader", objective="Decision opportunity", portfolio_id=runtime.portfolio_id)
    scheduler.claim("real-worker", roles={"trader"})
    _insert(
        runtime,
        "active_versions",
        portfolio_id=runtime.portfolio_id,
        version_id="v1",
        artifact_hash="artifact-1",
        fingerprint_json='{"artifact":"artifact-1"}',
        activated_at=utc_iso(runtime.clock.now()),
        generation=2,
    )
    _insert(
        runtime,
        "mandates",
        mandate_id="m1",
        portfolio_id=runtime.portfolio_id,
        revision=1,
        document_json='{"symbols":["BTC/USD"],"resource_note":"Approved bounds"}',
        active=1,
        expires_at="2026-01-01T00:00:00Z",
        created_at=utc_iso(runtime.clock.now()),
    )
    _insert(
        runtime,
        "pause_states",
        portfolio_id=runtime.portfolio_id,
        profile="MANAGE_ONLY",
        originator="owner",
        reason="Review",
        scope="portfolio",
        requested_at=utc_iso(runtime.clock.now()),
        achieved="MANAGE_ONLY",
        details_json='{"reconciliation":"continues"}',
    )
    _insert(
        runtime,
        "activity_events",
        event_id="trigger-1",
        portfolio_id=runtime.portfolio_id,
        kind="research_expiring",
        payload_json='{"finding_id":"f1"}',
        created_at=utc_iso(runtime.clock.now()),
        hash="hash-1",
        prev_hash=None,
    )
    result = organization(runtime)
    assert result["leader_revision"] == 3
    assert result["counts"] == {"active": 2, "blocked": 1, "leased": 1, "overdue": 1}
    by_id = {item["task_id"]: item for item in result["tasks"]}
    assert by_id[task]["blocked"] and by_id[task]["overdue"]
    assert by_id[task]["trigger"] == {"event_id": "trigger-1"}
    assert by_id[leased]["lease_owner"] == "real-worker"
    assert not by_id[leased]["lease_expired"]
    assert foreign not in by_id
    assert result["artifact_hash"] == "artifact-1" and result["version"]["generation"] == 2
    assert result["mandate"]["expired"] is True
    assert result["pause"]["profile"] == "MANAGE_ONLY"
    assert "trigger-1" in [event["event_id"] for event in result["trigger_events"]]
    assert "lease_token" not in json.dumps(result)
    runtime.clock.advance(31)
    assert next(t for t in organization(runtime)["tasks"] if t["task_id"] == leased)["lease_expired"]


def test_same_timestamp_pagination_is_stable_and_scoped(runtime):
    scheduler = Scheduler(runtime.database, runtime.clock)
    for n in range(4):
        scheduler.add_task(role="research", objective=f"Source {n}", portfolio_id=runtime.portfolio_id)
        _finding(runtime, f"f{n}")
        _lesson(runtime, f"r{n}", lesson_id=f"l{n}")
        _decision(runtime, f"d{n}")
    scheduler.add_task(role="research", objective="Foreign", portfolio_id=runtime.other)
    _finding(runtime, "foreign", portfolio=runtime.other)
    _lesson(runtime, "foreign", lesson_id="foreign", portfolio=runtime.other)
    _decision(runtime, "foreign", portfolio=runtime.other)
    for projection, key, identifier in (
        (organization, "tasks", "task_id"),
        (research, "findings", "finding_id"),
        (lessons, "lessons", "revision_id"),
        (decisions, "decisions", "decision_id"),
    ):
        first, second = projection(runtime, 2), projection(runtime, 2, 2)
        assert first["total"] == second["total"] == 4
        assert first["next_offset"] == 2 and second["next_offset"] is None
        combined = [item[identifier] for item in first[key] + second[key]]
        assert len(set(combined)) == 4
        assert combined == sorted(combined, reverse=True)
        assert projection(runtime, 2)[key] == first[key]
        assert projection(runtime, 2, 50)[key] == []


def test_research_freshness_and_lesson_revisions_preserve_counterevidence(runtime):
    _finding(runtime, "fresh")
    _finding(runtime, "stale", expires="2026-01-01T23:59:59Z")
    _finding(runtime, "untrusted", relevance="untrusted-page")
    _lesson(runtime, "r1", revision=1)
    _lesson(runtime, "r2", revision=2, counterexamples=["counter-case", "later-counter-case"], supersedes="r1")
    findings = {f["finding_id"]: f for f in research(runtime)["findings"]}
    assert findings["fresh"]["fresh"] and not findings["fresh"]["stale"]
    assert findings["stale"]["stale"] and not findings["stale"]["fresh"]
    assert not findings["untrusted"]["fresh"]
    revisions = {r["revision_id"]: r for r in lessons(runtime)["lessons"]}
    assert not revisions["r1"]["latest"] and revisions["r2"]["latest"]
    assert revisions["r2"]["counterexamples"] == ["counter-case", "later-counter-case"]
    assert revisions["r2"]["supersedes"] == "r1"


def test_decision_navigation_joins_only_scoped_snapshot_orders_fills_and_evidence(runtime):
    scheduler = Scheduler(runtime.database, runtime.clock)
    task = scheduler.add_task(role="trader", objective="Inspect a thesis", portfolio_id=runtime.portfolio_id)
    _finding(runtime, "f1")
    _finding(runtime, "foreign-f", portfolio=runtime.other, claim="Foreign market secret")
    _lesson(runtime, "r1", linked_decisions=["decision-1"])
    _lesson(runtime, "foreign-r", portfolio=runtime.other, lesson_id="foreign", observation="Foreign lesson secret")
    _decision(runtime, evidence_refs=["f1", "foreign-f"])
    runtime.database.execute("UPDATE decisions SET task_id = ? WHERE decision_id = 'decision-1'", (task,))
    _insert(
        runtime,
        "snapshots",
        snapshot_id="snapshot-1",
        portfolio_id=runtime.portfolio_id,
        as_of="2026-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        payload_json=json.dumps(
            {
                "artifact": {"version_id": "v1", "artifact_hash": "artifact-1", "generation": 1},
                "evidence_refs": ["f1", "foreign-f"],
                "selected_context": {
                    "always_include": ["active_safety"],
                    "lessons": [
                        {"record_id": "r1"},
                        {"record_id": "foreign-r", "observation": "Foreign embedded secret"},
                    ],
                },
                "messages": ["Never expose raw model conversations"],
                "request_json": "raw provider request",
                "source_instruction": "hidden prompt",
            }
        ),
    )
    _insert(runtime, "version_history", portfolio_id=runtime.portfolio_id, version_id="v1", artifact_hash="artifact-1")
    _insert(runtime, "version_history", portfolio_id=runtime.other, version_id="foreign", artifact_hash="artifact-1")
    for intent, portfolio in (("i1", runtime.portfolio_id), ("foreign-i", runtime.other)):
        _insert(
            runtime,
            "order_intents",
            intent_id=intent,
            portfolio_id=portfolio,
            client_order_id=f"c-{intent}",
            state="FILLED",
            symbol="BTC/USD",
            payload_json=json.dumps(
                {"decision_id": "decision-1", "quantity": "0.1", "side": "buy", "account_id": "Private account"}
            ),
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
    for fill, portfolio, intent in (
        ("fill-1", runtime.portfolio_id, "i1"),
        ("foreign-fill", runtime.other, "i1"),
        ("foreign-order-fill", runtime.portfolio_id, "foreign-i"),
    ):
        _insert(
            runtime,
            "fills",
            fill_id=fill,
            portfolio_id=portfolio,
            intent_id=intent,
            venue="paper",
            account_id="Private account",
            trade_id=fill,
            document_json=json.dumps(
                {
                    "symbol": "BTC/USD",
                    "quantity": "0.1",
                    "price": "100",
                    "fee_amount": "0.026",
                    "fee_asset": "USD",
                    "account_id": "Private account",
                }
            ),
            created_at="2026-01-01T00:00:00Z",
        )
    result = decision(runtime, "decision-1")
    assert result["task"]["task_id"] == task
    assert [f["finding_id"] for f in result["research"]] == ["f1"]
    assert [r["revision_id"] for r in result["lessons"]] == ["r1"]
    assert [o["intent_id"] for o in result["orders"]] == ["i1"]
    assert [f["fill_id"] for f in result["fills"]] == ["fill-1"]
    assert result["versions"] == [{"version_id": "v1", "artifact_hash": "artifact-1"}]
    assert result["snapshot"]["selected_context"]["lesson_revision_ids"] == ["r1"]
    encoded = json.dumps(result)
    for secret in (
        "Foreign market secret",
        "Foreign lesson secret",
        "Foreign embedded secret",
        "Private account",
        "raw provider request",
        "hidden prompt",
        "raw model conversations",
    ):
        assert secret not in encoded


def test_foreign_or_missing_detail_is_not_found_and_broken_links_are_visible(runtime):
    _decision(runtime, "foreign-decision", portfolio=runtime.other)
    _decision(runtime, "broken", snapshot="missing-snapshot")
    assert decision(runtime, "broken")["evidence_missing"] == ["missing-snapshot"]
    for identifier in ("foreign-decision", "unknown"):
        with pytest.raises(NotFound):
            decision(runtime, identifier)
        with pytest.raises(NotFound):
            change(runtime, identifier)
    _insert(
        runtime,
        "snapshots",
        snapshot_id="foreign-snapshot",
        portfolio_id=runtime.other,
        as_of="2026-01-01T00:00:00Z",
        payload_json='{"artifact":{"artifact_hash":"private"}}',
        created_at="2026-01-01T00:00:00Z",
    )
    _decision(runtime, "cross-linked", snapshot="foreign-snapshot")
    assert decision(runtime, "cross-linked")["snapshot"] is None


def test_events_are_scoped_stable_paginated_and_recursively_redacted(runtime):
    runtime.database.execute("DELETE FROM activity_events")
    for identifier, portfolio in (
        ("e1", runtime.portfolio_id),
        ("e2", runtime.portfolio_id),
        ("foreign", runtime.other),
    ):
        _insert(
            runtime,
            "activity_events",
            event_id=identifier,
            portfolio_id=portfolio,
            kind="incident",
            payload_json=json.dumps(
                {
                    "details": {
                        "api_key": "sk-private",
                        "conversation": ["private thoughts"],
                        "note": "stage /tmp/private/secret",
                        "account_id": "owner-account",
                    }
                }
            ),
            created_at="2026-01-01T00:00:00Z",
            hash=identifier,
        )
    _insert(
        runtime,
        "version_events",
        event_id="v1",
        portfolio_id=runtime.portfolio_id,
        kind="activation",
        from_hash="old",
        to_hash="new",
        created_at="2026-01-01T00:00:00Z",
        details_json='{"generation":1}',
    )
    first, second = events(runtime, 2), events(runtime, 2, 2)
    assert first["total"] == 3 and first["next_offset"] == 2
    assert second["next_offset"] is None
    assert [e["event_id"] for e in first["events"] + second["events"]] == ["v1", "e2", "e1"]
    encoded = json.dumps(first) + json.dumps(second)
    for secret in ("sk-private", "private thoughts", "/tmp/private", "owner-account", '"foreign"'):
        assert secret not in encoded


def test_change_detail_reads_real_independent_attestation_artifacts_and_observations(tmp_path):
    stack = lifecycle_stack(tmp_path)
    result = candidate(stack, tmp_path)
    bundle = activate(stack, result)
    failed = failed_turn(stack, bundle)
    stack.versions.observe(stack.portfolio, bundle, failed, ok=False, reason="Retained failure")
    runtime = SimpleNamespace(database=stack.database, clock=stack.clock, portfolio_id=stack.portfolio)
    shutil.rmtree(stack.source)
    shutil.rmtree(tmp_path / "candidate")
    projected = change(runtime, result.candidate_id)
    assert projected["attestation"]["exit_code"] == 0
    assert projected["attestation"]["report"]["isolation"]["verified"] is True
    assert projected["candidate"]["content_hash"] == result.content_hash
    artifact = projected["artifacts"][0]
    assert artifact["path"] == "artifacts/context_policy.json"
    assert json.loads(artifact["before"])["max_general_lessons"] == 8
    assert json.loads(artifact["after"])["max_general_lessons"] == 5
    assert "before/artifacts/context_policy.json" in artifact["diff"]
    assert projected["observations"][0]["observation_id"] == failed
    assert projected["observations"][0]["model_ok"] is False
    assert projected["rollouts"][0]["state"] == "ROLLBACK_PENDING"
    assert "rollback_requested" in [e["kind"] for e in projected["events"]]
    assert str(tmp_path) not in json.dumps(projected)
    listing = changes(runtime)
    assert listing["total"] == 1 and listing["candidates"][0]["candidate_id"] == result.candidate_id
    leader_id = stack.database.execute("SELECT decision_id FROM leader_decisions").fetchone()["decision_id"]
    leader = decision(runtime, leader_id)
    assert leader["decision"]["kind"] == "leader"
    assert leader["decision"]["rationale"]
    assert {d["kind"] for d in decisions(runtime)["decisions"]} == {"leader"}
    foreign = stack.ledger.create_portfolio(reporting_currency="EUR")
    other_runtime = SimpleNamespace(database=stack.database, clock=stack.clock, portfolio_id=foreign)
    assert changes(other_runtime)["candidates"] == []
    with pytest.raises(NotFound):
        change(other_runtime, result.candidate_id)
    with pytest.raises(NotFound):
        decision(other_runtime, leader_id)


@pytest.mark.parametrize("projection", [organization, decisions, research, lessons, changes, events])
def test_pagination_bounds_are_enforced(runtime, projection):
    for limit, offset in ((0, 0), (201, 0), (True, 0), (1, -1), (1, True)):
        with pytest.raises(ValidationFailure):
            projection(runtime, limit, offset)
