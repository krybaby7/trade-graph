"""All paid attempts reserve first. Scripted calls are synthetic and do not spend the real allowance."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter, ScriptedAdapter
from trade_graph.application.budget import BudgetGateway
from trade_graph.contracts.models import ModelRequest, ModelResult
from trade_graph.domain.errors import BudgetExhausted, PaidCallsDisabled
from trade_graph.domain.protocols import InferenceAdapter


class ModelGateway:
    def __init__(self, budget: BudgetGateway, *, paid_calls_enabled: bool = False) -> None:
        self.budget = budget
        self.paid_calls_enabled = paid_calls_enabled
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
    ) -> ModelResult:
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
        synthetic = request.provider == "scripted"
        try:
            reservation = self.budget.reserve(
                deployment_id=deployment_id,
                role=request.role,
                task_id=request.task_id,
                root_task_id=request.root_task_id,
                price_card_id=price_card_id,
                max_input=int(request.context.get("max_input_tokens", 1000)),
                max_output=request.max_output_tokens,
                max_tools=request.max_tool_calls,
                fx_rate=fx_rate,
                fx_buffer=fx_buffer,
                priority=priority,
                synthetic=synthetic,
                purpose=request.role,
            )
        except BudgetExhausted as exc:
            return ModelResult(ok=False, failure="validation", message=str(exc))
        self.attempts.append(reservation)
        try:
            if request.provider == "scripted":
                result = self.scripted.complete(request)
            else:
                result = adapter.parse(request.context["http_fixture"])
            if result.failure == "timeout_uncertain" or result.usage is None:
                self.budget.mark_uncertain(reservation)
                return result
            self.budget.commit(
                reservation, result.usage, provider=request.provider,
                model=result.provider_model or request.model, fx_rate=fx_rate,
            )
        except (KeyError, TypeError, ValueError):
            self.budget.mark_uncertain(reservation)
            return ModelResult(ok=False, failure="validation", message="unresolved provider output or pricing")
        return result

    def _adapter(self, provider: str) -> InferenceAdapter:
        if provider == "openai":
            return self.openai
        if provider == "anthropic":
            return self.anthropic
        if provider == "scripted":
            return self.scripted
        raise PaidCallsDisabled(provider)
