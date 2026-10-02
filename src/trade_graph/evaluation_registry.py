"""Append-only evidence registry; this is not the authoritative financial ledger.

Use an operator-owned private database outside Engineer snapshots. Registration
timestamps come from the collector's clock, never from trial/candidate input.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from decimal import localcontext
from pathlib import Path

from trade_graph.domain.clock import Clock, SystemClock, utc_iso
from trade_graph.evaluation_contracts import (
    Attempt,
    AttemptResult,
    CostInventory,
    ExpenseAllocation,
    ExpenseEvidence,
    ExpenseResolution,
    ForwardObservation,
    ForwardProtocol,
)


def document_hash(document: str) -> str:
    canonical = json.dumps(json.loads(document), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class TrialRegistry:
    """Durable preregistration with receipts allocated once across all trials.

    The owner/operator controls this collector and its clock. Source references
    are auditable imports, not proof that unimported upstream receipts do not
    exist; sealing must use a complete authoritative deployment-ledger export.
    """

    def __init__(self, path: str | Path, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.connection = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS evaluation_records (
                kind TEXT NOT NULL, record_key TEXT NOT NULL, trial_id TEXT,
                document_json TEXT NOT NULL, document_sha256 TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                PRIMARY KEY (kind, record_key)
            );
            CREATE TRIGGER IF NOT EXISTS evaluation_no_update
            BEFORE UPDATE ON evaluation_records
            BEGIN SELECT RAISE(ABORT, 'evaluation evidence is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS evaluation_no_delete
            BEFORE DELETE ON evaluation_records
            BEGIN SELECT RAISE(ABORT, 'evaluation evidence is append-only'); END;
        """)

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def _atomic(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def _append(self, kind: str, key: str, trial_id: str | None, document) -> None:
        raw = document.model_dump_json()
        self.connection.execute(
            "INSERT INTO evaluation_records VALUES (?, ?, ?, ?, ?, ?)",
            (kind, key, trial_id, raw, document_hash(raw), utc_iso(self.clock.now())),
        )

    def _rows(self, kind: str, trial_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM evaluation_records WHERE kind = ?"
        params = [kind]
        if trial_id is not None:
            sql += " AND trial_id = ?"
            params.append(trial_id)
        rows = self.connection.execute(sql + " ORDER BY collected_at, record_key", params).fetchall()
        for row in rows:
            if document_hash(row["document_json"]) != row["document_sha256"]:
                raise ValueError("evaluation evidence digest mismatch")
        return [dict(row) for row in rows]

    def protocol(self, trial_id: str) -> ForwardProtocol:
        rows = self._rows("protocol", trial_id)
        if len(rows) != 1:
            raise ValueError("unknown trial")
        return ForwardProtocol.model_validate_json(rows[0]["document_json"])

    def _open(self, trial_id: str) -> ForwardProtocol:
        protocol = self.protocol(trial_id)
        if self._rows("inventory", trial_id):
            raise ValueError("trial evidence is sealed; start a new predeclared trial")
        return protocol

    def register(self, protocol: ForwardProtocol) -> str:
        with self._atomic():
            now = self.clock.now()
            if protocol.validation.end > now or now >= protocol.forward_blocks[0].start:
                raise ValueError("register after validation and strictly before untouched forward data")
            prior = [ForwardProtocol.model_validate_json(row["document_json"]) for row in self._rows("protocol")]
            family = [item for item in prior if item.family_id == protocol.family_id]
            if len(family) >= protocol.maximum_family_trials:
                raise ValueError("predeclared family trial allowance is exhausted")
            for item in family:
                if (item.maximum_family_trials, item.uncertainty_alpha) != (
                    protocol.maximum_family_trials, protocol.uncertainty_alpha,
                ):
                    raise ValueError("family multiplicity/uncertainty definition cannot change")
            self._append("protocol", protocol.trial_id, protocol.trial_id, protocol)
        return document_hash(protocol.model_dump_json())

    def observe(self, trial_id: str, observation: ForwardObservation) -> None:
        with self._atomic():
            protocol = self._open(trial_id)
            index = observation.block_index
            if index >= len(protocol.forward_blocks) or observation.window != protocol.forward_blocks[index]:
                raise ValueError("observation must match its predeclared fixed held-out block")
            if observation.available_at > self.clock.now():
                raise ValueError("future observations are unavailable")
            for name in ("data_policy_sha256", "friction_policy_sha256", "regime_classifier_sha256"):
                if getattr(protocol, name) != getattr(observation, name):
                    raise ValueError("comparison data/friction/regime assumptions changed")
            previous = self.observations(trial_id)
            prior_ids = {decision.decision_id for item in previous for decision in item.decisions}
            if any(item.decision_id in prior_ids for item in observation.decisions):
                raise ValueError("decision evidence cannot be reused across blocks")
            if any(item.version_sha256 not in protocol.allowed_versions_sha256 for item in observation.decisions):
                raise ValueError("unregistered configuration change")
            if index == 0 and any(item.opening_equity_eur != protocol.capital_eur for item in observation.arms):
                raise ValueError("baselines must start with the same predeclared capital")
            # Require chronological collection and a continuous, flow-adjusted equity path.
            if index != len(previous):
                raise ValueError("collect every fixed block in chronological order")
            if previous:
                closing = {item.arm: item.closing_equity_eur for item in previous[-1].arms}
                if any(item.opening_equity_eur != closing[item.arm] for item in observation.arms):
                    raise ValueError("opening equity must match the preceding closing mark")
            self._append("observation", f"{trial_id}:{index}", trial_id, observation)

    def observations(self, trial_id: str) -> list[ForwardObservation]:
        values = [ForwardObservation.model_validate_json(row["document_json"])
                  for row in self._rows("observation", trial_id)]
        return sorted(values, key=lambda value: value.block_index)

    def start_attempt(self, trial_id: str, attempt: Attempt) -> None:
        with self._atomic():
            self._open(trial_id)
            self._append("attempt", f"{trial_id}:{attempt.attempt_id}", trial_id, attempt)

    def finish_attempt(self, trial_id: str, result: AttemptResult) -> None:
        with self._atomic():
            self._open(trial_id)
            attempts = [Attempt.model_validate_json(row["document_json"])
                        for row in self._rows("attempt", trial_id)]
            if result.attempt_id not in {item.attempt_id for item in attempts}:
                raise ValueError("record attempts before their outcome, including failures")
            receipts = {item.receipt_id for item in self.expenses()}
            if not set(result.receipt_ids) <= receipts:
                raise ValueError("attempt results require retained receipt evidence")
            self._append("attempt_result", f"{trial_id}:{result.attempt_id}", trial_id, result)

    def record_expense(self, expense: ExpenseEvidence) -> None:
        with self._atomic():
            if expense.incurred_at > self.clock.now():
                raise ValueError("future expenses are unavailable")
            self._append("expense", expense.receipt_id, None, expense)

    def expenses(self) -> list[ExpenseEvidence]:
        resolutions = {
            item.receipt_id: item for item in (
                ExpenseResolution.model_validate_json(row["document_json"])
                for row in self._rows("expense_resolution")
            )
        }
        expenses = [ExpenseEvidence.model_validate_json(row["document_json"]) for row in self._rows("expense")]
        return [expense if expense.receipt_id not in resolutions else ExpenseEvidence.model_validate({
            **expense.model_dump(),
            "amount_eur": resolutions[expense.receipt_id].amount_eur,
            "outcome": resolutions[expense.receipt_id].outcome,
            "source_ref": resolutions[expense.receipt_id].source_ref,
            "conversion_ref": resolutions[expense.receipt_id].conversion_ref,
        }) for expense in expenses]

    def resolve_expense(self, resolution: ExpenseResolution) -> None:
        with self._atomic():
            originals = [ExpenseEvidence.model_validate_json(row["document_json"])
                         for row in self._rows("expense")]
            matches = [item for item in originals if item.receipt_id == resolution.receipt_id]
            if not matches or matches[0].outcome != "unresolved":
                raise ValueError("only uncertain original receipts can be reconciled")
            # Reconciliation adds financial evidence without reopening the held-out
            # sample or changing its scoring definition. Original receipts persist.
            self._append("expense_resolution", resolution.receipt_id, None, resolution)

    def allocations(self, trial_id: str) -> list[ExpenseAllocation]:
        return [ExpenseAllocation.model_validate_json(row["document_json"])
                for row in self._rows("allocation", trial_id)]

    def allocate_expense(self, trial_id: str, allocation: ExpenseAllocation) -> None:
        with self._atomic():
            self._open(trial_id)
            if allocation.receipt_id not in {item.receipt_id for item in self.expenses()}:
                raise ValueError("unknown authoritative receipt")
            all_allocations = [ExpenseAllocation.model_validate_json(row["document_json"])
                               for row in self._rows("allocation")]
            with localcontext() as context:
                context.prec = 100
                weight = sum((item.weight for item in all_allocations if item.receipt_id == allocation.receipt_id),
                             start=allocation.weight)
            if weight > 1:
                raise ValueError("shared receipts cannot be allocated more than once")
            key = f"{trial_id}:{allocation.receipt_id}:{allocation.arm}"
            self._append("allocation", key, trial_id, allocation)

    def seal_cost_inventory(self, trial_id: str, inventory: CostInventory) -> None:
        with self._atomic():
            protocol = self._open(trial_id)
            if not protocol.forward_blocks[-1].end <= inventory.source_cutoff <= self.clock.now():
                raise ValueError("ledger inventory must cover the completed forward horizon")
            allocated = {item.receipt_id for item in self.allocations(trial_id)}
            if allocated != set(inventory.receipt_ids):
                raise ValueError("complete ledger inventory must match all allocated receipts")
            results = [AttemptResult.model_validate_json(row["document_json"])
                       for row in self._rows("attempt_result", trial_id)]
            if any(not set(result.receipt_ids) <= allocated for result in results):
                raise ValueError("failed/rejected attempt costs cannot be omitted")
            self._append("inventory", trial_id, trial_id, inventory)

    def report(self, trial_id: str) -> dict:
        from trade_graph.forward_evaluation import build_forward_report

        # A single read transaction prevents a concurrent collector producing a mixed report.
        with self._atomic():
            protocol = self.protocol(trial_id)
            return build_forward_report(
                protocol=protocol, as_of=self.clock.now(),
                registration=self._rows("protocol", trial_id)[0],
                observations=self.observations(trial_id),
                expenses=self.expenses(), allocations=self.allocations(trial_id),
                attempts=self._rows("attempt", trial_id),
                attempt_results=self._rows("attempt_result", trial_id),
                inventories=self._rows("inventory", trial_id),
                trials=self._rows("protocol"),
                expense_history=self._rows("expense"),
                expense_resolutions=self._rows("expense_resolution"),
            )
