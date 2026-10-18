"""A scripted day in the installed subscription service with the news check beside it (W9-A acceptance).

``run_subscription_operation`` runs the real protected subscription binding (synthetic CLI, no model call) with the
event engine on. With the reviewed ``news_feeds`` route in the owner's network profile, the service polls the news feeds
at start and every five minutes after, stores each headline, asks Jev (switched on in the owner's news alarm settings,
with the owner's key) about the fresh ones with receipts bound to protected budget origins, and the engine records
whom each urgent headline would wake: shadow mode, nobody is woken. With Jev off, headlines are stored and nothing is
called; without the route, nothing is fetched and the reason is recorded; a feed that cannot be read is reported.

Everything is synthetic: the account, a recorded live scan, the owner's folder (``tests/news_service_support``), the
Jev card re-dated for the test clock under a synthetic id, scripted feeds labelled ``synthetic_fixture`` and scripted
Jev answers with a made-up key. No network, real key, paid call or order; a scripted run is never a feed, Jev or
paid-provider test. The clock is frozen, so exactly one poll is due in a run. The service's stop ends a poll between
feeds and headlines (D255; ``test_news_poll_service.py`` tests that stop), so the scripted day lasts until the poll that
was due at start has finished: the service's stop first waits for the news step in flight, on the step itself with a
generous deadline. The outcome never depends on how fast this machine runs or which thread finishes first.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from tests.focus_research_support import record_scan
from tests.integration.test_owned_subscription_service import subscription_service
from tests.integration.test_subscription_trader_wakes import WAKE_GRAPH
from tests.leadership_support import reply as leader_reply
from tests.news_service_support import (
    ANSWERS,
    HACK,
    KEY,
    OLD,
    ORDERS,
    QUIET,
    ScriptedFeeds,
    owner_files,
    scripted_jev,
    settings_document,
)
from tests.perplexity_service_support import trust_owner_files

from trade_graph.adapters.persistence.db import Database
from trade_graph.application import news_installation as installed
from trade_graph.application import subscription_operation as operation
from trade_graph.application.paper_service import PaperService
from trade_graph.application.service_market_data import open_service_market_data
from trade_graph.paper_runtime import PaperRuntimeConfig

DEADLINE = 60  # seconds the due news step may take on a slow machine; the outcome never depends on it

ROUNDS = {"coindesk": [[(*HACK, 4)]], "decrypt": [[(*QUIET, 6)]], "cointelegraph": [[(*OLD, 180)]],
          "kraken-blog": [[(*ORDERS, 2)]]}


@pytest.fixture
def day(tmp_path, monkeypatch):
    flow, runtime, binding, _service, cli, _current = subscription_service(tmp_path, monkeypatch, source=WAKE_GRAPH)
    config = PaperRuntimeConfig(tick_interval_seconds=0.01, schedule_intervals={}, public_data_enabled=True,
                                event_engine=True, decision_candle_wait_seconds=0)
    runtime.config, runtime.ledger, runtime.public_feed = config, runtime.execution.ledger, None
    runtime.prepare_runtime, runtime.runtime_ready = binding.prepare, binding.ready
    runtime.protected_deployment = binding
    runtime.market_data = open_service_market_data(tmp_path / "market" / "market_data.sqlite", database=flow.db,
                                                   clock=flow.clock, portfolio_ids=[flow.pid])
    runtime.price_collector = None
    common = {"evidence_refs": [flow.ref], "summary": "Bounded evidence.", "outcome": "Insufficient sample."}
    cli.outputs.update({"leader": leader_reply([flow.ref]), "learning": {**common, "lessons": []},
                        "optimisation": {**common, "proposals": []},
                        "trader": {"day_plans": [], "next_check_in": None, "summary": "Synthetic: no plan today."}})
    record_scan(flow.db, ["BTC/USD", "ETH/USD", "SOL/USD"], as_of="2025-12-31T23:00:00.000000Z")
    monkeypatch.setattr(operation, "assert_boot_environment", lambda: None)
    monkeypatch.setattr(operation, "read_owner_file", lambda *_a: runtime.config.model_dump_json().encode())
    monkeypatch.setattr(operation, "assemble_paper_runtime", lambda _path, **_kwargs: runtime)
    feeds, jev, built = ScriptedFeeds(flow.clock, ROUNDS), scripted_jev(ANSWERS), []

    def fetcher(profile):
        built.append(("feeds", profile.route_url("news_feeds")))
        return feeds

    def transport(profile, *, timeout):
        built.append(("jev", profile.route_url("news_feeds")))
        return jev

    monkeypatch.setattr(installed, "pinned_feed_fetcher", fetcher)
    monkeypatch.setattr(installed, "pinned_jev_transport", transport)
    # The service closes its database and candle file when it stops; here the test owns them, so that a second start
    # (the same installation started again) can run on the same synthetic account.
    market, close_database, close_market = runtime.market_data, flow.db.close, runtime.market_data.close
    monkeypatch.setattr(flow.db, "close", lambda: None)
    monkeypatch.setattr(market, "close", lambda: None)
    # The day ends after the poll that was due at start: without this wait, a machine that finishes the 30 ticks before
    # the news thread finishes its poll would stop the poll between feeds (found by the wave 9 integration with slower
    # scripted feeds). Every later step finds nothing due and returns at once.
    stop = PaperService.stop

    async def stop_after_the_due_poll(service) -> None:
        task = service._news_task
        if task is not None and not task.done():
            done, _pending = await asyncio.wait({task}, timeout=DEADLINE)
            assert done, "the news step did not finish"
        await stop(service)

    monkeypatch.setattr(PaperService, "stop", stop_after_the_due_poll)
    state = SimpleNamespace(flow=flow, cli=cli, owner=tmp_path / "owner", feeds=feeds, jev=jev, built=built)
    state.serve = lambda **kwargs: trust_owner_files(monkeypatch, owner_files(**kwargs))
    yield state
    close_market()
    close_database()


def _run(day, ticks=30) -> tuple[dict, Database]:
    result = operation.run_subscription_operation(day.flow.db.path, day.owner, maximum_ticks=ticks)
    return result, Database(day.flow.db.path)


def _rows(database, sql, *params):
    return [dict(row) for row in database.execute(sql, params).fetchall()]


def _activity(database, kind):
    return [json.loads(row["payload_json"]) for row in _rows(
        database, "SELECT payload_json FROM activity_events WHERE kind = ? ORDER BY rowid", kind)]


def test_the_service_day_polls_records_headlines_and_jev_answers_and_the_engine_routes_in_shadow(day) -> None:
    reads = day.serve(settings=settings_document())
    result, database = _run(day)
    try:
        assert result["event_engine"] is True and result["news_polls"] == 1 and result["paid_authorization"] is False
        assert not [item for item in result["failures"] if item.startswith(("news_poll", "wakes"))], result["failures"]
        assert {"news-alarm.json", "typesafe.key"} <= set(reads)
        assert day.built == [("jev", "http://172.29.17.2:8083"), ("feeds", "http://172.29.17.2:8083")]
        [route] = _activity(database, "news_route")
        assert (route["status"], route["jev"], route["deployment_id"]) == ("ready", "on", "deployment:news_alarm")
        [poll] = _activity(database, "news_poll")
        assert (poll["status"], poll["new_headlines"], poll["checks"], poll["would_wake"], poll["wakes_sent"]) == (
            "ok", 4, 3, 1, 0)
        # Headlines stored as dated synthetic sources; Jev asked about the three fresh ones, with receipts.
        assert {row["evidence"] for row in _rows(database, "SELECT evidence FROM news_headlines")} == {
            "synthetic_fixture"}
        assert {call["body"]["state"]["headline"]["link"] for call in day.jev.calls} == {HACK[1], QUIET[1], ORDERS[1]}
        receipts = _rows(database, """SELECT r.status, r.synthetic, b.deployment_id, b.role, b.state
            FROM usage_receipts r JOIN budget_reservations b USING (reservation_id)""")
        assert len(receipts) == 3 and {tuple(row.values()) for row in receipts} == {
            ("committed", 1, "deployment:news_alarm", "news_alarm", "COMMITTED")}
        kinds = sorted(row["event_kind"] for row in _rows(
            database, "SELECT event_kind FROM protected_financial_budget_origins WHERE deployment_id = ?",
            "deployment:news_alarm"))
        assert kinds == ["ORIGIN"] * 3 + ["RECEIPT"] * 3
    finally:
        database.close()
    # The next start (the clock is frozen: no poll is due) runs the engine over the stored verdicts. The urgent BTC
    # headline would wake the Leader; the quiet one and the instruction-shaped one wake nobody; nobody is woken.
    second, database = _run(day, ticks=3)
    try:
        assert second["news_polls"] == 0 and len(day.jev.calls) == 3
        routes = _rows(database, """SELECT r.department, r.mode, h.link FROM news_alarm_wake_routes r
            JOIN news_headlines h USING (headline_id)""")
        assert routes == [{"department": "leader", "mode": "shadow", "link": HACK[1]}]
        assert database.execute("SELECT COUNT(*) FROM wake_triggers WHERE kind = 'news_alarm'").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
        dump = "\n".join(database.connection.iterdump())
        assert KEY not in dump and "Bearer" not in dump
    finally:
        database.close()


def test_with_jev_off_headlines_are_stored_and_nothing_is_called(day) -> None:
    reads = day.serve(settings=None)  # no owner settings: the recommended defaults, Jev off
    result, database = _run(day)
    try:
        assert result["news_polls"] == 1 and "typesafe.key" not in reads
        assert day.jev.calls == [] and [item[0] for item in day.built] == ["feeds"]
        assert database.execute("SELECT COUNT(*) FROM news_headlines").fetchone()[0] == 4
        for table in ("news_jev_attempts", "news_alarm_verdicts", "news_alarm_wake_routes", "budget_reservations"):
            assert database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        [poll] = _activity(database, "news_poll")
        assert poll["jev"] == "off" and "jev.calls_enabled" in poll["jev_reason"]
    finally:
        database.close()


def test_without_the_reviewed_route_the_service_runs_as_before_and_says_why(day) -> None:
    reads = day.serve(routes=None, settings=settings_document())
    result, database = _run(day)
    try:
        assert result["news_polls"] is None and reads == ["subscription-network-profile.json"]
        assert day.built == [] and day.feeds.calls == [] and day.jev.calls == []
        [route] = _activity(database, "news_route")
        assert route["status"] == "not_ready" and "no reviewed news_feeds route" in route["reason"]
        assert database.execute("SELECT COUNT(*) FROM news_feed_polls").fetchone()[0] == 0
        assert not [item for item in result["failures"] if item.startswith("news_poll")]
    finally:
        database.close()


def test_a_feed_that_cannot_be_read_is_reported_as_lost_never_as_no_news(day) -> None:
    day.serve(settings=settings_document())
    day.feeds.fail["coindesk"] = "transport_error"
    result, database = _run(day)
    try:
        [poll] = _activity(database, "news_poll")
        assert poll["status"] == "partial" and [item["feed_id"] for item in poll["lost_feeds"]] == ["coindesk"]
        assert poll["lost_feeds"][0]["why"] == "could not connect" and poll["lost_feeds"][0]["last_read_at_utc"] is None
        assert "could not read CoinDesk (could not connect, not read yet)" in poll["note"]
        assert "never taken as \"no news\"" in poll["note"]
        [row] = _rows(database, "SELECT outcome, items_new FROM news_feed_polls WHERE feed_id = 'coindesk'")
        assert row == {"outcome": "transport_error", "items_new": 0}
        # The other feeds' fresh headlines were still checked.
        assert {call["body"]["state"]["headline"]["link"] for call in day.jev.calls} == {QUIET[1], ORDERS[1]}
    finally:
        database.close()
