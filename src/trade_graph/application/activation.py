"""Compare-and-set artifact activation. Rollback restores the pointer, not the ledger."""

from __future__ import annotations

import json
import uuid

from trade_graph.adapters.engineering.provenance import checks_module_hash
from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import StaleState, ValidationFailure


class VersionController:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    def ensure(self, portfolio_id: str, version_id: str, artifact_hash: str) -> None:
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            row = conn.execute(
                "SELECT portfolio_id FROM active_versions WHERE portfolio_id = ?",
                (portfolio_id,),
            ).fetchone()
            if row:
                return
            conn.execute(
                """INSERT INTO active_versions
                (portfolio_id, version_id, artifact_hash, fingerprint_json, activated_at)
                VALUES (?, ?, ?, ?, ?)""",
                (portfolio_id, version_id, artifact_hash, json.dumps({"artifact": artifact_hash}), now),
            )

    def current_hash(self, portfolio_id: str) -> str:
        row = self.database.execute(
            "SELECT artifact_hash FROM active_versions WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
        return row["artifact_hash"]

    def activate(self, portfolio_id: str, candidate: dict) -> None:
        content_hash = candidate.get("content_hash")
        row = None
        if content_hash:
            row = self.database.execute(
                """SELECT exit_code, checks_module_hash FROM controller_attestations
                WHERE content_hash = ?""",
                (content_hash,),
            ).fetchone()
        trusted = (
            row is not None
            and row["exit_code"] == 0
            and row["checks_module_hash"] == checks_module_hash()
        )
        if not trusted:
            raise ValidationFailure("missing trusted attestation")
        current = self.current_hash(portfolio_id)
        if current != candidate["baseline_hash"]:
            raise StaleState("baseline moved; revalidate")
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            busy = conn.execute(
                """SELECT COUNT(*) AS n FROM tasks
                WHERE portfolio_id = ? AND role = 'trader' AND status IN ('LEASED', 'RUNNING')""",
                (portfolio_id,),
            ).fetchone()["n"]
            if busy:
                raise ValidationFailure("decision boundary is not quiescent")
            cursor = conn.execute(
                """UPDATE active_versions
                SET version_id = ?, artifact_hash = ?, fingerprint_json = ?, activated_at = ?
                WHERE portfolio_id = ? AND artifact_hash = ?""",
                (
                    candidate["candidate_id"],
                    candidate["content_hash"],
                    json.dumps({"artifact": candidate["content_hash"]}),
                    now,
                    portfolio_id,
                    candidate["baseline_hash"],
                ),
            )
            if cursor.rowcount != 1:
                raise StaleState("compare-and-set failed")
            conn.execute(
                """UPDATE tasks SET status = 'CANCELLED'
                WHERE portfolio_id = ? AND role = 'trader' AND status = 'QUEUED'
                AND expected_version IS NOT NULL AND expected_version != ?""",
                (portfolio_id, candidate["content_hash"]),
            )
            conn.execute(
                "UPDATE candidates SET state = 'OBSERVING' WHERE candidate_id = ?",
                (candidate["candidate_id"],),
            )
            conn.execute(
                """INSERT INTO version_events
                (event_id, portfolio_id, kind, from_hash, to_hash, created_at, details_json)
                VALUES (?, ?, 'activate', ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    portfolio_id,
                    current,
                    candidate["content_hash"],
                    now,
                    json.dumps({"candidate_id": candidate["candidate_id"]}),
                ),
            )

    def fingerprint(self, portfolio_id: str) -> str:
        return self.current_hash(portfolio_id)

    def rollback(self, portfolio_id: str, previous_hash: str, version_id: str) -> None:
        current = self.current_hash(portfolio_id)
        if previous_hash != current:
            known = self.database.execute(
                """SELECT event_id FROM version_events
                WHERE portfolio_id = ? AND (from_hash = ? OR to_hash = ?)""",
                (portfolio_id, previous_hash, previous_hash),
            ).fetchone()
            if known is None:
                raise ValidationFailure("unknown artifact pointer")
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            conn.execute(
                """UPDATE active_versions
                SET version_id = ?, artifact_hash = ?, fingerprint_json = ?, activated_at = ?
                WHERE portfolio_id = ?""",
                (
                    version_id,
                    previous_hash,
                    json.dumps({"artifact": previous_hash}),
                    now,
                    portfolio_id,
                ),
            )
            conn.execute(
                "UPDATE candidates SET state = 'ROLLED_BACK' WHERE content_hash = ?",
                (current,),
            )
            conn.execute(
                """INSERT INTO version_events
                (event_id, portfolio_id, kind, from_hash, to_hash, created_at, details_json)
                VALUES (?, ?, 'rollback', ?, ?, ?, '{}')""",
                (str(uuid.uuid4()), portfolio_id, current, previous_hash, now),
            )
