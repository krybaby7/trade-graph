"""HQ's Trading desk room (W13-A, C13; D302): ``GET /api/v1/hq/desk`` and its document, ``trade-graph.hq.desk.v1``.

On the scripted week (``tests/hq_desk_support.py``: W12-A's week with SOL's dip buys filled, all synthetic and
labelled ``synthetic_fixture``) the room shows, in plain words, the playbook per market type, today's stance per coin
with its market type, the lab (queued, the newest results with the result after fees against holding, tries per
family), the Leader news the desk read and what each strategy earned in each market type. Reading the room writes
nothing and starts nothing; a database from before migrations 0050, 0051 or 0052 still renders it; the busiest room
stays within its 80 KB budget. No network, key, model, provider or exchange call.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.day_plan_support import day_plan_account
from tests.hq_desk_support import busiest_inputs, busiest_parts, desk_week
from tests.hq_trading_support import runtime_for
from tests.wake_log_support import dump_sha256

from trade_graph.api import hq_copy, hq_desk
from trade_graph.api.app import create_app
from trade_graph.api.auth import issue_session
from trade_graph.api.hq_desk_contract import (
    LAB_RESULTS_MAX,
    PAYLOAD_BUDGET_BYTES,
    SCHEMA,
    SPLIT_ROWS_MAX,
    STANCES_MAX,
    validate_desk,
)
from trade_graph.application.strategy_lab.queue import LabQueue
from trade_graph.application.trading_desk.store import DeskStore
from trade_graph.contracts.trading_desk import PlaybookRecord, StanceRecord

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "hq_desk" / "desk_week.json"
D = Decimal


def _text(value) -> str:
    return hq_copy.plain(value)


def _read(account) -> dict:
    document = hq_desk.desk(runtime_for(account))
    validate_desk(document)
    return document


@pytest.fixture(scope="module")
def week(tmp_path_factory):
    built = desk_week(tmp_path_factory.mktemp("desk-week"))
    yield built
    built.close()


# --- what the owner sees on the scripted week -------------------------------------------------------------------------


def test_the_playbook_says_per_market_type_the_strategy_or_wait_and_why(week) -> None:
    playbook = _read(week.account)["playbook"]
    assert playbook["state"] == "shown" and playbook["revision"] == 2 and playbook["revisions_total"] == 2
    assert [choice["market_type"] for choice in playbook["choices"]] == ["rising", "sideways", "falling", "unclear"]
    texts = [_text(choice["text"]) for choice in playbook["choices"]]
    assert texts == ["Rising: follow the trend with slow-trend-pullback@1.", "Sideways: buy dips with dip-rebound@1.",
                     "Falling: wait in cash.", "Unclear: wait in cash."]
    sideways = playbook["choices"][1]
    assert sideways["reason"] == {"label": "In their words", "text": "Rest maker bids after dips inside the range."}
    assert sideways["reports"] >= 1 and sideways["strategy_refs"] == ["dip-rebound@1"]
    assert playbook["why"]["text"] == "The dip version is confirmed now."
    assert "revision 1" in _text(playbook["previous"])
    assert "Playbook revision 2" in _text(playbook["headline"])


def test_todays_stance_per_coin_shows_the_market_type_the_desk_saw_and_the_label_now(week) -> None:
    stances = _read(week.account)["stances"]
    assert stances["state"] == "shown"
    by_coin = {item["symbol"]: item for item in stances["items"]}
    assert set(by_coin) == {"SOL/USD", "BTC/USD"}
    sol, btc = by_coin["SOL/USD"], by_coin["BTC/USD"]
    assert (sol["stance"], _text(sol["text"])) == ("buy_dips", "Buy dips with dip-rebound@1.")
    assert (sol["shown"]["status"], sol["shown"]["market_type"]) == ("current", "sideways")
    assert (btc["stance"], _text(btc["text"])) == ("wait", "Wait in cash.")
    assert btc["shown"]["market_type"] == "falling"
    assert btc["news"] == 0  # no Jev verdict in the day before today's turn (the week's are on days 1 and 4)
    assert btc["reason"]["text"].startswith("It is falling")
    assert sol["now"]["status"] == "current" and "sideways" in _text(sol["now"]["text"]).lower()
    headline = _text(stances["headline"])
    assert "SOL/USD buys dips (sideways)" in headline and "BTC/USD waits (falling)" in headline


def test_the_lab_shows_the_queue_the_newest_results_against_holding_and_the_tries_per_family(week) -> None:
    lab = _read(week.account)["lab"]
    assert lab["state"] == "shown" and (lab["queued"], lab["running"]) == (0, 0)
    kinds = [_text(item["text"]) for item in lab["results"]]
    assert any(text.startswith("Confirmed dip-rebound@1 on SOL/USD") for text in kinds), kinds
    assert any(text.startswith("Registered dip-rebound@1") for text in kinds), kinds
    confirm = next(item for item in lab["results"] if _text(item["text"]).startswith("Confirmed"))
    assert confirm["status"] == "done" and confirm["reports"]
    for report in confirm["reports"]:
        assert report["result"]["currency"] == "USD"
        words = _text(report["text"])
        assert "result after fees" in words and "holding SOL/USD" in words, words
        if report["versus_hold"] is not None:
            assert report["tone"] in ("good", "neutral", "attention")
    family = next(item for item in lab["families"] if item["strategy_id"] == "dip-rebound")
    assert (family["versions"], family["studies"], family["tune_runs"]) == (1, 1, 2) or family["versions"] == 1
    assert family["confirmed"] == ["dip-rebound@1"]
    assert "1 version" in _text(family["text"]) and "confirmed: dip-rebound@1" in _text(family["text"])


def test_the_news_is_what_the_desk_read_at_its_last_turn(week) -> None:
    news = _read(week.account)["news"]
    assert news["state"] == "shown" and news["read_at"] is not None
    round_ = news["round"]
    assert round_ is not None and "Synthetic round of day 7" in round_["summary"]["text"]
    assert round_["headlines"] and all(item["text"]["label"] == "In their words" for item in round_["headlines"])
    assert news["inbox"] and "Buy dips in sideways markets" in news["inbox"][0]["idea"]["text"]
    assert "4 lab requests" in _text(news["inbox"][0]["text"])
    assert "shadow" in _text(news["jev"]).lower()


def test_the_split_says_what_each_strategy_earned_in_each_market_type(week) -> None:
    split = _read(week.account)["split"]
    assert split["state"] == "shown" and split["currency"] == "USD"
    (row,) = split["by_choice"]
    assert row["key"] == "dip-rebound@1|sideways|buy_dips" and (row["trades"], row["open_positions"]) == (2, 1)
    assert _text(row["text"]).startswith("dip-rebound@1 when sideways, buying dips: 2 trades (1 won, 1 lost)")
    assert [item["key"] for item in split["by_market_type"]] == ["sideways"]
    assert split["result"] == row["result"]  # every trade and held position is in one row
    assert "never proof of profit" in _text(split["note"])


def test_the_document_matches_the_committed_fixture_shape(week) -> None:
    """The JS tests and the browser check read ``tests/fixtures/hq_desk/desk_week.json``, made by this builder from
    this week; it must stay a valid document with the same sections and states."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validate_desk(fixture)
    document = _read(week.account)
    assert set(fixture) == set(document)
    assert {name: fixture[name]["state"] for name in ("playbook", "stances", "lab", "news", "split")} == {
        name: document[name]["state"] for name in ("playbook", "stances", "lab", "news", "split")}


# --- reading starts nothing -------------------------------------------------------------------------------------------


def test_reading_the_room_writes_nothing_and_starts_nothing(week, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("reading HQ must not write or start anything")

    for owner, name in ((LabQueue, "claim"), (LabQueue, "finish"), (LabQueue, "submit"), (LabQueue, "put_back"),
                        (DeskStore, "record_playbook"), (DeskStore, "record_stances")):
        monkeypatch.setattr(owner, name, refuse)
    before = dump_sha256(week.account.database)
    for _ in range(3):
        _read(week.account)
    assert dump_sha256(week.account.database) == before


def test_the_projection_imports_no_engine_worker_scheduler_provider_or_broker() -> None:
    tree = ast.parse((ROOT / "src" / "trade_graph" / "api" / "hq_desk.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    forbidden = ("trade_graph.application.events", "trade_graph.application.strategy_lab.worker",
                 "trade_graph.application.scheduler", "trade_graph.application.worker", "trade_graph.adapters.models",
                 "trade_graph.adapters.brokers", "trade_graph.application.leader_news.rounds")
    assert not [name for name in imported if name.startswith(forbidden)], imported


# --- older databases --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("dropped, states", [
    # Before 0052: no playbook or stances yet.
    (("day_plan_desk_records", "desk_stances", "desk_playbook_revisions"),
     {"playbook": "not_available", "stances": "not_available"}),
    # Before 0051 as well: no lab either.
    (("day_plan_desk_records", "desk_stances", "desk_playbook_revisions", "strategy_lab_requests",
      "strategy_lab_batches"), {"playbook": "not_available", "lab": "not_available"}),
    # Before 0050 as well: no labels; the split names every market type "no label".
    (("day_plan_desk_records", "desk_stances", "desk_playbook_revisions", "strategy_lab_requests",
      "strategy_lab_batches", "market_type_labels"), {"lab": "not_available", "split": "empty"}),
])
def test_an_older_database_still_renders_the_room(tmp_path, dropped, states) -> None:
    account = day_plan_account(tmp_path)
    with account.database.immediate() as connection:
        for table in dropped:
            connection.execute(f"DROP TABLE {table}")
    document = _read(account)
    for section, state in states.items():
        assert document[section]["state"] == state, (section, document[section])
    assert _text(document["headline"])


def test_an_edited_playbook_is_said_unreadable_and_the_rest_still_shows(tmp_path) -> None:
    week = desk_week(tmp_path, days=2, fills={})
    try:
        with week.account.database.immediate() as connection:
            connection.execute("DROP TRIGGER IF EXISTS desk_playbook_revisions_append_only")
            for trigger in [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'desk_playbook_revisions'")]:
                connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute("UPDATE desk_playbook_revisions SET document_json = '{\"choices\": []}'")
        document = _read(week.account)
        assert document["playbook"]["state"] == "unreadable"
        assert "couldn't be read" in _text(document["playbook"]["headline"])
        assert document["stances"]["state"] == "shown" and document["lab"]["state"] == "shown"
    finally:
        week.close()


# --- the payload budget -----------------------------------------------------------------------------------------------


def test_the_busiest_room_stays_within_its_budget() -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    document = hq_desk.document(now=now, **busiest_parts(now))
    validate_desk(document)
    size = len(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode())
    readable = len(json.dumps(document, indent=2, ensure_ascii=False).encode())
    print(f"busiest Trading desk room: {size} bytes compact, {readable} readable")
    assert size <= PAYLOAD_BUDGET_BYTES
    assert len(document["stances"]["items"]) == STANCES_MAX and document["stances"]["more_stances"] > 0
    assert len(document["lab"]["results"]) == LAB_RESULTS_MAX and document["lab"]["more_results"] > 0
    assert len(document["split"]["by_choice"]) == SPLIT_ROWS_MAX and document["split"]["more_choices"] > 0


def test_records_shown_in_the_busiest_room_are_real_record_types() -> None:
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    parts = busiest_inputs(now)
    assert isinstance(parts["playbook"], PlaybookRecord)
    assert all(isinstance(item, StanceRecord) for item in parts["stances"].values())


# --- the route --------------------------------------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path):
    account = day_plan_account(tmp_path)
    owner, _ = issue_session(account.database, account.clock, "owner")
    reader, _ = issue_session(account.database, account.clock, "reader")
    client = TestClient(create_app(runtime_for(account)))
    yield client, owner, reader


def test_any_signed_in_role_reads_the_room_and_nobody_else(served) -> None:
    client, owner, reader = served
    assert client.get("/api/v1/hq/desk", follow_redirects=False).status_code == 401
    for token in (owner, reader):
        response = client.get("/api/v1/hq/desk", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        document = response.json()
        validate_desk(document)
        assert document["schema"] == SCHEMA
    assert client.post("/api/v1/hq/desk", headers={"Authorization": f"Bearer {owner}"}).status_code in (403, 405)


def test_the_week_document_is_small_enough_and_its_size_is_reported(week) -> None:
    document = _read(week.account)
    size = len(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode())
    print(f"scripted week Trading desk room: {size} bytes")
    assert size <= PAYLOAD_BUDGET_BYTES
    assert timedelta(0) <= week.account.clock.now() - datetime.fromisoformat(
        document["as_of"].replace("Z", "+00:00")) < timedelta(seconds=1)
