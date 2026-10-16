"""T44: the shadow report reads stored records only: headlines, Jev answers, verdicts, latency and spend."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from tests.news_support import FIXTURES, NOW, WATCH, Ticks, open_state, reply, runner

from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD, JevAdapter, ScriptedJevHttp
from trade_graph.adapters.news.feeds import FixtureFeedFetcher
from trade_graph.application.jev_checks import JevBudgetBinding, JevCheckRunner
from trade_graph.application.news_report import format_report_text, news_alarm_report
from trade_graph.application.news_shadow import NewsAlarmShadow
from trade_graph.application.news_store import NewsStore
from trade_graph.contracts.news import NewsAlarmSettings

DELISTING = "https://blog.kraken.com/product/asset-listings/synthetic-exmpl-delisting"


def _polled(tmp_path):
    database, clock, budget = open_state(tmp_path)
    store = NewsStore(database, clock)
    binding = JevBudgetBinding(deployment_id="deployment", price_card_id=JEV_PRICE_CARD.price_card_id,
                               fx_rate=Decimal("0.9"), fx_buffer=Decimal("1.02"), synthetic=True)
    transport = ScriptedJevHttp.from_fixture(FIXTURES / "jev-scripted-answers.json")
    checks = JevCheckRunner(database, clock, budget, JevAdapter(transport), binding, store, monotonic=Ticks(0.125))
    NewsAlarmShadow(store=store, fetcher=FixtureFeedFetcher(FIXTURES), settings=NewsAlarmSettings(), clock=clock,
                    checks=checks, watch=lambda: WATCH).poll_once()
    return database, clock, budget


def test_the_report_lists_headlines_answers_verdicts_latency_and_daily_spend(tmp_path):
    database, *_ = _polled(tmp_path)
    report = news_alarm_report(database, since=NOW - timedelta(days=1), until=NOW + timedelta(minutes=1))
    assert report["mode"] == "shadow" and report["wakes_sent"] == 0
    assert report["summary"]["headlines"] == 8 and report["summary"]["checked"] == 7
    assert report["summary"]["would_wake"] == 2
    assert report["summary"]["would_wake_by_role"] == {"leader": 2, "trader": 2}
    delisting = next(item for item in report["headlines"] if item["link"] == DELISTING)
    assert delisting["evidence"] == "synthetic_fixture" and delisting["publisher"] == "Kraken blog"
    (check,) = delisting["checks"]
    assert check["status"] == "answered" and check["model_requested"] == check["model_answered"] == "jev-1.13.0"
    assert check["latency_ms"] == 125
    assert check["answers"]["concerns"]["probabilities"]["EXMPL"] == "0.91"
    assert check["cost"] == {"status": "synthetic", "native_usd": "0.00001764", "input_tokens": 420,
                             "output_tokens": 0}
    assert delisting["verdict"]["would_wake"] is True and delisting["verdict"]["wake_roles"] == ["leader", "trader"]
    assert delisting["verdict"]["event_type"] == "delisting"
    old = next(item for item in report["headlines"] if item["link"].endswith("synthetic-0000"))
    assert old["checks"] == [] and old["verdict"] is None
    assert report["latency_ms"] == {"checks": 7, "median": 125, "max": 125}
    (day,) = report["spend"]["days"]
    assert day == {"day": "2026-10-16", "calls": 7, "real_usd": "0", "synthetic_usd": "0.00012348",
                   "unresolved_calls": 0, "unresolved_held_eur": "0"}
    assert report["spend"]["testing_total_real_usd"] == "0" and report["spend"]["alert"] is False
    assert report["spend"]["alert_usd"] == "5"


def test_the_report_never_recomputes_stored_answers_or_verdicts(tmp_path):
    database, *_ = _polled(tmp_path)
    window = {"since": NOW - timedelta(days=1), "until": NOW + timedelta(minutes=1)}
    first = news_alarm_report(database, **window)
    stored = {row[0]: row[1] for row in database.execute(
        "SELECT h.link, o.answers_json FROM news_jev_outcomes o JOIN news_jev_attempts a USING (attempt_id) "
        "JOIN news_headlines h ON h.headline_id = a.headline_id")}
    for item in first["headlines"]:
        for check in item["checks"]:
            assert check["answers"] == json.loads(stored[item["link"]], parse_float=str, parse_int=str)
    # The report takes no alarm setting: reading it again gives the same stored answers and verdicts.
    assert news_alarm_report(database, **window) == first


def test_unresolved_and_real_spending_are_shown_apart_from_synthetic_and_the_alert_pauses_nothing(tmp_path):
    """Budget-path check with a stand-in transport marked non-synthetic. Not a paid-provider test."""
    database, clock, budget = open_state(tmp_path)
    from tests.integration.test_jev_checks import RULE, _headline

    checks, _, store = runner(database, clock, budget,
                              [reply({"BTC": "1"}, "0.9", request_id="stand-in-1"),
                               reply({"BTC": "1"}, "0.9", input_tokens=None)], synthetic=False)
    checks.check(_headline(store, "https://decrypt.co/a/synthetic"), WATCH, RULE)
    checks.check(_headline(store, "https://decrypt.co/b/synthetic"), WATCH, RULE)
    report = news_alarm_report(database, since=NOW - timedelta(days=1), until=NOW + timedelta(minutes=1),
                               alert_usd=Decimal("0.00001"))
    (day,) = report["spend"]["days"]
    assert day["real_usd"] == "0.00001764" and day["synthetic_usd"] == "0"
    assert day["unresolved_calls"] == 1 and Decimal(day["unresolved_held_eur"]) > 0
    assert report["spend"]["alert"] is True
    assert "alert only" in report["spend"]["alert_note"].lower()
    unresolved = [check for item in report["headlines"] for check in item["checks"]
                  if check["cost"]["status"] == "unresolved"]
    assert len(unresolved) == 1 and unresolved[0]["cost"]["native_usd"] is None
    # Reaching the alert pauses nothing: the next check still runs.
    more, transport, store = runner(database, clock, budget, [reply({"BTC": "1"}, "0.9", request_id="stand-in-2")],
                                    synthetic=False)
    assert more.check(_headline(store, "https://decrypt.co/c/synthetic"), WATCH, RULE).status == "answered"


def test_the_text_report_is_plain_english_and_says_nobody_was_woken(tmp_path):
    database, *_ = _polled(tmp_path)
    text = format_report_text(news_alarm_report(database, since=NOW - timedelta(days=1),
                                                until=NOW + timedelta(minutes=1)))
    assert "Shadow mode: nobody was woken." in text
    assert "Would have woken the Leader and the Trader" in text
    assert "Kraken will delist EXMPL/USD on 30 October (synthetic)" in text
    assert "synthetic" in text.lower() and "USD" in text
    assert "Not checked: published before the check window" in text


def test_an_empty_window_reports_zero_without_failing(tmp_path):
    database, *_ = _polled(tmp_path)
    report = news_alarm_report(database, since=NOW + timedelta(days=2), until=NOW + timedelta(days=3))
    assert report["headlines"] == [] and report["summary"]["headlines"] == 0
    assert report["latency_ms"] == {"checks": 0, "median": None, "max": None}
    assert report["spend"]["days"] == []
