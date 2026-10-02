"""Dashboard boundary tests: private data, coherent snapshots and honest health."""

import json

from tests.integration.test_api_security_live import _Runtime

from trade_graph.adapters.persistence.db import Database
from trade_graph.api.health import health
from trade_graph.api.security import redact


def test_recursive_projection_redaction_keeps_financial_values():
    projection = {
        "equity": "9000", "provider": "openai", "account_id": "private-account",
        "rationale": "loaded /workspace/private/stage.json using sk-test-fixture",
        "facts": [{"api_key": "fixture", "source": "https://example.test/report?token=fixture"}],
        "trace": "see /home/user/runtime/data.sqlite", "conversation": ["private transcript"],
    }
    visible = redact(projection)
    assert visible["equity"] == "9000" and visible["provider"] == "openai"
    assert visible["account_id"] == visible["rationale"] == "[redacted]"
    assert visible["facts"][0]["source"] == "https://example.test/report"
    assert "private-account" not in json.dumps(visible)
    assert "/home/" not in json.dumps(visible)
    assert visible["conversation"] == "[redacted]"
    assert redact("file:///srv/private/owner.json") == "[private path]"
    assert redact("from /arbitrary-private-root/database.sqlite") == "from [private path]"
    assert redact("/api/v1/changes/candidate") == "/api/v1/changes/candidate"


def test_dashboard_snapshot_survives_concurrent_committed_ledger_write(tmp_path):
    runtime = _Runtime(tmp_path)
    other = Database(runtime.database.path)
    try:
        with runtime.database.snapshot():
            assert runtime.database.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == 1
            other.execute(
                "INSERT INTO activity_events VALUES ('concurrent', ?, 'fixture', '{}', '2026', 'hash', NULL)",
                (runtime.portfolio_id,),
            )
            assert runtime.database.execute("SELECT COUNT(*) FROM activity_events WHERE event_id = 'concurrent'") \
                .fetchone()[0] == 0
        assert runtime.database.execute("SELECT COUNT(*) FROM activity_events WHERE event_id = 'concurrent'") \
            .fetchone()[0] == 1
    finally:
        other.close()


def test_health_does_not_certify_live_from_paper_or_caller_claims(tmp_path):
    runtime = _Runtime(tmp_path)
    private = health(runtime, authenticated=True)
    assert private["live_enabled"] is False
    assert private["live_prerequisites"]["implementation_ready"] is False
    assert private["economic_evidence"] == "insufficient_evidence"
    assert private["pause"]["profile"] == "RUNNING"
    public = health(runtime)
    assert "portfolio_id" not in public
    assert "active_version" not in public


def test_health_separates_unverified_declarations_from_passed_checks(tmp_path):
    runtime = _Runtime(tmp_path)
    runtime.database.execute(
        "INSERT INTO live_gate VALUES (?, 1, ?, '2026-01-01T00:00:00Z')",
        (getattr(runtime, "deployment_id", "deployment"), json.dumps({
            "eligibility_confirmed": "true", "owner_confirmed": True,
            "read_only_reconciliation_passed": True, "operating_budget_set": True,
        })),
    )
    gate = health(runtime, authenticated=True)["live_prerequisites"]
    assert gate["enabled"] is False and gate["ready"] is False
    assert gate["recorded_eligibility_passed"] is False
    assert not any(gate["checks"].values())
    assert gate["unverified_recorded_checks"]["explicit_owner_confirmation"] is True
    assert gate["unverified_recorded_checks"]["eligibility"] is False
    assert gate["verification_basis"] == "unverified_dashboard_record"
