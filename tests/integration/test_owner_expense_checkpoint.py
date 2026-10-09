"""Owner bill evidence stays in authenticated deployment history across accounts."""

import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_protected_financial_runtime import HOLD, activate, request, stack

from trade_graph.api.owner_expenses import EvidenceConflict, projection, record
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState


def _owner(runtime, ledger, portfolio):
    return SimpleNamespace(database=runtime.financial.database, clock=runtime.financial.clock, ledger=ledger,
                           portfolio_id=portfolio, deployment_id=runtime.financial.manifest.deployment_id)


def _bill(owner, **changes):
    now = utc_iso(owner.clock.now())
    return {"record_type": "expense", "request_id": "checkpoint-bill", "bill_id": "synthetic-bill",
            "billing_scope": "synthetic-subscription", "expense_kind": "subscription", "amount_native": "20",
            "native_currency": "USD", "incurred_at": now, "period_start": now,
            "period_end": "2026-11-01T00:00:00.000000Z", "graph_share": "0.5",
            "allocation_policy": "synthetic owner graph share", "department_weights": {"trader": "1"},
            "department_allocation_label": "synthetic owner allocation", "evidence_ref": "owner-evidence:synthetic",
            **changes}


def _checkpoint(database):
    return json.loads(database.execute("SELECT payload_json FROM protected_financial_checkpoints "
                                      "ORDER BY generation DESC LIMIT 1").fetchone()[0])


def _prepared(tmp_path):
    result = stack(tmp_path)
    activate(result[-1], HOLD)
    return result


def test_owner_bill_append_is_checkpointed_and_invalidates_existing_capability(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = _prepared(tmp_path)
    owner = _owner(runtime, ledger, portfolio)
    before = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    old = _checkpoint(db)
    assert old["tables"]["owner_expense_evidence"]["rows"] == 0
    first = record(owner, _bill(owner))
    with pytest.raises(StaleState):
        runtime.financial.dispatch(request(before, action="hold"))
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    current = _checkpoint(db)
    assert current["tables"]["owner_expense_evidence"]["rows"] == 1
    assert current["tables"]["owner_expense_evidence"]["sha256"] != old["tables"]["owner_expense_evidence"]["sha256"]
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("10000")
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert record(owner, _bill(owner)) == first
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert _checkpoint(db) == current


@pytest.mark.parametrize("attack", ["update", "delete", "sequence", "document"])
def test_checkpoint_refuses_changed_owner_evidence_even_if_sql_triggers_are_removed(tmp_path, attack):
    db, clock, ledger, execution, broker, portfolio, runtime = _prepared(tmp_path)
    owner = _owner(runtime, ledger, portfolio)
    record(owner, _bill(owner))
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    db.execute("DROP TRIGGER owner_expense_evidence_no_update")
    db.execute("DROP TRIGGER owner_expense_evidence_no_delete")
    if attack == "delete":
        db.execute("DELETE FROM owner_expense_evidence")
    elif attack == "sequence":
        db.execute("UPDATE owner_expense_evidence SET sequence=99")
    elif attack == "document":
        db.execute("UPDATE owner_expense_evidence SET document_json='{}'")
    else:
        db.execute("UPDATE owner_expense_evidence SET evidence_ref='owner-evidence:rewritten'")
    with pytest.raises(StaleState):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0


def test_restoring_before_owner_bill_cannot_restore_financial_highwater(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = _prepared(tmp_path)
    owner = _owner(runtime, ledger, portfolio)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    backup = sqlite3.connect(tmp_path / "before-owner-bill.sqlite")
    db.connection.backup(backup)
    record(owner, _bill(owner))
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    retained_witness = runtime.financial.history.path.read_bytes()
    backup.backup(db.connection)
    backup.close()
    assert db.execute("SELECT COUNT(*) FROM owner_expense_evidence").fetchone()[0] == 0
    assert runtime.financial.history.path.read_bytes() == retained_witness
    with pytest.raises(StaleState, match="witness"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")


def test_owner_bill_identity_is_shared_across_portfolios_and_retained_after_new_account(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = _prepared(tmp_path)
    owner = _owner(runtime, ledger, portfolio)
    retained = record(owner, _bill(owner))
    clock.advance(1)
    second = ledger.create_portfolio(reporting_currency="USD", reset_of=portfolio)
    ledger.deposit(second, "USD", Decimal("10000"), "new-virtual-only")
    other_view = _owner(runtime, ledger, second)
    with pytest.raises(EvidenceConflict):
        record(other_view, _bill(owner, request_id="duplicate-in-new-account"))
    assert db.execute("SELECT COUNT(*) FROM owner_expense_evidence").fetchone()[0] == 1
    start = retained["period_start"]
    end = utc_iso(clock.now())
    original = projection(owner, start, end, "USD")
    following = projection(other_view, start, end, "USD")
    assert original == following  # Explicitly deployment graph allocation, independent of account selection.
    assert original["deployment_id"] == runtime.financial.manifest.deployment_id
    assert original["full_bills"][0]["bill_id"] == "synthetic-bill"
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert _checkpoint(db)["tables"]["owner_expense_evidence"]["rows"] == 1
    assert db.execute("SELECT COUNT(*) FROM usage_receipts").fetchone()[0] == 0
