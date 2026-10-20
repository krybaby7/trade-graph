"""The HQ Trading desk room, ``trade-graph.hq.desk.v1`` (W13-A, C13; D280, D302).

``GET /api/v1/hq/desk`` returns one of these; the page (``web/static/hq-desk.js``) only lays it out. It is served on
its own, never inside the HQ snapshot, so the snapshot's 80 KB budget is unchanged (as the charts, the wake log and
the day plans are: D197, D215, D251). In plain words it holds, for the paper portfolio:

- ``playbook``: the desk's playbook in force (W12-A, D295): per market type, the strategy versions it means to use or
  "wait", and why, with who set it, when, why it changed and how many revisions there are;
- ``stances``: today's stance per coin (D296) with the market type software showed the desk then and the label now;
- ``lab``: the strategy lab (W11-B): what is queued and running, the newest results with each report's result after
  fees against holding the coin, and the tries per strategy family;
- ``news``: the Leader news the desk read at its last turn (W12-A's ``news_context``): the newest round, the lab
  inbox and Jev's verdicts on its coins;
- ``split``: what each strategy earned in each market type, from the scorecard's split (D300).

Every word comes from the server (``hq_copy.DESK``) in the snapshot's ``Text`` segments; the desk's own words are
labelled quotes. The list bounds below are document bounds, like a page size: they never limit what the desk does,
tries or records (D116); each list says how many more there are.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import Field, model_validator

from trade_graph.api.hq_contract import (
    ISO,
    Currency,
    Identifier,
    Money,
    Name,
    NonEmptyText,
    NonNegative,
    Positive,
    Quote,
    Text,
    _Contract,
)
from trade_graph.api.hq_contract import contract_keys as _contract_keys
from trade_graph.contracts.alarms import SYMBOL_PATTERN

SCHEMA: Final = "trade-graph.hq.desk.v1"
STANCES_MAX: Final = 16  # today's coins written out; ``more_stances`` counts the rest
LAB_NEXT_MAX: Final = 5
LAB_RESULTS_MAX: Final = 8
LAB_REPORTS_MAX: Final = 4  # reports per result (one per cost profile)
FAMILIES_MAX: Final = 8
HEADLINES_MAX: Final = 5
IDEAS_MAX: Final = 3
INBOX_MAX: Final = 5
VERDICTS_MAX: Final = 5
SPLIT_ROWS_MAX: Final = 8  # rows per list of the split; ``more`` counts the rest (their sums stay in the totals)
# The HQ snapshot's budget, reused as this endpoint's own ceiling on a busy synthetic week.
PAYLOAD_BUDGET_BYTES: Final = 80 * 1024

Symbol = Annotated[str, Field(pattern=SYMBOL_PATTERN)]
MarketType = Literal["rising", "sideways", "falling", "unclear"]
MARKET_TYPES: Final = ("rising", "sideways", "falling", "unclear")
Stance = Literal["wait", "buy_dips", "follow_trend", "other"]
LabelStatus = Literal["current", "stale", "missing"]
Tone = Literal["good", "neutral", "attention"]
SectionState = Literal["shown", "empty", "not_available", "unreadable"]


class Section(_Contract):
    """What every part of the room shares: whether it has something to show, and its headline in plain words.

    ``shown``: records to show; ``empty``: the records exist but nothing has happened yet; ``not_available``: the
    database is older than the records (or a live account has no paper scorecard); ``unreadable``: the records could
    not be read (for example an edited, hash-checked record), said as such and never invented.
    """

    state: SectionState
    headline: NonEmptyText


class TypeReading(_Contract):
    """A coin's market type as software labelled it: ``market_type`` only when the label was current (D287)."""

    status: LabelStatus
    market_type: MarketType | None
    text: NonEmptyText

    @model_validator(mode="after")
    def _as_such(self):
        if (self.market_type is not None) != (self.status == "current"):
            raise ValueError("a market type is shown exactly when its label was current; stale or missing say so")
        return self


class PlaybookChoiceView(_Contract):
    market_type: MarketType
    stance: Stance | None  # None: this revision has no choice for the type
    strategy_refs: Annotated[list[Name], Field(max_length=8)]
    text: NonEmptyText
    reason: Quote | None
    reports: NonNegative  # how many practice-range reports or lab requests it rests on

    @model_validator(mode="after")
    def _wait_is_cash(self):
        if self.stance in (None, "wait") and self.strategy_refs:
            raise ValueError("waiting (or no choice) names no strategy version")
        if self.stance is None and (self.reason is not None or self.reports):
            raise ValueError("a type without a choice has no reason or reports")
        return self


class PlaybookView(Section):
    revision: Positive | None
    set_at: ISO | None
    revisions_total: NonNegative
    why: Quote | None  # why it changed, in the desk's words
    choices: Annotated[list[PlaybookChoiceView], Field(max_length=4)]
    previous: Text | None  # the revision it replaced, in plain words

    @model_validator(mode="after")
    def _consistent(self):
        shown = self.state == "shown"
        if shown != (self.revision is not None) or shown != (self.set_at is not None):
            raise ValueError("a shown playbook names its revision and when it was set, and only then")
        if shown and [choice.market_type for choice in self.choices] != list(MARKET_TYPES):
            raise ValueError("a shown playbook lists the four market types in order")
        if not shown and (self.choices or self.why is not None or self.previous is not None):
            raise ValueError("without a playbook there are no choices")
        if self.revision is not None and self.revisions_total < self.revision:
            raise ValueError("the revisions counted include the one in force")
        return self


class StanceView(_Contract):
    symbol: Symbol
    stance: Stance
    at: ISO
    text: NonEmptyText  # the stance in plain words, with the strategy version
    shown: TypeReading  # the market type software showed the desk for the coin in that wake
    now: TypeReading  # the coin's label now
    reason: Quote
    news: NonNegative  # how many news items the desk said it weighed


class StancesView(Section):
    day_start: ISO | None
    day_end: ISO | None
    items: Annotated[list[StanceView], Field(max_length=STANCES_MAX)]
    more_stances: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        if (self.day_start is None) != (self.day_end is None):
            raise ValueError("a trading day has a start and an end")
        if self.items and self.state != "shown":
            raise ValueError("only a shown section lists stances")
        if len({item.symbol for item in self.items}) != len(self.items):
            raise ValueError("one stance per coin: the newest of today")
        return self


class LabReportView(_Contract):
    run_id: Identifier
    text: NonEmptyText
    trades: NonNegative
    result: Money  # after fees
    versus_hold: Money | None  # the result less holding the coin over the same days; None when not reported
    tone: Tone


class LabResultView(_Contract):
    request_id: Identifier
    at: ISO | None  # when it ended
    status: Literal["done", "refused", "failed"]
    tone: Tone
    text: NonEmptyText
    reason: Text | None  # why it was refused or failed, as the lab recorded it
    reports: Annotated[list[LabReportView], Field(max_length=LAB_REPORTS_MAX)]
    more_reports: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        if (self.status == "done") == (self.reason is not None):
            raise ValueError("a refused or failed request says why, and a done one doesn't")
        if self.status != "done" and (self.reports or self.more_reports):
            raise ValueError("only a done request has reports")
        return self


class FamilyView(_Contract):
    strategy_id: Name
    text: NonEmptyText  # the tries in plain words
    versions: NonNegative
    studies: NonNegative
    tune_runs: NonNegative
    confirm_runs: NonNegative
    confirmed: Annotated[list[Name], Field(max_length=5)]


class LabView(Section):
    queued: NonNegative
    running: NonNegative
    next: Annotated[list[NonEmptyText], Field(max_length=LAB_NEXT_MAX)]
    results: Annotated[list[LabResultView], Field(max_length=LAB_RESULTS_MAX)]
    more_results: NonNegative
    families: Annotated[list[FamilyView], Field(max_length=FAMILIES_MAX)]
    more_families: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        if self.state in ("not_available", "unreadable") and (
                self.queued or self.running or self.next or self.results or self.families):
            raise ValueError("a lab that can't be read shows no figures")
        if len(self.next) > self.queued + self.running:
            raise ValueError("the next requests are among those queued or running")
        if len({item.request_id for item in self.results}) != len(self.results):
            raise ValueError("lab results are listed once each")
        return self


class Headline(_Contract):
    text: Quote  # what the Leader took from it, in its words
    source: Annotated[str, Field(max_length=120)] | None  # the stored source's publisher or reference


class LeaderRound(_Contract):
    at: ISO
    summary: Quote | None
    headlines: Annotated[list[Headline], Field(max_length=HEADLINES_MAX)]
    more_headlines: NonNegative
    desk_note: Quote | None  # the note the Leader left the desk
    ideas: Annotated[list[Quote], Field(max_length=IDEAS_MAX)]  # strategy ideas addressed to the desk
    more_ideas: NonNegative


class InboxIdea(_Contract):
    at: ISO
    idea: Quote
    text: NonEmptyText  # where it went and which lab requests cite it


class Verdict(_Contract):
    at: ISO
    title: Quote  # the headline, as published
    text: NonEmptyText  # the publisher, the coins and whether it would have woken someone


class NewsView(Section):
    read_at: ISO | None  # the moment of the desk's turn whose news this is (now when it has had none yet)
    round: LeaderRound | None
    inbox: Annotated[list[InboxIdea], Field(max_length=INBOX_MAX)]
    more_inbox: NonNegative
    jev: Text  # Jev's mode and its verdicts' count, in plain words
    verdicts: Annotated[list[Verdict], Field(max_length=VERDICTS_MAX)]
    more_verdicts: NonNegative

    @model_validator(mode="after")
    def _consistent(self):
        if self.state in ("not_available", "unreadable") and (self.round is not None or self.inbox or self.verdicts):
            raise ValueError("news that can't be read shows nothing")
        return self


class SplitRow(_Contract):
    key: Name  # the group: a strategy version, a market type at entry, a stance, or a combination of the three
    text: NonEmptyText
    trades: NonNegative
    open_positions: NonNegative
    result: Money
    tone: Tone


class SplitView(Section):
    period_start: ISO | None
    currency: Currency | None
    result: Money | None  # every trade's and held position's result after fees
    by_choice: Annotated[list[SplitRow], Field(max_length=SPLIT_ROWS_MAX)]
    more_choices: NonNegative
    by_strategy: Annotated[list[SplitRow], Field(max_length=SPLIT_ROWS_MAX)]
    more_strategies: NonNegative
    by_market_type: Annotated[list[SplitRow], Field(max_length=SPLIT_ROWS_MAX)]
    by_stance: Annotated[list[SplitRow], Field(max_length=SPLIT_ROWS_MAX)]
    note: NonEmptyText  # evidence, not proof of profit or permission

    @model_validator(mode="after")
    def _consistent(self):
        rows = (*self.by_choice, *self.by_strategy, *self.by_market_type, *self.by_stance)
        if self.state != "shown" and (rows or self.result is not None):
            raise ValueError("only a shown split has rows")
        if self.state == "shown" and (self.period_start is None or self.currency is None or self.result is None):
            raise ValueError("a shown split names its period, currency and result")
        if any(row.result.currency != self.currency for row in rows):
            raise ValueError("the split is in the account's currency")
        return self


class DeskDocument(_Contract):
    schema_: Literal["trade-graph.hq.desk.v1"] = Field(alias="schema")
    as_of: ISO
    headline: NonEmptyText
    playbook: PlaybookView
    stances: StancesView
    lab: LabView
    news: NewsView
    split: SplitView


def validate_desk(document: Mapping[str, Any]) -> DeskDocument:
    """Validate a Trading desk room document (raises ``pydantic.ValidationError``)."""
    return DeskDocument.model_validate(document)


def contract_keys() -> set[str]:
    """Every JSON key the document can emit, for redaction-safety checks."""
    return _contract_keys(DeskDocument)


__all__ = ["FAMILIES_MAX", "HEADLINES_MAX", "IDEAS_MAX", "INBOX_MAX", "LAB_NEXT_MAX", "LAB_REPORTS_MAX",
           "LAB_RESULTS_MAX", "MARKET_TYPES", "PAYLOAD_BUDGET_BYTES", "SCHEMA", "SPLIT_ROWS_MAX", "STANCES_MAX",
           "VERDICTS_MAX", "DeskDocument", "contract_keys", "validate_desk"]
