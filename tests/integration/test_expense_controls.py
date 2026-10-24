"""Bounded repairs, priced fallbacks, and invoice differences. No live provider bill."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.models.transport import ScriptedProviderHttp
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway, ProviderFallback
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ModelRequest, PriceCard
from trade_graph.domain.clock import FrozenClock
from trade_graph.kernel.pricing import worst_case_cost


def _card(provider: str, model: str, card_id: str, output: str = "0.50") -> PriceCard:
    return PriceCard(
        price_card_id=card_id,
        provider=provider,
        model=model,
        endpoint="https://example.invalid",
        currency="USD",
        input_per_million="0.10",
        output_per_million=output,
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
        "task_id": "task-1",
        "root_task_id": "root-1",
        "run_id": "run-1",
        "system_version_id": "version-a",
        "provider": "openai",
        "model": "gpt-6-luna",
        "instructions": "decide",
        "context": {},
        "output_schema": {"type": "object"},
        "schema_name": "decision",
        "max_output_tokens": 50,
        "max_tool_calls": 0,
        "timeout_seconds": 5,
    }
    payload.update(updates)
    return ModelRequest.model_validate(payload)


def _success(provider: str = "openai", request_id: str = "resp_ok") -> dict:
    if provider == "anthropic":
        return {
            "id": request_id,
            "model": "claude-sonnet-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "{\"action\":\"hold\"}"}],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
    return {
        "id": request_id,
        "model": "gpt-6-luna",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "{\"action\":\"hold\"}"}]}],
        "usage": {"input_tokens": 4, "output_tokens": 2},
    }


def _stack(tmp_path, *, root: Decimal = Decimal("5"), total: Decimal = Decimal("5"), name: str = "expense.sqlite"):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / name)
    budget = BudgetGateway(database, clock)
    openai = _card("openai", "gpt-6-luna", "openai-card")
    anthropic = _card("anthropic", "claude-sonnet-5-5", "anthropic-card", output="1.00")
    budget.seed_card(openai)
    budget.seed_card(anthropic)
    budget.configure(
        deployment_id="deployment",
        currency="EUR",
        total=total,
        period=total,
        priority_reserve=Decimal("0"),
        daily=total,
        root=root,
        roles={"trader": total, "leader": total, "engineer": total},
    )
    return database, budget, clock


def _kinds(database: Database) -> list[str]:
    rows = database.execute(
        "SELECT attempt_kind FROM budget_reservations ORDER BY rowid"
    ).fetchall()
    return [row["attempt_kind"] for row in rows]


def test_schema_repair_and_transport_retry_share_the_attempt_budget(tmp_path) -> None:
    database, budget, _clock = _stack(tmp_path)
    transport = ScriptedProviderHttp(
        [
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "not-json"}]}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
            _success(request_id="resp_repair"),
        ]
    )
    gateway = ModelGateway(
        budget,
        transport=transport,
        paid_calls_enabled=True,
        api_keys={"openai": "fixture-key"},
    )
    repaired = gateway.invoke_bounded(
        _request(),
        deployment_id="deployment",
        price_card_id="openai-card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
        max_attempts=3,
        max_schema_repairs=1,
    )
    assert repaired.ok is True
    assert _kinds(database) == ["primary", "schema_repair"]
    assert len(transport.calls) == 2

    timeout_transport = ScriptedProviderHttp(
        [{"error": {"type": "timeout"}}, _success(request_id="resp_retry")]
    )
    gateway.transport = timeout_transport
    retried = gateway.invoke_bounded(
        _request(task_id="task-retry", root_task_id="root-retry"),
        deployment_id="deployment",
        price_card_id="openai-card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
        max_attempts=2,
        max_schema_repairs=0,
    )
    assert retried.ok is True
    assert len(timeout_transport.calls) == 2
    uncertain = database.execute(
        """SELECT state FROM budget_reservations
        WHERE root_task_id = 'root-retry' ORDER BY rowid"""
    ).fetchall()
    assert [row["state"] for row in uncertain] == ["UNCERTAIN", "COMMITTED"]


def test_root_limit_stops_a_retry_and_fallback_is_priced(tmp_path) -> None:
    card = _card("openai", "gpt-6-luna", "openai-card")
    one = worst_case_cost(card, 1000, 50, 0) * Decimal("0.9")
    database, budget, _clock = _stack(tmp_path, root=one, total=Decimal("5"))
    blocked = ScriptedProviderHttp([{"error": {"type": "timeout"}}, _success(request_id="resp_blocked")])
    gateway = ModelGateway(
        budget,
        transport=blocked,
        paid_calls_enabled=True,
        api_keys={"openai": "fixture-key", "anthropic": "fixture-key"},
        enabled_providers={"openai"},
    )
    stopped = gateway.invoke_bounded(
        _request(root_task_id="root-tight"),
        deployment_id="deployment",
        price_card_id="openai-card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
        max_attempts=3,
        max_schema_repairs=1,
        fallback=ProviderFallback("anthropic", "claude-sonnet-5-5", "anthropic-card"),
    )
    assert stopped.failure == "validation"
    assert stopped.message.startswith("room ")
    assert len(blocked.calls) == 1
    assert database.execute(
        "SELECT COUNT(*) AS n FROM budget_reservations WHERE attempt_kind = 'fallback'"
    ).fetchone()["n"] == 0

    database, budget, _clock = _stack(tmp_path, name="fallback.sqlite")
    transport = ScriptedProviderHttp(
        [
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
            _success("anthropic", request_id="msg_fallback"),
        ]
    )
    gateway = ModelGateway(
        budget,
        transport=transport,
        paid_calls_enabled=True,
        api_keys={"openai": "fixture-key", "anthropic": "fixture-key"},
    )
    fallen = gateway.invoke_bounded(
        _request(),
        deployment_id="deployment",
        price_card_id="openai-card",
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
        max_attempts=3,
        max_schema_repairs=0,
        fallback=ProviderFallback("anthropic", "claude-sonnet-5-5", "anthropic-card"),
    )
    assert fallen.ok is True
    assert fallen.payload == {"action": "hold"}
    assert _kinds(database) == ["primary", "fallback"]
    assert transport.calls[1]["url"] == "https://api.anthropic.com/v1/messages"
    assert transport.calls[1]["headers"]["x-api-key"] == "fixture-key"
    cards = database.execute(
        "SELECT price_card_id FROM budget_reservations ORDER BY rowid"
    ).fetchall()
    assert [row["price_card_id"] for row in cards] == ["openai-card", "anthropic-card"]
    models = database.execute("SELECT model FROM usage_receipts ORDER BY rowid").fetchall()
    assert [row["model"] for row in models] == ["gpt-6-luna", "claude-sonnet-5-5"]


def test_role_views_invoice_difference_and_paper_equity_do_not_refill(tmp_path) -> None:
    database, budget, clock = _stack(tmp_path)
    gateway = ModelGateway(budget, paid_calls_enabled=True)
    for role, task_id, version in (
        ("leader", "lead-task", "version-a"),
        ("engineer", "eng-task", "version-b"),
    ):
        result = gateway.invoke(
            _request(
                role=role,
                task_id=task_id,
                system_version_id=version,
                context={"http_fixture": _success(request_id=f"resp_{role}")},
            ),
            deployment_id="deployment",
            price_card_id="openai-card",
            fx_rate=Decimal("0.9"),
            fx_buffer=Decimal("1"),
        )
        assert result.ok is True
    views = budget.expense_views("deployment")
    assert views["global"] == budget.allowance("deployment") - budget.remaining("deployment")
    assert set(views["by_role"]) == {"leader", "engineer"}
    assert set(views["by_task"]) == {"lead-task", "eng-task"}
    assert set(views["by_version"]) == {"version-a", "version-b"}
    recorded = views["global"]
    gap = budget.reconcile_invoice("deployment", "inv-1", recorded + Decimal("0.25"))
    assert gap.unexplained == Decimal("0.25")
    again = budget.reconcile_invoice("deployment", "inv-1", recorded + Decimal("0.25"))
    assert again.reconciliation_id == gap.reconciliation_id
    with pytest.raises(ValueError, match="invoice reconciliation conflict"):
        budget.reconcile_invoice("deployment", "inv-1", recorded)
    before = budget.remaining("deployment")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "EUR", Decimal("10000"), "paper-open")
    assert ledger.equity(portfolio).equity == Decimal("10000")
    assert budget.remaining("deployment") == before
