"""Read projections count persisted native attempts without querying providers."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.api.evidence import decision
from trade_graph.application.ledger import Ledger
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import FrozenClock, utc_iso


@pytest.fixture
def runtime(tmp_path):
    database = Database(tmp_path / "activity.sqlite")
    clock = FrozenClock(datetime(2026, 1, 2, tzinfo=UTC))
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    yield SimpleNamespace(database=database, clock=clock, portfolio_id=portfolio,
                          other=ledger.create_portfolio(reporting_currency="EUR"))
    database.close()


def insert(runtime, table, **values):
    runtime.database.execute(
        f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        tuple(values.values()),
    )


def invocation(runtime, name, *, role="research", portfolio=None, state="COMPLETED", usage=None,
               synthetic=False, stamp="2026-01-01T00:00:00Z"):
    task = Scheduler(runtime.database, runtime.clock).add_task(
        role=role, portfolio_id=runtime.portfolio_id if portfolio is None else portfolio, objective="Real assignment")
    insert(runtime, "subscription_invocations", invocation_id=name, request_hash=name, task_id=task,
           root_task_id=task, role=role, run_id=name, system_version_id="artifact-1", provider="codex_subscription",
           requested_model="model-requested", actual_model="model-actual", state=state, result_json=None,
           quota_json="{}", usage_json=json.dumps(usage) if usage else None, synthetic=int(synthetic),
           created_at=stamp, updated_at=stamp)
    return task


def attempt(runtime, invocation_id, index, state, usage=None, *, quota=None, stamp="2026-01-01T00:00:00Z"):
    insert(runtime, "subscription_attempts", attempt_id=f"{invocation_id}:{index}", invocation_id=invocation_id,
           attempt_index=index, request_hash=f"{invocation_id}:{index}", provider="codex_subscription",
           requested_model="model-requested", actual_model="model-actual", state=state, result_json=None,
           usage_json=json.dumps(usage) if usage else None, quota_json=json.dumps(quota or {}),
           created_at=stamp, updated_at=stamp)


def test_usage_counts_children_once_and_preserves_unknown_field_coverage(runtime):
    from trade_graph.api import activity

    partial = {"uncached_input_tokens": 0, "cache_read_tokens": 30, "cache_write_tokens": 0,
               "billed_output_tokens": 20, "reasoning_tokens": 7, "tool_units": 0,
               "provider_reported_input_tokens": 100, "unreported_fields": ["uncached_input_tokens", "tool_units"]}
    invocation(runtime, "aggregate", usage={"provider_reported_input_tokens": 9999, "billed_output_tokens": 9999})
    attempt(runtime, "aggregate", 1, "FAILED", partial)
    attempt(runtime, "aggregate", 2, "COMPLETED", partial)
    invocation(runtime, "legacy", role="trader", usage={"uncached_input_tokens": 12, "cache_read_tokens": 3,
                                                       "cache_write_tokens": 5, "billed_output_tokens": 10})
    invocation(runtime, "unresolved", role="leader", state="UNCERTAIN")
    invocation(runtime, "synthetic", usage=partial, synthetic=True)
    invocation(runtime, "foreign", portfolio=runtime.other, usage=partial)
    invocation(runtime, "blocked", state="BLOCKED")
    result = activity.overview(runtime)
    totals = result["usage"]["totals"]
    assert totals["attempts"] == 5
    assert totals["actual_attempts"] == 4 and totals["synthetic_attempts"] == 1
    assert (totals["completed"], totals["failed"], totals["uncertain"], totals["retries"]) == (2, 1, 1, 1)
    assert totals["input_tokens"] == 220 and totals["output_tokens"] == 50
    assert totals["total_tokens"] == 270  # cache and reasoning are subsets, not extra tokens
    assert totals["cache_read_tokens"] == 63 and totals["reasoning_tokens"] == 14
    assert totals["unreported_fields"]["uncached_input_tokens"] == 3
    assert totals["coverage"]["input_tokens"] == {"reported": 3, "unreported": 1, "attempts": 4}
    assert totals["coverage_label"] == "Known subtotal; missing usage remains unknown"
    assert result["usage"]["period"] == "all retained lifetime records"
    assert {row["role"] for row in result["departments"]} == {
        "research", "trader", "learning", "optimisation", "leader", "engineer"}


def test_departments_distinguish_native_completion_from_applied_failure_and_retry(runtime):
    from trade_graph.api import activity

    task = invocation(runtime, "native", role="trader")
    attempt(runtime, "native", 1, "COMPLETED")
    runtime.database.execute("UPDATE tasks SET status='FAILED',output_json=? WHERE task_id=?",
                             (json.dumps({"reason": "Invalid decision", "messages": ["raw private"]}), task))
    insert(runtime, "role_results", task_id=task, portfolio_id=runtime.portfolio_id, role="trader", status="FAILED",
           document_json='{"reason":"Invalid decision"}', created_at="2026-01-01T01:00:00Z")
    insert(runtime, "schedules", schedule_id="schedule-trader", portfolio_id=runtime.portfolio_id,
           name="trader", next_due_at="2026-01-02T04:00:00Z", missed_run_policy="coalesce", interval_seconds=14400)
    retry = Scheduler(runtime.database, runtime.clock).add_task(
        role="trader", portfolio_id=runtime.portfolio_id, objective="Delayed retry",
        due_at="2026-01-02T00:05:00Z", payload={"retry_of": task})
    runtime.database.execute("UPDATE tasks SET attempts_used=1 WHERE task_id=?", (retry,))
    result = activity.overview(runtime)
    trader = next(row for row in result["departments"] if row["role"] == "trader")
    assert trader["last_native"]["status"] == "COMPLETED"
    assert trader["last_applied"]["status"] == "FAILED" and trader["last_applied"]["task_id"] == task
    assert trader["last_applied"]["completion"] == {"reason": "Invalid decision"}
    assert trader["next_due"]["next_due_at"] == "2026-01-02T04:00:00Z"
    assert trader["failed_tasks"] == 1
    assert trader["retries"][0]["task_id"] == retry
    assert "raw private" not in json.dumps(result)


def test_shared_quota_reads_only_persisted_observations_and_service_heartbeat(runtime):
    from trade_graph.api import activity

    runtime.controller = SimpleNamespace(public_status=lambda: pytest.fail("No controller probes"))
    runtime.model_router = SimpleNamespace(readiness=lambda: pytest.fail("No provider probes"))
    invocation(runtime, "native")
    quota = {"source": "codex-app-server", "observed_at": "2026-01-01T22:00:00+00:00",
             "windows": {"primary": {"used_percent": 40, "remaining_percent": 60,
                                     "window_duration_mins": 300, "resets_at": 1767391200}},
             "api_key": "private-quota-key"}
    attempt(runtime, "native", 1, "COMPLETED", quota=quota)
    insert(runtime, "subscription_provider_state", provider="codex_subscription", quota_json=json.dumps(
        {**quota, "observed_at": "2026-01-01T21:00:00+00:00"}), updated_at="2026-01-01T23:59:59Z")
    insert(runtime, "graph_service_runs", run_id="service-1", portfolio_id=runtime.portfolio_id, mode="paper",
           status="RUNNING", requested_at="2026-01-01T00:00:00Z", heartbeat_at="2026-01-01T23:59:40Z")
    before = runtime.database.execute("SELECT total_changes() AS n").fetchone()["n"]
    result = activity.overview(runtime)
    assert runtime.database.execute("SELECT total_changes() AS n").fetchone()["n"] == before
    shared = result["usage"]["shared_quota"]
    assert shared["scope"] == "shared account allowance"
    assert shared["observed_at"] == quota["observed_at"] and shared["available"] is True
    assert shared["windows"]["primary"]["remaining_percent"] == 60
    assert shared["record_source"] == "subscription_attempts"
    assert result["service"]["heartbeat_at"] == "2026-01-01T23:59:40Z"
    assert result["service"]["stale"] is False
    assert "private-quota-key" not in json.dumps(result)


def test_decision_includes_safe_retained_market_strategy_and_source_inputs(runtime):
    snapshot = {
        "artifact": {"version_id": "v1", "artifact_hash": "artifact-1"},
        "market": {"BTC/USD": {"fresh": True, "features": {"spread": "2", "raw_request": "private"},
                               "observation": {"symbol": "BTC/USD", "bid": "99", "ask": "101",
                                               "source": "synthetic", "messages": ["private market"]},
                               "history": {"source_ref": "hourly-features:1", "stale": False,
                                           "values": {"trend": "up"}, "source_instruction": "private history"}}},
        "portfolio": {"cash": {"USD": "10000"}, "inventory": {}, "account_id": "private-account"},
        "active_strategy_templates": {"trend": {"strategy_id": "trend", "hypothesis": "Trend can persist",
                                                  "features": ["trend"], "entry": "Pullback", "prompt": "private"}},
        "strategy_templates": {"foreign": {"strategy_id": "foreign"}},
        "sources": [{"source_ref": "source-1", "url": "https://example.test/market", "publisher": "Source",
                     "content": "private raw page", "messages": ["private source"]}],
        "selected_context": {"always_include": ["active_safety"], "lessons": []},
        "source_instruction": "private prompt", "messages": ["private conversation"],
    }
    insert(runtime, "snapshots", snapshot_id="s1", portfolio_id=runtime.portfolio_id,
           as_of="2026-01-01T00:00:00Z", created_at="2026-01-01T00:00:00Z", payload_json=json.dumps(snapshot))
    insert(runtime, "decisions", decision_id="d1", portfolio_id=runtime.portfolio_id, action="hold",
           payload_json='{"rationale":"Wait for pullback","strategy_id":"trend"}', mandate_revision="1",
           policy_revision="p1", snapshot_id="s1", system_version_id="artifact-1",
           created_at=utc_iso(runtime.clock.now()))
    result = decision(runtime, "d1")
    retained = result["snapshot"]["retained_inputs"]
    assert retained["market"]["BTC/USD"]["observation"]["bid"] == "99"
    assert retained["portfolio"]["cash"]["USD"] == "10000"
    assert retained["strategy_templates"]["trend"]["hypothesis"] == "Trend can persist"
    assert "foreign" not in retained["strategy_templates"]
    assert retained["sources"] == [{"source_ref": "source-1", "url": "https://example.test/market",
                                     "publisher": "Source"}]
    assert result["decision"]["rationale"] == "Wait for pullback"
    encoded = json.dumps(result)
    for text in ("private market", "private history", "private-account", "private raw page", "private source",
                 "private prompt", "private conversation", '"prompt"', '"raw_request"'):
        assert text not in encoded


def test_api_synthetic_attribution_comes_from_gateway_reservation_not_model_request(runtime):
    from trade_graph.api import activity

    task = Scheduler(runtime.database, runtime.clock).add_task(
        role="engineer", portfolio_id=runtime.portfolio_id, objective="Synthetic artifact trial")
    insert(runtime, "budget_reservations", reservation_id="r1", deployment_id="deployment", role="engineer",
           task_id=task, root_task_id=task, amount="0", currency="EUR", state="SETTLED", price_card_id="card",
           purpose="engineering", synthetic=1, created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
    insert(runtime, "model_invocations", invocation_id="api-1", request_hash="api-hash",
           request_json=json.dumps({"role": "engineer", "provider": "scripted", "model": "fixture",
                                    "synthetic": False}),
           portfolio_id=runtime.portfolio_id, task_id=task, root_task_id=task, run_id="run",
           system_version_id="artifact-1", reservation_id="r1", state="COMPLETED",
           result_json=json.dumps({"usage": {"uncached_input_tokens": 100, "cache_read_tokens": 0,
                                            "cache_write_tokens": 0, "billed_output_tokens": 10}}),
           created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
    totals = activity.overview(runtime)["usage"]["totals"]
    assert totals["actual_attempts"] == 0 and totals["synthetic_attempts"] == 1
    assert totals["input_tokens"] == 0
    runtime.database.execute("UPDATE budget_reservations SET synthetic=0 WHERE reservation_id='r1'")
    runtime.database.execute("UPDATE model_invocations SET result_json=? WHERE invocation_id='api-1'",
                             (json.dumps({"ok": False, "failure": "validation"}),))
    native = next(row for row in activity.overview(runtime)["departments"] if row["role"] == "engineer")["last_native"]
    assert native["status"] == "FAILED"
    assert native["persisted_state"] == "COMPLETED"


def test_inactive_or_stale_management_service_and_empty_quota_are_explicit(runtime):
    from trade_graph.api import activity

    insert(runtime, "graph_service_runs", run_id="manage", portfolio_id=runtime.portfolio_id, mode="paper",
           status="MANAGEMENT_ONLY", requested_at="2026-01-01T00:00:00Z", heartbeat_at="2026-01-01T23:58:00Z")
    result = activity.overview(runtime)
    assert result["service"]["stale"] is True
    assert result["usage"]["shared_quota"]["observed_at"] is None
    assert result["usage"]["shared_quota"]["available"] is False
    assert result["counts"]["trades"] == 0
