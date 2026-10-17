"""T45: news alarms reach the event engine. In shadow mode software records whom it would wake and why; with the
owner's switch live it wakes the Leader, and the Trader too when the coin is held. A news alarm only adds wake-ups.

Everything is synthetic: invented headlines on a synthetic feed, scripted Jev answers (receipted as synthetic), the
synthetic USD10,000 paper account and scripted departments. No network, key, paid call, model or real order.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest
from tests.alarm_support import quote_request
from tests.news_wake_support import (
    SOL_DELISTING,
    SOL_DELISTING_TITLE,
    NewsDesk,
    hold_sol,
    news_triggers,
    routes,
    switch_news_wakes,
)
from tests.wake_support import engine_account, engine_for, run_all, run_one, scripted, wake_input

from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.news_wakes import NewsWakeRouter, news_wake_setting
from trade_graph.contracts.news_wakes import NOTE_PREFIX


def _engine(tmp_path):
    """The engine without its daily cycle, so each test sees only the wakes it makes."""
    return engine_account(tmp_path, daily_cycle=False)


def _wakes(account, department: str | None = None) -> list[dict]:
    rows = account.database.execute(
        "SELECT * FROM wakes WHERE (? IS NULL OR department = ?) ORDER BY sequence", (department, department))
    return [dict(row) for row in rows]


def _news_wakes(account, department: str) -> list[dict]:
    return [wake for wake in _wakes(account, department) if "news_alarm" in json.loads(wake["context_json"])["reasons"]]


# --- shadow mode (the shipped setting) ----------------------------------------------------------------------------


def test_in_shadow_mode_a_would_wake_alarm_is_recorded_with_whom_and_why_and_wakes_nobody(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    desk = NewsDesk(account)
    desk.delisting()
    summary = desk.poll()
    assert summary.would_wake == 1 and summary.wakes_sent == 0
    report = engine.step([account.portfolio])
    assert report.failures == ()
    (route,) = routes(account)
    assert (route["department"], route["mode"], route["because"], route["trigger_id"]) == (
        "leader", "shadow", "alarm", None)
    assert route["portfolio_id"] == account.portfolio and route["symbol"] == "SOL/USD"
    assert route["note"].startswith(f'{NOTE_PREFIX}: Kraken blog reported "{SOL_DELISTING_TITLE}"')
    reason = json.loads(route["reason_json"])
    assert reason["subjects"] == ["SOL"] and reason["event_type"] == "delisting" and reason["holder_yes"] == "0.93"
    assert reason["held_at_check"] == [] and reason["held_now"] == []
    assert news_triggers(account) == [] and _news_wakes(account, "leader") == []
    assert news_wake_setting(account.database).mode == "shadow"


def test_shadow_logging_continues_after_the_switch_goes_live(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    engine.step([account.portfolio])
    verdicts = account.database.execute("SELECT would_wake, wake_roles_json FROM news_alarm_verdicts").fetchall()
    assert [tuple(row) for row in verdicts] == [(1, '["leader"]')]  # T44's own shadow log, as before
    (route,) = routes(account)
    assert route["mode"] == "live" and route["policy_revision"] == "owner-news-live"


# --- live mode ------------------------------------------------------------------------------------------------------


def test_live_mode_wakes_the_leader_with_the_headline_its_source_and_jevs_answers(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    seen: list = []
    handlers = {"leader": scripted({"summary": "Synthetic: noted the delisting."}, seen=seen),
                "trader": scripted({"summary": "Synthetic."})}
    run_all(scheduler, engine, handlers, [account.portfolio])
    (trigger,) = news_triggers(account)
    (route,) = routes(account)
    assert trigger["department"] == "leader" and trigger["trigger_id"] == route["trigger_id"]
    assert trigger["source_ref"] == f"software:news-alarm:{route['route_id']}" and trigger["status"] == "delivered"
    (wake,) = [task for task in seen if "news_alarm" in wake_input(task)["reasons"]]
    handed = wake_input(wake)
    assert handed["purpose"] == "event" and handed["reasons"] == ["news_alarm"]
    (news,) = handed["triggers"]
    assert news["requested_by"] == "software" and news["symbol"] == "SOL/USD"
    assert "Kraken blog" in news["note"] and SOL_DELISTING_TITLE in news["note"] and SOL_DELISTING in news["note"]
    assert "delisting" in news["note"] and "yes 0.93" in news["note"]
    assert "data" in handed["instruction"]
    assert [wake["status"] for wake in _news_wakes(account, "leader")] == ["completed"]
    assert _news_wakes(account, "trader") == []  # the paper account holds no SOL


def test_the_trader_is_woken_too_when_the_coin_is_held(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    hold_sol(account)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    seen: list = []
    handlers = {"leader": scripted({"summary": "Synthetic."}), "trader": scripted({"summary": "Synthetic."}, seen=seen)}
    run_all(scheduler, engine, handlers, [account.portfolio])
    assert {(route["department"], route["because"], route["mode"]) for route in routes(account)} == {
        ("leader", "alarm", "live"), ("trader", "held_at_check", "live")}
    assert json.loads(routes(account)[1]["reason_json"])["held_at_check"] == ["SOL"]
    trader_news = [task for task in seen if "news_alarm" in wake_input(task)["reasons"]]
    assert len(trader_news) == 1
    assert any(SOL_DELISTING_TITLE in (trigger.get("note") or "") for trigger in wake_input(trader_news[0])["triggers"])
    assert len(_news_wakes(account, "leader")) == len(_news_wakes(account, "trader")) == 1


def test_a_coin_bought_after_jev_was_asked_still_wakes_the_trader(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()  # SOL is only on the coin list when Jev is asked
    hold_sol(account)
    engine.step([account.portfolio])
    assert {(route["department"], route["because"]) for route in routes(account)} == {
        ("leader", "alarm"), ("trader", "held_now")}


def test_a_headline_about_kraken_itself_wakes_the_trader_while_anything_is_held(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.headline("Kraken announces synthetic maintenance (synthetic)", "https://status.kraken.com/synthetic-1",
                  concerns={"kraken": "0.9", "none": "0.1"}, event="exchange_outage_or_maintenance", yes="0.8")
    desk.poll()
    engine.step([account.portfolio])
    assert {route["department"] for route in routes(account)} == {"leader"}
    hold_sol(account)
    desk.headline("Kraken announces more synthetic maintenance (synthetic)", "https://status.kraken.com/synthetic-2",
                  concerns={"kraken": "0.9", "none": "0.1"}, event="exchange_outage_or_maintenance", yes="0.8")
    desk.poll()
    engine.step([account.portfolio])
    second = [route for route in routes(account) if "more synthetic" in route["note"]]
    assert {(route["department"], route["because"]) for route in second} == {
        ("leader", "alarm"), ("trader", "kraken_while_holding")}
    assert all(route["symbol"] is None for route in second)


def test_an_unrelated_headline_wakes_nobody(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    hold_sol(account)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.unrelated()
    summary = desk.poll()
    assert summary.answered == 1 and summary.would_wake == 0
    run_all(scheduler, engine, {"leader": scripted(), "trader": scripted()}, [account.portfolio])
    assert routes(account) == [] and news_triggers(account) == []


def test_live_wake_ups_start_with_alarms_recorded_from_the_switch_onwards(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()  # recorded while the switch is in shadow mode, routed only after it went live
    account.clock.advance(60)
    switch_news_wakes(account, "live", "owner-news-live")
    desk.delisting("BTC")
    desk.poll()
    engine.step([account.portfolio])
    modes = {route["symbol"]: route["mode"] for route in routes(account)}
    assert modes == {"SOL/USD": "shadow", "BTC/USD": "live"}
    assert [trigger["department"] for trigger in news_triggers(account)] == ["leader"]
    setting = news_wake_setting(account.database)
    assert setting.mode == "live" and setting.revision_id == "owner-news-live"


def test_live_lasts_through_later_owner_revisions_that_keep_it(tmp_path) -> None:
    account, _, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    since = news_wake_setting(account.database).live_since
    account.clock.advance(3600)
    policy = account.execution.authority.active_policy()
    account.execution.authority.install_policy(policy.model_copy(update={"revision_id": "owner-money"}), role="owner")
    setting = news_wake_setting(account.database)
    assert (setting.mode, setting.revision_id, setting.live_since) == ("live", "owner-money", since)
    switch_news_wakes(account, "shadow", "owner-news-shadow")
    assert news_wake_setting(account.database).mode == "shadow"


def test_each_alarm_is_routed_once_also_after_a_restart(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    engine.step([account.portfolio])
    engine.step([account.portfolio])
    _, restarted = engine_for(account, daily_cycle=False)  # a new engine on the same records, as after a restart
    restarted.step([account.portfolio])
    assert len(routes(account)) == 1 and len(news_triggers(account)) == 1


def test_an_unreadable_or_missing_switch_counts_as_shadow(tmp_path) -> None:
    connection = sqlite3.connect(tmp_path / "empty.sqlite")
    assert news_wake_setting(connection).mode == "shadow"
    connection.execute("CREATE TABLE owner_policy_revisions (revision_id TEXT, document_json TEXT, content_hash TEXT,"
                       " created_at TEXT)")
    connection.execute("INSERT INTO owner_policy_revisions VALUES ('broken', '{not json', 'x', '2026-10-16T08:00:00Z')")
    setting = news_wake_setting(connection)
    assert setting.mode == "shadow" and setting.revision_id is None
    connection.close()


# --- a news alarm only adds wake-ups (D116, D228) -------------------------------------------------------------------


def test_a_wake_made_only_by_news_alarms_keeps_the_departments_named_check_in(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    later = account.clock.now() + timedelta(hours=6)
    engine.request_wake(account.portfolio, "leader", "Synthetic: look at the plan.", requested_by="trader")
    run_all(scheduler, engine, {"leader": scripted({"check_in": {"at_utc": later.isoformat(), "note": "Synthetic"}})},
            [account.portfolio])
    assert engine.next_check_ins(account.portfolio)["leader"]["basis"] == "named"
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    run_all(scheduler, engine, {"leader": scripted({"summary": "Synthetic: read it."})}, [account.portfolio])
    assert [wake["status"] for wake in _news_wakes(account, "leader")] == ["completed"]
    kept = engine.next_check_ins(account.portfolio)["leader"]
    assert kept["basis"] == "named" and kept["due_at"].startswith(later.strftime("%Y-%m-%dT%H:%M"))
    # A news wake whose answer names a time sets it, as any wake does: the department decides.
    desk.delisting("BTC")
    desk.poll()
    sooner = account.clock.now() + timedelta(hours=1)
    run_all(scheduler, engine, {"leader": scripted({"check_in": {"at_utc": sooner.isoformat()}})},
            [account.portfolio])
    assert engine.next_check_ins(account.portfolio)["leader"]["due_at"].startswith(sooner.strftime("%Y-%m-%dT%H:%M"))


def test_a_news_alarm_and_a_price_alarm_arrive_together_and_neither_is_dropped(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    hold_sol(account)
    engine.step([account.portfolio])
    run_all(scheduler, engine, {"trader": scripted({"summary": "Synthetic."}), "leader": scripted()},
            [account.portfolio])  # the fill's own wake
    switch_news_wakes(account, "live", "owner-news-live")
    book = AlarmBook(account.database, account.clock)
    alarm = book.create(quote_request("99", "crosses_below", symbol="SOL/USD", portfolio_id=account.portfolio,
                                      note="Synthetic: below the entry."))
    delivery = book.fire(alarm.alarm_id, alarm.generation, observed={"price": "98.9"})
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    seen: list = []
    run_all(scheduler, engine, {"trader": scripted({"summary": "Synthetic."}, seen=seen), "leader": scripted()},
            [account.portfolio])
    (both,) = [task for task in seen if "news_alarm" in wake_input(task)["reasons"]]
    assert wake_input(both)["reasons"] == ["alarm_fired", "news_alarm"]
    assert [item["delivery_id"] for item in wake_input(both)["alarms"]] == [delivery.delivery_id]
    statuses = account.database.execute("SELECT kind, status FROM wake_triggers WHERE department = 'trader'").fetchall()
    assert {row["status"] for row in statuses} == {"delivered"}
    assert account.database.execute("SELECT status FROM alarm_deliveries WHERE delivery_id = ?",
                                    (delivery.delivery_id,)).fetchone()[0] == "delivered"


def test_a_news_alarm_for_a_busy_department_waits_for_its_next_wake_and_is_never_dropped(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    engine.request_wake(account.portfolio, "leader", "Synthetic: first.", requested_by="trader")
    engine.step([account.portfolio])
    assert len(_wakes(account, "leader")) == 1  # awake, its turn not yet run
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    engine.step([account.portfolio])
    (waiting,) = news_triggers(account)
    assert waiting["status"] == "pending" and len(_wakes(account, "leader")) == 1
    seen: list = []
    run_all(scheduler, engine, {"leader": scripted({"summary": "Synthetic."}, seen=seen)}, [account.portfolio])
    assert [wake_input(task)["reasons"] for task in seen] == [["wake_request"], ["news_alarm"]]
    assert news_triggers(account)[0]["status"] == "delivered"


def test_a_failing_news_source_holds_back_no_other_wake(tmp_path, monkeypatch) -> None:
    account, scheduler, engine = _engine(tmp_path)

    def broken(self, engine, portfolios):
        raise RuntimeError("synthetic news routing failure")

    monkeypatch.setattr(NewsWakeRouter, "collect", broken)
    engine.request_wake(account.portfolio, "trader", "Synthetic: look.", requested_by="leader")
    report = engine.step([account.portfolio])
    assert "news:RuntimeError" in report.failures
    assert report.created == 1 and len(_wakes(account, "trader")) == 1


def test_the_router_reads_the_engine_records_only_through_the_engine(tmp_path) -> None:
    account, scheduler, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    before = {row[0]: row[1] for row in account.database.execute(
        "SELECT kind, COUNT(*) FROM wake_triggers GROUP BY kind")}
    added = NewsWakeRouter(account.database, account.clock, account.execution).collect(engine, [account.portfolio])
    after = {row[0]: row[1] for row in account.database.execute(
        "SELECT kind, COUNT(*) FROM wake_triggers GROUP BY kind")}
    assert added == 1 and after == {**before, "news_alarm": 1}
    assert run_one(scheduler, engine, {"leader": scripted()}) is None  # adding a trigger dispatches nothing itself


@pytest.mark.parametrize("mode", ["shadow", "live"])
def test_routes_record_the_owner_revision_and_the_rule(tmp_path, mode) -> None:
    account, _, engine = _engine(tmp_path)
    switch_news_wakes(account, mode, f"owner-news-{mode}")
    desk = NewsDesk(account)
    desk.delisting()
    desk.poll()
    engine.step([account.portfolio])
    (route,) = routes(account)
    assert (route["mode"], route["policy_revision"], route["rule_version"]) == (
        mode, f"owner-news-{mode}", "news-wake-route-v1")
    verdict = account.database.execute("SELECT verdict_id, created_at FROM news_alarm_verdicts").fetchone()
    assert (route["verdict_id"], route["alarm_at"]) == (verdict["verdict_id"], verdict["created_at"])


def test_one_unreadable_verdict_never_holds_back_the_others(tmp_path, monkeypatch) -> None:
    account, _, engine = _engine(tmp_path)
    switch_news_wakes(account, "live", "owner-news-live")
    desk = NewsDesk(account)
    desk.delisting()
    desk.delisting("BTC")
    desk.poll()
    original = NewsWakeRouter._route

    def flaky(self, engine, portfolio_id, row, setting, held):
        if "BTC" in row["title"]:  # the first verdict routed (Jev was asked about the BTC headline first)
            raise ValueError("synthetic unreadable verdict")
        return original(self, engine, portfolio_id, row, setting, held)

    monkeypatch.setattr(NewsWakeRouter, "_route", flaky)
    report = engine.step([account.portfolio])
    assert "news:ValueError" in report.failures
    assert [route["symbol"] for route in routes(account)] == ["SOL/USD"] and report.created == 1
    monkeypatch.setattr(NewsWakeRouter, "_route", original)
    engine.step([account.portfolio])  # the next step routes it: nothing is lost
    assert sorted(route["symbol"] for route in routes(account)) == ["BTC/USD", "SOL/USD"]
