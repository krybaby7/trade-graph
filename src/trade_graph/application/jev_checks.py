"""One Jev check per headline through the existing budget path (T44).

Order of effects: the reservation and the attempt row commit together before the call; the reply, its
receipt (or the unresolved hold) and the would-wake verdict commit together after it. Usage that is not
reported, a reply from another model, a timeout or an interrupted call keeps the worst-case hold
(``UNCERTAIN``): unknown usage is unresolved, never free. Synthetic (scripted) receipts stay apart from real
spending. Nothing here touches orders, owner policy or wake-ups.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from trade_graph.adapters.decisions.jev import (
    JEV_ENDPOINT,
    JEV_MAX_INPUT_TOKENS,
    JEV_PROVIDER,
    JevAdapter,
    pinned_model,
)
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.news_questions import QUESTION_SET_VERSION, build_request, evaluate
from trade_graph.application.news_store import NewsStore
from trade_graph.contracts.decisions import DecisionResult
from trade_graph.contracts.models import ModelUsage
from trade_graph.contracts.news import Headline, NewsAlarmRule, WatchList, WouldWakeVerdict
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import AuthorityDenied, BudgetExhausted, TradeGraphError, ValidationFailure

ROLE = "news_alarm"  # the spending line for HQ (T40); a software check, not a department
PURPOSE = "news_alarm:jev"


@dataclass(frozen=True)
class JevBudgetBinding:
    deployment_id: str
    price_card_id: str
    fx_rate: Decimal
    fx_buffer: Decimal
    synthetic: bool
    fx_rate_id: str | None = None
    portfolio_id: str | None = None
    timeout_seconds: float = 10
    max_output_tokens: int = 1024  # output is free on the dated card; reserved in case a later card prices it


@dataclass(frozen=True)
class CheckRecord:
    attempt_id: str
    attempt_number: int | None
    status: str
    failure: str | None
    reservation_id: str | None
    reservation_state: str | None
    receipt_id: str | None
    latency_ms: int | None
    verdict: WouldWakeVerdict | None


def _status(result: DecisionResult) -> str:
    if result.ok:
        return "answered"
    if result.failure in {"unexpected_model", "malformed_answers", "refused_response"}:
        return result.failure
    if result.http_status is not None and result.http_status != 200:
        return "http_error"
    return "timeout_uncertain"


class JevCheckRunner:
    def __init__(self, database, clock: Clock, budget: BudgetGateway, adapter: JevAdapter,
                 binding: JevBudgetBinding, store: NewsStore, *, monotonic: Callable[[], float] = time.monotonic):
        if binding.synthetic != adapter.synthetic:
            raise AuthorityDenied("a scripted Jev reply cannot be recorded as a real call, or the reverse")
        card = budget.card(binding.price_card_id)
        if card.provider != JEV_PROVIDER or card.endpoint != JEV_ENDPOINT:
            raise ValidationFailure("the Jev price card must name TypeSafe's official endpoint")
        pinned_model(card.model)
        self.database, self.clock, self.budget, self.adapter = database, clock, budget, adapter
        self.binding, self.store, self.card, self.monotonic = binding, store, card, monotonic

    def _reserve(self, headline: Headline, watch: WatchList, body: dict) -> tuple[str, str, int]:
        with self.database.immediate():
            number = self.store.next_attempt_number(headline.headline_id)
            reservation_id = self.budget.reserve(
                deployment_id=self.binding.deployment_id, role=ROLE, task_id=None, root_task_id=None,
                price_card_id=self.card.price_card_id, max_input=JEV_MAX_INPUT_TOKENS,
                max_output=self.binding.max_output_tokens, max_tools=0, fx_rate=self.binding.fx_rate,
                fx_buffer=self.binding.fx_buffer, priority=False, synthetic=self.binding.synthetic, purpose=PURPOSE,
                system_version_id=QUESTION_SET_VERSION, attempt_kind="primary" if number == 1 else "retry",
                fx_rate_id=self.binding.fx_rate_id)
            attempt_id = self.store.insert_attempt(
                headline, number, reservation_id, requested_model=self.card.model,
                question_set_version=QUESTION_SET_VERSION, body=body, watch=watch, synthetic=self.binding.synthetic)
        return attempt_id, reservation_id, number

    def reserve_and_record(self, headline: Headline, watch: WatchList) -> tuple[str, str]:
        """The pre-dispatch step alone (used by recovery tests)."""
        body = self.adapter.prepare(build_request(headline, watch, self.card.model))
        attempt_id, reservation_id, _ = self._reserve(headline, watch, body)
        return attempt_id, reservation_id

    def check(self, headline: Headline, watch: WatchList, rule: NewsAlarmRule) -> CheckRecord:
        request = build_request(headline, watch, self.card.model)
        body = self.adapter.prepare(request)
        try:
            attempt_id, reservation_id, number = self._reserve(headline, watch, body)
        except BudgetExhausted:
            # Nothing is sent. A money limit refusal is recorded; it is not an attempt and is asked again later.
            with self.database.immediate():
                attempt_id = self.store.insert_attempt(
                    headline, None, None, requested_model=self.card.model, question_set_version=QUESTION_SET_VERSION,
                    body=body, watch=watch, synthetic=self.binding.synthetic)
                self.store.insert_outcome(attempt_id, status="budget_refused", result=None, reservation_id=None,
                                          reservation_state=None, receipt_id=None, latency_ms=None)
            return CheckRecord(attempt_id, None, "budget_refused", None, None, None, None, None, None)
        dispatch = getattr(self.budget, "protect_dispatch", None)
        if dispatch is not None:
            dispatch(reservation_id)
        started = self.monotonic()
        try:
            result = self.adapter.call(request, timeout_seconds=self.binding.timeout_seconds)
        except BaseException:
            self._interrupted(attempt_id, reservation_id)
            raise
        latency_ms = max(0, round((self.monotonic() - started) * 1000))
        record = self._settle(headline, watch, rule, attempt_id, reservation_id, number, result, latency_ms)
        settlement = getattr(self.budget, "protect_settlement", None)
        if settlement is not None:
            settlement(reservation_id)
        return record

    def _priced(self, result: DecisionResult) -> bool:
        if not result.usage.reported:
            return False
        if result.http_status == 200:
            return result.answering_model == self.card.model
        return result.answering_model in {None, self.card.model}

    def _settle(self, headline, watch, rule, attempt_id, reservation_id, number, result, latency_ms) -> CheckRecord:
        status = _status(result)
        receipt_id = None
        verdict = evaluate(result.answers, watch, rule) if status == "answered" else None
        with self.database.immediate():
            if self._priced(result):
                usage = ModelUsage(uncached_input_tokens=result.usage.input_tokens,
                                   billed_output_tokens=result.usage.output_tokens,
                                   provider_request_id=result.provider_request_id,
                                   provider_reported_input_tokens=result.usage.input_tokens)
                try:
                    receipt_id = self.budget.commit(reservation_id, usage, provider=JEV_PROVIDER,
                                                    model=self.card.model, fx_rate=self.binding.fx_rate)
                    if self.binding.portfolio_id:
                        self.budget.allocate(receipt_id, {self.binding.portfolio_id: Decimal("1")})
                except (ValueError, sqlite3.IntegrityError, TradeGraphError):
                    receipt_id = None
            if receipt_id is None:
                self.budget.mark_uncertain(reservation_id)
            state = self._state(reservation_id)
            self.store.insert_outcome(attempt_id, status=status, result=result, reservation_id=reservation_id,
                                      reservation_state=state, receipt_id=receipt_id, latency_ms=latency_ms)
            if verdict is not None:
                self.store.insert_verdict(attempt_id, headline, verdict)
        return CheckRecord(attempt_id, number, status, result.failure, reservation_id, state, receipt_id, latency_ms,
                           verdict)

    def _state(self, reservation_id: str) -> str:
        return self.database.execute("SELECT state FROM budget_reservations WHERE reservation_id = ?",
                                     (reservation_id,)).fetchone()["state"]

    def _interrupted(self, attempt_id: str, reservation_id: str) -> None:
        with self.database.immediate():
            self.budget.mark_uncertain(reservation_id)
            self.store.insert_outcome(attempt_id, status="interrupted", result=None, reservation_id=reservation_id,
                                      reservation_state=self._state(reservation_id), receipt_id=None,
                                      latency_ms=None)

    def recover(self) -> list[str]:
        """Settle attempts whose process stopped after the reservation: unresolved, never free."""
        recovered = []
        for attempt_id, reservation_id in self.store.unfinished_attempts():
            self._interrupted(attempt_id, reservation_id)
            recovered.append(attempt_id)
        return recovered
