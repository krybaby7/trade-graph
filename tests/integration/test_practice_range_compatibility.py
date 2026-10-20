"""Strategy documents and stored reports made before market types (W11-A, C10) stay valid.

The two shipped strategies keep their registered hashes (L2 registered them as ``slow-trend-pullback@1``
``771329ed...`` and ``range-reversion@1`` ``df0229bb...``), and a report stored by the code at 663465e (the base of
W11-A, ``tests/fixtures/practice_range/stored-report-663465e.json``, made on the golden synthetic day) is still
reproduced exactly by ``practice-range-verify`` and still reads as plain sentences, without a market-type split.
Synthetic candles only; no network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from tests.practice_range_support import cross_strategy, golden_btc, golden_candles, open_store, save

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.practice_range import summary
from trade_graph.application.practice_range.service import PracticeRange
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow, StudyPlan
from trade_graph.domain.clock import FrozenClock

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 2, tzinfo=UTC)
FIXTURE = ROOT / "tests" / "fixtures" / "practice_range" / "stored-report-663465e.json"
SHIPPED = {
    "range-reversion.v1.json": ("range-reversion@1",
                                "df0229bb1e96b3deac23a4812f160406ad257db05cd132bfac85da7dedef729a"),
    "slow-trend-pullback.v1.json": ("slow-trend-pullback@1",
                                    "771329eda2035da0a21ac8727beeb116ad4584518c788a7a0dd6132cb7bbcc2a"),
}
# The golden test strategy (tests/practice_range_support.py) and the stored report's result, as made at 663465e.
CROSS_SHA256 = "233f9b777a1f0dec56c438f78b95ce8887c53844650caa8f5bb3c08090d50c90"
OLD_RESULT_SHA256 = "8807097ba35ac8b75fd38cc520cf1cac603728c4e5d20a3af66509aab625bc74"


def test_the_shipped_strategy_documents_keep_their_registered_hashes() -> None:
    found = {path.name: path for path in (ROOT / "strategies" / "practice").glob("*.json")}
    assert sorted(found) == sorted(SHIPPED)
    for name, (ref, sha256) in SHIPPED.items():
        strategy = PracticeStrategy.model_validate_json(found[name].read_text(encoding="utf-8"))
        assert (strategy.ref, strategy.content_sha256) == (ref, sha256)
        assert "market_type" not in json.dumps(strategy.canonical_document())


def _day_two(candles: list) -> list:
    return [candle.model_copy(update={"open_time_utc": candle.open_time_utc + timedelta(days=1),
                                      "close_time_utc": candle.close_time_utc + timedelta(days=1)})
            for candle in candles if candle.open_time_utc >= datetime(2026, 7, 1, tzinfo=UTC)]


@pytest.fixture
def stored(tmp_path):
    """The fixture report written into a practice database as the code at 663465e stored it."""
    state = Database(tmp_path / "state" / "trade_graph.sqlite")
    market, candles = open_store(tmp_path / "market" / "market_data.sqlite")
    save(candles, golden_candles() + _day_two(golden_candles()))
    save(candles, golden_btc())
    clock = FrozenClock(NOW)
    store = PracticeRangeStore(state, clock)
    store.register(cross_strategy())
    store.create_study(StudyPlan(study_id="cross-study-1", strategy_id="cross-hundred",
                                 tune=ReplayWindow(start=date(2026, 7, 1), end=date(2026, 7, 2)),
                                 confirm=ReplayWindow(start=date(2026, 7, 2), end=date(2026, 7, 3))))
    old = json.loads(FIXTURE.read_text(encoding="utf-8"))
    request = old["report"]["result"]["request"]
    record = store.record_run(
        study_id="cross-study-1", phase="tune", strategy_ref="cross-hundred@1", symbol="SYN/USD",
        interval_minutes=15, source="synthetic", cost_profile_ref=request["cost_profile"]["ref"],
        fill_policy_ref=request["fill_policy"]["ref"], window=ReplayWindow(start=date(2026, 7, 1),
                                                                           end=date(2026, 7, 2)),
        data_as_of=old["data_as_of"], evidence=tuple(old["evidence"]), result_sha256=old["result_sha256"],
        report=old["report"])
    yield old, record, PracticeRange(store, candles, clock)
    state.close()
    market.close()


def test_the_old_report_fixture_is_the_one_663465e_made() -> None:
    old = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert old["result_sha256"] == OLD_RESULT_SHA256 == old["report"]["result_sha256"]
    assert cross_strategy().content_sha256 == CROSS_SHA256 == old["report"]["result"]["request"]["strategy"]["sha256"]
    assert "market_types" not in old["report"]["result"]
    assert "market_type_rules" not in old["report"]["result"]["request"]
    assert old["report"]["result"]["metrics"]["result"] == "-185.58736"


def test_an_old_stored_report_is_still_reproduced_exactly(stored) -> None:
    _, record, practice = stored
    check = practice.verify(record.run_id)
    assert check.reproduced, check.document
    assert check.result_sha256 == OLD_RESULT_SHA256 and check.document["differences"] == []


def test_an_old_stored_report_still_reads_without_a_market_type_split(stored) -> None:
    old, record, _ = stored
    lines = summary.report_lines(record)
    assert lines[0] == f"Practice-range report {record.run_id} (tune, study cross-study-1)."
    assert not any("market type" in line.lower() for line in lines)
    assert summary.report_text(record) == "\n".join(lines)
    overview = summary.report_overview(record)
    assert overview["result_sha256"] == OLD_RESULT_SHA256 and overview["trades"] == 2
    assert overview.get("by_market_type", []) == []
