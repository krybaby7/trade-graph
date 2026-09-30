"""Crash boundaries exercise the real gateway/reservations, not fabricated receipts."""

from decimal import Decimal

import pytest
from tests.integration.test_engineer import _stack
from tests.leadership_support import stack

from trade_graph.application.gateway import ModelGateway
from trade_graph.contracts.models import ModelRequest, ModelResult, ModelUsage
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure


@pytest.fixture
def durable(tmp_path):
    clock, db, pid, _, engineer, _ = _stack(tmp_path)
    office, _, gateway, _ = stack(db, clock, engineer.ledger, pid)
    task_id = office.scheduler.add_task(role="engineer", objective="bounded generation", portfolio_id=pid,
                                        allocated_spend=Decimal("1"))
    request = ModelRequest(role="engineer", task_id=task_id, root_task_id=task_id, run_id="run",
                           system_version_id="baseline", provider="scripted", model="scripted",
                           instructions="Return a bounded patch", context={}, output_schema={"type": "object"},
                           schema_name="patch", max_output_tokens=500, max_tool_calls=0, timeout_seconds=5)
    gateway.scripted.outputs["engineer"] = {"files": [{"path": "artifacts/context_policy.json", "content": "{}"}]}
    kwargs = dict(deployment_id="deployment", price_card_id="scripted-review", fx_rate=Decimal("1"),
                  fx_buffer=Decimal("1.02"), invocation_id="attempt-1", portfolio_id=pid)
    return db, gateway, request, kwargs


def test_completed_response_and_receipt_recover_without_calling_model(durable, monkeypatch):
    db, gateway, request, kwargs = durable
    first = gateway.invoke(request, **kwargs)
    assert first.ok
    restarted = ModelGateway(gateway.budget, paid_calls_enabled=False)
    monkeypatch.setattr(restarted.scripted, "complete", lambda _: pytest.fail("duplicate external invocation"))
    assert restarted.invoke(request, **kwargs) == first
    assert db.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    row = db.execute("SELECT * FROM model_invocations").fetchone()
    assert (row["portfolio_id"], row["task_id"], row["run_id"], row["system_version_id"]) == (
        kwargs["portfolio_id"], request.task_id, "run", "baseline")
    assert db.execute("SELECT COUNT(*) FROM cost_allocations WHERE portfolio_id = ?",
                      (kwargs["portfolio_id"],)).fetchone()[0] == 1
    assert db.execute("SELECT synthetic FROM usage_receipts").fetchone()[0] == 1


def test_crash_after_dispatch_preserves_hold_and_never_replays(durable, monkeypatch):
    db, gateway, request, kwargs = durable

    def crash(_):
        raise KeyboardInterrupt("process died after dispatch intent")

    monkeypatch.setattr(gateway.scripted, "complete", crash)
    with pytest.raises(KeyboardInterrupt):
        gateway.invoke(request, **kwargs)
    assert db.execute("SELECT state FROM model_invocations").fetchone()[0] == "DISPATCHED"
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    monkeypatch.setattr(gateway.scripted, "complete", lambda _: pytest.fail("uncertain external call replayed"))
    recovered = gateway.invoke(request, **kwargs)
    assert recovered.failure == "timeout_uncertain"
    assert db.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert gateway.invoke(request, **kwargs) == recovered
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1
    reservation = db.execute("SELECT reservation_id FROM budget_reservations").fetchone()[0]
    receipt = gateway.budget.conservative_charge(reservation)
    assert gateway.budget.conservative_charge(reservation) == receipt
    assert db.execute("SELECT status FROM usage_receipts").fetchone()[0] == "conservative_charge"
    assert gateway.invoke(request, **kwargs).failure == "timeout_uncertain"  # Explicit reconciliation, not free retry.


@pytest.mark.parametrize("mutation", ["portfolio", "root", "version", "role", "instructions", "billing"])
def test_attempt_key_is_immutable_and_scope_bound(durable, mutation):
    db, gateway, request, kwargs = durable
    gateway.invoke(request, **kwargs)
    if mutation == "portfolio":
        kwargs["portfolio_id"] = "foreign"
    elif mutation == "billing":
        kwargs["fx_buffer"] = Decimal("1.50")
    else:
        field = {"root": "root_task_id", "version": "system_version_id"}.get(mutation, mutation)
        request = request.model_copy(update={field: "different"})
    with pytest.raises(StaleState):
        gateway.invoke(request, **kwargs)
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 1


def test_foreign_new_invocation_rolls_back_reservation(durable):
    db, gateway, request, kwargs = durable
    kwargs["portfolio_id"] = "foreign"
    with pytest.raises(AuthorityDenied):
        gateway.invoke(request, **kwargs)
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_failed_model_usage_is_retained_and_recovered(durable, monkeypatch):
    db, gateway, request, kwargs = durable
    failure = ModelResult(ok=False, failure="refusal", message="refused", usage=ModelUsage(
        uncached_input_tokens=20, billed_output_tokens=4))
    monkeypatch.setattr(gateway.scripted, "complete", lambda _: failure)
    assert gateway.invoke(request, **kwargs) == failure
    assert gateway.invoke(request, **kwargs) == failure
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1
    assert Decimal(db.execute("SELECT amount FROM budget_reservations").fetchone()[0]) > 0


def test_revocation_after_dispatch_cannot_erase_bill(durable, monkeypatch):
    db, gateway, request, kwargs = durable
    allowed = True
    original = gateway.scripted.complete

    def authorize():
        if not allowed:
            raise AuthorityDenied("revoked")

    def revoke_after_response(req):
        nonlocal allowed
        result = original(req)
        allowed = False
        return result

    monkeypatch.setattr(gateway.scripted, "complete", revoke_after_response)
    assert gateway.invoke(request, authorize=authorize, **kwargs).ok
    with pytest.raises(AuthorityDenied):
        gateway.invoke(request, authorize=authorize, **kwargs)
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 1


def test_timeout_is_durable_uncertainty(durable, monkeypatch):
    db, gateway, request, kwargs = durable

    def timeout(_):
        raise TimeoutError("provider did not confirm billing")

    monkeypatch.setattr(gateway.scripted, "complete", timeout)
    assert gateway.invoke(request, **kwargs).failure == "timeout_uncertain"
    assert db.execute("SELECT state FROM budget_reservations").fetchone()[0] == "UNCERTAIN"
    assert len(gateway.attempts) == 1
    gateway.invoke(request, **kwargs)
    assert len(gateway.attempts) == 1


def test_durable_interface_refuses_tool_expansion(durable):
    db, gateway, request, kwargs = durable
    with pytest.raises(ValidationFailure):
        gateway.invoke(request.model_copy(update={"max_tool_calls": 1}), **kwargs)
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
