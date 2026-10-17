"""Protective exits for filled day-plan entries (T34, D117): every open position carries one while the Trader sleeps.

``Execution.record_fill`` calls ``after_fill`` inside the transaction that records the fill, so the exit's order
intent (with its reservation and outbox row) is persisted together with the fill that needs it: there is no moment
in the records where a filled part of a day-plan entry has no exit on its way. The management step's ``sweep``
covers what could not be placed then (no quote yet, a pause that removed every order) and is idempotent.

How much an entry still needs protecting is plain arithmetic over persisted records: what it filled, less what its
exits cover. An exit still outstanding, or whose status is unknown, covers its whole quantity, so an unknown status
never leads to a second exit for the same coins (it is reconciled first); a filled exit covers what it sold; an exit
that ended without filling (cancelled by the owner's cancel-all, or refused) covers only what it sold. An exit never
asks to sell more than is held and not already reserved, so spot stays spot (D127).

This module is protected execution control (``kernel/authority.py``): agents cannot edit it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from trade_graph.contracts.models import FillRecord
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.domain.money import canonical_decimal
from trade_graph.domain.precision import floor_to_increment
from trade_graph.kernel.authority import pause_allows_protection

if TYPE_CHECKING:
    from trade_graph.application.execution import Execution

OUTSTANDING = frozenset({"SUBMISSION_PENDING", "SUBMITTING", "OPEN", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_PENDING"})
TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED"})
_ENTRY = """SELECT e.*, p.portfolio_id, p.symbol FROM day_plan_entries e JOIN day_plans p ON p.plan_id = e.plan_id"""
# What a refused placement may raise without the fill itself being lost: the next sweep tries again.
_DEFERRABLE = (TradeGraphError, ValueError, ArithmeticError, sqlite3.IntegrityError)


class DayPlanProtection:
    def __init__(self, execution: Execution) -> None:
        self.execution = execution

    @property
    def database(self):
        return self.execution.database

    def entry_for_intent(self, intent_id: str) -> sqlite3.Row | None:
        return self.database.execute(f"{_ENTRY} WHERE e.intent_id = ?", (intent_id,)).fetchone()

    def exits(self, entry_id: str) -> list[sqlite3.Row]:
        return self.database.execute("SELECT * FROM day_plan_exits WHERE entry_id = ? ORDER BY sequence",
                                     (entry_id,)).fetchall()

    def coverage(self, entry: sqlite3.Row) -> tuple[Decimal, Decimal]:
        """(filled by the entry, covered by its exits), in the coin's own units."""
        execution = self.execution
        filled = execution._filled_quantity(entry["intent_id"])
        covered = Decimal("0")
        for item in self.exits(entry["entry_id"]):
            state = execution.intent_state(item["intent_id"])
            if state in OUTSTANDING or state == "FILLED":
                covered += Decimal(item["quantity"])
            else:
                covered += execution._filled_quantity(item["intent_id"])
        return filled, covered

    def after_fill(self, fill: FillRecord) -> str | None:
        """Attach the exit for a day-plan entry fill; called inside the fill's own transaction."""
        if fill.intent_id is None or fill.side != "buy":
            return None
        entry = self.entry_for_intent(fill.intent_id)
        if entry is None:
            return None
        return self.protect(entry, cause="fill", fill_trade_id=fill.trade_id)

    def protect(self, entry: sqlite3.Row, *, cause: str, fill_trade_id: str | None = None) -> str | None:
        """Place one exit for whatever part of ``entry`` is filled and not yet covered; None when nothing is due."""
        execution = self.execution
        portfolio_id, symbol = entry["portfolio_id"], entry["symbol"]
        if not pause_allows_protection(execution.profile(portfolio_id)):  # type: ignore[arg-type]
            return None
        filled, covered = self.coverage(entry)
        exposed = filled - covered
        if exposed <= 0:
            return None
        try:
            rules = execution.instrument("paper", symbol)
            available = (execution.owned_quantity(portfolio_id, rules.base_asset)
                         - execution._reserved(portfolio_id, rules.base_asset))
            wanted = min(exposed, available)
            quantity = floor_to_increment(wanted, rules.quantity_increment) if wanted > 0 else Decimal("0")
            if quantity <= 0:
                return None  # nothing held and unreserved to protect: the coins were sold another way
            take_profit = None if entry["take_profit_price"] is None else Decimal(entry["take_profit_price"])
            with self.database.immediate() as connection:
                intent_id = execution.place_protection(
                    portfolio_id, symbol, quantity, Decimal(entry["stop_price"]), f"day-plan:{entry['plan_id']}",
                    take_profit_price=take_profit,
                    protective={"plan_id": entry["plan_id"], "entry_id": entry["entry_id"]},
                )
                connection.execute(
                    """INSERT INTO day_plan_exits (exit_id, entry_id, intent_id, quantity, stop_price,
                       take_profit_price, cause, fill_trade_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"dx-{uuid.uuid4().hex}", entry["entry_id"], intent_id, canonical_decimal(quantity),
                     entry["stop_price"], entry["take_profit_price"], cause, fill_trade_id,
                     utc_iso(execution.clock.now())),
                )
        except _DEFERRABLE as error:
            self._deferred(entry, error, exposed=exposed)
            return None
        return intent_id

    def sweep(self, portfolio_id: str) -> list[str]:
        """Protect every filled, uncovered day-plan entry of the portfolio's held coins. Returns the new exits."""
        execution = self.execution
        if not pause_allows_protection(execution.profile(portfolio_id)):  # type: ignore[arg-type]
            return []
        placed: list[str] = []
        held: dict[str, bool] = {}
        with self.database.immediate():
            rows = self.database.execute(f"{_ENTRY} WHERE p.portfolio_id = ? ORDER BY e.sequence",
                                         (portfolio_id,)).fetchall()
            for entry in rows:
                symbol = entry["symbol"]
                if symbol not in held:
                    base = symbol.split("/", 1)[0]
                    held[symbol] = execution.owned_quantity(portfolio_id, base) > 0
                if not held[symbol]:
                    continue
                intent_id = self.protect(entry, cause="sweep")
                if intent_id is not None:
                    placed.append(intent_id)
        return placed

    def _deferred(self, entry: sqlite3.Row, error: Exception, *, exposed: Decimal) -> None:
        """Say once, per reason and per number of exits, that a filled part waits for its exit."""
        reason = f"{type(error).__name__}: {error}"[:500]
        exits = len(self.exits(entry["entry_id"]))
        latest = self.database.execute(
            "SELECT kind, reason, document_json FROM day_plan_events WHERE entry_id = ? ORDER BY sequence DESC "
            "LIMIT 1", (entry["entry_id"],)).fetchone()
        if (latest is not None and latest["kind"] == "protection_deferred" and latest["reason"] == reason
                and json.loads(latest["document_json"]).get("exits") == exits):
            return
        document: dict[str, Any] = {"exits": exits, "exposed": canonical_decimal(exposed)}
        with self.database.immediate() as connection:
            connection.execute(
                "INSERT INTO day_plan_events (plan_id, entry_id, kind, reason, document_json, created_at) "
                "VALUES (?, ?, 'protection_deferred', ?, ?, ?)",
                (entry["plan_id"], entry["entry_id"], reason, json.dumps(document, sort_keys=True),
                 utc_iso(self.execution.clock.now())))


__all__ = ["OUTSTANDING", "TERMINAL", "DayPlanProtection"]
