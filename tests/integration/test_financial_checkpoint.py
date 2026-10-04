"""Actual financial history, independent witness continuity, and refusal cases."""

import asyncio
import json
import os
import sqlite3
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_protected_financial_runtime import ENTER, HOLD, KEY, activate, request, stack

from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel import financial_checkpoint


def prepared(tmp_path):
    result = stack(tmp_path)
    activate(result[-1], HOLD)
    return result


def test_complete_stream_beyond_old_row_and_byte_limits_and_unchanged_receipt_reuse(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    now = ledger.now()
    with db.immediate() as connection:
        connection.executemany("""INSERT INTO valuation_marks
            (mark_id,portfolio_id,asset,quote_currency,mark,convention,observed_at,stale,source)
            VALUES (?,?,'BTC','USD','100','mid',?,0,?)""",
            ((f"retained-{i:05d}", portfolio, now, "synthetic-full-history-" + "x" * 1000) for i in range(12_001)))
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    receipt = json.loads(db.execute("SELECT payload_json FROM protected_financial_checkpoints "
        "ORDER BY generation DESC LIMIT 1").fetchone()[0])
    assert receipt["tables"]["marks"]["rows"] == 12_001
    assert receipt["tables"]["marks"]["bytes"] > 8_388_608
    assert context["snapshot"]["cash"] == "10000" and "tables" not in context
    before = db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0]
    again = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert again["snapshot_id"] == context["snapshot_id"]
    assert db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0] == before
    assert runtime.financial.dispatch(request(context, action="hold"))["status"] == "APPLIED"


def test_stale_dispatch_does_not_publish_uncommitted_highwater(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    witness = runtime.financial.history.path.read_bytes()
    count = db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0]
    ledger.deposit(portfolio, "USD", Decimal("1"), "new-owner-flow")
    with pytest.raises(StaleState):
        runtime.financial.dispatch(request(context, action="hold"))
    assert runtime.financial.history.path.read_bytes() == witness
    assert db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0] == count
    assert runtime.financial.issue("protected-test", portfolio, "BTC/USD")["snapshot"]["cash"] == "10001"


@pytest.mark.parametrize("attack", ["ledger", "balanced_journal", "journal_delete", "source_missing",
                                  "unknown_kind", "transaction_unbalanced", "orphan_posting"])
def test_committed_native_prefix_and_new_source_corruption_refuse_authority(tmp_path, attack):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    if attack == "ledger":
        db.execute("UPDATE ledger_events SET payload_json='{}'")
    elif attack == "balanced_journal":
        db.execute("UPDATE journal_postings SET amount=CASE WHEN amount='10000' THEN '9999' ELSE '-9999' END")
    elif attack == "journal_delete":
        db.execute("DELETE FROM journal_postings")
    elif attack == "source_missing":
        db.execute("DELETE FROM ledger_events")
    elif attack == "unknown_kind":
        db.execute("""INSERT INTO ledger_events VALUES ('unreviewed',?,2,'unknown_financial_hook','{}',?,'new')""",
                   (portfolio, ledger.now()))
    else:
        ledger.deposit(portfolio, "USD", Decimal("2"), "new-flow")
        if attack == "transaction_unbalanced":
            db.execute("UPDATE journal_postings SET amount='3' WHERE amount='2'")
        else:
            db.execute("INSERT INTO journal_postings VALUES ('orphan','missing',?,'cash','USD','3')", (portfolio,))
    with pytest.raises(StaleState):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert db.execute("SELECT count(*) FROM order_intents").fetchone()[0] == 0


@pytest.mark.parametrize("attack", ["missing", "authentication", "symlink", "hardlink", "fifo", "public"])
def test_private_independent_witness_attacks_and_restart_never_reseal(tmp_path, attack):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    path = runtime.financial.history.path
    saved = path.read_bytes()
    if attack == "missing":
        path.unlink()
    elif attack == "authentication":
        value = json.loads(saved)
        value["authentication"] = "0" * 64
        path.write_text(json.dumps(value))
    elif attack == "public":
        path.chmod(0o644)
    else:
        path.unlink()
        target = tmp_path / "replacement.json"
        target.write_bytes(saved)
        target.chmod(0o600)
        if attack == "symlink":
            path.symlink_to(target)
        elif attack == "hardlink":
            os.link(target, path)
        else:
            os.mkfifo(path, 0o600)
    restart = ProtectedPaperRuntime(database=db, clock=clock, execution=execution,
        manifest=runtime.financial.manifest, capability_key=KEY, instance_id="protected-test")
    assert restart.financial.history.ready(portfolio) is False
    result = asyncio.run(restart.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "MANAGE_ONLY" and result["dispatched"] == 0
    with pytest.raises((StaleState, OSError)):
        restart.financial.issue("protected-test", portfolio, "BTC/USD")
    assert db.execute("SELECT count(*) FROM order_intents").fetchone()[0] == 0


def test_restored_financial_database_cannot_restore_independent_highwater(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    backup = sqlite3.connect(tmp_path / "old.sqlite")
    db.connection.backup(backup)
    ledger.deposit(portfolio, "USD", Decimal("10"), "later-flow")
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    witness = runtime.financial.history.path.read_bytes()
    backup.backup(db.connection)
    backup.close()
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("10000")
    assert runtime.financial.history.path.read_bytes() == witness
    assert runtime.financial.history.ready(portfolio) is False
    with pytest.raises(StaleState, match="witness"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")


def test_interrupted_checkpoint_witness_publication_does_not_grant_or_reset(tmp_path, monkeypatch):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    old = runtime.financial.history.path.read_bytes()
    original = runtime.financial.history._write_witness
    def failed(*args):
        raise OSError("synthetic filesystem failure")
    monkeypatch.setattr(runtime.financial.history, "_write_witness", failed)
    with pytest.raises(OSError):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    monkeypatch.setattr(runtime.financial.history, "_write_witness", original)
    assert runtime.financial.history.path.read_bytes() == old
    assert runtime.financial.history.ready(portfolio) is False
    with pytest.raises(StaleState, match="witness"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert db.execute("SELECT count(*) FROM order_intents").fetchone()[0] == 0


@pytest.mark.parametrize("bound", ["row", "total_rows", "total_bytes", "deadline", "cancelled"])
def test_complete_scan_bounds_refuse_before_capability_or_effect(tmp_path, monkeypatch, bound):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    count = db.execute("SELECT count(*) FROM protected_rpc_requests").fetchone()[0]
    if bound == "row":
        ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="x" * 65537)
    elif bound == "total_rows":
        monkeypatch.setattr(financial_checkpoint, "MAXIMUM_ROWS", 1)
    elif bound == "total_bytes":
        monkeypatch.setattr(financial_checkpoint, "MAXIMUM_TOTAL_BYTES", 1)
    elif bound == "deadline":
        monkeypatch.setattr(financial_checkpoint, "MAXIMUM_SECONDS", 0)
    else:
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(StaleState):
            runtime.financial.history.scan(portfolio, {"snapshot_at": ledger.now()}, previous=None, cancelled=cancelled)
        return
    with pytest.raises(StaleState):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert db.execute("SELECT count(*) FROM protected_rpc_requests").fetchone()[0] == count
    assert db.execute("SELECT count(*) FROM order_intents").fetchone()[0] == 0


def test_initial_owner_admission_refuses_known_inconsistent_native_journal(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    db.execute("UPDATE journal_postings SET amount=CASE WHEN amount='10000' THEN '9999' ELSE '-9999' END")
    with pytest.raises(StaleState, match="journal"):
        activate(runtime, HOLD)
    assert runtime.controller.status()["status"] == "MANAGE_ONLY"
    assert db.execute("SELECT count(*) FROM protected_financial_checkpoints").fetchone()[0] == 0


def test_actual_fill_and_release_rollback_never_restore_financial_checkpoint(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    effect = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    quote = execution.latest_observation("BTC/USD", execution.now())
    execution.on_observation(quote.model_copy(update={"observation_id": "actual-fill"}))
    current = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    generation = db.execute("SELECT max(generation) FROM protected_financial_checkpoints").fetchone()[0]
    activate(runtime, HOLD, "next")
    following = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert current["snapshot"] == following["snapshot"]
    assert db.execute("SELECT max(generation) FROM protected_financial_checkpoints").fetchone()[0] == generation
    assert effect["response"]["intent_id"] and following["snapshot"]["position_quantity"] == "0.01"


def test_new_balanced_native_postings_cannot_disagree_with_new_deposit_source(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    ledger.deposit(portfolio, "USD", Decimal("2"), "new-correct-flow")
    db.execute("UPDATE journal_postings SET amount=CASE WHEN amount='2' THEN '3' ELSE '-3' END "
               "WHERE amount IN ('2','-2')")
    assert ledger.journal_balanced(portfolio) and ledger.books(portfolio).cash_amount("USD") == 10002
    with pytest.raises(StaleState, match="exact financial source postings"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")


def test_new_manifest_cannot_rebootstrap_restored_database_financial_highwater(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    backup = sqlite3.connect(tmp_path / "old-manifest.sqlite")
    db.connection.backup(backup)
    ledger.deposit(portfolio, "USD", Decimal("10"), "later-authenticated-flow")
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    backup.backup(db.connection)
    backup.close()
    manifest = replace(runtime.financial.manifest, deployment_id="new-code-scope")
    newer = ProtectedPaperRuntime(database=db, clock=clock, execution=execution, manifest=manifest,
        capability_key=KEY, instance_id="new-owner-source")
    with pytest.raises(StaleState, match="prior financial"):
        newer.controller.admit_release(release_id="new-owner-financial-source", source_text=HOLD)
    assert newer.controller.status()["status"] == "MANAGE_ONLY"


def test_owner_source_change_requires_explicit_financial_continuity_transition(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    newer = ProtectedPaperRuntime(database=db, clock=clock, execution=execution,
        manifest=replace(runtime.financial.manifest, capability_ttl_seconds=14),
        capability_key=KEY, instance_id="different-owner-source")
    with pytest.raises(StaleState, match="continuity transition"):
        newer.controller.admit_release(release_id="different-owner-financial-source", source_text=HOLD)
    assert newer.controller.status()["status"] == "MANAGE_ONLY"


def test_future_balanced_matching_source_is_refused_after_initial_preparation(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    ledger.deposit(portfolio, "USD", Decimal("2"), "future-matching-source")
    future = utc_iso(clock.now() + timedelta(seconds=60))
    db.execute("UPDATE ledger_events SET effective_at=? WHERE external_ref='future-matching-source'", (future,))
    db.execute("UPDATE journal_transactions SET created_at=? WHERE external_ref='future-matching-source'", (future,))
    with pytest.raises(StaleState, match="future"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")


def test_late_native_correction_continuity_and_following_source_match_pinned_ledger(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    start = clock.now()
    clock.advance(4)
    def fill(name, seconds, price, side="buy"):
        return FillRecord(venue="paper", account_id="synthetic-only", trade_id=name, intent_id=name,
            symbol="BTC/USD", side=side, quantity="1", price=price, quote_cost=price,
            fee_amount="0", fee_asset="USD", liquidity="taker", filled_at_utc=start + timedelta(seconds=seconds))
    ledger.apply_fill(portfolio, fill("known-buy", 2, "200"), base_asset="BTC", quote_asset="USD")
    ledger.apply_fill(portfolio, fill("known-sale", 3, "300", "sell"), base_asset="BTC", quote_asset="USD")
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    clock.advance(1)
    ledger.apply_late_fill(portfolio, fill("late-buy", 1, "100"), base_asset="BTC", quote_asset="USD")
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    assert Decimal(context["snapshot"]["cash"]) == ledger.books(portfolio).cash_amount("USD") == 10000
    assert context["snapshot"]["position_quantity"] == "1"
    clock.advance(1)
    ledger.apply_fill(portfolio, fill("following-sale", 6, "400", "sell"), base_asset="BTC", quote_asset="USD")
    assert runtime.financial.issue("protected-test", portfolio, "BTC/USD")["snapshot"]["cash"] == "10400"


@pytest.mark.parametrize("attack", ["previous-projection", "current-projection", "balanced-invented-cash"])
def test_new_correction_requires_independent_pinned_projection_and_delta_validation(tmp_path, attack):
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(tmp_path)
    start = clock.now()
    clock.advance(4)
    def fill(name, seconds, price, side="buy"):
        return FillRecord(venue="paper", account_id="synthetic-only", trade_id=name, intent_id=name,
            symbol="BTC/USD", side=side, quantity="1", price=price, quote_cost=price,
            fee_amount="0", fee_asset="USD", liquidity="taker", filled_at_utc=start + timedelta(seconds=seconds))
    ledger.apply_fill(portfolio, fill("known-buy", 2, "200"), base_asset="BTC", quote_asset="USD")
    ledger.apply_fill(portfolio, fill("known-sale", 3, "300", "sell"), base_asset="BTC", quote_asset="USD")
    runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    clock.advance(1)
    ledger.apply_late_fill(portfolio, fill("late-buy", 1, "100"), base_asset="BTC", quote_asset="USD")
    correction = db.execute("SELECT * FROM ledger_events WHERE kind='fill_chronological_replay'").fetchone()
    payload = json.loads(correction["payload_json"])
    if attack == "balanced-invented-cash":
        cash = next(row for row in payload["postings"] if row["account"] == "cash" and row["asset"] == "USD")
        counter = next(row for row in payload["postings"] if row["asset"] == "USD" and row is not cash)
        cash["amount"] = canonical_decimal(Decimal(cash["amount"]) + 1)
        counter["amount"] = canonical_decimal(Decimal(counter["amount"]) - 1)
        for row in (cash, counter):
            db.execute("""UPDATE journal_postings SET amount=? WHERE account=? AND asset=?
                AND transaction_id IN (SELECT transaction_id FROM journal_transactions WHERE external_ref=?)""",
                (row["amount"], row["account"], row["asset"], correction["external_ref"]))
        assert ledger.journal_balanced(portfolio)
    else:
        payload["previous_projection_sha256" if attack == "previous-projection"
                else "current_projection_sha256"] = "0" * 64
    db.execute("UPDATE ledger_events SET payload_json=? WHERE event_id=?",
               (json.dumps(payload, sort_keys=True, separators=(",", ":")), correction["event_id"]))
    with pytest.raises(StaleState, match="complete native Ledger verification refused"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")


def test_private_witness_ancestor_symlink_is_refused_through_secure_handles(tmp_path):
    private = tmp_path / "real-private"
    private.mkdir(mode=0o700)
    db, clock, ledger, execution, broker, portfolio, runtime = prepared(private)
    moved = tmp_path / "same-private-identity"
    private.rename(moved)
    private.symlink_to(moved, target_is_directory=True)
    with pytest.raises(StaleState, match="ancestor"):
        runtime.financial.issue("protected-test", portfolio, "BTC/USD")
