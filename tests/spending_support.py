"""Synthetic AI spending records for the T40 tests, made through the real ``BudgetGateway``.

Every amount here is made up: no provider was called. Reservations, receipts, uncertain calls and conservative
charges are written exactly as the Perplexity research round (T36) and the Jev news alarm (T44) write them, so
the spending read model is tested against the shapes those packages really store.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.jev_checks import PURPOSE as JEV_PURPOSE
from trade_graph.application.jev_checks import ROLE as JEV_ROLE
from trade_graph.application.web_research import AGENT_CARD_ID, SEARCH_CARD_ID, load_price_cards
from trade_graph.contracts.models import ModelUsage
from trade_graph.domain.clock import utc_iso

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "config" / "perplexity-price-cards.json"
FX = Decimal("0.9")
BUFFER = Decimal("1.02")
DEPLOYMENT = "deployment"
_IDS = itertools.count(1)


class Spending:
    """Writes Perplexity, Jev and Codex records into a state database at the clock's time."""

    def __init__(self, database, clock) -> None:
        self.database, self.clock = database, clock
        self.budget = BudgetGateway(database, clock)
        for card in load_price_cards(CARDS):
            self.budget.seed_card(card)
        self.budget.seed_card(JEV_PRICE_CARD)
        big = Decimal("100000")
        self.budget.configure(deployment_id=DEPLOYMENT, currency="EUR", total=big, period=big,
                              priority_reserve=Decimal("0"), daily=big, root=big, roles={})

    # --- Perplexity and Jev calls -----------------------------------------------------------------

    def reserve(self, role: str = "research", *, service: str = "perplexity", search: bool = False,
                synthetic: bool = False, max_input: int = 4000, max_output: int = 1000) -> str:
        if service == "jev":
            card, role, purpose, max_input, max_output, tools = (JEV_PRICE_CARD.price_card_id, JEV_ROLE,
                                                                 JEV_PURPOSE, 64000, 1024, 0)
        elif search:
            card, purpose, max_input, max_output, tools = SEARCH_CARD_ID, f"{role}:web_search", 0, 0, 1
        else:
            card, purpose, tools = AGENT_CARD_ID, role, 2
        return self.budget.reserve(
            deployment_id=DEPLOYMENT, role=role, task_id=None, root_task_id=None, price_card_id=card,
            max_input=max_input, max_output=max_output, max_tools=tools, fx_rate=FX, fx_buffer=BUFFER,
            priority=False, synthetic=synthetic, purpose=purpose)

    def commit(self, reservation: str, *, input_tokens: int = 1000, output_tokens: int = 200,
               tools: int = 0) -> str:
        row = self.database.execute("SELECT price_card_id FROM budget_reservations WHERE reservation_id=?",
                                    (reservation,)).fetchone()
        card = self.budget.card(row["price_card_id"])
        usage = ModelUsage(uncached_input_tokens=input_tokens, billed_output_tokens=output_tokens, tool_units=tools,
                           provider_request_id=f"synthetic-request-{next(_IDS)}")
        return self.budget.commit(reservation, usage, provider=card.provider, model=card.model, fx_rate=FX)

    def call(self, role: str = "research", outcome: str = "committed", *, service: str = "perplexity",
             search: bool = False, synthetic: bool = False, **usage) -> str:
        """One call with the given outcome: committed, uncertain, conservative or reserved (still open)."""
        reservation = self.reserve(role, service=service, search=search, synthetic=synthetic)
        if outcome == "committed":
            if search:
                usage = {"input_tokens": 0, "output_tokens": 0, "tools": 1, **usage}
            self.commit(reservation, **usage)
        elif outcome == "uncertain":
            self.budget.mark_uncertain(reservation)
        elif outcome == "conservative":
            self.budget.mark_uncertain(reservation)
            self.budget.conservative_charge(reservation)
        elif outcome != "reserved":
            raise ValueError(outcome)
        return reservation

    # --- Owner policy and records ---------------------------------------------------------------

    def run(self, mode: str | None, *, limit: str | None = None, role: str = "research",
            at: datetime | None = None) -> None:
        """A Perplexity round's start row, which records the owner policy it ran under."""
        policy = None
        if mode == "unlimited_testing":
            policy = {"revision_id": "owner-policy-test", "mode": mode, "monthly_limit_usd": None,
                      "decision_ref": "D125", "set_by": "owner"}
        elif mode == "monthly_limit":
            policy = {"revision_id": "owner-policy-limit", "mode": mode, "monthly_limit_usd": limit,
                      "decision_ref": "D138", "set_by": "owner"}
        number = next(_IDS)
        self.database.execute(
            """INSERT INTO web_research_runs (run_id, deployment_id, portfolio_id, role, task_id, root_task_id,
            system_version_id, provider, model, agent_price_card_id, search_price_card_id, policy_json, question,
            started_at) VALUES (?, ?, 'paper', ?, ?, ?, 'v-test', 'perplexity', 'anthropic/claude-sonnet-5-5', ?, ?,
            ?, 'Synthetic question?', ?)""",
            (f"run-{number}", DEPLOYMENT, role, f"task-{number}", f"task-{number}", AGENT_CARD_ID, SEARCH_CARD_ID,
             json.dumps(policy), utc_iso(at or self.clock.now())))

    def record(self, service: str, kind: str, amount: str, *, observed_at: datetime | None = None) -> None:
        self.database.execute(
            """INSERT INTO ai_spending_owner_records (record_id, service, kind, amount_usd, observed_at, set_by,
            created_at) VALUES (?, ?, ?, ?, ?, 'owner', ?)""",
            (f"record-{next(_IDS)}", service, kind, amount, utc_iso(observed_at or self.clock.now()),
             utc_iso(self.clock.now())))

    # --- Codex subscription turns --------------------------------------------------------------

    def codex_turn(self, role: str, *, used: int | None = 1000, synthetic: bool = False, state: str = "COMPLETED",
                   ago: timedelta = timedelta(hours=1)) -> None:
        number = next(_IDS)
        usage = None if used is None else json.dumps({"uncached_input_tokens": used - 100, "cache_read_tokens": 0,
                                                      "cache_write_tokens": 0, "billed_output_tokens": 100,
                                                      "reasoning_tokens": 0, "tool_units": 0,
                                                      "unreported_fields": []})
        at = utc_iso(self.clock.now() - ago)
        self.database.execute(
            """INSERT INTO subscription_invocations (invocation_id, request_hash, task_id, root_task_id, role, run_id,
            system_version_id, provider, requested_model, state, quota_json, usage_json, cost_status, synthetic,
            created_at, updated_at) VALUES (?, 'h', ?, ?, ?, 'run', 'v', 'codex_subscription', 'gpt-codex', ?, '{}',
            ?, ?, ?, ?, ?)""",
            (f"inv-{number}", f"t-{number}", f"t-{number}", role, state, usage,
             "not_incurred" if state == "BLOCKED" else "unknown", int(synthetic), at, at))

    # --- Independent sums for reconciliation checks ----------------------------------------------

    def receipts_usd(self, *, roles: tuple[str, ...] | None = None, synthetic: bool = False) -> Decimal:
        """Committed receipts (USD as priced) plus conservative charges (EUR held / FX), straight from SQL."""
        rows = self.database.execute(
            """SELECT r.status, r.native_cost, r.native_currency, b.role, b.fx_rate_value FROM usage_receipts r
            JOIN budget_reservations b ON b.reservation_id = r.reservation_id WHERE r.synthetic = ?""",
            (int(synthetic),)).fetchall()
        total = Decimal("0")
        for row in rows:
            if roles is not None and row["role"] not in roles:
                continue
            amount = Decimal(row["native_cost"])
            total += amount if row["native_currency"] == "USD" else amount / Decimal(row["fx_rate_value"])
        return total

    def held_usd(self, reservation: str) -> Decimal:
        row = self.database.execute("SELECT amount, fx_rate_value FROM budget_reservations WHERE reservation_id=?",
                                    (reservation,)).fetchone()
        return Decimal(row["amount"]) / Decimal(row["fx_rate_value"])
