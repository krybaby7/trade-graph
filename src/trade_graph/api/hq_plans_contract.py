"""The HQ day plans document, ``trade-graph.hq.plans.v1`` (W8-C, D251).

``GET /api/v1/hq/plans`` returns one of these; the page (``web/static/hq-plans.js``) only lays it out. It is served on
its own, never inside the HQ snapshot, so the snapshot's 80 KB budget is unchanged (as the price charts and the wake
log are, D197 and D215). It holds, for the paper portfolio:

- ``plans``: each coin's newest day plan of today (W4-A, T34) and any older plan that still has an order open (a
  safety exit still protecting coins), newest first: its waiting buys (resting limit entries), what each filled, the
  safety exits software attached to every filled part and what they sold, how much of a filled part has no safety
  exit right now, the plan's events (put in place, replaced, a buy cancelled and why, a safety exit that couldn't be
  placed yet) and the Trader's rationale and invalidation as labelled quotes;
- ``refusals``: today's plans software refused (``day_plan_refused``), with the reason in plain words.

The wording uses the HQ snapshot's ``Text`` segments (``api/hq_contract.py``), so times read in the viewer's own time
zone and every word comes from the server. Prices and quantities are exact decimal text. The list bounds below are
document bounds, like a page size: they never limit how many plans, entries, fills or refusals there are (D116);
totals are exact and a list says how many more there are.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from trade_graph.api.hq_contract import (
    ISO,
    Dec,
    Identifier,
    NonEmptyText,
    NonNegative,
    Quote,
    Text,
    _Contract,
)
from trade_graph.api.hq_contract import contract_keys as _contract_keys
from trade_graph.contracts.alarms import SYMBOL_PATTERN

SCHEMA: Final = "trade-graph.hq.plans.v1"
PLANS_MAX: Final = 8  # plans written out, newest first; ``more_plans`` counts the rest
ENTRIES_MAX: Final = 16  # a day plan's own bound (contracts/day_plans.py)
EXITS_MAX: Final = 4  # safety exits written out per entry; ``more_exits`` counts the rest
FILLS_MAX: Final = 4  # fills written out per order; ``more_fills`` counts the rest
EVENTS_MAX: Final = 8  # plan events written out; ``more_events`` counts the rest
REFUSALS_MAX: Final = 10  # today's refusals written out; ``refusals_total`` counts all of them
# The HQ snapshot's budget, reused as this endpoint's own ceiling on a busy synthetic day.
PAYLOAD_BUDGET_BYTES: Final = 80 * 1024

Symbol = Annotated[str, Field(pattern=SYMBOL_PATTERN)]
# An order intent's state in words: SUBMISSION_PENDING and SUBMITTING are "sending"; UNKNOWN and CANCEL_PENDING are
# "being_checked" (an unknown status is never read as refused or cancelled).
OrderStatus = Literal["sending", "waiting", "partly_filled", "filled", "cancelled", "rejected", "being_checked"]
ORDER_STATUSES: Final = ("sending", "waiting", "partly_filled", "filled", "cancelled", "rejected", "being_checked")
PlanTone = Literal["good", "neutral", "attention"]


def _number(value: str) -> Decimal:
    return Decimal(value)


class ExitView(_Contract):
    """One safety exit: a sell stop with an optional target leg, one cancelling the other (D188)."""

    id: Identifier
    status: OrderStatus
    tone: PlanTone
    text: NonEmptyText
    fills: Annotated[list[NonEmptyText], Field(max_length=FILLS_MAX)]
    more_fills: NonNegative


class EntryView(_Contract):
    """One waiting buy of a plan (a resting limit buy below the market) and what became of it."""

    id: Identifier
    status: OrderStatus
    tone: PlanTone
    text: NonEmptyText
    quantity: Dec
    filled: Dec
    unprotected: Dec  # filled and not covered by a safety exit right now ("0" when everything filled is covered)
    limit_price: Dec
    stop_price: Dec
    take_profit_price: Dec | None
    expires_at: ISO
    fills: Annotated[list[NonEmptyText], Field(max_length=FILLS_MAX)]
    more_fills: NonNegative
    exits: Annotated[list[ExitView], Field(max_length=EXITS_MAX)]
    more_exits: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        quantity, filled, unprotected = (_number(value) for value in (self.quantity, self.filled, self.unprotected))
        if not Decimal("0") <= filled <= quantity or not Decimal("0") <= unprotected <= filled:
            raise ValueError("an entry fills at most its quantity, and only what filled can be unprotected")
        if unprotected > 0 and self.tone != "attention":
            raise ValueError("an unprotected filled part needs attention")
        if self.status == "filled" and filled != quantity:
            raise ValueError("a filled entry filled all of its quantity")
        if not _number(self.stop_price) < _number(self.limit_price):
            raise ValueError("a safety exit's stop is below the buy")
        if self.take_profit_price is not None and not _number(self.take_profit_price) > _number(self.limit_price):
            raise ValueError("a target is above the buy")
        if len({item.id for item in self.exits}) != len(self.exits):
            raise ValueError("exit ids are unique")
        return self


class PlanView(_Contract):
    id: Identifier
    symbol: Symbol
    installed_at: ISO
    day_start: ISO
    day_end: ISO
    current: bool  # its trading day has not ended and no newer plan replaced it
    replaced_today: NonNegative  # earlier plans of this coin that were replaced on the same trading day
    headline: NonEmptyText
    strategy: NonEmptyText
    watching: Text  # the indicators and alarms the plan set, in words
    quote: Quote | None  # the Trader's rationale, in its words
    invalidation: Quote | None  # what the Trader said would prove the plan wrong, in its words
    entries: Annotated[list[EntryView], Field(max_length=ENTRIES_MAX)]
    events: Annotated[list[NonEmptyText], Field(max_length=EVENTS_MAX)]
    more_events: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        if not self.day_start <= self.installed_at < self.day_end:
            raise ValueError("a plan is put in place inside its trading day")
        if len({item.id for item in self.entries}) != len(self.entries):
            raise ValueError("entry ids are unique")
        return self


class RefusalView(_Contract):
    at: ISO
    symbol: Symbol | None
    text: NonEmptyText
    technical: str | None  # the recorded reason, shown only with "Show technical names"


class PlansDay(_Contract):
    start: ISO
    end: ISO

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("a trading day ends after it starts")
        return self


class PlansDocument(_Contract):
    schema_: Literal["trade-graph.hq.plans.v1"] = Field(alias="schema")
    as_of: ISO
    available: bool  # False on a database from before the day-plan records (migration 0034)
    status_text: Text | None
    day: PlansDay | None
    limits_recorded: bool | None  # whether the owner's money limits are recorded (day plans are off until then)
    headline: NonEmptyText
    plans: Annotated[list[PlanView], Field(max_length=PLANS_MAX)]
    more_plans: NonNegative
    refusals: Annotated[list[RefusalView], Field(max_length=REFUSALS_MAX)]
    refusals_total: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        if len({item.id for item in self.plans}) != len(self.plans):
            raise ValueError("plan ids are unique")
        if len(self.refusals) > self.refusals_total:
            raise ValueError("the total counts every listed refusal")
        moments = [item.at for item in self.refusals]
        if moments != sorted(moments, reverse=True):
            raise ValueError("refusals are newest first")
        if not self.available and (self.plans or self.refusals or self.refusals_total or self.more_plans
                                   or self.day is not None):
            raise ValueError("without day-plan records there is nothing to list")
        if self.available and self.day is None:
            raise ValueError("an available document names its trading day")
        return self


def validate_plans(document: Mapping[str, Any]) -> PlansDocument:
    """Validate a day plans document (raises ``pydantic.ValidationError``)."""
    return PlansDocument.model_validate(document)


def contract_keys() -> set[str]:
    """Every JSON key the document can emit, for redaction-safety checks."""
    return _contract_keys(PlansDocument)


__all__ = ["ENTRIES_MAX", "EVENTS_MAX", "EXITS_MAX", "FILLS_MAX", "ORDER_STATUSES", "PAYLOAD_BUDGET_BYTES",
           "PLANS_MAX", "REFUSALS_MAX", "SCHEMA", "PlansDocument", "contract_keys", "validate_plans"]
