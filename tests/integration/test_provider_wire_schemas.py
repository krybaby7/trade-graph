"""Provider dialect checks for real role contracts; no credential or provider call.

These static checks cover the reviewed official SDK subsets, not a claim that a
credentialed API accepted a request. Original JSON Schema and Pydantic gates stay
authoritative when a provider cannot enforce a constraint in its grammar.
"""

import json
from copy import deepcopy
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator, ValidationError
from pydantic import ValidationError as ModelValidationError
from tests.integration.test_cost_acceptance import _response, _stack

from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter
from trade_graph.application.gateway import ModelGateway, ProviderFallback, _validate_result
from trade_graph.application.runtime_departments import LearningReply, OptimisationReply, ResearchReply
from trade_graph.application.scheduler import Scheduler
from trade_graph.application.trader_workflow import TraderReply
from trade_graph.contracts.engineering import EngineerPatch
from trade_graph.contracts.leadership import LeaderReply
from trade_graph.contracts.models import ModelRequest

REPLIES = {
    "research": ResearchReply, "trader": TraderReply, "learning": LearningReply,
    "optimisation": OptimisationReply, "leader": LeaderReply, "engineer": EngineerPatch,
}
ADAPTERS = (OpenAIAdapter, AnthropicAdapter)


def _request(role="research", *, provider="openai", schema=None, context=None):
    return ModelRequest(
        role=role, task_id="synthetic-task", root_task_id="synthetic-task", run_id="synthetic-run",
        system_version_id="synthetic-version", provider=provider,
        model="gpt-6-luna" if provider == "openai" else "claude-sonnet-5-5",
        instructions="Use supplied synthetic evidence only.", context=context or {},
        output_schema=REPLIES[role].model_json_schema() if schema is None else schema,
        schema_name=REPLIES[role].__name__, max_output_tokens=3000, max_tool_calls=0, timeout_seconds=5,
    )


def _schema(adapter, request):
    body = adapter.build_body(request)
    return body["text"]["format"]["schema"] if adapter.provider == "openai" else (
        body["output_config"]["format"]["schema"]
    )


def _payload(role):
    department = {"evidence_refs": ["synthetic-evidence"], "summary": "Visible evidence inspected.",
                  "outcome": "Insufficient economic evidence."}
    if role == "research":
        value = {**department, "findings": [{"source_ref": "synthetic-evidence", "question": "Visible spread?",
            "claim": "Spread is observable.", "counterevidence": "One quote cannot establish an edge.",
            "invalidation": "New observations supersede this one.", "expires_after_seconds": 60}]}
    elif role == "learning":
        value = {**department, "lessons": [{"observation": "One hold decision.", "supporting_cases": [],
            "counterexamples": ["Future price may rise."], "explanation": "Holding is discretionary.",
            "proposed_improvement": "Review a forward sample.", "validation_method": "Held-out blocks.",
            "scope": "Synthetic BTC/USD", "sample_note": "One case.", "linked_decisions": ["synthetic-evidence"],
            "confidence_category": "insufficient", "status": "tentative", "process_assessment": "unclassified",
            "outcome_sign": "unknown"}]}
    elif role == "optimisation":
        value = {**department, "proposals": [{"issue": "Sparse evidence.", "resources": "Existing allocation.",
            "interval": "Next review.", "modification": "Retain counterevidence.",
            "expected_benefit": "Bounded context.",
            "quality_risk": "Rare case omitted.", "validation_metrics": ["Required obligations retained."]}],
            "changes": [{"objective": "Review context policy.", "allowed_classes": ["context_policy"],
                "allowed_paths": ["artifacts/context_policy.json"], "invariants": ["Keep counterevidence."],
                "max_spend_eur": "0.10", "max_steps": 1, "test_plan": "Run local acceptance.",
                "success_criteria": "Bounded valid context.", "rollback_criteria": "Missing evidence.",
                "expires_after_seconds": 60}]}
    elif role == "trader":
        value = {"action": "hold", "strategy_id": "synthetic-strategy", "rationale": "Insufficient visible evidence.",
                 "invalidation": "New available quote."}
    elif role == "leader":
        money = {"amount": "0.10", "currency": "EUR"}
        value = {"evidence_refs": ["synthetic-evidence"], "rationale": "Review the available evidence.",
            "intended_outcome": "Bounded tasks.", "review_criteria": "Preserve protected authority.", "actions": [
                {"kind": "assign", "role": "research", "objective": "Review source.", "budget": money},
                {"kind": "consult", "role": "learning", "objective": "Review cases.", "budget": money},
                {"kind": "commission", "change_id": "synthetic-change"},
                {"kind": "schedule", "role": "leader", "interval_seconds": 60},
                {"kind": "pause", "profile": "NO_NEW_EXPOSURE", "reason": "Review evidence."},
                {"kind": "allocate", "role": "engineer", "budget": money},
                {"kind": "activate", "candidate_id": "synthetic-candidate"},
                {"kind": "mandate", "mandate": {
                    "mandate_id": "synthetic-mandate", "portfolio_id": "synthetic-portfolio", "revision": 1,
                    "strategy_ids": ["synthetic-strategy"], "symbols": ["BTC/USD"], "allowed_order_types": ["market"],
                    "max_gross_exposure_fraction": "0.8", "max_single_asset_exposure_fraction": "0.2",
                    "decision_horizon_seconds": 60, "max_quote_age_seconds": 30,
                    "expires_at_utc": "2026-10-03T00:00:00Z", "resource_note": "Synthetic mandate."}},
            ]}
    else:
        value = {"summary": "Preserve the bounded source template.", "files": [
            {"path": "artifacts/report_template.txt", "content": "Synthetic report."}]}
    return REPLIES[role].model_validate(value).model_dump(mode="json")


def _check_dialect(schema, *, provider):
    assert schema["type"] == "object"
    common = {"type", "title", "description", "enum", "$defs", "$ref", "anyOf", "properties",
              "required", "additionalProperties", "items"}
    allowed = common | ({"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                         "pattern", "format", "minItems", "maxItems"} if provider == "openai" else (
                             {"allOf", "minItems", "format"}))
    pending = [schema]
    while pending:
        node = pending.pop()
        assert set(node).issubset(allowed), node
        if "$ref" in node:
            assert node["$ref"].startswith("#/$defs/")
            assert node["$ref"].split("/")[-1] in schema["$defs"]
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            if provider == "openai":
                assert node["required"] == list(node["properties"])
            pending.extend(node["properties"].values())
        if provider == "anthropic" and "minItems" in node:
            assert node["minItems"] in (0, 1)
        pending.extend(node.get("$defs", {}).values())
        for key in ("anyOf", "allOf"):
            pending.extend(node.get(key, []))
        if "items" in node:
            pending.append(node["items"])
    Draft202012Validator.check_schema(schema)


def _parse(adapter, payload):
    text = json.dumps(payload)
    if adapter.provider == "openai":
        envelope = {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}
    else:
        envelope = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    return adapter.parse({**envelope, "usage": {"input_tokens": 2, "output_tokens": 3}})


@pytest.mark.parametrize("role", REPLIES)
@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_actual_six_role_wire_dialects_and_original_typed_replies(role, adapter_type):
    adapter = adapter_type()
    request = _request(role, provider=adapter.provider)
    original = deepcopy(request.output_schema)
    wire = _schema(adapter, request)
    _check_dialect(wire, provider=adapter.provider)
    value = _payload(role)
    Draft202012Validator(wire).validate(value)
    result = _validate_result(request, _parse(adapter, value))
    assert result.ok and result.payload == value
    assert REPLIES[role].model_validate(result.payload).model_dump(mode="json") == value
    assert request.output_schema == original
    # Neither provider schema nor body may mutate the software contract afterwards.
    wire["properties"].clear()
    assert request.output_schema == original


@pytest.mark.parametrize("role", REPLIES)
@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_role_reply_extra_properties_rejected_by_wire_and_original(role, adapter_type):
    adapter = adapter_type()
    request = _request(role, provider=adapter.provider)
    value = {**_payload(role), "owner_budget": "999999"}
    with pytest.raises(ValidationError):
        Draft202012Validator(_schema(adapter, request)).validate(value)
    with pytest.raises(ValidationError):
        _validate_result(request, _parse(adapter, value))


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_nested_leader_tag_and_engineer_authority_remain_closed(adapter_type):
    adapter = adapter_type()
    for role, path in (("leader", "actions"), ("engineer", "files"), ("research", "findings")):
        request = _request(role, provider=adapter.provider)
        wire = _schema(adapter, request)
        value = _payload(role)
        value[path][0]["protected_approval"] = True
        with pytest.raises(ValidationError):
            Draft202012Validator(wire).validate(value)
        with pytest.raises(ValidationError):
            _validate_result(request, _parse(adapter, value))
    value = _payload("leader")
    value["actions"][2]["kind"] = "assign"
    request = _request("leader", provider=adapter.provider)
    with pytest.raises(ValidationError):
        Draft202012Validator(_schema(adapter, request)).validate(value)


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_defaulted_lists_do_not_become_nullable_or_fabricated_evidence(adapter_type):
    adapter = adapter_type()
    request = _request(provider=adapter.provider)
    original_omitted = {key: value for key, value in _payload("research").items() if key != "findings"}
    assert ResearchReply.model_validate(original_omitted).findings == []
    # Existing omitted-field behavior remains valid for software/scripted replies.
    assert _validate_result(request, _parse(adapter, original_omitted)).ok
    wire = _schema(adapter, request)
    if adapter.provider == "openai":
        with pytest.raises(ValidationError):
            Draft202012Validator(wire).validate(original_omitted)
    else:
        Draft202012Validator(wire).validate(original_omitted)
    null_findings = {**original_omitted, "findings": None}
    parsed = _parse(adapter, null_findings)
    assert parsed.payload == null_findings
    with pytest.raises(ValidationError):
        Draft202012Validator(wire).validate(null_findings)
    with pytest.raises(ValidationError):
        _validate_result(request, parsed)


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_trader_original_nullable_fields_and_nonnullable_defaults_stay_distinct(adapter_type):
    adapter = adapter_type()
    request = _request("trader", provider=adapter.provider)
    wire = _schema(adapter, request)
    value = _payload("trader")
    assert value["symbol"] is None and value["quantity"] is None and value["no_action_reason"] is None
    assert value["evidence_ids"] == [] and value["experiment"] is False
    Draft202012Validator(wire).validate(value)
    omitted = {key: value[key] for key in ("action", "strategy_id", "rationale", "invalidation")}
    assert _validate_result(request, _parse(adapter, omitted)).ok
    assert TraderReply.model_validate(omitted).model_dump(mode="json") == value
    for field in ("experiment", "evidence_ids"):
        invalid = {**value, field: None}
        with pytest.raises(ValidationError):
            Draft202012Validator(wire).validate(invalid)
        with pytest.raises(ValidationError):
            _validate_result(request, _parse(adapter, invalid))


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_original_constraint_and_model_validators_remain_authoritative(adapter_type):
    adapter = adapter_type()
    request = _request("research", provider=adapter.provider)
    value = _payload("research")
    value["summary"] = ""
    # Length is descriptive in the conservative provider schema, enforced locally.
    Draft202012Validator(_schema(adapter, request)).validate(value)
    with pytest.raises(ValidationError):
        _validate_result(request, _parse(adapter, value))
    value = _payload("research")
    value["findings"][0]["expires_after_seconds"] = 1
    if adapter.provider == "anthropic":
        Draft202012Validator(_schema(adapter, request)).validate(value)
    with pytest.raises(ValidationError):
        _validate_result(request, _parse(adapter, value))
    patch = _payload("engineer")
    patch["files"].append(deepcopy(patch["files"][0]))
    request = _request("engineer", provider=adapter.provider)
    result = _validate_result(request, _parse(adapter, patch))
    with pytest.raises(ModelValidationError, match="duplicate artifact path"):
        EngineerPatch.model_validate(result.payload)


@pytest.mark.parametrize("schema", [
    {"type": "array", "items": {"type": "string"}},
    {"anyOf": [{"type": "object", "properties": {}}]},
    {"type": "object", "properties": {"remote": {"$ref": "https://untrusted.invalid/schema"}}},
    {"type": "object", "properties": {"files": {"type": "object", "additionalProperties": {"type": "string"}}}},
])
@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_unsupported_roots_remote_references_and_dynamic_maps_fail_locally(schema, adapter_type):
    adapter = adapter_type()
    with pytest.raises(ValueError):
        adapter.build_body(_request(provider=adapter.provider, schema=schema))


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_registered_strict_function_wire_schema_is_copied_and_normalized(adapter_type):
    adapter = adapter_type()
    key = "parameters" if adapter.provider == "openai" else "input_schema"
    tool = {"name": "inspect", "strict": True, key: ResearchReply.model_json_schema()}
    if adapter.provider == "openai":
        tool["type"] = "function"
    request = _request(provider=adapter.provider, context={"tools": [tool]})
    original = deepcopy(request.context)
    emitted = adapter.build_body(request)["tools"][0][key]
    _check_dialect(emitted, provider=adapter.provider)
    Draft202012Validator(emitted).validate(_payload("research"))
    assert request.context == original


class RecordingTransport:
    """Raw recording fixture deliberately does not enforce a provider dialect."""

    def __init__(self, provider, value):
        self.provider, self.value, self.calls = provider, value, []

    def post_json(self, url, body, headers, *, timeout_seconds=None):
        self.calls.append({"url": url, "body": body, "headers": dict(headers)})
        return _response(self.provider, json.dumps(self.value))


@pytest.mark.parametrize("adapter_type", ADAPTERS)
@pytest.mark.parametrize("role", REPLIES)
def test_real_six_role_schema_goes_through_gateway_http_fixture(tmp_path, adapter_type, role):
    adapter = adapter_type()
    runtime = _stack(tmp_path)
    value = _payload(role)
    transport = RecordingTransport(adapter.provider, value)
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    request = _request(role, provider=adapter.provider)
    result = gateway.invoke(request, deployment_id="fixture", price_card_id=adapter.provider,
                            fx_rate=Decimal("1"), fx_buffer=Decimal("1"))
    assert result.ok and result.payload == value
    assert len(transport.calls) == 1
    body = transport.calls[0]["body"]
    wire = body["text"]["format"]["schema"] if adapter.provider == "openai" else (
        body["output_config"]["format"]["schema"]
    )
    _check_dialect(wire, provider=adapter.provider)
    Draft202012Validator(wire).validate(value)
    receipt = runtime.database.execute("SELECT * FROM usage_receipts").fetchone()
    assert receipt["synthetic"] == 1 and receipt["status"] == "committed"
    runtime.database.close()


@pytest.mark.parametrize("adapter_type", ADAPTERS)
@pytest.mark.parametrize("schema", [
    {"type": "array", "items": {"type": "string"}},
    {"type": "object", "properties": []},
    {"type": "object", "properties": {"bad": {"not": {"type": "null"}}}},
    {"type": "object", "properties": {"remote": {"$ref": "https://untrusted.invalid/schema"}}},
])
def test_unrepresentable_request_has_no_http_reservation_receipt_journal_or_event(tmp_path, adapter_type, schema):
    adapter = adapter_type()
    runtime = _stack(tmp_path)
    transport = RecordingTransport(adapter.provider, _payload("research"))
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    tables = ("budget_reservations", "usage_receipts", "model_invocations", "cost_allocations",
              "ledger_events", "activity_events")
    before = {name: runtime.database.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables}
    result = gateway.invoke(_request(provider=adapter.provider, schema=schema), deployment_id="fixture",
                            price_card_id=adapter.provider, fx_rate=Decimal("1"), fx_buffer=Decimal("1"),
                            invocation_id="synthetic-refused-attempt", portfolio_id=runtime.portfolio_id)
    after = {name: runtime.database.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in tables}
    assert result.failure == "unsupported" and result.usage is None
    assert transport.calls == [] and gateway.attempts == [] and after == before
    runtime.database.close()


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_durable_saved_reply_recovers_before_new_wire_preflight(tmp_path, monkeypatch, adapter_type):
    adapter = adapter_type()
    runtime = _stack(tmp_path)
    task = Scheduler(runtime.database, runtime.clock).add_task(
        role="research", objective="Synthetic provider schema test.", portfolio_id=runtime.portfolio_id,
        allocated_spend=Decimal("1"),
    )
    transport = RecordingTransport(adapter.provider, _payload("research"))
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    request = _request(provider=adapter.provider).model_copy(update={"task_id": task, "root_task_id": task})
    kwargs = {"deployment_id": "fixture", "price_card_id": adapter.provider, "fx_rate": Decimal("1"),
              "fx_buffer": Decimal("1"), "invocation_id": "synthetic-saved-attempt",
              "portfolio_id": runtime.portfolio_id}
    original = gateway.invoke(request, **kwargs)
    assert original.ok

    def refuse_new_body(_):
        pytest.fail("durable recovery attempted fresh schema build or HTTP dispatch")

    monkeypatch.setattr(gateway._adapter(adapter.provider), "build_body", refuse_new_body)
    assert gateway.invoke(request, **kwargs) == original
    assert len(transport.calls) == 1
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert runtime.database.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 1
    runtime.database.close()


def test_bounded_fallback_checks_destination_dialect_before_any_reservation(tmp_path):
    runtime = _stack(tmp_path)
    schema = {"type": "object", "properties": {"value": {"allOf": [
        {"type": "integer", "minimum": 1}, {"type": "integer", "maximum": 5}]}},
        "required": ["value"], "additionalProperties": False}
    transport = RecordingTransport("anthropic", {"value": 3})
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    result = gateway.invoke_bounded(
        _request(schema=schema), deployment_id="fixture", price_card_id="openai",
        fx_rate=Decimal("1"), fx_buffer=Decimal("1"), max_attempts=2,
        fallback=ProviderFallback(provider="anthropic", model="claude-sonnet-5-5", price_card_id="anthropic"),
    )
    assert result.ok and result.payload == {"value": 3}
    assert len(transport.calls) == 1 and transport.calls[0]["url"] == AnthropicAdapter.endpoint
    reservations = runtime.database.execute("SELECT * FROM budget_reservations").fetchall()
    assert len(reservations) == 1 and reservations[0]["role"] == "research"
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    runtime.database.close()
