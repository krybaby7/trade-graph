"""Owner-controlled single-host service lifecycle; model text supplies no authority.

The control lock serializes launches. The worker owns the database inode flock;
PID birth identity and durable records distinguish interruption from a running
worker. No exchange transport, model provider or credential is created here.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_graph.application.scheduler import Scheduler
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

ACTIVE_SERVICE = {"STARTING", "RUNNING", "MANAGEMENT_ONLY", "STOPPING"}
ACTIVE_TASK = {"QUEUED", "LEASED", "RUNNING", "WAITING_EXTERNAL", "BLOCKED_BUDGET"}


def process_identity(pid: int | None) -> str | None:
    if not pid:
        return None
    try:
        # comm can contain spaces/parentheses; field 22 follows the final ')'.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, IndexError, ValueError):
        return None


def database_owned(database) -> bool:
    fd = os.open(database.path.resolve(strict=True), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def service_started(database, clock, portfolio_id: str, mode: str, run_id: str | None, *, ai_available: bool) -> str:
    run_id = run_id or str(uuid.uuid4())
    now, pid = utc_iso(clock.now()), os.getpid()
    with database.immediate():
        old = database.execute("SELECT * FROM graph_service_runs WHERE run_id = ?", (run_id,)).fetchone()
        if old and (
            old["portfolio_id"] != portfolio_id
            or old["mode"] != mode
            or old["status"] != "STARTING"
            or old["stop_requested"]
        ):
            raise StaleState("service launch record is no longer eligible")
        # Exclusive database ownership proves no preceding controller is active.
        database.execute(
            """UPDATE graph_service_runs SET status = 'INTERRUPTED', finished_at = ?,
            error_type = 'ProcessInterrupted' WHERE status IN ('STARTING', 'RUNNING', 'MANAGEMENT_ONLY', 'STOPPING')
            AND run_id != ?""",
            (now, run_id),
        )
        database.execute(
            """INSERT INTO graph_service_runs
            (run_id, portfolio_id, mode, status, pid, pid_start_ticks, requested_at, heartbeat_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, pid=excluded.pid,
            pid_start_ticks=excluded.pid_start_ticks, heartbeat_at=excluded.heartbeat_at""",
            (
                run_id,
                portfolio_id,
                mode,
                "RUNNING" if ai_available else "MANAGEMENT_ONLY",
                pid,
                process_identity(pid),
                now,
                now,
            ),
        )
    return run_id


def service_heartbeat(database, clock, run_id: str | None) -> bool:
    if run_id is None:
        return False
    with database.immediate():
        row = database.execute("SELECT * FROM graph_service_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None or row["pid"] != os.getpid() or row["pid_start_ticks"] != process_identity(os.getpid()):
            raise StaleState("service lifecycle ownership was replaced")
        database.execute(
            "UPDATE graph_service_runs SET heartbeat_at = ? WHERE run_id = ?", (utc_iso(clock.now()), run_id)
        )
        return bool(row["stop_requested"])


def service_finished(database, clock, run_id: str | None, error_type: str | None = None) -> None:
    if run_id is not None:
        database.execute(
            """UPDATE graph_service_runs SET status = ?, finished_at = ?, error_type = ?
            WHERE run_id = ? AND (pid IS NULL OR (pid = ? AND (pid_start_ticks IS NULL OR pid_start_ticks = ?)))""",
            (
                "FAILED" if error_type else "STOPPED",
                utc_iso(clock.now()),
                error_type,
                run_id,
                os.getpid(),
                process_identity(os.getpid()),
            ),
        )


class ServiceController:
    def __init__(
        self,
        runtime,
        *,
        config_path: Path | None = None,
        protected_owner: Path | None = None,
        launcher=None,
        prerequisites=None,
    ) -> None:
        self.runtime, self.database, self.clock = runtime, runtime.database, runtime.clock
        self.config_path = config_path.absolute() if config_path is not None else None
        self.protected_owner = protected_owner
        self.launcher = launcher or self._launch
        self.prerequisites = prerequisites
        self._children: dict[str, subprocess.Popen] = {}

    @contextmanager
    def _control_lock(self):
        path = self.database.path.resolve().parent / ".service-control.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.fstat(fd).st_mode & 0o077:
                raise ValidationFailure("service control lock must be private")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _launch(self, command):
        # Bind child imports to this installed/source package, not ambient PYTHONPATH.
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        env.pop("OPENAI_API_KEY", None)
        env.pop("ANTHROPIC_API_KEY", None)
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            env=env,
            cwd=str(self.database.path.resolve().parent),
        )

    def _readiness(self) -> dict:
        if self.prerequisites:
            return self.prerequisites()
        from trade_graph.paper_runtime import load_runtime_config

        result = {
            "ai_available": False,
            "paper_available": True,
            "live_available": False,
            "reasons": ["Subscription model routing and isolation require owner provisioning."],
            "live_reasons": ["Live startup requires separately commissioned protected owner configuration."],
        }
        try:
            config = load_runtime_config(self.config_path)
            if config.models and config.models.get("paid_calls_enabled"):
                # The controller never authorizes separately billed API operation.
                result["paper_available"] = False
                result["reasons"] = ["Direct paid API model routing is not authorized for this installation."]
        except (OSError, ValueError):
            result["paper_available"] = False
            result["reasons"] = ["Protected runtime configuration is invalid or unavailable."]
        return result

    def _observed(self, row) -> dict | None:
        if row is None:
            return None
        result = {
            key: row[key]
            for key in (
                "run_id",
                "portfolio_id",
                "mode",
                "status",
                "requested_at",
                "heartbeat_at",
                "finished_at",
                "error_type",
                "stop_requested",
            )
        }
        if row["status"] in ACTIVE_SERVICE:
            identity = process_identity(row["pid"])
            if row["pid"] and (identity is None or identity != row["pid_start_ticks"]):
                result.update(status="INTERRUPTED", error_type="ProcessInterrupted")
            elif not row["pid"]:
                started = datetime.fromisoformat(row["requested_at"].replace("Z", "+00:00"))
                if self.clock.now() - started > timedelta(seconds=30):
                    result.update(status="INTERRUPTED", error_type="LaunchInterrupted")
        return result

    def status(self) -> dict:
        for run_id, child in tuple(self._children.items()):
            if hasattr(child, "poll") and child.poll() is not None:
                self._children.pop(run_id, None)
        row = self.database.execute("SELECT * FROM graph_service_runs ORDER BY rowid DESC LIMIT 1").fetchone()
        observed = self._observed(row)
        owned = database_owned(self.database)
        if owned and (observed is None or observed["status"] not in ACTIVE_SERVICE):
            observed = {
                "run_id": None,
                "mode": getattr(self.runtime.execution, "mode", "paper"),
                "status": "ATTACHED_EXISTING",
                "heartbeat_at": None,
            }
        ready = self._readiness()
        provider_states = self.database.execute(
            "SELECT * FROM subscription_provider_state ORDER BY provider"
        ).fetchall()
        providers = [
            {
                "provider": row["provider"],
                "ai_paused": bool(row["ai_paused"]),
                "reason": row["reason"],
                "quota": json.loads(row["quota_json"]),
                "updated_at": row["updated_at"],
            }
            for row in provider_states
        ]
        selected = ready.get("selected_provider")
        if any(provider["ai_paused"] and (selected is None or provider["provider"] == selected)
               for provider in providers):
            ready = {
                **ready,
                "ai_available": False,
                "reasons": ["Subscription AI work paused; reconciliation and protection continue."],
            }
        unresolved = self.database.execute("""SELECT COUNT(*) FROM subscription_invocations
            WHERE cost_status = 'unknown'
            AND state IN ('DISPATCHED', 'COMPLETED', 'FAILED', 'UNCERTAIN')""").fetchone()[0]
        cycle = self.database.execute(
            """SELECT * FROM tasks WHERE portfolio_id = ?
            AND objective = 'owner-optimisation-cycle' ORDER BY rowid DESC LIMIT 1""",
            (self.runtime.portfolio_id,),
        ).fetchone()
        return {
            "service": observed or {"status": "IDLE", "mode": getattr(self.runtime.execution, "mode", "paper")},
            "database_owned": owned,
            "pause_profile": self.runtime.execution.profile(self.runtime.portfolio_id),
            "prerequisites": ready,
            "optimisation": self._cycle(cycle["task_id"]) if cycle else None,
            "automatic_optimisation": False,
            "ai_usage": {
                "providers": providers,
                "available_quota": "unknown" if not providers else "provider_reported",
                "unknown_usage_or_cost_records": unresolved,
                "subscription_separate_from_operating_budget": True,
            },
            "position_management": "Reconciliation and protection continue during an AI pause. "
            "Stopping the service is allowed only when flat with no outstanding orders.",
        }

    def _cycle(self, task_id: str) -> dict:
        rows = self.database.execute("SELECT * FROM tasks WHERE root_task_id = ? ORDER BY rowid", (task_id,)).fetchall()
        root = next((row for row in rows if row["task_id"] == task_id), None)
        if root is None:
            raise StaleState("saved optimisation task is missing")
        states = {row["status"] for row in rows}
        state = (
            "FAILED"
            if states & {"FAILED", "DEAD_LETTER", "CANCELLED"}
            else "BLOCKED"
            if states & {"BLOCKED_BUDGET", "WAITING_EXTERNAL"}
            else "RUNNING"
            if states & {"LEASED", "RUNNING"}
            else "QUEUED"
            if "QUEUED" in states
            else "SUCCEEDED"
        )
        return {
            "task_id": task_id,
            "status": state,
            "created_at": root["created_at"],
            "deadline_at": root["deadline_at"],
            "bounded_attempts": 1,
            "tasks": [
                {
                    "task_id": row["task_id"],
                    "role": row["role"],
                    "status": row["status"],
                    "result": json.loads(row["output_json"] or "{}"),
                }
                for row in rows
            ],
        }

    def _replay(self, request_id: str, action: str, payload: dict):
        row = self.database.execute(
            "SELECT * FROM service_control_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        if (
            row["action"] != action
            or row["portfolio_id"] != self.runtime.portfolio_id
            or json.loads(row["payload_json"]) != payload
        ):
            raise ValidationFailure("request identity belongs to another command")
        result = json.loads(row["result_json"])
        if action == "start_optimisation":
            result["optimisation"] = self._cycle(result["task_id"])
        result["replayed"] = True
        return result

    def _record(self, request_id: str, action: str, payload: dict, result: dict):
        self.database.execute(
            "INSERT INTO service_control_requests VALUES (?, ?, ?, ?, ?, ?)",
            (
                request_id,
                self.runtime.portfolio_id,
                action,
                json.dumps(payload, sort_keys=True),
                json.dumps(result, sort_keys=True),
                utc_iso(self.clock.now()),
            ),
        )
        return result

    def start_trading(self, request_id: str, mode: str = "paper") -> dict:
        if mode not in {"paper", "live"}:
            raise ValidationFailure("paper or live mode is required")
        payload = {"mode": mode}
        with self._control_lock():
            replay = self._replay(request_id, "start_trading", payload)
            if replay:
                return {**replay, "lifecycle": self.status()}
            ready = self._readiness()
            if not ready.get(mode + "_available"):
                raise AuthorityDenied("; ".join(ready.get("live_reasons" if mode == "live" else "reasons", [])))
            current = self.status()["service"]
            if current["status"] in ACTIVE_SERVICE | {"ATTACHED_EXISTING"}:
                if current["mode"] != mode:
                    raise StaleState("another service mode already owns this database")
                result = {"run_id": current["run_id"], "attached": True}
            else:
                run_id = str(uuid.uuid4())
                with self.database.immediate():
                    self.database.execute(
                        """UPDATE graph_service_runs SET status = 'INTERRUPTED',
                        finished_at = ?, error_type = 'ProcessInterrupted'
                        WHERE status IN ('STARTING', 'RUNNING', 'MANAGEMENT_ONLY', 'STOPPING')""",
                        (utc_iso(self.clock.now()),),
                    )
                    self.database.execute(
                        """INSERT INTO graph_service_runs
                        (run_id, portfolio_id, mode, status, requested_at)
                        VALUES (?, ?, ?, 'STARTING', ?)""",
                        (run_id, self.runtime.portfolio_id, mode, utc_iso(self.clock.now())),
                    )
                command = [
                    sys.executable,
                    "-m",
                    "trade_graph.cli",
                    "run",
                    "--mode",
                    mode,
                    "--database",
                    str(self.database.path.resolve()),
                    "--portfolio-id",
                    self.runtime.portfolio_id,
                    "--service-run-id",
                    run_id,
                ]
                if self.config_path:
                    command += ["--config", str(self.config_path)]
                if self.protected_owner:
                    command += ["--protected-owner", str(self.protected_owner)]
                try:
                    child = self.launcher(command)
                    self._children[run_id] = child
                    self.database.execute(
                        """UPDATE graph_service_runs SET pid = ?, pid_start_ticks = ?
                        WHERE run_id = ? AND status = 'STARTING'""",
                        (child.pid, process_identity(child.pid), run_id),
                    )
                except Exception as exc:
                    self.database.execute(
                        """UPDATE graph_service_runs SET status = 'FAILED',
                        finished_at = ?, error_type = ? WHERE run_id = ?""",
                        (utc_iso(self.clock.now()), type(exc).__name__, run_id),
                    )
                result = {"run_id": run_id, "attached": False}
            with self.database.immediate():
                self._record(request_id, "start_trading", payload, result)
            return {**result, "lifecycle": self.status()}

    def start_optimisation(self, request_id: str) -> dict:
        with self._control_lock(), self.database.immediate():
            replay = self._replay(request_id, "start_optimisation", {})
            if replay:
                return replay
            state = self.status()
            if state["service"]["status"] != "RUNNING" or not state["prerequisites"]["ai_available"]:
                raise AuthorityDenied("Optimisation requires an active isolated subscription model runtime.")
            if self.runtime.execution.profile(self.runtime.portfolio_id) != "RUNNING":
                raise AuthorityDenied("Owner or system pause prevents new AI work.")
            active = self.database.execute(
                """SELECT root_task_id FROM tasks WHERE portfolio_id = ?
                AND status IN ('QUEUED', 'LEASED', 'RUNNING', 'WAITING_EXTERNAL', 'BLOCKED_BUDGET')
                AND root_task_id IN (SELECT task_id FROM tasks WHERE objective = 'owner-optimisation-cycle')
                LIMIT 1""",
                (self.runtime.portfolio_id,),
            ).fetchone()
            if active:
                raise StaleState("An optimisation cycle is already active or unresolved; review its saved status.")
            policy = self.runtime.execution.authority.active_policy()
            scheduler = Scheduler(self.database, self.clock)
            amount = policy.root_paid_limit.amount
            task_id = scheduler.add_task(
                role="optimisation",
                objective="owner-optimisation-cycle",
                portfolio_id=self.runtime.portfolio_id,
                dedup_key="owner-optimisation:" + request_id,
                max_attempts=1,
                deadline_at=utc_iso(self.clock.now() + timedelta(minutes=10)),
                allocated_spend=amount,
                payload={"owner_requested": True, "consultation": True, "followup_budget": str(amount / Decimal(2))},
            )
            result = {"task_id": task_id, "optimisation": self._cycle(task_id)}
            self._record(request_id, "start_optimisation", {}, result)
            return result

    def stop(self, request_id: str, position_policy: str = "manage-only") -> dict:
        if position_policy not in {"manage-only", "flatten"}:
            raise ValidationFailure("Choose manage-only or flatten for existing orders and positions.")
        payload = {"position_policy": position_policy}
        with self._control_lock(), self.database.immediate():
            replay = self._replay(request_id, "stop_service", payload)
            if replay:
                return replay
            from trade_graph.api.controls import _nonflat

            nonflat = _nonflat(self.runtime) or self.runtime.execution._has_outstanding(self.runtime.portfolio_id)
            profile = ("MANAGE_ONLY" if position_policy == "manage-only" else "FLATTEN") if nonflat else "STOPPED"
            from trade_graph.api.controls import Command, _Commands, _revision, _scope

            def effect():
                nonlocal profile
                current = self.runtime.execution.pause(self.runtime.portfolio_id)
                if (
                    nonflat
                    and current
                    and (
                        (current["originator"] == "system" and current["profile"] != "RUNNING")
                        or current["profile"] in {"FLATTEN", "CANCEL_ALL", "STOPPED"}
                    )
                ):
                    profile = current["profile"]
                else:
                    self.runtime.execution.set_pause(
                        self.runtime.portfolio_id, profile, "owner", "Owner service stop request"
                    )
                if not nonflat:
                    self.database.execute("""UPDATE graph_service_runs SET stop_requested = 1,
                        status = 'STOPPING' WHERE status IN ('STARTING', 'RUNNING', 'MANAGEMENT_ONLY')""")
                return {
                    "profile": profile,
                    "service_stop_requested": not bool(nonflat),
                    "management": "Service remains active for reconciliation and position protection."
                    if nonflat
                    else "Flat account: service drains current work and stops. No monitoring continues offline.",
                }

            scope = _scope(self.runtime, "owner")
            command = Command(
                request_id=hashlib.sha256(("service-stop:" + request_id).encode()).hexdigest(),
                expected_revision=_revision(self.runtime, scope),
            )
            # A newer stop fences a still-running resume/pause command. The local
            # emergency latch remains available during an interrupted owner command.
            result = _Commands(self.runtime).mutate(scope, command, "service-stop", effect, allow_processing=True)
            return self._record(request_id, "stop_service", payload, result)
