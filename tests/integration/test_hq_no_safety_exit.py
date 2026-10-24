"""HQ asks you when a bought coin has no safety exit (W10-C, C9; D274-D278).

A "Needs you" card appears once a filled day-plan buy has been without full safety-exit cover for 10 minutes, says
which coin, how much, since when and what the system is doing about it, and clears once the exit is in place or the
position is closed. Reading HQ starts nothing, and the card has no button that trades.

Synthetic paper account, synthetic money limits and scripted quotes only (``tests/day_plan_support.py``); the refused
exit placement is scripted (``place_protection`` raises, as in ``test_day_plans.py``). The 10-minute delay is measured
on the account's frozen clock, never on the wall clock. No network, key, model, provider or exchange call.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.day_plan_action_support import act, position_action
from tests.day_plan_support import day_plan_account, install, manage
from tests.hq_trading_support import fullest_facts, runtime_for, table_counts
from tests.integration import test_hq_payload as payload
from tests.integration.test_dashboard import _client
from tests.money_limit_support import open_account

from trade_graph.api import hq, hq_copy, hq_plans, hq_protection, hq_trading, resources
from trade_graph.api.hq_contract import AttentionItem, iso_ms, validate_snapshot
from trade_graph.application import service_controller
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState

D = Decimal
OWNER = hq.Viewer("owner", "cookie", True)
READER = hq.Viewer("reader", "bearer", False)
LIVE_PID = 4243
REFUSAL = "synthetic: the exchange refused the stop"


def _text(value) -> str:
    return hq_copy.plain(value)


@pytest.fixture
def day(tmp_path, monkeypatch):
    """A synthetic paper account with today's SOL plan: a 5 SOL buy resting at 99.5 (stop 97, target 104)."""
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == LIVE_PID else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    account = day_plan_account(tmp_path)
    install(account)
    account.dispatch()
    original = account.execution.place_protection

    def refused(*args, **kwargs):
        raise StaleState(REFUSAL)

    def refuse(on: bool = True) -> None:
        monkeypatch.setattr(account.execution, "place_protection", refused if on else original)

    yield SimpleNamespace(account=account, runtime=runtime_for(account), refuse=refuse)
    account.database.close()


def running(day) -> None:
    """The team's service is running, with a fresh sign of life (as ``test_hq_projection.run`` writes it)."""
    now = day.account.clock.now()
    database = day.account.database
    database.execute("DELETE FROM graph_service_runs WHERE run_id = 'r-protect'")
    database.execute(
        "INSERT INTO graph_service_runs (run_id, portfolio_id, mode, status, pid, pid_start_ticks, requested_at, "
        "heartbeat_at) VALUES ('r-protect', ?, 'paper', 'RUNNING', ?, 'birth', ?, ?)",
        (day.account.portfolio, LIVE_PID, utc_iso(now - timedelta(hours=1)), utc_iso(now - timedelta(seconds=4))))


def later(day, seconds: float) -> None:
    day.account.clock.advance(seconds)


def fill(day) -> None:
    """A quote crosses the resting buy: 5 SOL fill at 99.5."""
    later(day, 1)
    day.account.quote("SOL/USD", "99.4", "99.5", volume="100")


def snap(day, viewer=OWNER) -> dict:
    day.runtime._hq_money = day.runtime._hq_spending = day.runtime._hq_trading = None
    data = hq.snapshot(day.runtime, viewer)
    validate_snapshot(data)
    return data


def cards(data) -> list[dict]:
    return [item for item in data["attention"] if item["id"].startswith("no_safety_exit")]


def the_card(data) -> dict:
    (item,) = cards(data)
    return item


# --- the scripted day (the package's "done when") ------------------------------------------------------------------


def test_a_refused_exit_shows_the_card_after_ten_minutes_and_clears_once_the_exit_is_in_place(day) -> None:
    day.refuse()
    fill(day)
    filled_at = day.account.clock.now()
    assert day.account.held("SOL") == D("5")
    events = [row["kind"] for row in day.account.database.execute("SELECT kind FROM day_plan_events ORDER BY sequence")]
    assert events[-1] == "protection_deferred"  # the fill is recorded; its exit waits
    running(day)
    assert cards(snap(day)) == []  # just bought: not yet
    # The day plans panel marks the same 5 SOL at once (the same arithmetic as the card).
    entry = hq_plans.plans(day.runtime)["plans"][0]["entries"][0]
    assert entry["unprotected"] == "5"

    later(day, 9 * 60 + 59)  # 9 min 59 s after the fill; the sweep tries again and is refused again
    manage(day.account)
    running(day)
    assert cards(snap(day)) == []

    later(day, 1)  # 10 minutes without full cover
    running(day)
    data = snap(day)
    card = the_card(data)
    assert card["id"] == "no_safety_exit:SOL/USD" and card["sev"] == "act"
    assert _text(card["title"]) == "SOL/USD: 5 SOL has no safety exit"
    assert {"t": "term", "key": "safety_exit", "v": "safety exit"} in card["title"]
    assert card["since"] == iso_ms(filled_at)
    assert {"t": "time", "at": iso_ms(filled_at), "style": "clock"} in card["why"]
    assert _text(card["why"]) == ("Software puts a safety exit on every buy as it fills; this one has had none since "
                                  "08:00 UTC.")
    assert "nothing sells these coins by itself if the price falls" in _text(card["consequence"])
    how = _text(card["how"])
    assert how.startswith("Software tries again at every step of the team's service.")
    assert "The last try failed: a problem HQ can't describe in plain words." in how
    assert any(segment.get("code") == f"StaleState: {REFUSAL}" for segment in card["how"])
    assert "HQ has no button for this" in how
    # No button that trades: no decision, nothing to fix here, only a link to the records.
    assert card["decision"] is None and card["can_fix_here"] is False
    assert card["link"] == {"href": "/trading", "label": "Trading records"}
    assert card["in_next_prompt"] is True
    assert data["now"]["attention_count"] == sum(item["sev"] != "info" for item in data["attention"])
    assert "SOL/USD: 5 SOL has no safety exit" in data["prompt_facts"]["flagged"]

    day.refuse(False)  # the refusal's cause is gone: the next management step puts the exit on
    later(day, 30)
    result = manage(day.account)
    assert len(result["protected"]) == 1
    running(day)
    data = snap(day)
    assert cards(data) == []
    assert hq_plans.plans(day.runtime)["plans"][0]["entries"][0]["unprotected"] == "0"


def test_the_card_clears_once_the_trader_closes_the_position(day) -> None:
    day.refuse()
    fill(day)
    later(day, 600)
    running(day)
    assert the_card(snap(day))["id"] == "no_safety_exit:SOL/USD"
    record = act(day.account, position_action("close"), request_id="close-1")
    assert record.status in ("done", "running")
    later(day, 1)
    day.account.quote("SOL/USD", "99.4", "99.5", volume="100")  # the close's market sell fills
    assert day.account.held("SOL") == D("0")
    running(day)
    assert cards(snap(day)) == []


def test_coins_sold_another_way_need_no_card(day) -> None:
    day.refuse()
    fill(day)
    later(day, 600)
    running(day)
    assert cards(snap(day))
    # The owner's sell-everything setting sells the coins with its own order, not a day-plan exit.
    day.account.execution.set_pause(day.account.portfolio, "FLATTEN", "owner", "synthetic sell everything")
    asyncio.run(day.account.execution.advance_pause(day.account.portfolio))
    day.account.dispatch()
    later(day, 1)
    day.account.quote("SOL/USD", "99.4", "99.5", volume="100")
    assert day.account.held("SOL") == D("0")
    running(day)
    assert cards(snap(day)) == []


# --- since when, and what the system is doing --------------------------------------------------------------------


def test_after_a_cancel_all_the_time_counts_from_the_cancel_and_the_card_says_nothing_is_put_back(day) -> None:
    fill(day)
    manage(day.account)  # the exit is in place
    running(day)
    assert cards(snap(day)) == []
    later(day, 3600)
    day.account.execution.set_pause(day.account.portfolio, "CANCEL_ALL", "owner", "synthetic cancel all")
    asyncio.run(day.account.execution.advance_pause(day.account.portfolio))
    cancelled_at = day.account.clock.now()
    later(day, 9 * 60)
    running(day)
    assert cards(snap(day)) == []  # the exit ended 9 minutes ago; the fill an hour before does not count
    later(day, 60)
    running(day)
    card = the_card(snap(day))
    assert card["since"] == iso_ms(cancelled_at)
    assert _text(card["how"]).startswith("Software puts none on while the cancel-all setting is on")
    assert "last try" not in _text(card["how"]).lower()


def test_while_the_team_is_off_the_card_says_nothing_happens_until_it_runs(day) -> None:
    day.refuse()
    fill(day)
    later(day, 900)
    data = snap(day)  # no service run recorded: the team is off
    assert data["now"]["code"] == "no_run"
    card = the_card(data)
    assert _text(card["how"]).startswith("Nothing happens while the team is off")
    assert {"team_off", "no_safety_exit:SOL/USD"} <= {item["id"] for item in data["attention"]}


def test_every_signed_in_role_sees_the_card_and_it_never_says_practice_on_a_live_account(day) -> None:
    day.refuse()
    fill(day)
    later(day, 600)
    running(day)
    assert the_card(snap(day, READER))["decision"] is None
    words = json.dumps(the_card(snap(day)))
    assert "practice" not in words.lower() and "paper" not in words.lower()


# --- reading starts nothing -----------------------------------------------------------------------------------------


def test_reading_hq_writes_nothing_and_places_nothing(day, monkeypatch) -> None:
    day.refuse()
    fill(day)
    later(day, 900)
    running(day)
    before = table_counts(day.account.database)
    changes = day.account.database.execute("SELECT total_changes()").fetchone()[0]

    def forbidden(*args, **kwargs):
        raise AssertionError("reading HQ must never place, send or cancel an order")

    for name in ("place_protection", "dispatch", "cancel_intents", "place_order"):
        if hasattr(day.account.execution, name):
            monkeypatch.setattr(day.account.execution, name, forbidden)
    assert cards(snap(day)) and cards(snap(day, READER))
    assert table_counts(day.account.database) == before
    assert day.account.database.execute("SELECT total_changes()").fetchone()[0] == changes


def test_the_card_module_imports_no_execution_broker_engine_or_provider() -> None:
    source = (Path(hq_protection.__file__)).read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import) (trade_graph\.[\w.]+)", source, flags=re.MULTILINE)
    assert imports and all(name.startswith(("trade_graph.api", "trade_graph.kernel.day_plan_coverage",
                                            "trade_graph.domain", "trade_graph.contracts.alarms"))
                           for name in imports), imports


def test_a_database_without_day_plan_records_has_no_card(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    runtime = runtime_for(account)
    account.database.execute("DROP TABLE day_plan_events")
    assert hq_protection.read(runtime, account.clock.now()) == []


# --- the cards themselves ---------------------------------------------------------------------------------------


def _gap(symbol: str, quantity: str, minutes: float, now, reason: str | None = None) -> hq_protection.CoinGap:
    return hq_protection.CoinGap(symbol=symbol, quantity=D(quantity), since=now - timedelta(minutes=minutes),
                                 reason=reason)


def test_only_gaps_of_at_least_ten_minutes_become_cards(day) -> None:
    now = day.account.clock.now()
    gaps = [_gap("SOL/USD", "5", 9.99, now), _gap("ETH/USD", "0.5", 10, now)]
    built = hq_protection.cards(gaps, now, situation="retrying")
    assert [item["id"] for item in built] == ["no_safety_exit:ETH/USD"]
    assert hq_protection.GRACE == timedelta(minutes=10)


def test_more_than_three_coins_are_summed_up_in_one_more_card(day) -> None:
    now = day.account.clock.now()
    coins = ["SOL/USD", "ETH/USD", "ADA/USD", "XRP/USD", "DOGE/USD", "AVAX/USD"]
    gaps = [_gap(symbol, "1.25", 60 - index, now, reason="StaleState: no fresh quote") for index, symbol in
            enumerate(coins)]
    built = hq_protection.cards(gaps, now, situation="retrying")
    assert [item["id"] for item in built] == [f"no_safety_exit:{symbol}" for symbol in coins[:3]] + [
        "no_safety_exit:more"]
    more = built[-1]
    assert _text(more["title"]) == "3 more coins have no safety exit: XRP/USD, DOGE/USD, AVAX/USD"
    assert more["since"] == iso_ms(now - timedelta(minutes=57))  # the oldest of the coins it names
    one = hq_protection.cards(gaps[:4], now, situation="retrying")[-1]
    assert _text(one["title"]) == "1 more coin has no safety exit: XRP/USD"
    for item in built:
        AttentionItem.model_validate(item)
        assert item["decision"] is None and item["link"]["href"] == "/trading"


def test_a_gap_whose_start_is_unknown_still_shows_and_says_so(day) -> None:
    now = day.account.clock.now()
    (card,) = hq_protection.cards([hq_protection.CoinGap("SOL/USD", D("5"), None, None)], now, situation="retrying")
    assert card["since"] is None and "can't tell since when" in _text(card["why"])


@pytest.mark.parametrize(("situation", "start"), [
    ("retrying", "Software tries again at every step"), ("cancel_all", "Software puts none on while the cancel-all"),
    ("flatten", "The sell-everything setting is selling"), ("stopped", "The account is stopped"),
    ("team_off", "Nothing happens while the team is off")])
def test_each_situation_says_what_the_system_is_doing(day, situation, start) -> None:
    now = day.account.clock.now()
    (card,) = hq_protection.cards([_gap("SOL/USD", "0.0001", 30, now)], now, situation=situation)
    assert _text(card["how"]).startswith(start)
    assert _text(card["title"]) == "SOL/USD: 0.0001 SOL has no safety exit"


def test_the_busy_week_with_the_most_cards_fits_the_budget(tmp_path, monkeypatch) -> None:
    client, database, portfolio, token, csrf, leader = _client(tmp_path)
    runtime = client.app.state.runtime
    monkeypatch.setattr(service_controller, "process_identity", lambda pid: "birth" if pid == payload.t.LIVE_PID
                        else None)
    monkeypatch.setattr(resources, "observation",
                        lambda runtime: {"observed_at": utc_iso(runtime.clock.now()), "warnings": []})
    env = SimpleNamespace(client=client, runtime=runtime, db=database, pid=portfolio, token=token, csrf=csrf,
                          leader=leader)
    runtime.clock.advance(10 * 86400)  # as test_hq_payload's busy week
    payload._seed(env)
    now = runtime.clock.now()
    reason = "StaleState: " + "a long synthetic refusal reason " * 12
    gaps = [_gap(f"COIN{index}/USD", "12345.67891234", 600 + index, now, reason=reason) for index in range(9)]
    monkeypatch.setattr(hq_protection, "read", lambda runtime, now: gaps)
    data = hq.snapshot(runtime, payload.OWNER, calendar_days=14)
    validate_snapshot(data)
    assert len(cards(data)) == 4  # three coins and one card naming the other six
    size = payload._wire_bytes(data)
    print(f"busy week with the most no-safety-exit cards: {size} B")
    assert size <= payload.BUDGET and len(json.dumps(data)) < payload.BUDGET
    # With W8-C's fullest trading block as well, the payload on the wire still fits.
    data["trading"] = hq_trading.block(fullest_facts(now))
    validate_snapshot(data)
    size = payload._wire_bytes(data)
    print(f"... and the fullest trading block: {size} B")
    assert size <= payload.BUDGET
    database.close()


# --- the page's fixture -----------------------------------------------------------------------------------------

PAGE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hq_cards" / "no_safety_exit.json"


def page_fixture() -> dict:
    """The cards ``tests/js/hq_protection.test.mjs`` renders, built by the real card builder from synthetic gaps, at
    the ``as_of`` of the quiet, reader and live snapshot fixtures."""
    from datetime import UTC, datetime

    now = datetime(2026, 10, 9, 13, 59, 10, 506000, tzinfo=UTC)
    coins = [("SOL/USD", "5", 25), ("ETH/USD", "0.5", 18), ("ADA/USD", "1500", 15), ("XRP/USD", "200", 12),
             ("DOGE/USD", "3000", 11)]
    gaps = [_gap(symbol, quantity, minutes, now, reason=f"StaleState: {REFUSAL}")
            for symbol, quantity, minutes in coins]
    return {"as_of": iso_ms(now),
            "paper": hq_protection.cards(gaps, now, situation="retrying"),
            "live": hq_protection.cards(gaps[:1], now, situation="cancel_all", live=True)}


def test_the_page_fixture_is_what_the_server_builds() -> None:
    expected = page_fixture()
    for item in [*expected["paper"], *expected["live"]]:
        AttentionItem.model_validate(item)
    assert json.loads(PAGE_FIXTURE.read_text(encoding="utf-8")) == expected, (
        "regenerate tests/fixtures/hq_cards/no_safety_exit.json from page_fixture()")
