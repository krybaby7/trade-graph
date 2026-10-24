"""Citation eligibility is explicit at the real confined subscription interface."""

import json

import pytest
from tests.integration.test_subscription_departments import flow

from trade_graph.adapters.models.subscription import CliOutcome


@pytest.mark.parametrize("role", ["research", "learning", "optimisation", "leader"])
def test_subscription_request_declares_top_level_citation_allowlist(tmp_path, role):
    setup, _protected, cli = flow(tmp_path)
    nested_ref = "nested-source-provenance"
    report = setup.secretary.report(
        setup.pid, role="system", kind="failed",
        summary="Synthetic failed task evidence retained for review.",
        evidence_refs=[nested_ref], source_key="nested-citation-fixture",
    )
    identity = setup.add(role)

    assert setup.run(role) == 1
    assert setup.row(identity)["status"] == "SUCCEEDED", setup.row(identity)["output_json"]
    request = cli.requests[0]
    assert report in request.context["evidence_refs"]
    assert nested_ref not in request.context["evidence_refs"]
    assert any(nested_ref in item["evidence_refs"] for item in request.context["reports"])
    for guidance in (
        request.instructions,
        request.output_schema["properties"]["evidence_refs"].get("description", ""),
    ):
        assert "top-level context.evidence_refs" in guidance
        assert "Nested report references are provenance" in guidance
        assert "not eligible citations unless also listed there" in guidance


def test_nested_report_reference_is_rejected_and_durable_receipt_retained(tmp_path, monkeypatch):
    setup, _protected, cli = flow(tmp_path)
    nested_ref = "nested-source-provenance"
    report = setup.secretary.report(
        setup.pid, role="system", kind="failed",
        summary="Synthetic failed task evidence retained for review.",
        evidence_refs=[nested_ref], source_key="nested-citation-fixture",
    )

    def reply_with_nested_reference(request, *, cancel_event=None):
        cli.requests.append(request)
        assert report in request.context["evidence_refs"]
        assert nested_ref not in request.context["evidence_refs"]
        assert any(nested_ref in item["evidence_refs"] for item in request.context["reports"])
        return CliOutcome(json.dumps({
            "type": "result", "is_error": False, "num_turns": 1,
            "modelUsage": {request.model: {"inputTokens": 7, "outputTokens": 9}},
            "structured_output": {
                "evidence_refs": [report, nested_ref],
                "summary": "Review the supplied failure report.",
                "outcome": "Propose an observability improvement.",
                "proposals": [{
                    "issue": "Failure details are sparse.",
                    "resources": "Existing records.",
                    "interval": "Next review.",
                    "modification": "Improve report detail.",
                    "expected_benefit": "Diagnosable failures.",
                    "quality_risk": "Unknown causes may remain unknown.",
                    "validation_metrics": ["All asserted causes cite evidence."],
                }],
                "changes": [],
            },
        }), 0)

    monkeypatch.setattr(cli, "execute", reply_with_nested_reference)
    identity = setup.add("optimisation")

    assert setup.run("optimisation") == 1
    row = setup.row(identity)
    assert row["status"] == "FAILED"
    assert "decision cites evidence outside its persisted context" in row["output_json"]
    assert setup.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0
    assert setup.db.execute("SELECT COUNT(*) FROM change_tasks").fetchone()[0] == 0
    receipt = setup.db.execute("SELECT * FROM subscription_invocations WHERE task_id=?", (identity,)).fetchone()
    assert receipt["state"] == "COMPLETED"
    result = json.loads(receipt["result_json"])
    assert result["ok"] is True
    assert result["payload"]["evidence_refs"] == [report, nested_ref]
    assert receipt["cost_status"] == "unknown"
    assert len(cli.requests) == 1
