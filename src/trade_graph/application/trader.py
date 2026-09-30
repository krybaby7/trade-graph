"""Mandate-driven decisions. A model failure is not a hold, and the Leader does not approve trades."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from trade_graph.application.execution import Execution
from trade_graph.application.research import ResearchStore
from trade_graph.contracts.models import Decision, ModelResult
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import Quantity
from trade_graph.roles.strategies import counterfactual, template


@dataclass(frozen=True)
class TraderTurn:
    kind: str
    action: str | None
    decision_id: str | None = None
    intent_id: str | None = None


class Trader:
    def __init__(self, execution: Execution, research: ResearchStore) -> None:
        self.execution = execution
        self.research = research

    def act(
        self,
        portfolio_id: str,
        result: ModelResult,
        *,
        snapshot_id: str,
        task_id: str = "trader",
    ) -> TraderTurn:
        if not result.ok or result.payload is None:
            self.execution.ledger._activity(
                portfolio_id,
                "model_failure",
                {"failure": result.failure, "message": result.message},
            )
            return TraderTurn(kind="failure", action=None)
        choice = _choice(result.payload)
        as_of = self.execution.clock.now()
        for finding_id in choice["evidence_ids"]:
            self.research.require_fresh(portfolio_id, finding_id, as_of=as_of)
        mandate = self.execution.authority.active_mandate(portfolio_id)
        policy = self.execution.authority.active_policy()
        if choice["experiment"] and not mandate.discretionary_experiment:
            raise ValidationFailure("mandate does not allow a discretionary experiment")
        if template(choice["strategy_id"]) is None and not choice["experiment"]:
            raise ValidationFailure("strategy template is unknown")
        decision = Decision(
            record_id=str(uuid.uuid4()),
            created_at_utc=as_of,
            run_id=task_id,
            task_id=task_id,
            root_task_id=task_id,
            portfolio_id=portfolio_id,
            mode=self.execution.mode,
            system_version_id="trader",
            evidence_refs=list(choice["evidence_ids"]),
            trace_id=str(uuid.uuid4()),
            action=choice["action"],
            symbol=choice["symbol"],
            quantity=_quantity(choice),
            rationale=choice["rationale"],
            invalidation=choice["invalidation"],
            horizon_seconds=3600,
            strategy_id=choice["strategy_id"],
            experiment_id=f"exp-{choice['strategy_id']}" if choice["experiment"] else None,
            snapshot_id=snapshot_id,
            mandate_revision=str(mandate.revision),
            policy_revision=policy.revision_id,
            no_action_reason=choice["no_action_reason"],
            uncertainty_note=counterfactual(choice["strategy_id"])
            if choice["action"] in {"hold", "no_action"}
            else None,
        )
        if choice["action"] in {"hold", "no_action"}:
            self.execution.record_non_order(portfolio_id, decision)
            self.execution.ledger._activity(
                portfolio_id,
                "no_trade_evidence",
                {
                    "decision_id": decision.record_id,
                    "action": decision.action,
                    "counterfactual": decision.uncertainty_note,
                    "as_of": utc_iso(as_of),
                },
            )
            return TraderTurn(kind="decision", action=decision.action, decision_id=decision.record_id)
        if choice["action"] not in {"enter", "exit"}:
            raise ValidationFailure("trader action is not executable")
        intent_id = self.execution.authorize(portfolio_id, decision)
        return TraderTurn(
            kind="decision",
            action=decision.action,
            decision_id=decision.record_id,
            intent_id=intent_id,
        )


def _choice(payload: dict) -> dict:
    action = payload.get("action")
    if action not in {"enter", "exit", "hold", "no_action"}:
        raise ValidationFailure("trader payload action is invalid")
    strategy_id = payload.get("strategy_id")
    rationale = payload.get("rationale")
    invalidation = payload.get("invalidation")
    if not isinstance(strategy_id, str) or not isinstance(rationale, str) or not isinstance(invalidation, str):
        raise ValidationFailure("trader payload is incomplete")
    evidence = payload.get("evidence_ids") or []
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        raise ValidationFailure("evidence ids must be strings")
    return {
        "action": action,
        "symbol": payload.get("symbol"),
        "quantity": payload.get("quantity"),
        "strategy_id": strategy_id,
        "rationale": rationale,
        "invalidation": invalidation,
        "experiment": bool(payload.get("experiment", False)),
        "evidence_ids": evidence,
        "no_action_reason": payload.get("no_action_reason"),
        "confidence": payload.get("confidence"),
    }


def _quantity(choice: dict) -> Quantity | None:
    if choice["action"] not in {"enter", "exit"}:
        return None
    raw = choice["quantity"]
    if not isinstance(raw, str):
        raise ValidationFailure("order quantity must be a decimal string")
    symbol = choice["symbol"]
    if not isinstance(symbol, str) or "/" not in symbol:
        raise ValidationFailure("order symbol is required")
    return Quantity(amount=raw, asset=symbol.split("/", 1)[0])
