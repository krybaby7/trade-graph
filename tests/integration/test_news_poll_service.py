"""The news check runs in its own thread in the paper service, every five minutes by the injected clock (W9-A, D255).

Deterministic: the clock is frozen and moved only by the test, and after each tick the test waits until the news step
has finished before it moves on, so how fast this machine runs never changes what happens. The protected subscription
binding is real (synthetic CLI, no model call); the owner's folder is a dictionary of synthetic files; feeds are
scripted and labelled ``synthetic_fixture``; Jev is ``ScriptedJevHttp`` with a made-up key. No network, real key, paid
call or order.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from tests.integration.test_owned_subscription_service import subscription_service
from tests.news_service_support import ANSWERS, HACK, QUIET, ScriptedFeeds, owner_files, scripted_jev, settings_document
from tests.perplexity_service_support import trust_owner_files

from trade_graph.application import news_installation as installed
from trade_graph.application.events import EngineOptions
from trade_graph.application.paper_service import PaperService
from trade_graph.domain.clock import parse_utc

DEADLINE = 60  # seconds a news step may take on a slow machine; the outcome never depends on it


async def settle(service) -> None:
    """Wait until the news step started by the last tick has finished (never a fixed sleep)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + DEADLINE
    while service._news_task is not None and not service._news_task.done():
        assert loop.time() < end, "the news step did not finish"
        await asyncio.sleep(0.005)


@pytest.fixture
def news(tmp_path, monkeypatch):
    flow, runtime, binding, _service, _cli, _current = subscription_service(tmp_path, monkeypatch)
    runtime.ledger, runtime.protected_deployment = runtime.execution.ledger, binding
    rounds = {"coindesk": [[(*HACK, 3)], [], [("Synthetic: a second BTC hack report", HACK[1] + "-follow-up", 1)]],
              "decrypt": [[(*QUIET, 4)]]}
    answers = {**ANSWERS, HACK[1] + "-follow-up": ANSWERS[HACK[1]]}
    feeds, jev = ScriptedFeeds(flow.clock, rounds), scripted_jev(answers)
    monkeypatch.setattr(installed, "pinned_feed_fetcher", lambda profile: feeds)
    monkeypatch.setattr(installed, "pinned_jev_transport", lambda profile, *, timeout: jev)
    trust_owner_files(monkeypatch, owner_files(settings=settings_document()))
    owner = tmp_path / "owner"
    service = PaperService(flow.db, runtime.execution, clock=flow.clock, portfolio_ids=[flow.pid],
                           artifact_runtime=flow.runtime, secretary=flow.secretary, schedule_intervals={},
                           runtime_ready=binding.ready, subscription_provider=runtime.subscription_provider,
                           tick_interval_seconds=0.01,
                           event_engine=EngineOptions(scan=None, watcher=None, daily_cycle=False))

    def prepare():  # as run_subscription_operation's prepare: the protected binding, then the news check
        handlers = binding.prepare()
        service.news_poll = installed.installed_news_poll(runtime, owner)
        return handlers

    service.prepare_runtime = prepare
    return SimpleNamespace(flow=flow, service=service, feeds=feeds, jev=jev)


def _poll_starts(database) -> list[str]:
    return [row[0] for row in database.execute(
        "SELECT DISTINCT started_at FROM news_feed_polls ORDER BY started_at").fetchall()]


def test_the_service_polls_every_five_minutes_and_the_engine_routes_each_would_be_alarm(news) -> None:
    flow, service = news.flow, news.service
    start = flow.clock.now()

    async def scenario():
        at = 0
        for minute in (0, 1, 4, 5, 6, 10, 14):
            flow.clock.advance((minute - at) * 60)
            at = minute
            result = await service.tick()
            assert not [item for item in result.failures if item.startswith("news_poll")], result.failures
            await settle(service)
        await service.tick()  # the engine's step after the last poll's verdicts are stored
        await settle(service)
        await service.stop()

    asyncio.run(scenario())
    database = flow.db
    # Polls at 0, 5 and 10 minutes only: the steps at 1, 4, 6 and 14 minutes found nothing due.
    assert [(parse_utc(value) - start).total_seconds() for value in _poll_starts(database)] == [0, 300, 600]
    assert service.news_poll.polls == 3 and len(news.feeds.calls) == 3 * 5
    # Each headline once, checked by Jev with a synthetic receipt; the two hack reports would wake the Leader.
    headlines = {row[0] for row in database.execute("SELECT link FROM news_headlines")}
    assert headlines == {HACK[1], QUIET[1], HACK[1] + "-follow-up"}
    assert len(news.jev.calls) == 3
    assert database.execute("SELECT COUNT(*) FROM usage_receipts WHERE synthetic = 1 AND status = 'committed'"
                            ).fetchone()[0] == 3
    routes = [dict(row) for row in database.execute(
        "SELECT r.department, r.mode, r.because, h.link FROM news_alarm_wake_routes r "
        "JOIN news_headlines h USING (headline_id) ORDER BY h.link")]
    assert routes == [{"department": "leader", "mode": "shadow", "because": "alarm", "link": HACK[1]},
                      {"department": "leader", "mode": "shadow", "because": "alarm", "link": HACK[1] + "-follow-up"}]
    # Shadow mode: nobody was woken by a news alarm.
    assert database.execute("SELECT COUNT(*) FROM wake_triggers WHERE kind = 'news_alarm'").fetchone()[0] == 0


def test_a_restart_within_five_minutes_does_not_poll_again(news) -> None:
    flow, service = news.flow, news.service

    async def run(minutes):
        flow.clock.advance(minutes * 60)
        await service.tick()
        await settle(service)
        await service.stop()

    asyncio.run(run(0))
    asyncio.run(run(2))  # a new start two minutes later reads the stored poll and waits
    assert len(_poll_starts(flow.db)) == 1
    asyncio.run(run(3))  # five minutes after the first poll
    assert len(_poll_starts(flow.db)) == 2


def test_a_failed_news_step_is_reported_and_management_goes_on(news, monkeypatch) -> None:
    service = news.service

    async def scenario():
        await service.start()
        monkeypatch.setattr(service.news_poll, "step", lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
        first = await service.tick()
        await settle(service)
        second = await service.tick()  # reports the failed step and starts the next one
        await settle(service)
        await service.stop()
        return first, second

    first, second = asyncio.run(scenario())
    assert second.management is not None and "news_poll:RuntimeError" in second.failures
    assert not [item for item in first.failures if item.startswith("news_poll")]


def test_a_tick_never_waits_for_the_news_check_and_a_stop_ends_it_between_feeds(news, monkeypatch) -> None:
    flow, service = news.flow, news.service
    entered, release = threading.Event(), threading.Event()
    fetch = news.feeds.fetch

    def slow(feed, **kwargs):
        if feed.feed_id == "kraken-status":
            entered.set()
            assert release.wait(DEADLINE)
        return fetch(feed, **kwargs)

    monkeypatch.setattr(news.feeds, "fetch", slow)

    async def scenario():
        await service.tick()
        assert await asyncio.to_thread(entered.wait, DEADLINE)
        second = await service.tick()  # the first news step still waits in its first feed; management ran again
        assert second.management is not None and not service._news_task.done()
        service.request_stop()
        release.set()
        await service.stop()

    asyncio.run(scenario())
    # The feed in flight finished; no feed after it was read and no Jev call started after the stop.
    assert news.feeds.calls == ["kraken-status"] and news.jev.calls == []
    [poll] = [json.loads(row[0]) for row in flow.db.execute(
        "SELECT payload_json FROM activity_events WHERE kind = 'news_poll'").fetchall()]
    assert poll["status"] == "stopped"
