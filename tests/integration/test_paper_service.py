"""Actual fenced paper controller, database, broker and role-worker paths; no network."""

import asyncio
import json
import multiprocessing
import os
import signal
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.integration.test_execution import _decision, _quote, _stack

from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.activation import VersionController
from trade_graph.application.artifact_runtime import ArtifactRuntime
from trade_graph.application.authority import AuthorityRecord, seed_paper_authority
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.application.paper_service import PaperService
from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import SystemClock
from trade_graph.domain.errors import StaleState, ValidationFailure


class Feed:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.closed = False

    def poll(self):
        batch = self.batches.pop(0) if self.batches else []
        if isinstance(batch, Exception):
            raise batch
        return batch

    def close(self):
        self.closed = True


def test_failed_feed_shutdown_restores_signals_and_releases_service_ownership(tmp_path):
    _clock, ledger, execution, _broker, _portfolio = _stack(tmp_path)

    class FailingClose(Feed):
        def close(self):
            raise OSError("synthetic private feed shutdown failure")

    service = PaperService(ledger.database, execution, public_feed=FailingClose(), schedule_intervals={})

    async def scenario():
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        with pytest.raises(OSError, match="shutdown failure"):
            await service.run(max_ticks=1)
        assert {signum: signal.getsignal(signum) for signum in previous} == previous
        assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0
        replacement = PaperService(ledger.database, execution, schedule_intervals={})
        await replacement.start()
        await replacement.stop()

    asyncio.run(scenario())


def test_tick_runs_durable_schedules_and_coalesces_restart(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    seen = []
    service = PaperService(ledger.database, execution, handlers={"research": lambda task: seen.append(task) or {}},
                           schedule_intervals={"research": 60})

    async def scenario():
        result = await service.tick(wait_roles=True)
        assert result.completed == result.scheduled == 1
        assert result.failures == ()
        assert seen[0]["role"] == "research"
        assert seen[0]["snapshot_id"]
        assert (await service.tick(wait_roles=True)).scheduled == 0
        await service.stop()
        clock.advance(60 * 100)
        replacement = PaperService(ledger.database, execution,
                                   handlers={"research": lambda task: seen.append(task) or {}},
                                   schedule_intervals={"research": 60})
        assert (await replacement.tick(wait_roles=True)).scheduled == 1
        assert (await replacement.tick(wait_roles=True)).scheduled == 0
        await replacement.stop()

    asyncio.run(scenario())
    assert len(seen) == 2
    assert broker.submit_count == 0
    assert ledger.database.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
    assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0


def test_single_queued_occurrence_does_not_accumulate_during_slow_role(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    def slow(task):
        entered.set()
        assert finish.wait(5)
        return {}

    service = PaperService(ledger.database, execution, handlers={"research": slow},
                           schedule_intervals={"research": 1}, role_ttl_seconds=100)

    async def scenario():
        await service.tick()
        assert await asyncio.to_thread(entered.wait, 5)
        clock.advance(10)
        assert (await service.tick()).scheduled == 0
        assert ledger.database.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        finish.set()
        await service.stop()

    asyncio.run(scenario())
    assert ledger.database.execute("SELECT status FROM tasks").fetchone()[0] == "SUCCEEDED"


def test_execution_reconciles_unknown_on_restart_without_resubmission(tmp_path):
    clock, ledger, execution, drop, portfolio = _stack(tmp_path, lambda db, clk: DropAckBroker(PaperBroker(db, clk)))
    execution.save_observation(_quote(clock, "99", "100"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    assert execution.intent_state(intent) == "UNKNOWN"
    clock.advance(1)
    # Matching has happened in the broker, but process died before recording its fill.
    drop.inner.match(_quote(clock, "99", "100", observation_id="lost-fill"))
    database = Database(ledger.database.path)
    replacement_broker = PaperBroker(database, clock)
    replacement = Execution(database, Ledger(database, clock), clock, replacement_broker)
    service = PaperService(database, replacement, schedule_intervals={})

    async def scenario():
        await service.start()
        assert replacement.intent_state(intent) == "FILLED"
        assert replacement.owned_quantity(portfolio, "BTC") == Decimal("0.01")
        await service.tick()
        await service.stop()

    asyncio.run(scenario())
    assert drop.inner.submit_count == 1
    assert replacement_broker.submit_count == 0
    assert database.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
    assert database.execute("SELECT count(*) FROM position_reservations WHERE state = 'held'").fetchone()[0] == 0
    database.close()


def test_failure_does_not_stop_paused_position_protection(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100", observation_id="entry"))
    execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="fill-entry"))
    ledger.observe_mark(portfolio, "BTC", Decimal("100"), "USD", source="fixture")
    protection = execution.place_protection(portfolio, "BTC/USD", Decimal("0.01"), Decimal("90"), "snapshot")
    asyncio.run(execution.dispatch())

    def fail(task):
        raise ValueError("model provider refused")

    service = PaperService(ledger.database, execution, handlers={"trader": fail},
                           schedule_intervals={"trader": 60})

    async def scenario():
        assert (await service.tick(wait_roles=True)).completed == 1
        assert execution.pause(portfolio)["profile"] == "MANAGE_ONLY"
        clock.advance(1)
        service.public_feed = Feed([_quote(clock, "80", "81", observation_id="stop")])
        result = await service.tick(wait_feed=True, wait_roles=True)
        assert result.observations == 1
        assert execution.intent_state(protection) == "FILLED"
        assert execution.owned_quantity(portfolio, "BTC") == 0
        assert result.scheduled == 0
        await service.stop()

    asyncio.run(scenario())
    failed = ledger.database.execute("SELECT * FROM tasks WHERE role = 'trader'").fetchone()
    assert failed["status"] == "FAILED"
    assert json.loads(failed["output_json"])["reason"] == "ValueError"
    assert ledger.database.execute("SELECT count(*) FROM secretary_reports WHERE kind = 'failed'").fetchone()[0] == 1


def test_software_secretary_keeps_owner_halt_and_routes_material_evidence_once(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    service = PaperService(ledger.database, execution, handlers={"leader": lambda task: {}}, schedule_intervals={})
    ledger._activity(portfolio, "execution_failed", {"synthetic": True})

    async def scenario():
        execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "owner halt")
        await service.tick(wait_roles=True)
        assert ledger.database.execute("SELECT count(*) FROM secretary_reports").fetchone()[0] == 1
        assert ledger.database.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert execution.pause(portfolio)["originator"] == "owner"
        execution.set_pause(portfolio, "RUNNING", "owner", "resume")
        # New material event creates one current route; already-digested halted
        # reports remain available in the digest and periodic review.
        ledger._activity(portfolio, "execution_failed", {"synthetic": True, "next": True})
        await service.tick(wait_roles=True)
        await service.tick(wait_roles=True)
        await service.stop()

    asyncio.run(scenario())
    assert ledger.database.execute("SELECT count(*) FROM tasks WHERE role = 'leader'").fetchone()[0] == 1


def test_feed_failure_blocks_increases_but_still_manages_orders(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    feed = Feed(OSError("offline"), [_quote(clock, "99", "100", observation_id="next")])
    service = PaperService(ledger.database, execution, public_feed=feed, schedule_intervals={})

    async def scenario():
        result = await service.tick(wait_feed=True)
        assert result.failures == ("feed:OSError",)
        assert broker.submit_count == 0
        assert execution.intent_state(intent) == "SUBMISSION_PENDING"
        assert execution.blocks_increase("BTC/USD")
        clock.advance(1)
        assert (await service.tick(wait_feed=True)).failures == ()
        assert broker.submit_count == 1
        assert not execution.blocks_increase("BTC/USD")
        await service.stop()

    asyncio.run(scenario())
    assert feed.closed


def test_future_feed_input_is_rejected_and_does_not_refresh_market(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    observation = _quote(clock, "99", "100").model_copy(update={
        "event_time_utc": clock.now() + timedelta(hours=1),
    })
    service = PaperService(ledger.database, execution, public_feed=Feed([observation]), schedule_intervals={})

    async def scenario():
        result = await service.tick(wait_feed=True)
        assert result.failures == ("feed:ValidationFailure",)
        await service.stop()

    asyncio.run(scenario())
    assert ledger.database.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
    assert broker.submit_count == 0


def test_feed_local_sequence_reuse_survives_restart(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)

    async def scenario():
        for price in ("99", "100"):
            feed = Feed([_quote(clock, price, "101", observation_id="feed-sequence-1")])
            service = PaperService(ledger.database, execution, public_feed=feed, schedule_intervals={})
            await service.tick(wait_feed=True)
            await service.stop()
            clock.advance(1)

    asyncio.run(scenario())
    assert ledger.database.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    assert execution.latest_observation("BTC/USD", execution.now()).bid == Decimal("100")


def test_flock_blocks_aliases_even_after_persisted_lease_expiry(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    service = PaperService(ledger.database, execution, schedule_intervals={})
    alias = tmp_path / "hardlink.sqlite"
    os.link(ledger.database.path, alias)
    # Opening another Database via a hardlink could create independent SQLite WAL
    # sidecars. Only open an OS file here: same-inode flock must still conflict.
    import fcntl

    async def scenario():
        await service.start()
        ledger.database.execute("UPDATE process_leases SET expires_at = '2000-01-01T00:00:00.000000Z'")
        contender = PaperService(ledger.database, execution, schedule_intervals={})
        with pytest.raises(StaleState, match="another paper service"):
            await contender.start()
        descriptor = os.open(alias, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        await service.stop()
        await contender.start()
        await contender.stop()

    asyncio.run(scenario())
    assert broker.submit_count == 0


def test_startup_recovers_previous_boot_lease_before_controller_work(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    scheduler = Scheduler(ledger.database, clock)
    previous = "paper-service-crashed-boot"
    assert scheduler.acquire_process_lease("paper-service", previous)
    assert scheduler.acquire_process_lease("role-worker", previous)
    evidence = []
    service = PaperService(ledger.database, execution, schedule_intervals={}, recover_commands=lambda: evidence.append(
        ledger.database.execute("SELECT owner FROM process_leases WHERE lease_name='paper-service'").fetchone()[0],
    ))
    asyncio.run(service.run(max_ticks=1))
    assert evidence == [service.owner]
    assert broker.submit_count == 0


def test_startup_never_reclaims_an_unrelated_role_worker(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    scheduler = Scheduler(ledger.database, clock)
    scheduler.acquire_process_lease("role-worker", "unrelated-worker")
    service = PaperService(ledger.database, execution, schedule_intervals={})
    with pytest.raises(StaleState, match="persisted lease"):
        asyncio.run(service.start())
    assert ledger.database.execute("SELECT owner FROM process_leases").fetchone()[0] == "unrelated-worker"


def _slow_child(path, entered, finish):
    database = Database(path)
    clock = SystemClock()
    execution = Execution(database, Ledger(database, clock), clock, PaperBroker(database, clock))

    def slow(task):
        entered.set()
        if not finish.wait(10):
            raise AssertionError("test did not release synthetic handler")
        return {"done": True}

    service = PaperService(database, execution, handlers={"research": slow}, schedule_intervals={},
                           role_ttl_seconds=1, tick_interval_seconds=0.02)
    try:
        asyncio.run(service.run())
    finally:
        database.close()


def test_slow_synchronous_role_renews_lease_and_drains_sigterm(tmp_path, monkeypatch):
    _clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    scheduler = Scheduler(ledger.database, SystemClock())
    # importlib pytest mode can omit the repository from sys.path when launched
    # by the installed pytest entry point; spawned children must import this target.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    task_id = scheduler.add_task(role="research", objective="synthetic slow inference", portfolio_id=portfolio)
    context = multiprocessing.get_context("spawn")
    entered, finish = context.Event(), context.Event()
    child = context.Process(target=_slow_child, args=(ledger.database.path, entered, finish))
    child.start()
    try:
        assert entered.wait(8)
        # More than one lease TTL elapses during a blocked synchronous handler.
        assert not finish.wait(1.3)
        row = ledger.database.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        assert row["status"] == "RUNNING"
        assert row["lease_expires_at"] > Scheduler(ledger.database, SystemClock()).now()
        contender = PaperService(ledger.database, execution, schedule_intervals={})
        with pytest.raises(StaleState, match="another paper service"):
            asyncio.run(contender.start())
        os.kill(child.pid, signal.SIGTERM)
        child.join(timeout=0.2)
        assert child.is_alive()  # A real paid attempt would retain its fence while draining.
        finish.set()
        child.join(timeout=8)
        assert child.exitcode == 0
    finally:
        finish.set()
        if child.is_alive():
            child.terminate()
            child.join(timeout=3)
    row = ledger.database.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    assert row["status"] == "SUCCEEDED"
    assert row["attempts_used"] == 1
    assert ledger.database.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0


def test_market_poll_does_not_block_reconciliation_while_waiting(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    class SlowFeed(Feed):
        def poll(self):
            entered.set()
            assert finish.wait(5)
            return []

    service = PaperService(ledger.database, execution, public_feed=SlowFeed(), schedule_intervals={})
    execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "manage during feed outage")

    async def scenario():
        assert (await service.tick()).management[portfolio] == "managing"
        assert await asyncio.to_thread(entered.wait, 5)
        result = await asyncio.wait_for(service.tick(), timeout=1)
        assert result.management[portfolio] == "managing"
        finish.set()
        await service.stop()

    asyncio.run(scenario())


def test_bounded_run_ingests_its_final_public_quote(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    service = PaperService(ledger.database, execution, public_feed=Feed([_quote(clock, "99", "100")]),
                           schedule_intervals={})
    summary = asyncio.run(service.run(max_ticks=1))
    assert summary["observations"] == summary["ticks"] == 1
    assert summary["stopped"]
    assert ledger.database.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_cancelling_role_wait_and_shutdown_retains_fence_until_thread_drains(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    def slow(task):
        entered.set()
        assert finish.wait(5)
        return {}

    service = PaperService(ledger.database, execution, handlers={"research": slow},
                           schedule_intervals={"research": 60}, tick_interval_seconds=0.02)

    async def scenario():
        turn = asyncio.create_task(service.tick(wait_roles=True))
        assert await asyncio.to_thread(entered.wait, 5)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        shutdown = asyncio.create_task(service.stop())
        await asyncio.sleep(0.05)
        shutdown.cancel()
        await asyncio.sleep(0.05)
        assert not shutdown.done()
        contender = PaperService(ledger.database, execution, schedule_intervals={})
        with pytest.raises(StaleState, match="another paper service"):
            await contender.start()
        assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 2
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert ledger.database.execute("SELECT status FROM tasks").fetchone()[0] == "SUCCEEDED"
        await contender.start()
        await contender.stop()

    asyncio.run(scenario())
    assert broker.submit_count == 0


def test_shutdown_keeps_management_running_until_slow_feed_drains(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    class SlowFeed(Feed):
        def poll(self):
            entered.set()
            assert finish.wait(5)
            return []

    service = PaperService(ledger.database, execution, public_feed=SlowFeed(), schedule_intervals={},
                           tick_interval_seconds=0.02)

    async def scenario():
        await service.tick()
        assert await asyncio.to_thread(entered.wait, 5)
        shutdown = asyncio.create_task(service.stop())
        execution.set_pause(portfolio, "MANAGE_ONLY", "owner", "manage during shutdown")
        for _ in range(50):
            if execution.pause(portfolio)["achieved"] == "managing":
                break
            await asyncio.sleep(0.01)
        assert execution.pause(portfolio)["achieved"] == "managing"
        assert not shutdown.done()
        finish.set()
        await shutdown

    asyncio.run(scenario())


def test_replacing_failed_feed_restores_original_gate_and_contender_cannot_modify_it(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    def original_gate(symbol):
        return False
    execution.blocks_increase = original_gate
    first = PaperService(ledger.database, execution, public_feed=Feed(OSError("offline")), schedule_intervals={})
    assert execution.blocks_increase is original_gate  # Construction has no controller effects.

    async def scenario():
        await first.tick(wait_feed=True)
        assert execution.blocks_increase("BTC/USD")
        previous_gate = execution.blocks_increase
        contender = PaperService(ledger.database, execution, public_feed=Feed(), schedule_intervals={})
        with pytest.raises(StaleState):
            await contender.start()
        assert execution.blocks_increase == previous_gate
        await first.stop()
        assert execution.blocks_increase is original_gate
        assert (await contender.tick(wait_feed=True)).failures == ()
        assert not execution.blocks_increase("BTC/USD")
        await contender.stop()
        assert execution.blocks_increase is original_gate

    asyncio.run(scenario())


def test_failed_startup_callback_cannot_dispatch_pending_intents(tmp_path):
    clock, ledger, execution, broker, portfolio = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100"))
    intent = execution.authorize(portfolio, _decision(clock, portfolio))

    def fail_recovery():
        raise ValueError("recovery is unavailable")

    service = PaperService(ledger.database, execution, schedule_intervals={}, recover_commands=fail_recovery)
    with pytest.raises(ValueError, match="recovery"):
        asyncio.run(service.start())
    assert broker.submit_count == 0
    assert execution.intent_state(intent) == "SUBMISSION_PENDING"
    assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0


def test_concurrent_tick_waits_for_startup_recovery_and_only_one_role_runs(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    seen = []

    def recover():
        entered.set()
        assert finish.wait(5)

    service = PaperService(ledger.database, execution, handlers={"research": lambda task: seen.append(task) or {}},
                           schedule_intervals={"research": 60}, recover_commands=recover)

    async def scenario():
        startup = asyncio.create_task(service.start())
        assert await asyncio.to_thread(entered.wait, 5)
        turns = [asyncio.create_task(service.tick(wait_roles=True)) for _ in range(2)]
        await asyncio.sleep(0.05)
        assert seen == []
        assert ledger.database.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        finish.set()
        await startup
        outcomes = await asyncio.gather(*turns)
        assert sum(outcome.completed for outcome in outcomes) == 1
        await service.stop()

    asyncio.run(scenario())
    assert len(seen) == 1


def test_failed_worker_task_still_drains_feed_before_releasing_process_fence(tmp_path, monkeypatch):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    class SlowFeed(Feed):
        def poll(self):
            entered.set()
            assert finish.wait(5)
            return []

    def fail_claim():
        raise ValueError("worker storage unavailable")

    service = PaperService(ledger.database, execution, public_feed=SlowFeed(), schedule_intervals={},
                           tick_interval_seconds=0.02)
    monkeypatch.setattr(service, "_run_role", fail_claim)

    async def scenario():
        await service.tick()
        assert await asyncio.to_thread(entered.wait, 5)
        shutdown = asyncio.create_task(service.stop())
        await asyncio.sleep(0.05)
        assert not shutdown.done()
        contender = PaperService(ledger.database, execution, schedule_intervals={})
        with pytest.raises(StaleState):
            await contender.start()
        finish.set()
        with pytest.raises(ValueError, match="worker storage"):
            await shutdown
        assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("blocked_work", ["role", "feed", "close"])
def test_failed_maintenance_retains_fence_until_blocking_work_and_cleanup_finish(tmp_path, monkeypatch, blocked_work):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    entered, finish, maintenance_failed = threading.Event(), threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert finish.wait(5)

    def role(task):
        blocked()
        return {}

    class SlowFeed(Feed):
        def poll(self):
            if blocked_work == "feed":
                blocked()
                return [_quote(clock, "99", "100", observation_id="drained-feed")]
            return []

        def close(self):
            if blocked_work == "close":
                blocked()
            super().close()
            if blocked_work == "role":
                raise ValueError("later feed cleanup failure")

    feed = SlowFeed()
    service = PaperService(ledger.database, execution, public_feed=feed,
                           handlers={"research": role} if blocked_work == "role" else {},
                           schedule_intervals={"research": 60} if blocked_work == "role" else {},
                           tick_interval_seconds=0.02)

    def unavailable_management():
        maintenance_failed.set()
        raise OSError("management storage unavailable")

    async def scenario():
        await service.tick()
        if blocked_work != "close":
            assert await asyncio.to_thread(entered.wait, 5)
        monkeypatch.setattr(service, "_management", unavailable_management)
        shutdown = asyncio.create_task(service.stop())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert await asyncio.to_thread(maintenance_failed.wait, 5)
            assert not shutdown.done()
            contender = PaperService(ledger.database, execution, schedule_intervals={})
            with pytest.raises(StaleState, match="another paper service"):
                await contender.start()
        finally:
            finish.set()
        with pytest.raises(OSError, match="management storage") as error:
            await shutdown
        if blocked_work == "role":
            assert any("ValueError" in note for note in error.value.__notes__)
        assert feed.closed
        assert service._lock_fd is None
        assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0

    asyncio.run(scenario())
    if blocked_work == "role":
        assert ledger.database.execute("SELECT status FROM tasks").fetchone()[0] == "SUCCEEDED"
    if blocked_work == "feed":
        assert ledger.database.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_shutdown_drains_heartbeat_storage_started_while_role_is_draining(tmp_path, monkeypatch):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    role_entered, role_finish = threading.Event(), threading.Event()
    drain_entered, heartbeat_entered, heartbeat_finish = threading.Event(), threading.Event(), threading.Event()

    def role(task):
        role_entered.set()
        assert role_finish.wait(5)
        return {}

    service = PaperService(ledger.database, execution, handlers={"research": role},
                           schedule_intervals={"research": 60}, role_ttl_seconds=1, tick_interval_seconds=0.02)
    heartbeat, management = service._heartbeat_once, service._management

    def blocked_heartbeat():
        heartbeat_entered.set()
        assert heartbeat_finish.wait(5)
        heartbeat()

    def draining_management():
        drain_entered.set()
        return management()

    async def scenario():
        await service.tick()
        assert await asyncio.to_thread(role_entered.wait, 5)
        monkeypatch.setattr(service, "_management", draining_management)
        shutdown = asyncio.create_task(service.stop())
        try:
            # Management during role drain proves the first pending-operation
            # snapshot has completed before the next heartbeat offload starts.
            assert await asyncio.to_thread(drain_entered.wait, 5)
            monkeypatch.setattr(service, "_heartbeat_once", blocked_heartbeat)
            assert await asyncio.to_thread(heartbeat_entered.wait, 5)
            role_finish.set()
            await asyncio.sleep(0.05)
            assert not shutdown.done()
            contender = PaperService(ledger.database, execution, schedule_intervals={})
            with pytest.raises(StaleState, match="another paper service"):
                await contender.start()
        finally:
            role_finish.set()
            heartbeat_finish.set()
        await shutdown
        assert service._pending_operations == set()
        assert service._lock_fd is None
        assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0

    asyncio.run(scenario())


def test_artifact_leader_cadence_keeps_digest_and_current_owner_attempt_bound(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    authority = AuthorityRecord(ledger.database, clock)
    authority.install_policy(authority.active_policy().model_copy(update={
        "revision_id": "bounded-policy", "ordinary_max_paid_attempts": 1,
    }), role="owner")
    versions = VersionController(ledger.database, clock)
    bundle = versions.register_baseline(portfolio, "service-baseline", {
        "artifacts/context_policy.json": json.dumps({
            "schema_version": 1, "max_general_lessons": 2,
            "always_include": ["mandate_obligations", "active_safety"],
        }),
        "artifacts/schedules.json": json.dumps({"schema_version": 1, "interval_seconds": {"leader": 10}}),
    })
    runtime = ArtifactRuntime(versions, Scheduler(ledger.database, clock))
    service = PaperService(ledger.database, execution, artifact_runtime=runtime,
                           handlers={"leader": lambda task: {}, "research": lambda task: {}},
                           schedule_intervals={"leader": 3600, "research": 60})
    report_id = service.secretary.report(portfolio, role="research", kind="dossier", summary="Synthetic dossier",
                                         evidence_refs=["synthetic-evidence"], source_key="service-dossier")

    async def scenario():
        assert (await service.tick(wait_roles=True)).scheduled == 2
        await service.tick(wait_roles=True)
        clock.advance(10)
        assert (await service.tick(wait_roles=True)).scheduled == 1
        await service.stop()

    asyncio.run(scenario())
    leaders = ledger.database.execute("SELECT * FROM tasks WHERE role='leader' ORDER BY created_at").fetchall()
    assert len(leaders) == 2
    assert all(row["objective"] == "artifact-leader-review" for row in leaders)
    assert all(row["max_attempts"] == 1 for row in leaders)
    assert all(row["expected_version"] == bundle["artifact_hash"] for row in leaders)
    first = json.loads(leaders[0]["input_json"])
    digest = json.loads(ledger.database.execute(
        "SELECT document_json FROM secretary_digests WHERE digest_id = ?", (first["digest_id"],),
    ).fetchone()[0])
    assert report_id in digest["evidence_refs"]
    research = ledger.database.execute("SELECT * FROM tasks WHERE role='research'").fetchone()
    assert research["expected_version"] == bundle["artifact_hash"]
    assert research["max_attempts"] == 1


def test_selected_graph_portfolio_keeps_older_order_and_position_management(tmp_path):
    clock, ledger, execution, _broker, older = _stack(tmp_path)
    execution.save_observation(_quote(clock, "99", "100", observation_id="entry"))
    execution.authorize(older, _decision(clock, older, record_id="old-entry"))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    execution.on_observation(_quote(clock, "99", "100", observation_id="entry-fill"))
    ledger.observe_mark(older, "BTC", Decimal("100"), "USD", source="fixture")
    protection = execution.place_protection(older, "BTC/USD", Decimal("0.01"), Decimal("50"), "snapshot")
    increase = execution.authorize(older, _decision(clock, older, record_id="old-increase"))
    asyncio.run(execution.dispatch())
    execution.set_pause(older, "NO_NEW_EXPOSURE", "owner", "old experiment retains its positions")
    newer = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(newer, "USD", Decimal("10000"), "new-experiment")
    seed_paper_authority(ledger.database, clock, newer)
    seen = []
    service = PaperService(ledger.database, execution, portfolio_ids=[newer],
                           handlers={"research": lambda task: seen.append(task) or {}},
                           schedule_intervals={"research": 60})

    async def scenario():
        result = await service.tick(wait_roles=True)
        assert set(service.management_portfolio_ids) == {older, newer}
        assert result.management[older] == "increases-cleared"
        assert execution.intent_state(increase) == "CANCELLED"
        assert execution.intent_state(protection) == "OPEN"
        execution.save_observation(_quote(clock, "100", "100.1", observation_id="flatten-quote"))
        execution.set_pause(older, "FLATTEN", "owner", "close previous experiment")
        assert (await service.tick(wait_roles=True)).management[older] == "flattening"
        clock.advance(1)
        execution.on_observation(_quote(clock, "100", "100.1", observation_id="flatten-fill"))
        assert (await service.tick(wait_roles=True)).management[older] == "flat-verified"
        await service.stop()

    asyncio.run(scenario())
    assert len(seen) == 1 and seen[0]["portfolio_id"] == newer
    assert ledger.database.execute("SELECT count(*) FROM tasks WHERE portfolio_id = ?", (older,)).fetchone()[0] == 0
    assert execution.owned_quantity(older, "BTC") == 0


@pytest.mark.parametrize("other_mode", ["live", "replay"])
def test_mixed_mode_database_is_refused_before_any_financial_management(tmp_path, other_mode):
    clock, ledger, execution, broker, paper = _stack(tmp_path)
    historical = ledger.create_portfolio(reporting_currency="USD", mode=other_mode)
    ledger.deposit(historical, "USD", Decimal("1"), "synthetic-historical-account")
    execution.save_observation(_quote(clock, "99", "100"))
    intent = execution.authorize(paper, _decision(clock, paper))
    execution.set_pause(paper, "NO_NEW_EXPOSURE", "owner", "do not touch even paper state before validation")
    before_events = ledger.database.execute("SELECT count(*) FROM ledger_events").fetchone()[0]
    service = PaperService(ledger.database, execution, portfolio_ids=[paper], schedule_intervals={})
    with pytest.raises(ValidationFailure, match="only paper portfolios"):
        asyncio.run(service.start())
    assert broker.submit_count == 0
    assert execution.intent_state(intent) == "SUBMISSION_PENDING"
    assert execution.pause(paper)["achieved"] == "requested"
    assert ledger.database.execute("SELECT count(*) FROM ledger_events").fetchone()[0] == before_events
    assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0


def test_leader_can_review_and_resume_its_own_pause_while_other_roles_wait(tmp_path):
    clock, ledger, execution, _broker, portfolio = _stack(tmp_path)
    seen = []
    execution.set_pause(portfolio, "MANAGE_ONLY", "leader", "bounded leadership review")

    def leader(task):
        seen.append(task["role"])
        execution.set_pause(portfolio, "RUNNING", "leader", "review completed")
        return {}

    service = PaperService(ledger.database, execution, handlers={
        "leader": leader, "research": lambda task: seen.append(task["role"]) or {},
    }, schedule_intervals={"research": 60, "leader": 60})

    async def scenario():
        assert (await service.tick(wait_roles=True)).scheduled == 1
        assert seen == ["leader"]
        assert execution.pause(portfolio)["profile"] == "RUNNING"
        assert (await service.tick(wait_roles=True)).scheduled == 1
        assert seen == ["leader", "research"]
        await service.stop()

    asyncio.run(scenario())


def test_service_loads_activated_bytes_and_keeps_receipt_provenance(tmp_path, monkeypatch):
    from tests.integration.test_artifact_consumers import NEW_PROMPT, _consumer_flow

    flow = _consumer_flow(tmp_path, schedule=True)
    flow.bind_consumer()
    requests = []
    complete = flow.gateway.scripted.complete

    def capture(request):
        requests.append(request)
        return complete(request)

    monkeypatch.setattr(flow.gateway.scripted, "complete", capture)
    # The prior fixture worker finished commissioning before the controller starts.
    flow.db.execute("DELETE FROM process_leases")
    service = PaperService(flow.db, flow.office.execution, handlers={"trader": flow.handler},
                           artifact_runtime=flow.runtime, secretary=flow.secretary,
                           schedule_intervals={"trader": 14400})

    async def scenario():
        result = await service.tick(wait_roles=True)
        assert result.scheduled == 1
        assert result.completed == 1
        assert result.failures == ()
        await service.stop()

    asyncio.run(scenario())
    task = flow.db.execute("SELECT * FROM tasks WHERE role='trader'").fetchone()
    assert task["status"] == "SUCCEEDED", task["output_json"]
    snapshot = json.loads(flow.db.execute(
        "SELECT payload_json FROM snapshots WHERE json_extract(payload_json, '$.task_id') = ?", (task["task_id"],),
    ).fetchone()[0])
    assert NEW_PROMPT in requests[0].instructions
    assert snapshot["artifact"]["artifact_hash"] == flow.versions.current_hash(flow.pid)
    assert flow.receipts(task["task_id"])[0]["system_version_id"] == snapshot["artifact"]["artifact_hash"]


@pytest.mark.parametrize("options", [{"tick_interval_seconds": 0}, {"role_ttl_seconds": True},
                                    {"schedule_intervals": {"engineer": 1}}])
def test_invalid_service_configuration_fails_before_startup(tmp_path, options):
    _clock, ledger, execution, _broker, _portfolio = _stack(tmp_path)
    with pytest.raises(ValidationFailure):
        PaperService(ledger.database, execution, **options)
    assert ledger.database.execute("SELECT count(*) FROM process_leases").fetchone()[0] == 0
