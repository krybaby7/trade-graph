"""Nested reservations. Unknown usage stays reserved. Paper equity cannot refill the allowance."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from trade_graph.adapters.persistence.db import Database, atomic
from trade_graph.contracts.models import ModelUsage, PriceCard
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import BudgetExhausted, NotFound
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.pricing import usage_cost, worst_case_cost

OPEN_STATES = ("RESERVED", "COMMITTED", "UNCERTAIN", "RECONCILED", "CONSERVATIVE")


class BudgetGateway:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    @atomic
    def configure(
        self,
        *,
        deployment_id: str,
        currency: str,
        total: Decimal,
        period: Decimal,
        priority_reserve: Decimal,
        daily: Decimal,
        root: Decimal,
        roles: dict[str, Decimal],
    ) -> None:
        amounts = [total, period, priority_reserve, daily, root, *roles.values()]
        if currency != "EUR" or any(not value.is_finite() or value < 0 for value in amounts):
            raise ValueError("budgets require nonnegative finite EUR amounts")
        if priority_reserve > total:
            raise ValueError("priority reserve exceeds total")
        with self.database.immediate() as conn:
            conn.execute(
                """INSERT INTO deployment_budget
                (deployment_id, currency, total_allowance, period_allowance, priority_reserve,
                 daily_limit, root_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deployment_id) DO UPDATE SET
                  currency=excluded.currency,
                  total_allowance=excluded.total_allowance,
                  period_allowance=excluded.period_allowance,
                  priority_reserve=excluded.priority_reserve,
                  daily_limit=excluded.daily_limit,
                  root_limit=excluded.root_limit""",
                (
                    deployment_id,
                    currency,
                    canonical_decimal(total),
                    canonical_decimal(period),
                    canonical_decimal(priority_reserve),
                    canonical_decimal(daily),
                    canonical_decimal(root),
                ),
            )
            conn.execute("DELETE FROM role_allocations WHERE deployment_id = ?", (deployment_id,))
            for role, amount in roles.items():
                conn.execute(
                    "INSERT INTO role_allocations (deployment_id, role, amount) VALUES (?, ?, ?)",
                    (deployment_id, role, canonical_decimal(amount)),
                )

    def allowance(self, deployment_id: str) -> Decimal:
        row = self._budget(deployment_id)
        return Decimal(row["total_allowance"])

    def seed_card(self, card: PriceCard) -> None:
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            existing = conn.execute("SELECT document_json FROM price_cards WHERE price_card_id = ?",
                                    (card.price_card_id,)).fetchone()
            if existing is not None:
                if PriceCard.model_validate_json(existing["document_json"]) != card:
                    raise ValueError("price-card IDs are immutable; use a new revision")
                return
            conn.execute(
                """INSERT INTO price_cards (price_card_id, document_json, created_at)
                VALUES (?, ?, ?)""",
                (card.price_card_id, card.model_dump_json(), now),
            )

    def card(self, price_card_id: str) -> PriceCard:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT document_json FROM price_cards WHERE price_card_id = ?",
                (price_card_id,),
            ).fetchone()
        if row is None:
            raise NotFound(price_card_id)
        return PriceCard.model_validate_json(row["document_json"])

    @atomic
    def reserve(
        self,
        *,
        deployment_id: str,
        role: str,
        task_id: str | None,
        root_task_id: str | None,
        price_card_id: str,
        max_input: int,
        max_output: int,
        max_tools: int,
        fx_rate: Decimal,
        fx_buffer: Decimal,
        priority: bool,
        synthetic: bool,
        purpose: str,
    ) -> str:
        card = self.card(price_card_id)
        native = worst_case_cost(card, max_input, max_output, max_tools)
        if not fx_rate.is_finite() or fx_rate <= 0 or not fx_buffer.is_finite() or fx_buffer < 1:
            raise ValueError("FX must be positive and the reservation buffer at least one")
        amount = native * (Decimal("1") if card.currency == "EUR" else fx_rate) * fx_buffer
        reservation_id = str(uuid.uuid4())
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            if not synthetic:
                self._assert_room(
                    conn,
                    deployment_id=deployment_id,
                    role=role,
                    root_task_id=root_task_id,
                    amount=amount,
                    priority=priority,
                    now=now,
                )
            conn.execute(
                """INSERT INTO budget_reservations
                (reservation_id, deployment_id, role, task_id, root_task_id, amount, currency,
                 state, price_card_id, purpose, synthetic, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?, ?, ?, ?, ?)""",
                (
                    reservation_id,
                    deployment_id,
                    role,
                    task_id,
                    root_task_id,
                    canonical_decimal(amount),
                    "EUR",
                    price_card_id,
                    purpose,
                    1 if synthetic else 0,
                    now,
                    now,
                ),
            )
        return reservation_id

    @atomic
    def commit(
        self,
        reservation_id: str,
        usage: ModelUsage,
        *,
        provider: str,
        model: str,
        fx_rate: Decimal,
    ) -> str:
        row = self._reservation(reservation_id)
        card = self.card(row["price_card_id"])
        if not fx_rate.is_finite() or fx_rate <= 0:
            raise ValueError("FX must be positive")
        native = usage_cost(card, usage)
        reporting = native if card.currency == "EUR" else native * fx_rate
        existing = self.database.execute("SELECT * FROM usage_receipts WHERE reservation_id = ?",
                                         (reservation_id,)).fetchone()
        if existing is not None:
            if (existing["status"] == "committed" and existing["provider"] == provider
                    and existing["model"] == model and existing["usage_json"] == usage.model_dump_json()
                    and Decimal(existing["reporting_cost"]) == reporting):
                return existing["receipt_id"]
            raise ValueError("receipt conflict requires explicit reconciliation")
        if row["state"] not in {"RESERVED", "UNCERTAIN"}:
            raise ValueError("reservation is already settled")
        now = utc_iso(self.clock.now())
        receipt_id = str(uuid.uuid4())
        with self.database.immediate() as conn:
            conn.execute(
                """UPDATE budget_reservations
                SET amount = ?, state = 'COMMITTED', updated_at = ? WHERE reservation_id = ?""",
                (canonical_decimal(reporting), now, reservation_id),
            )
            conn.execute(
                """INSERT INTO usage_receipts
                (receipt_id, reservation_id, provider, provider_request_id, model, native_cost,
                 native_currency, reporting_cost, reporting_currency, status, usage_json, synthetic, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EUR', 'committed', ?, ?, ?)""",
                (
                    receipt_id,
                    reservation_id,
                    provider,
                    usage.provider_request_id,
                    model,
                    canonical_decimal(native),
                    card.currency,
                    canonical_decimal(reporting),
                    usage.model_dump_json(),
                    row["synthetic"],
                    now,
                ),
            )
        return receipt_id

    @atomic
    def mark_uncertain(self, reservation_id: str) -> None:
        now = utc_iso(self.clock.now())
        with self.database.immediate() as conn:
            conn.execute(
                """UPDATE budget_reservations SET state = 'UNCERTAIN', updated_at = ?
                WHERE reservation_id = ? AND state = 'RESERVED'""",
                (now, reservation_id),
            )

    @atomic
    def conservative_charge(self, reservation_id: str) -> str:
        row = self._reservation(reservation_id)
        existing = self.database.execute("SELECT receipt_id FROM usage_receipts WHERE reservation_id = ?",
                                         (reservation_id,)).fetchone()
        if existing is not None:
            return existing["receipt_id"]
        if row["state"] not in {"RESERVED", "UNCERTAIN"}:
            raise ValueError("reservation is already settled")
        now = utc_iso(self.clock.now())
        receipt_id = str(uuid.uuid4())
        with self.database.immediate() as conn:
            conn.execute(
                """UPDATE budget_reservations SET state = 'CONSERVATIVE', updated_at = ?
                WHERE reservation_id = ?""",
                (now, reservation_id),
            )
            conn.execute(
                """INSERT INTO usage_receipts
                (receipt_id, reservation_id, provider, provider_request_id, model, native_cost,
                 native_currency, reporting_cost, reporting_currency, status, usage_json, synthetic, created_at)
                VALUES (?, ?, 'unresolved', ?, 'unresolved', ?, 'EUR', ?, 'EUR', 'conservative_charge', ?, ?, ?)""",
                (
                    receipt_id,
                    reservation_id,
                    f"conservative:{reservation_id}",
                    row["amount"],
                    row["amount"],
                    json.dumps({"label": "unresolved"}),
                    row["synthetic"],
                    now,
                ),
            )
        return receipt_id

    def remaining(self, deployment_id: str) -> Decimal:
        row = self._budget(deployment_id)
        with self.database.connect() as conn:
            spent = self._sum(conn, deployment_id, synthetic=0)
        return Decimal(row["total_allowance"]) - spent

    @atomic
    def allocate(self, receipt_id: str, weights: dict[str, Decimal]) -> None:
        if any(not weight.is_finite() or weight < 0 for weight in weights.values()):
            raise ValueError("allocation weights must be nonnegative and finite")
        if sum(weights.values(), Decimal("0")) != Decimal("1"):
            raise ValueError("allocation weights must sum to 1")
        row = self.database.execute(
            "SELECT reporting_cost FROM usage_receipts WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise NotFound(receipt_id)
        previous = self.database.execute("SELECT portfolio_id, weight FROM cost_allocations WHERE receipt_id = ?",
                                         (receipt_id,)).fetchall()
        if previous:
            if {item["portfolio_id"]: Decimal(item["weight"]) for item in previous} == weights:
                return
            raise ValueError("receipt is already allocated")
        total = Decimal(row["reporting_cost"])
        with self.database.immediate() as conn:
            for portfolio_id, weight in weights.items():
                conn.execute(
                    """INSERT INTO cost_allocations (allocation_id, receipt_id, portfolio_id, weight, amount)
                    VALUES (?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        receipt_id,
                        portfolio_id,
                        canonical_decimal(weight),
                        canonical_decimal(total * weight),
                    ),
                )

    def allocated_total(self, receipt_id: str) -> Decimal:
        rows = self.database.execute(
            "SELECT amount FROM cost_allocations WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchall()
        return sum((Decimal(row["amount"]) for row in rows), Decimal("0"))

    def reconcile_invoice(self, deployment_id: str, invoice_total: Decimal) -> Decimal:
        rows = self.database.execute(
            """SELECT reporting_cost FROM usage_receipts r
            JOIN budget_reservations b ON b.reservation_id = r.reservation_id
            WHERE b.deployment_id = ? AND r.synthetic = 0 AND r.status != 'uncertain'""",
            (deployment_id,),
        ).fetchall()
        recorded = sum((Decimal(row["reporting_cost"]) for row in rows), Decimal("0"))
        return invoice_total - recorded

    def _assert_room(
        self,
        conn,
        *,
        deployment_id: str,
        role: str,
        root_task_id: str | None,
        amount: Decimal,
        priority: bool,
        now: str,
    ) -> None:
        budget = conn.execute(
            "SELECT * FROM deployment_budget WHERE deployment_id = ?",
            (deployment_id,),
        ).fetchone()
        if budget is None:
            raise BudgetExhausted("no allowance")
        total = Decimal(budget["total_allowance"])
        priority_reserve = Decimal(budget["priority_reserve"])
        spent = self._sum(conn, deployment_id, synthetic=0)
        room = total - spent - (Decimal("0") if priority else priority_reserve)
        period_spent = self._sum(conn, deployment_id, synthetic=0, prefix=now[:7])
        daily_spent = self._sum(conn, deployment_id, synthetic=0, prefix=now[:10])
        room = min(room, Decimal(budget["period_allowance"]) - period_spent)
        room = min(room, Decimal(budget["daily_limit"]) - daily_spent)
        if root_task_id:
            root_spent = self._sum(conn, deployment_id, synthetic=0, root_task_id=root_task_id)
            room = min(room, Decimal(budget["root_limit"]) - root_spent)
        role_row = conn.execute(
            "SELECT amount FROM role_allocations WHERE deployment_id = ? AND role = ?",
            (deployment_id, role),
        ).fetchone()
        if role_row is not None:
            role_spent = self._sum(conn, deployment_id, synthetic=0, role=role)
            room = min(room, Decimal(role_row["amount"]) - role_spent)
        if amount > room:
            raise BudgetExhausted(f"room {room} requested {amount}")

    def _sum(
        self,
        conn,
        deployment_id: str,
        *,
        synthetic: int,
        prefix: str | None = None,
        role: str | None = None,
        root_task_id: str | None = None,
    ) -> Decimal:
        sql = """SELECT amount FROM budget_reservations
            WHERE deployment_id = ? AND synthetic = ? AND state IN ({})""".format(
            ",".join("?" for _ in OPEN_STATES)
        )
        params: list = [deployment_id, synthetic, *OPEN_STATES]
        if prefix is not None:
            sql += " AND created_at LIKE ?"
            params.append(prefix + "%")
        if role is not None:
            sql += " AND role = ?"
            params.append(role)
        if root_task_id is not None:
            sql += " AND root_task_id = ?"
            params.append(root_task_id)
        rows = conn.execute(sql, params).fetchall()
        return sum((Decimal(row["amount"]) for row in rows), Decimal("0"))

    def _budget(self, deployment_id: str):
        row = self.database.execute(
            "SELECT * FROM deployment_budget WHERE deployment_id = ?",
            (deployment_id,),
        ).fetchone()
        if row is None:
            raise NotFound(deployment_id)
        return row

    def _reservation(self, reservation_id: str):
        row = self.database.execute(
            "SELECT * FROM budget_reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise NotFound(reservation_id)
        return row
