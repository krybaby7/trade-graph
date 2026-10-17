"""HQ's day plans read (W8-C, D251): ``GET /api/v1/hq/plans`` and its document, ``trade-graph.hq.plans.v1``.

Synthetic paper account, synthetic money limits and scripted quotes only (``tests/day_plan_support.py``); no network,
key, model, provider or exchange call. Reading the plans writes nothing and starts nothing.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from tests.day_plan_support import day_plan_account, entry, install, manage, plan
from tests.hq_trading_support import runtime_for, table_counts
from tests.money_limit_support import open_account

from trade_graph.api import hq_copy, hq_plans
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.hq_plans_contract import (
    EVENTS_MAX,
    FILLS_MAX,
    PAYLOAD_BUDGET_BYTES,
    PLANS_MAX,
    REFUSALS_MAX,
    SCHEMA,
    validate_plans,
)
from trade_graph.domain.clock import utc_iso

D = Decimal


def _text(value) -> str:
    return hq_copy.plain(value)


def _read(account) -> dict:
    document = hq_plans.plans(runtime_for(account))
    validate_plans(document)
    return document


def _cross(account, bid: str, ask: str, **options) -> None:
    account.clock.advance(1)
    account.quote("SOL/USD", bid, ask, **options)


# --- what the owner sees ---------------------------------------------------------------------------------------------


def test_without_money_limits_plans_are_off_and_the_document_says_so(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    document = _read(account)
    assert document["schema"] == SCHEMA and document["available"] is True
    assert document["limits_recorded"] is False and document["plans"] == []
    assert "money limits" in _text(document["headline"]) and "step L3" in _text(document["headline"])


def test_no_plan_today_is_said_plainly(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    document = _read(account)
    assert document["limits_recorded"] is True and document["plans"] == [] and document["refusals_total"] == 0
    assert _text(document["headline"]).startswith("No day plan today")
    assert (document["day"]["start"], document["day"]["end"]) == ("2026-10-16T00:00:00.000Z",
                                                                  "2026-10-17T00:00:00.000Z")


def test_a_plan_shows_its_waiting_buy_rationale_and_what_it_watches(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    installed = install(account)
    sending = _read(account)["plans"][0]["entries"][0]
    assert sending["status"] == "sending"
    account.dispatch()
    document = _read(account)
    (item,) = document["plans"]
    assert (item["id"], item["symbol"], item["current"], item["replaced_today"]) == (
        installed.plan_id, "SOL/USD", True, 0)
    (waiting,) = item["entries"]
    assert waiting["status"] == "waiting" and waiting["tone"] == "neutral"
    assert (waiting["quantity"], waiting["filled"], waiting["unprotected"]) == ("5", "0", "0")
    assert (waiting["limit_price"], waiting["stop_price"], waiting["take_profit_price"]) == ("99.5", "97", "104")
    words = _text(waiting["text"])
    assert "Buy 5 SOL at 99.5 USD" in words and "stop 97" in words and "target 104" in words
    assert "waiting" in words.lower()
    assert item["quote"]["text"].startswith("Synthetic plan: rest a bid") and item["quote"]["label"] == "In their words"
    assert item["invalidation"]["text"] == "Synthetic: a quote below 95."
    assert "RSI" in _text(item["watching"]) and "15-minute" in _text(item["watching"])
    assert "1 price alarm" in _text(item["watching"])
    assert "slow-trend-pullback" in _text(item["strategy"])
    assert any("Put in place" in _text(line) for line in item["events"])
    assert "1 waiting buy" in _text(item["headline"])
    assert "1 day plan today" in _text(document["headline"])


def test_a_filled_buy_shows_its_fill_and_its_safety_exit(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="2")  # a partial fill: 2 of 5
    manage(account)
    entry_view = _read(account)["plans"][0]["entries"][0]
    assert entry_view["status"] == "partly_filled" and entry_view["filled"] == "2" and entry_view["unprotected"] == "0"
    (fill,) = entry_view["fills"]
    assert "Bought 2 SOL at 99.5 USD" in _text(fill)
    (exit_view,) = entry_view["exits"]
    assert exit_view["status"] == "waiting" and "Safety exit for 2 SOL" in _text(exit_view["text"])
    assert "stop 97" in _text(exit_view["text"]) and "target 104" in _text(exit_view["text"])
    _cross(account, "99.4", "99.5", volume="100")
    manage(account)
    _cross(account, "96.9", "97.1", volume="100")  # through the stop: both exits sell
    entry_view = _read(account)["plans"][0]["entries"][0]
    assert entry_view["status"] == "filled" and entry_view["tone"] == "good"
    assert [item["status"] for item in entry_view["exits"]] == ["filled", "filled"]
    assert all("Sold" in _text(item["fills"][0]) for item in entry_view["exits"])


def test_a_filled_part_without_a_safety_exit_needs_attention(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")
    manage(account)
    account.execution.set_pause(account.portfolio, "CANCEL_ALL", "owner", "synthetic cancel all")
    asyncio.run(account.execution.advance_pause(account.portfolio))
    manage(account)
    entry_view = _read(account)["plans"][0]["entries"][0]
    assert entry_view["unprotected"] == "5" and entry_view["tone"] == "attention"
    assert "no safety exit right now" in _text(entry_view["text"])
    assert entry_view["exits"][0]["status"] == "cancelled"


def test_a_replaced_plan_is_counted_and_cancelled_buys_say_why(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    first = install(account)
    account.dispatch()
    account.clock.advance(600)
    account.quote("SOL/USD", "99.9", "100.1")
    second = install(account, plan(entries=[entry("4", "99", "96")]), request_id="plan-2")
    account.dispatch()
    document = _read(account)
    assert [item["id"] for item in document["plans"]] == [second.plan_id]
    assert document["plans"][0]["replaced_today"] == 1
    # The replaced plan has nothing open any more, so it isn't listed; its cancelled buy is a recorded event.
    events = account.database.execute("SELECT kind, reason FROM day_plan_events WHERE plan_id = ?",
                                      (first.plan_id,)).fetchall()
    assert ("entry_cancelled", "replaced") in [(row["kind"], row["reason"]) for row in events]
    view = hq_plans.plan_view(hq_plans.read_plan(account.database, first.plan_id, account.clock.now()),
                              account.clock.now())
    assert view["entries"][0]["status"] == "cancelled"
    assert "replaced by a newer plan" in _text(view["entries"][0]["text"]).lower()
    assert any("Replaced by a newer plan" in _text(line) for line in view["events"])


def test_an_older_plan_still_protecting_coins_stays_listed(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    first = install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")
    manage(account)
    # The next day: yesterday's plan ended, but its safety exit still protects the 5 SOL.
    account.clock.advance((account.clock.now().replace(hour=8) + timedelta(days=1) - account.clock.now())
                          .total_seconds())
    account.quote("SOL/USD", "99.9", "100.1")
    document = _read(account)
    (item,) = document["plans"]
    assert item["id"] == first.plan_id and item["current"] is False
    assert "earlier day" in _text(item["headline"]).lower() and item["entries"][0]["exits"][0]["status"] == "waiting"
    assert _text(document["headline"]).startswith("No day plan today")


def test_todays_refusals_are_listed_in_plain_words_newest_first(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    with pytest.raises(Exception, match="rests below the market"):
        install(account, plan(entries=[entry("5", "100.1", "97")]), request_id="refused-1")
    account.clock.advance(60)
    with pytest.raises(Exception, match="trading day"):
        install(account, plan(entries=[entry("5", "99.5", "97", expires_at_utc="2026-10-17T00:00:01Z")]),
                request_id="refused-2")
    document = _read(account)
    assert document["refusals_total"] == 2 and len(document["refusals"]) == 2
    newest, oldest = document["refusals"]
    assert newest["at"] > oldest["at"] and newest["symbol"] == "SOL/USD"
    assert "outside today's trading day" in _text(newest["text"])
    assert "at or above the current price" in _text(oldest["text"])
    assert "rests below the market" in oldest["technical"]
    assert "2 plans refused today" in _text(document["headline"])


def test_a_database_without_day_plan_records_says_so(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    runtime = runtime_for(account)
    original = hq_plans.REQUIRED_TABLES
    try:
        hq_plans.REQUIRED_TABLES = (*original, "a_table_this_database_lacks")
        document = hq_plans.plans(runtime)
    finally:
        hq_plans.REQUIRED_TABLES = original
    validate_plans(document)
    assert document["available"] is False and document["plans"] == [] and document["day"] is None
    assert "no day-plan records" in _text(document["status_text"]).lower()


# --- bounds, reads and the route -------------------------------------------------------------------------------------


def test_lists_are_bounded_and_say_how_many_more(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    now = account.clock.now()
    record = hq_plans.read_plan(account.database, _read(account)["plans"][0]["id"], now)
    fill = {"symbol": "SOL/USD", "side": "buy", "quantity": "0.5", "price": "99.5", "fee_amount": "0.4",
            "fee_asset": "USD", "filled_at_utc": utc_iso(now)}
    record["entries"][0]["fills"] = [fill] * (FILLS_MAX + 3)
    record["entries"][0]["state"] = "FILLED"
    record["entries"][0]["quantity"] = str(D("0.5") * (FILLS_MAX + 3))
    record["events"] = record["events"] * (EVENTS_MAX + 2)
    view = hq_plans.plan_view(record, now)
    assert len(view["entries"][0]["fills"]) == FILLS_MAX and view["entries"][0]["more_fills"] == 3
    assert len(view["events"]) == EVENTS_MAX and view["more_events"] == len(record["events"]) - EVENTS_MAX


def test_a_busy_synthetic_day_fits_the_endpoints_budget(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    now = account.clock.now()
    record = hq_plans.read_plan(account.database, _read(account)["plans"][0]["id"], now)
    template = record["entries"][0]
    fill = {"symbol": "SOL/USD", "side": "buy", "quantity": "1.2345", "price": "99.5", "fee_amount": "0.98765",
            "fee_asset": "USD", "filled_at_utc": utc_iso(now)}
    sold = {**fill, "side": "sell", "price": "96.9"}
    entries = []
    for position in range(4):
        exits = [{"exit_id": f"x-{position}-{index}", "intent_id": f"i-x-{position}-{index}", "quantity": "1.2345",
                  "stop_price": "97", "take_profit_price": "104", "state": "FILLED", "cause": "fill",
                  "created_at": utc_iso(now), "fills": [sold]} for index in range(2)]
        entries.append({**template, "entry_id": f"{template['entry_id'][:-2]}-{position}", "position": position,
                        "quantity": "2.469", "state": "FILLED", "fills": [fill, fill], "exits": exits})
    events = [{"kind": "protection_deferred", "entry_id": template["entry_id"], "created_at": utc_iso(now),
               "reason": "StaleState: synthetic: no quote yet " + "x" * 200, "document": {"exits": 1}}] * 10
    busy = []
    for index in range(PLANS_MAX):
        symbol = f"C{index:02d}/USD"
        busy.append({**record, "plan_id": f"dp-{index:032x}", "symbol": symbol, "entries": entries,
                     "events": events, "replaced_today": 3,
                     "document": {**record["document"], "rationale": "r" * 1000, "invalidation": "i" * 500}})
    refusals = [{"created_at": utc_iso(now - timedelta(seconds=index)),
                 "payload": {"symbol": "SOL/USD", "reason": "entry 1: a day-plan entry rests below the market; "
                             + "y" * 400}} for index in range(REFUSALS_MAX + 30)]
    document = hq_plans.document(now=now, day=hq_plans.trading_day_of(account.database, now), limits_recorded=True,
                                 plans=[hq_plans.plan_view(item, now) for item in busy], more_plans=4,
                                 refusals=refusals, refusals_total=len(refusals))
    validate_plans(document)
    size = len(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    print(f"busy synthetic day of plans: {size} B")
    assert size <= PAYLOAD_BUDGET_BYTES


def test_reading_the_plans_writes_nothing(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    _cross(account, "99.4", "99.5", volume="100")
    before = table_counts(account.database)
    changes = account.database.execute("SELECT total_changes()").fetchone()[0]
    _read(account)
    _read(account)
    assert table_counts(account.database) == before
    assert account.database.execute("SELECT total_changes()").fetchone()[0] == changes


@pytest.fixture
def served(tmp_path):
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    owner, _ = issue_session(account.database, account.clock, "owner")
    reader, _ = issue_session(account.database, account.clock, "reader")
    client = TestClient(create_app(runtime_for(account)))
    yield client, owner, reader


def test_any_signed_in_role_reads_the_plans_and_nobody_else(served) -> None:
    client, owner, reader = served
    response = client.get("/api/v1/hq/plans", follow_redirects=False)
    assert response.status_code == 401
    for token in (owner, reader):
        response = client.get("/api/v1/hq/plans", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        document = response.json()
        validate_plans(document)
        assert document["plans"][0]["symbol"] == "SOL/USD"
    assert client.post("/api/v1/hq/plans", headers={"Authorization": f"Bearer {owner}"}).status_code == 405
