"""HQ price charts (T41): the chart document is projected from saved records only.

Candles come from the candle file (read-only, point in time), indicators from the T27 engine over those candles,
alarm lines from the alarm book's records, orders and fills from the execution records and day plans. Synthetic
paper account and synthetic candles only; no network, key, model or exchange call.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.alarm_support import quote_request
from tests.chart_support import (
    candles,
    chart_now,
    insert_shortlist,
    market_file,
    planned_account,
    read_only,
    runtime,
    table_counts,
    write_candles,
)
from tests.day_plan_support import entry, manage

from trade_graph.api import hq_chart
from trade_graph.api.hq_chart_contract import SCHEMA, WINDOW_SLOTS, validate_chart
from trade_graph.api.hq_contract import iso_ms
from trade_graph.application.alarms.book import AlarmBook
from trade_graph.application.indicators.engine import compute_indicator
from trade_graph.contracts.candles import to_unix
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal

D = Decimal
HISTORY = 2400  # 15-minute candles: enough for RSI 14's settled span (D136) behind a 300-candle chart


def _cross(account, bid: str, ask: str, **options):
    account.clock.advance(1)
    return account.quote("SOL/USD", bid, ask, **options)


@pytest.fixture
def charted(tmp_path):
    account, installed = planned_account(tmp_path)
    path = market_file(tmp_path)
    end = chart_now(account)
    write_candles(path, candles("SOL/USD", 15, end, HISTORY) + candles("SOL/USD", 60, end, 600))
    return account, installed, path


def _chart(account, path, **options) -> dict:
    document = hq_chart.chart(runtime(account, read_only(path)), source="synthetic", **options)
    validate_chart(document)
    return document


# --- which coins can be charted ---------------------------------------------------------------------------------


def test_the_coin_list_comes_from_day_plans_holdings_alarms_the_shortlist_and_two_references(charted) -> None:
    account, installed, path = charted
    AlarmBook(account.database, account.clock).create(quote_request("2.5", symbol="ADA/USD"))
    insert_shortlist(account.database, ["LTC/USD", "SOL/USD"], as_of=utc_iso(account.clock.now() - timedelta(hours=1)))
    document = _chart(account, path)
    assert document["schema"] == SCHEMA
    coins = {coin["symbol"]: coin["reasons"] for coin in document["coins"]}
    assert list(coins) == ["SOL/USD", "ADA/USD", "LTC/USD", "BTC/USD", "ETH/USD"]
    assert coins["SOL/USD"] == ["day_plan", "alarm", "shortlist"]
    assert coins["ADA/USD"] == ["alarm"] and coins["LTC/USD"] == ["shortlist"]
    assert coins["BTC/USD"] == ["reference"] and coins["ETH/USD"] == ["reference"]
    assert document["symbol"] == "SOL/USD" and document["interval_minutes"] == 15  # the plan's first interval
    _cross(account, "99.4", "99.5", volume="100")  # the resting bid fills: SOL is now held
    assert _chart(account, path)["coins"][0]["reasons"] == ["day_plan", "held", "alarm", "shortlist"]


def test_an_old_shortlist_and_a_newer_failed_scan_add_no_coin(charted) -> None:
    account, _, path = charted
    insert_shortlist(account.database, ["LTC/USD"], as_of=utc_iso(account.clock.now() - timedelta(hours=30)))
    assert "LTC/USD" not in [coin["symbol"] for coin in _chart(account, path)["coins"]]
    insert_shortlist(account.database, ["XRP/USD"], as_of=utc_iso(account.clock.now() - timedelta(hours=2)),
                     scan_id="scan-synthetic-2")
    assert "XRP/USD" in [coin["symbol"] for coin in _chart(account, path)["coins"]]
    with account.database.transaction() as connection:  # the newest scan failed: the shortlist is not known now
        connection.execute(
            """INSERT INTO coin_scans (scan_id, mode, as_of, computed_at, recorded_at, ranking_version, status, error,
               evidence, optimistic, usd_pairs, ranked_pairs, shortlisted, document_json, document_sha256)
               VALUES ('scan-synthetic-3', 'live', ?, ?, ?, 'synthetic@1', 'failed', 'ticker_unavailable',
               'synthetic_fixture', 0, 0, 0, 0, '{}', ?)""",
            (*[utc_iso(account.clock.now() - timedelta(hours=1))] * 3, "0" * 64))
    assert "XRP/USD" not in [coin["symbol"] for coin in _chart(account, path)["coins"]]


def test_a_coin_that_is_not_listed_is_not_charted(charted) -> None:
    account, _, path = charted
    document = _chart(account, path, symbol="DOGE/USD")
    assert document["status"] == "coin_not_listed" and document["symbol"] is None
    assert document["candles"] == [] and document["lines"] == [] and document["coins"]


# --- candles --------------------------------------------------------------------------------------------------


def test_candles_are_exact_completed_and_received_by_now(charted) -> None:
    account, _, path = charted
    end = chart_now(account)
    document = _chart(account, path)
    # The candle closing at the current boundary is received 20 s after it: not visible yet, so the chart ends at
    # the one before it instead of showing a hole at the right edge.
    assert document["newest_close"] == iso_ms(end - timedelta(minutes=15))
    window = document["window"]
    assert window["slots"] == WINDOW_SLOTS and window["step_seconds"] == 900
    assert window["first_open"] == to_unix(end - timedelta(minutes=15 * (WINDOW_SLOTS + 1)))
    assert len(document["candles"]) == WINDOW_SLOTS and document["missing"] == []
    stored = {to_unix(item.open_time_utc): item for item in candles("SOL/USD", 15, end, HISTORY)}
    for item in document["candles"]:
        source = stored[item[0]]
        assert item[1:] == [canonical_decimal(value) for value in (source.open, source.high, source.low, source.close,
                                                                   source.volume)]
    assert document["price_precision"] == 2
    assert document["source"] == "synthetic"


def test_a_missing_candle_stays_missing_and_is_reported(tmp_path) -> None:
    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    end = chart_now(account)
    hole = end - timedelta(minutes=15 * 40)
    write_candles(path, candles("SOL/USD", 15, end, HISTORY, skip=[hole]))
    document = _chart(account, path)
    assert document["missing"] == [to_unix(hole - timedelta(minutes=15))]
    assert len(document["candles"]) == WINDOW_SLOTS - 1
    assert all(item[0] != document["missing"][0] for item in document["candles"])
    rsi = next(item for item in document["indicators"] if item["indicator"] == "RSI")
    assert rsi["status"] == "gapped" and rsi["outputs"] == [] and "missing" in rsi["status_text"]
    assert any("1 candle" in note for note in document["notes"])


def test_a_feed_that_stopped_shows_the_empty_slots_at_the_right_edge(tmp_path) -> None:
    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    end = chart_now(account)
    write_candles(path, candles("SOL/USD", 15, end - timedelta(hours=2), HISTORY))
    document = _chart(account, path)
    assert len(document["missing"]) == 8 and document["missing"][-1] == to_unix(end - timedelta(minutes=15))
    assert document["newest_close"] == iso_ms(end - timedelta(hours=2))


def test_a_candle_received_after_now_is_not_shown(tmp_path) -> None:
    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    end = chart_now(account)
    write_candles(path, candles("SOL/USD", 15, end - timedelta(minutes=15), HISTORY, received_after_seconds=3600))
    document = _chart(account, path)
    newest = max(item[0] for item in document["candles"])
    assert newest + 900 <= to_unix(account.clock.now() - timedelta(hours=1))


def test_a_coin_without_candles_says_so(charted) -> None:
    account, _, path = charted
    document = _chart(account, path, symbol="BTC/USD", interval=60)
    assert document["status"] == "ok" and document["candles"] == [] and len(document["missing"]) == WINDOW_SLOTS
    assert document["newest_close"] is None
    assert any("No 1-hour candles of BTC/USD" in note for note in document["notes"])


# --- indicators -------------------------------------------------------------------------------------------------


def test_indicators_are_the_plans_choices_computed_by_the_engine_on_the_charts_candles(charted) -> None:
    account, _, path = charted
    document = _chart(account, path)
    shown = {item["label"]: item for item in document["indicators"]}
    assert list(shown) == ["RSI 14", "SMA 20", "BBANDS 20, 2, 2"]  # EMA watches the 60-minute candles
    assert shown["RSI 14"]["pane"] == "own" and shown["SMA 20"]["pane"] == "price"
    assert shown["BBANDS 20, 2, 2"]["pane"] == "price"
    assert shown["RSI 14"]["chosen_by"] == ["day_plan", "alarm"] and shown["SMA 20"]["chosen_by"] == ["day_plan"]
    assert [output["name"] for output in shown["BBANDS 20, 2, 2"]["outputs"]] == ["upper", "middle", "lower"]
    store = read_only(path)
    end = chart_now(account) - timedelta(minutes=15)
    expected = compute_indicator(store, "RSI", {}, symbol="SOL/USD", interval_minutes=15, source="synthetic",
                                 end_close_time=end, as_of=account.clock.now(), count=WINDOW_SLOTS)
    assert expected.is_ready
    values = shown["RSI 14"]["outputs"][0]["values"]
    assert len(values) == WINDOW_SLOTS
    assert values == [float(f"{value:.8g}") for value in expected.values["value"]]
    assert any("EMA 20" in line and "1-hour chart" in line for line in document["elsewhere"])
    hourly = _chart(account, path, interval=60)
    assert [item["label"] for item in hourly["indicators"]] == ["EMA 20"]


def test_a_coin_still_warming_up_says_so_instead_of_drawing_a_partial_line(tmp_path) -> None:
    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    write_candles(path, candles("SOL/USD", 15, chart_now(account), 350))
    rsi = next(item for item in _chart(account, path)["indicators"] if item["indicator"] == "RSI")
    assert rsi["status"] == "warming_up" and rsi["outputs"] == [] and "history" in rsi["status_text"]


def test_every_menu_entry_has_a_place_on_the_chart() -> None:
    from trade_graph.contracts.indicators import menu_names

    assert set(hq_chart.PLACEMENT) == set(menu_names())
    overlays = {name for name, pane in hq_chart.PLACEMENT.items() if pane == "price"}
    assert overlays == {"SMA", "EMA", "WMA", "DEMA", "BBANDS", "SAR", "MIDPRICE", "HIGHEST_HIGH", "LOWEST_LOW"}


# --- alarm lines, orders, exits and fills ------------------------------------------------------------------------


def test_alarm_lines_resting_entries_safety_exits_and_fills(charted) -> None:
    account, installed, path = charted
    document = _chart(account, path)
    lines = [(line["kind"], line["price"], line["pane"], line["state"]) for line in document["lines"]]
    assert ("alarm", "95", "price", "armed") in lines and ("alarm", "110", "price", "armed") in lines
    rsi_key = next(item["key"] for item in document["indicators"] if item["indicator"] == "RSI")
    assert ("alarm", "30", rsi_key, "armed") in lines  # an oscillator alarm sits on the oscillator's own pane
    assert ("entry", "99.5", "price", "open") in lines and ("entry", "98", "price", "open") in lines
    entry_text = next(line["text"] for line in document["lines"] if line["kind"] == "entry" and line["price"] == "99.5")
    assert "5 SOL" in entry_text and "97" in entry_text and "104" in entry_text
    assert document["markers"] == []
    _cross(account, "99.4", "99.5", volume="100")  # the first entry fills; its safety exit is attached
    manage(account)
    document = _chart(account, path)
    kinds = [(line["kind"], line["price"]) for line in document["lines"]]
    assert ("entry", "99.5") not in kinds and ("entry", "98") in kinds
    assert ("stop", "97") in kinds and ("take_profit", "104") in kinds
    marker = document["markers"][0]
    assert marker["kind"] == "buy" and marker["price"] == "99.5" and "5" in marker["text"]
    assert marker["t"] % 900 == 0 and marker["t"] <= to_unix(account.clock.now())
    _cross(account, "104.1", "104.3")  # the take-profit sells; the exit is gone
    document = _chart(account, path)
    assert ("stop", "97") not in [(line["kind"], line["price"]) for line in document["lines"]]
    assert [item["kind"] for item in document["markers"]] == ["buy", "sell"]
    assert "safety exit" in document["markers"][1]["text"]


def test_an_order_whose_status_is_unclear_is_labelled_and_still_drawn(charted) -> None:
    account, installed, path = charted
    intent = installed.entries[1].intent_id
    with account.database.transaction() as connection:
        connection.execute("UPDATE order_intents SET state = 'UNKNOWN' WHERE intent_id = ?", (intent,))
    line = next(line for line in _chart(account, path)["lines"] if line["price"] == "98")
    assert line["state"] == "unclear" and "being checked" in line["text"]


def test_a_fired_alarm_is_a_marker_and_leaves_the_lines(charted) -> None:
    account, installed, path = charted
    book = AlarmBook(account.database, account.clock)
    alarm = next(item for item in book.alarms(department="trader") if item.condition.kind == "quote_price")
    book.fire(alarm.alarm_id, alarm.generation, observed={"synthetic": "chart test"})
    document = _chart(account, path)
    assert ("alarm", "95") not in [(line["kind"], line["price"]) for line in document["lines"]]
    assert [marker["kind"] for marker in document["markers"]] == ["alarm"]
    assert "went off" in document["markers"][0]["text"]


def test_other_departments_alarms_and_plain_orders_are_drawn_too(charted) -> None:
    account, _, path = charted
    AlarmBook(account.database, account.clock).create(quote_request("101", symbol="SOL/USD", department="leader"))
    account.authorize("enter", "SOL/USD", "1", limit="96.5")
    document = _chart(account, path)
    leader = next(line for line in document["lines"] if line["price"] == "101")
    assert leader["kind"] == "alarm" and "Leader" in leader["text"]
    plain = next(line for line in document["lines"] if line["price"] == "96.5")
    assert plain["kind"] == "order" and "buy" in plain["text"].lower()


# --- what HQ does without the candle file or newer tables -------------------------------------------------------


@pytest.mark.parametrize("status", ["not_configured", "missing", "unreadable", "outdated"])
def test_without_a_usable_candle_file_the_chart_says_why_and_still_lists_coins(tmp_path, status) -> None:
    account, _ = planned_account(tmp_path)
    document = hq_chart.chart(runtime(account, None, status=status))
    validate_chart(document)
    assert document["status"] == "no_price_history" and document["candles"] == []
    assert document["status_text"] and document["coins"][0]["symbol"] == "SOL/USD"
    assert document["lines"]  # alarm and order lines are records of the state database and still known


def test_an_older_state_database_without_alarm_day_plan_or_scan_tables_still_charts(tmp_path) -> None:
    account, _ = planned_account(tmp_path)
    path = market_file(tmp_path)
    write_candles(path, candles("SOL/USD", 15, chart_now(account), 400))
    with account.database.transaction() as connection:
        for table in ("day_plan_events", "day_plan_exits", "day_plan_entries", "day_plans", "alarm_wake_claims",
                      "alarm_deliveries", "price_alarm_watch", "price_alarm_events", "price_feed_events",
                      "price_alarms", "coin_shortlist_entries", "coin_scan_pairs", "coin_scans"):
            connection.execute(f"DROP TABLE {table}")
    document = _chart(account, path, symbol="BTC/USD")
    assert [coin["symbol"] for coin in document["coins"]] == ["BTC/USD", "ETH/USD"]
    document = _chart(account, path, symbol="BTC/USD", interval=15)
    assert document["status"] == "ok" and document["indicators"] == []


def test_reading_a_chart_writes_nothing_to_either_database(charted) -> None:
    import sqlite3

    account, _, path = charted
    _cross(account, "99.4", "99.5", volume="100")
    state_before = table_counts(account.database.execute("SELECT 1").connection)
    candle_file = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    candles_before = table_counts(candle_file)
    for interval in (1, 5, 15, 60):
        _chart(account, path, interval=interval)
    assert table_counts(account.database.execute("SELECT 1").connection) == state_before
    assert table_counts(candle_file) == candles_before
    candle_file.close()


# --- size and speed ---------------------------------------------------------------------------------------------


def test_a_busy_chart_stays_within_its_budget(tmp_path) -> None:
    indicators = [{"indicator": name, "interval_minutes": 15} for name in
                  ("BBANDS", "MACD", "STOCH", "AROON", "RSI", "SMA", "EMA", "ADX")]
    account, _ = planned_account(tmp_path, indicators=indicators,
                                    entries=[entry("0.5", f"{99 - step * D('0.25')}", "90") for step in range(16)])
    path = market_file(tmp_path)
    write_candles(path, candles("SOL/USD", 15, chart_now(account), HISTORY))
    book = AlarmBook(account.database, account.clock)
    for index in range(40):
        book.create(quote_request(f"{90 + index}", symbol="SOL/USD"))
    started = time.perf_counter()
    document = _chart(account, path)
    elapsed = time.perf_counter() - started
    size = len(json.dumps(document, separators=(",", ":")).encode())
    assert size <= hq_chart.PAYLOAD_BUDGET_BYTES, size
    assert elapsed < 5, elapsed
    assert len(document["indicators"]) == 8 and len(document["lines"]) >= 56


def test_the_chart_module_has_no_indicator_formula_of_its_own() -> None:
    from pathlib import Path

    from tests.indicator_guard import arithmetic_on_prices

    assert arithmetic_on_prices(Path(hq_chart.__file__)) == []
