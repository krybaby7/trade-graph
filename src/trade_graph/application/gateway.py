"""All paid attempts reserve first. Scripted calls are synthetic and do not spend the real allowance."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from inspect import Parameter, signature
from math import isfinite
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter, ScriptedAdapter
from trade_graph.adapters.models.transport import ProviderHttp, ProviderHttpResponseError
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.model_invocations import InvocationJournal
from trade_graph.contracts.models import ModelRequest, ModelResult
from trade_graph.domain.errors import BudgetExhausted, PaidCallsDisabled, ValidationFailure
from trade_graph.domain.protocols import InferenceAdapter


@dataclass(frozen=True)
class ProviderFallback:
    provider: Literal["openai", "anthropic", "scripted"]
    model: str
    price_card_id: str


class ModelGateway:
    def __init__(
        self,
        budget: BudgetGateway,
        *,
        paid_calls_enabled: bool = False,
        transport: ProviderHttp | None = None,
        api_keys: dict[str, str] | None = None,
        enabled_providers: set[str] | None = None,
        tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
    ) -> None:
        self.budget = budget
        self.paid_calls_enabled = paid_calls_enabled
        self.transport = transport
        self.api_keys = {name: value for name, value in (api_keys or {}).items() if value}
        self.enabled_providers = enabled_providers
        self.tools = tools or {}
        self.openai = OpenAIAdapter()
        self.anthropic = AnthropicAdapter()
        self.scripted = ScriptedAdapter()
        self.attempts: list[str] = []

    def invoke(
        self,
        request: ModelRequest,
        *,
        deployment_id: str,
        price_card_id: str,
        fx_rate: Decimal,
        fx_buffer: Decimal,
        priority: bool = False,
        attempt_kind: str = "primary",
        invocation_id: str | None = None,
        portfolio_id: str | None = None,
        authorize: Callable[[], None] | None = None,
        fx_rate_id: str | None = None,
    ) -> ModelResult:
        if invocation_id and (request.max_tool_calls != 0 or not portfolio_id):
            raise ValidationFailure("durable invocation requires scoped tool-free request")
        if self.enabled_providers is not None and request.provider not in self.enabled_providers:
            return ModelResult(ok=False, failure="unsupported", message="provider is not enabled for this installation")
        if request.provider != "scripted" and not self.paid_calls_enabled:
            raise PaidCallsDisabled(request.provider)
        adapter = self._adapter(request.provider)
        caps = adapter.capabilities(request.model)
        if caps is None:
            return ModelResult(ok=False, failure="unsupported", message="model is not on the approved registry")
        if request.context.get("temperature") is not None and not caps.sampling_temperature:
            return ModelResult(ok=False, failure="unsupported", message="sampling temperature is not supported")
        if request.context.get("forced_tool") and not caps.forced_tool:
            return ModelResult(ok=False, failure="unsupported", message="forced tool use is not supported")
        if (
            request.provider != "scripted"
            and "http_fixture" not in request.context
            and self.transport is None
        ):
            return ModelResult(ok=False, failure="unsupported", message="provider transport is not configured")
        transcript = [dict(item) for item in request.context.get("tool_results") or []]
        result = ModelResult(ok=False, failure="validation", message="provider attempt did not run")
        tools_used = 0
        for step in range(request.max_tool_calls + 1):
            current = request
            if step:
                context = dict(request.context)
                context["tool_results"] = transcript
                context.pop("http_fixture", None)
                current = request.model_copy(update={"context": context})
            result = self._attempt(
                current,
                adapter,
                deployment_id=deployment_id,
                price_card_id=price_card_id,
                fx_rate=fx_rate,
                fx_buffer=fx_buffer,
                priority=priority,
                attempt_kind=attempt_kind,
                invocation_id=invocation_id,
                portfolio_id=portfolio_id,
                authorize=authorize,
                fx_rate_id=fx_rate_id,
            )
            if not result.ok or not result.tool_requests:
                return result
            if step == request.max_tool_calls or tools_used + len(result.tool_requests) > request.max_tool_calls:
                return _invalid_result(result, "tool continuation exceeded the reserved steps")
            tools_used += len(result.tool_requests)
            for call in result.tool_requests:
                handler = self.tools.get(call.name)
                if handler is None:
                    return _invalid_result(result, f"tool {call.name} is not registered")
                transcript.append(
                    {"call_id": call.call_id, "name": call.name, "output": handler(dict(call.arguments))}
                )
        return result

    def invoke_bounded(
        self,
        request: ModelRequest,
        *,
        deployment_id: str,
        price_card_id: str,
        fx_rate: Decimal,
        fx_buffer: Decimal,
        priority: bool = False,
        max_attempts: int = 3,
        max_schema_repairs: int = 1,
        fallback: ProviderFallback | None = None,
        fx_rate_id: str | None = None,
    ) -> ModelResult:
        """Repair, retry and fallback each reserve. They share the root-task limit."""
        if max_attempts < 1 or max_schema_repairs < 0:
            return ModelResult(ok=False, failure="validation", message="attempt bounds are invalid")
        attempts_used = 0
        repairs_used = 0
        current = request
        kind = "primary"
        last = ModelResult(ok=False, failure="validation", message="no provider attempt")
        while attempts_used < max_attempts:
            attempts_used += 1
            last = self.invoke(
                current,
                deployment_id=deployment_id,
                price_card_id=price_card_id,
                fx_rate=fx_rate,
                fx_buffer=fx_buffer,
                priority=priority,
                attempt_kind=kind,
                fx_rate_id=fx_rate_id,
            )
            if last.ok or _budget_stop(last):
                return last
            if last.failure == "validation" and repairs_used < max_schema_repairs:
                repairs_used += 1
                kind = "schema_repair"
                context = dict(current.context)
                context["schema_repair"] = repairs_used
                current = current.model_copy(update={"context": context})
                continue
            if last.failure in {"timeout_uncertain", "rate_limit"}:
                kind = "transport_retry"
                continue
            break
        if fallback is None or attempts_used >= max_attempts or last.ok or _budget_stop(last):
            return last
        context = dict(request.context)
        context["fallback_from"] = f"{request.provider}:{request.model}"
        fallback_request = request.model_copy(
            update={"provider": fallback.provider, "model": fallback.model, "context": context}
        )
        return self.invoke(
            fallback_request,
            deployment_id=deployment_id,
            price_card_id=fallback.price_card_id,
            fx_rate=fx_rate,
            fx_buffer=fx_buffer,
            priority=priority,
            attempt_kind="fallback",
            fx_rate_id=fx_rate_id,
        )

    def _attempt(
        self,
        request: ModelRequest,
        adapter: InferenceAdapter,
        *,
        deployment_id: str,
        price_card_id: str,
        fx_rate: Decimal,
        fx_buffer: Decimal,
        priority: bool,
        attempt_kind: str,
        invocation_id: str | None = None,
        portfolio_id: str | None = None,
        authorize: Callable[[], None] | None = None,
        fx_rate_id: str | None = None,
    ) -> ModelResult:
        card = self.budget.card(price_card_id)
        if request.provider != "scripted" and (card.provider != request.provider or card.model != request.model):
            return ModelResult(ok=False, failure="unsupported", message="price card does not match the request")
        synthetic = request.provider == "scripted" or (
            "http_fixture" not in request.context and not self.api_keys.get(request.provider)
        )
        journal = InvocationJournal(self.budget)
        billing = {
            "deployment_id": deployment_id, "price_card_id": price_card_id, "fx_rate": fx_rate,
            "fx_buffer": fx_buffer, "priority": priority, "attempt_kind": attempt_kind,
        }
        # Keep existing durable hashes recoverable when no link was supplied.
        if fx_rate_id is not None:
            billing["fx_rate_id"] = fx_rate_id
        binding = journal.binding(request, portfolio_id or "", billing) if invocation_id else ""
        wire_body = None
        try:
            # The authorization check, reservation and dispatch intent commit together.
            # No database transaction is held over the external call.
            with self.budget.database.immediate():
                if authorize:
                    authorize()
                if invocation_id:
                    recovered = journal.recover(invocation_id, binding)
                    if recovered is not None:
                        return recovered
                if request.provider != "scripted":
                    try:
                        _bounded_json(request.output_schema)
                        Draft202012Validator.check_schema(request.output_schema)
                        wire_body = adapter.build_body(request)
                    except (KeyError, TypeError, ValueError, RuntimeError, SchemaError):
                        return ModelResult(ok=False, failure="unsupported",
                                           message="request cannot be represented by the provider schema dialect")
                reservation = self.budget.reserve(
                    deployment_id=deployment_id, role=request.role, task_id=request.task_id,
                    root_task_id=request.root_task_id, price_card_id=price_card_id,
                    max_input=int(request.context.get("max_input_tokens", 1000)),
                    max_output=request.max_output_tokens, max_tools=request.max_tool_calls,
                    fx_rate=fx_rate, fx_buffer=fx_buffer, priority=priority, synthetic=synthetic,
                    purpose=request.role, system_version_id=request.system_version_id, attempt_kind=attempt_kind,
                    fx_rate_id=fx_rate_id,
                )
                if invocation_id:
                    journal.start(invocation_id, binding, request, portfolio_id, reservation)
        except BudgetExhausted as exc:
            return ModelResult(ok=False, failure="validation", message=str(exc))
        self.attempts.append(reservation)
        try:
            if request.provider == "scripted":
                result = self.scripted.complete(request)
            else:
                result = _parse_provider_result(adapter, self._payload(request, adapter, wire_body))
        except (TimeoutError, OSError):
            result = ModelResult(ok=False, failure="timeout_uncertain", message="provider transport outcome uncertain")
        except ProviderHttpResponseError as exc:
            try:
                usage = adapter.parse(exc.usage_payload).usage
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError, RuntimeError):
                usage = None
            result = ModelResult(ok=False, failure=exc.failure, message=str(exc), usage=usage)
        except (KeyError, TypeError, ValueError, RuntimeError):
            result = ModelResult(ok=False, failure="validation", message="unresolved provider output or pricing")
        try:
            result = _validate_result(request, result)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError, RuntimeError,
                SchemaError, ValidationError, Unresolvable):
            result = _invalid_result(result, "invalid output or tool schema")
        # Recording cost facts must survive revoked authority or a stale worker. Only
        # the application effect/publication is fenced, never the real bill.
        with self.budget.database.immediate():
            state = "COMPLETED"
            if not self._usage_is_priced(request, result):
                result = _invalid_result(result, "resolved provider model has no approved price binding")
                self.budget.mark_uncertain(reservation)
                state = "UNCERTAIN"
            elif result.failure == "timeout_uncertain" or result.usage is None:
                self.budget.mark_uncertain(reservation)
                state = "UNCERTAIN"
            else:
                try:
                    receipt = self.budget.commit(
                        reservation, result.usage, provider=request.provider,
                        model=result.provider_model or request.model, fx_rate=fx_rate,
                    )
                    if invocation_id:
                        self.budget.allocate(receipt, {portfolio_id: Decimal("1")})
                except (KeyError, TypeError, ValueError, RuntimeError):
                    self.budget.mark_uncertain(reservation)
                    state = "UNCERTAIN"
                    result = ModelResult(ok=False, failure="validation",
                                         message="unresolved provider output or pricing")
            if invocation_id:
                journal.save(invocation_id, result, state)
                # Return exactly the bounded payload retained for recovery.
                return journal.recover(invocation_id, binding)
        return result

    def _usage_is_priced(self, request: ModelRequest, result: ModelResult) -> bool:
        """Runtime installations may require exact protected resolved-model pricing."""
        return True

    def _payload(self, request: ModelRequest, adapter: InferenceAdapter,
                 wire_body: dict[str, Any] | None = None) -> dict[str, Any]:
        fixture = request.context.get("http_fixture")
        if isinstance(fixture, dict):
            return fixture
        if self.transport is None:
            raise RuntimeError("provider transport is not configured")
        post = self.transport.post_json
        parameters = signature(post).parameters
        kwargs = {"timeout_seconds": request.timeout_seconds} if (
            "timeout_seconds" in parameters or any(p.kind == Parameter.VAR_KEYWORD for p in parameters.values())
        ) else {}
        # Legacy/custom transports keep their three-argument contract. Inspect before
        # calling: retrying after TypeError could duplicate an already-dispatched request.
        return post(
            getattr(adapter, "endpoint"), wire_body if wire_body is not None else adapter.build_body(request),
            self._headers(request.provider), **kwargs,
        )

    def _headers(self, provider: str) -> dict[str, str]:
        key = self.api_keys.get(provider, "")
        if not key:
            return {}
        if provider == "anthropic":
            return {"x-api-key": key}
        return {"Authorization": f"Bearer {key}"}

    def _adapter(self, provider: str) -> InferenceAdapter:
        if provider == "openai":
            return self.openai
        if provider == "anthropic":
            return self.anthropic
        if provider == "scripted":
            return self.scripted
        raise PaidCallsDisabled(provider)


def _budget_stop(result: ModelResult) -> bool:
    return result.failure == "validation" and result.message.startswith("room ")


def _invalid_result(result: ModelResult, message: str) -> ModelResult:
    """Validation must retain the paid attempt's supplied usage and attribution."""
    return result.model_copy(update={"ok": False, "failure": "validation", "message": message,
                                     "payload": None, "tool_requests": []})


def _validate_result(request: ModelRequest, result: ModelResult) -> ModelResult:
    if not result.ok:
        return result
    if not result.tool_requests:
        _validate_schema(result.payload, request.output_schema)
        return result
    _bounded_json([call.arguments for call in result.tool_requests])
    definitions = request.context.get("tools") or []
    allowed = {}
    for definition in definitions:
        if isinstance(definition, dict):
            definition = definition.get("function", definition)
            if isinstance(definition, dict):
                allowed[definition.get("name")] = definition
    call_ids = set()
    for call in result.tool_requests:
        if not call.call_id.strip() or call.call_id in call_ids or not call.name.strip() or call.name not in allowed:
            return _invalid_result(result, "tool identity is missing, repeated or undeclared")
        call_ids.add(call.call_id)
        definition = allowed[call.name]
        schema = definition.get("parameters", definition.get("input_schema", {}))
        _validate_schema(call.arguments, schema)
    return result


def _deny_reference(uri: str):
    raise NoSuchResource(ref=uri)


_LOCAL_SCHEMA_REGISTRY = Registry(retrieve=_deny_reference)
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 10_000
_MAX_JSON_BYTES = 256 * 1024


def _validate_schema(value: Any, schema: Any) -> None:
    _bounded_json(schema)
    _bounded_json(value)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, registry=_LOCAL_SCHEMA_REGISTRY).validate(value)


def _bounded_json(value: Any) -> None:
    """Bound schemas and data before recursive validation, including raw responses."""
    pending = [(value, 0)]
    nodes = size = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > _MAX_JSON_DEPTH or nodes > _MAX_JSON_NODES:
            raise ValueError("JSON depth or node bound exceeded")
        if isinstance(item, dict):
            if nodes + len(pending) + len(item) > _MAX_JSON_NODES:
                raise ValueError("JSON node bound exceeded")
            size += 2 + max(0, len(item) - 1) + len(item)
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > _MAX_JSON_BYTES:
                    raise ValueError("invalid or oversized JSON key")
                size += len(json.dumps(key, ensure_ascii=False).encode("utf-8"))
                pending.append((child, depth + 1))
        elif isinstance(item, list):
            if nodes + len(pending) + len(item) > _MAX_JSON_NODES:
                raise ValueError("JSON node bound exceeded")
            size += 2 + max(0, len(item) - 1)
            pending.extend((child, depth + 1) for child in item)
        else:
            if (not isinstance(item, (str, int, float, bool, type(None)))
                    or (isinstance(item, float) and not isfinite(item))
                    or (isinstance(item, str) and len(item) > _MAX_JSON_BYTES)):
                raise ValueError("invalid or oversized JSON scalar")
            size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if size > _MAX_JSON_BYTES or nodes + len(pending) > _MAX_JSON_NODES:
            raise ValueError("JSON size or node bound exceeded")


def _parse_provider_result(adapter: InferenceAdapter, payload: dict[str, Any]) -> ModelResult:
    try:
        _bounded_json(payload)
        return adapter.parse(payload)
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError, RuntimeError):
        # Parse only billing metadata if the response exceeds bounds or cannot be
        # decoded. Failure must not erase usage or the actual model attribution.
        metadata = {key: payload.get(key) for key in ("id", "model", "usage")} if isinstance(payload, dict) else {}
        try:
            billing = adapter.parse(metadata)
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError, RuntimeError):
            billing = ModelResult(ok=False, failure="validation")
        return _invalid_result(billing.model_copy(update={"provider_model": metadata.get("model")}),
                               "invalid or oversized provider output")
