"""Field-specific Research citations and valuation scope; synthetic providers only."""

import json
from decimal import Decimal

import pytest
from tests.integration.test_execution import _rules
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.application.runtime_departments import ResearchReply
from trade_graph.contracts.models import Observation


def _observation(flow):
    flow.office.execution.save_observation(Observation(
        observation_id="eligible-source", venue="paper", symbol="BTC/USD",
        event_time_utc=flow.clock.now(), available_at_utc=flow.clock.now(),
        bid="99", ask="100", kind="quote", source="synthetic"))


def _finding(source_ref):
    return {"source_ref": source_ref, "question": "Visible spread?", "claim": "One-unit spread.",
            "counterevidence": "One observation does not establish an edge.",
            "invalidation": "New observation supersedes it.", "expires_after_seconds": 3600}


def test_research_source_ref_has_field_specific_schema_and_prompt_guidance(tmp_path):
    flow = RuntimeFlow(tmp_path)
    _observation(flow)
    identity = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "SUCCEEDED"
    body = flow.transport.calls[0]["body"]
    instructions = body["input"][0]["content"]
    context = json.loads(body["input"][1]["content"])
    schema = ResearchReply.model_json_schema()
    description = schema["$defs"]["FindingAnalysis"]["properties"]["source_ref"].get("description", "")
    for text in (instructions, context["analysis_instruction"], description):
        assert "sources[].source_ref" in text
        assert "evidence_refs alone" in text
    assert {source["source_ref"] for source in context["sources"]} == {"eligible-source"}
    assert flow.ref in context["evidence_refs"]


def test_research_mixed_eligible_and_report_only_sources_publish_nothing(tmp_path):
    flow = RuntimeFlow(tmp_path)
    _observation(flow)
    def mixed_sources(context):
        assert flow.ref in context["evidence_refs"]
        assert flow.ref not in {source["source_ref"] for source in context["sources"]}
        return {"evidence_refs": [flow.ref], "summary": "Inspect visible evidence.", "outcome": "Unproven.",
                "findings": [_finding("eligible-source"), _finding(flow.ref)]}
    flow.transport.outputs["ResearchReply"] = mixed_sources
    identity = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(identity)["status"] == "FAILED"
    assert "research source is outside the persisted snapshot" in flow.row(identity)["output_json"]
    assert flow.db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert flow.db.execute("SELECT COUNT(*) FROM secretary_reports WHERE kind='journal'").fetchone()[0] == 0


def test_trader_scopes_provisional_eur_valuation_separately_from_native_state(tmp_path):
    flow = RuntimeFlow(tmp_path)
    flow.office.execution.ledger.deposit(flow.pid, "USD", Decimal("10000"), "synthetic-opening")
    _observation(flow)
    identity = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(identity)["status"] == "SUCCEEDED", flow.row(identity)["output_json"]
    body = flow.transport.calls[0]["body"]
    context = json.loads(body["input"][1]["content"])
    portfolio = context["portfolio"]
    assert portfolio["reporting_valuation"] == {
        "currency": "EUR", "equity": None, "provisional": True, "stale": True}
    assert "provisional" not in portfolio and "stale" not in portfolio
    assert portfolio["cash"] == {"USD": "10000"}
    assert portfolio["inventory"] == {} and portfolio["reserved"] == {}
    assert portfolio["execution_state"]["uncertain_order_ids"] == []
    assert portfolio["execution_state"]["basis"] == "persisted native ledger and order intents at snapshot"
    assert "reporting_valuation" in body["input"][0]["content"]
    assert "cash, inventory or order status" in body["input"][0]["content"]


def test_trader_exposes_real_order_uncertainty_independently_of_valuation(tmp_path):
    flow = RuntimeFlow(tmp_path)
    _observation(flow)
    from tests.integration.test_artifact_consumers import _choice
    flow.office.execution.ledger.deposit(flow.pid, "USD", Decimal("10000"), "synthetic-opening")
    flow.office.execution.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
        source="synthetic", kind="synthetic", stale=False)
    flow.office.execution.register_instrument(_rules())
    flow.transport.outputs["TraderReply"] = _choice(action="enter")
    first = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(first)["status"] == "SUCCEEDED", flow.row(first)["output_json"]
    intent_id = json.loads(flow.row(first)["output_json"])["intent_id"]
    flow.db.execute("UPDATE order_intents SET state='UNKNOWN' WHERE intent_id=?", (intent_id,))
    flow.transport.outputs["TraderReply"] = _choice()
    second = flow.add("trader")
    assert flow.run("trader") == 1
    assert flow.row(second)["status"] == "SUCCEEDED", flow.row(second)["output_json"]
    context = json.loads(flow.transport.calls[-1]["body"]["input"][1]["content"])
    assert context["portfolio"]["execution_state"]["uncertain_order_ids"] == [intent_id]
    assert context["portfolio"]["open_orders"][0]["state"] == "UNKNOWN"


@pytest.mark.parametrize("role", ["learning", "optimisation"])
def test_other_department_instructions_do_not_inherit_research_finding_rules(tmp_path, role):
    flow = RuntimeFlow(tmp_path)
    flow.add(role)
    assert flow.run(role) == 1
    instructions = flow.transport.calls[0]["body"]["input"][0]["content"]
    assert "findings[].source_ref" not in instructions


def test_subscription_receives_field_specific_research_and_valuation_context(tmp_path):
    from tests.integration.test_subscription_departments import flow as subscription_flow
    setup, _protected, cli = subscription_flow(tmp_path)
    _observation(setup)
    setup.office.execution.ledger.deposit(setup.pid, "USD", Decimal("10000"), "synthetic-opening")
    research = setup.add("research")
    assert setup.run("research") == 1
    assert setup.row(research)["status"] == "SUCCEEDED", setup.row(research)["output_json"]
    assert "sources[].source_ref" in cli.requests[0].instructions
    assert "evidence_refs alone" in cli.requests[0].context["analysis_instruction"]
    trader = setup.add("trader")
    assert setup.run("trader") == 1
    assert setup.row(trader)["status"] == "SUCCEEDED", setup.row(trader)["output_json"]
    portfolio = cli.requests[-1].context["portfolio"]
    assert portfolio["reporting_valuation"]["provisional"]
    assert portfolio["cash"] == {"USD": "10000"}
    assert portfolio["execution_state"]["uncertain_order_ids"] == []
