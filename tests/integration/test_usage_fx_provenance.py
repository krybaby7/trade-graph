"""Exact retained FX links use synthetic sources and scripted wire responses."""

import json
from decimal import Decimal

import pytest
from tests.integration.test_cost_acceptance import _request, _reserve, _response, _stack
from tests.integration.test_runtime_models import RuntimeFlow

from trade_graph.adapters.models.transport import ScriptedProviderHttp
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.contracts.models import ModelUsage


def _source(runtime, *, rate_id="rate-one"):
    return runtime.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
        source="synthetic-expense-conversion", kind="reference", stale=False, rate_id=rate_id)


def test_exact_fx_source_is_frozen_before_dispatch_and_receipt_survives_source_drift(tmp_path):
    runtime = _stack(tmp_path)
    source = _source(runtime)
    reservation = _reserve(runtime, fx_rate=Decimal("0.9"), fx_rate_id=source)
    saved = dict(runtime.database.execute("SELECT * FROM budget_reservations").fetchone())
    runtime.database.execute("UPDATE fx_rates SET rate='0.8' WHERE rate_id=?", (source,))
    usage = ModelUsage(uncached_input_tokens=100, billed_output_tokens=2)
    receipt_id = runtime.budget.commit(reservation, usage, provider="openai", model="gpt-6-luna",
                                      fx_rate=Decimal("0.9"))
    receipt = dict(runtime.database.execute("SELECT * FROM usage_receipts").fetchone())
    assert receipt["fx_rate_id"] == source and receipt["fx_rate_value"] == "0.9"
    assert receipt["fx_source_json"] == saved["fx_source_json"]
    assert json.loads(receipt["fx_source_json"])["rate"] == "0.9"
    assert runtime.budget.commit(reservation, usage, provider="openai", model="gpt-6-luna",
                                 fx_rate=Decimal("0.9")) == receipt_id
    runtime.database.close()
    reopened = Database(tmp_path / "cost-acceptance.sqlite")
    assert reopened.execute("SELECT fx_source_json FROM usage_receipts").fetchone()[0] == saved["fx_source_json"]
    reopened.close()


@pytest.mark.parametrize("change", [
    "missing", "pair", "value", "stale", "future_observed", "future_valid", "future_retrieved", "oversized",
])
def test_invalid_link_fails_before_reservation_and_any_external_call(tmp_path, change):
    runtime = _stack(tmp_path)
    source = _source(runtime)
    edits = {
        "missing": "DELETE FROM fx_rates",
        "pair": "UPDATE fx_rates SET base='GBP'",
        "value": "UPDATE fx_rates SET rate='0.8'",
        "stale": "UPDATE fx_rates SET stale=1",
        "future_observed": "UPDATE fx_rates SET observed_at='2026-01-02T00:00:00.000000Z'",
        "future_valid": "UPDATE fx_rates SET valid_as_of='2026-01-02T00:00:00.000000Z'",
        "future_retrieved": "UPDATE fx_rates SET retrieved_at='2026-01-02T00:00:00.000000Z'",
        "oversized": "UPDATE fx_rates SET source=?",
    }
    runtime.database.execute(edits[change], ("s" * 8193,) if change == "oversized" else ())
    transport = ScriptedProviderHttp([_response("openai")])
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    with pytest.raises(ValueError, match="FX source"):
        gateway.invoke(_request("openai"), deployment_id="fixture", price_card_id="openai",
            fx_rate=Decimal("0.9"), fx_buffer=Decimal("1"), fx_rate_id=source)
    assert transport.calls == []
    assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    runtime.database.close()


def test_unlinked_conversion_records_used_value_without_manufacturing_matching_source(tmp_path):
    runtime = _stack(tmp_path)
    _source(runtime)
    reservation = _reserve(runtime, fx_rate=Decimal("0.9"))
    runtime.budget.commit(reservation, ModelUsage(uncached_input_tokens=100, billed_output_tokens=2),
        provider="openai", model="gpt-6-luna", fx_rate=Decimal("0.9"))
    receipt = runtime.database.execute("SELECT * FROM usage_receipts").fetchone()
    assert receipt["fx_rate_value"] == "0.9"
    assert receipt["fx_rate_id"] is None and receipt["fx_source_json"] is None
    runtime.database.close()


def test_linked_conversion_cannot_change_numeric_value_on_commit(tmp_path):
    runtime = _stack(tmp_path)
    source = _source(runtime)
    reservation = _reserve(runtime, fx_rate=Decimal("0.9"), fx_rate_id=source)
    with pytest.raises(ValueError, match="reserved source"):
        runtime.budget.commit(reservation, ModelUsage(uncached_input_tokens=100, billed_output_tokens=2),
            provider="openai", model="gpt-6-luna", fx_rate=Decimal("0.8"))
    assert runtime.database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "RESERVED"
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    runtime.database.close()


def test_protected_runtime_route_supplies_configured_source_for_actual_gateway_path(tmp_path):
    flow = RuntimeFlow(tmp_path, overrides={"fx_rate": "0.9", "fx_rate_id": "runtime-rate"})
    budget: BudgetGateway = flow.office.budget
    card = budget.card("primary")
    budget.seed_card(card.model_copy(update={"price_card_id": "usd-card", "currency": "USD"}))
    flow.config = flow.config.model_copy(update={"approved_price_card_ids": ["usd-card", "stronger"],
        "role_routes": {role: "usd-card" for role in flow.config.role_routes}})
    flow.engineer.ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"),
        source="synthetic runtime reference", kind="reference", stale=False, rate_id="runtime-rate")
    flow.assembly = flow.bind()
    task_id = flow.add("research")
    assert flow.run("research") == 1
    assert flow.row(task_id)["status"] == "SUCCEEDED", flow.row(task_id)["output_json"]
    receipt = flow.db.execute("SELECT * FROM usage_receipts").fetchone()
    assert receipt["fx_rate_id"] == "runtime-rate" and receipt["fx_rate_value"] == "0.9"
    assert json.loads(receipt["fx_source_json"])["base"] == "USD"
    assert len(flow.transport.calls) == 1
    flow.db.close()
