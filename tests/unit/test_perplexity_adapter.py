"""Perplexity Agent API translation (T36). Synthetic fixtures only: no key and no network."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from trade_graph.adapters.models.perplexity import (
    AGENT_ENDPOINT,
    PERPLEXITY_SONNET,
    PerplexityAgentAdapter,
    search_tool_definition,
)
from trade_graph.adapters.models.transport import HttpxProviderHttp, provider_payload
from trade_graph.contracts.models import ModelRequest
from trade_graph.domain.protocols import InferenceAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "perplexity"


def _fixture(name: str) -> dict:
    # Same decoding as the real transport: decimals stay Decimal, duplicate keys refused.
    return provider_payload((FIXTURES / name).read_bytes())


def _request(**context) -> ModelRequest:
    return ModelRequest(
        role="research", task_id="task", root_task_id="root", run_id="run", system_version_id="v1",
        provider="perplexity", model=PERPLEXITY_SONNET, instructions="Research the question.",
        context={"question": "Did Solana have an outage?", "tools": [search_tool_definition()],
                 "max_input_tokens": 9000, **context},
        output_schema={"type": "object", "properties": {"summary": {"type": "string", "maxLength": 50}},
                       "required": ["summary"]},
        schema_name="web_research.reply-v1", max_output_tokens=800, max_tool_calls=2, timeout_seconds=60,
    )


def test_adapter_meets_the_provider_neutral_protocol() -> None:
    adapter = PerplexityAgentAdapter()
    assert isinstance(adapter, InferenceAdapter)
    assert adapter.provider == "perplexity"
    assert adapter.endpoint == AGENT_ENDPOINT == "https://api.perplexity.ai/v1/agent"
    caps = adapter.capabilities(PERPLEXITY_SONNET)
    assert caps is not None and caps.provider == "perplexity" and caps.model == "anthropic/claude-sonnet-5-5"
    assert caps.structured_output and caps.tool_continuation
    assert not caps.forced_tool and not caps.sampling_temperature
    assert adapter.capabilities("anthropic/claude-opus-5-5") is None
    assert adapter.capabilities("sonar-pro") is None


def test_body_offers_only_the_software_search_tool_and_keeps_control_fields_out_of_the_prompt() -> None:
    body = PerplexityAgentAdapter().build_body(_request())
    assert body["model"] == "anthropic/claude-sonnet-5-5"
    assert body["instructions"] == "Research the question."
    assert body["max_output_tokens"] == 800
    # Built-in Perplexity tools (web_search, fetch_url) are never enabled: search runs in software (D150).
    assert [tool["type"] for tool in body["tools"]] == ["function"]
    assert body["tools"][0]["name"] == "web_search"
    assert body["tools"][0]["parameters"]["additionalProperties"] is False
    message = body["input"][0]
    assert message["type"] == "message" and message["role"] == "user"
    prompt = json.loads(message["content"])
    assert prompt == {"question": "Did Solana have an outage?"}
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    # Perplexity requires a 1-64 character alphanumeric schema name.
    assert response_format["json_schema"]["name"] == "webresearchreplyv1"
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "maxLength" not in schema["properties"]["summary"]  # Claude dialect keeps it as a description hint
    assert "Domain constraints" in schema["properties"]["summary"]["description"]


@pytest.mark.parametrize("tool", [{"type": "web_search"}, {"type": "fetch_url"},
                                  {"type": "function", "name": "fetch_url", "parameters": {"type": "object"}}])
def test_body_refuses_provider_run_tools(tool) -> None:
    request = _request(tools=[search_tool_definition(), tool])
    with pytest.raises(ValueError, match="software-run"):
        PerplexityAgentAdapter().build_body(request)


def test_body_replays_each_software_tool_call_and_its_stored_output() -> None:
    transcript = [{"call_id": "call_1", "name": "web_search", "arguments": {"query": "SOL outage", "recency": "week"},
                   "output": {"status": "results", "results": [{"source_ref": "search:abc:1"}]}}]
    body = PerplexityAgentAdapter().build_body(_request(tool_results=transcript))
    call, output = body["input"][1:]
    assert call == {"type": "function_call", "call_id": "call_1", "name": "web_search",
                    "arguments": json.dumps({"query": "SOL outage", "recency": "week"}, sort_keys=True)}
    assert output["type"] == "function_call_output" and output["call_id"] == "call_1"
    assert json.loads(output["output"])["results"] == [{"source_ref": "search:abc:1"}]
    assert "tool_results" not in json.loads(body["input"][0]["content"])


def test_tool_call_turn_parses_arguments_and_usage() -> None:
    result = PerplexityAgentAdapter().parse(_fixture("agent_tool_call.json"))
    assert result.ok and result.payload is None
    assert [(call.call_id, call.name) for call in result.tool_requests] == [("call_synthetic_1", "web_search")]
    assert result.tool_requests[0].arguments == {"query": "Solana network outage October 2026", "recency": "week"}
    usage = result.usage
    assert (usage.uncached_input_tokens, usage.cache_read_tokens, usage.billed_output_tokens) == (1000, 200, 40)
    assert usage.tool_units == 0 and usage.unreported_fields == []
    assert usage.provider_request_id == "resp_synthetic_tool_1"
    assert result.provider_model == "anthropic/claude-sonnet-5-5"


def test_final_turn_parses_structured_output_and_keeps_the_reported_cost_for_comparison() -> None:
    result = PerplexityAgentAdapter().parse(_fixture("agent_final.json"))
    assert result.ok and result.tool_requests == []
    assert result.payload["findings"][0]["source_ref"] == "SOURCE_REF"
    assert (result.usage.uncached_input_tokens, result.usage.billed_output_tokens) == (2500, 300)
    redacted = json.loads(result.raw_redacted)
    assert redacted == {"id": "resp_synthetic_final_1", "reported_total_cost_usd": "0.008"}
    assert Decimal(redacted["reported_total_cost_usd"]) == Decimal("0.008")


def test_refusal_and_truncation_are_typed_failures_that_keep_usage() -> None:
    refusal = PerplexityAgentAdapter().parse(_fixture("agent_refusal.json"))
    assert (refusal.ok, refusal.failure) == (False, "refusal")
    assert refusal.usage.billed_output_tokens == 12
    truncated = PerplexityAgentAdapter().parse(_fixture("agent_truncated.json"))
    assert (truncated.ok, truncated.failure) == (False, "truncation")
    assert truncated.usage.billed_output_tokens == 2000


def test_missing_usage_is_absent_never_zero() -> None:
    result = PerplexityAgentAdapter().parse(_fixture("agent_missing_usage.json"))
    assert result.ok and result.payload == {"summary": "Nothing relevant found.", "findings": []}
    assert result.usage is None


def test_partial_or_invalid_usage_is_marked_unreported() -> None:
    payload = _fixture("agent_final.json")
    del payload["usage"]["output_tokens"]
    payload["usage"]["input_tokens"] = -5
    usage = PerplexityAgentAdapter().parse(payload).usage
    assert set(usage.unreported_fields) == {"billed_output_tokens", "uncached_input_tokens"}
    payload["usage"]["input_tokens"] = True
    assert "uncached_input_tokens" in PerplexityAgentAdapter().parse(payload).usage.unreported_fields


def test_reported_provider_tool_use_is_unresolved_because_those_tools_are_never_enabled() -> None:
    payload = _fixture("agent_final.json")
    payload["usage"]["tool_calls_details"] = {"fetch_url": {"invocation": 2}}
    usage = PerplexityAgentAdapter().parse(payload).usage
    assert "tool_units" in usage.unreported_fields
    payload["usage"]["tool_calls_details"] = {"web_search": {"invocation": 0}}
    assert PerplexityAgentAdapter().parse(payload).usage.unreported_fields == []


@pytest.mark.parametrize(("error", "failure"), [
    ({"type": "rate_limit_exceeded", "message": "slow down"}, "rate_limit"),
    ({"code": "rate_limit_exceeded"}, "rate_limit"),
    ({"type": "timeout", "message": "upstream timeout"}, "timeout_uncertain"),
    ({"type": "invalid_api_key"}, "credentials"),
    ({"type": "authentication_error"}, "credentials"),
    ({"type": "server_error"}, "temporary"),
])
def test_error_payloads_map_to_typed_failures(error, failure) -> None:
    result = PerplexityAgentAdapter().parse({"id": "resp_error", "error": error})
    assert (result.ok, result.failure) == (False, failure)
    assert "slow down" not in result.message  # provider text is not retained


@pytest.mark.parametrize("text", ["not json", "[1, 2]", ""])
def test_unusable_output_text_is_a_validation_failure(text) -> None:
    payload = _fixture("agent_missing_usage.json")
    payload["output"][0]["content"][0]["text"] = text
    payload["usage"] = {"input_tokens": 10, "output_tokens": 2}
    result = PerplexityAgentAdapter().parse(payload)
    assert (result.ok, result.failure) == (False, "validation")
    assert result.usage.billed_output_tokens == 2


def test_bad_tool_arguments_are_a_validation_failure_with_usage() -> None:
    payload = _fixture("agent_tool_call.json")
    payload["output"][0]["arguments"] = "{not json"
    result = PerplexityAgentAdapter().parse(payload)
    assert (result.ok, result.failure) == (False, "validation")
    assert result.usage.billed_output_tokens == 40


def test_the_pinned_transport_accepts_only_official_endpoints() -> None:
    from trade_graph.adapters.research.perplexity_search import SEARCH_ENDPOINT

    transport = HttpxProviderHttp(allowed_urls=(AGENT_ENDPOINT, SEARCH_ENDPOINT))
    assert transport.allowed_urls == ("https://api.perplexity.ai/v1/agent", "https://api.perplexity.ai/search")
    with pytest.raises(ValueError, match="fixed official provider endpoints"):
        HttpxProviderHttp(allowed_urls=("https://api.perplexity.ai/v1/other",))
    with pytest.raises(ValueError, match="outside the owner-pinned"):
        transport.post_json("https://api.openai.com/v1/responses", {}, {})


def test_raw_provider_responses_may_carry_fractional_numbers_but_other_bounds_stay_strict() -> None:
    """The real transport decodes 0.008 as Decimal; refusing it would fail every real Perplexity call."""
    from trade_graph.adapters.models.providers import OpenAIAdapter
    from trade_graph.application.gateway import _bounded_json, _parse_provider_result

    result = _parse_provider_result(PerplexityAgentAdapter(), _fixture("agent_final.json"))
    assert result.ok and json.loads(result.raw_redacted)["reported_total_cost_usd"] == "0.008"
    openai = _parse_provider_result(OpenAIAdapter(), provider_payload(json.dumps({
        "id": "resp_1", "model": "gpt-6-luna", "temperature": 1.0, "top_p": 1.0,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"a\": 1}"}]}],
        "usage": {"input_tokens": 3, "output_tokens": 1}}).encode()))
    assert openai.ok and openai.payload == {"a": 1}
    with pytest.raises(ValueError):
        _bounded_json({"context": Decimal("1.5")})
    with pytest.raises(ValueError):
        _bounded_json({"usage": Decimal("NaN")}, provider_numbers=True)
