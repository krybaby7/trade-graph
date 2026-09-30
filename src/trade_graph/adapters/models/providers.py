"""OpenAI Responses and Anthropic Messages translations. Fixtures need no credentials."""

from __future__ import annotations

import json
import uuid
from typing import Any

from trade_graph.contracts.models import ModelCapabilities, ModelRequest, ModelResult, ModelUsage, ToolRequest

CAPABILITIES: dict[str, dict[str, bool]] = {
    "openai:gpt-6-luna": {"structured_output": True, "forced_tool": True, "sampling_temperature": False},
    "openai:gpt-6.1-sol": {"structured_output": True, "forced_tool": True, "sampling_temperature": False},
    "anthropic:claude-sonnet-5-5": {
        "structured_output": True,
        "forced_tool": False,
        "sampling_temperature": False,
    },
    "scripted:scripted": {"structured_output": True, "forced_tool": False, "sampling_temperature": False},
}


def capability_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def lookup_capabilities(provider: str, model: str) -> ModelCapabilities | None:
    raw = CAPABILITIES.get(capability_key(provider, model))
    if raw is None:
        return None
    return ModelCapabilities(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        structured_output=raw["structured_output"],
        forced_tool=raw["forced_tool"],
        sampling_temperature=raw["sampling_temperature"],
    )


class OpenAIAdapter:
    provider = "openai"
    endpoint = "https://api.openai.com/v1/responses"

    def capabilities(self, model: str) -> ModelCapabilities | None:
        return lookup_capabilities(self.provider, model)

    def build_body(self, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "input": [
                {"role": "system", "content": request.instructions},
                {"role": "user", "content": json.dumps(request.context, default=str)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.output_schema,
                    "strict": True,
                }
            },
            "max_output_tokens": request.max_output_tokens,
        }
        tools = request.context.get("tools")
        if tools:
            body["tools"] = tools
        for item in request.context.get("tool_results") or []:
            body["input"].append(
                {
                    "type": "function_call_output",
                    "call_id": item["call_id"],
                    "output": json.dumps(item["output"]),
                }
            )
        return body

    def parse(self, payload: dict[str, Any]) -> ModelResult:
        if (payload.get("error") or {}).get("code") == "rate_limit_exceeded":
            return ModelResult(ok=False, failure="rate_limit", message="rate limit", usage=_openai_usage(payload))
        if (payload.get("error") or {}).get("type") == "timeout":
            return ModelResult(ok=False, failure="timeout_uncertain", message="timeout")
        incomplete = (payload.get("incomplete_details") or {}).get("reason")
        if incomplete in {"max_output_tokens", "length"} or payload.get("status") == "incomplete":
            if incomplete != "refusal":
                return ModelResult(
                    ok=False,
                    failure="truncation" if incomplete != "refusal" else "refusal",
                    message=str(incomplete),
                    usage=_openai_usage(payload),
                )
        tool_requests = _openai_tools(payload)
        if isinstance(tool_requests, ModelResult):
            return tool_requests
        if tool_requests:
            return ModelResult(
                ok=True,
                tool_requests=tool_requests,
                usage=_openai_usage(payload),
                provider_model=payload.get("model"),
                raw_redacted=json.dumps({"id": payload.get("id")}),
            )
        text = _openai_text(payload)
        if payload.get("status") == "refusal" or (text and text.get("refusal")):
            return ModelResult(ok=False, failure="refusal", message="refusal", usage=_openai_usage(payload))
        if not text:
            return ModelResult(ok=False, failure="validation", message="empty output", usage=_openai_usage(payload))
        try:
            parsed = json.loads(text["text"])
        except json.JSONDecodeError:
            return ModelResult(ok=False, failure="validation", message="bad json", usage=_openai_usage(payload))
        if not isinstance(parsed, dict):
            return ModelResult(ok=False, failure="validation", message="output must be an object",
                               usage=_openai_usage(payload))
        return ModelResult(
            ok=True,
            payload=parsed,
            usage=_openai_usage(payload),
            provider_model=payload.get("model"),
            raw_redacted=json.dumps({"id": payload.get("id")}),
        )


class AnthropicAdapter:
    provider = "anthropic"
    endpoint = "https://api.anthropic.com/v1/messages"

    def capabilities(self, model: str) -> ModelCapabilities | None:
        return lookup_capabilities(self.provider, model)

    def build_body(self, request: ModelRequest) -> dict[str, Any]:
        schema = dict(request.output_schema)
        schema.setdefault("additionalProperties", False)
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": request.instructions,
            "messages": [{"role": "user", "content": json.dumps(request.context, default=str)}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        tools = request.context.get("tools")
        if tools:
            body["tools"] = tools
        results = request.context.get("tool_results") or []
        if results:
            body["messages"].append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": item["call_id"],
                            "content": json.dumps(item["output"]),
                        }
                        for item in results
                    ],
                }
            )
        return body

    def parse(self, payload: dict[str, Any]) -> ModelResult:
        error = payload.get("error") or {}
        if error.get("type") in {"rate_limit_error", "overloaded_error"} or payload.get("type") == "error":
            kind = "rate_limit" if "rate" in str(error.get("type", "")) else "temporary"
            if error.get("type") == "timeout":
                kind = "timeout_uncertain"
            return ModelResult(
                ok=False,
                failure=kind,
                message=str(error.get("message", "")),
                usage=_anthropic_usage(payload),
            )
        stop = payload.get("stop_reason")
        if stop == "max_tokens":
            return ModelResult(ok=False, failure="truncation", message="max_tokens", usage=_anthropic_usage(payload))
        if stop == "refusal":
            return ModelResult(ok=False, failure="refusal", message="refusal", usage=_anthropic_usage(payload))
        blocks = payload.get("content") or []
        tool_requests = _anthropic_tools(blocks)
        if isinstance(tool_requests, ModelResult):
            return tool_requests
        if tool_requests:
            return ModelResult(
                ok=True,
                tool_requests=tool_requests,
                usage=_anthropic_usage(payload),
                provider_model=payload.get("model"),
                raw_redacted=json.dumps({"id": payload.get("id")}),
            )
        text = next((block.get("text") for block in blocks if block.get("type") == "text"), None)
        if not text:
            return ModelResult(ok=False, failure="validation", message="empty", usage=_anthropic_usage(payload))
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ModelResult(ok=False, failure="validation", message="bad json", usage=_anthropic_usage(payload))
        if not isinstance(parsed, dict):
            return ModelResult(ok=False, failure="validation", message="output must be an object",
                               usage=_anthropic_usage(payload))
        return ModelResult(
            ok=True,
            payload=parsed,
            usage=_anthropic_usage(payload),
            provider_model=payload.get("model"),
            raw_redacted=json.dumps({"id": payload.get("id")}),
        )


class ScriptedAdapter:
    provider = "scripted"

    def __init__(self, outputs: dict[str, dict[str, Any]] | None = None) -> None:
        self.outputs = outputs or {}

    def capabilities(self, model: str) -> ModelCapabilities | None:
        return lookup_capabilities(self.provider, model)

    def build_body(self, request: ModelRequest) -> dict[str, Any]:
        return {"model": request.model, "input": request.instructions, "scripted": True}

    def parse(self, payload: dict[str, Any]) -> ModelResult:
        failure = payload.get("failure")
        if failure:
            return ModelResult(ok=False, failure=failure, message=str(payload.get("message", "")))
        body = payload.get("payload")
        if not isinstance(body, dict):
            return ModelResult(ok=False, failure="validation", message="no scripted payload")
        return ModelResult(
            ok=True,
            payload=body,
            usage=ModelUsage(uncached_input_tokens=0, billed_output_tokens=0, provider_request_id="scripted"),
            provider_model="scripted",
        )

    def complete(self, request: ModelRequest) -> ModelResult:
        scripted = request.context.get("scripted_result")
        if isinstance(scripted, dict) and scripted.get("failure"):
            return ModelResult(ok=False, failure=scripted["failure"], message=scripted.get("message", ""))
        payload = scripted or self.outputs.get(request.role) or request.context.get("fallback_payload")
        if not isinstance(payload, dict):
            return ModelResult(ok=False, failure="validation", message="no scripted payload")
        usage = ModelUsage(
            uncached_input_tokens=int(request.context.get("input_tokens", 10)),
            billed_output_tokens=int(request.context.get("output_tokens", 5)),
            provider_request_id=f"scripted-{request.task_id}-{uuid.uuid4().hex}",
        )
        return ModelResult(ok=True, payload=payload, usage=usage, provider_model="scripted")


def _openai_usage(payload: dict[str, Any]) -> ModelUsage | None:
    usage = payload.get("usage")
    if not usage:
        return None
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    written = int((usage.get("input_tokens_details") or {}).get("cache_write_tokens") or 0)
    input_tokens = int(usage.get("input_tokens") or 0)
    reasoning = int((usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
    return ModelUsage(
        uncached_input_tokens=input_tokens - cached - written,
        cache_write_tokens=written,
        cache_read_tokens=cached,
        billed_output_tokens=int(usage.get("output_tokens") or 0),
        reasoning_tokens=reasoning,
        provider_request_id=payload.get("id"),
    )


def _openai_tools(payload: dict[str, Any]) -> list[ToolRequest] | ModelResult:
    requests: list[ToolRequest] = []
    for item in payload.get("output") or []:
        if item.get("type") != "function_call":
            continue
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return ModelResult(
                    ok=False,
                    failure="validation",
                    message="bad tool arguments",
                    usage=_openai_usage(payload),
                )
        if not isinstance(arguments, dict):
            return ModelResult(
                ok=False,
                failure="validation",
                message="tool arguments must be an object",
                usage=_openai_usage(payload),
            )
        requests.append(
            ToolRequest(
                call_id=str(item.get("call_id") or ""),
                name=str(item.get("name") or ""),
                arguments=arguments,
            )
        )
    return requests


def _anthropic_tools(blocks: list[dict[str, Any]]) -> list[ToolRequest] | ModelResult:
    requests: list[ToolRequest] = []
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        arguments = block.get("input")
        if not isinstance(arguments, dict):
            return ModelResult(ok=False, failure="validation", message="tool arguments must be an object")
        requests.append(
            ToolRequest(
                call_id=str(block.get("id") or ""),
                name=str(block.get("name") or ""),
                arguments=arguments,
            )
        )
    return requests


def _openai_text(payload: dict[str, Any]) -> dict[str, Any] | None:
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                return content
            if content.get("type") == "refusal":
                return {"refusal": True, "text": content.get("refusal", "")}
    return None


def _anthropic_usage(payload: dict[str, Any]) -> ModelUsage | None:
    usage = payload.get("usage")
    if not usage:
        return None
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    input_tokens = int(usage.get("input_tokens") or 0)
    return ModelUsage(
        uncached_input_tokens=input_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        billed_output_tokens=int(usage.get("output_tokens") or 0),
        provider_request_id=payload.get("id"),
    )
