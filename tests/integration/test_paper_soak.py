"""Funded-soak controls use synthetic transports; no paid or public requests occur."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.application.paper_soak import expense_observations, run_funded_soak, soak_preflight
from trade_graph.cli import main
from trade_graph.contracts.models import Observation
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel.pricing import worst_case_cost
from trade_graph.paper_runtime import PaperRuntimeConfig


class FixtureFeed:
    def __init__(self, flow):
        self.flow, self.sent = flow, False

    def poll(self):
        if self.sent:
            return []
        self.sent = True
        return [Observation(observation_id="fixture-public", venue="paper", symbol="BTC/USD",
                            event_time_utc=self.flow.clock.now(), available_at_utc=self.flow.clock.now(),
                            bid="99", ask="100", volume="1", kind="quote", source="synthetic-soak-fixture")]


def runtime(flow):
    return SimpleNamespace(database=flow.db, clock=flow.clock, portfolio_id=flow.pid,
                           deployment_id="deployment", paid_calls_enabled=True, model_handlers=flow.assembly,
                           public_feed=FixtureFeed(flow), budget=flow.office.budget, ledger=flow.engineer.ledger,
                           execution=flow.office.execution, secretary=flow.secretary, handlers=flow.assembly.handlers,
                           artifact_runtime=flow.runtime, config=PaperRuntimeConfig(tick_interval_seconds=0.05))


def test_missing_paid_owner_permission_cannot_create_evidence_or_reserve_spend(tmp_path):
    flow = RuntimeFlow(tmp_path, owner_paid=False)
    configured = runtime(flow)
    destination = tmp_path / "not-authorized" / "evidence.json"
    with pytest.raises(AuthorityDenied, match="owner and private"):
        asyncio.run(run_funded_soak(configured, duration_seconds=1, report_path=destination))
    assert not destination.exists()
    assert not destination.parent.exists()
    assert flow.transport.calls == []
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


@pytest.mark.parametrize("missing", ["feed", "budget", "keys", "pause", "synthetic"])
def test_funded_preflight_requires_real_separate_prerequisites_without_calls(tmp_path, missing):
    flow = RuntimeFlow(tmp_path, keys={} if missing == "keys" else None,
                       provider="scripted" if missing == "synthetic" else "openai")
    configured = runtime(flow)
    if missing == "feed":
        configured.public_feed = None
    elif missing == "budget":
        flow.db.execute("UPDATE deployment_budget SET total_allowance = '0'")
    elif missing == "pause":
        flow.office.execution.set_pause(flow.pid, "MANAGE_ONLY", "owner", "retained pause")
    with pytest.raises(AuthorityDenied):
        soak_preflight(configured)
    assert flow.transport.calls == []
    assert flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_uncertain_attempt_retains_amount_and_attribution_in_expense_report(tmp_path):
    flow = RuntimeFlow(tmp_path)
    flow.transport.omit_usage = True
    task = flow.add("research")
    flow.run("research")
    report = expense_observations(runtime(flow), set(), set())
    assert report["known_actual_accrued"] == "0"
    assert report["unresolved_reservations"] == 1
    assert Decimal(report["unresolved_reserved_upper_bound"]) > 0
    attempt = report["unresolved_attempts"][0]
    assert attempt["task_id"] == task and attempt["role"] == "research"
    assert attempt["state"] == "UNCERTAIN"
    assert attempt["price_card_id"] == "primary"
    assert Decimal(report["forecast_upper_bound_for_dispatched_requests"]) > 0


def test_equal_timestamp_restart_forecast_excludes_preexisting_invocation_ids(tmp_path):
    flow = RuntimeFlow(tmp_path)
    old = flow.add("research")
    flow.run("research")
    prior_receipts = {row[0] for row in flow.db.execute("SELECT receipt_id FROM usage_receipts")}
    prior_invocations = {row[0] for row in flow.db.execute("SELECT invocation_id FROM model_invocations")}
    new = flow.add("learning")
    flow.run("learning")
    assert flow.invocation(old)["created_at"] == flow.invocation(new)["created_at"]
    report = expense_observations(runtime(flow), prior_receipts, prior_invocations)
    assert len(report["attempts"]) == 1 and report["attempts"][0]["task_id"] == new
    request = json.loads(flow.invocation(new)["request_json"])
    expected = worst_case_cost(flow.office.budget.card("primary"), request["context"]["max_input_tokens"],
                               request["max_output_tokens"], request["max_tool_calls"]) * Decimal("1.02")
    assert Decimal(report["forecast_upper_bound_for_dispatched_requests"]) == expected


def test_synthetic_transport_soak_reports_pending_and_measures_requested_wall_window(tmp_path):
    flow = RuntimeFlow(tmp_path)
    destination = tmp_path / "evidence.json"
    result = asyncio.run(run_funded_soak(runtime(flow), duration_seconds=1, report_path=destination))
    assert result["requested_duration_completed"] is True
    assert Decimal(result["observed_duration_seconds"]) >= 1
    assert result["credentialed_provider_verification"] == "pending"
    assert result["economic_evidence"] == "insufficient_evidence"
    assert result["live_enabled"] is False
    assert result["service"]["completed"] >= 1
    assert json.loads(destination.read_text()) == result
    assert destination.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        asyncio.run(run_funded_soak(runtime(flow), duration_seconds=1, report_path=destination))


def test_early_graceful_stop_is_interrupted_not_a_completed_duration(tmp_path, monkeypatch):
    flow = RuntimeFlow(tmp_path)

    async def stopped(self):
        return {"ticks": 0, "stopped": True, "observations": 0, "failures": []}

    monkeypatch.setattr("trade_graph.application.paper_soak.PaperService.run", stopped)
    result = asyncio.run(run_funded_soak(runtime(flow), duration_seconds=60, report_path=tmp_path / "early.json"))
    assert result["status"] == "interrupted"
    assert result["failure_type"] == "StoppedBeforeRequestedDuration"
    assert result["requested_duration_completed"] is False
    assert result["credentialed_provider_verification"] == "pending"
    assert flow.transport.calls == []


def test_service_start_failure_retains_private_failure_report_and_no_calls(tmp_path, monkeypatch):
    flow = RuntimeFlow(tmp_path)

    async def failed(self):
        raise RuntimeError("untrusted detail should never appear")

    monkeypatch.setattr("trade_graph.application.paper_soak.PaperService.run", failed)
    destination = tmp_path / "failed.json"
    result = asyncio.run(run_funded_soak(runtime(flow), duration_seconds=1, report_path=destination))
    assert result["status"] == "interrupted" and result["failure_type"] == "RuntimeError"
    assert "untrusted detail" not in destination.read_text()
    assert flow.transport.calls == []


def test_cli_observer_failure_after_real_worker_effects_retains_interrupted_evidence(tmp_path, monkeypatch, capsys):
    flow = RuntimeFlow(tmp_path)
    configured = runtime(flow)
    destination = tmp_path / "observer-failed.json"

    def failed_observer(*args):
        assert flow.transport.calls
        assert flow.db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] > 0
        raise OSError("private observer exception detail must remain private")

    monkeypatch.setattr("trade_graph.paper_runtime.load_runtime_config", lambda path: configured.config)
    monkeypatch.setattr("trade_graph.paper_runtime.assemble_paper_runtime", lambda *args, **kwargs: configured)
    monkeypatch.setattr("trade_graph.application.paper_soak.expense_observations", failed_observer)
    assert main(["soak", "--config", "fixture-config", "--duration-seconds", "1",
                 "--report", str(destination)]) == 1
    result = json.loads(destination.read_text())
    assert result["status"] == "interrupted" and result["failure_type"] == "OSError"
    assert result["requested_duration_completed"] is True
    assert result["service"]["completed"] >= 1
    assert result["expenses"] is None
    assert result["expense_observation_status"] == "unavailable"
    assert result["expense_failure_type"] == "OSError"
    assert result["credentialed_provider_verification"] == "unavailable"
    assert destination.stat().st_mode & 0o077 == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == result
    assert "private observer exception detail" not in destination.read_text() + output.out + output.err
    with flow.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] > 0


def test_cli_existing_report_refusal_is_sanitized_and_precedes_model_work(tmp_path, monkeypatch, capsys):
    flow = RuntimeFlow(tmp_path)
    configured = runtime(flow)
    destination = tmp_path / "private-report-path-must-not-leak.json"
    destination.write_text("prior evidence must remain intact")
    monkeypatch.setattr("trade_graph.paper_runtime.load_runtime_config", lambda path: configured.config)
    monkeypatch.setattr("trade_graph.paper_runtime.assemble_paper_runtime", lambda *args, **kwargs: configured)
    with pytest.raises(SystemExit) as stopped:
        main(["soak", "--config", "fixture-config", "--duration-seconds", "1",
              "--report", str(destination)])
    assert stopped.value.code == 2
    output = capsys.readouterr()
    assert "FileExistsError" in output.err and "use doctor" in output.err
    assert destination.name not in output.err
    assert "Traceback" not in output.err and output.out == ""
    assert destination.read_text() == "prior evidence must remain intact"
    assert flow.transport.calls == []
    with flow.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
