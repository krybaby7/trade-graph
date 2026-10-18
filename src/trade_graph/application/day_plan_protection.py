"""Protective exits for filled day-plan entries (T34, D117): every open position carries one while the Trader sleeps.

``Execution.record_fill`` calls ``after_fill`` inside the transaction that records the fill, so the exit's order
intent (with its reservation and outbox row) is persisted together with the fill that needs it: there is no moment
in the records where a filled part of a day-plan entry has no exit on its way. The management step's ``sweep``
covers what could not be placed then (no quote yet, a pause that removed every order) and is idempotent.

How much an entry still needs protecting is plain arithmetic over persisted records: what it filled, less what its
exits cover. An exit still outstanding, or whose status is unknown, covers its whole quantity, so an unknown status
never leads to a second exit for the same coins (it is reconciled first); a filled exit covers what it sold; an exit
that ended without filling (cancelled by the owner's cancel-all, or refused) covers only what it sold. The Trader's
close (W8-B) counts the same way: its market sell covers each entry's part while it is outstanding, unknown or
filled, and only what it sold once it ended unfilled. An exit never asks to sell more than is held and not already
reserved, so spot stays spot (D127). The arithmetic itself is ``kernel/day_plan_coverage.py``, shared with HQ's day
plans panel and its "no safety exit" card (W10-C), so the three never disagree.

Terms (W8-B, D246): a fill's exit uses the entry's own stop and take-profit (the bracket the Trader wrote). When the
Trader moved the position's exit, re-protecting that entry later (the sweep, after the owner's cancel-all) uses the
newest moved terms; a move the broker refused never becomes the terms in force.

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
from trade_graph.domain.money import canonical_decimal
from trade_graph.domain.precision import floor_to_increment
from trade_graph.kernel import day_plan_coverage
from trade_graph.kernel.authority import pause_allows_protection
from trade_graph.kernel.day_plan_coverage import OUTSTANDING, Cover

if TYPE_CHECKING:
    from trade_graph.application.execution import Execution

TERMINAL = frozenset({"FILLED", "CANCELLED", "REJECTED"})
ACTION_TABLE = "day_plan_action_orders"  # migration 0044 (W8-B): the Trader's closes and moved exits
_ENTRY = """SELECT e.*, p.portfolio_id, p.symbol FROM day_plan_entries e JOIN day_plans p ON p.plan_id = e.plan_id"""
# A placement that fails for any reason is recorded and retried by the next sweep: recording the fill that needs the
# exit must never fail because the exit could not be placed yet.
_DEFERRABLE = (Exception,)


class DayPlanProtection:
    def __init__(self, execution: Execution) -> None:
        self.execution = execution

    @property
    def database(self):
        return self.execution.database

    def available(self) -> bool:
        """A database still on a schema before 0034 (a legacy upgrade in progress) has no day plans."""
        return self.database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'day_plan_exits'").fetchone() is not None

    def entry_for_intent(self, intent_id: str) -> sqlite3.Row | None:
        return self.database.execute(f"{_ENTRY} WHERE e.intent_id = ?", (intent_id,)).fetchone()

    def exits(self, entry_id: str) -> list[sqlite3.Row]:
        return self.database.execute("SELECT * FROM day_plan_exits WHERE entry_id = ? ORDER BY sequence",
                                     (entry_id,)).fetchall()

    def actions_available(self) -> bool:
        """A database before migration 0044 has no Trader actions (W8-B): nothing of them is counted."""
        return self.database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (ACTION_TABLE,)).fetchone() is not None

    def coverage(self, entry: sqlite3.Row) -> tuple[Decimal, Decimal]:
        """(filled by the entry, covered by its exits and the Trader's close), in the coin's own units.

        The arithmetic is ``kernel/day_plan_coverage.py``'s, shared with HQ (W10-C); what an order sold is read only
        when the rule needs it (an order that ended unsold).
        """
        execution = self.execution
        filled = execution._filled_quantity(entry["intent_id"])
        orders = []
        for item in self.exits(entry["entry_id"]):
            state = execution.intent_state(item["intent_id"])
            whole = day_plan_coverage.counts_whole(state)
            sold = Decimal("0") if whole else execution._filled_quantity(item["intent_id"])
            orders.append(Cover(state, Decimal(item["quantity"]), sold))
        if self.actions_available():
            for part in self.database.execute(
                    f"SELECT * FROM {ACTION_TABLE} WHERE entry_id = ? AND role = 'sell' ORDER BY sequence",
                    (entry["entry_id"],)).fetchall():
                state = execution.intent_state(part["intent_id"])
                sold = Decimal("0") if day_plan_coverage.counts_whole(state) else self._sold_part(part)
                orders.append(Cover(state, Decimal(part["quantity"]), sold))
        return filled, day_plan_coverage.covered(orders)

    def _sold_part(self, part: sqlite3.Row) -> Decimal:
        """What an ended close sell sold for this part: its fills shared out over its parts in their order."""
        parts = [(item["sequence"], Decimal(item["quantity"])) for item in self.database.execute(
            f"SELECT sequence, quantity FROM {ACTION_TABLE} WHERE intent_id = ? AND role = 'sell' ORDER BY sequence",
            (part["intent_id"],)).fetchall()]
        return day_plan_coverage.shared_part(self.execution._filled_quantity(part["intent_id"]), parts,
                                             part["sequence"])

    def terms(self, entry: sqlite3.Row) -> tuple[Decimal, Decimal | None]:
        """The exit terms in force for re-protecting ``entry``: its newest exit the Trader moved (W8-B), else its own.

        Only a move that ended ``done`` counts, so a move the broker refused never becomes the terms in force.
        """
        if self.actions_available():
            row = self.database.execute(
                f"""SELECT x.stop_price, x.take_profit_price FROM {ACTION_TABLE} o
                    JOIN day_plan_exits x ON x.intent_id = o.intent_id
                    JOIN day_plan_action_steps s ON s.action_id = o.action_id AND s.kind = 'done'
                    WHERE o.entry_id = ? AND o.role = 'moved_exit' ORDER BY o.sequence DESC LIMIT 1""",
                (entry["entry_id"],)).fetchone()
            if row is not None:
                return Decimal(row["stop_price"]), (None if row["take_profit_price"] is None
                                                    else Decimal(row["take_profit_price"]))
        return self.own_terms(entry)

    @staticmethod
    def own_terms(entry: sqlite3.Row) -> tuple[Decimal, Decimal | None]:
        """The stop and take-profit the Trader wrote for the entry (its bracket)."""
        target = entry["take_profit_price"]
        return Decimal(entry["stop_price"]), None if target is None else Decimal(target)

    def after_fill(self, fill: FillRecord) -> str | None:
        """Attach the exit for a day-plan entry fill; called inside the fill's own transaction."""
        if fill.intent_id is None or fill.side != "buy" or not self.available():
            return None
        entry = self.entry_for_intent(fill.intent_id)
        if entry is None:
            return None
        return self.protect(entry, cause="fill", fill_trade_id=fill.trade_id)

    def protect(self, entry: sqlite3.Row, *, cause: str, fill_trade_id: str | None = None) -> str | None:
        """Place one exit for whatever part of ``entry`` is filled and not yet covered; None when nothing is due.

        A fill's exit uses the entry's own terms; the sweep uses the terms in force (a move by the Trader, W8-B).
        """
        execution = self.execution
        if not pause_allows_protection(execution.profile(entry["portfolio_id"])):  # type: ignore[arg-type]
            return None
        filled, covered = self.coverage(entry)
        exposed = filled - covered
        if exposed <= 0:
            return None
        try:
            stop, take_profit = self.own_terms(entry) if cause == "fill" else self.terms(entry)
            placed = self.place(entry, cause=cause, stop_price=stop, take_profit_price=take_profit,
                                fill_trade_id=fill_trade_id)
        except _DEFERRABLE as error:
            self._deferred(entry, error, exposed=exposed)
            return None
        return None if placed is None else placed[0]

    def place(self, entry: sqlite3.Row, *, cause: str, stop_price: Decimal, take_profit_price: Decimal | None,
              limit: Decimal | None = None, fill_trade_id: str | None = None) -> tuple[str, Decimal] | None:
        """Place one exit at these terms for the entry's filled, uncovered part (at most ``limit``).

        Returns ``(intent_id, quantity)``, or None when nothing is due or nothing is held and unreserved; raises when
        the exit cannot be placed. It does not look at the pause: callers decide (``protect`` and W8-B's actions).
        """
        execution = self.execution
        portfolio_id, symbol = entry["portfolio_id"], entry["symbol"]
        filled, covered = self.coverage(entry)
        exposed = filled - covered if limit is None else min(filled - covered, limit)
        if exposed <= 0:
            return None
        rules = execution.instrument("paper", symbol)
        available = (execution.owned_quantity(portfolio_id, rules.base_asset)
                     - execution._reserved(portfolio_id, rules.base_asset))
        wanted = min(exposed, available)
        quantity = floor_to_increment(wanted, rules.quantity_increment) if wanted > 0 else Decimal("0")
        if quantity <= 0:
            return None  # nothing held and unreserved to protect: the coins were sold another way
        with self.database.immediate() as connection:
            intent_id = execution.place_protection(
                portfolio_id, symbol, quantity, stop_price, f"day-plan:{entry['plan_id']}",
                take_profit_price=take_profit_price,
                protective={"plan_id": entry["plan_id"], "entry_id": entry["entry_id"]},
            )
            connection.execute(
                """INSERT INTO day_plan_exits (exit_id, entry_id, intent_id, quantity, stop_price,
                   take_profit_price, cause, fill_trade_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"dx-{uuid.uuid4().hex}", entry["entry_id"], intent_id, canonical_decimal(quantity),
                 canonical_decimal(stop_price),
                 None if take_profit_price is None else canonical_decimal(take_profit_price), cause, fill_trade_id,
                 utc_iso(execution.clock.now())),
            )
        return intent_id, quantity

    def sweep(self, portfolio_id: str) -> list[str]:
        """Protect every filled, uncovered day-plan entry of the portfolio's held coins. Returns the new exits."""
        execution = self.execution
        if not self.available() or not pause_allows_protection(execution.profile(portfolio_id)):  # type: ignore[arg-type]
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


__all__ = ["ACTION_TABLE", "OUTSTANDING", "TERMINAL", "DayPlanProtection"]
