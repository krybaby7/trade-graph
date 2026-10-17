"""T45 in HQ's wake log (T39): a news wake-up shows its reason, the headline's source and the headline itself as a
quote, and the day's counts say whether news alarms wake people or only record whom they would wake.

The records come from the real pipeline: invented headlines on a synthetic feed, scripted Jev answers, the real event
engine and scripted departments on the synthetic USD10,000 paper account. Reading HQ starts nothing. No network, key,
model, paid call or real order.
"""

from __future__ import annotations

import json
import time

from tests.news_wake_support import SOL_DELISTING_TITLE, NewsDesk, hold_sol, switch_news_wakes
from tests.wake_log_support import Wakes, dump_sha256, hq_runtime
from tests.wake_support import engine_account, run_all, scripted

from trade_graph.api import hq_wakes
from trade_graph.api.hq_contract import iso_ms
from trade_graph.api.hq_wakes_contract import PAGE_MAX, PAYLOAD_BUDGET_BYTES, validate_wake_log
from trade_graph.api.security import redact
from trade_graph.contracts.wakes import DEPARTMENTS
from trade_graph.domain.clock import utc_iso


def _words(text) -> str:
    return "".join(segment.get("v", "") for segment in text or [])


def _log(account, **options) -> dict:
    document = hq_wakes.wake_log(hq_runtime(account), **options)
    validate_wake_log(document)
    return document


def _news_item(document: dict, department: str) -> dict:
    (item,) = [item for item in document["items"] if item["dept"] == department
               and any(reason["kind"] == "news_alarm" for reason in item["reasons"])]
    return item


def _live_news_day(tmp_path, *, held: bool = True):
    account, scheduler, engine = engine_account(tmp_path, daily_cycle=False)
    handlers = {"leader": scripted({"summary": "Synthetic: read the delisting news."}),
                "trader": scripted({"summary": "Synthetic: the plan stands."})}
    if held:
        hold_sol(account)
        run_all(scheduler, engine, handlers, [account.portfolio])  # the fill's own wake comes first
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    run_all(scheduler, engine, handlers, [account.portfolio])
    return account


def test_a_news_wake_up_shows_its_reason_the_source_and_the_headline(tmp_path) -> None:
    account = _live_news_day(tmp_path)
    document = _log(account)
    for department in ("leader", "trader"):
        item = _news_item(document, department)
        assert item["reasons"] == [{"kind": "news_alarm", "count": 1}]
        assert _words(item["headline"]).startswith("A news alarm")
        (event,) = item["events"]
        assert _words(event) == "A news alarm about SOL/USD from Kraken blog."
        (note,) = item["notes"]
        assert note["text"].startswith(f'News alarm: Kraken blog reported "{SOL_DELISTING_TITLE[:40]}')
        assert item["status"] == "completed"
    reasons = document["day"]["counts"]["reasons_today"]
    assert {"kind": "news_alarm", "count": 2} in reasons


def test_the_days_counts_say_news_alarms_wake_people_once_the_switch_is_live(tmp_path) -> None:
    account = _live_news_day(tmp_path)
    words = _words(_log(account)["day"]["counts"]["text"])
    assert "News alarms wake people: 2 news wake-ups today, 2 in the last 7 days." in words


def test_in_shadow_mode_the_counts_say_how_many_wake_ups_would_have_happened(tmp_path) -> None:
    account, scheduler, engine = engine_account(tmp_path, daily_cycle=False)
    hold_sol(account)
    words = _words(_log(account)["day"]["counts"]["text"])
    assert "News alarms only record whom they would wake for now; none would have woken anyone" in words
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    run_all(scheduler, engine, {"leader": scripted(), "trader": scripted()}, [account.portfolio])
    document = _log(account)
    words = _words(document["day"]["counts"]["text"])
    assert "2 wake-ups would have happened today, 2 in the last 7 days; nobody was woken." in words
    assert not [item for item in document["items"]
                if any(reason["kind"] == "news_alarm" for reason in item["reasons"])]


def test_a_news_trigger_without_a_saved_route_still_reads_in_plain_words(tmp_path) -> None:
    account, _, _ = engine_account(tmp_path, daily_cycle=False)
    wakes = Wakes(account)
    wake_id = wakes.wake("leader", [wakes.trigger("news_alarm", "leader", source_ref="software:news-alarm:unknown")])
    (item,) = [item for item in _log(account)["items"] if item["id"] == wake_id]
    assert _words(item["events"][0]) == "A news alarm about SOL/USD."


def test_reading_the_news_wake_log_starts_nothing(tmp_path) -> None:
    account = _live_news_day(tmp_path)
    before = dump_sha256(account.database)
    for _ in range(3):
        _log(account)
    assert dump_sha256(account.database) == before


def _route_rows(account, count: int, publisher: str) -> list[str]:
    """Synthetic saved routes (with their headline, Jev attempt and verdict) for the payload check; no engine run."""
    database, now = account.database, utc_iso(account.clock.now())
    refs = []
    with database.immediate() as connection:
        connection.execute("""INSERT INTO news_feed_polls (poll_id, feed_id, url, publisher, started_at, finished_at,
            outcome, evidence, items_seen, items_new, items_repeat, items_rejected) VALUES ('busy-poll', 'busy',
            'https://example.org/feed', ?, ?, ?, 'fetched', 'synthetic_fixture', 0, 0, 0, 0)""", (publisher, now, now))
        for n in range(count):
            connection.execute("""INSERT INTO news_headlines (headline_id, link, feed_id, publisher, title,
                published_at, received_at, item_sha256, evidence, poll_id) VALUES (?, ?, 'busy', ?, ?, ?, ?, ?,
                'synthetic_fixture', 'busy-poll')""", (f"h-{n}", f"https://example.org/{n}", publisher,
                                                         "Synthetic busy headline " * 20, now, now, "b" * 64))
            connection.execute("""INSERT INTO news_jev_attempts (attempt_id, headline_id, attempt_number,
                reservation_id, requested_model, question_set_version, request_sha256, request_json, watch_json,
                synthetic, started_at) VALUES (?, ?, NULL, NULL, 'jev-1.13.0', 'news-alarm-questions-v1', ?, '{}',
                '{}', 1, ?)""", (f"a-{n}", f"h-{n}", "b" * 64, now))
            connection.execute("""INSERT INTO news_jev_outcomes (attempt_id, status, usage_reported, finished_at)
                VALUES (?, 'answered', 0, ?)""", (f"a-{n}", now))
            connection.execute("""INSERT INTO news_alarm_verdicts (verdict_id, attempt_id, headline_id, rule_version,
                question_set_version, urgent_threshold, would_wake, wake_roles_json, subjects_json,
                held_subjects_json, event_type, holder_yes, created_at) VALUES (?, ?, ?, 'news-alarm-rule-v1',
                'news-alarm-questions-v1', '0.5', 1, '["leader"]', '["SOL"]', '[]', 'delisting', '0.93', ?)""",
                               (f"v-{n}", f"a-{n}", f"h-{n}", now))
            connection.execute("""INSERT INTO news_alarm_wake_routes (route_id, verdict_id, headline_id, portfolio_id,
                department, mode, because, rule_version, policy_revision, reason_json, note, symbol, source_ref,
                trigger_id, alarm_at, created_at) VALUES (?, ?, ?, ?, 'leader', 'shadow', 'alarm',
                'news-wake-route-v1', NULL, '{}', 'Synthetic note', 'SOL/USD', ?, NULL, ?, ?)""",
                               (f"r-{n}", f"v-{n}", f"h-{n}", account.portfolio, f"news-alarm:r-{n}", now, now))
            refs.append(f"software:news-alarm:r-{n}")
    return refs


def test_a_busy_day_of_news_wake_ups_stays_within_the_payload_budget(tmp_path) -> None:
    """600 wakes of 12 news alarms each, with full 500-character notes; the newest page names every source."""
    account, _, _ = engine_account(tmp_path, daily_cycle=False)
    wakes = Wakes(account)
    refs = _route_rows(account, PAGE_MAX * 12, "P" * 80)
    note = "N" * 500
    for n in range(600):
        department = DEPARTMENTS[n % 5]
        newest = n >= 600 - PAGE_MAX
        triggers = [wakes.trigger("news_alarm", department, note=note,
                                  **({"source_ref": refs.pop()} if newest else {})) for _ in range(12)]
        wakes.wake(department, triggers, outcome={"received": True, "summary": "Synthetic busy summary. " * 40},
                   close_after=60)
    for limit in (None, PAGE_MAX):
        began = time.perf_counter()
        document = _log(account, **({} if limit is None else {"limit": limit}))
        elapsed = time.perf_counter() - began
        size = len(json.dumps(redact(document), separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        assert size <= PAYLOAD_BUDGET_BYTES, size
        assert elapsed < 5, elapsed
    newest = document["items"][0]
    assert all(_words(event).endswith("from " + "P" * 80 + ".") for event in newest["events"])
    assert newest["more_events"] == 6 and document["as_of"] == iso_ms(account.clock.now())
