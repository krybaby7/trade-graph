import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.scheduler import Scheduler
from trade_graph.contracts.models import ModelRequest, ModelUsage, PriceCard
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, BudgetExhausted, PaidCallsDisabled
from trade_graph.kernel.pricing import usage_cost
from trade_graph.orchestration.graph import run_once


def _card() -> PriceCard:
    return PriceCard(
        price_card_id="card",
        provider="openai",
        model="gpt-6-luna",
        endpoint="https://api.openai.com/v1/responses",
        currency="USD",
        input_per_million="0.10",
        output_per_million="0.50",
        cache_read_per_million="0.01",
        search_per_call="0.01",
        effective_at="2026-09-29",
        verified_at="2026-09-29",
        source_id="S01",
        tier="standard",
        context_band="short",
    )


def test_usage_does_not_double_count_reasoning_or_cache() -> None:
    card = _card()
    usage = ModelUsage(
        uncached_input_tokens=60,
        cache_read_tokens=40,
        billed_output_tokens=100,
        reasoning_tokens=50,
        tool_units=0,
    )
    cost = usage_cost(card, usage)
    expected = (
        Decimal(60) / Decimal(1_000_000) * Decimal("0.10")
        + Decimal(40) / Decimal(1_000_000) * Decimal("0.01")
        + Decimal(100) / Decimal(1_000_000) * Decimal("0.50")
    )
    assert cost == expected


def test_provider_fixtures_cover_failures() -> None:
    openai = OpenAIAdapter()
    body = openai.build_body(
        ModelRequest(
            role="trader",
            task_id="t",
            root_task_id="t",
            run_id="r",
            system_version_id="v",
            provider="openai",
            model="gpt-6-luna",
            instructions="decide",
            context={},
            output_schema={"type": "object", "additionalProperties": False},
            schema_name="decision",
            max_output_tokens=50,
            max_tool_calls=0,
            timeout_seconds=5,
        )
    )
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    ok = openai.parse(
        {
            "id": "resp_1",
            "model": "gpt-6-luna",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"action\":\"hold\"}"}]}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "input_tokens_details": {"cached_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        }
    )
    assert ok.ok is True
    assert ok.usage is not None
    assert ok.usage.uncached_input_tokens == 7
    assert ok.usage.billed_output_tokens == 4
    assert ok.usage.reasoning_tokens == 2
    assert openai.parse({"error": {"code": "rate_limit_exceeded"}}).failure == "rate_limit"
    truncated = openai.parse(
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
    )
    assert truncated.failure == "truncation"
    assert openai.parse({"error": {"type": "timeout"}}).failure == "timeout_uncertain"
    anthropic = AnthropicAdapter()
    anthropic_body = anthropic.build_body(
        ModelRequest(
            role="engineer",
            task_id="t",
            root_task_id="t",
            run_id="r",
            system_version_id="v",
            provider="anthropic",
            model="claude-sonnet-5-5",
            instructions="edit",
            context={},
            output_schema={"type": "object"},
            schema_name="patch",
            max_output_tokens=50,
            max_tool_calls=0,
            timeout_seconds=5,
        )
    )
    assert anthropic_body["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in anthropic_body
    parsed = anthropic.parse(
        {
            "id": "msg_1",
            "model": "claude-sonnet-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "{\"files\":{}}"}],
            "usage": {
                "input_tokens": 8,
                "output_tokens": 3,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 1,
            },
        }
    )
    assert parsed.ok is True
    assert parsed.usage is not None
    assert parsed.usage.uncached_input_tokens == 5
    refusal = anthropic.parse(
        {"stop_reason": "refusal", "usage": {"input_tokens": 1, "output_tokens": 1}}
    )
    assert refusal.failure == "refusal"
    truncated_anthropic = anthropic.parse(
        {"stop_reason": "max_tokens", "usage": {"input_tokens": 1, "output_tokens": 1}}
    )
    assert truncated_anthropic.failure == "truncation"
    assert anthropic.parse({"type": "error", "error": {"type": "rate_limit_error"}}).failure == "rate_limit"


def test_paid_calls_disabled_and_unknown_cost_stays_reserved(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "b.sqlite")
    budget = BudgetGateway(database, clock)
    budget.seed_card(_card())
    budget.configure(
        deployment_id="deployment",
        currency="EUR",
        total=Decimal("5"),
        period=Decimal("5"),
        priority_reserve=Decimal("1"),
        daily=Decimal("5"),
        root=Decimal("5"),
        roles={"trader": Decimal("4")},
    )
    gateway = ModelGateway(budget, paid_calls_enabled=False)
    request = ModelRequest(
        role="trader",
        task_id="t",
        root_task_id="t",
        run_id="r",
        system_version_id="v",
        provider="openai",
        model="gpt-6-luna",
        instructions="x",
        context={"http_fixture": {"error": {"type": "timeout"}}},
        output_schema={"type": "object"},
        schema_name="decision",
        max_output_tokens=100,
        max_tool_calls=0,
        timeout_seconds=5,
    )
    with pytest.raises(PaidCallsDisabled):
        gateway.invoke(
            request,
            deployment_id="deployment",
            price_card_id="card",
            fx_rate=Decimal("0.90"),
            fx_buffer=Decimal("1"),
        )
    gateway.paid_calls_enabled = True
    result = gateway.invoke(
        request,
        deployment_id="deployment",
        price_card_id="card",
        fx_rate=Decimal("0.90"),
        fx_buffer=Decimal("1"),
    )
    assert result.failure == "timeout_uncertain"
    assert budget.remaining("deployment") < Decimal("5")
    row = database.execute("SELECT state FROM budget_reservations").fetchone()
    assert row["state"] == "UNCERTAIN"


def test_concurrent_reservations_cannot_oversubscribe(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "c.sqlite")
    budget = BudgetGateway(database, clock)
    card = _card().model_copy(update={"output_per_million": Decimal("1")})
    budget.seed_card(card)
    budget.configure(
        deployment_id="deployment",
        currency="EUR",
        total=Decimal("1"),
        period=Decimal("1"),
        priority_reserve=Decimal("0"),
        daily=Decimal("1"),
        root=Decimal("1"),
        roles={},
    )
    errors: list[str] = []

    def work() -> None:
        try:
            budget.reserve(
                deployment_id="deployment",
                role="trader",
                task_id="t",
                root_task_id="root",
                price_card_id="card",
                max_input=0,
                max_output=600_000,
                max_tools=0,
                fx_rate=Decimal("1"),
                fx_buffer=Decimal("1"),
                priority=True,
                synthetic=False,
                purpose="race",
            )
        except BudgetExhausted:
            errors.append("blocked")

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == ["blocked"]
    assert budget.remaining("deployment") == Decimal("0.4")


def test_scheduler_lease_and_graph_idempotency(tmp_path) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "s.sqlite")
    scheduler = Scheduler(database, clock)
    portfolio = "p"
    root = scheduler.add_task(role="leader", objective="root", portfolio_id=portfolio)
    parent = root
    for index in range(3):
        parent = scheduler.add_task(
            role="trader",
            objective=str(index),
            portfolio_id=portfolio,
            root_task_id=root,
            parent_id=parent,
        )
    with pytest.raises(AuthorityDenied):
        scheduler.add_task(
            role="trader",
            objective="too deep",
            portfolio_id=portfolio,
            root_task_id=root,
            parent_id=parent,
        )
    assert scheduler.acquire_process_lease("core", "worker-a") is True
    assert scheduler.acquire_process_lease("core", "worker-b") is False
    scheduler.ensure_schedule(portfolio, "trader", 3600, "coalesce")
    clock.advance(10)
    database.execute("UPDATE schedules SET next_due_at = ? WHERE name = 'trader'", ("2020-01-01T00:00:00.000000Z",))
    first = scheduler.coalesce_due(portfolio, "trader", "trader")
    second = scheduler.coalesce_due(portfolio, "trader", "trader")
    assert first is not None
    assert second is None
    calls = []

    def worker(task_id: str) -> None:
        if task_id in calls:
            return
        calls.append(task_id)

    run_once("task-1", tmp_path / "checkpoints.sqlite", worker)
    run_once("task-1", tmp_path / "checkpoints.sqlite", worker)
    assert calls == ["task-1"]
