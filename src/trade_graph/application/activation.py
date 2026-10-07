"""Deterministic artifact loading, generation fencing, observation and safe rollback."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from trade_graph.adapters.engineering.artifact_files import content_hash, manifest
from trade_graph.adapters.engineering.provenance import checks_module_hash
from trade_graph.adapters.engineering.sandbox import CPU_SECONDS, INPUT_BYTES, MEMORY_BYTES, OUTPUT_BYTES, WALL_SECONDS
from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.application.artifact_store import ArtifactStore
from trade_graph.application.change_authority import authorized_change
from trade_graph.contracts.models import ChangeResult
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure


class ObservationPolicy(BaseModel):
    """Software-pinned functional health gates, never selected by candidate prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    min_decisions: int = Field(default=3, ge=1, le=1000)
    max_failures: int = Field(default=0, ge=0, le=10)
    horizon_seconds: int = Field(default=3600, ge=1, le=604800)
    reload_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_context_bytes: int = Field(default=262144, ge=1024, le=1048576)


class VersionController:
    def __init__(self, database: Database, clock: Clock, *,
                 observation_policy: ObservationPolicy | None = None) -> None:
        self.database, self.clock = database, clock
        self.store = ArtifactStore(database, clock)
        self.observation_policy = observation_policy or ObservationPolicy()

    @atomic
    def ensure(self, portfolio_id: str, version_id: str, artifact_hash: str,
               files: dict[str, str] | None = None) -> None:
        if files is not None and self.store.put(files) != artifact_hash:
            raise ValidationFailure("baseline artifact identity mismatch")
        if self.database.execute("SELECT 1 FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)).fetchone():
            return
        self.database.execute(
            """INSERT INTO active_versions
            (portfolio_id, version_id, artifact_hash, fingerprint_json, activated_at) VALUES (?, ?, ?, ?, ?)""",
            (
                portfolio_id,
                version_id,
                artifact_hash,
                json.dumps({"artifact": artifact_hash}),
                utc_iso(self.clock.now()),
            ),
        )
        self.database.execute("INSERT INTO version_history VALUES (?, ?, ?)", (portfolio_id, version_id, artifact_hash))

    def register_baseline(self, portfolio_id: str, version_id: str, files: dict[str, str]) -> dict:
        self.ensure(portfolio_id, version_id, content_hash(files), files)
        return self.load_active(portfolio_id)

    def current_hash(self, portfolio_id: str) -> str:
        row = self.database.execute(
            "SELECT artifact_hash FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        if row is None:
            raise ValidationFailure("unknown portfolio active version")
        return row["artifact_hash"]

    @atomic
    def activate(self, portfolio_id: str, candidate: dict) -> None:
        stored = self.database.execute(
            """SELECT c.* FROM candidates c JOIN change_tasks t USING (change_id)
            WHERE c.candidate_id = ? AND t.portfolio_id = ?""",
            (candidate.get("candidate_id"), portfolio_id),
        ).fetchone()
        if stored is None:
            raise ValidationFailure("unknown persisted candidate; missing trusted attestation")
        # Caller fields are consistency assertions only, never the source of truth.
        for name in ("content_hash", "baseline_hash"):
            if name in candidate and candidate[name] != stored[name]:
                raise ValidationFailure("candidate content/baseline identity mismatch")
        current = self.current_hash(portfolio_id)
        if current != stored["baseline_hash"]:
            raise StaleState("baseline moved; revalidate")
        if stored["state"] not in {"READY", "APPROVED"}:
            raise ValidationFailure("candidate lifecycle is not activatable")
        task, change, commission = authorized_change(self.database, self.clock, portfolio_id, stored["change_id"])
        if change["state"] not in {"READY", "APPROVED"}:
            raise ValidationFailure("change lifecycle is not activatable")
        result = ChangeResult.model_validate_json(stored["document_json"])
        proof = self.database.execute(
            "SELECT * FROM candidate_attestations WHERE attestation_id = ?", (result.attestation_id,)
        ).fetchone()
        expected = {
            "candidate_id": stored["candidate_id"],
            "portfolio_id": portfolio_id,
            "change_id": task.record_id,
            "decision_id": commission["decision_id"],
            "task_hash": commission["task_hash"],
            "content_hash": stored["content_hash"],
            "baseline_hash": task.baseline_hash,
            "checks_module_hash": checks_module_hash(),
            "exit_code": 0,
        }
        if (
            proof is None
            or any(proof[k] != v for k, v in expected.items())
            or result.candidate_id != stored["candidate_id"]
            or result.record_id != stored["candidate_id"]
            or result.change_id != task.record_id
            or result.portfolio_id != portfolio_id
            or result.content_hash != stored["content_hash"]
            or result.task_id != task.task_id
            or result.root_task_id != task.root_task_id
            or result.state != "READY"
        ):
            raise ValidationFailure("missing candidate-bound trusted attestation")
        # Full bytes and the unambiguous manifest are retained independently of
        # the mutable worktree. An old pointer alone is not a rollback baseline.
        self.store.get(current)
        try:
            report = json.loads(proof["report_json"])
            files = report["artifact_files"]
            self.store.check(files)
            raw = json.dumps({"files": files}, sort_keys=True, ensure_ascii=False).encode()
            checked = json.loads(report["stdout"])
            isolation = report["isolation"]
            if (content_hash(files) != stored["content_hash"] or manifest(files) != report["manifest"]
                    or report["content_hash"] != stored["content_hash"]
                    or report["checks_module_hash"] != checks_module_hash() or report["exit_code"] != 0
                    or not checked["passed"] or checked["failures"]
                    or checked["input_sha256"] != hashlib.sha256(raw).hexdigest()
                    or not isolation["verified"] or isolation["uid"] == 0
                    or isolation["mechanism"] != "linux-x86_64-seccomp-data-pipe-v1"
                    or isolation["filesystem"] != "denied" or isolation["network"] != "denied"
                    or isolation["process_creation"] != "denied" or not isolation["no_new_privs"]
                    or isolation["memory_bytes"] != MEMORY_BYTES or isolation["cpu_seconds"] != CPU_SECONDS
                    or isolation["wall_seconds"] != WALL_SECONDS or isolation["input_bytes"] != INPUT_BYTES
                    or isolation["output_bytes"] != OUTPUT_BYTES
                    or checked["isolation"] != {k: v for k, v in isolation.items() if k != "verified"}):
                raise ValidationFailure("tested artifact manifest/confinement identity mismatch")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ValidationFailure("invalid tested artifact bytes/manifest") from exc
        self.store.put(files)
        self._quiescent(portfolio_id)
        previous_row = self._active(portfolio_id)
        previous, generation = previous_row["version_id"], previous_row["generation"] + 1
        changed = self.database.execute(
            """UPDATE active_versions SET version_id = ?, artifact_hash = ?,
            fingerprint_json = ?, activated_at = ?, generation = ?
            WHERE portfolio_id = ? AND artifact_hash = ? AND generation = ?""",
            (
                stored["candidate_id"],
                stored["content_hash"],
                json.dumps({"artifact": stored["content_hash"], "manifest": manifest(files)["sha256"]}),
                utc_iso(self.clock.now()),
                generation,
                portfolio_id,
                task.baseline_hash,
                previous_row["generation"],
            ),
        )
        if changed.rowcount != 1:
            raise StaleState("compare-and-set failed")
        self.database.execute(
            "INSERT INTO version_history VALUES (?, ?, ?)",
            (portfolio_id, stored["candidate_id"], stored["content_hash"]),
        )
        self._cancel_stale(portfolio_id, stored["content_hash"])
        self.database.execute(
            "UPDATE candidates SET state = 'OBSERVING' WHERE candidate_id = ?", (stored["candidate_id"],)
        )
        self.database.execute("UPDATE change_tasks SET state = 'OBSERVING' WHERE change_id = ?", (task.record_id,))
        self.database.execute(
            """UPDATE version_rollouts SET state = 'SUPERSEDED' WHERE portfolio_id = ?
            AND state IN ('RESTART_PENDING', 'OBSERVING', 'ACTIVE', 'RESTORED')""", (portfolio_id,),
        )
        self.database.execute("UPDATE candidates SET state = 'SUPERSEDED' WHERE candidate_id = ?", (previous,))
        self.database.execute("UPDATE change_tasks SET state = 'SUPERSEDED' WHERE change_id = "
                              "(SELECT change_id FROM candidates WHERE candidate_id = ?)", (previous,))
        self.database.execute(
            "INSERT INTO version_rollouts VALUES (?, ?, ?, ?, ?, ?, ?, 'RESTART_PENDING', ?, ?)",
            (str(uuid.uuid4()), portfolio_id, stored["candidate_id"], generation,
             stored["content_hash"], current, previous, self.observation_policy.model_dump_json(),
             utc_iso(self.clock.now())),
        )
        self._event(
            portfolio_id,
            "activate",
            current,
            stored["content_hash"],
            {"candidate_id": stored["candidate_id"], "previous_version_id": previous, "generation": generation},
        )

    def fingerprint(self, portfolio_id: str) -> str:
        return self.current_hash(portfolio_id)

    def _active(self, portfolio_id: str):
        row = self.database.execute(
            "SELECT * FROM active_versions WHERE portfolio_id = ?", (portfolio_id,),
        ).fetchone()
        if row is None:
            raise ValidationFailure("unknown portfolio active version")
        return row

    def _rollout(self, portfolio_id: str):
        active = self._active(portfolio_id)
        return self.database.execute(
            "SELECT * FROM version_rollouts WHERE portfolio_id = ? AND generation = ?",
            (portfolio_id, active["generation"]),
        ).fetchone()

    def load_active(self, portfolio_id: str, expected_hash: str | None = None) -> dict:
        active = self._active(portfolio_id)
        if expected_hash is not None and active["artifact_hash"] != expected_hash:
            raise StaleState("requested artifact is not active")
        rollout = self._rollout(portfolio_id)
        if rollout and rollout["state"] in {"ROLLBACK_PENDING", "BLOCKED"}:
            raise ValidationFailure("artifact rollback pending; no new decisions")
        bundle = self.store.get(active["artifact_hash"])
        return {**bundle, "version_id": active["version_id"], "generation": active["generation"]}

    def assert_current(self, portfolio_id: str, bundle: dict) -> None:
        current = self.load_active(portfolio_id)
        if any(current[k] != bundle[k] for k in ("artifact_hash", "version_id", "generation", "manifest")):
            raise StaleState("loaded artifact generation is obsolete")
        # A hash/label supplied by a caller cannot stand in for the actual loaded bytes.
        if bundle["files"] != current["files"]:
            raise ValidationFailure("loaded artifact bytes do not match the active bundle")

    def begin(self, portfolio_id: str, consumer_id: str, reconcile: Callable[[], None],
              lease=None) -> dict:
        """Rebuild from persisted bytes, reconcile protection, then acknowledge a generation."""
        if lease is not None:
            from trade_graph.application.scheduler import Scheduler

            leased = Scheduler(self.database, self.clock).leased_row(lease)
            if leased["portfolio_id"] != portfolio_id:
                raise ValidationFailure("reload worker lease belongs to another portfolio")
        active = dict(self._active(portfolio_id))
        try:
            bundle = self.load_active(portfolio_id)
            # This is a synchronous deterministic callback, not a provider/model call.
            reconcile()
            with self.database.immediate():
                if lease is not None:
                    from trade_graph.application.scheduler import Scheduler

                    task = Scheduler(self.database, self.clock).leased_row(lease)
                    if task["portfolio_id"] != portfolio_id:
                        raise ValidationFailure("reload worker lease belongs to another portfolio")
                self.assert_current(portfolio_id, bundle)
                self.database.execute(
                    """INSERT INTO consumer_loads VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (portfolio_id, consumer_id) DO UPDATE SET
                    version_id=excluded.version_id, artifact_hash=excluded.artifact_hash,
                    generation=excluded.generation, manifest_sha256=excluded.manifest_sha256,
                    reconciled_at=excluded.reconciled_at""",
                    (portfolio_id, consumer_id, bundle["version_id"], bundle["artifact_hash"],
                     bundle["generation"], bundle["manifest"]["sha256"], utc_iso(self.clock.now())),
                )
                rollout = self._rollout(portfolio_id)
                if rollout and rollout["state"] in {"RESTART_PENDING", "RESTORE_PENDING"}:
                    self.database.execute(
                        "UPDATE version_rollouts SET state = ? WHERE rollout_id = ?",
                        ("OBSERVING" if rollout["state"] == "RESTART_PENDING" else "RESTORED",
                         rollout["rollout_id"]),
                    )
                    self._event(portfolio_id, "reload", bundle["artifact_hash"], bundle["artifact_hash"],
                                {"consumer_id": consumer_id, "generation": bundle["generation"],
                                 "manifest_sha256": bundle["manifest"]["sha256"], "reconciliation_ready": True})
                return bundle
        except StaleState:
            # A replaced/expired worker or an obsolete generation has no rollout authority.
            raise
        except Exception:
            # Never roll back a newer activation because an obsolete loader failed.
            with self.database.immediate():
                if lease is not None:
                    from trade_graph.application.scheduler import Scheduler

                    Scheduler(self.database, self.clock).leased_row(lease)
                current = self._active(portfolio_id)
                if current["generation"] == active["generation"]:
                    self._request_rollback(portfolio_id, "artifact reload/reconciliation failed")
            raise

    def _request_rollback(self, portfolio_id: str, reason: str) -> None:
        rollout = self._rollout(portfolio_id)
        if rollout and rollout["state"] in {"RESTART_PENDING", "OBSERVING", "ACTIVE"}:
            self.database.execute("UPDATE version_rollouts SET state = 'ROLLBACK_PENDING' WHERE rollout_id = ?",
                                  (rollout["rollout_id"],))
            self._event(portfolio_id, "rollback_requested", rollout["target_hash"], rollout["previous_hash"],
                        {"generation": rollout["generation"], "reason": reason[:500]})

    @atomic
    def observe(self, portfolio_id: str, bundle: dict, observation_id: str, *, ok: bool,
                context_bytes: int = 0, input_tokens: int = 0, output_tokens: int = 0,
                required_retained: bool = True, reason: str = "") -> str:
        """Record scoped real decision evidence against the policy pinned at activation."""
        active = self._active(portfolio_id)
        if active["generation"] != bundle["generation"] or active["artifact_hash"] != bundle["artifact_hash"]:
            raise StaleState("obsolete observation cannot change the active rollout")
        rollout = self._rollout(portfolio_id)
        if rollout is None:
            return "BASELINE"
        if rollout["state"] == "RESTORED":
            return "RESTORED"
        prior = self.database.execute(
            "SELECT document_json FROM version_observations WHERE rollout_id = ? AND observation_id = ?",
            (rollout["rollout_id"], observation_id),
        ).fetchone()
        if prior:
            return rollout["state"]
        if rollout["state"] in {"ROLLBACK_PENDING", "BLOCKED", "ROLLED_BACK", "SUPERSEDED"}:
            raise ValidationFailure("terminal rollout cannot accept new observation effects")
        policy = ObservationPolicy.model_validate_json(rollout["policy_json"])
        if any(type(x) is not int or x < 0 for x in (context_bytes, input_tokens, output_tokens)):
            raise ValidationFailure("observation metrics require nonnegative integer units")
        invocation = None
        if ok:
            invocation = self.database.execute(
                """SELECT i.* FROM model_invocations i JOIN decisions d ON d.snapshot_id = i.run_id
                WHERE d.decision_id = ? AND d.portfolio_id = ? AND i.portfolio_id = ?
                AND i.system_version_id = ?""",
                (observation_id, portfolio_id, portfolio_id, bundle["artifact_hash"]),
            ).fetchone()
            if invocation is None:
                raise ValidationFailure("observation requires an actual persisted model invocation")
        if ok:
            self.assert_current(portfolio_id, bundle)
            row = self.database.execute(
                """SELECT d.system_version_id, s.payload_json FROM decisions d
                JOIN snapshots s ON s.snapshot_id = d.snapshot_id
                WHERE d.decision_id = ? AND d.portfolio_id = ? AND s.portfolio_id = ?""",
                (observation_id, portfolio_id, portfolio_id),
            ).fetchone()
            if row is None or row["system_version_id"] != bundle["artifact_hash"]:
                raise ValidationFailure("observation requires a scoped persisted new-version decision")
            snapshot = json.loads(row["payload_json"])
            expected = {"version_id": bundle["version_id"], "artifact_hash": bundle["artifact_hash"],
                        "generation": bundle["generation"], "manifest_sha256": bundle["manifest"]["sha256"]}
            if snapshot.get("artifact") != expected:
                raise ValidationFailure("decision snapshot does not prove the loaded artifact generation")
            request = json.loads(invocation["request_json"])
            if (request.get("role") != "trader" or request.get("context", {}).get("artifact") != expected
                    or request["context"].get("selected_context") != snapshot.get("selected_context")
                    or not invocation["result_json"] or not json.loads(invocation["result_json"])["ok"]):
                raise ValidationFailure("observation model request/result does not match loaded decision context")
            selected = snapshot.get("selected_context", {})
            guard = snapshot.get("guard", {})
            required_retained = bool(required_retained and
                                     set(selected.get("always_include", [])) == {"mandate_obligations", "active_safety"}
                                     and isinstance(selected.get("mandate_obligations"), dict)
                                     and isinstance(guard.get("mandate"), dict) and guard["mandate"]
                                     and selected["mandate_obligations"] == guard["mandate"]
                                     and isinstance(guard.get("policy"), dict) and guard["policy"]
                                     and selected.get("active_safety") ==
                                     {"policy": guard["policy"], "pause": guard.get("pause", {})})
            if not self.database.execute(
                "SELECT 1 FROM consumer_loads WHERE portfolio_id = ? AND generation = ? AND artifact_hash = ?",
                (portfolio_id, bundle["generation"], bundle["artifact_hash"]),
            ).fetchone():
                raise ValidationFailure("decision has no reconciled consumer reload")
        else:
            failure = self.database.execute(
                """SELECT r.status FROM role_results r JOIN tasks t ON t.task_id = r.task_id
                WHERE r.task_id = ? AND r.portfolio_id = ? AND t.portfolio_id = ?
                AND r.role = 'trader' AND t.role = 'trader'""",
                (observation_id, portfolio_id, portfolio_id),
            ).fetchone()
            snapshot = self.database.execute(
                """SELECT payload_json FROM snapshots WHERE portfolio_id = ?
                AND json_extract(payload_json, '$.task_id') = ? ORDER BY created_at DESC LIMIT 1""",
                (portfolio_id, observation_id),
            ).fetchone()
            expected = {"version_id": bundle["version_id"], "artifact_hash": bundle["artifact_hash"],
                        "generation": bundle["generation"], "manifest_sha256": bundle["manifest"]["sha256"]}
            if (failure is None or failure["status"] not in {"FAILED", "BLOCKED_BUDGET", "WAITING_EXTERNAL"}
                    or snapshot is None or json.loads(snapshot["payload_json"]).get("artifact") != expected):
                raise ValidationFailure("failure observation requires a scoped persisted Trader failure")
            snapshot = json.loads(snapshot["payload_json"])
            invocation = self.database.execute(
                """SELECT * FROM model_invocations WHERE task_id = ? AND portfolio_id = ?
                AND system_version_id = ? ORDER BY created_at DESC LIMIT 1""",
                (observation_id, portfolio_id, bundle["artifact_hash"]),
            ).fetchone()
        # Authoritative units come from durable provider requests and usage facts,
        # never a caller's metrics or a byte-to-token conversion.
        usage, receipts = None, []
        context_bytes = len(json.dumps(snapshot, sort_keys=True).encode())
        if invocation:
            request = json.loads(invocation["request_json"])
            context_bytes = len(json.dumps(request["context"], sort_keys=True).encode())
            result = json.loads(invocation["result_json"]) if invocation["result_json"] else {}
            usage = result.get("usage")
            receipts = [dict(r) for r in self.database.execute(
                "SELECT receipt_id, synthetic, status FROM usage_receipts WHERE reservation_id = ?",
                (invocation["reservation_id"],),
            ).fetchall()]
        input_tokens = (sum(usage[k] for k in ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens"))
                        if usage else None)
        output_tokens = usage["billed_output_tokens"] if usage else None
        healthy = ok and required_retained and context_bytes <= policy.max_context_bytes
        document = {"ok": bool(healthy), "model_ok": ok, "required_retained": required_retained,
                    "context_bytes": context_bytes, "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "reason": reason[:500], "artifact_hash": bundle["artifact_hash"],
                    "generation": bundle["generation"], "usage_known": usage is not None,
                    "usage_receipts": receipts}
        self.database.execute("INSERT INTO version_observations VALUES (?, ?, ?, ?)",
                              (rollout["rollout_id"], observation_id, json.dumps(document, sort_keys=True),
                               utc_iso(self.clock.now())))
        observations = self.database.execute(
            "SELECT document_json FROM version_observations WHERE rollout_id = ?", (rollout["rollout_id"],),
        ).fetchall()
        facts = [json.loads(x["document_json"]) for x in observations]
        failures = sum(not x["ok"] for x in facts)
        if failures > policy.max_failures:
            self._request_rollback(portfolio_id, "observation health/required-context gate failed")
        elif rollout["state"] == "OBSERVING" and sum(x["ok"] for x in facts) >= policy.min_decisions:
            self.database.execute("UPDATE version_rollouts SET state = 'ACTIVE' WHERE rollout_id = ?",
                                  (rollout["rollout_id"],))
            self.database.execute("UPDATE candidates SET state = 'ACTIVE' WHERE candidate_id = ?",
                                  (rollout["candidate_id"],))
            self.database.execute("UPDATE change_tasks SET state = 'ACTIVE' WHERE change_id = "
                                  "(SELECT change_id FROM candidates WHERE candidate_id = ?)",
                                  (rollout["candidate_id"],))
            self._event(portfolio_id, "observation_passed", bundle["artifact_hash"], bundle["artifact_hash"],
                        {"generation": bundle["generation"], "samples": len(facts), "failures": failures})
        return self._rollout(portfolio_id)["state"]

    @atomic
    def maintain(self, portfolio_id: str) -> str:
        """Emergency controller; independent of models, and safe to retry after restart."""
        rollout = self._rollout(portfolio_id)
        if rollout is None:
            return "BASELINE"
        policy = ObservationPolicy.model_validate_json(rollout["policy_json"])
        age = self.clock.now() - datetime.fromisoformat(rollout["activated_at"].replace("Z", "+00:00"))
        if rollout["state"] == "RESTORE_PENDING":
            if age >= timedelta(seconds=policy.reload_timeout_seconds):
                self.database.execute("UPDATE version_rollouts SET state = 'BLOCKED' WHERE rollout_id = ?",
                                      (rollout["rollout_id"],))
                return "BLOCKED"
            return "RESTORE_PENDING"
        if rollout["state"] == "BLOCKED" and rollout["candidate_id"] == rollout["previous_version_id"]:
            return "BLOCKED"
        if ((rollout["state"] == "RESTART_PENDING" and age >= timedelta(seconds=policy.reload_timeout_seconds))
                or (rollout["state"] == "OBSERVING" and age >= timedelta(seconds=policy.horizon_seconds))):
            self._request_rollback(portfolio_id, "reload/observation deadline expired")
            rollout = self._rollout(portfolio_id)
        if rollout["state"] not in {"ROLLBACK_PENDING", "BLOCKED"}:
            return rollout["state"]
        if self._busy(portfolio_id):
            return "ROLLBACK_PENDING"
        try:
            # Automatic recovery requires actual valid predecessor bytes.
            self.store.get(rollout["previous_hash"])
            self.rollback(portfolio_id, rollout["previous_hash"], rollout["previous_version_id"])
        except ValidationFailure:
            self.database.execute("UPDATE version_rollouts SET state = 'BLOCKED' WHERE rollout_id = ?",
                                  (rollout["rollout_id"],))
            return "BLOCKED"
        return "ROLLED_BACK"

    @atomic
    def rollback(self, portfolio_id: str, previous_hash: str, version_id: str) -> None:
        known = self.database.execute(
            """SELECT 1 FROM version_history
            WHERE portfolio_id = ? AND version_id = ? AND artifact_hash = ?""",
            (portfolio_id, version_id, previous_hash),
        ).fetchone()
        if known is None:
            raise ValidationFailure("unknown artifact identity for this portfolio")
        self.store.get(previous_hash)
        self._quiescent(portfolio_id)
        active = self.database.execute(
            "SELECT * FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        if active["version_id"] == version_id and active["artifact_hash"] == previous_hash:
            return
        self.database.execute(
            """UPDATE active_versions SET version_id = ?, artifact_hash = ?,
            fingerprint_json = ?, activated_at = ?, generation = generation + 1 WHERE portfolio_id = ?""",
            (
                version_id,
                previous_hash,
                json.dumps({"artifact": previous_hash}),
                utc_iso(self.clock.now()),
                portfolio_id,
            ),
        )
        self.database.execute(
            """UPDATE candidates SET state = 'ROLLED_BACK' WHERE candidate_id = ?
            AND change_id IN (SELECT change_id FROM change_tasks WHERE portfolio_id = ?)""",
            (active["version_id"], portfolio_id),
        )
        self.database.execute(
            "UPDATE change_tasks SET state = 'ROLLED_BACK' WHERE change_id = "
            "(SELECT change_id FROM candidates WHERE candidate_id = ?) AND portfolio_id = ?",
            (active["version_id"], portfolio_id),
        )
        self.database.execute(
            "UPDATE version_rollouts SET state = 'ROLLED_BACK' WHERE portfolio_id = ? AND generation = ?",
            (portfolio_id, active["generation"]),
        )
        self.database.execute(
            "INSERT INTO version_rollouts VALUES (?, ?, ?, ?, ?, ?, ?, 'RESTORE_PENDING', ?, ?)",
            (str(uuid.uuid4()), portfolio_id, version_id, active["generation"] + 1,
             previous_hash, previous_hash, version_id, self.observation_policy.model_dump_json(),
             utc_iso(self.clock.now())),
        )
        self.database.execute("UPDATE candidates SET state = 'ACTIVE' WHERE candidate_id = ?", (version_id,))
        self.database.execute("UPDATE change_tasks SET state = 'ACTIVE' WHERE change_id = "
                              "(SELECT change_id FROM candidates WHERE candidate_id = ?)", (version_id,))
        self._cancel_stale(portfolio_id, previous_hash)
        self._event(portfolio_id, "rollback", active["artifact_hash"], previous_hash,
                    {"version_id": version_id, "generation": active["generation"] + 1})

    def _busy(self, portfolio_id: str) -> bool:
        return self.database.execute(
            """SELECT 1 FROM tasks WHERE portfolio_id = ? AND role = 'trader'
            AND status IN ('LEASED', 'RUNNING') LIMIT 1""", (portfolio_id,),
        ).fetchone() is not None

    def _quiescent(self, portfolio_id: str) -> None:
        if self._busy(portfolio_id):
            raise ValidationFailure("decision boundary is not quiescent")

    def _cancel_stale(self, portfolio_id: str, new_hash: str) -> None:
        self.database.execute(
            """UPDATE tasks SET status = 'CANCELLED' WHERE portfolio_id = ? AND role = 'trader'
            AND status = 'QUEUED' AND expected_version IS NOT NULL AND expected_version != ?""",
            (portfolio_id, new_hash),
        )

    def _event(self, portfolio_id: str, kind: str, old: str, new: str, details: dict) -> None:
        self.database.execute(
            "INSERT INTO version_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                portfolio_id,
                kind,
                old,
                new,
                utc_iso(self.clock.now()),
                json.dumps(details, sort_keys=True),
            ),
        )
