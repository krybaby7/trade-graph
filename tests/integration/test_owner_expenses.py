"""Owner bills remain immutable, explicitly allocated, and separate from API costs."""

import secrets
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from trade_graph.adapters.persistence.db import Database
from trade_graph.api.auth import csrf_for_token, issue_session, role_for_token
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock, utc_iso

START = "2026-01-01T00:00:00.000000Z"
MIDDLE = "2026-01-16T00:00:00.000000Z"
END = "2026-01-31T00:00:00.000000Z"


def _runtime(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    database = Database(tmp_path / "owner-expenses.sqlite")
    ledger = Ledger(database, clock)
    portfolio = ledger.create_portfolio(reporting_currency="EUR")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "opening")
    ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"), source="synthetic", kind="reference", stale=False)
    budget = BudgetGateway(database, clock)
    budget.configure(deployment_id="deployment", currency="EUR", total=Decimal("10"), period=Decimal("10"),
                     priority_reserve=Decimal("0"), daily=Decimal("10"), root=Decimal("10"), roles={})
    clock.advance(15 * 86400)
    ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.8"), source="synthetic later", kind="reference",
                      stale=False)
    clock.advance(15 * 86400)
    return SimpleNamespace(database=database, ledger=ledger, clock=clock, portfolio_id=portfolio,
                           deployment_id="deployment")


def _bill(**changes):
    return {"record_type": "expense", "request_id": "bill-request", "bill_id": "opaque-bill-1",
            "billing_scope": "codex-monthly", "expense_kind": "subscription", "amount_native": "20",
            "native_currency": "USD", "incurred_at": START, "period_start": START, "period_end": END,
            "graph_share": "0.5", "allocation_policy": "owner graph share",
            "department_weights": {"trader": "0.6", "engineer": "0.4"},
            "department_allocation_label": "owner weights", "evidence_ref": "owner-evidence:bill-1", **changes}


def _complete(**changes):
    return {"record_type": "completeness", "request_id": "complete-request", "period_start": START,
            "period_end": END, "expense_kinds": ["subscription", "other"],
            "evidence_ref": "owner-evidence:complete-1", **changes}


def _api(runtime):
    from trade_graph.api.owner_expenses import register

    app = FastAPI()

    def identity(request: Request):
        parts = request.headers.get("authorization", "").split()
        bearer = len(parts) == 2 and parts[0].lower() == "bearer"
        token = parts[1] if bearer else request.cookies.get("tg_session")
        role = role_for_token(runtime.database, token)
        if role is None:
            raise HTTPException(401, "authentication required")
        if request.method != "GET" and not bearer:
            if not secrets.compare_digest(csrf_for_token(runtime.database, token) or "",
                                          request.headers.get("x-csrf-token", "missing")):
                raise HTTPException(403, "csrf")
        return role

    def owner_write(request: Request):
        if identity(request) != "owner":
            raise HTTPException(403, "owner authority required")

    register(app, runtime, identity, owner_write)
    token, csrf = issue_session(runtime.database, runtime.clock, "owner")
    return TestClient(app), {"Authorization": f"Bearer {token}"}, token, csrf


def _record(runtime, body):
    from trade_graph.api.owner_expenses import record
    return record(runtime, body)


def _projection(runtime, start=START, end=END):
    from trade_graph.api.owner_expenses import projection
    return projection(runtime, start, end, "EUR")


def test_no_owner_evidence_is_unknown_even_without_calls(tmp_path):
    runtime = _runtime(tmp_path)
    result = _projection(runtime)
    assert result["full_bills"] == []
    assert result["graph_subscription_allocation"]["amount"] is None
    assert result["graph_subscription_allocation"]["known_amount"] == "0"
    assert result["graph_other_allocation"]["amount"] is None
    assert result["coverage"]["status"] == "unknown"
    assert result["provisional"] is True


def test_full_bill_graph_proration_departments_and_historical_fx(tmp_path):
    runtime = _runtime(tmp_path)
    before = BudgetGateway(runtime.database, runtime.clock).remaining("deployment")
    _record(runtime, _bill())
    _record(runtime, _complete())
    result = _projection(runtime, MIDDLE)
    full = result["full_bills"][0]
    assert full["amount_native"] == "20"
    assert full["full_bill_valuation"]["amount"] == "18"
    assert full["graph_allocation_native"] == "5"
    assert full["period_fraction"] == "0.5"
    assert full["graph_allocation_valuation"]["amount"] == "4.5"
    assert full["graph_allocation_valuation"]["fx"]["rate"] == "0.9"
    assert result["graph_subscription_allocation"]["amount"] == "4.5"
    assert result["graph_other_allocation"]["amount"] == "0"
    departments = {row["department"]: row for row in result["department_allocations"]}
    assert departments["trader"]["amount"] == "2.7"
    assert departments["engineer"]["amount"] == "1.8"
    assert departments["trader"]["allocation_labels"] == ["owner weights"]
    assert result["coverage"]["status"] == "complete"
    assert result["provisional"] is False
    assert BudgetGateway(runtime.database, runtime.clock).remaining("deployment") == before
    assert runtime.database.execute("SELECT COUNT(*) FROM ledger_events WHERE kind='expense'").fetchone()[0] == 0


def test_exact_replay_and_duplicate_overlap_are_rejected(tmp_path):
    runtime = _runtime(tmp_path)
    first = _record(runtime, _bill())
    assert _record(runtime, _bill()) == first
    from trade_graph.api.owner_expenses import EvidenceConflict
    for changes in ({"amount_native": "21"}, {"request_id": "retry"},
                    {"request_id": "overlap", "bill_id": "different-bill", "evidence_ref": "owner-evidence:bill-2"},
                    {"request_id": "other-scope", "bill_id": "different-bill", "billing_scope": "other",
                     "evidence_ref": "owner-evidence:bill-1"}):
        with pytest.raises(EvidenceConflict):
            _record(runtime, _bill(**changes))
    assert runtime.database.execute("SELECT COUNT(*) FROM owner_expense_evidence").fetchone()[0] == 1
    _record(runtime, _bill(request_id="next-month", bill_id="next", evidence_ref="owner-evidence:next",
                          period_start=END, period_end="2026-03-02T00:00:00Z"))


def test_sql_update_delete_and_direct_overlapping_insert_refused(tmp_path):
    runtime = _runtime(tmp_path)
    _record(runtime, _bill())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        runtime.database.execute("UPDATE owner_expense_evidence SET document_json='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        runtime.database.execute("DELETE FROM owner_expense_evidence")
    with pytest.raises(sqlite3.IntegrityError, match="overlapping"):
        runtime.database.execute("""INSERT INTO owner_expense_evidence
          (record_id,deployment_id,request_id,record_type,bill_id,billing_scope,expense_kind,
           period_start,period_end,incurred_at,document_json,created_at)
          SELECT 'injected','deployment','injected','expense','injected',billing_scope,expense_kind,
                 period_start,period_end,incurred_at,document_json,created_at FROM owner_expense_evidence""")


@pytest.mark.parametrize("changes", [
    {"amount_native": 0.1}, {"graph_share": "1.1"}, {"graph_share": "-0.1"},
    {"amount_native": "NaN"}, {"department_weights": {"trader": "0.9"}},
    {"department_weights": {"trader": 1.0}}, {"period_end": START},
    {"incurred_at": "2026-02-01T00:00:00Z"}, {"incurred_at": "2026-01-01"},
    {"evidence_ref": "C:/private/invoice.pdf"}, {"allocation_policy": "api_key=not-retainable"},
    {"raw_invoice": "private"}, {"native_currency": "usd"},
])
def test_invalid_private_or_inexact_evidence_rejected(tmp_path, changes):
    runtime = _runtime(tmp_path)
    client, headers, _, _ = _api(runtime)
    response = client.post("/api/v1/owner/expenses", headers=headers, json=_bill(**changes))
    assert response.status_code == 422
    assert "not-retainable" not in response.text
    assert runtime.database.execute("SELECT COUNT(*) FROM owner_expense_evidence").fetchone()[0] == 0


def test_completeness_is_period_scoped_and_later_evidence_needs_new_declaration(tmp_path):
    runtime = _runtime(tmp_path)
    _record(runtime, _complete(period_end=MIDDLE))
    assert _projection(runtime)["coverage"]["status"] == "unknown"
    _record(runtime, _complete(request_id="second-half", period_start=MIDDLE,
                               evidence_ref="owner-evidence:second-half"))
    assert _projection(runtime)["graph_subscription_allocation"]["amount"] == "0"
    _record(runtime, _bill())
    assert _projection(runtime)["graph_subscription_allocation"]["amount"] is None
    assert _projection(runtime)["graph_subscription_allocation"]["known_amount"] == "9"
    assert _projection(runtime)["graph_other_allocation"]["amount"] == "0"
    _record(runtime, _complete(request_id="refreshed", evidence_ref="owner-evidence:refreshed"))
    assert _projection(runtime)["graph_subscription_allocation"]["amount"] == "9"
    with pytest.raises(ValueError):
        _record(runtime, _complete(request_id="future", period_end="2026-02-01T00:00:00Z"))


def test_missing_and_stale_fx_never_claim_resolved_economics(tmp_path):
    runtime = _runtime(tmp_path)
    _record(runtime, _bill(native_currency="GBP"))
    _record(runtime, _complete())
    result = _projection(runtime)
    assert result["coverage"]["status"] == "complete"
    assert result["graph_subscription_allocation"]["amount"] is None
    assert result["graph_subscription_allocation"]["known_amount"] is None
    assert result["provisional"] is True
    assert "missing_fx" in result["coverage"]["unknown_reasons"]
    runtime.database.execute("UPDATE fx_rates SET stale=1")
    result = _projection(runtime)
    assert result["full_bills"][0]["graph_allocation_valuation"]["provisional"] is True


def test_existing_ledger_or_api_evidence_is_not_charged_twice(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.ledger.add_expense(runtime.portfolio_id, expense_id="existing-host", native_amount=Decimal("20"),
                              native_currency="USD", reporting_amount=Decimal("18"), reporting_currency="EUR",
                              embedded=False, source="owner-evidence:existing-host")
    from trade_graph.api.owner_expenses import EvidenceConflict
    with pytest.raises(EvidenceConflict):
        _record(runtime, _bill(bill_id="existing-host", expense_kind="other"))
    with pytest.raises(EvidenceConflict):
        _record(runtime, _bill(evidence_ref="owner-evidence:existing-host", expense_kind="other"))
    runtime.database.execute("""INSERT INTO budget_reservations
        (reservation_id,deployment_id,role,amount,currency,state,price_card_id,purpose,synthetic,created_at,updated_at)
        VALUES ('r','deployment','trader','1','EUR','COMMITTED','card','fixture',0,?,?)""", (START, START))
    runtime.database.execute("""INSERT INTO usage_receipts
        (receipt_id,reservation_id,provider,provider_request_id,model,native_cost,native_currency,
         status,usage_json,synthetic,created_at) VALUES ('api-receipt','r','scripted','provider-bill','m','1','USD',
         'actual','{}',0,?)""", (START,))
    with pytest.raises(EvidenceConflict):
        _record(runtime, _bill(bill_id="api-receipt"))
    with pytest.raises(EvidenceConflict):
        _record(runtime, _bill(evidence_ref="receipt:api-receipt"))


def test_authentication_owner_permission_cookie_csrf_and_reader_projection(tmp_path):
    runtime = _runtime(tmp_path)
    client, owner, token, csrf = _api(runtime)
    assert client.get("/api/v1/owner/expenses").status_code == 401
    reader_token, _ = issue_session(runtime.database, runtime.clock, "reader")
    reader = {"Authorization": f"Bearer {reader_token}"}
    assert client.get("/api/v1/owner/expenses", headers=reader).status_code == 200
    assert client.post("/api/v1/owner/expenses", headers=reader, json=_bill()).status_code == 403
    client.cookies.set("tg_session", token)
    assert client.post("/api/v1/owner/expenses", json=_bill()).status_code == 403
    response = client.post("/api/v1/owner/expenses", headers={"X-CSRF-Token": csrf}, json=_bill())
    assert response.status_code == 200
    assert response.json()["bill_id"] == "opaque-bill-1"
    assert client.post("/api/v1/owner/expenses", headers=owner, json=_bill(amount_native="21")).status_code == 409
    result = client.get("/api/v1/owner/expenses", headers=reader).json()
    assert result["period"] == {"start_at": START, "end_at": utc_iso(runtime.clock.now())}
    assert result["full_bills"][0]["bill_id"] == "opaque-bill-1"
    assert client.get("/api/v1/owner/expenses?start_at=invalid", headers=reader).status_code == 422


def test_new_account_zero_elapsed_period_remains_unknown_without_evidence(tmp_path):
    runtime = _runtime(tmp_path)
    result = _projection(runtime, END, END)
    assert result["full_bills"] == []
    assert result["coverage"]["status"] == "unknown"
    assert result["graph_subscription_allocation"]["amount"] is None
    assert result["graph_subscription_allocation"]["known_amount"] == "0"


def test_stale_bill_fx_retains_known_amount_but_is_provisional(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.database.execute("UPDATE fx_rates SET stale=1 WHERE observed_at=?", (START,))
    _record(runtime, _bill())
    _record(runtime, _complete())
    result = _projection(runtime)
    assert result["graph_subscription_allocation"]["amount"] is None
    assert result["graph_subscription_allocation"]["known_amount"] == "9"
    assert "stale_fx" in result["coverage"]["unknown_reasons"]
    assert result["full_bills"][0]["graph_allocation_valuation"]["provisional"] is True


def test_later_ledger_mirror_is_excluded_and_preserves_uncertainty(tmp_path):
    runtime = _runtime(tmp_path)
    _record(runtime, _bill())
    _record(runtime, _complete())
    runtime.ledger.add_expense(runtime.portfolio_id, expense_id="opaque-bill-1", native_amount=Decimal("10"),
                              native_currency="USD", reporting_amount=Decimal("9"), reporting_currency="EUR",
                              embedded=False, source="owner-evidence:bill-1")
    result = _projection(runtime)
    assert result["graph_subscription_allocation"]["known_amount"] == "0"
    assert result["graph_subscription_allocation"]["amount"] is None
    assert result["full_bills"][0]["excluded_existing_accounting"] is True
    assert "duplicate_existing_accounting" in result["coverage"]["unknown_reasons"]


def test_other_bill_department_rounding_reconciles_to_graph_total(tmp_path):
    runtime = _runtime(tmp_path)
    _record(runtime, _bill(expense_kind="other", native_currency="EUR", amount_native="1",
                          graph_share="1", department_weights={"trader": "0.3333333333333333333333333333",
                              "engineer": "0.6666666666666666666666666667"}))
    _record(runtime, _complete())
    end = "2026-01-11T00:00:00.000000Z"
    result = _projection(runtime, START, end)
    with __import__("decimal").localcontext() as context:
        context.prec = 128
        total = sum((Decimal(row["amount"]) for row in result["department_allocations"]), Decimal("0"))
    assert str(total) == result["graph_other_allocation"]["amount"]
    assert result["graph_subscription_allocation"]["amount"] == "0"


def test_migration_adds_evidence_without_rewriting_existing_rows(tmp_path):
    from trade_graph.adapters.persistence.migrate import STATEMENTS, apply_migrations
    path = tmp_path / "migration.sqlite"
    connection = sqlite3.connect(path)
    for version, statements in STATEMENTS:
        if version == "0024":
            break
        for statement in statements:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, START))
    connection.execute("INSERT INTO portfolios VALUES ('retained','paper','EUR','experiment','active',NULL,?)",
                       (START,))
    connection.commit()
    assert apply_migrations(connection, END) == ["0024"]
    assert connection.execute("SELECT portfolio_id,created_at FROM portfolios").fetchone() == ("retained", START)
    assert apply_migrations(connection, END) == []
    assert connection.execute("SELECT COUNT(*) FROM owner_expense_evidence").fetchone()[0] == 0
    connection.close()
