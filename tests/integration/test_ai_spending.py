"""AI spending from receipts (T40): per department, actual / estimated / unresolved apart, Codex use per department.

Every record is synthetic (``tests/spending_support.py``); no provider is called. The read model must add up to
the receipts, keep unknown usage unresolved (never zero), keep test calls apart, follow the owner's recorded
Perplexity policy for the testing period and never read practice money.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.spending_support import BUFFER, Spending

from trade_graph.adapters.persistence.db import Database
from trade_graph.application import ai_spending
from trade_graph.application.ai_spending import spending_report
from trade_graph.application.web_research import PerplexitySpendGuard
from trade_graph.contracts.web_research import PerplexitySpendPolicy
from trade_graph.domain.clock import FrozenClock

START = datetime(2026, 10, 16, 12, tzinfo=UTC)
ZERO = Decimal("0")


@pytest.fixture
def env(tmp_path):
    clock = FrozenClock(START)
    database = Database(tmp_path / "state.sqlite")
    yield Spending(database, clock)
    database.close()


def report(env):
    return spending_report(env.database, env.clock.now())


def test_departments_add_up_to_the_receipts_with_each_kind_of_cost_apart(env):
    env.run("unlimited_testing")
    env.call("research")
    env.call("research", search=True)
    uncertain = env.call("research", "uncertain")
    conservative = env.call("research", "conservative")
    env.call("leader")
    in_flight = env.call("leader", "reserved")
    env.call("research", synthetic=True)

    perplexity = report(env).perplexity
    research, leader = perplexity.departments["research"], perplexity.departments["leader"]
    committed = env.receipts_usd(roles=("research",)) - env.held_usd(conservative)
    assert research.actual == committed == Decimal("0.004") + Decimal("0.005")  # agent step + one search
    assert research.estimated == env.held_usd(conservative)
    assert research.unresolved == env.held_usd(uncertain) and research.unresolved_calls == 1
    assert research.calls == 4
    assert leader.actual == Decimal("0.004") and leader.unresolved == env.held_usd(in_flight)
    assert (leader.calls, leader.unresolved_calls) == (2, 1)
    total = perplexity.testing
    assert total.known == env.receipts_usd(roles=("research", "leader"))  # reconciles to the receipts
    assert total.actual == research.actual + leader.actual
    assert total.unresolved == research.unresolved + leader.unresolved and total.unresolved_calls == 2
    assert total.total == total.known + total.unresolved
    assert (perplexity.synthetic_calls, perplexity.synthetic_known) == (1, Decimal("0.004"))
    assert list(perplexity.departments) == ["leader", "research"]  # HQ map order


def test_unknown_usage_is_unresolved_and_never_zero(env):
    env.run("unlimited_testing")
    reservation = env.call("research", "uncertain")
    tally = report(env).perplexity.testing
    assert tally.actual == ZERO and tally.estimated == ZERO and tally.known == ZERO
    assert tally.unresolved_calls == 1
    held = env.held_usd(reservation)
    assert tally.unresolved == held > ZERO
    assert held == Decimal("2") * 4000 / 1_000_000 * BUFFER + Decimal("10") * 1000 / 1_000_000 * BUFFER \
        + Decimal("0.0025") * 2 * BUFFER  # the reserved maximum with its buffer, in USD
    assert tally.total == held


def test_a_held_amount_that_cannot_be_valued_stays_unknown(env):
    env.run("unlimited_testing")
    reservation = env.call("research", "uncertain")
    env.database.execute("UPDATE budget_reservations SET fx_rate_value=NULL WHERE reservation_id=?", (reservation,))
    tally = report(env).perplexity.testing
    assert tally.unresolved is None and tally.unresolved_calls == 1 and tally.total is None


def test_jev_spending_is_its_own_line_with_its_own_alert(env):
    env.call(service="jev", input_tokens=4000, output_tokens=12)
    unresolved = env.call(service="jev", outcome="uncertain")
    jev = report(env).jev
    assert list(jev.departments) == ["news_alarm"]
    line = jev.departments["news_alarm"]
    assert line.actual == Decimal("0.042") * 4000 / 1_000_000 == env.receipts_usd(roles=("news_alarm",))
    assert line.unresolved == env.held_usd(unresolved) and line.unresolved_calls == 1
    assert jev.status == "testing" and jev.alert_usd == Decimal("5") and jev.alert_source == "default"
    assert jev.alert_state == "below"
    assert "news_alarm" not in report(env).perplexity.departments


def test_nothing_recorded_yet(env):
    result = report(env)
    for service in (result.perplexity, result.jev):
        assert service.status == "off" and service.testing.calls == 0 and service.testing.total == ZERO
        assert service.alert_state == "below" and service.credit is None
    assert result.perplexity.alert_usd == Decimal("20")
    assert result.codex.turns == 0 and result.codex.share("trader") is None


def test_testing_runs_from_the_first_unlimited_round_until_the_owner_sets_a_monthly_limit(env):
    env.call("research")  # before any recorded policy: not testing spend
    env.clock.advance(60)
    env.run("unlimited_testing")
    env.call("research")
    env.call("leader")
    env.clock.advance(3 * 86400)
    limit_at = env.clock.now()
    env.run("monthly_limit", limit="30")
    env.call("research", "uncertain")
    result = report(env).perplexity
    assert result.status == "monthly_limit" and result.monthly_limit_usd == Decimal("30")
    assert result.testing_start == START + timedelta(seconds=60) and result.testing_end == limit_at
    assert result.testing.calls == 2 and result.testing.actual == Decimal("0.008")
    assert result.alert_state is None  # the testing alert ends with testing
    month = result.month
    assert month.calls == 4 and month.unresolved_calls == 1
    guard = PerplexitySpendGuard(env.database, env.clock, PerplexitySpendPolicy(
        revision_id="owner-policy-limit", mode="monthly_limit", monthly_limit_usd="30", decision_ref="D138"),
        deployment_id="deployment")
    assert month.total == guard.spent_usd()  # HQ's month total is exactly what the owner limit counts


def test_the_alert_passes_on_known_spending_and_may_have_passed_with_unresolved_calls(env):
    env.run("unlimited_testing")
    env.call("research", input_tokens=4_000_000, output_tokens=1_000_000)  # $8 + $10
    result = report(env).perplexity
    assert result.testing.known == Decimal("18") and result.alert_state == "below"
    env.clock.advance(60)
    env.call("research", "uncertain")
    assert report(env).perplexity.alert_state == "below"  # $18 + up to $0.0153 stays under $20
    env.record("perplexity", "testing_alert", "18.01")
    result = report(env).perplexity
    assert (result.alert_usd, result.alert_source, result.alert_state) == (Decimal("18.01"), "owner",
                                                                           "maybe_passed")
    assert result.alert_since is None
    env.clock.advance(60)
    crossed = env.clock.now()
    env.call("leader", input_tokens=1_000_000, output_tokens=0)  # +$2 known
    result = report(env).perplexity
    assert result.alert_state == "passed" and result.alert_since == crossed
    env.record("perplexity", "testing_alert", "500")
    assert report(env).perplexity.alert_state == "below"  # the newest owner record wins


def test_the_credit_shows_spending_since_the_owner_read_it(env):
    env.run("unlimited_testing")
    env.call("research")
    env.clock.advance(3600)
    read_at = env.clock.now()
    env.record("perplexity", "prepaid_credit", "50", observed_at=read_at)
    env.clock.advance(60)
    env.call("leader")
    uncertain = env.call("research", "uncertain")
    credit = report(env).perplexity.credit
    assert credit.amount == Decimal("50") and credit.observed_at == read_at
    assert credit.since.known == Decimal("0.004") and credit.since.unresolved == env.held_usd(uncertain)
    assert credit.left == Decimal("50") - Decimal("0.004") - env.held_usd(uncertain)
    assert report(env).jev.credit is None


def test_codex_use_per_department_is_a_share_of_the_reported_use_over_seven_days(env):
    env.codex_turn("trader", used=3000)
    env.codex_turn("trader", used=1000)
    env.codex_turn("trader", used=None)  # usage not reported: counted as a turn, unknown use
    env.codex_turn("research", used=1000)
    env.codex_turn("leader", used=500, synthetic=True)  # a test turn: not counted
    env.codex_turn("learning", used=500, state="BLOCKED")  # never sent
    env.codex_turn("learning", used=500, ago=timedelta(days=8))  # older than the window
    codex = report(env).codex
    assert codex.start == START - timedelta(days=7) and codex.end == START
    assert (codex.turns, codex.unreported_turns, codex.reported_use) == (4, 1, 5000)
    assert list(codex.departments) == ["research", "trader"]
    assert codex.departments["trader"].turns == 3 and codex.departments["trader"].unreported_turns == 1
    assert codex.share("trader") == Decimal("80") and codex.share("research") == Decimal("20")
    assert codex.share("leader") is None


def test_unreported_fields_make_a_codex_turn_unknown(env):
    env.codex_turn("trader", used=1000)
    env.database.execute("UPDATE subscription_invocations SET usage_json=?",
                         (json.dumps({"uncached_input_tokens": 900, "billed_output_tokens": 100,
                                      "unreported_fields": ["billed_output_tokens"]}),))
    codex = report(env).codex
    assert (codex.turns, codex.unreported_turns, codex.reported_use) == (1, 1, 0)
    assert codex.share("trader") is None


def test_the_report_document_uses_exact_decimal_strings(env):
    env.run("unlimited_testing")
    env.call("research")
    env.call("research", "uncertain")
    document = report(env).document()
    json.dumps(document)  # plain JSON
    perplexity = document["services"]["perplexity"]
    assert perplexity["testing"]["actual_usd"] == "0.004"
    assert perplexity["testing"]["unresolved_calls"] == 1
    assert perplexity["alert"] == {"threshold_usd": "20", "source": "default", "state": "below", "since": None}
    assert document["paper_money_used"] is False


def test_practice_money_never_enters_the_report(env):
    """Acceptance: paper gains are never shown as paying for AI. No paper record changes any figure."""
    env.run("unlimited_testing")
    env.call("research")
    env.call("research", "uncertain")
    before = report(env).document()
    env.database.execute("""INSERT INTO fills (fill_id, venue, account_id, trade_id, portfolio_id, intent_id,
        document_json, created_at) VALUES ('f', 'paper', 'paper', 't', 'paper-rich', NULL,
        '{"symbol": "BTC/USD", "side": "sell", "quantity": "1", "price": "999999"}', '2026-10-16T12:00:00.000000Z')""")
    assert report(env).document() == before
    source = Path(ai_spending.__file__).read_text()
    queries = [node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)
               and isinstance(node.value, str) and "SELECT" in node.value]
    assert queries
    for word in ("fills", "ledger_events", "portfolios", "balances", "positions", "order_intents", "fx_rates"):
        assert not any(word in query for query in queries), word


def test_older_databases_without_the_newer_tables_still_report(env):
    for table in ("ai_spending_owner_records", "web_research_runs"):
        env.database.execute(f"ALTER TABLE {table} RENAME TO hidden_{table}")
    env.call("research")
    result = report(env)
    assert result.perplexity.status == "off" and result.perplexity.testing.actual == Decimal("0.004")
    assert result.perplexity.alert_source == "default" and result.perplexity.credit is None


def test_real_research_rounds_land_in_the_research_line(tmp_path):
    """End to end through the T36 round with its scripted (synthetic) Perplexity transport."""
    from tests.integration import test_perplexity_research as research

    setup = research.Setup(tmp_path, research._round_trip() + [TimeoutError("synthetic timeout")])
    assert setup.service.run(setup.ask()).status == "completed"
    assert setup.service.run(setup.ask(task_id="task-2", root_task_id="task-2", role="leader")).status == "uncertain"
    result = spending_report(setup.database, setup.clock.now()).perplexity
    assert result.status == "testing" and result.testing_start == setup.clock.now()
    research_line, leader_line = result.departments["research"], result.departments["leader"]
    receipts = sum(Decimal(row["native_cost"]) for row in setup.receipts())
    assert research_line.calls == 3 and research_line.actual == receipts == Decimal("0.0158")
    assert research_line.unresolved_calls == 0 and research_line.unresolved == ZERO
    assert leader_line.calls == 1 and leader_line.unresolved_calls == 1 and leader_line.actual == ZERO
    assert leader_line.unresolved == Decimal("0.02346")  # the reserved maximum with its buffer, never free
    guard = PerplexitySpendGuard(setup.database, setup.clock, research.UNLIMITED, deployment_id="deployment")
    assert result.testing.total == guard.spent_usd()
