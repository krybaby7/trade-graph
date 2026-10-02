"""Gateway-owned durable effect journal. A lost external response is uncertain, not free.

This is deliberately not a second billing implementation: all holds and receipts still
belong to BudgetGateway. The invocation key identifies ONE external attempt, not a
provider idempotency guarantee. Reusing a key never reissues its external call.
"""

from __future__ import annotations

import hashlib
import json

from trade_graph.application.budget import BudgetGateway
from trade_graph.contracts.models import ModelRequest, ModelResult
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure

MAX_RESPONSE_BYTES = 65536
MAX_REQUEST_BYTES = 131072


class InvocationJournal:
    def __init__(self, budget: BudgetGateway) -> None:
        self.budget, self.database, self.clock = budget, budget.database, budget.clock

    def binding(self, request: ModelRequest, portfolio_id: str, billing: dict) -> str:
        raw = json.dumps({"request": request.model_dump(mode="json"), "portfolio_id": portfolio_id,
                          "billing": billing}, sort_keys=True, default=str)
        if len(raw.encode()) > MAX_REQUEST_BYTES:
            raise ValidationFailure("bounded model request exceeded")
        return hashlib.sha256(raw.encode()).hexdigest()

    def recover(self, invocation_id: str, request_hash: str) -> ModelResult | None:
        """Called under the gateway writer transaction; RESERVED means dispatch was possible."""
        row = self.database.execute("SELECT * FROM model_invocations WHERE invocation_id = ?",
                                    (invocation_id,)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise StaleState("invocation key reused with different scope, request or billing")
        if row["result_json"] is not None:
            return ModelResult.model_validate_json(row["result_json"])
        self.budget.mark_uncertain(row["reservation_id"])
        self.database.execute("""UPDATE provider_transport_attempts SET outcome='UNCERTAIN',finished_at=?
            WHERE reservation_id=? AND outcome='DISPATCH_POSSIBLE'""",
            (utc_iso(self.clock.now()), row["reservation_id"]))
        result = ModelResult(ok=False, failure="timeout_uncertain",
                             message="recovered dispatch without a durable response; reconcile before new work")
        self.save(invocation_id, result, "UNCERTAIN")
        return result

    def start(self, invocation_id: str, request_hash: str, request: ModelRequest,
              portfolio_id: str, reservation: str) -> None:
        row = self.database.execute("SELECT * FROM tasks WHERE task_id = ?", (request.task_id,)).fetchone()
        if row is None or row["portfolio_id"] != portfolio_id or row["root_task_id"] != request.root_task_id:
            raise AuthorityDenied("durable invocation task/portfolio/root mismatch")
        if row["role"] != request.role:
            raise AuthorityDenied("durable invocation role mismatch")
        now = utc_iso(self.clock.now())
        self.database.execute(
            """INSERT INTO model_invocations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DISPATCHED', NULL, ?, ?)""",
            (invocation_id, request_hash, request.model_dump_json(), portfolio_id, request.task_id,
             request.root_task_id, request.run_id, request.system_version_id, reservation, now, now),
        )

    def save(self, invocation_id: str, result: ModelResult, state: str) -> None:
        # Usage is settled by the caller first, even when the provider output is oversized.
        if len(result.model_dump_json().encode()) > MAX_RESPONSE_BYTES:
            result = ModelResult(ok=False, failure="validation", message="bounded model response exceeded",
                                 usage=result.usage, provider_model=result.provider_model)
        self.database.execute(
            "UPDATE model_invocations SET state = ?, result_json = ?, updated_at = ? WHERE invocation_id = ?",
            (state, result.model_dump_json(), utc_iso(self.clock.now()), invocation_id),
        )
