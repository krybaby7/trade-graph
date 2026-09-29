"""Compare-and-set artifact activation. Rollback restores the pointer, not the ledger."""

from __future__ import annotations

import json
import uuid

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
        attestation = candidate.get("attestation") or {}
        if attestation.get("runner") != "trusted-controller" or attestation.get("exit_code") != 0:
            raise ValidationFailure("missing trusted attestation")
        if attestation.get("content_hash") != candidate.get("content_hash"):
            raise ValidationFailure("attestation hash mismatch")
        current = self.current_hash(portfolio_id)
        if current != candidate["baseline_hash"]:
            raise StaleState("baseline moved; revalidate")
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
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

    def rollback(self, portfolio_id: str, previous_hash: str, version_id: str) -> None:
        current = self.current_hash(portfolio_id)
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            conn.execute(
                """UPDATE active_versions SET version_id = ?, artifact_hash = ?, activated_at = ?
                WHERE portfolio_id = ?""",
                (version_id, previous_hash, now, portfolio_id),
            )
            conn.execute(
                """INSERT INTO version_events
                (event_id, portfolio_id, kind, from_hash, to_hash, created_at, details_json)
                VALUES (?, ?, 'rollback', ?, ?, ?, '{}')""",
                (str(uuid.uuid4()), portfolio_id, current, previous_hash, now),
            )
