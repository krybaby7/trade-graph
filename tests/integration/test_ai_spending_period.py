"""AI spending over one period, from receipts (T42 scorecard, D203): the same three kinds as HQ's spending view.

The scorecard shows AI spending beside the paper result for the same days. Every record here is synthetic
(``tests/spending_support.py``, through the real ``BudgetGateway``); no provider is called. Each paid call made in the
period counts once, as actual, estimated or unresolved; unknown usage is never zero; test calls are kept apart; paid
calls on any other price card are counted too, so nothing paid is left out; Codex turns are counted, not priced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.spending_support import DEPLOYMENT, FX, Spending

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.ai_spending import period_spending
from trade_graph.contracts.models import ModelUsage, PriceCard

START = datetime(2026, 11, 2, tzinfo=UTC)
END = START + timedelta(days=1)
ZERO = Decimal("0")
OTHER_CARD = PriceCard(price_card_id="synthetic-other-api-card", provider="openai", model="synthetic-model",
                       endpoint="responses", currency="USD", input_per_million="1", output_per_million="4",
                       effective_at="2026-10-01", verified_at="2026-10-01", source_id="synthetic", tier="standard",
                       context_band="any")


@pytest.fixture
def env(tmp_path):
    from trade_graph.domain.clock import FrozenClock

    clock = FrozenClock(START - timedelta(hours=2))
    database = Database(tmp_path / "state.sqlite")
    spending = Spending(database, clock)
    spending.budget.seed_card(OTHER_CARD)
    yield spending
    database.close()


def _other_call(env, *, commit: bool = True) -> str:
    reservation = env.budget.reserve(
        deployment_id=DEPLOYMENT, role="engineer", task_id=None, root_task_id=None,
        price_card_id=OTHER_CARD.price_card_id, max_input=2000, max_output=500, max_tools=0, fx_rate=FX,
        fx_buffer=Decimal("1.02"), priority=False, synthetic=False, purpose="engineer")
    if commit:
        usage = ModelUsage(uncached_input_tokens=1000, billed_output_tokens=250,
                           provider_request_id=f"synthetic-other-{reservation}")
        env.budget.commit(reservation, usage, provider="openai", model="synthetic-model", fx_rate=FX)
    return reservation


def test_calls_in_the_period_add_up_to_their_receipts_with_each_kind_apart(env):
    env.call("research")  # before the start: not counted
    env.clock.advance(3 * 3600)  # START + 1h
    env.call("research")
    env.call("research", search=True)
    estimated = env.call("leader", "conservative")
    open_call = env.call("research", "uncertain")
    env.call(service="jev")
    env.call("research", synthetic=True)
    _other_call(env)
    env.clock.advance(24 * 3600)  # after the end: not counted
    env.call("research")

    report = period_spending(env.database, START, END)
    perplexity, jev, other = (report.services[name] for name in ("perplexity", "jev", "other"))
    # Agent step 0.004 + one search 0.005 from receipts; the leader's call charged at its maximum; the open call
    # held at its maximum, never zero.
    assert perplexity.actual == Decimal("0.009") and perplexity.actual_calls == 2
    assert perplexity.estimated == env.held_usd(estimated) and perplexity.estimated_calls == 1
    assert perplexity.unresolved == env.held_usd(open_call) > ZERO and perplexity.unresolved_calls == 1
    assert perplexity.calls == 4
    assert jev.actual == Decimal("0.000042") and jev.calls == 1  # 1,000 input tokens at USD0.042 per million
    assert other.actual == Decimal("0.002") and other.calls == 1  # 1,000 x 1 + 250 x 4 per million
    total = report.total
    assert total.known == perplexity.known + jev.known + other.known
    assert total.total == total.known + perplexity.unresolved
    # The lines per department.
    assert set(report.departments["perplexity"]) == {"research", "leader"}
    assert report.departments["perplexity"]["leader"].estimated == env.held_usd(estimated)
    assert set(report.departments["jev"]) == {"news_alarm"}
    assert set(report.departments["other"]) == {"engineer"}
    # The test call is counted apart and never added.
    assert report.synthetic_calls == 1


def test_a_receipt_that_arrives_after_the_end_leaves_the_call_unresolved_as_of_the_end(env):
    env.clock.advance(25 * 3600)  # START + 23h
    reservation = env.reserve("research")
    env.clock.advance(2 * 3600)  # START + 25h, after the end
    env.commit(reservation)
    report = period_spending(env.database, START, END)
    tally = report.services["perplexity"]
    assert tally.actual == ZERO and tally.unresolved_calls == 1 and tally.unresolved == env.held_usd(reservation)
    later = period_spending(env.database, START, END + timedelta(hours=2))
    assert later.services["perplexity"].actual == Decimal("0.004") and later.services["perplexity"].unresolved == 0


def test_a_period_without_calls_counts_none_and_says_so(env):
    report = period_spending(env.database, START, END)
    assert all(tally.calls == 0 and tally.total == ZERO for tally in report.services.values())
    assert report.departments == {"perplexity": {}, "jev": {}, "other": {}}
    document = report.document()
    assert document["total"]["calls"] == 0 and document["paper_money_used"] is False
    assert document["currency"] == "USD"


def test_codex_turns_in_the_period_are_counted_per_department_not_priced(env):
    env.clock.advance(4 * 3600)  # START + 2h
    env.codex_turn("trader", ago=timedelta(minutes=30))
    env.codex_turn("trader", used=None, ago=timedelta(minutes=20))
    env.codex_turn("leader", ago=timedelta(minutes=10))
    env.codex_turn("trader", state="BLOCKED", ago=timedelta(minutes=5))  # never sent
    env.codex_turn("trader", ago=timedelta(hours=3))  # before the start
    codex = period_spending(env.database, START, END).codex
    assert codex.turns == 3 and codex.unreported_turns == 1
    assert codex.departments["trader"].turns == 2 and codex.departments["leader"].turns == 1
    assert codex.start == START and codex.end == END


def test_the_period_document_keeps_unknown_as_unknown(env):
    env.clock.advance(3 * 3600)
    env.call("research", "uncertain")
    document = period_spending(env.database, START, END).document()
    perplexity = document["services"]["perplexity"]["total"]
    assert perplexity["actual_usd"] == "0" and perplexity["unresolved_calls"] == 1
    assert Decimal(perplexity["unresolved_usd"]) > 0 and perplexity["total_usd"] == perplexity["unresolved_usd"]
    assert document["start"] == "2026-11-02T00:00:00.000000Z" and document["end"] == "2026-11-03T00:00:00.000000Z"
