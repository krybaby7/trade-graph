"""Protected paper-only auxiliary asset holds; never evidence of a native venue bound."""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Context, Decimal, Inexact, localcontext
from typing import Literal

from pydantic import ConfigDict, Field, field_serializer, field_validator, model_validator

from trade_graph.adapters.persistence.db import atomic
from trade_graph.contracts.models import ContractModel
from trade_graph.domain.errors import AuthorityDenied, StaleState, ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal


class FeeAssetMaximum(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset: str
    maximum_amount: Decimal

    @field_validator("asset")
    @classmethod
    def _asset(cls, value: str) -> str:
        if not value.isupper() or not value.isalnum() or not 2 <= len(value) <= 16:
            raise ValueError("native fee reserve asset must be uppercase alphanumeric")
        return value

    @field_validator("maximum_amount", mode="before")
    @classmethod
    def _amount(cls, value) -> Decimal:
        if isinstance(value, str) and len(value) > 96:
            raise ValueError("native fee reserve amount is oversized")
        amount = parse_decimal(value)
        shape = amount.as_tuple()
        if not -18 <= shape.exponent <= 28 or len(shape.digits) + shape.exponent > 28:
            raise ValueError("native fee reserve amount exceeds bounded native width")
        with localcontext(Context(prec=28)) as context:
            context.traps[Inexact] = True
            try:
                context.plus(amount)
            except ArithmeticError:
                raise ValueError("native fee reserve precision exceeds protected context") from None
        if amount <= 0:
            raise ValueError("fee reserve maximum must be positive")
        return amount

    @field_serializer("maximum_amount")
    def _serialize(self, value: Decimal) -> str:
        return canonical_decimal(value)


class PaperNativeFeeReservePlan(ContractModel):
    """A trusted virtual-paper plan; construction cannot authorize any live effect."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    portfolio_id: str = Field(min_length=1, max_length=128)
    intent_id: str = Field(min_length=1, max_length=128)
    venue: Literal["paper"] = "paper"
    account_id: Literal["paper"] = "paper"
    mode: Literal["paper"] = "paper"
    source_ref: str = Field(min_length=1, max_length=128)
    components: tuple[FeeAssetMaximum, ...] = Field(min_length=1, max_length=8)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("paper fee reserve schema requires integer version 1")
        return value

    @model_validator(mode="after")
    def _distinct(self) -> PaperNativeFeeReservePlan:
        if len({item.asset for item in self.components}) != len(self.components):
            raise ValueError("paper fee reserve assets must be distinct")
        return self

    @property
    def sha256(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class PaperNativeFeeReserveController:
    """Extend a pending exact paper intent in the same protected writer transaction."""

    def __init__(self, execution) -> None:
        from trade_graph.application.execution import Execution
        from trade_graph.application.ledger import Ledger

        if (
            type(execution) is not Execution
            or type(execution.ledger) is not Ledger
            or execution.ledger.database is not execution.database
            or execution.ledger.clock is not execution.clock
        ):
            raise TypeError("paper fee reservation requires the concrete protected Execution")
        self.execution = execution
        self.database = execution.database
        self.clock = execution.clock

    @atomic
    def reserve(self, plan: PaperNativeFeeReservePlan) -> tuple[str, ...]:
        if type(plan) is not PaperNativeFeeReservePlan:
            raise TypeError("paper fee reservation requires an exact typed plan")
        if (
            type(plan.components) is not tuple
            or not 1 <= len(plan.components) <= 8
            or any(type(item) is not FeeAssetMaximum for item in plan.components)
        ):
            raise ValueError("paper fee reserve plan has invalid bounded components")
        plan = PaperNativeFeeReservePlan.model_validate(plan.model_dump(mode="json"))
        execution = self.execution
        from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker

        if (execution.venue, execution.account_id, execution.mode) != ("paper", "paper", "paper"):
            raise AuthorityDenied("native fee reserve fixture is restricted to the exact paper execution")
        broker = execution.broker
        inner = broker.inner if type(broker) is DropAckBroker else broker
        if (
            type(inner) is not PaperBroker
            or type(broker) not in {PaperBroker, DropAckBroker}
            or inner.database is not self.database
            or inner.clock is not self.clock
            or execution.database is not self.database
            or execution.clock is not self.clock
        ):
            raise AuthorityDenied("fee reserve requires the concrete local paper broker and protected database/clock")
        row = self.database.execute(
            "SELECT portfolio_id,state,payload_json FROM order_intents WHERE intent_id=?",
            (plan.intent_id,),
        ).fetchone()
        portfolio = self.database.execute(
            "SELECT mode,status FROM portfolios WHERE portfolio_id=?",
            (plan.portfolio_id,),
        ).fetchone()
        if row is None or portfolio is None:
            raise ValidationFailure("unknown paper fee reserve portfolio or intent")
        payload = json.loads(row["payload_json"])
        if (
            row["portfolio_id"] != plan.portfolio_id
            or portfolio["mode"] != "paper"
            or portfolio["status"] != "open"
            or (payload.get("venue"), payload.get("account_id"), payload.get("mode")) != ("paper", "paper", "paper")
        ):
            raise AuthorityDenied("paper fee reserve plan does not match its durable scope")
        if (
            row["state"] != "SUBMISSION_PENDING"
            or self.database.execute(
                "SELECT 1 FROM order_attempts WHERE intent_id=? LIMIT 1",
                (plan.intent_id,),
            ).fetchone()
            is not None
        ):
            raise StaleState("auxiliary fee holds must precede every submission attempt")
        if (
            self.database.execute(
                "SELECT 1 FROM native_fee_reservations WHERE intent_id=? LIMIT 1",
                (plan.intent_id,),
            ).fetchone()
            is not None
        ):
            raise StaleState("native fee reserve plan cannot replace original authority")
        primary = payload.get("reserve_asset")
        if primary is None or any(component.asset == primary for component in plan.components):
            raise ValidationFailure("auxiliary fee plan requires a distinct existing primary hold")
        if (
            self.database.execute(
                "SELECT 1 FROM position_reservations WHERE intent_id=? AND asset=? AND state='held'",
                (plan.intent_id, primary),
            ).fetchone()
            is None
        ):
            raise StaleState("paper fee reserve primary authority is not held")
        books = execution.ledger.books(plan.portfolio_id)
        with localcontext(Context(prec=28)) as context:
            context.traps[Inexact] = True
            for component in plan.components:
                cash = books.cash_amount(component.asset)
                inventory = execution._owned(books, component.asset)
                # Cash and FIFO inventory for one asset cannot be silently netted.
                if cash and inventory:
                    raise ValidationFailure("ambiguous cash/inventory fee asset custody")
                available = (cash or inventory) - execution._reserved(plan.portfolio_id, component.asset)
                if available < component.maximum_amount:
                    raise StaleState("insufficient unreserved paper asset for native fee plan")
        identifiers = tuple(str(uuid.uuid4()) for _ in plan.components)
        self.database.connection.executemany(
            """INSERT INTO native_fee_reservations
            (reservation_id,portfolio_id,intent_id,asset,original_amount,current_amount,state,plan_sha256,created_at)
            VALUES (?,?,?,?,?,?,'held',?,?)""",
            [
                (
                    identity,
                    plan.portfolio_id,
                    plan.intent_id,
                    component.asset,
                    canonical_decimal(component.maximum_amount),
                    canonical_decimal(component.maximum_amount),
                    plan.sha256,
                    execution.now(),
                )
                for identity, component in zip(identifiers, plan.components, strict=True)
            ],
        )
        execution.ledger._activity(
            plan.portfolio_id,
            "paper_native_fee_reserves_created",
            {
                "intent_id": plan.intent_id,
                "plan_sha256": plan.sha256,
                "plan": plan.model_dump(mode="json"),
                "reservation_ids": list(identifiers),
                "external_venue_bound_verified": False,
            },
        )
        return identifiers
