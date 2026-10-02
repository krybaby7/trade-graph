"""Real durable paper financial effects behind actual-host process confinement."""

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import paper_owner_policy, seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.application.protected_runtime import ProtectedPaperRuntime
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.kernel.runtime_manifest import ProtectedRuntimeManifest, protected_package_sha256

HOLD = '''
def propose(context):
    return {"action":"hold", "rationale":"protected proposal", "invalidation":"new quote",
            "horizon_seconds":3600, "strategy_id":"slow-trend", "no_action_reason":"no signal"}
'''
ENTER = HOLD.replace('"action":"hold"', '"action":"enter", "quantity":"0.01"').replace(
    ', "no_action_reason":"no signal"', '')
BROKEN = 'def propose(context):\n    raise RuntimeError("broken candidate")\n'
KEY = b"synthetic-private-parent-hmac-key-41"


def stack(tmp_path, *, sources=(HOLD, ENTER, BROKEN), broker_type=PaperBroker):
    clock = FrozenClock(datetime(2026, 10, 2, tzinfo=UTC))
    db = Database(tmp_path / "protected.sqlite")
    ledger = Ledger(db, clock)
    broker = PaperBroker(db, clock)
    if broker_type is DropAckBroker:
        broker = DropAckBroker(broker)
    execution = Execution(db, ledger, clock, broker)
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "initial-virtual-only")
    seed_paper_authority(db, clock, portfolio)
    execution.register_instrument(InstrumentRules(venue="paper", symbol="BTC/USD", base_asset="BTC",
        quote_asset="USD", price_increment="0.1", quantity_increment="0.00000001", min_quantity="0.0001",
        min_notional="1", synthetic=True))
    quote = Observation(observation_id="quote-1", venue="paper", symbol="BTC/USD", event_time_utc=clock.now(),
                        available_at_utc=clock.now(), bid="99", ask="100", bid_size="1", ask_size="1",
                        kind="quote", source="synthetic-test")
    execution.save_observation(quote)
    manifest = ProtectedRuntimeManifest(schema_version=1, protected_package_sha256=protected_package_sha256(),
        deployment_id="synthetic-private-deployment", approved_source_sha256=tuple(
            hashlib.sha256(source.encode()).hexdigest() for source in sources))
    runtime = ProtectedPaperRuntime(database=db, clock=clock, execution=execution, manifest=manifest,
                                   capability_key=KEY, instance_id="protected-test")
    return db, clock, ledger, execution, broker, portfolio, runtime


def activate(runtime, source, release="r1"):
    runtime.controller.admit_release(release_id=release, source_text=source)
    runtime.controller.activate_release(release)


def request(context, *, action="enter", **updates):
    decision = {"action": action, "rationale": "protected test", "invalidation": "new quote",
                "horizon_seconds": 3600, "strategy_id": "slow-trend"}
    if action != "hold":
        decision["quantity"] = "0.01"
    decision.update(updates)
    return json.dumps({"operation": "submit_decision", "request_id": context["request_id"],
                       "capability": context["capability"], "decision": decision})


def test_real_authorization_atomic_once_and_replay_survives_restart(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    raw = request(context)
    outcome = runtime.financial.dispatch(raw)
    assert outcome["intent_id"] and outcome["live_authorization"] is False
    assert db.execute("SELECT COUNT(*) FROM position_reservations").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0] == 1
    restart = ProtectedPaperRuntime(database=db, clock=clock, execution=execution, manifest=runtime.financial.manifest,
                                   capability_key=KEY, instance_id="protected-test")
    assert restart.financial.dispatch(raw) == outcome
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 1
    with pytest.raises(AuthorityDenied, match="different replay"):
        restart.financial.dispatch(request(context, quantity="0.02"))
    assert ledger.books(portfolio).cash_amount("USD") == Decimal("10000")


@pytest.mark.parametrize("change", ["policy", "quote", "ledger", "reservation", "generation", "pause", "expiry",
                                    "marks", "fx", "other_quote", "account", "fee"])
def test_actual_authority_and_financial_freshness_invalidate_proposals(tmp_path, change):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    if change == "policy":
        execution.authority.install_policy(paper_owner_policy(revision_id="owner-2"), role="owner")
    elif change == "quote":
        quote = execution.latest_observation("BTC/USD", execution.now())
        execution.save_observation(quote.model_copy(update={"observation_id": "changed", "ask": Decimal("101")}))
    elif change == "ledger":
        ledger.deposit(portfolio, "USD", Decimal("1"), "owner-flow")
    elif change == "reservation":
        other = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
        runtime.financial.dispatch(request(other))
    elif change == "generation":
        activate(runtime, HOLD, "r2")
    elif change == "pause":
        execution.set_pause(portfolio, "NO_NEW_EXPOSURE", "owner", "test owner halt")
    elif change == "expiry":
        clock.advance(15)
    elif change == "marks":
        ledger.observe_mark(portfolio, "BTC", Decimal("101"), "USD", source="owner-feed")
    elif change == "fx":
        ledger.observe_fx(base="USD", quote="EUR", rate=Decimal("0.9"), source="owner-feed", kind="spot", stale=False)
    elif change == "other_quote":
        quote = execution.latest_observation("BTC/USD", execution.now())
        execution.save_observation(quote.model_copy(update={"observation_id": "other-quote", "symbol": "ETH/USD"}))
    elif change == "account":
        execution.account_id = "changed-private-paper-account"
    elif change == "fee":
        execution.fee_reserve_rate = Decimal("0.01")
    with pytest.raises(StaleState):
        runtime.financial.dispatch(request(context))
    assert db.execute("SELECT state FROM protected_rpc_requests WHERE request_id=?",
                      (context["request_id"],)).fetchone()[0] in {"ISSUED", "REVOKED"}


@pytest.mark.parametrize("attack", ["enable_live", "budget", "ledger", "policy_revision", "mode", "system_version_id"])
def test_child_cannot_supply_protected_fields(tmp_path, attack):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    with pytest.raises(ValidationFailure):
        runtime.financial.dispatch(request(context, **{attack: "attacker"}))
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM position_reservations").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_bad_capability_and_privileged_rpc_have_no_effect(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    envelope = json.loads(request(context))
    envelope["capability"] = "0" * 64
    with pytest.raises(AuthorityDenied, match="authentication"):
        runtime.financial.dispatch(json.dumps(envelope))
    envelope["capability"] = context["capability"]
    envelope["operation"] = "set_budget"
    with pytest.raises(AuthorityDenied):
        runtime.financial.dispatch(json.dumps(envelope))
    assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_real_os_child_cannot_read_database_secret_env_or_network(tmp_path, monkeypatch):
    source = '''
import os, socket
def propose(context):
    assert "SYNTHETIC_PARENT_KEY" not in os.environ
    for action in (lambda: os.open(PATH, os.O_RDONLY), lambda: socket.socket()):
        try:
            action()
            raise AssertionError("OS boundary failed")
        except PermissionError:
            pass
    assert "budget" not in context and "policy" not in context and "ledger" not in context
    return {"action":"hold", "rationale":"attacks blocked", "invalidation":"new quote",
            "horizon_seconds":3600, "strategy_id":"slow-trend"}
'''.replace("PATH", repr(str(tmp_path / "protected.sqlite")))
    monkeypatch.setenv("SYNTHETIC_PARENT_KEY", KEY.decode())
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path, sources=(source,))
    activate(runtime, source)
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "APPLIED", result
    assert result["response"]["intent_id"] is None
    assert KEY.decode() not in json.dumps(result)
    assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_actual_enter_fill_then_mutable_rollback_preserves_financial_history(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "APPLIED", result
    intent = result["response"]["intent_id"]
    quote = execution.latest_observation("BTC/USD", execution.now())
    execution.on_observation(quote.model_copy(update={"observation_id": "fill-quote"}))
    assert execution.intent_state(intent) == "FILLED"
    before = [dict(row) for row in db.execute("SELECT * FROM ledger_events ORDER BY sequence")]
    activate(runtime, BROKEN, "r2")
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "MUTABLE_REJECTED"
    assert result["recovery"] == "ROLLED_BACK_TO_VALIDATED_RELEASE"
    assert runtime.controller.status()["active_release_id"] == "r1"
    assert [dict(row) for row in db.execute("SELECT * FROM ledger_events ORDER BY sequence")] == before
    assert ledger.books(portfolio).cash_amount("USD") < Decimal("10000")
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.01")


def test_restart_dispatches_prior_intent_even_when_new_candidate_fails(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    outcome = runtime.financial.dispatch(request(context))
    activate(runtime, BROKEN, "r2")
    restarted = ProtectedPaperRuntime(database=db, clock=clock, execution=execution,
        manifest=runtime.financial.manifest, capability_key=KEY, instance_id="protected-test")
    result = asyncio.run(restarted.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "MUTABLE_REJECTED" and result["dispatched"] == 1
    assert execution.intent_state(outcome["intent_id"]) == "OPEN"
    assert db.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] == 1


def test_unknown_submit_reconciles_without_replay_after_restart(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path, broker_type=DropAckBroker)
    activate(runtime, ENTER)
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    intent = result["response"]["intent_id"]
    assert execution.intent_state(intent) == "UNKNOWN"
    recovered = asyncio.run(runtime.recover(portfolio))
    assert recovered["status"] == "RECONCILED"
    # Core deliberately retains UNKNOWN while an acknowledged-open order has
    # no fills. That uncertainty must keep its reservation and never replay.
    assert execution.intent_state(intent) == "UNKNOWN"
    assert db.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] == 1
    quote = execution.latest_observation("BTC/USD", execution.now())
    asyncio.run(runtime.ingest_observation(quote.model_copy(update={"observation_id": "fill-after-unknown"})))
    asyncio.run(runtime.recover(portfolio))
    assert execution.intent_state(intent) == "FILLED"
    assert db.execute("SELECT COUNT(*) FROM order_attempts").fetchone()[0] == 1


def test_owner_cancel_and_management_work_without_mutable_candidate(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    intent = result["response"]["intent_id"]
    execution.set_pause(portfolio, "CANCEL_ALL", "owner", "independent protected management")
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "MANAGE_ONLY"
    assert execution.intent_state(intent) == "CANCELLED"
    assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_management_continues_during_child_timeout(tmp_path, monkeypatch):
    hanging = 'def propose(context):\n    while True:\n        pass\n'
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path, sources=(hanging,))
    activate(runtime, hanging)
    original = execution.reconcile
    calls = []

    async def observe_management():
        calls.append("protected-management")
        await original()

    monkeypatch.setattr(execution, "reconcile", observe_management)
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "MUTABLE_REJECTED"
    assert result["recovery"] == "MANAGE_ONLY"
    assert len(calls) >= 3
    assert runtime.controller.status()["status"] == "MANAGE_ONLY"
    assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_owner_manifest_and_immutable_release_admission_are_authority(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    with pytest.raises(AuthorityDenied, match="owner-manifest"):
        runtime.controller.admit_release(release_id="attack", source_text="def propose(context): return {}")
    activate(runtime, HOLD)
    with pytest.raises(AuthorityDenied, match="immutable"):
        runtime.controller.admit_release(release_id="r1", source_text=ENTER)
    mutable_hashes = list(runtime.financial.manifest.approved_source_sha256)
    for updates in ({"schema_version": True}, {"approved_source_sha256": mutable_hashes},
                    {"operations": ["submit_decision"]}, {"wall_seconds": True}):
        with pytest.raises(ValueError):
            replace(runtime.financial.manifest, **updates)


def test_journal_write_failure_rolls_back_actual_intent_reservation_and_decision(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)
    context = runtime.financial.issue("protected-test", portfolio, "BTC/USD")
    db.execute("""CREATE TRIGGER reject_rpc_commit BEFORE UPDATE ON protected_rpc_requests
        WHEN NEW.state='APPLIED' BEGIN SELECT RAISE(ABORT,'synthetic storage fault'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic storage fault"):
        runtime.financial.dispatch(request(context))
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM position_reservations").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert db.execute("SELECT state FROM protected_rpc_requests").fetchone()[0] == "ISSUED"


def test_old_failed_worker_cannot_roll_back_new_owner_activation(tmp_path):
    hanging = 'def propose(context):\n    while True:\n        pass\n'
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path, sources=(HOLD, ENTER, hanging))
    activate(runtime, HOLD)
    assert asyncio.run(runtime.cycle(portfolio, "BTC/USD"))["status"] == "APPLIED"
    activate(runtime, hanging, "r2")

    async def scenario():
        old = asyncio.create_task(runtime.cycle(portfolio, "BTC/USD"))
        for _ in range(300):
            if db.execute("""SELECT 1 FROM protected_rpc_requests WHERE state='ISSUED'
                AND json_extract(scope_json,'$.release_id')='r2'""").fetchone():
                break
            await asyncio.sleep(0.01)
        assert not old.done()
        assert db.execute("""SELECT 1 FROM protected_rpc_requests WHERE state='ISSUED'
            AND json_extract(scope_json,'$.release_id')='r2'""").fetchone()
        activate(runtime, ENTER, "r3")
        return await old

    result = asyncio.run(scenario())
    assert result["status"] == "MUTABLE_REJECTED"
    assert result["recovery"] == "NEWER_RELEASE_PRESERVED"
    assert runtime.controller.status()["active_release_id"] == "r3"
    assert runtime.controller.status()["status"] == "RUNNING"


def test_cancellation_drains_child_suppresses_rpc_and_keeps_controller_exclusive(tmp_path):
    delayed = 'import time\n' + ENTER.replace('    return {', '''    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline:
        pass
    return {''')
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path, sources=(delayed,))
    activate(runtime, delayed)

    async def scenario():
        active = asyncio.create_task(runtime.cycle(portfolio, "BTC/USD"))
        for _ in range(200):
            if db.execute("SELECT 1 FROM protected_rpc_requests WHERE state='ISSUED'").fetchone():
                break
            await asyncio.sleep(0.01)
        assert not active.done()
        active.cancel()
        await asyncio.sleep(0.05)
        with pytest.raises(StaleState, match="owns the financial database"):
            await runtime.cycle(portfolio, "BTC/USD")
        active.cancel()  # A second cancellation must not release a live child's lock.
        with pytest.raises(asyncio.CancelledError):
            await active

    asyncio.run(scenario())
    assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM protected_rpc_requests WHERE state='ISSUED'").fetchone()[0] == 0
    with runtime._exclusive_controller():
        assert runtime._owns_database


def test_protected_owner_flatten_runs_without_new_mutable_decisions(tmp_path):
    source = ENTER.replace('"quantity":"0.01"', '"quantity":"0.02"')
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path, sources=(source,))
    activate(runtime, source)
    asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    quote = execution.latest_observation("BTC/USD", execution.now())
    asyncio.run(runtime.ingest_observation(quote.model_copy(update={"observation_id": "entered"})))
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.02")
    execution.set_pause(portfolio, "FLATTEN", "owner", "trusted recovery")
    result = asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert result["status"] == "MANAGE_ONLY"
    asyncio.run(runtime.ingest_observation(quote.model_copy(update={"observation_id": "flattened"})))
    asyncio.run(runtime.cycle(portfolio, "BTC/USD"))
    assert execution.owned_quantity(portfolio, "BTC") == 0
    assert execution.pause(portfolio)["achieved"] == "flat-verified"
    assert db.execute("SELECT COUNT(*) FROM decisions WHERE system_version_id='r1'").fetchone()[0] == 1


def test_trusted_feed_waits_for_pending_submit_acknowledgement(tmp_path):
    db, clock, ledger, execution, broker, portfolio, runtime = stack(tmp_path)
    activate(runtime, ENTER)

    async def scenario():
        submitted, release_ack = asyncio.Event(), asyncio.Event()
        original = broker.submit

        async def delayed_ack(intent):
            acknowledgement = await original(intent)
            submitted.set()
            await release_ack.wait()
            return acknowledgement

        broker.submit = delayed_ack
        cycle = asyncio.create_task(runtime.cycle(portfolio, "BTC/USD"))
        await asyncio.wait_for(submitted.wait(), timeout=5)
        quote = execution.latest_observation("BTC/USD", execution.now())
        feed = asyncio.create_task(runtime.ingest_observation(
            quote.model_copy(update={"observation_id": "feed-after-ack"})))
        await asyncio.sleep(0.05)
        assert not feed.done(), "trusted matching must not race an in-flight submit"
        release_ack.set()
        result = await cycle
        await feed
        return result

    result = asyncio.run(scenario())
    assert execution.intent_state(result["response"]["intent_id"]) == "FILLED"
    assert db.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
