"""``news-alarm-report`` says which feeds could not be read (W9-A, D259): a lost feed is reported, never "no news".

Read only, from the stored polls. Scripted feeds labelled ``synthetic_fixture``; Jev off; no network or key.
"""

from __future__ import annotations

from datetime import timedelta

from tests.news_service_support import HACK, QUIET, ScriptedFeeds
from tests.news_support import NOW, open_state

from trade_graph.application.news_report import format_report_text, news_alarm_report
from trade_graph.application.news_shadow import NewsAlarmShadow
from trade_graph.application.news_store import NewsStore
from trade_graph.contracts.news import NewsAlarmSettings, WatchList


def _poll(database, clock, feeds) -> None:
    NewsAlarmShadow(store=NewsStore(database, clock), fetcher=feeds, settings=NewsAlarmSettings(), clock=clock,
                    checks=None, watch=WatchList).poll_once()


def test_the_report_lists_each_feeds_polls_and_a_feed_lost_in_its_last_poll(tmp_path) -> None:
    database, clock, _budget = open_state(tmp_path)
    feeds = ScriptedFeeds(clock, {"coindesk": [[(*HACK, 3)]], "decrypt": [[(*QUIET, 3)]]})
    _poll(database, clock, feeds)  # 12:00, every feed read
    feeds.fail["coindesk"] = "transport_error"
    for _ in range(2):  # 12:05 and 12:10, CoinDesk cannot be reached
        clock.advance(300)
        _poll(database, clock, feeds)
    report = news_alarm_report(database, since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=1))
    feeds_by_id = {item["feed_id"]: item for item in report["feeds"]}
    assert set(feeds_by_id) == {"kraken-status", "kraken-blog", "coindesk", "cointelegraph", "decrypt"}
    coindesk = feeds_by_id["coindesk"]
    assert (coindesk["polls"], coindesk["read"], coindesk["lost"], coindesk["lost_now"]) == (3, 1, 2, True)
    assert coindesk["last_outcome"] == "transport_error" and coindesk["last_read_at"] == "2026-10-16T12:00:00.000000Z"
    assert coindesk["lost_since"] == "2026-10-16T12:05:00.000000Z"
    assert (feeds_by_id["decrypt"]["read"], feeds_by_id["decrypt"]["lost_now"]) == (3, False)
    assert report["summary"]["feeds_lost_now"] == 1
    assert any("never taken as \"no news\"" in note for note in report["notes"])
    text = format_report_text(report)
    assert "CoinDesk: not read in its last poll (transport_error) since 2026-10-16 12:05 UTC" in text
    assert "Decrypt: read in 3 of 3 polls." in text


def test_a_window_without_polls_lists_no_feeds(tmp_path) -> None:
    database, _clock, _budget = open_state(tmp_path)
    report = news_alarm_report(database, since=NOW - timedelta(hours=1), until=NOW)
    assert report["feeds"] == [] and report["summary"]["feeds_lost_now"] == 0
