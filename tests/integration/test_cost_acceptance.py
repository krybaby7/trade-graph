"""A05/A06/A17 local acceptance evidence. All prices, expenses and calls are fixtures.

Non-synthetic reservation rows below exercise real-budget arithmetic in temporary
databases only. Native provider fixtures are synthetic; actual probes remain pending.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest

from trade_graph.adapters.models.transport import ScriptedProviderHttp
from trade_graph.adapters.persistence.db import Database
from trade_graph.api import financial
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.gateway import ModelGateway
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import ModelRequest, ModelUsage, PriceCard
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.errors import BudgetExhausted

PROVIDERS = {"openai": "gpt-6-luna", "anthropic": "claude-sonnet-5-5"}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["hold"]}},
    "required": ["action"],
    "additionalProperties": False,
}
TOOL_SCHEMA = {
    "type": "object",
    "properties": {"symbol": {"type": "string"}},
    "required": ["symbol"],
    "additionalProperties": False,
}


def _stack(tmp_path, **limits):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "cost-acceptance.sqlite")
    budget = BudgetGateway(database, clock)
    values = {name: Decimal("10") for name in ("total", "period", "daily", "root")}
    values.update(limits)
    budget.configure(
        deployment_id="fixture",
        currency="EUR",
        priority_reserve=Decimal("0"),
        roles={"trader": Decimal("10"), "engineer": Decimal("10")},
        **values,
    )
    for provider, model in PROVIDERS.items():
        budget.seed_card(
            PriceCard(
                price_card_id=provider,
                provider=provider,
                model=model,
                endpoint="https://example.invalid",
                currency="USD",
                input_per_million="1",
                output_per_million="2",
                search_per_call="0.10",
                cache_read_per_million="0.25",
                cache_write_per_million="3",
                effective_at="2026-01-01",
                verified_at="2026-01-01",
                source_id="synthetic-fixture",
                tier="standard",
                context_band="short",
            )
        )
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "EUR", Decimal("100"), "synthetic-opening")
    return SimpleNamespace(
        database=database,
        budget=budget,
        ledger=ledger,
        clock=clock,
        portfolio_id=portfolio,
        deployment_id="fixture",
    )


def _reserve(runtime, **updates):
    values = dict(
        deployment_id="fixture",
        role="trader",
        task_id="task",
        root_task_id="root",
        price_card_id="openai",
        max_input=0,
        max_output=300_000,
        max_tools=0,
        fx_rate=Decimal("1"),
        fx_buffer=Decimal("1"),
        priority=False,
        synthetic=False,
        purpose="temporary cap arithmetic fixture",
        system_version_id="version-fixture",
    )
    values.update(updates)
    return runtime.budget.reserve(**values)


@pytest.mark.parametrize("correction", [Decimal("0.20"), Decimal("-0.20")], ids=["debit", "credit"])
def test_a05_supplied_invoice_correction_preserves_historical_reporting_and_legacy_fx(tmp_path, correction):
    runtime = _stack(tmp_path)
    started = utc_iso(runtime.clock.now())
    runtime.ledger.observe_fx(
        base="USD",
        quote="EUR",
        rate=Decimal("0.9"),
        source="synthetic opening reference",
        kind="reference",
        stale=False,
        rate_id="opening-fx-fixture",
    )
    receipt = runtime.budget.commit(
        _reserve(runtime, max_input=1_000_000, max_output=0, fx_rate=Decimal("0.9")),
        ModelUsage(uncached_input_tokens=1_000_000, billed_output_tokens=0, provider_request_id="fixture-usage"),
        provider="openai",
        model=PROVIDERS["openai"],
        fx_rate=Decimal("0.9"),
    )
    runtime.budget.allocate(receipt, {runtime.portfolio_id: Decimal("1")})
    original = dict(runtime.database.execute("SELECT * FROM usage_receipts").fetchone())
    historical = utc_iso(runtime.clock.now())
    runtime.clock.advance(60)
    reconciliation = runtime.budget.reconcile_invoice("fixture", "synthetic-invoice", Decimal("0.9") + correction)
    invoice = dict(runtime.database.execute("SELECT * FROM invoice_reconciliations").fetchone())
    runtime.ledger.observe_fx(
        base="USD",
        quote="EUR",
        rate=Decimal("0.5"),
        source="synthetic later reference",
        kind="reference",
        stale=False,
        rate_id="later-fx-fixture",
    )
    runtime.ledger.add_expense(
        runtime.portfolio_id,
        expense_id="supplied-invoice-correction",
        native_amount=correction * 2,
        native_currency="USD",
        reporting_amount=correction,
        reporting_currency="EUR",
        embedded=False,
        source=f"invoice:{reconciliation.reconciliation_id};corrects:receipt:{receipt}",
    )
    projected = financial.costs(runtime)
    assert Decimal(projected["actual_spend"]) == Decimal("0.9") + correction
    assert Decimal(projected["synthetic_spend"]) == 0
    recorded = projected["receipts"][0]
    adjustment = projected["ledger_expenses"][0]
    assert Decimal(recorded["native_cost"]) == 1
    assert Decimal(recorded["reporting_cost"]) == Decimal("0.9")
    assert Decimal(adjustment["reporting_amount"]) == correction
    assert adjustment["source"] == f"invoice:{reconciliation.reconciliation_id};corrects:receipt:{receipt}"
    for record, rate in ((recorded, Decimal("0.9")), (adjustment, Decimal("0.5"))):
        basis = record["original_fx_basis"]
        assert Decimal(basis["rate"]) == rate
        assert basis["source"] == "stored native and reporting accrual amounts"
        assert basis["rate_id"] is None and basis["provenance_complete"] is False
    assert dict(runtime.database.execute("SELECT * FROM usage_receipts").fetchone()) == original
    assert dict(runtime.database.execute("SELECT * FROM invoice_reconciliations").fetchone()) == invoice
    assert reconciliation.unexplained == correction
    assert projected["provisional"] is True  # Supplying an accrual does not erase reconciliation evidence.
    assert runtime.ledger.books(runtime.portfolio_id, historical).expenses == []
    assert (
        runtime.ledger.performance(runtime.portfolio_id, started, utc_iso(runtime.clock.now())).all_operating
        == correction
    )
    runtime.database.close()
    reopened = Database(tmp_path / "cost-acceptance.sqlite")
    runtime.database = reopened
    runtime.ledger = Ledger(reopened, runtime.clock)
    runtime.budget = BudgetGateway(reopened, runtime.clock)
    assert financial.costs(runtime) == projected
    reopened.close()


@pytest.mark.parametrize("cap", ["period", "root", "total"])
def test_a06_concurrent_requests_independently_enforce_each_cap(tmp_path, cap):
    runtime = _stack(tmp_path, **{cap: Decimal("1")})
    barrier = Barrier(2)

    def fake_paid_work(index):
        barrier.wait(timeout=5)
        try:
            reservation = _reserve(
                runtime,
                task_id=f"task-{index}",
                root_task_id="shared-root" if cap == "root" else f"root-{index}",
                role="trader" if index == 0 else "engineer",
            )
        except BudgetExhausted:
            return "blocked"
        # Fake dispatch only after the real BudgetGateway durably reserves the bound.
        row = runtime.database.execute(
            "SELECT state, amount FROM budget_reservations WHERE reservation_id = ?",
            (reservation,),
        ).fetchone()
        assert row["state"] == "RESERVED" and Decimal(row["amount"]) == Decimal("0.6")
        return "fake-dispatched"

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(fake_paid_work, range(2)))
    assert sorted(outcomes) == ["blocked", "fake-dispatched"]
    rows = runtime.database.execute("SELECT * FROM budget_reservations").fetchall()
    assert len(rows) == 1 and Decimal(rows[0]["amount"]) == Decimal("0.6")
    assert runtime.budget.remaining("fixture") == (Decimal("0.4") if cap == "total" else Decimal("9.4"))
    if cap == "period":
        runtime.clock.advance(31 * 86400)
        assert _reserve(runtime, root_task_id="next-month-root")
    elif cap == "root":
        assert _reserve(runtime, root_task_id="independent-root")
    else:
        runtime.clock.advance(31 * 86400)
        with pytest.raises(BudgetExhausted):
            _reserve(runtime, root_task_id="independent-root")
    runtime.database.close()


@pytest.mark.parametrize("currency", ["USD", "EUR"])
def test_a06_reserves_worst_input_output_and_paid_tools_before_fake_dispatch(tmp_path, currency):
    # Worst input category: 100k * USD3/M; output: 200k * USD2/M; tools: 3 * USD0.10.
    expected = Decimal("1.00") * (Decimal("0.9") if currency == "USD" else 1) * Decimal("1.10")
    runtime = _stack(tmp_path, total=expected)
    original = runtime.budget.card("openai")
    runtime.budget.seed_card(original.model_copy(update={"price_card_id": "bound-card", "currency": currency}))
    kwargs = dict(
        price_card_id="bound-card",
        max_input=100_000,
        max_output=200_000,
        max_tools=3,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1.10"),
    )
    reservation = _reserve(runtime, **kwargs)
    row = runtime.database.execute(
        "SELECT state, amount FROM budget_reservations WHERE reservation_id = ?",
        (reservation,),
    ).fetchone()
    assert row["state"] == "RESERVED" and Decimal(row["amount"]) == expected
    assert runtime.budget.remaining("fixture") == 0
    with pytest.raises(BudgetExhausted):
        _reserve(runtime, root_task_id="second-root", **kwargs)
    receipt = runtime.budget.commit(
        reservation,
        ModelUsage(
            uncached_input_tokens=0,
            cache_write_tokens=100_000,
            billed_output_tokens=200_000,
            reasoning_tokens=150_000,
            tool_units=3,
        ),
        provider="openai",
        model=PROVIDERS["openai"],
        fx_rate=Decimal("0.9"),
    )
    settled = runtime.database.execute("SELECT * FROM usage_receipts WHERE receipt_id = ?", (receipt,)).fetchone()
    assert Decimal(settled["native_cost"]) == Decimal("1.00")
    assert Decimal(settled["reporting_cost"]) == expected / Decimal("1.10")
    assert runtime.budget.remaining("fixture") == expected - Decimal(settled["reporting_cost"])
    runtime.database.close()


def _request(provider, **updates):
    tool = {"name": "lookup", "parameters" if provider == "openai" else "input_schema": TOOL_SCHEMA}
    if provider == "openai":
        tool["type"] = "function"
    values = dict(
        provider=provider,
        model=PROVIDERS[provider],
        role="trader",
        task_id="task",
        root_task_id="root",
        run_id="run-fixture",
        system_version_id="version-fixture",
        instructions="synthetic local fixture",
        context={"tools": [tool]},
        output_schema=OUTPUT_SCHEMA,
        schema_name="fixture-decision",
        max_output_tokens=50,
        max_tool_calls=1,
        timeout_seconds=5,
    )
    values.update(updates)
    return ModelRequest(**values)


def _response(
    provider,
    text='{"action":"hold"}',
    *,
    arguments=None,
    tool=False,
    call_id="call-fixture",
    name="lookup",
    request_id="fixture",
):
    payload = {
        "id": f"{provider}-{request_id}",
        "model": PROVIDERS[provider],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    if provider == "openai":
        payload.update(
            status="completed",
            output=[
                {"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments)}
                if tool
                else {"type": "message", "content": [{"type": "output_text", "text": text}]},
            ],
        )
    else:
        payload.update(
            stop_reason="tool_use" if tool else "end_turn",
            content=[
                {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
                if tool
                else {"type": "text", "text": text},
            ],
        )
    return payload


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "text",
    ["not-json", "[]", "null", "{}", '{"action":7}', '{"action":"buy"}', '{"action":"hold","extra":true}'],
    ids=["malformed-json", "array", "null", "required", "type", "enum", "extra-property"],
)
def test_a17_bad_structured_output_is_rejected_with_synthetic_usage(tmp_path, provider, text):
    runtime = _stack(tmp_path)
    transport = ScriptedProviderHttp([_response(provider, text)])
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    result = gateway.invoke(
        _request(provider),
        deployment_id="fixture",
        price_card_id=provider,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert not result.ok and result.failure == "validation"
    assert result.usage.uncached_input_tokens == 7 and result.usage.billed_output_tokens == 3
    assert len(transport.calls) == 1 and transport.calls[0]["headers"] == {}
    row = runtime.database.execute("SELECT * FROM usage_receipts").fetchone()
    assert row["synthetic"] == 1 and row["status"] == "committed"
    assert Decimal(row["native_cost"]) == Decimal("0.000013")
    assert runtime.budget.remaining("fixture") == 10
    runtime.database.close()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "arguments,call_id,name",
    [
        ([], "call-fixture", "lookup"),
        (None, "call-fixture", "lookup"),
        ({}, "call-fixture", "lookup"),
        ({"symbol": 7}, "call-fixture", "lookup"),
        ({"symbol": "TEST", "extra": True}, "call-fixture", "lookup"),
        ({"symbol": "TEST"}, "", "lookup"),
        ({"symbol": "TEST"}, "call-fixture", ""),
        ({"symbol": "TEST"}, "call-fixture", "undeclared"),
    ],
    ids=["array", "null", "required", "type", "extra-property", "missing-id", "missing-name", "undeclared"],
)
def test_a17_bad_tool_schema_never_runs_handler_and_retains_synthetic_usage(
    tmp_path, provider, arguments, call_id, name
):
    runtime = _stack(tmp_path)
    transport = ScriptedProviderHttp([_response(provider, arguments=arguments, tool=True, call_id=call_id, name=name)])
    calls = []
    gateway = ModelGateway(
        runtime.budget,
        paid_calls_enabled=True,
        transport=transport,
        tools={"lookup": lambda args: calls.append(args) or {}, "undeclared": lambda args: calls.append(args) or {}},
    )
    result = gateway.invoke(
        _request(provider),
        deployment_id="fixture",
        price_card_id=provider,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert not result.ok and result.failure == "validation"
    assert calls == [] and len(transport.calls) == 1
    assert result.usage.uncached_input_tokens == 7 and result.usage.billed_output_tokens == 3
    row = runtime.database.execute("SELECT * FROM usage_receipts").fetchone()
    assert row["synthetic"] == 1 and row["status"] == "committed"
    assert Decimal(row["native_cost"]) == Decimal("0.000013")
    assert runtime.budget.remaining("fixture") == 10
    runtime.database.close()


@pytest.mark.parametrize(
    "provider,setting",
    [
        ("openai", "temperature"),
        ("anthropic", "temperature"),
        ("openai", "unknown-model"),
        ("anthropic", "unknown-model"),
        ("anthropic", "forced-tool"),
    ],
)
def test_a17_unsupported_settings_fail_before_reservation_or_dispatch(tmp_path, provider, setting):
    runtime = _stack(tmp_path)
    transport = ScriptedProviderHttp([])
    gateway = ModelGateway(runtime.budget, paid_calls_enabled=True, transport=transport)
    request = _request(provider)
    if setting == "unknown-model":
        request = request.model_copy(update={"model": "unapproved-fixture-model"})
    else:
        request = request.model_copy(
            update={"context": {"temperature": 0} if setting == "temperature" else {"forced_tool": True}}
        )
    result = gateway.invoke(
        request, deployment_id="fixture", price_card_id=provider, fx_rate=Decimal("0.9"), fx_buffer=Decimal("1")
    )
    assert not result.ok and result.failure == "unsupported" and result.usage is None
    assert transport.calls == [] and gateway.attempts == []
    assert runtime.database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    assert runtime.budget.remaining("fixture") == 10
    runtime.database.close()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a17_valid_tool_schema_continues_to_valid_structured_output(tmp_path, provider):
    runtime = _stack(tmp_path)
    transport = ScriptedProviderHttp(
        [
            _response(provider, arguments={"symbol": "TEST"}, tool=True, request_id="tool-fixture"),
            _response(provider),
        ]
    )
    calls = []
    gateway = ModelGateway(
        runtime.budget,
        paid_calls_enabled=True,
        transport=transport,
        tools={"lookup": lambda args: calls.append(args) or {"price": "40"}},
    )
    result = gateway.invoke(
        _request(provider),
        deployment_id="fixture",
        price_card_id=provider,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert result.ok and result.payload == {"action": "hold"}
    assert calls == [{"symbol": "TEST"}] and len(transport.calls) == 2
    rows = runtime.database.execute("SELECT status, synthetic FROM usage_receipts").fetchall()
    assert [(row["status"], row["synthetic"]) for row in rows] == [("committed", 1), ("committed", 1)]
    assert Decimal(financial.costs(runtime)["actual_spend"]) == 0
    assert Decimal(financial.costs(runtime)["synthetic_spend"]) == Decimal("0.0000234")
    assert runtime.budget.remaining("fixture") == 10
    runtime.database.close()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("mode", ["batch", "continuation"])
def test_a06_tool_call_bound_blocks_excess_batch_and_cumulative_continuation(tmp_path, provider, mode):
    runtime = _stack(tmp_path)
    batch = _response(provider, arguments={"symbol": "TEST"}, tool=True)
    key = "output" if provider == "openai" else "content"
    identity = "call_id" if provider == "openai" else "id"
    batch[key].append({**batch[key][0], identity: "second-call-fixture"})
    payloads = (
        [batch]
        if mode == "batch"
        else [
            _response(
                provider,
                arguments={"symbol": "FIRST"},
                tool=True,
                call_id="first-call-fixture",
                request_id="first-response-fixture",
            ),
            batch,
        ]
    )
    transport = ScriptedProviderHttp(payloads)
    calls = []
    gateway = ModelGateway(
        runtime.budget,
        paid_calls_enabled=True,
        transport=transport,
        tools={"lookup": lambda args: calls.append(args) or {}},
    )
    result = gateway.invoke(
        _request(provider, max_tool_calls=1 if mode == "batch" else 2),
        deployment_id="fixture",
        price_card_id=provider,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert not result.ok and result.failure == "validation"
    assert result.usage.uncached_input_tokens == 7 and result.usage.billed_output_tokens == 3
    assert calls == ([] if mode == "batch" else [{"symbol": "FIRST"}])
    assert len(transport.calls) == len(payloads)
    assert runtime.database.execute("SELECT COUNT(*) FROM usage_receipts WHERE synthetic = 1").fetchone()[0] == len(
        payloads
    )
    assert runtime.budget.remaining("fixture") == 10
    runtime.database.close()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a17_malformed_local_schema_retains_supplied_usage(tmp_path, provider):
    runtime = _stack(tmp_path)
    gateway = ModelGateway(
        runtime.budget,
        paid_calls_enabled=True,
        transport=ScriptedProviderHttp([_response(provider)]),
    )
    result = gateway.invoke(
        _request(provider, output_schema={"type": "object", "properties": []}),
        deployment_id="fixture",
        price_card_id=provider,
        fx_rate=Decimal("0.9"),
        fx_buffer=Decimal("1"),
    )
    assert not result.ok and result.failure == "validation"
    assert result.usage.uncached_input_tokens == 7 and result.usage.billed_output_tokens == 3
    assert runtime.database.execute("SELECT synthetic FROM usage_receipts").fetchone()[0] == 1
    assert runtime.database.execute("SELECT state FROM budget_reservations").fetchone()[0] == "COMMITTED"
    runtime.database.close()
