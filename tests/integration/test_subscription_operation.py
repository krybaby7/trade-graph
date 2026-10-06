"""Real protected graph operation with synthetic CLI; no diagnostic prerequisite."""
from types import SimpleNamespace

import pytest
from tests.integration.test_owned_subscription_service import subscription_service
from tests.integration.test_paper_service import Feed

from trade_graph.adapters.persistence.db import Database
from trade_graph.application import subscription_operation as operation
from trade_graph.contracts.models import Observation
from trade_graph.paper_runtime import PaperRuntimeConfig


@pytest.fixture
def operating_flow(tmp_path, monkeypatch):
    flow, runtime, binding, _unused, cli, _current = subscription_service(tmp_path, monkeypatch)
    config = PaperRuntimeConfig(tick_interval_seconds=0.01, schedule_intervals={})
    runtime.config = config
    runtime.ledger = runtime.execution.ledger
    runtime.public_feed = None
    runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
    cli.outputs["trader"] = {"action": "hold", "strategy_id": "range-reversion", "rationale": "No edge observed.",
        "invalidation": "New evidence.", "experiment": False, "evidence_ids": [], "no_action_reason": "No signal."}
    monkeypatch.setattr(operation, "assert_boot_environment", lambda: None)
    monkeypatch.setattr(operation, "read_owner_file", lambda *_a: runtime.config.model_dump_json().encode())
    def assemble(_path, **kwargs):
        assert kwargs["api_keys"] == {} and kwargs["protected_owner"] == tmp_path / "owner"
        assert kwargs["config"] == runtime.config
        return runtime
    monkeypatch.setattr(operation, "assemble_paper_runtime", assemble)
    yield SimpleNamespace(flow=flow, runtime=runtime, binding=binding, cli=cli, owner=tmp_path / "owner")


def run(h, ticks=3):
    result = operation.run_subscription_operation(h.flow.db.path, h.owner, maximum_ticks=ticks)
    h.database = Database(h.flow.db.path)
    return result


def test_normal_graph_runs_departments_without_diagnostic_and_stays_running(operating_flow):
    h = operating_flow
    before_orders = h.flow.db.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0]
    h.flow.add("research")
    h.flow.add("trader")
    origin, reason = next(pair for pair in operation._PREVIOUS_TASK_PAUSES if pair[0] == "owner")
    h.runtime.execution.set_pause(h.flow.pid, "MANAGE_ONLY", origin, reason)
    result = run(h, ticks=30)
    assert result["inference_attempts"] == 2 and result["completed"] == 2
    assert result["position_management"] == "RUNNING" and result["previous_task_pause_resumed"]
    assert result["orders_hosted_by"] == "trade_graph_local_simulator"
    assert result["automatic_schedule"] and result["automatic_optimisation"]
    assert {request.role for request in h.cli.requests} == {"research", "trader"}
    assert result["decisions_created"] == 1
    assert h.database.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0] == before_orders
    assert h.database.execute("SELECT COUNT(*) FROM activity_events "
                              "WHERE kind='subscription_smoke_intent'").fetchone()[0] == 0
    assert h.database.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 0
    assert result["cost_status"] == "unknown" and result["actual_cost_native"] is None
    h.database.close()


@pytest.mark.parametrize("profile", ["MANAGE_ONLY", "FLATTEN", "CANCEL_ALL", "STOPPED"])
def test_normal_graph_preserves_unrelated_owner_halts(operating_flow, profile):
    h = operating_flow
    h.flow.add("research")
    h.runtime.execution.set_pause(h.flow.pid, profile, "owner", "Unrelated owner control remains.")
    result = run(h)
    assert result["inference_attempts"] == 0 and not result["previous_task_pause_resumed"]
    pause = h.database.execute("SELECT * FROM pause_states WHERE portfolio_id=?", (h.flow.pid,)).fetchone()
    assert (pause["profile"], pause["originator"], pause["reason"]) == (
        profile, "owner", "Unrelated owner control remains.")
    assert h.cli.requests == []
    h.database.close()


def test_unavailable_ai_keeps_management_and_saved_tasks(operating_flow, monkeypatch):
    h = operating_flow
    task = h.flow.add("research")
    h.runtime.prepare_runtime = lambda: {}
    h.runtime.runtime_ready = lambda: False
    result = run(h)
    assert result["inference_attempts"] == 0 and result["ai_available"] is False
    assert result["status"] == "paper_stopped"
    assert h.database.execute("SELECT status FROM tasks WHERE task_id=?", (task,)).fetchone()[0] == "QUEUED"
    assert h.database.execute("SELECT COUNT(*) FROM process_leases").fetchone()[0] == 0
    h.database.close()


def test_first_public_history_and_quotes_arrive_before_department_work(operating_flow, monkeypatch):
    h = operating_flow
    h.flow.add("research")
    h.runtime.config = h.runtime.config.model_copy(update={"public_data_enabled": True})
    feed = Feed([Observation(observation_id="public-current", venue="paper", symbol="BTC/USD",
        event_time_utc=h.flow.clock.now(), available_at_utc=h.flow.clock.now(), bid="99", ask="100",
        kind="quote", source="kraken_public_rest:paper_reference:receipt_time")])
    feed.transport, feed.symbols = object(), ["BTC/USD", "ETH/USD"]
    h.runtime.public_feed = feed
    calls = []
    def history(database, clock, transport, **kwargs):
        assert not h.cli.requests
        calls.append(kwargs)
        return {"status": "partial", "symbols": {"BTC/USD": {"missing_hours": 2, "gaps": ["synthetic"]}}}
    monkeypatch.setattr(operation, "collect_public_hourly_history", history)
    result = run(h)
    assert calls == [{"hours": 168, "symbols": ("BTC/USD", "ETH/USD")}]
    assert result["public_history"]["status"] == "partial" and result["inference_attempts"] == 1
    assert result["observations"] == 1 and feed.closed
    assert h.database.execute("SELECT COUNT(*) FROM activity_events "
                              "WHERE kind='public_history_refresh'").fetchone()[0] == 1
    h.database.close()


def test_optional_tick_limit_validation_does_no_admission_work(monkeypatch, tmp_path):
    monkeypatch.setattr(operation, "assert_boot_environment", lambda: pytest.fail("invalid operating option"))
    for ticks in (True, 0, -1, 1.5):
        with pytest.raises(ValueError, match="maximum_ticks"):
            operation.run_subscription_operation(tmp_path / "absent.sqlite", tmp_path, maximum_ticks=ticks)


def test_schedule_configuration_supports_normal_departments_and_provider_history_bounds():
    config = PaperRuntimeConfig(schedule_intervals={"optimisation": 604800, "research": 86400})
    assert config.automatic_schedule and not config.optimisation_manual_only
    assert config.optimisation_deadline_seconds is None
    for intervals in ({"engineer": 1}, {"research": True}, {"research": 0}):
        with pytest.raises(ValueError):
            PaperRuntimeConfig(schedule_intervals=intervals)
    with pytest.raises(ValueError):
        PaperRuntimeConfig(public_history_hours=720)
