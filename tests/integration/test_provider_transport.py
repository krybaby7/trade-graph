"""Scripted provider HTTP. No API key and no live provider call."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter
from trade_graph.adapters.models.transport import ScriptedProviderHttp
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.contracts.models import ModelRequest, PriceCard
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import PaidCallsDisabled


def _card() -> PriceCard:
    return PriceCard(
        price_card_id="card",
        provider="openai",
        model="gpt-6-luna",
        endpoint="https://api.openai.com/v1/responses",
        currency="USD",
        input_per_million="0.10",
        output_per_million="0.50",
        search_per_call="0.01",
        effective_at="2026-09-29",
        verified_at="2026-09-29",
        source_id="S01",
        tier="standard",
        context_band="short",
    )


def _request(**updates) -> ModelRequest:
    payload = {
        "role": "trader",
        "task_id": "t",
        "root_task_id": "t",
        "run_id": "r",
        "system_version_id": "v",
        "provider": "openai",
        "model": "gpt-6-luna",
        "instructions": "decide",
        "context": {"tools": [{"name": "lookup"}]},
        "output_schema": {"type": "object"},
        "schema_name": "decision",
        "max_output_tokens": 50,
        "max_tool_calls": 1,
        "timeout_seconds": 5,
    }
    payload.update(updates)
    return ModelRequest.model_validate(payload)


def _gateway(tmp_path, transport, **kwargs) -> tuple[ModelGateway, Database]:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "provider.sqlite")
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
    gateway = ModelGateway(budget, transport=transport, paid_calls_enabled=True, **kwargs)
    return gateway, database


def test_tool_fixtures_parse_for_both_providers() -> None:
    parsed = OpenAIAdapter().parse(
        {
            "id": "resp_tool",
            "model": "gpt-6-luna",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{\"symbol\":\"BTC/USD\"}",
                }
            ],
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
    )
    assert parsed.ok is True
    assert parsed.tool_requests[0].arguments == {"symbol": "BTC/USD"}
    assert OpenAIAdapter().parse(
        {"output": [{"type": "function_call", "call_id": "c", "name": "lookup", "arguments": "not-json"}]}
    ).failure == "validation"
    anthropic = AnthropicAdapter().parse(
        {
            "id": "msg_tool",
            "model": "claude-sonnet-5-5",
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"symbol": "ETH/USD"}}],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }
    )
    assert anthropic.tool_requests[0].call_id == "toolu_1"


def test_scripted_transport_continues_one_tool_without_a_credential(tmp_path) -> None:
    transport = ScriptedProviderHttp(
        [
            {
                "id": "resp_tool",
                "model": "gpt-6-luna",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "lookup",
                        "arguments": "{\"symbol\":\"BTC/USD\"}",
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
            {
                "id": "resp_final",
                "model": "gpt-6-luna",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"action\":\"hold\"}"}]}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        ]
    )
    gateway, database = _gateway(
        tmp_path,
        transport,
        enabled_providers={"openai"},
        tools={"lookup": lambda arguments: {"bid": "100", "symbol": arguments["symbol"]}},
    )
    with pytest.raises(PaidCallsDisabled):
        gateway.paid_calls_enabled = False
        gateway.invoke(
            _request(),
            deployment_id="deployment",
            price_card_id="card",
            fx_rate=Decimal("0.9"),
            fx_buffer=Decimal("1"),
        )
    gateway.paid_calls_enabled = True
    disabled = gateway.invoke(
        _request(provider="anthropic", model="claude-sonnet-5-5"),
        deployment_id="deployment",
        price_card_id="card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert disabled.failure == "unsupported"
    assert transport.calls == []
    result = gateway.invoke(
        _request(),
        deployment_id="deployment",
        price_card_id="card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert result.ok is True
    assert result.payload == {"action": "hold"}
    assert len(transport.calls) == 2
    assert transport.calls[0]["headers"] == {}
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/responses"
    assert transport.calls[1]["body"]["input"][-1]["type"] == "function_call_output"
    assert transport.calls[1]["body"]["input"][-1]["output"] == '{"bid": "100", "symbol": "BTC/USD"}'
    assert gateway.budget.remaining("deployment") == Decimal("5")
    rows = database.execute("SELECT synthetic FROM usage_receipts").fetchall()
    assert [row["synthetic"] for row in rows] == [1, 1]


def test_unsupported_settings_do_not_call_transport(tmp_path) -> None:
    transport = ScriptedProviderHttp([{"id": "unused", "output": []}])
    gateway, _database = _gateway(tmp_path, transport, enabled_providers={"openai"})
    temperature = gateway.invoke(
        _request(context={"temperature": 0.2}),
        deployment_id="deployment",
        price_card_id="card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    unknown = gateway.invoke(
        _request(model="gpt-unlisted"),
        deployment_id="deployment",
        price_card_id="card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    bad_schema = OpenAIAdapter().parse(
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "not-json"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    assert temperature.failure == "unsupported"
    assert unknown.failure == "unsupported"
    assert bad_schema.failure == "validation"
    assert transport.calls == []


def test_unregistered_tool_does_not_continue(tmp_path) -> None:
    transport = ScriptedProviderHttp(
        [
            {
                "id": "resp_tool",
                "model": "gpt-6-luna",
                "output": [
                    {"type": "function_call", "call_id": "call_1", "name": "browse", "arguments": "{}"}
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        ]
    )
    gateway, _ = _gateway(tmp_path, transport, tools={})
    result = gateway.invoke(
        _request(),
        deployment_id="deployment",
        price_card_id="card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert result.failure == "validation"
    assert len(transport.calls) == 1
