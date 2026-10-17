"""Research's focus-coin round as a strict provider reply (T37; D120, D124, D150, D192, D206, D207).

At the day start, and when it refreshes, Research reviews the coin scanner's shortlist with news it found through
the software-run ``web_search`` tool and answers with a ``FocusResearchReply``:

- ``focus``: the coins worth a Trader day plan today, each with a short reason and the evidence it rests on (the
  day's scan id or ``source_ref`` values the tool returned in this round). Picking nothing is a valid answer.
- ``keep_current_focus``: at a refresh, leave today's focus as it is (then ``focus`` is empty).
- ``findings``: news findings, each citing exactly one stored ``source_ref`` and naming the coins it concerns.
  The model chooses no lifetime: software keeps a finding until the trading day ends, as long as the day plan.
- ``next_check_in``: when Research wants to look again; none means the next day start (D123).

The reply holds no confidence, approval, order, plan or wake field: what Research reads on the web is data, never
authority. Software checks every pick against the day's candidates and every reference against the round's stored
sources before anything is kept (``application/focus_research.py``). Provider-neutral and SDK-free.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from trade_graph.contracts.alarms import REFERENCE_PATTERN, SYMBOL_PATTERN
from trade_graph.contracts.models import ContractModel
from trade_graph.contracts.wakes import MAX_EVIDENCE_REFS, MAX_FOCUS_COINS, CheckInRequest, FocusCoin, FocusSelection
from trade_graph.contracts.web_research import WebResearchReply

Symbol = Annotated[str, Field(pattern=SYMBOL_PATTERN)]
Reference = Annotated[str, Field(pattern=REFERENCE_PATTERN)]
MAX_PICK_REFS = 8
MAX_FINDINGS = 12


class FocusFinding(ContractModel):
    """One news finding. It cites one stored result; software fills in its link, date, hash and lifetime."""

    question: str = Field(min_length=1, max_length=500)
    claim: str = Field(min_length=1, max_length=1000)
    source_ref: str = Field(min_length=1, max_length=200)
    instruments: list[Symbol] = Field(min_length=1, max_length=10)
    counterevidence: str = Field(min_length=1, max_length=1000)
    invalidation: str = Field(min_length=1, max_length=500)


class FocusPick(ContractModel):
    """One focus coin: a coin from the day's candidates, a short reason and the evidence it rests on."""

    symbol: Symbol
    reason: str = Field(min_length=1, max_length=500)
    evidence_refs: list[Reference] = Field(min_length=1, max_length=MAX_PICK_REFS)

    @model_validator(mode="after")
    def _as_a_focus_coin(self) -> FocusPick:
        FocusCoin(symbol=self.symbol, note=self.reason, evidence_refs=tuple(self.evidence_refs))
        return self


class FocusResearchReply(WebResearchReply):
    """What Research's Perplexity round returns for the focus step or a refresh."""

    summary: str = Field(min_length=1, max_length=1000)
    findings: list[FocusFinding] = Field(default_factory=list, max_length=MAX_FINDINGS)  # type: ignore[assignment]
    keep_current_focus: bool = False
    focus: list[FocusPick] = Field(default_factory=list, max_length=MAX_FOCUS_COINS)
    next_check_in: CheckInRequest | None = None

    @model_validator(mode="after")
    def _consistent(self) -> FocusResearchReply:
        symbols = [pick.symbol for pick in self.focus]
        if len(symbols) != len(set(symbols)):
            raise ValueError("each focus coin is named once")
        if self.keep_current_focus and self.focus:
            raise ValueError("keeping today's focus names no new coins")
        FocusSelection(coins=(), summary=self.summary)  # the summary is plain text, as the engine records it
        return self

    def selection(self, extra_refs: dict[str, tuple[str, ...]] | None = None) -> FocusSelection | None:
        """The engine's ``FocusSelection``, or None when Research keeps today's focus.

        ``extra_refs`` adds software-made references to a coin (the ids of the findings stored for it), bounded
        by the contract's evidence limit.
        """
        if self.keep_current_focus:
            return None
        coins = []
        for pick in self.focus:
            refs = tuple(dict.fromkeys([*pick.evidence_refs, *(extra_refs or {}).get(pick.symbol, ())]))
            coins.append(FocusCoin(symbol=pick.symbol, note=pick.reason, evidence_refs=refs[:MAX_EVIDENCE_REFS]))
        return FocusSelection(coins=tuple(coins), summary=self.summary)


__all__ = ["MAX_FINDINGS", "MAX_PICK_REFS", "FocusFinding", "FocusPick", "FocusResearchReply"]
