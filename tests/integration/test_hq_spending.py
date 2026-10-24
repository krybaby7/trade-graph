"""HQ shows AI spending per department from receipts, the testing total beside the prepaid credit, and the
testing alert (T40). Synthetic records only (``tests/spending_support.py``); reading HQ never calls a provider.

Acceptance: totals reconcile to receipts and unknown usage is unresolved, never zero; practice gains are never
shown as paying for AI; crossing the alert threshold notifies in HQ without pausing any department.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration import test_hq_projection as t
from tests.integration.test_dashboard import _client
from tests.spending_support import Spending

from trade_graph.api import hq, hq_spending, resources
from trade_graph.api.hq_contract import ATTENTION_SEVERITY, validate_snapshot
from trade_graph.application import service_controller
from trade_graph.application.ai_spending import spending_report
from trade_graph.domain.clock import utc_iso

WRITES = ("pause_states", "tasks", "activity_events", "budget_reservations", "usage_receipts", "decisions",
          "order_intents", "ai_spending_owner_records")


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    runtime.clock.advance(2 * 86400 + 3600)
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == t.LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    env = SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    env.spending = Spending(database, runtime.clock)
    yield env
    database.close()


def snap(env, viewer=t.OWNER) -> dict:
    env.runtime._hq_spending = None
    return t.snap(env, viewer)


def service(data: dict, name: str) -> dict:
    return next(item for item in data["ai_spending"]["services"] if item["service"] == name)


def line(block: dict, line_id: str) -> dict:
    return next(item for item in block["lines"] if item["id"] == line_id)


def amount(money: dict | None) -> Decimal | None:
    return None if money is None else Decimal(money["amount"])


def text(segments: list) -> str:
    return "".join(s.get("v") or s.get("display") or "" for s in segments)


def _seed(env) -> dict:
    s = env.spending
    s.run("unlimited_testing")
    ids = {"research": s.call("research"), "search": s.call("research", search=True),
           "uncertain": s.call("research", "uncertain"), "conservative": s.call("research", "conservative"),
           "leader": s.call("leader"), "jev": s.call(service="jev", input_tokens=4000, output_tokens=10),
           "test": s.call("research", synthetic=True)}
    return ids


def test_spending_per_department_reconciles_to_receipts(env):
    ids = _seed(env)
    t.quiet(env)
    data = snap(env)
    perplexity = service(data, "perplexity")
    assert perplexity["status"] == "testing" and perplexity["name"] == "Perplexity web research"
    research, leader = line(perplexity, "research")["amounts"], line(perplexity, "leader")["amounts"]
    assert amount(research["actual"]) == Decimal("0.009") and research["calls"] == 4
    assert amount(research["estimated"]) == env.spending.held_usd(ids["conservative"])
    assert amount(research["unresolved"]) == env.spending.held_usd(ids["uncertain"]) > 0
    assert research["unresolved_calls"] == 1 and amount(leader["actual"]) == Decimal("0.004")
    total = perplexity["total"]
    known = amount(total["actual"]) + amount(total["estimated"])
    assert known == env.spending.receipts_usd(roles=("research", "leader"))  # reconciles to the receipts
    assert amount(total["total"]) == known + amount(total["unresolved"])
    assert {money["currency"] for money in (research["actual"], total["total"])} == {"USD"}
    assert perplexity["synthetic_calls"] == 1  # shown apart, never added
    jev = service(data, "jev")
    assert [item["id"] for item in jev["lines"]] == ["news_alarm"]
    assert amount(jev["total"]["actual"]) == env.spending.receipts_usd(roles=("news_alarm",))
    assert jev["alert"]["state"] == "below" and amount(jev["alert"]["threshold"]) == Decimal("5")
    assert amount(perplexity["alert"]["threshold"]) == Decimal("20")
    assert data["ai_spending"]["link"] == {"href": "/costs", "label": "Costs"}


def test_unknown_usage_shows_as_not_known_never_zero(env):
    env.spending.run("unlimited_testing")
    reservation = env.spending.call("research", "uncertain")
    t.quiet(env)
    perplexity = service(snap(env), "perplexity")
    amounts = line(perplexity, "research")["amounts"]
    assert amounts["actual"] is None and amounts["estimated"] is None  # no such call: nothing, not $0
    assert amounts["unresolved_calls"] == 1
    assert amount(amounts["unresolved"]) == env.spending.held_usd(reservation) > 0
    headline = text(perplexity["headline"])
    assert headline == ("Testing total so far: nothing from receipts, plus up to $0.02 not known yet. "
                        "No spending limit while testing.")


def test_the_testing_total_sits_beside_the_prepaid_credit_as_information(env):
    env.spending.run("unlimited_testing")
    env.spending.call("research", input_tokens=2_000_000, output_tokens=0)  # $4, before the reading
    env.runtime.clock.advance(60)
    env.spending.record("perplexity", "prepaid_credit", "50")
    env.runtime.clock.advance(60)
    env.spending.call("leader", input_tokens=500_000, output_tokens=0)  # $1 since the reading
    t.quiet(env)
    perplexity = service(snap(env), "perplexity")
    credit = perplexity["credit"]
    assert amount(credit["recorded"]) == Decimal("50") and amount(credit["left"]) == Decimal("49")
    assert amount(credit["since"]["total"]) == Decimal("1")
    words = text(credit["text"])
    assert words.startswith("Prepaid credit: $50.00") and "about $49.00 is left" in words
    assert "never stops anything" in words
    assert amount(perplexity["total"]["total"]) == Decimal("5")  # the testing total, beside the credit
    assert text(service(snap(env), "jev")["credit"]["text"]).startswith("Prepaid credit: not recorded")


def test_crossing_the_alert_notifies_in_hq_without_pausing_anything(env):
    t.run(env)
    t.reading(env)
    env.spending.run("unlimited_testing")
    env.spending.call("research", input_tokens=4_000_000, output_tokens=1_000_000)  # $18
    before = snap(env)
    assert not [item for item in before["attention"] if item["id"].startswith("spending_alert")]
    env.runtime.clock.advance(60)
    crossed = env.runtime.clock.now()
    env.spending.call("leader", input_tokens=1_000_000, output_tokens=0)  # +$2: $20 reaches the alert
    counts = {table: env.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in WRITES}
    data = snap(env)
    [card] = [item for item in data["attention"] if item["id"].startswith("spending_alert")]
    assert card["id"] == "spending_alert:perplexity" and card["sev"] == ATTENTION_SEVERITY["spending_alert"]
    assert text(card["title"]) == "Perplexity testing spending passed your $20.00 alert"
    assert text(card["why"]) == "$20.00 so far during testing, from receipts."
    assert text(card["consequence"]) == "Nothing is paused: it's an alert only."
    assert card["since"] == hq.iso_ms(crossed) and card["link"] == {"href": "/costs", "label": "Costs"}
    assert card["decision"] is None and card["can_fix_here"] is False
    assert service(data, "perplexity")["alert"]["state"] == "passed"
    # Nothing pauses: no pause, task or budget record is written; every department looks as it did.
    assert {table: env.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in WRITES} == counts
    assert data["team"]["pause"] == before["team"]["pause"] and data["team"]["ai"] == before["team"]["ai"]
    assert [(dept["id"], dept["light"]) for dept in data["departments"]] == \
        [(dept["id"], dept["light"]) for dept in before["departments"]]
    assert data["now"]["code"] == before["now"]["code"]
    assert data["now"]["attention_count"] == before["now"]["attention_count"] + 1
    assert card["in_next_prompt"] and text(card["title"]) in data["prompt_facts"]["flagged"]


def test_unresolved_calls_can_mean_the_alert_may_have_passed(env):
    env.spending.run("unlimited_testing")
    env.spending.call("research", input_tokens=9_000_000, output_tokens=0)  # $18
    env.spending.call("research", "uncertain")
    env.spending.record("perplexity", "testing_alert", "18.01")
    t.quiet(env)
    data = snap(env)
    [card] = [item for item in data["attention"] if item["id"].startswith("spending_alert")]
    assert text(card["title"]) == "Perplexity testing spending may have passed your $18.01 alert"
    assert "plus up to $0.02 not known yet" in text(card["why"])
    alert = service(data, "perplexity")["alert"]
    assert alert["state"] == "maybe_passed" and alert["source"] == "owner"


def test_jev_has_its_own_five_dollar_alert(env):
    for _ in range(3):
        env.spending.call(service="jev", input_tokens=50_000_000, output_tokens=0)  # $2.10 each
    t.quiet(env)
    data = snap(env)
    cards = [item for item in data["attention"] if item["id"].startswith("spending_alert")]
    assert [card["id"] for card in cards] == ["spending_alert:jev"]
    assert text(cards[0]["title"]) == "Jev news alarm testing spending passed your $5.00 alert"
    assert "switch Jev checks off" in text(cards[0]["how"])


def test_practice_gains_never_appear_to_pay_for_ai(env):
    env.spending.run("unlimited_testing")
    env.spending.call("research")
    t.quiet(env)
    first = snap(env)["ai_spending"]
    t.insert(env, "fills", fill_id="fill-gain", venue="paper", account_id="paper", trade_id="tr-gain",
             portfolio_id=env.pid, intent_id=None, created_at=t.at(env, -30),
             document_json=json.dumps({"symbol": "BTC/USD", "side": "sell", "quantity": "1", "price": "99999"}))
    second = snap(env)
    assert second["ai_spending"] == first  # practice records change nothing here
    words = json.dumps(second["ai_spending"]).lower()
    for phrase in ("pays for itself", "covered", "paid for by", "left after costs", "practice gain"):
        assert phrase not in words
    assert text(first["paper_note"]).startswith("Practice money never pays for AI.")
    currencies = {value["currency"] for value in _money_values(first)}
    assert currencies == {"USD"}  # never the practice account's reporting currency, never summed with it


def test_monthly_limit_replaces_the_testing_alert(env):
    env.spending.run("unlimited_testing")
    env.spending.call("research", input_tokens=15_000_000, output_tokens=0)  # $30 in testing
    env.runtime.clock.advance(60)
    env.spending.run("monthly_limit", limit="45")
    env.spending.call("research", "uncertain")
    t.quiet(env)
    data = snap(env)
    perplexity = service(data, "perplexity")
    assert perplexity["status"] == "monthly_limit" and perplexity["alert"] is None
    limit = perplexity["monthly_limit"]
    assert amount(limit["limit"]) == Decimal("45") and limit["month"]["unresolved_calls"] == 1
    assert "pauses Research and Leader news rounds only" in text(limit["text"])
    assert not [item for item in data["attention"] if item["id"] == "spending_alert:perplexity"]
    assert amount(perplexity["total"]["actual"]) == Decimal("30")  # the testing period, kept as history


def test_codex_allowance_use_per_department(env):
    env.spending.codex_turn("trader", used=3000)
    env.spending.codex_turn("research", used=1000)
    env.spending.codex_turn("trader", used=None)
    t.quiet(env)
    codex = snap(env)["ai_spending"]["codex"]
    assert (codex["turns"], codex["unreported_turns"]) == (3, 1)
    shares = {item["id"]: (item["turns"], item["share_percent"]) for item in codex["lines"]}
    assert shares == {"research": (1, 25.0), "trader": (2, 75.0)}
    assert [item["name"] for item in codex["lines"]] == ["Research", "Trader"]
    assert text(codex["text"]) == "Last 7 days: 3 turns on your ChatGPT subscription."


def test_every_viewer_sees_it_and_a_live_account_gets_live_words(env):
    env.spending.call("research")
    t.quiet(env)
    assert snap(env, t.READER)["ai_spending"]["services"]
    env.db.execute("UPDATE portfolios SET mode='live' WHERE portfolio_id=?", (env.pid,))
    live = snap(env)["ai_spending"]
    assert "practice" not in json.dumps(live).lower()
    assert text(live["paper_note"]).startswith("These are real costs from receipts.")


def test_the_cache_follows_new_receipts_and_owner_records(env):
    env.spending.run("unlimited_testing")
    t.quiet(env)
    env.runtime._hq_spending = None
    first = t.snap(env)
    assert service(first, "perplexity")["total"]["calls"] == 0
    reservation = env.spending.call("research", "reserved")
    assert service(t.snap(env), "perplexity")["total"]["unresolved_calls"] == 1
    env.spending.commit(reservation)
    assert service(t.snap(env), "perplexity")["total"]["unresolved_calls"] == 0  # a state change refreshes it
    env.spending.record("perplexity", "prepaid_credit", "10")
    assert amount(service(t.snap(env), "perplexity")["credit"]["recorded"]) == Decimal("10")


def test_a_failing_read_leaves_the_block_out_instead_of_inventing_figures(env, monkeypatch):
    t.quiet(env)
    monkeypatch.setattr(hq_spending, "spending_report",
                        lambda *args: (_ for _ in ()).throw(ValueError("synthetic failure")))
    data = snap(env)
    assert "ai_spending" not in data
    validate_snapshot(data)


def test_a_busy_month_of_spending_stays_cheap_and_small(env):
    env.spending.run("unlimited_testing")
    for index in range(400):
        env.spending.call(("research", "leader")[index % 2], "uncertain" if index % 25 == 0 else "committed")
    for index in range(600):
        env.spending.call(service="jev", outcome="uncertain" if index % 50 == 0 else "committed")
    for index in range(300):
        env.spending.codex_turn(("trader", "research", "learning")[index % 3], used=None if index % 30 == 0 else 900,
                                ago=timedelta(minutes=index * 10))
    t.quiet(env)
    data = snap(env)
    size = len(json.dumps(data["ai_spending"], separators=(",", ":")))
    assert size < 8 * 1024, size
    report = spending_report(env.db, env.runtime.clock.now())
    assert report.perplexity.testing.calls == 400 and report.jev.testing.calls == 600


def _money_values(value):
    if isinstance(value, dict):
        if set(value) == {"amount", "currency", "display"}:
            yield value
        for item in value.values():
            yield from _money_values(item)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("t") == "money":
                yield item
            yield from _money_values(item)
