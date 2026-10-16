"""Practice-range studies end to end (T29): windows, one report per cost profile, labels, reruns, recorded data.

The state database is a temporary file; candles are synthetic or recorded public Kraken fixtures
(``tests/fixtures/kraken``, evidence label ``recorded_public_fixture``). No test makes a network call.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from tests.candle_support import fixture_text, listed_pairs, recorded_clock
from tests.indicator_series import build_candles
from tests.practice_range_support import (
    GOLDEN_RULES,
    cross_strategy,
    golden_btc,
    golden_candles,
    open_store,
    save,
)

from trade_graph.adapters.market.kraken_candles import KrakenCandleFetcher
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.practice_range.service import PracticeRange
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow, StudyPlan
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import NotFound, ValidationFailure

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 2, tzinfo=UTC)
D = Decimal


def _day_two(candles: list) -> list:
    return [candle.model_copy(update={"open_time_utc": candle.open_time_utc + timedelta(days=1),
                                      "close_time_utc": candle.close_time_utc + timedelta(days=1)})
            for candle in candles if candle.open_time_utc >= datetime(2026, 7, 1, tzinfo=UTC)]


@pytest.fixture
def lab(tmp_path):
    state = Database(tmp_path / "state" / "trade_graph.sqlite")
    market, candles = open_store(tmp_path / "market" / "market_data.sqlite")
    save(candles, golden_candles() + _day_two(golden_candles()))
    save(candles, golden_btc())
    clock = FrozenClock(NOW)
    store = PracticeRangeStore(state, clock)
    yield store, PracticeRange(store, candles, clock), candles
    state.close()
    market.close()


def _plan(study_id: str = "cross-study-1", **windows) -> StudyPlan:
    tune = windows.get("tune", (date(2026, 7, 1), date(2026, 7, 2)))
    confirm = windows.get("confirm", (date(2026, 7, 2), date(2026, 7, 3)))
    return StudyPlan(study_id=study_id, strategy_id="cross-hundred", tune=ReplayWindow(start=tune[0], end=tune[1]),
                     confirm=ReplayWindow(start=confirm[0], end=confirm[1]))


def test_a_strategy_version_is_registered_once_and_read_back_exactly(lab) -> None:
    store, _, _ = lab
    strategy = cross_strategy()
    first = store.register(strategy)
    again = store.register(strategy)
    assert first.created and not again.created and first.content_sha256 == again.content_sha256
    assert store.strategy("cross-hundred@1") == strategy
    with pytest.raises(ValidationFailure, match="new version"):
        store.register(cross_strategy(sizing={"equity_fraction": "0.1"}))
    store.register(cross_strategy(version=2, sizing={"equity_fraction": "0.1"}))
    assert [item.ref for item in store.strategies()] == ["cross-hundred@1", "cross-hundred@2"]
    with pytest.raises(NotFound):
        store.strategy("cross-hundred@9")


def test_one_report_per_cost_profile_with_everything_a_rerun_needs(lab) -> None:
    store, practice, _ = lab
    store.register(cross_strategy())
    store.create_study(_plan())
    records = practice.run(study_id="cross-study-1", phase="tune", strategy_ref="cross-hundred@1",
                           symbol="SYN/USD", source="synthetic", rules=GOLDEN_RULES)
    assert [record.cost_profile_ref for record in records] == [
        "kraken-spot-after-10k-volume@1", "kraken-spot-starting-tier@1", "okx-eea-spot@1"]
    report = records[1].report
    request = report["result"]["request"]
    assert request["strategy"] == {"ref": "cross-hundred@1", "sha256": cross_strategy().content_sha256}
    assert request["cost_profile"]["ref"] == "kraken-spot-starting-tier@1"
    assert len(request["cost_profile"]["sha256"]) == 64 and request["fill_policy"]["ref"] == "bar-replay@1"
    assert request["window"] == {"start": "2026-07-01", "end": "2026-07-02", "days": 1, "end_is_exclusive": True}
    assert request["data_as_of_utc"] == "2026-10-02T00:00:00.000000Z"
    assert report["study"]["tune"]["start"] == "2026-07-01" and report["study"]["confirm"]["start"] == "2026-07-02"
    data = report["result"]["data"]
    assert (data["symbol"], data["source"], data["interval_minutes"]) == ("SYN/USD", "synthetic", 15)
    assert len(data["input_sha256"]) == 64
    assert report["label"]["pass_mark"] is None and report["label"]["live_authorization"] is False
    assert "not a pass mark" in report["label"]["statement"]
    assert report["evidence"] == {"labels": ["synthetic_fixture"], "real_market_data": False, "synthetic_data": True,
                                  "statement": "Replayed on synthetic test data, not market history.",
                                  "paid_calls": 0, "orders_sent": 0}
    # The Kraken-tier golden numbers, through the service: -185.58736 after fees.
    assert report["result"]["metrics"]["result"] == "-185.58736"
    assert records[1].evidence == ("synthetic_fixture",)
    assert store.has_report("cross-hundred@1") and not store.has_report("cross-hundred@1", confirmed=True)


def test_a_rerun_reproduces_the_stored_report_and_a_changed_input_does_not(lab, tmp_path) -> None:
    store, practice, candles = lab
    store.register(cross_strategy())
    store.create_study(_plan())
    record = practice.run(study_id="cross-study-1", phase="tune", strategy_ref="cross-hundred@1",
                          symbol="SYN/USD", source="synthetic", profiles=["okx-eea-spot"])[0]
    later = [candle.model_copy(update={"available_at_utc": NOW + timedelta(days=3), "open": D("99"),
                                       "high": D("99.1"), "low": D("99"), "close": D("99.1")})
             for candle in golden_candles()[40:41]]
    save(candles, later, at=NOW + timedelta(days=3))  # a revision received after the cut-off changes nothing
    check = practice.verify(record.run_id)
    assert check.reproduced and check.document["differences"] == []
    assert check.result_sha256 == record.result_sha256
    market, other = open_store(tmp_path / "other" / "market_data.sqlite")
    save(other, golden_candles()[:50])  # the same report against a store that lacks half the day
    different = PracticeRange(store, other, FrozenClock(NOW)).verify(record.run_id)
    assert not different.reproduced and "data" in different.document["differences"]
    assert [item.reproduced for item in store.verifications(record.run_id)] == [True, False]
    market.close()


def test_the_confirmation_window_is_never_used_for_tuning(lab) -> None:
    store, practice, _ = lab
    store.register(cross_strategy())
    store.create_study(_plan())
    with pytest.raises(ValidationFailure, match="inside"):
        practice.run(study_id="cross-study-1", phase="tune", strategy_ref="cross-hundred@1", symbol="SYN/USD",
                     source="synthetic", window=ReplayWindow(start=date(2026, 7, 1), end=date(2026, 7, 3)))
    with pytest.raises(ValidationFailure, match="never reused for tuning"):
        store.create_study(_plan("cross-study-2", tune=(date(2026, 6, 1), date(2026, 7, 3)),
                                 confirm=(date(2026, 7, 3), date(2026, 7, 4))))
    with pytest.raises(ValidationFailure, match="untouched"):
        store.create_study(_plan("cross-study-3", tune=(date(2026, 5, 1), date(2026, 6, 1)),
                                 confirm=(date(2026, 6, 15), date(2026, 7, 2))))
    assert store.runs() == []


def test_the_first_version_confirmed_spends_the_confirmation_window(lab) -> None:
    store, practice, _ = lab
    store.register(cross_strategy())
    store.register(cross_strategy(version=2, sizing={"equity_fraction": "0.25"}))
    store.create_study(_plan())
    for ref in ("cross-hundred@1", "cross-hundred@2"):
        practice.run(study_id="cross-study-1", phase="tune", strategy_ref=ref, symbol="SYN/USD", source="synthetic",
                     profiles=["kraken-spot-starting-tier"])
    with pytest.raises(ValidationFailure, match="whole confirmation window"):
        practice.run(study_id="cross-study-1", phase="confirm", strategy_ref="cross-hundred@2", symbol="SYN/USD",
                     source="synthetic", window=ReplayWindow(start=date(2026, 7, 2), end=date(2026, 7, 4)))
    confirmed = practice.run(study_id="cross-study-1", phase="confirm", strategy_ref="cross-hundred@2",
                             symbol="SYN/USD", source="synthetic")
    assert confirmed[0].report["selection"] == {"tune_runs": 2, "versions_tuned": ["cross-hundred@1",
                                                                                   "cross-hundred@2"],
                                                "confirm_runs": 0, "versions_confirmed": []}
    assert confirmed[2].report["selection"]["confirm_runs"] == 2
    with pytest.raises(ValidationFailure, match="spent on cross-hundred@2"):
        practice.run(study_id="cross-study-1", phase="confirm", strategy_ref="cross-hundred@1", symbol="SYN/USD",
                     source="synthetic")
    store.create_study(_plan("cross-study-2", confirm=(date(2026, 7, 3), date(2026, 7, 4))))
    later = practice.run(study_id="cross-study-2", phase="confirm", strategy_ref="cross-hundred@1",
                         symbol="SYN/USD", source="synthetic", profiles=["okx-eea-spot"])
    assert later[0].window == ReplayWindow(start=date(2026, 7, 3), end=date(2026, 7, 4))
    assert store.has_report("cross-hundred@2", confirmed=True)
    assert [record.phase for record in store.reports_for("cross-hundred@2")] == ["tune", "confirm", "confirm",
                                                                                "confirm"]


def test_a_three_month_study_on_fifteen_minute_candles_under_every_profile(tmp_path) -> None:
    state = Database(tmp_path / "state" / "trade_graph.sqlite")
    market, candles = open_store(tmp_path / "market" / "market_data.sqlite")
    series = build_candles("trend_pullback", count=9_000)  # 2026-01-05 onward, 93.75 days of 15-minute candles
    save(candles, series[:4_500])
    save(candles, series[4_500:])
    clock = FrozenClock(datetime(2026, 5, 1, tzinfo=UTC))
    store = PracticeRangeStore(state, clock)
    practice = PracticeRange(store, candles, clock)
    strategy = PracticeStrategy.model_validate_json(
        (ROOT / "strategies" / "practice" / "slow-trend-pullback.v1.json").read_text(encoding="utf-8"))
    store.register(strategy)
    plan = StudyPlan(study_id="trend-quarter", strategy_id="slow-trend-pullback",
                     tune=ReplayWindow(start=date(2026, 1, 8), end=date(2026, 3, 9)),
                     confirm=ReplayWindow(start=date(2026, 3, 9), end=date(2026, 4, 8)))
    store.create_study(plan)
    assert plan.replayed_days == 90 and plan.meets_three_month_minimum
    tuned = practice.run(study_id="trend-quarter", phase="tune", strategy_ref="slow-trend-pullback@1",
                         symbol="SYN/USD", source="synthetic", benchmark_symbol="SYN/USD")
    confirmed = practice.run(study_id="trend-quarter", phase="confirm", strategy_ref="slow-trend-pullback@1",
                             symbol="SYN/USD", source="synthetic", benchmark_symbol="SYN/USD")
    assert len(tuned) == len(confirmed) == 3
    for record in (*tuned, *confirmed):
        data = record.report["result"]["data"]
        assert data["window_missing"] == 0 and data["warmup_slots"] == 200
        assert record.report["study"]["meets_three_month_minimum"] is True
        assert record.report["result"]["metrics"]["trades"] > 0
    assert tuned[0].report["result"]["data"]["window_bars"] == 60 * 96
    assert confirmed[0].report["result"]["data"]["window_bars"] == 30 * 96
    fees = {record.cost_profile_ref: D(record.report["result"]["metrics"]["fees_paid"]) for record in confirmed}
    assert fees["okx-eea-spot@1"] < fees["kraken-spot-after-10k-volume@1"] < fees["kraken-spot-starting-tier@1"]
    assert practice.verify(confirmed[1].run_id).reproduced
    state.close()
    market.close()


class _Recorded:
    """Answers with a recorded public response; never reaches the network."""

    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, url: str) -> str:
        return self.text


def test_recorded_kraken_candles_replay_and_their_gaps_are_reported(tmp_path) -> None:
    state = Database(tmp_path / "state" / "trade_graph.sqlite")
    market, candles = open_store(tmp_path / "market" / "market_data.sqlite")
    pair = listed_pairs()["BTC/USD"]
    fetched = KrakenCandleFetcher(_Recorded(fixture_text("XBTUSD_15")), recorded_clock("XBTUSD_15")).fetch(pair, 15)
    run_id = candles.begin_run(kind="public_collect", source="kraken_public_ohlc",
                               evidence_label="recorded_public_fixture", started_at=fetched.received_at,
                               parameters={"fixture": "ohlc_XBTUSD_15.json"})
    candles.save(list(fetched.candles), run_id=run_id)
    clock = FrozenClock(datetime(2026, 10, 16, tzinfo=UTC))
    store = PracticeRangeStore(state, clock)
    practice = PracticeRange(store, candles, clock)
    strategy = PracticeStrategy.model_validate({
        "schema_version": 1, "strategy_id": "btc-average-cross", "version": 1, "title": "Close over its average",
        "hypothesis": "A close back above a short average starts a short move; a format check on recorded data.",
        "interval_minutes": 15, "indicators": {"average": {"indicator": "SMA", "parameters": {"timeperiod": 8}}},
        "entry": {"all_of": [{"left": {"candle": "close"}, "relation": "crosses_above",
                              "right": {"indicator": "average"}}], "order": {"kind": "market"}},
        "exit": {"stop_loss_fraction": "0.003", "take_profit_fraction": "0.003"},
        "sizing": {"equity_fraction": "0.2"}})
    store.register(strategy)
    store.create_study(StudyPlan(study_id="btc-recorded", strategy_id="btc-average-cross",
                                 tune=ReplayWindow(start=date(2026, 10, 12), end=date(2026, 10, 14)),
                                 confirm=ReplayWindow(start=date(2026, 10, 14), end=date(2026, 10, 15))))
    records = practice.run(study_id="btc-recorded", phase="tune", strategy_ref="btc-average-cross@1",
                           symbol="BTC/USD", source="kraken_public_ohlc")
    report = records[1].report
    data = report["result"]["data"]
    assert data["window_present"] == 120 and data["window_bars"] == 192
    assert [(item["first_open_utc"], item["slots"]) for item in data["missing_ranges"]] == [
        ("2026-10-12T00:00:00.000000Z", 37), ("2026-10-13T15:15:00.000000Z", 35)]
    assert report["evidence"]["labels"] == ["recorded_public_fixture"] and report["evidence"]["real_market_data"]
    assert report["venue"]["data_venue"] == "kraken"
    assert records[2].report["venue"]["stand_in"] is True  # Kraken prices under OKX fees
    assert report["result"]["benchmark_data"]["hold_btc"]["same_as"] == "hold_coin"
    metrics = report["result"]["metrics"]
    assert metrics["trades"] + len(metrics["open_positions"]) >= 1
    assert all(practice.verify(record.run_id).reproduced for record in records)
    json.dumps(report)  # a plain JSON document
    state.close()
    market.close()
