"""T44: every Jev call gets a reservation and a receipt through the existing budget path.

Scripted replies only. A scripted reply is synthetic: it is never reported as a real Jev or paid-provider test.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from tests.news_support import NOW, WATCH, open_state, reply, runner

from trade_graph.adapters.models.transport import ProviderHttpResponseError
from trade_graph.adapters.news.feeds import FetchResult, parse_feed
from trade_graph.contracts.decisions import exact_json
from trade_graph.contracts.news import RECOMMENDED_FEEDS, NewsAlarmRule

DECRYPT = next(feed for feed in RECOMMENDED_FEEDS if feed.feed_id == "decrypt")
RULE = NewsAlarmRule()


def _headline(store, link: str = "https://decrypt.co/1/synthetic", title: str = "Synthetic headline"):
    raw = (f"<rss><channel><item><title>{title}</title><link>{link}</link>"
           "<pubDate>Fri, 16 Oct 2026 11:59:00 +0000</pubDate></item></channel></rss>").encode()
    fetched = FetchResult(outcome="fetched", http_status=200, body=raw, etag=None, last_modified=None,
                          evidence="synthetic_fixture", error_category=None)
    return store.record_poll(DECRYPT, fetched, parse_feed(raw, "rss"), started_at=NOW).new[0]


def _reservation(database, reservation_id):
    return dict(database.execute("SELECT * FROM budget_reservations WHERE reservation_id=?",
                                 (reservation_id,)).fetchone())


def _receipts(database):
    return [dict(row) for row in database.execute("SELECT * FROM usage_receipts ORDER BY rowid")]


def test_an_answered_call_is_reserved_before_dispatch_and_receipted_after(tmp_path):
    database, clock, budget = open_state(tmp_path)
    seen = []

    def at_dispatch(body):
        seen.append({
            "headline": database.execute("SELECT title FROM news_headlines").fetchall(),
            "attempt": database.execute("SELECT reservation_id FROM news_jev_attempts").fetchall(),
            "reservations": database.execute("SELECT state, role FROM budget_reservations").fetchall(),
            "outcomes": database.execute("SELECT COUNT(*) FROM news_jev_outcomes").fetchone()[0],
        })

    checks, transport, store = runner(database, clock, budget,
                                      [reply({"ETH": "0.9", "none": "0.1"}, "0.8", request_id="synthetic-1")],
                                      on_call=at_dispatch)
    headline = _headline(store)
    record = checks.check(headline, WATCH, RULE)
    assert [tuple(row) for row in seen[0]["headline"]] == [("Synthetic headline",)]
    assert len(seen[0]["attempt"]) == 1 and seen[0]["outcomes"] == 0
    assert [tuple(row) for row in seen[0]["reservations"]] == [("RESERVED", "news_alarm")]
    assert record.status == "answered" and record.attempt_number == 1 and record.latency_ms == 250
    reservation = _reservation(database, record.reservation_id)
    assert reservation["state"] == "COMMITTED" and reservation["synthetic"] == 1
    assert reservation["purpose"] == "news_alarm:jev"
    (receipt,) = _receipts(database)
    assert (receipt["provider"], receipt["model"], receipt["status"]) == ("typesafe", "jev-1.13.0", "committed")
    assert receipt["provider_request_id"] == "synthetic-1" and receipt["synthetic"] == 1
    assert Decimal(receipt["native_cost"]) == Decimal("0.00001764") and receipt["native_currency"] == "USD"
    assert Decimal(receipt["reporting_cost"]) == Decimal("0.000015876")
    assert record.verdict is not None and record.verdict.wake_roles == ("leader", "trader")
    assert transport.calls[0]["body"]["model"] == "jev-1.13.0"


def test_the_reservation_holds_the_largest_documented_request():
    from trade_graph.adapters.decisions.jev import JEV_MAX_INPUT_TOKENS

    assert JEV_MAX_INPUT_TOKENS == 64_000


@pytest.mark.parametrize(("script", "status", "failure", "state"), [
    (ProviderHttpResponseError(400, {}), "http_error", "invalid_request", "UNCERTAIN"),
    (ProviderHttpResponseError(401, {}), "http_error", "credentials", "UNCERTAIN"),
    (ProviderHttpResponseError(422, {}), "http_error", "unprocessable", "UNCERTAIN"),
    (ProviderHttpResponseError(429, {}), "http_error", "rate_limit", "UNCERTAIN"),
    (ProviderHttpResponseError(529, {}), "http_error", "overloaded", "UNCERTAIN"),
    (TimeoutError("provider transport outcome uncertain"), "timeout_uncertain", "timeout_uncertain", "UNCERTAIN"),
    (ValueError("provider response byte bound exceeded"), "refused_response", "refused_response", "UNCERTAIN"),
    (reply({"BTC": "1"}, "0.9", input_tokens=None), "answered", None, "UNCERTAIN"),
    (reply({"BTC": "1"}, "0.9", model="jev-1.14.0"), "unexpected_model", "unexpected_model", "UNCERTAIN"),
    ({"model": "jev-1.13.0", "answers": {"concerns": {}}, "usage": {"input_tokens": 9, "output_tokens": 0}},
     "malformed_answers", "malformed_answers", "COMMITTED"),
    (ProviderHttpResponseError(422, {"usage": {"input_tokens": 37, "output_tokens": 0}, "model": "jev-1.13.0"}),
     "http_error", "unprocessable", "COMMITTED"),
])
def test_every_failure_keeps_a_reservation_and_unknown_usage_stays_unresolved(tmp_path, script, status, failure,
                                                                              state):
    database, clock, budget = open_state(tmp_path)
    checks, _, store = runner(database, clock, budget, [script])
    record = checks.check(_headline(store), WATCH, RULE)
    assert (record.status, record.failure) == (status, failure)
    reservation = _reservation(database, record.reservation_id)
    assert reservation["state"] == state
    receipts = _receipts(database)
    if state == "UNCERTAIN":
        assert receipts == [] and Decimal(reservation["amount"]) > 0  # held at the worst case, never free
    else:
        assert [item["status"] for item in receipts] == ["committed"]
    outcome = dict(database.execute("SELECT * FROM news_jev_outcomes").fetchone())
    assert outcome["status"] == status and outcome["reservation_state"] == state
    assert (record.verdict is not None) == (status == "answered")
    assert database.execute("SELECT COUNT(*) FROM news_alarm_verdicts").fetchone()[0] == (status == "answered")


def test_a_retry_is_a_new_receipted_attempt_and_stops_once_answered(tmp_path):
    database, clock, budget = open_state(tmp_path)
    checks, _, store = runner(database, clock, budget, [ProviderHttpResponseError(429, {}),
                                                        reply({"BTC": "1"}, "0.1", request_id="synthetic-2")])
    headline = _headline(store)
    assert [item.headline_id for item in store.due_for_check(NOW, window_seconds=3600, max_attempts=3)] == [
        headline.headline_id]
    first = checks.check(headline, WATCH, RULE)
    assert store.due_for_check(NOW, window_seconds=3600, max_attempts=3)[0].headline_id == headline.headline_id
    second = checks.check(headline, WATCH, RULE)
    assert (first.attempt_number, second.attempt_number) == (1, 2)
    assert first.reservation_id != second.reservation_id
    assert [_reservation(database, item.reservation_id)["state"] for item in (first, second)] == [
        "UNCERTAIN", "COMMITTED"]
    assert store.due_for_check(NOW, window_seconds=3600, max_attempts=3) == []


def test_retryable_failures_stop_after_the_attempt_setting_and_final_ones_at_once(tmp_path):
    database, clock, budget = open_state(tmp_path)
    checks, _, store = runner(database, clock, budget, [ProviderHttpResponseError(529, {})] * 3
                              + [ProviderHttpResponseError(422, {})])
    retried = _headline(store, "https://decrypt.co/2/synthetic")
    for _ in range(3):
        checks.check(retried, WATCH, RULE)
    final = _headline(store, "https://decrypt.co/3/synthetic")
    checks.check(final, WATCH, RULE)
    assert store.due_for_check(NOW, window_seconds=3600, max_attempts=3) == []
    assert [item.headline_id for item in store.due_for_check(NOW, window_seconds=3600, max_attempts=4)] == [
        retried.headline_id]


def test_old_headlines_are_not_sent_to_jev(tmp_path):
    database, clock, budget = open_state(tmp_path)
    _, _, store = runner(database, clock, budget, [])
    raw = (b"<rss><channel><item><title>old</title><link>https://decrypt.co/old</link>"
           b"<pubDate>Mon, 12 Oct 2026 06:00:00 +0000</pubDate></item></channel></rss>")
    fetched = FetchResult(outcome="fetched", http_status=200, body=raw, etag=None, last_modified=None,
                          evidence="synthetic_fixture", error_category=None)
    store.record_poll(DECRYPT, fetched, parse_feed(raw, "rss"), started_at=NOW)
    assert store.due_for_check(NOW, window_seconds=3600, max_attempts=3) == []


def test_repeated_identical_calls_may_differ_and_are_kept_as_they_came(tmp_path):
    database, clock, budget = open_state(tmp_path)
    first_reply = reply({"ETH": "0.9", "none": "0.1"}, "0.8", request_id="synthetic-a")
    second_reply = reply({"none": "0.6", "ETH": "0.4"}, "0.30000", request_id="synthetic-b")
    checks, transport, store = runner(database, clock, budget, [first_reply, second_reply])
    headline = _headline(store)
    first = checks.check(headline, WATCH, RULE)
    second = checks.check(headline, WATCH, RULE)
    assert transport.calls[0]["body"] == transport.calls[1]["body"]
    assert first.verdict.would_wake and not second.verdict.would_wake
    stored = [row[0] for row in database.execute(
        "SELECT answers_json FROM news_jev_outcomes ORDER BY rowid")]
    assert stored == [exact_json(first_reply["answers"]), exact_json(second_reply["answers"])]
    assert '"yes":0.30000' in stored[1]
    verdicts = [row[0] for row in database.execute("SELECT would_wake FROM news_alarm_verdicts ORDER BY rowid")]
    assert verdicts == [1, 0]


def test_stored_answers_and_verdicts_are_append_only(tmp_path):
    database, clock, budget = open_state(tmp_path)
    checks, _, store = runner(database, clock, budget, [reply({"BTC": "1"}, "0.9", request_id="synthetic-c")])
    checks.check(_headline(store), WATCH, RULE)
    for table in ("news_headlines", "news_feed_polls", "news_jev_attempts", "news_jev_outcomes",
                  "news_alarm_verdicts"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute(f"UPDATE {table} SET rowid = rowid")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            database.execute(f"DELETE FROM {table}")


def test_an_interrupted_call_stays_unresolved_and_is_recorded(tmp_path):
    database, clock, budget = open_state(tmp_path)

    def interrupt(body):
        raise KeyboardInterrupt

    checks, _, store = runner(database, clock, budget, [reply({"BTC": "1"}, "0.9")], on_call=interrupt)
    with pytest.raises(KeyboardInterrupt):
        checks.check(_headline(store), WATCH, RULE)
    outcome = dict(database.execute("SELECT * FROM news_jev_outcomes").fetchone())
    assert outcome["status"] == "interrupted"
    assert _reservation(database, outcome["reservation_id"])["state"] == "UNCERTAIN"


def test_recovery_settles_an_attempt_whose_process_died(tmp_path):
    database, clock, budget = open_state(tmp_path)
    checks, _, store = runner(database, clock, budget, [])
    headline = _headline(store)
    attempt_id, reservation_id = checks.reserve_and_record(headline, WATCH)  # the process dies after this
    assert _reservation(database, reservation_id)["state"] == "RESERVED"
    assert checks.recover() == [attempt_id]
    assert _reservation(database, reservation_id)["state"] == "UNCERTAIN"
    assert database.execute("SELECT status FROM news_jev_outcomes").fetchone()[0] == "interrupted"
    assert checks.recover() == []


def test_an_exhausted_money_limit_sends_nothing(tmp_path):
    """Budget-path check with a stand-in transport marked non-synthetic. Not a paid-provider test."""
    database, clock, budget = open_state(tmp_path, allowance="0")
    checks, transport, store = runner(database, clock, budget, [reply({"BTC": "1"}, "0.9")], synthetic=False)
    record = checks.check(_headline(store), WATCH, RULE)
    assert record.status == "budget_refused" and record.reservation_id is None
    assert transport.calls == [] and _receipts(database) == []
    assert database.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert store.due_for_check(NOW, window_seconds=3600, max_attempts=1)  # a refusal is not an attempt


def test_a_scripted_transport_cannot_be_labelled_real(tmp_path):
    from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD, JevAdapter, ScriptedJevHttp
    from trade_graph.application.jev_checks import JevBudgetBinding, JevCheckRunner
    from trade_graph.application.news_store import NewsStore
    from trade_graph.domain.errors import AuthorityDenied

    database, clock, budget = open_state(tmp_path)
    binding = JevBudgetBinding(deployment_id="deployment", price_card_id=JEV_PRICE_CARD.price_card_id,
                               fx_rate=Decimal("0.9"), fx_buffer=Decimal("1.02"), synthetic=False)
    with pytest.raises(AuthorityDenied):
        JevCheckRunner(database, clock, budget, JevAdapter(ScriptedJevHttp([])), binding, NewsStore(database, clock))


def test_a_receipt_is_allocated_once_to_the_watched_portfolio(tmp_path):
    from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD, JevAdapter, ScriptedJevHttp
    from trade_graph.application.jev_checks import JevBudgetBinding, JevCheckRunner
    from trade_graph.application.news_store import NewsStore

    database, clock, budget = open_state(tmp_path)
    binding = JevBudgetBinding(deployment_id="deployment", price_card_id=JEV_PRICE_CARD.price_card_id,
                               fx_rate=Decimal("0.9"), fx_buffer=Decimal("1.02"), synthetic=True,
                               portfolio_id="paper-portfolio")
    store = NewsStore(database, clock)
    checks = JevCheckRunner(database, clock, budget,
                            JevAdapter(ScriptedJevHttp([reply({"BTC": "1"}, "0.9", request_id="synthetic-d")])),
                            binding, store)
    record = checks.check(_headline(store), WATCH, RULE)
    rows = database.execute("SELECT portfolio_id, weight FROM cost_allocations WHERE receipt_id=?",
                            (record.receipt_id,)).fetchall()
    assert [tuple(row) for row in rows] == [("paper-portfolio", "1")]
