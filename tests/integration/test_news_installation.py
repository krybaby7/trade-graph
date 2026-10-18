"""The news check's installed route (W9-A, D255-D258): the owner's files, the Jev switch, money and protected origins.

The protected subscription binding is real (``subscription_service``: synthetic CLI, no model call) and prepared, so the
protected financial service and controller exist. The owner's folder is a dictionary of synthetic files; feeds are
scripted and labelled ``synthetic_fixture``; Jev is ``ScriptedJevHttp`` with a made-up key. Two tests mark a scripted
Jev transport as not synthetic to stand in for the pinned network transport on the money path; they say so and are
not Jev or paid-provider tests. No network, real key, paid call or order.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.integration.test_owned_subscription_service import subscription_service
from tests.news_service_support import (
    ANSWERS,
    FEED_IDS,
    HACK,
    KEY,
    NEWS_HOSTS,
    OLD,
    ORDERS,
    QUIET,
    SYNTHETIC_CARD,
    ScriptedFeeds,
    owner_files,
    scripted_jev,
    settings_document,
)
from tests.perplexity_service_support import trust_owner_files

from trade_graph.application import news_installation as installed
from trade_graph.application.budget import BudgetGateway
from trade_graph.domain.errors import StaleState
from trade_graph.domain.money import Money

ROUNDS = {"coindesk": [[(*HACK, 4)]], "decrypt": [[(*QUIET, 6)]], "cointelegraph": [[(*OLD, 180)]],
          "kraken-blog": [[(*ORDERS, 2)]]}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    flow, runtime, binding, _service, _cli, _current = subscription_service(tmp_path, monkeypatch)
    runtime.ledger, runtime.protected_deployment = runtime.execution.ledger, binding
    binding.prepare()  # the protected financial service and a running controller, as at the service's start
    feeds = ScriptedFeeds(flow.clock, ROUNDS)
    jev = scripted_jev(ANSWERS)
    built = []

    def fetcher(profile):
        built.append(("feeds", profile.route_url("news_feeds")))
        return feeds

    def transport(profile, *, timeout):
        built.append(("jev", profile.route_url("news_feeds"), timeout))
        return jev

    monkeypatch.setattr(installed, "pinned_feed_fetcher", fetcher)
    monkeypatch.setattr(installed, "pinned_jev_transport", transport)
    state = SimpleNamespace(flow=flow, runtime=runtime, binding=binding, feeds=feeds, jev=jev, built=built,
                            owner=tmp_path / "owner", monkeypatch=monkeypatch)
    state.serve = lambda **kwargs: trust_owner_files(monkeypatch, owner_files(**kwargs))
    return state


def _activities(database, kind):
    return [json.loads(row["payload_json"]) for row in database.execute(
        "SELECT payload_json FROM activity_events WHERE kind = ? ORDER BY rowid", (kind,)).fetchall()]


def _rows(database, sql, *params):
    return [dict(row) for row in database.execute(sql, params).fetchall()]


# --- what is built from the owner's folder --------------------------------------------------------------------------

def test_without_the_reviewed_route_nothing_is_read_or_fetched_and_the_reason_is_recorded(stack) -> None:
    reads = stack.serve(routes=None, settings=settings_document())
    assert installed.installed_news_poll(stack.runtime, stack.owner) is None
    assert reads == ["subscription-network-profile.json"] and stack.built == [] and stack.feeds.calls == []
    [route] = _activities(stack.flow.db, "news_route")
    assert route["status"] == "not_ready" and "lists no reviewed news_feeds route" in route["reason"]


def test_without_owner_settings_the_defaults_collect_headlines_and_jev_stays_off_without_reading_a_key(stack) -> None:
    reads = stack.serve(settings=None)
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    assert poll is not None and "typesafe.key" not in reads
    [route] = _activities(stack.flow.db, "news_route")
    assert (route["status"], route["settings"], route["jev"]) == ("ready", "recommended_defaults", "off")
    assert "jev.calls_enabled" in route["jev_reason"] and route["feeds"] == list(FEED_IDS)
    report = poll.step()
    assert report["jev"] == "off" and report["checks"] == 0 and stack.jev.calls == []
    assert stack.flow.db.execute("SELECT COUNT(*) FROM news_headlines").fetchone()[0] == 4
    assert stack.flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_settings_the_owner_installed_are_refused_by_field_name_only(stack) -> None:
    stack.serve(settings=settings_document(poll_interval_seconds=5) | {"extra_watch_coins": ["synthetic-secret/x"]})
    assert installed.installed_news_poll(stack.runtime, stack.owner) is None
    [route] = _activities(stack.flow.db, "news_route")
    assert route["status"] == "not_ready" and "poll_interval_seconds" in route["reason"]
    assert "extra_watch_coins" in route["reason"] and "synthetic-secret" not in json.dumps(route)
    stack.serve(settings=settings_document(testing_alert_usd="not-a-number"))
    assert installed.installed_news_poll(stack.runtime, stack.owner) is None
    assert "settings are refused (InvalidOperation)" in _activities(stack.flow.db, "news_route")[-1]["reason"]


@pytest.mark.parametrize(("files", "reason"), [
    ({"key": None}, "no Jev key: the owner folder has no typesafe.key"),
    ({"key": "short"}, "the Jev key file is refused (ValueError)"),
    ({"routes": {"news_feeds": {"port": 8083, "hosts": [h for h in NEWS_HOSTS if h != "api.typesafe.ai"]}}},
     "does not list api.typesafe.ai"),
    ({"settings": settings_document(card=SYNTHETIC_CARD.model_copy(update={"verified_at": "2025-11-30"}))},
     "more than 30 days ago"),
    ({"settings": settings_document(calls_enabled=False)}, "Jev calls are off"),
])
def test_jev_stays_off_with_the_reason_unless_every_prerequisite_holds(stack, files, reason) -> None:
    stack.serve(**({"settings": settings_document()} | files))
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    assert poll is not None
    [route] = _activities(stack.flow.db, "news_route")
    assert route["jev"] == "off" and reason in route["jev_reason"], route
    report = poll.step()
    assert report["checks"] == 0 and stack.jev.calls == [] and report["new_headlines"] == 4
    assert stack.flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0
    assert all(item[0] != "jev" for item in stack.built)


# --- a poll with Jev on: receipts and protected origins --------------------------------------------------------------

def test_a_poll_with_jev_on_checks_fresh_headlines_with_receipts_bound_to_protected_origins(stack) -> None:
    reads = stack.serve(settings=settings_document())
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    assert {"news-alarm.json", "typesafe.key"} <= set(reads)
    assert stack.built == [("jev", "http://172.29.17.2:8083", 5), ("feeds", "http://172.29.17.2:8083")]
    report = poll.step()
    database, pid = stack.flow.db, stack.flow.pid
    assert (report["status"], report["jev"], report["new_headlines"], report["checks"], report["would_wake"]) == (
        "ok", "scripted", 4, 3, 1)  # the three-hour-old summary is stored and not checked
    assert report["not_checked_old"] == 1 and report["wakes_sent"] == 0
    # The key went only into the request header; the headline only into Jev's state, as quoted data.
    assert {call["headers"]["authorization"] for call in stack.jev.calls} == {f"Bearer {KEY}"}
    assert {call["body"]["state"]["headline"]["link"] for call in stack.jev.calls} == {HACK[1], QUIET[1], ORDERS[1]}
    reservations = _rows(database, "SELECT * FROM budget_reservations ORDER BY rowid")
    assert len(reservations) == 3
    assert {(row["deployment_id"], row["role"], row["purpose"], row["task_id"], row["synthetic"], row["state"],
             row["price_card_id"]) for row in reservations} == {
        ("deployment:news_alarm", "news_alarm", "news_alarm:jev", None, 1, "COMMITTED", SYNTHETIC_CARD.price_card_id)}
    receipts = _rows(database, "SELECT r.*, a.portfolio_id, a.weight FROM usage_receipts r "
                               "JOIN cost_allocations a USING (receipt_id)")
    assert len(receipts) == 3 and {(row["status"], row["synthetic"], row["portfolio_id"], row["weight"])
                                   for row in receipts} == {("committed", 1, pid, "1")}
    origins = _rows(database, "SELECT reservation_id, event_kind, deployment_id, portfolio_id "
                              "FROM protected_financial_budget_origins ORDER BY rowid")
    assert {(row["event_kind"], row["deployment_id"], row["portfolio_id"]) for row in origins} == {
        ("ORIGIN", "deployment:news_alarm", pid), ("RECEIPT", "deployment:news_alarm", pid)}
    assert {row["reservation_id"] for row in origins} == {row["reservation_id"] for row in reservations}
    assert len(origins) == 6
    # The subscription deployment's protected books are unchanged by Jev's records and still verify.
    financial = stack.binding.protected.financial
    financial.origins.verify_all()
    assert financial.history.ready(pid)
    verdicts = _rows(database, "SELECT h.link, v.would_wake, v.wake_roles_json FROM news_alarm_verdicts v "
                               "JOIN news_headlines h USING (headline_id) ORDER BY h.link")
    assert {row["link"]: (row["would_wake"], json.loads(row["wake_roles_json"])) for row in verdicts} == {
        HACK[1]: (1, ["leader"]), QUIET[1]: (0, []), ORDERS[1]: (0, [])}
    dump = "\n".join(database.connection.iterdump())
    assert KEY not in dump and "Bearer" not in dump


def test_a_changed_reservation_is_found_by_the_route_audit_and_jev_stops_with_the_reason(stack) -> None:
    stack.serve(settings=settings_document())
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    poll.step()
    database = stack.flow.db
    with database.immediate() as connection:  # a reinterpreted settled amount would refill the owner's limit
        connection.execute("UPDATE budget_reservations SET amount = '0' WHERE rowid = "
                           "(SELECT MIN(rowid) FROM budget_reservations)")
    stack.feeds.rounds["coindesk"] = [[("Synthetic: a second BTC headline", HACK[1] + "-2", 1)]]
    stack.flow.clock.advance(300)
    calls = len(stack.jev.calls)
    report = poll.step()
    assert report["jev"] == "off" and "protected budget origins were refused" in report["jev_reason"]
    assert len(stack.jev.calls) == calls and report["new_headlines"] == 1  # headlines go on; nothing is sent


def test_a_reservation_without_its_protected_origin_is_never_dispatched(stack) -> None:
    stack.serve(settings=settings_document())
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    budget = poll.jev.budget
    plain = BudgetGateway(stack.flow.db, stack.flow.clock).reserve(
        deployment_id="deployment:news_alarm", role="news_alarm", task_id=None, root_task_id=None,
        price_card_id=SYNTHETIC_CARD.price_card_id, max_input=10, max_output=0, max_tools=0, fx_rate=Decimal("1"),
        fx_buffer=Decimal("1.02"), priority=False, synthetic=True, purpose="news_alarm:jev")
    with pytest.raises(StaleState):
        budget.protect_dispatch(plain)
    with pytest.raises(StaleState):
        budget.audit()


@pytest.mark.parametrize("change", [{"role": "leader"}, {"purpose": "research:search"},
                                    {"deployment_id": "deployment"}, {"task_id": "a-task"},
                                    {"price_card_id": "typesafe-jev-1.13.0-2026-10-15"}])
def test_the_route_admits_only_its_own_jev_checks(stack, change) -> None:
    stack.serve(settings=settings_document())
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    BudgetGateway(stack.flow.db, stack.flow.clock).seed_card(installed.JEV_PRICE_CARD)
    request = dict(deployment_id="deployment:news_alarm", role="news_alarm", task_id=None, root_task_id=None,
                   price_card_id=SYNTHETIC_CARD.price_card_id, max_input=10, max_output=0, max_tools=0,
                   fx_rate=Decimal("1"), fx_buffer=Decimal("1.02"), priority=False, synthetic=True,
                   purpose="news_alarm:jev")
    with pytest.raises(installed.NewsRouteRefused):
        poll.jev.budget.reserve(**(request | change))
    assert stack.flow.db.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_jev_waits_while_the_protected_controller_is_not_running(stack, monkeypatch) -> None:
    stack.serve(settings=settings_document())
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    monkeypatch.setattr(poll.jev.budget.controller, "status", lambda: {"status": "MANAGEMENT_ONLY"})
    report = poll.step()
    assert report["jev"] == "off" and "management only" in report["jev_reason"] and stack.jev.calls == []


def test_the_owners_paid_call_limit_refuses_a_check_with_no_room_and_sends_nothing(stack) -> None:
    """A scripted Jev stands in for the pinned network transport (``synthetic`` off) so that the money rule applies;
    its replies report no usage, so each call stays unresolved at its full hold. Not a Jev or paid-provider test."""
    authority = stack.flow.office.execution.authority
    policy = authority.active_policy()
    authority.install_policy(policy.model_copy(update={
        "revision_id": "synthetic-small-paid-limit", "daily_paid_limit": Money(amount="0.004", currency="EUR")}),
        role="owner")
    stack.serve(settings=settings_document())
    stack.jev.synthetic = False
    unreported = stack.jev.responder

    def without_usage(body):
        reply = unreported(body)
        reply.pop("usage")
        return reply

    stack.jev.responder = without_usage
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    report = poll.step()
    database = stack.flow.db
    # One check holds EUR 0.00274176 (64,000 tokens at USD 0.042 per million, 1:1, plus 2%); a second would pass
    # the EUR 0.004 limit, so it is refused before anything is sent and the poll's other checks wait.
    assert len(stack.jev.calls) == 1 and report["failures"] == {"budget_refused": 1}
    [hold] = _rows(database, "SELECT state, amount, synthetic FROM budget_reservations")
    assert (hold["state"], hold["amount"], hold["synthetic"]) == ("UNCERTAIN", "0.00274176", 0)
    outcomes = [row["status"] for row in _rows(database, "SELECT status FROM news_jev_outcomes ORDER BY rowid")]
    assert outcomes == ["answered", "budget_refused"]  # answered, but its bill is unknown: held, never free


def test_the_route_reads_the_owner_limits_per_utc_day_and_month(stack) -> None:
    """The same stand-in transport as above: a held check counts against today's and this month's limits."""
    authority = stack.flow.office.execution.authority
    policy = authority.active_policy()
    authority.install_policy(policy.model_copy(update={
        "revision_id": "synthetic-month-limit", "daily_paid_limit": Money(amount="1", currency="EUR"),
        "monthly_operating": Money(amount="0.00279", currency="EUR")}), role="owner")
    stack.serve(settings=settings_document())
    stack.jev.synthetic = False
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    poll.step()
    assert len(stack.jev.calls) == 3  # each settles to EUR 0.00001764, so the month had room for each next hold
    stack.flow.clock.advance(timedelta(days=1).total_seconds())
    stack.feeds.rounds["coindesk"] = [[("Synthetic: one more BTC headline", HACK[1] + "-3", 1)]]
    poll.step()
    # Three settled checks (EUR 0.00005292) and the next hold (EUR 0.00274176) would pass the month's EUR 0.00279.
    assert len(stack.jev.calls) == 3


def test_a_changed_reservation_from_earlier_this_month_is_found_by_the_rolling_sweep(stack) -> None:
    stack.serve(settings=settings_document())
    poll = installed.installed_news_poll(stack.runtime, stack.owner)
    poll.step()
    stack.flow.clock.advance(timedelta(days=1).total_seconds())  # yesterday's checks are outside today's audit
    with stack.flow.db.immediate() as connection:
        connection.execute("UPDATE budget_reservations SET amount = '0' WHERE rowid = "
                           "(SELECT MIN(rowid) FROM budget_reservations)")
    calls = len(stack.jev.calls)
    stack.feeds.rounds["coindesk"] = [[("Synthetic: a BTC headline the next day", HACK[1] + "-4", 1)]]
    report = poll.step()
    assert report["jev"] == "off" and "protected budget origins were refused" in report["jev_reason"]
    assert len(stack.jev.calls) == calls
