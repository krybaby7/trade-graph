"""Transactional candidate identity/authority validation and pointer-only rollback."""

from __future__ import annotations

import json
import uuid

from trade_graph.adapters.engineering.provenance import checks_module_hash
from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.application.change_authority import authorized_change
from trade_graph.contracts.models import ChangeResult
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure


class VersionController:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database, self.clock = database, clock

    @atomic
    def ensure(self, portfolio_id: str, version_id: str, artifact_hash: str) -> None:
        if self.database.execute("SELECT 1 FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)).fetchone():
            return
        self.database.execute(
            "INSERT INTO active_versions VALUES (?, ?, ?, ?, ?)",
            (
                portfolio_id,
                version_id,
                artifact_hash,
                json.dumps({"artifact": artifact_hash}),
                utc_iso(self.clock.now()),
            ),
        )
        self.database.execute("INSERT INTO version_history VALUES (?, ?, ?)", (portfolio_id, version_id, artifact_hash))

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
        self._quiescent(portfolio_id)
        previous = self.database.execute(
            "SELECT version_id FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()["version_id"]
        changed = self.database.execute(
            """UPDATE active_versions SET version_id = ?, artifact_hash = ?,
            fingerprint_json = ?, activated_at = ? WHERE portfolio_id = ? AND artifact_hash = ?""",
            (
                stored["candidate_id"],
                stored["content_hash"],
                json.dumps({"artifact": stored["content_hash"]}),
                utc_iso(self.clock.now()),
                portfolio_id,
                task.baseline_hash,
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
        self._event(
            portfolio_id,
            "activate",
            current,
            stored["content_hash"],
            {"candidate_id": stored["candidate_id"], "previous_version_id": previous},
        )

    def fingerprint(self, portfolio_id: str) -> str:
        return self.current_hash(portfolio_id)

    @atomic
    def rollback(self, portfolio_id: str, previous_hash: str, version_id: str) -> None:
        known = self.database.execute(
            """SELECT 1 FROM version_history
            WHERE portfolio_id = ? AND version_id = ? AND artifact_hash = ?""",
            (portfolio_id, version_id, previous_hash),
        ).fetchone()
        if known is None:
            raise ValidationFailure("unknown artifact identity for this portfolio")
        self._quiescent(portfolio_id)
        active = self.database.execute(
            "SELECT * FROM active_versions WHERE portfolio_id = ?", (portfolio_id,)
        ).fetchone()
        if active["version_id"] == version_id and active["artifact_hash"] == previous_hash:
            return
        self.database.execute(
            """UPDATE active_versions SET version_id = ?, artifact_hash = ?,
            fingerprint_json = ?, activated_at = ? WHERE portfolio_id = ?""",
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
        self._cancel_stale(portfolio_id, previous_hash)
        self._event(portfolio_id, "rollback", active["artifact_hash"], previous_hash, {"version_id": version_id})

    def _quiescent(self, portfolio_id: str) -> None:
        if self.database.execute(
            """SELECT 1 FROM tasks WHERE portfolio_id = ? AND role = 'trader'
            AND status IN ('LEASED', 'RUNNING') LIMIT 1""",
            (portfolio_id,),
        ).fetchone():
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
