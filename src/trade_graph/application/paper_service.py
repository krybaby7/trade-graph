"""Single-host paper controller; financial maintenance outlives role failures.

Synchronous role/provider work and public-feed polling use separate threads. The
controller owns a real OS flock, not just an expiring database lease, throughout
their lifetime. Nothing here constructs a network transport or a paid provider.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import signal
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.scheduler import Scheduler, TaskLease
from trade_graph.application.secretary import Secretary
from trade_graph.application.service_controller import service_finished, service_heartbeat, service_started
from trade_graph.application.worker import RoleWorker
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure

DEFAULT_SCHEDULES = {
    "trader": 14400,
    "research": 86400,
    "learning": 302400,
    "leader": 604800,
    "optimisation": 604800,
}
PAUSED_WORK = {"PAUSE_DECISIONS", "MANAGE_ONLY", "CANCEL_ALL", "FLATTEN", "STOPPED"}


@dataclass(frozen=True)
class TickResult:
    observations: int = 0
    scheduled: int = 0
    completed: int = 0
    role_active: bool = False
    management: dict[str, str] | None = None
    failures: tuple[str, ...] = ()


def _call(callback: Callable, *args, **kwargs):
    result = callback(*args, **kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


class PaperService:
    def __init__(
        self,
        database: Database,
        execution,
        *,
        clock: Clock | None = None,
        portfolio_ids: Sequence[str] | None = None,
        handlers: Mapping[str, Callable[[dict], dict]] | None = None,
        artifact_runtime=None,
        public_feed=None,
        secretary: Secretary | None = None,
        schedule_intervals: Mapping[str, int] | None = None,
        tick_interval_seconds: float = 1,
        role_ttl_seconds: int = 30,
        recover_commands: Callable[[], Any] | None = None,
        prepare_runtime: Callable[[], Mapping[str, Callable]] | None = None,
        runtime_ready: Callable[[], bool] | None = None,
        system_version_id: str = "paper-runtime",
        service_mode: str = "paper",
        service_run_id: str | None = None,
        subscription_provider: str | None = None,
        automatic_schedule: bool = True,
        optimisation_manual_only: bool = False,
    ) -> None:
        if (service_mode not in {"paper", "live"} or execution.mode != service_mode
                or execution.database is not database):
            raise ValidationFailure(f"{service_mode} execution bound to the service database is required")
        if subscription_provider not in {None, "codex_subscription", "claude_subscription"}:
            raise ValidationFailure("one supported subscription provider is required")
        if type(automatic_schedule) is not bool or type(optimisation_manual_only) is not bool:
            raise ValidationFailure("explicit boolean schedule controls required")
        self.automatic_schedule = automatic_schedule
        self.optimisation_manual_only = optimisation_manual_only
        self.subscription_provider = subscription_provider
        self.service_mode, self.service_run_id = service_mode, service_run_id
        if (isinstance(tick_interval_seconds, bool) or not isinstance(tick_interval_seconds, (int, float))
                or not math.isfinite(tick_interval_seconds) or tick_interval_seconds <= 0):
            raise ValidationFailure("positive finite service tick interval required")
        Scheduler._positive(role_ttl_seconds, "role_ttl_seconds")
        self.database, self.execution = database, execution
        self.clock = clock or execution.clock
        self.scheduler = artifact_runtime.scheduler if artifact_runtime else Scheduler(database, self.clock)
        self.scheduler.manual_optimisation_only = optimisation_manual_only
        if self.scheduler.database is not database:
            raise ValidationFailure("artifact runtime must use the service database")
        self.secretary = secretary or Secretary(execution, self.scheduler, artifact_runtime=artifact_runtime)
        self.secretary.subscription_only = subscription_provider is not None
        self.handlers = dict(handlers or {})
        self.artifact_runtime, self.public_feed = artifact_runtime, public_feed
        if artifact_runtime:
            if optimisation_manual_only:
                artifact_runtime.disabled_schedule_roles.add("optimisation")
            else:
                artifact_runtime.disabled_schedule_roles.discard("optimisation")
        self.portfolio_ids = tuple(dict.fromkeys(portfolio_ids or ()))
        self.management_portfolio_ids: tuple[str, ...] = ()
        self.schedule_intervals = dict(DEFAULT_SCHEDULES if schedule_intervals is None else schedule_intervals)
        if optimisation_manual_only:
            self.schedule_intervals.pop("optimisation", None)
        for role, interval in self.schedule_intervals.items():
            if role not in DEFAULT_SCHEDULES:
                raise ValidationFailure("Engineer work must be explicitly commissioned")
            Scheduler._positive(interval, f"{role} interval")
        self.tick_interval_seconds, self.role_ttl_seconds = tick_interval_seconds, role_ttl_seconds
        self.recover_commands, self.system_version_id = recover_commands, system_version_id
        self.prepare_runtime, self.runtime_ready = prepare_runtime, runtime_ready
        self.owner = "paper-service-" + uuid.uuid4().hex
        self.worker = RoleWorker(self.scheduler, owner=self.owner, system_version_id=system_version_id,
                                 reconcile=self._reconcile, artifact_runtime=artifact_runtime)
        self._lock_fd: int | None = None
        self._ownership_context = None
        self._started = False
        self._ready = False
        self._stopping = False
        self._stop_requested = asyncio.Event()
        self._heartbeat_task: asyncio.Task | None = None
        self._role_task: asyncio.Task | None = None
        self._feed_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._start_task: asyncio.Task | None = None
        self._tick_lock = asyncio.Lock()
        self._pending_operations: set[asyncio.Task] = set()
        self._active_lease: TaskLease | None = None
        self._completed_total = 0
        self._observations_total = 0
        self._shutdown_failures: list[str] = []
        self._run_error_type: str | None = None
        self._last_tick_failures: tuple[str, ...] = ()
        self._boot_completed = False
        self._lease_lock = threading.Lock()
        self._execution_lock = threading.RLock()
        self._feed_failed = public_feed is not None
        self._old_gate = None
        self._feed_failure_count = 0
        self._feed_first_failed_at = None
        self._feed_last_diagnostic_at = None
        self._feed_last_failure = None

    def _blocks_increase(self, symbol: str) -> bool:
        gate = getattr(self.public_feed, "blocks_increase", None)
        return (self._feed_failed or (bool(gate(symbol)) if gate else False)
                or (bool(self._old_gate(symbol)) if self._old_gate else False))

    def request_stop(self) -> None:
        """Signal a graceful drain; current paid attempts are never cancelled/replayed."""
        self._stop_requested.set()

    def _acquire(self) -> None:
        ownership = self.database.exclusive_lock("paper-service", single_link=False)
        try:
            descriptor = ownership.__enter__()
        except StaleState as exc:
            raise StaleState("another paper service owns this database or its path changed") from exc
        self._ownership_context = ownership
        self._lock_fd = descriptor
        try:
            with self.database.immediate():
                previous = self.database.execute(
                    "SELECT owner FROM process_leases WHERE lease_name = 'paper-service'",
                ).fetchone()
                if previous and previous["owner"].startswith("paper-service-"):
                    # The flock proves the prior controller no longer owns this file.
                    # Reclaim only that boot's leases, never an unrelated role worker.
                    self.database.execute(
                        "DELETE FROM process_leases WHERE lease_name IN ('paper-service', 'role-worker') AND owner = ?",
                        (previous["owner"],),
                    )
                for name in ("paper-service", "role-worker"):
                    if not self.scheduler.acquire_process_lease(name, self.owner, self.role_ttl_seconds):
                        raise StaleState("another worker has an active persisted lease")
        except BaseException:
            self._release()
            raise

    def _release(self) -> None:
        if self._lock_fd is not None:
            try:
                self.database.execute("DELETE FROM process_leases WHERE owner = ?", (self.owner,))
            finally:
                ownership, self._ownership_context = self._ownership_context, None
                self._lock_fd = None
                ownership.__exit__(None, None, None)

    async def start(self) -> None:
        if self._ready:
            return
        if self._start_task is None or self._start_task.done():
            self._start_task = asyncio.create_task(self._start())
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        if self._boot_completed:
            # A new boot has a new durable identity. Completed launch requests
            # remain attached to their original terminal record.
            self.service_run_id = None
        self._run_error_type = None
        self._last_tick_failures = ()
        self._stop_requested.clear()
        self._acquire()
        self._shutdown_failures = []
        self._shutdown_task = None
        self._old_gate = self.execution.blocks_increase
        if self.public_feed is not None:
            self.execution.blocks_increase = self._blocks_increase
        try:
            if self.database.execute("SELECT 1 FROM portfolios WHERE mode != ? LIMIT 1",
                                     (self.service_mode,)).fetchone():
                # Execution and the simulated broker currently share database-wide
                # outboxes/lookups. R1 cannot safely run them against live/replay
                # history, even when that history appears financially complete.
                raise ValidationFailure(
                    f"{self.service_mode} service requires a database containing only {self.service_mode} portfolios")
            rows = self.database.execute(
                "SELECT portfolio_id FROM portfolios WHERE mode = ? ORDER BY created_at, rowid", (self.service_mode,),
            ).fetchall()
            available = {row["portfolio_id"] for row in rows}
            # A database owns one execution outbox. Selecting a newer experiment
            # narrows graph work, never reconciliation/protection for older accounts.
            self.management_portfolio_ids = tuple(row["portfolio_id"] for row in rows)
            if not self.portfolio_ids:
                self.portfolio_ids = tuple(row["portfolio_id"] for row in rows)
            if not self.portfolio_ids or any(pid not in available for pid in self.portfolio_ids):
                raise ValidationFailure(f"persisted {self.service_mode} portfolios are required")
            self._started = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat())
            if self.recover_commands:
                await self._offload(self.recover_commands)
            # No graph handler may run before uncertainty and owner pauses recover.
            await self._offload(self._management)
            if self.prepare_runtime:
                self.handlers = dict(await self._offload(self.prepare_runtime))
            self.service_run_id = await self._offload(
                lambda: service_started(self.database, self.clock, self.portfolio_ids[0], self.service_mode,
                                        self.service_run_id, ai_available=bool(self.handlers) and
                                        (self.runtime_ready is None or self.runtime_ready())))
            self._ready = True
        except BaseException as exc:
            await self._offload(service_finished, self.database, self.clock, self.service_run_id, type(exc).__name__)
            self._started = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                self._heartbeat_task = None
            pending = tuple(self._pending_operations)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if self.execution.blocks_increase == self._blocks_increase:
                self.execution.blocks_increase = self._old_gate
            self._release()
            raise

    def _thread_call(self, callback: Callable, *args):
        with self.database.thread_connection():
            return _call(callback, *args)

    async def _offload(self, callback: Callable, *args):
        task = asyncio.create_task(asyncio.to_thread(self._thread_call, callback, *args))
        self._pending_operations.add(task)
        task.add_done_callback(self._pending_operations.discard)
        return await asyncio.shield(task)

    def _heartbeat_once(self) -> None:
        with self.database.immediate():
            for name in ("paper-service", "role-worker"):
                row = self.database.execute(
                    "SELECT owner FROM process_leases WHERE lease_name = ?", (name,),
                ).fetchone()
                if row is None or row["owner"] != self.owner:
                    raise StaleState("service process lease was replaced")
                self.scheduler.acquire_process_lease(name, self.owner, self.role_ttl_seconds)
            if self._ready and service_heartbeat(self.database, self.clock, self.service_run_id):
                self.request_stop()
            with self._lease_lock:
                lease = self._active_lease
                if lease:
                    row = self.database.execute("SELECT * FROM tasks WHERE task_id = ?", (lease.task_id,)).fetchone()
                    if row["status"] in {"LEASED", "RUNNING"}:
                        self.scheduler.renew(lease, self.role_ttl_seconds)

    async def _heartbeat(self) -> None:
        try:
            while self._started:
                await self._offload(self._heartbeat_once)
                await asyncio.sleep(min(self.role_ttl_seconds / 3, 5))
        except Exception:
            self.request_stop()
            raise

    def _reconcile(self) -> None:
        with self._execution_lock:
            asyncio.run(self.execution.reconcile())

    def _management(self) -> tuple[dict[str, str], tuple[str, ...]]:
        management, failures = {}, []
        with self._execution_lock:
            try:
                asyncio.run(self.execution.reconcile())
            except Exception as exc:
                failures.append("reconciliation:" + type(exc).__name__)
            for pid in self.management_portfolio_ids:
                try:
                    management[pid] = asyncio.run(self.execution.advance_pause(pid))
                except Exception as exc:
                    failures.append("pause:" + type(exc).__name__)
            # An unavailable reconciliation cannot authorize fresh submission.
            if not failures:
                try:
                    asyncio.run(self.execution.dispatch())
                except Exception as exc:
                    failures.append("dispatch:" + type(exc).__name__)
        return management, tuple(failures)

    def _ai_paused(self) -> bool:
        if self.subscription_provider is not None and self.runtime_ready is not None:
            # The admitted router owns compatible route/quota selection. A
            # paused primary route must not disable a verified fallback.
            return not self.runtime_ready()
        if self.subscription_provider is not None:
            return bool(self.database.execute(
                "SELECT 1 FROM subscription_provider_state WHERE provider=? AND ai_paused=1",
                (self.subscription_provider,)).fetchone())
        return bool(self.database.execute(
            "SELECT 1 FROM subscription_provider_state WHERE ai_paused = 1 LIMIT 1").fetchone())

    def _schedules(self) -> int:
        if self.artifact_runtime:
            self.artifact_runtime.maintain(reconcile=self._reconcile, consumer_id=self.owner)
        if self._ai_paused() or not self.automatic_schedule:
            for pid in self.portfolio_ids:
                self.secretary.process(pid, route=False)
            return 0
        created = 0
        allocation = (Decimal("0") if self.subscription_provider is not None
                      else self.execution.authority.active_policy().root_paid_limit.amount)
        if self.runtime_ready and not self.runtime_ready():
            for pid in self.portfolio_ids:
                self.secretary.process(pid, route=False)
            return 0
        for pid in self.portfolio_ids:
            profile = self.execution.profile(pid)
            pause = self.execution.pause(pid)
            own_leader_pause = bool(pause and pause["originator"] == "leader")
            # Secretary remains software even when discretionary tasks are paused.
            self.secretary.process(
                pid, route=(profile == "RUNNING" or own_leader_pause) and "leader" in self.handlers,
            )
            if profile in PAUSED_WORK and not own_leader_pause:
                continue
            settings = {}
            bundle = None
            if self.artifact_runtime:
                bundle = self.artifact_runtime.versions.load_active(pid)
                settings = {role: interval for role, interval in self.artifact_runtime.schedule_settings(bundle).items()
                            if role not in self.artifact_runtime.disabled_schedule_roles}
                self.artifact_runtime.apply_schedules(pid, bundle)
            for role, interval in {**self.schedule_intervals, **settings}.items():
                if role not in self.handlers:
                    continue
                if profile in PAUSED_WORK and role != "leader":
                    continue
                name = f"artifact-{role}-review" if role in settings else f"{role}-review"
                if role not in settings:
                    self.scheduler.ensure_schedule(pid, name, interval, "coalesce")
                with self.database.immediate():
                    active = self.database.execute(
                        """SELECT task_id FROM tasks WHERE portfolio_id = ? AND role = ?
                        AND objective = ? AND status IN ('QUEUED', 'LEASED', 'RUNNING') LIMIT 1""",
                        (pid, role, name),
                    ).fetchone()
                    if active:
                        self.database.execute(
                            """UPDATE schedules SET last_due_at = ?, next_due_at = ?
                            WHERE portfolio_id = ? AND name = ? AND next_due_at <= ?""",
                            (self.scheduler.now(), utc_iso(self.clock.now() + timedelta(
                                seconds=settings.get(role, interval))), pid, name, self.scheduler.now()),
                        )
                        continue
                    if role == "leader":
                        task_id = self.secretary.scheduled(pid, interval)
                    elif role in settings:
                        task_id = self.artifact_runtime.coalesce_due(
                            pid, role, allocated_spend=allocation,
                        )
                    else:
                        task_id = self.scheduler.coalesce_due(pid, name, role)
                        if task_id:
                            self.scheduler.allocate(
                                task_id, allocation,
                            )
                    if task_id:
                        self.database.execute(
                            "UPDATE tasks SET max_attempts = ?, expected_version = ? WHERE task_id = ?",
                            (self.execution.authority.active_policy().ordinary_max_paid_attempts,
                             bundle["artifact_hash"] if bundle else None, task_id),
                        )
                    created += bool(task_id)
        return created

    def _subscription_availability_failure(self, task_id: str) -> bool:
        """A known provider stop pauses AI without inventing a financial failure."""
        if self.subscription_provider is None:
            return False
        row = self.database.execute("SELECT invocation_id,provider,state,result_json FROM subscription_invocations "
                                    "WHERE task_id=?", (task_id,)).fetchone()
        if row is None or row["state"] != "FAILED" or not row["result_json"]:
            return False
        result = json.loads(row["result_json"])
        expected = {"unsupported": "subscription model unavailable", "rate_limit": "subscription quota exhausted"}
        reason = expected.get(result.get("failure"))
        if result.get("ok") is not False or reason is None:
            return False
        attempt = self.database.execute("SELECT provider FROM subscription_attempts WHERE invocation_id=? "
                                        "ORDER BY attempt_index DESC LIMIT 1", (row["invocation_id"],)).fetchone()
        provider = attempt["provider"] if attempt else row["provider"]
        state = self.database.execute("SELECT ai_paused,reason FROM subscription_provider_state WHERE provider=?",
                                      (provider,)).fetchone()
        return bool(state and state["ai_paused"] and state["reason"] == reason)

    def _run_role(self) -> int:
        # Run one task at a time; recurring service ticks continue departmental work.
        if self._ai_paused() or (self.runtime_ready and not self.runtime_ready()):
            return 0
        lease = self.scheduler.claim(self.owner, ttl_seconds=self.role_ttl_seconds, roles=set(self.handlers))
        if lease is None:
            return 0
        row = self.scheduler.leased_row(lease)
        if row["deadline_at"] and row["deadline_at"] <= self.scheduler.now():
            self.scheduler.skip(lease, {"reason": "Bounded task deadline expired; no new inference"})
            return 0
        if row["role"] == "optimisation" and self.optimisation_manual_only:
            requested = self.database.execute("""SELECT 1 FROM service_control_requests
                WHERE action = 'start_optimisation'
                AND portfolio_id = ? AND json_extract(result_json, '$.task_id') = ?""",
                (row["portfolio_id"], row["task_id"])).fetchone()
            if not requested:
                self.scheduler.skip(lease, {"reason": "Optimisation requires a persisted owner request"})
                return 0
        if row["portfolio_id"] not in self.portfolio_ids:
            self.scheduler.defer(lease, {"reason": "portfolio is outside service scope"}, 30)
            return 0
        pause = self.execution.pause(row["portfolio_id"])
        if self.execution.profile(row["portfolio_id"]) in PAUSED_WORK and not (
            row["role"] == "leader" and pause and pause["originator"] == "leader"
        ):
            self.scheduler.defer(lease, {"reason": "discretionary work paused"}, 30)
            return 0
        with self._lease_lock:
            self._active_lease = lease
        try:
            self.worker._run_lease(lease, self.handlers)
        except Exception as exc:
            # Provider gateways retain their own receipt/reservation state. A
            # software exception is a failed task, never a fabricated Trader hold.
            try:
                with self.database.immediate():
                    self.scheduler.finish(lease, {"_status": "FAILED", "reason": type(exc).__name__}, "FAILED")
                    self.execution.ledger._activity(row["portfolio_id"], "role_failed", {
                        "task_id": lease.task_id, "role": row["role"], "error_type": type(exc).__name__,
                    })
            except StaleState:
                pass  # Another durable terminal result or cancellation won.
        finally:
            with self._lease_lock:
                self._active_lease = None
        with self.database.immediate():
            result = self.database.execute("SELECT status,output_json FROM tasks WHERE task_id = ?",
                                           (lease.task_id,)).fetchone()
            if result["status"] == "QUEUED":
                if json.loads(result["output_json"] or "{}").get("_subscription_quota_deferred"):
                    self.execution.ledger._activity(row["portfolio_id"], "subscription_quota_deferred", {
                        "task_id": lease.task_id, "role": row["role"], "inference_attempts": 0})
                    return 0
            availability_stop = (result["status"] == "FAILED"
                                 and self._subscription_availability_failure(lease.task_id))
            if availability_stop:
                self.execution.ledger._activity(row["portfolio_id"], "subscription_ai_paused", {
                    "task_id": lease.task_id, "role": row["role"], "financial_management_continues": True})
            if result["status"] == "BLOCKED_BUDGET" or (
                row["role"] == "trader" and result["status"] in {"FAILED", "WAITING_EXTERNAL", "DEAD_LETTER"}
                and not availability_stop
            ):
                pause = self.execution.pause(row["portfolio_id"])
                if not pause or pause["profile"] == "RUNNING":
                    profile = (self.execution.authority.active_policy().budget_exhaustion_profile
                               if result["status"] == "BLOCKED_BUDGET" else "MANAGE_ONLY")
                    self.execution.set_pause(
                        row["portfolio_id"], profile, "system", "role unavailable; manage positions",
                    )
        self._completed_total += 1
        return 1

    def _ingest(self, observations: list) -> int:
        with self._execution_lock:
            for observation in observations:
                if observation.event_time_utc > self.clock.now() or observation.available_at_utc > self.clock.now():
                    raise ValidationFailure("public feed returned a future observation")
                # Adapter sequence IDs restart at zero. Preserve uniqueness across boots.
                observation = observation.model_copy(update={
                    "observation_id": self.owner + ":" + observation.observation_id,
                })
                self.execution.on_observation(observation)
                self._observations_total += 1
        return len(observations)

    async def _wait_for_work(self, task: asyncio.Task, *, maintenance_errors: list[Exception] | None = None):
        """Drain blocking work while keeping financial maintenance alive."""
        maintenance_error = None
        while not task.done():
            try:
                await self._offload(self._management)
            except Exception as exc:
                # A failed storage connection cannot cancel an external attempt
                # or release its ownership. Retain the error and keep draining;
                # later maintenance attempts may recover position management.
                if maintenance_error is None:
                    maintenance_error = exc
                self.request_stop()
            await asyncio.wait({task}, timeout=self.tick_interval_seconds)
        if maintenance_error is not None and maintenance_errors is not None:
            maintenance_errors.append(maintenance_error)
        try:
            result = task.result()
        except Exception as exc:
            if maintenance_error is not None and maintenance_errors is None:
                maintenance_error.add_note("Additional work error: " + type(exc).__name__)
                raise maintenance_error from exc
            raise
        if maintenance_error is not None and maintenance_errors is None:
            raise maintenance_error
        return result

    async def tick(self, *, wait_roles: bool = False, wait_feed: bool = False) -> TickResult:
        await self.start()
        async with self._tick_lock:
            return await self._tick(wait_roles=wait_roles, wait_feed=wait_feed)

    def _record_feed_health(self, error_type: str | None, stage: str) -> None:
        """Keep first, recurring and recovered evidence without raw transport data."""
        now = self.clock.now()
        symbol = None
        if error_type is not None:
            context = getattr(self.public_feed, "diagnostic_context", None)
            if stage == "poll" and isinstance(context, dict):
                if context.get("stage") in {"metadata", "ticker", "reference_fx"}:
                    stage = context["stage"]
                if context.get("symbol") in {"BTC/USD", "ETH/USD", "BTC/EUR", "ETH/EUR"}:
                    symbol = context["symbol"]
            self._feed_failure_count += 1
            if self._feed_first_failed_at is None:
                self._feed_first_failed_at = now
            failure = (stage, symbol, error_type)
            if (failure == self._feed_last_failure and self._feed_last_diagnostic_at is not None
                    and now < self._feed_last_diagnostic_at + timedelta(seconds=30)):
                return
            self._feed_last_failure = failure
        elif not self._feed_failure_count:
            return
        payload = {"run_id": self.service_run_id, "status": "degraded" if error_type else "recovered",
            "stage": stage, "symbol": symbol, "error_type": error_type,
            "consecutive_failures": self._feed_failure_count,
            "first_failed_at_utc": utc_iso(self._feed_first_failed_at), "observed_at_utc": utc_iso(now),
            "financial_management_continues": True, "feed_blocks_increase": error_type is not None}
        with self.database.immediate():
            for pid in self.portfolio_ids:
                self.execution.ledger._activity(pid, "public_feed_health", payload)
        self._feed_last_diagnostic_at = now
        if error_type is None:
            self._feed_failure_count = 0
            self._feed_first_failed_at = None
            self._feed_last_failure = None

    async def _consume_feed(self, task: asyncio.Task, *, maintenance_errors: list[Exception]) -> tuple[int, str | None]:
        try:
            emitted = await self._wait_for_work(task, maintenance_errors=maintenance_errors)
        except Exception as exc:
            self._feed_failed = True
            await self._offload(self._record_feed_health, type(exc).__name__, "poll")
            return 0, "feed:" + type(exc).__name__
        try:
            observations = await self._offload(self._ingest, emitted)
        except ValidationFailure as exc:
            self._feed_failed = True
            await self._offload(self._record_feed_health, type(exc).__name__, "ingest")
            return 0, "feed:" + type(exc).__name__
        except Exception as exc:
            self._feed_failed = True
            await self._offload(self._record_feed_health, type(exc).__name__, "ingest")
            raise
        self._feed_failed = False
        await self._offload(self._record_feed_health, None, "poll")
        return observations, None

    async def _prepare_role_inputs(self) -> tuple[int, tuple[str, ...]]:
        """Runtime-specific acquisitions required before capturing new inputs."""
        return 0, ()

    async def _tick(self, *, wait_roles: bool, wait_feed: bool) -> TickResult:
        if self._heartbeat_task and self._heartbeat_task.done():
            self._heartbeat_task.result()
        # Explicit ticks renew immediately as well as through the wall-clock heartbeat.
        await self._offload(self._heartbeat_once)
        completed, observations, failures = 0, 0, []
        if self._role_task and self._role_task.done():
            completed += self._role_task.result()
            self._role_task = None
        if self.public_feed and not self._stop_requested.is_set():
            if self._feed_task is None:
                self._feed_task = asyncio.create_task(asyncio.to_thread(self._thread_call, self.public_feed.poll))
            if wait_feed or self._feed_task.done():
                maintenance_errors: list[Exception] = []
                try:
                    observations, failure = await self._consume_feed(
                        self._feed_task, maintenance_errors=maintenance_errors,
                    )
                    if failure:
                        failures.append(failure)
                except Exception as exc:
                    if maintenance_errors:
                        maintenance_errors[0].add_note("Additional ingestion error: " + type(exc).__name__)
                        raise maintenance_errors[0] from exc
                    raise
                finally:
                    self._feed_task = None
                if maintenance_errors:
                    raise maintenance_errors[0]
        management, management_failures = await self._offload(self._management)
        failures.extend(management_failures)
        scheduled = 0
        if not self._stop_requested.is_set():
            acquired, input_failures = await self._prepare_role_inputs()
            observations += acquired
            failures.extend(input_failures)
            try:
                scheduled = await self._offload(self._schedules)
            except Exception as exc:
                failures.append("scheduler:" + type(exc).__name__)
            if self._role_task is None and not management_failures:
                self._role_task = asyncio.create_task(asyncio.to_thread(self._thread_call, self._run_role))
                if wait_roles:
                    completed += await self._wait_for_work(self._role_task)
                    self._role_task = None
        from trade_graph.kernel.operational_diagnostics import emit_event, sample_resources

        sample_resources()
        if tuple(failures) != self._last_tick_failures:
            emit_event("service_degraded" if failures else "service_recovered", failures=len(failures))
            with self.database.immediate():
                for pid in self.portfolio_ids:
                    self.execution.ledger._activity(pid, "service_degraded" if failures else "service_recovered",
                                                    {"run_id": self.service_run_id, "failures": failures})
                self.database.execute("UPDATE graph_service_runs SET error_type = ? WHERE run_id = ?",
                                      (";".join(failures) if failures else None, self.service_run_id))
            self._last_tick_failures = tuple(failures)
        return TickResult(observations, scheduled, completed, self._role_task is not None,
                          management, tuple(failures))

    async def run(self, *, max_ticks: int | None = None, install_signal_handlers: bool = True) -> dict:
        if max_ticks is not None:
            Scheduler._positive(max_ticks, "max_ticks")
        loop, installed, previous = asyncio.get_running_loop(), [], {}
        if install_signal_handlers and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                loop.add_signal_handler(signum, self.request_stop)
                installed.append(signum)
        summary = {"ticks": 0, "observations": 0, "scheduled": 0, "completed": 0,
                   "failures": [], "stopped": False, "management": {}}
        initially_completed = self._completed_total
        initially_observed = self._observations_total
        try:
            await self.start()
            while not self._stop_requested.is_set() and (max_ticks is None or summary["ticks"] < max_ticks):
                result = await self.tick(wait_feed=max_ticks is not None and summary["ticks"] + 1 == max_ticks)
                summary["ticks"] += 1
                summary["observations"] += result.observations
                summary["scheduled"] += result.scheduled
                summary["management"] = result.management
                summary["failures"] = list(dict.fromkeys([*summary["failures"], *result.failures]))[:20]
                if max_ticks is not None and summary["ticks"] >= max_ticks:
                    break
                try:
                    await asyncio.wait_for(self._stop_requested.wait(), self.tick_interval_seconds)
                except TimeoutError:
                    pass
        except BaseException as exc:
            self._run_error_type = type(exc).__name__
            raise
        finally:
            try:
                await self.stop()
            finally:
                for signum in installed:
                    loop.remove_signal_handler(signum)
                    signal.signal(signum, previous[signum])
        summary["completed"] = self._completed_total - initially_completed
        summary["observations"] = self._observations_total - initially_observed
        summary["failures"] = list(dict.fromkeys([*summary["failures"], *self._shutdown_failures]))[:20]
        summary["stopped"] = True
        return summary

    async def stop(self) -> None:
        if not self._started and self._lock_fd is None:
            return
        self.request_stop()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                # Cancellation belongs to the caller. It cannot release a fence
                # around a still-running synchronous provider or feed thread.
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        self._stopping = True
        errors: list[Exception] = []
        try:
            if self._start_task and not self._start_task.done():
                await asyncio.gather(self._start_task, return_exceptions=True)
            # A cancelled caller may have left a short transaction/scheduler job
            # running. Those jobs also finish before process ownership is released.
            async with self._tick_lock:
                pending = tuple(self._pending_operations)
                if pending:
                    results = await asyncio.gather(*pending, return_exceptions=True)
                    errors.extend(result for result in results if isinstance(result, Exception))
            # Keep reconciliation and task-lease renewal running while any paid
            # call drains. Releasing the flock early would allow duplicate work.
            if self._role_task:
                try:
                    await self._wait_for_work(self._role_task, maintenance_errors=errors)
                except Exception as exc:
                    # A failed claim/storage operation must not abandon another
                    # still-running feed thread before it releases ownership.
                    errors.append(exc)
                self._role_task = None
            if self._feed_task:
                try:
                    _, failure = await self._consume_feed(self._feed_task, maintenance_errors=errors)
                    if failure:
                        self._shutdown_failures = list(dict.fromkeys([*self._shutdown_failures, failure]))[:20]
                except Exception as exc:
                    errors.append(exc)
                self._feed_task = None
            close = getattr(self.public_feed, "close", None)
            if close:
                close_task = asyncio.create_task(asyncio.to_thread(self._thread_call, close))
                try:
                    await self._wait_for_work(close_task, maintenance_errors=errors)
                except Exception as exc:
                    errors.append(exc)
            if self._ready:
                try:
                    await self._offload(self._management)
                except Exception as exc:
                    errors.append(exc)
        finally:
            self._started = False
            self._ready = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                results = await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                errors.extend(result for result in results if isinstance(result, Exception))
                self._heartbeat_task = None
            # Cancelling the heartbeat leaves its shielded storage operation
            # alive. Drain that final operation before dropping the OS fence.
            pending = tuple(self._pending_operations)
            if pending:
                results = await asyncio.gather(*pending, return_exceptions=True)
                errors.extend(result for result in results if isinstance(result, Exception))
            if self.execution.blocks_increase == self._blocks_increase:
                self.execution.blocks_increase = self._old_gate
            try:
                try:
                    service_finished(self.database, self.clock, self.service_run_id,
                                     type(errors[0]).__name__ if errors else self._run_error_type)
                finally:
                    self._release()
            except Exception as exc:
                errors.append(exc)
            self._stopping = False
            self._boot_completed = True
        if errors:
            for error in errors[1:]:
                errors[0].add_note("Additional shutdown error: " + type(error).__name__)
            raise errors[0]
