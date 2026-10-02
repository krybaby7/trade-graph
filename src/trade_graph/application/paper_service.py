"""Single-host paper controller; financial maintenance outlives role failures.

Synchronous role/provider work and public-feed polling use separate threads. The
controller owns a real OS flock, not just an expiring database lease, throughout
their lifetime. Nothing here constructs a network transport or a paid provider.
"""

from __future__ import annotations

import asyncio
import fcntl
import inspect
import math
import os
import signal
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.scheduler import Scheduler, TaskLease
from trade_graph.application.secretary import Secretary
from trade_graph.application.worker import RoleWorker
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure

DEFAULT_SCHEDULES = {
    "trader": 14400,
    "research": 86400,
    "learning": 302400,
    "optimisation": 604800,
    "leader": 604800,
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
        system_version_id: str = "paper-runtime",
    ) -> None:
        if execution.mode != "paper" or execution.database is not database:
            raise ValidationFailure("paper execution bound to the service database is required")
        if (isinstance(tick_interval_seconds, bool) or not isinstance(tick_interval_seconds, (int, float))
                or not math.isfinite(tick_interval_seconds) or tick_interval_seconds <= 0):
            raise ValidationFailure("positive finite service tick interval required")
        Scheduler._positive(role_ttl_seconds, "role_ttl_seconds")
        self.database, self.execution = database, execution
        self.clock = clock or execution.clock
        self.scheduler = artifact_runtime.scheduler if artifact_runtime else Scheduler(database, self.clock)
        if self.scheduler.database is not database:
            raise ValidationFailure("artifact runtime must use the service database")
        self.secretary = secretary or Secretary(execution, self.scheduler, artifact_runtime=artifact_runtime)
        self.handlers = dict(handlers or {})
        self.artifact_runtime, self.public_feed = artifact_runtime, public_feed
        self.portfolio_ids = tuple(dict.fromkeys(portfolio_ids or ()))
        self.schedule_intervals = dict(DEFAULT_SCHEDULES if schedule_intervals is None else schedule_intervals)
        for role, interval in self.schedule_intervals.items():
            if role not in DEFAULT_SCHEDULES:
                raise ValidationFailure("Engineer work must be explicitly commissioned")
            Scheduler._positive(interval, f"{role} interval")
        self.tick_interval_seconds, self.role_ttl_seconds = tick_interval_seconds, role_ttl_seconds
        self.recover_commands, self.system_version_id = recover_commands, system_version_id
        self.owner = "paper-service-" + uuid.uuid4().hex
        self.worker = RoleWorker(self.scheduler, owner=self.owner, system_version_id=system_version_id,
                                 reconcile=self._reconcile, artifact_runtime=artifact_runtime)
        self._lock_fd: int | None = None
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
        self._lease_lock = threading.Lock()
        self._execution_lock = threading.RLock()
        self._feed_failed = public_feed is not None
        self._old_gate = None

    def _blocks_increase(self, symbol: str) -> bool:
        gate = getattr(self.public_feed, "blocks_increase", None)
        return (self._feed_failed or (bool(gate(symbol)) if gate else False)
                or (bool(self._old_gate(symbol)) if self._old_gate else False))

    def request_stop(self) -> None:
        """Signal a graceful drain; current paid attempts are never cancelled/replayed."""
        self._stop_requested.set()

    def _acquire(self) -> None:
        path = self.database.path.resolve(strict=True)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise StaleState("another paper service owns this database") from exc
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
                os.close(self._lock_fd)
                self._lock_fd = None

    async def start(self) -> None:
        if self._ready:
            return
        if self._start_task is None or self._start_task.done():
            self._start_task = asyncio.create_task(self._start())
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        self._stop_requested.clear()
        self._acquire()
        self._shutdown_task = None
        self._old_gate = self.execution.blocks_increase
        if self.public_feed is not None:
            self.execution.blocks_increase = self._blocks_increase
        try:
            rows = self.database.execute(
                "SELECT portfolio_id FROM portfolios WHERE mode = 'paper' ORDER BY created_at, rowid",
            ).fetchall()
            available = {row["portfolio_id"] for row in rows}
            if not self.portfolio_ids:
                self.portfolio_ids = tuple(row["portfolio_id"] for row in rows)
            if not self.portfolio_ids or any(pid not in available for pid in self.portfolio_ids):
                raise ValidationFailure("persisted paper portfolios are required")
            self._started = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat())
            if self.recover_commands:
                await self._offload(self.recover_commands)
            # No graph handler may run before uncertainty and owner pauses recover.
            await self._offload(self._management)
            self._ready = True
        except BaseException:
            self._started = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                self._heartbeat_task = None
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
            for pid in self.portfolio_ids:
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

    def _schedules(self) -> int:
        created = 0
        if self.artifact_runtime:
            self.artifact_runtime.maintain(reconcile=self._reconcile, consumer_id=self.owner)
        for pid in self.portfolio_ids:
            profile = self.execution.profile(pid)
            # Secretary remains software even when discretionary tasks are paused.
            self.secretary.process(pid, route=profile == "RUNNING" and "leader" in self.handlers)
            if profile in PAUSED_WORK:
                continue
            settings = {}
            bundle = None
            if self.artifact_runtime:
                bundle = self.artifact_runtime.versions.load_active(pid)
                settings = self.artifact_runtime.schedule_settings(bundle)
                self.artifact_runtime.apply_schedules(pid, bundle)
            for role, interval in self.schedule_intervals.items():
                if role not in self.handlers:
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
                            pid, role, allocated_spend=self.execution.authority.active_policy().root_paid_limit.amount,
                        )
                    else:
                        task_id = self.scheduler.coalesce_due(pid, name, role)
                        if task_id:
                            self.scheduler.allocate(
                                task_id, self.execution.authority.active_policy().root_paid_limit.amount,
                            )
                    if task_id:
                        self.database.execute(
                            "UPDATE tasks SET max_attempts = ?, expected_version = ? WHERE task_id = ?",
                            (min(3, self.execution.authority.active_policy().ordinary_max_paid_attempts),
                             bundle["artifact_hash"] if bundle else None, task_id),
                        )
                    created += bool(task_id)
        return created

    def _run_role(self) -> int:
        # One finite task per launch keeps cadence and shutdown under the controller.
        lease = self.scheduler.claim(self.owner, ttl_seconds=self.role_ttl_seconds, roles=set(self.handlers))
        if lease is None:
            return 0
        row = self.scheduler.leased_row(lease)
        if row["portfolio_id"] not in self.portfolio_ids:
            self.scheduler.defer(lease, {"reason": "portfolio is outside service scope"}, 30)
            return 0
        if self.execution.profile(row["portfolio_id"]) in PAUSED_WORK:
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
            result = self.database.execute("SELECT status FROM tasks WHERE task_id = ?", (lease.task_id,)).fetchone()
            if result["status"] == "BLOCKED_BUDGET" or (
                row["role"] == "trader" and result["status"] in {"FAILED", "WAITING_EXTERNAL", "DEAD_LETTER"}
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

    async def _wait_for_work(self, task: asyncio.Task):
        """Drain blocking work while keeping financial maintenance alive."""
        while not task.done():
            await self._offload(self._management)
            await asyncio.wait({task}, timeout=self.tick_interval_seconds)
        return task.result()

    async def tick(self, *, wait_roles: bool = False, wait_feed: bool = False) -> TickResult:
        await self.start()
        async with self._tick_lock:
            return await self._tick(wait_roles=wait_roles, wait_feed=wait_feed)

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
                try:
                    emitted = await self._wait_for_work(self._feed_task)
                    observations = await self._offload(self._ingest, emitted)
                    self._feed_failed = False
                except Exception as exc:
                    self._feed_failed = True
                    failures.append("feed:" + type(exc).__name__)
                self._feed_task = None
        management, management_failures = await self._offload(self._management)
        failures.extend(management_failures)
        scheduled = 0
        if not self._stop_requested.is_set():
            try:
                scheduled = await self._offload(self._schedules)
            except Exception as exc:
                failures.append("scheduler:" + type(exc).__name__)
            if self._role_task is None and not management_failures:
                self._role_task = asyncio.create_task(asyncio.to_thread(self._thread_call, self._run_role))
                if wait_roles:
                    completed += await self._wait_for_work(self._role_task)
                    self._role_task = None
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
        finally:
            await self.stop()
            for signum in installed:
                loop.remove_signal_handler(signum)
                signal.signal(signum, previous[signum])
        summary["completed"] = self._completed_total - initially_completed
        summary["observations"] = self._observations_total - initially_observed
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
        try:
            if self._start_task and not self._start_task.done():
                await asyncio.gather(self._start_task, return_exceptions=True)
            # A cancelled caller may have left a short transaction/scheduler job
            # running. Those jobs also finish before process ownership is released.
            async with self._tick_lock:
                pending = tuple(self._pending_operations)
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            # Keep reconciliation and task-lease renewal running while any paid
            # call drains. Releasing the flock early would allow duplicate work.
            if self._role_task:
                await self._wait_for_work(self._role_task)
                self._role_task = None
            if self._feed_task:
                try:
                    emitted = await self._wait_for_work(self._feed_task)
                    await self._offload(self._ingest, emitted)
                    self._feed_failed = False
                except Exception:
                    self._feed_failed = True
                self._feed_task = None
            close = getattr(self.public_feed, "close", None)
            if close:
                close_task = asyncio.create_task(asyncio.to_thread(self._thread_call, close))
                await self._wait_for_work(close_task)
            if self._ready:
                await self._offload(self._management)
        finally:
            self._started = False
            self._ready = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                await asyncio.gather(self._heartbeat_task, return_exceptions=True)
                self._heartbeat_task = None
            if self.execution.blocks_increase == self._blocks_increase:
                self.execution.blocks_increase = self._old_gate
            self._release()
            self._stopping = False
