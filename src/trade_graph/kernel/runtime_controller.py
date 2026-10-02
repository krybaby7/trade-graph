"""Pinned release admission and recovery, independent of mutable strategy code."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from pathlib import Path

from trade_graph.adapters.engineering.process import run_bounded
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError
from trade_graph.kernel.financial_service import ProtectedFinancialService
from trade_graph.kernel.process_boundary import _no_duplicate_keys
from trade_graph.kernel.runtime_manifest import document_sha256

CHILD = Path(__file__).resolve().parents[1] / "adapters" / "isolation" / "protected_child.py"


class ProtectedRuntimeController:
    def __init__(self, financial: ProtectedFinancialService, *, instance_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", instance_id):
            raise ValueError("bounded protected instance identity required")
        self.financial = financial
        self.database, self.clock, self.manifest = financial.database, financial.clock, financial.manifest
        self.instance_id = instance_id
        self.manifest.assert_current()
        with self.database.immediate() as conn:
            conn.execute("""INSERT OR IGNORE INTO protected_runtime_instances
                (instance_id,manifest_sha256,generation,status,updated_at) VALUES (?,?,0,'MANAGE_ONLY',?)""",
                (instance_id, self.manifest.sha256, utc_iso(self.clock.now())))
            self.financial._instance(instance_id)

    def admit_release(self, *, release_id: str, source_text: str) -> dict:
        """Admit only exact source hashes already approved in the owner manifest.

        A caller's role label, test boolean, build label or submitted hash grants
        nothing. The controller copies source into its private authoritative DB;
        there is no later read of candidate-controlled files.
        """
        self.manifest.assert_current()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", release_id):
            raise ValueError("bounded immutable release ID required")
        if type(source_text) is not str or len(source_text.encode()) > self.manifest.maximum_source_bytes:
            raise ValueError("bounded immutable source required")
        source_sha256 = hashlib.sha256(source_text.encode()).hexdigest()
        if source_sha256 not in self.manifest.approved_source_sha256:
            raise AuthorityDenied("release source lacks exact owner-manifest approval")
        build_digest = document_sha256({"source_sha256": source_sha256, "contract": "decision-proposal-v1"})
        document = {"release_id": release_id, "manifest_sha256": self.manifest.sha256,
                    "source_sha256": source_sha256, "source_text": source_text, "build_digest": build_digest,
                    "admitted_by": "owner_pinned_manifest"}
        with self.database.immediate() as conn:
            existing = conn.execute("SELECT * FROM protected_mutable_releases WHERE release_id=?",
                                    (release_id,)).fetchone()
            if existing is not None:
                if any(existing[name] != value for name, value in document.items()):
                    raise AuthorityDenied("release IDs are immutable")
            else:
                conn.execute("""INSERT INTO protected_mutable_releases
                    (release_id,manifest_sha256,source_sha256,source_text,build_digest,admitted_by,created_at)
                    VALUES (?,?,?,?,?,?,?)""", (*document.values(), utc_iso(self.clock.now())))
        return {key: value for key, value in document.items() if key != "source_text"}

    def _has_success(self, release_id: str | None) -> bool:
        return bool(release_id and self.database.execute("""SELECT 1 FROM protected_rpc_requests
            WHERE instance_id=? AND state='APPLIED' AND json_extract(scope_json,'$.release_id')=?
            AND json_extract(scope_json,'$.manifest_sha256')=? LIMIT 1""",
            (self.instance_id, release_id, self.manifest.sha256)).fetchone())

    def activate_release(self, release_id: str) -> None:
        self.manifest.assert_current()
        with self.database.immediate() as conn:
            instance = self.financial._instance(self.instance_id)
            release = conn.execute("SELECT * FROM protected_mutable_releases WHERE release_id=?",
                                   (release_id,)).fetchone()
            if (release is None or release["manifest_sha256"] != self.manifest.sha256
                    or release["source_sha256"] not in self.manifest.approved_source_sha256
                    or hashlib.sha256(release["source_text"].encode()).hexdigest() != release["source_sha256"]):
                raise AuthorityDenied("release lacks immutable owner-pinned admission")
            if instance["active_release_id"] == release_id and instance["status"] == "RUNNING":
                return
            previous = (instance["active_release_id"] if self._has_success(instance["active_release_id"])
                        else instance["previous_release_id"])
            if previous == release_id:
                previous = None
            conn.execute("""UPDATE protected_runtime_instances SET active_release_id=?, previous_release_id=?,
                generation=generation+1,status='RUNNING',updated_at=? WHERE instance_id=?""",
                (release_id, previous, utc_iso(self.clock.now()), self.instance_id))
            conn.execute("UPDATE protected_rpc_requests SET state='REVOKED',updated_at=? "
                         "WHERE instance_id=? AND state='ISSUED'", (utc_iso(self.clock.now()), self.instance_id))

    def status(self) -> dict:
        self.manifest.assert_current()
        instance = self.financial._instance(self.instance_id)
        return dict(instance)

    def _recover_mutable(self, expected_release_id: str, expected_generation: int) -> str:
        """Switch executable version only. Never restore finance or call candidate hooks."""
        self.manifest.assert_current()
        with self.database.immediate() as conn:
            instance = self.financial._instance(self.instance_id)
            if (instance["active_release_id"] != expected_release_id
                    or instance["generation"] != expected_generation):
                return "NEWER_RELEASE_PRESERVED"
            previous = instance["previous_release_id"]
            if previous and self._has_success(previous):
                release = conn.execute("SELECT * FROM protected_mutable_releases WHERE release_id=?",
                                       (previous,)).fetchone()
                if (release and release["manifest_sha256"] == self.manifest.sha256
                        and release["source_sha256"] in self.manifest.approved_source_sha256
                        and hashlib.sha256(release["source_text"].encode()).hexdigest() == release["source_sha256"]):
                    conn.execute("""UPDATE protected_runtime_instances SET active_release_id=?,
                        previous_release_id=NULL,generation=generation+1,status='RUNNING',updated_at=?
                        WHERE instance_id=?""", (previous, utc_iso(self.clock.now()), self.instance_id))
                    return "ROLLED_BACK_TO_VALIDATED_RELEASE"
            conn.execute("""UPDATE protected_runtime_instances SET generation=generation+1,status='MANAGE_ONLY',
                updated_at=? WHERE instance_id=?""", (utc_iso(self.clock.now()), self.instance_id))
            return "MANAGE_ONLY"

    def run_active(self, portfolio_id: str, symbol: str, *, cancelled: threading.Event | None = None) -> dict:
        self.manifest.assert_current()
        if cancelled is not None and cancelled.is_set():
            return {"status": "CANCELLED", "process": None}
        context = self.financial.issue(self.instance_id, portfolio_id, symbol)
        release = self.database.execute("SELECT * FROM protected_mutable_releases WHERE release_id=?",
                                        (context["release_id"],)).fetchone()
        if (release is None or release["source_sha256"] != context["source_sha256"]
                or hashlib.sha256(release["source_text"].encode()).hexdigest() != context["source_sha256"]):
            self.financial.revoke(context["request_id"])
            raise AuthorityDenied("issued immutable release changed")
        payload = json.dumps({"source": release["source_text"], "context": context}, allow_nan=False).encode()
        process = run_bounded([sys.executable, "-I", "-S", "-B", str(CHILD)], payload,
                              cwd=str(CHILD.parent), wall_seconds=self.manifest.wall_seconds)
        diagnostic = {name: process[name] for name in ("exit_code", "timed_out", "output_exceeded") if name in process}
        self.manifest.assert_current()
        if cancelled is not None and cancelled.is_set():
            self.financial.revoke(context["request_id"])
            return {"status": "CANCELLED", "process": diagnostic}
        if process["exit_code"] == 0:
            try:
                envelope = json.loads(process["stdout"], object_pairs_hook=_no_duplicate_keys)
                if (type(envelope) is not dict or envelope.get("request_id") != context["request_id"]
                        or envelope.get("capability") != context["capability"]):
                    raise AuthorityDenied("worker response does not match its issued capability")
                response = self.financial.dispatch(process["stdout"])
                return {"status": "APPLIED", "response": response, "process": diagnostic}
            except StaleState:
                # Normal concurrent management invalidates a decision; do not
                # blame or roll back correct strategy code for stale state.
                self.financial.revoke(context["request_id"])
                return {"status": "STALE_RETRY_REQUIRED", "process": diagnostic}
            except (TradeGraphError, ValueError, TypeError, ArithmeticError, RecursionError):
                pass
        self.financial.revoke(context["request_id"])
        recovered = self._recover_mutable(context["release_id"], context["generation"])
        return {"status": "MUTABLE_REJECTED", "recovery": recovered, "process": diagnostic,
                "live_authorization": False, "paid_authorization": False}

    async def recover(self) -> dict:
        """Restart reconciliation precedes any resumed decision or submission."""
        self.manifest.assert_current()
        with self.database.immediate() as conn:
            conn.execute("UPDATE protected_rpc_requests SET state='REVOKED',updated_at=? "
                         "WHERE instance_id=? AND state='ISSUED'", (utc_iso(self.clock.now()), self.instance_id))
        await self.financial.reconcile()
        return {"status": "RECONCILED", "runtime": self.status(), "live_authorization": False}
