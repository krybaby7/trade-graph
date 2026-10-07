"""Subscription receipt uncertainty cannot become resolved economic performance."""

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_dashboard_financial import _receipt, _runtime
from tests.test_dashboard_pages import render
from tests.unit.test_subscription_adapter import request

from trade_graph.adapters.models.subscription import SubscriptionJournal
from trade_graph.api import financial
from trade_graph.contracts.models import ModelResult, ModelUsage
from trade_graph.domain.clock import utc_iso


def subscription_record(runtime, *, blocked=False, synthetic=False):
    journal = SubscriptionJournal(runtime.database, runtime.clock)
    journal.begin("subscription-fixture", request(), "claude_subscription", {})
    result = ModelResult(ok=False, failure="credentials", message="synthetic unavailable login") if blocked else \
        ModelResult(ok=True, payload={"note": "synthetic public evidence"}, provider_model="claude-sonnet-5-5",
                    usage=ModelUsage(uncached_input_tokens=7, billed_output_tokens=9))
    journal.save("subscription-fixture", result, "BLOCKED" if blocked else "COMPLETED", dispatched=not blocked)
    if synthetic:
        runtime.database.execute("UPDATE subscription_invocations SET synthetic=1")


def test_unknown_subscription_dispatch_nulls_economics_and_preserves_known_cost_budget_and_capital(tmp_path):
    runtime = _runtime(tmp_path, capital="10000", currency="USD")
    receipt = _receipt(runtime)
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    before = financial.overview(runtime)
    allowance = runtime.budget.remaining("deployment")
    subscription_record(runtime)
    current = financial.overview(runtime)
    costs = financial.costs(runtime)
    assert current["provisional"] and current["performance"]["provisional"]
    assert current["performance"]["net_economic_pnl"] is None
    assert current["performance"]["trading_pnl"] == before["performance"]["trading_pnl"] == "0"
    assert current["equity"] == before["equity"] == "9000"
    assert current["actual_spend"] == costs["actual_spend"] == "0.9"
    assert costs["allocated_actual_spend"] == "0.9"
    assert runtime.budget.remaining("deployment") == allowance == Decimal("9.1")
    assert current["cost_views"]["all_in"] is None
    assert costs["subscription"]["unknown_inference_costs"] == 1
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert len(costs["receipts"]) == len(costs["subscription"]["records"]) == 1
    assert costs["subscription"]["records"][0]["actual_cost_native"] is None
    assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_blocked_subscription_has_no_inference_charge_but_shared_fee_remains_unknown(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime, blocked=True)
    costs = financial.costs(runtime)
    assert costs["subscription"]["unknown_inference_costs"] == 0
    record = costs["subscription"]["records"][0]
    assert record["inference_dispatched"] is False and record["cost_status"] == "not_incurred"
    assert costs["actual_spend"] == "0" and costs["remaining_allowance"] == "10"
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert costs["provisional"] and financial.overview(runtime)["performance"]["net_economic_pnl"] is None


def test_declared_subscription_shared_fee_is_not_reported_as_resolved_zero(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.subscription_provider = "codex_subscription"
    costs = financial.costs(runtime)
    assert costs["subscription"]["records"] == [] and costs["subscription"]["unknown_inference_costs"] == 0
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert costs["provisional"] and financial.overview(runtime)["performance"]["net_economic_pnl"] is None
    assert runtime.budget.remaining("deployment") == Decimal("10")


def test_synthetic_subscription_receipt_does_not_create_real_expense_uncertainty(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime, synthetic=True)
    costs = financial.costs(runtime)
    assert not costs["provisional"]
    assert costs["subscription"]["unknown_inference_costs"] == 0
    assert costs["subscription"]["shared_fee_allocation_status"] == "not_recorded"
    assert financial.overview(runtime)["performance"]["net_economic_pnl"] == "0"
    assert costs["subscription"]["records"][0]["synthetic"] is True


def test_future_subscription_receipt_is_not_part_of_current_economic_evidence(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime)
    future = utc_iso(runtime.clock.now() + timedelta(seconds=60))
    runtime.database.execute("UPDATE subscription_invocations SET created_at=?,updated_at=?", (future, future))
    assert not financial.costs(runtime)["provisional"]
    runtime.clock.advance(60)
    assert financial.costs(runtime)["provisional"]


def test_costs_template_keeps_subscription_unknowns_separate_from_recorded_api_expenses(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime, blocked=True)
    page = render("costs.html", financial.costs(runtime), operating_mode="paper")
    assert "Subscription usage and shared fees" in page
    assert "not_incurred" in page and "unknown" in page
    assert "Recorded actual operating spend" in page
    assert "Shared subscription fees are not recorded as zero" in page
    assert "claude_subscription" in page


@pytest.mark.parametrize("state", ["DISPATCHED", "FAILED", "UNCERTAIN"])
def test_unknown_subscription_journal_states_keep_economics_unresolved(tmp_path, state):
    runtime = _runtime(tmp_path)
    journal = SubscriptionJournal(runtime.database, runtime.clock)
    journal.begin("interrupted-subscription", request(), "claude_subscription", {})
    if state != "DISPATCHED":
        result = ModelResult(ok=False, failure="timeout_uncertain" if state == "UNCERTAIN" else "validation",
                             message="synthetic retained failure or interrupted dispatch")
        journal.save("interrupted-subscription", result, state)
    costs = financial.costs(runtime)
    assert costs["provisional"] and costs["subscription"]["unknown_inference_costs"] == 1
    assert costs["subscription"]["records"][0]["state"] == state
    assert financial.overview(runtime)["performance"]["net_economic_pnl"] is None
    assert costs["actual_spend"] == "0" and runtime.budget.remaining("deployment") == Decimal("10")


@pytest.mark.parametrize("provider,digest,present,expected", [
    ("claude_subscription", "a" * 64, False, "unknown"),
    (None, "a" * 64, False, "unknown"),
    (None, None, True, "unknown"),
    (None, None, False, "not_recorded"),
])
def test_actual_protected_dashboard_retains_subscription_fee_evidence(
    tmp_path, monkeypatch, provider, digest, present, expected,
):
    import hashlib

    from tests.integration.test_protected_department_graph import GRAPH

    from trade_graph.adapters.models.subscription import SubscriptionConfig
    from trade_graph.application.subscription_profile import SubscriptionAdmission
    from trade_graph.application.subscription_runtime import SubscriptionRuntimeConfig
    from trade_graph.dashboard import dashboard_runtime
    from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256

    fixture = _runtime(tmp_path)
    manifest = ProtectedRuntimeManifest(
        schema_version=1, protected_package_sha256=protected_package_sha256(), deployment_id="deployment",
        approved_source_sha256=(hashlib.sha256(GRAPH.encode()).hexdigest(),),
        operations=("submit_decision", "invoke_model", "apply_role_result"),
    )
    monkeypatch.setattr("trade_graph.application.deployment_runtime._owner_bundle",
                        lambda directory: (manifest, GRAPH, b"synthetic-owner-key-not-a-real-credential"))
    config = SubscriptionRuntimeConfig(subscription=SubscriptionConfig(
        provider=provider, model="claude-sonnet-5-5", enabled=False)) if provider else None
    admission = SubscriptionAdmission(config, None, {
        "ready": False, "selected_provider": provider, "cost_status": "unknown",
        "blockers": ["synthetic blocked native login or refused profile"],
    }, digest)
    calls = []

    def admitted(directory):
        calls.append("admission")
        return admission

    monkeypatch.setattr("trade_graph.application.subscription_profile.load_subscription_profile", admitted)
    monkeypatch.setattr("trade_graph.application.subscription_profile.subscription_profile_unchanged",
                        lambda directory, digest: True)

    def no_config(directory, name, maximum_bytes):
        raise FileNotFoundError("synthetic absent optional paper configuration")

    monkeypatch.setattr("trade_graph.kernel.deployment_image.read_owner_file", no_config)
    owner = tmp_path / "owner"
    if present:
        owner.mkdir(mode=0o700)
        (owner / "subscription-profile.json").write_text("synthetic refused profile, no credentials")
    runtime = dashboard_runtime(fixture.database.path, protected_owner=owner)
    assert runtime.subscription_provider == provider
    assert runtime.database.execute("SELECT COUNT(*) FROM subscription_invocations").fetchone()[0] == 0
    costs = financial.costs(runtime)
    assert costs["subscription"]["shared_fee_allocation_status"] == expected
    assert costs["provisional"] is (expected == "unknown")
    economic = financial.overview(runtime)["performance"]["net_economic_pnl"]
    assert economic is None if expected == "unknown" else economic == "0"
    assert costs["subscription"]["unknown_inference_costs"] == 0
    assert costs["actual_spend"] == "0" and runtime.ledger.books(runtime.portfolio_id).cash["EUR"] == Decimal("100")
    assert calls == ["admission"]  # Projection consumes captured readonly readiness, never probes a CLI.
    assert runtime.database.execute("SELECT COUNT(*) FROM graph_service_runs").fetchone()[0] == 0
    runtime.database.close()


def test_unavailable_existing_readiness_is_conservative_without_constructing_a_service(tmp_path):
    from types import SimpleNamespace

    runtime = _runtime(tmp_path)

    def unavailable():
        raise OSError("synthetic local readonly metadata unavailable")

    runtime.service_controller = SimpleNamespace(prerequisites=unavailable)
    costs = financial.costs(runtime)
    assert costs["subscription"]["shared_fee_allocation_status"] == "unknown"
    assert costs["provisional"] and financial.overview(runtime)["performance"]["net_economic_pnl"] is None
    assert runtime.database.execute("SELECT COUNT(*) FROM graph_service_runs").fetchone()[0] == 0


def test_attempt_details_do_not_duplicate_aggregate_subscription_receipts(tmp_path):
    runtime = _runtime(tmp_path)
    subscription_record(runtime)
    now = utc_iso(runtime.clock.now())
    runtime.database.execute("""INSERT INTO subscription_attempts
        (attempt_id,invocation_id,attempt_index,request_hash,provider,requested_model,state,usage_json,created_at,updated_at)
        VALUES ('attempt-one','subscription-fixture',1,'request-hash','claude_subscription',
                'claude-sonnet-5-5','FAILED','{"uncached_input_tokens":4,"billed_output_tokens":2}',?,?)""", (now, now))
    costs = financial.costs(runtime)
    assert costs["subscription"]["attempt_count"] == 1
    assert len(costs["subscription"]["records"]) == 1
    assert len(costs["subscription"]["records"][0]["attempts"]) == 1
    assert costs["subscription"]["unknown_inference_costs"] == 1
    assert costs["actual_spend"] == "0"


def test_subscription_projection_preserves_reported_totals_and_nulls_unreported_breakdowns(tmp_path):
    import json

    runtime = _runtime(tmp_path)
    subscription_record(runtime)
    usage = {"uncached_input_tokens": 360, "cache_read_tokens": 90, "cache_write_tokens": 0,
        "billed_output_tokens": 20, "reasoning_tokens": 0, "tool_units": 0,
        "provider_reported_input_tokens": 450,
        "unreported_fields": ["cache_write_tokens", "reasoning_tokens"]}
    runtime.database.execute("UPDATE subscription_invocations SET usage_json=?", (json.dumps(usage),))
    shown = financial.costs(runtime)["subscription"]["records"][0]["usage"]
    assert shown["provider_reported_input_tokens"] == 450
    assert shown["uncached_input_tokens"] == 360 and shown["cache_read_tokens"] == 90
    assert shown["cache_write_tokens"] is None and shown["reasoning_tokens"] is None
    assert shown["billed_output_tokens"] == 20 and shown["tool_units"] == 0
