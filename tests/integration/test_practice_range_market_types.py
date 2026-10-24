"""Market types in the practice range (W11-A, C10; D286, D288, D289).

On one synthetic coin whose replay window is falling, then sideways, then rising, then unclear (a missing hour):

- a replay labels each decision with the live function's label for that moment on the same candles;
- a strategy's market-type condition enters only in that type;
- every report splits its trades by the market type at entry, and the split sums to the report's totals, a position
  still held at the end included;
- a request without market-type rules (how every report before W11-A was made) gives exactly the old document;
- the service stores the split, reruns it exactly and reads it out in plain sentences.

Synthetic candles only (``synthetic_fixture``); no network.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.market_type_support import (
    HOUR,
    REPLAY_WINDOW,
    busy_strategy,
    mixed_hours,
    quarters,
    store_series,
)
from tests.practice_range_support import open_store

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.market_types.labeller import label_coin
from trade_graph.application.practice_range import summary
from trade_graph.application.practice_range.replay import ReplayRequest, replay
from trade_graph.application.practice_range.service import PracticeRange
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.practice_range import ReplayWindow, StudyPlan
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.market_types import MARKET_TYPES
from trade_graph.kernel.cost_profiles import load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY
from trade_graph.kernel.market_type import CURRENT_MARKET_TYPE_RULES, MARKET_TYPE_RULES_V1

AS_OF = datetime(2026, 7, 1, tzinfo=UTC)
D = Decimal


def _fill(store, *, hourly: bool = True) -> None:
    hours = mixed_hours()
    if hourly:
        store_series(store, hours)
    store_series(store, quarters(hours, first=REPLAY_WINDOW.start_utc - HOUR, last=REPLAY_WINDOW.end_utc))


@pytest.fixture
def market(tmp_path):
    database, store = open_store(tmp_path / "market" / "market_data.sqlite")
    _fill(store)
    yield store
    database.close()


def _request(strategy, *, rules=MARKET_TYPE_RULES_V1, profile: str = "okx-eea-spot") -> ReplayRequest:
    return ReplayRequest(strategy=strategy, symbol="SYN/USD", source="synthetic", window=REPLAY_WINDOW,
                         data_as_of=AS_OF, profile=load_cost_profiles().get(profile), policy=BAR_REPLAY_POLICY,
                         capital=D("10000"), market_type_rules=rules)


def _rows(document: dict) -> dict[str, dict]:
    return {row["market_type"]: row for row in document["market_types"]["by_market_type"]}


def test_a_replay_sees_the_live_label_at_every_decision(market) -> None:
    result = replay(market, _request(busy_strategy(("is_not", "falling"))))
    seen = result.decision_labels
    # Four days of 15-minute candles, less the last close (no order there) and the missing hour's four quarters.
    assert len(seen) == result.events["decision_bars"] == 4 * 96 - 1 - 4
    assert {market_type for _, market_type in seen} == set(MARKET_TYPES)
    for moment, market_type in seen:
        live = label_coin(market, symbol="SYN/USD", moment=moment, source="synthetic", rules=MARKET_TYPE_RULES_V1)
        assert live.market_type == market_type, moment
    counts = result.to_document()["market_types"]["decision_bars"]
    assert list(counts) == list(MARKET_TYPES) and sum(counts.values()) == len(seen)


def test_a_market_type_condition_enters_only_in_that_type(market) -> None:
    rising = _rows(replay(market, _request(busy_strategy(("is", "rising")))).to_document())
    assert rising["rising"]["trades"] > 0
    assert all(rising[name]["trades"] == rising[name]["open_positions"] == 0 for name in MARKET_TYPES
               if name != "rising")
    other = _rows(replay(market, _request(busy_strategy(("is_not", "rising"), ("is_not", "unclear"))))
                  .to_document())
    assert other["rising"]["trades"] == other["unclear"]["trades"] == 0
    assert other["sideways"]["trades"] > 0 and other["falling"]["trades"] > 0


def _sums(document: dict, metrics) -> None:
    rows = document["market_types"]["by_market_type"]
    assert [row["market_type"] for row in rows] == list(MARKET_TYPES)
    assert sum(row["trades"] for row in rows) == metrics.trades
    assert sum(row["wins"] for row in rows) == metrics.wins
    assert sum(row["losses"] for row in rows) == metrics.losses
    assert sum(row["flat_trades"] for row in rows) == metrics.flat_trades
    assert sum(row["open_positions"] for row in rows) == len(metrics.open_positions)
    assert sum((D(row["result_after_fees"]) for row in rows), D("0")) == metrics.result
    assert D(document["metrics"]["result"]) == metrics.result


def test_the_split_by_market_type_at_entry_sums_to_the_report_totals(market) -> None:
    result = replay(market, _request(busy_strategy()))
    document = result.to_document()
    _sums(document, result.metrics)
    rows = _rows(document)
    assert sum(1 for name in MARKET_TYPES if rows[name]["trades"]) == 4  # trades in every type
    assert document["request"]["market_type_rules"] == {"ref": "market-type-rules@1",
                                                        "sha256": MARKET_TYPE_RULES_V1.content_sha256,
                                                        "interval_minutes": 60}
    candles = document["market_types"]["candles"]
    assert candles["interval_minutes"] == 60 and candles["source"] == "synthetic"
    assert candles["evidence_labels"] == ["synthetic_fixture"] and candles["candles_read"] > 863


def test_a_position_still_held_at_the_end_counts_in_its_entry_type(market) -> None:
    holder = busy_strategy(("is", "sideways"), exit={"stop_loss_fraction": "0.4"})
    result = replay(market, _request(holder))
    assert result.metrics.trades == 0 and len(result.metrics.open_positions) == 1
    document = result.to_document()
    _sums(document, result.metrics)
    row = _rows(document)["sideways"]
    assert row["open_positions"] == 1 and D(row["result_after_fees"]) == result.metrics.result != 0
    assert D(row["open_position_result"]) == result.metrics.result and row["closed_trades_result"] == "0"


def test_without_rules_a_replay_makes_exactly_the_document_it_made_before(market) -> None:
    plain = replay(market, _request(busy_strategy(), rules=None))
    assert plain.market_types is None and plain.decision_labels == ()
    labelled = replay(market, _request(busy_strategy())).to_document()
    old = plain.to_document()
    assert "market_types" not in old and "market_type_rules" not in old["request"]
    labelled.pop("market_types")
    labelled["request"].pop("market_type_rules")
    assert labelled == old  # labelling changes no decision of a strategy that does not read it


def test_a_strategy_that_reads_the_type_needs_the_rules(market) -> None:
    with pytest.raises(ValidationFailure, match="market-type rules"):
        _request(busy_strategy(("is", "rising")), rules=None)


def test_without_label_candles_every_type_is_unclear(tmp_path) -> None:
    database, store = open_store(tmp_path / "quarters.sqlite")
    _fill(store, hourly=False)
    result = replay(store, _request(busy_strategy()))
    document = result.to_document()
    _sums(document, result.metrics)
    assert document["market_types"]["decision_bars"]["unclear"] == result.events["decision_bars"]
    assert _rows(document)["unclear"]["trades"] == result.metrics.trades > 0
    assert document["market_types"]["candles"]["candles_read"] == 0
    database.close()


@pytest.fixture
def lab(tmp_path, market):
    state = Database(tmp_path / "state" / "trade_graph.sqlite")
    clock = FrozenClock(AS_OF)
    store = PracticeRangeStore(state, clock)
    store.register(busy_strategy())
    store.create_study(StudyPlan(study_id="busy-1", strategy_id="busy-test", tune=REPLAY_WINDOW,
                                 confirm=ReplayWindow(start=date(2026, 6, 11), end=date(2026, 6, 12))))
    yield store, PracticeRange(store, market, clock)
    state.close()


def test_the_service_stores_the_split_reruns_it_and_reads_it_out(lab) -> None:
    store, practice = lab
    records = practice.run(study_id="busy-1", phase="tune", strategy_ref="busy-test@1", symbol="SYN/USD",
                           source="synthetic", profiles=["okx-eea-spot", "kraken-spot-starting-tier"])
    for record in records:
        result = record.report["result"]
        assert result["request"]["market_type_rules"]["ref"] == CURRENT_MARKET_TYPE_RULES.ref
        assert record.evidence == ("synthetic_fixture",)
        check = practice.verify(record.run_id)
        assert check.reproduced and check.result_sha256 == record.result_sha256
        lines = summary.report_lines(record)
        heading = next(index for index, line in enumerate(lines) if line.startswith("By market type at entry"))
        assert CURRENT_MARKET_TYPE_RULES.ref in lines[heading] and "60-minute" in lines[heading]
        rows = {row["market_type"]: row for row in result["market_types"]["by_market_type"]}
        for offset, name in enumerate(MARKET_TYPES, start=1):
            line = lines[heading + offset]
            assert line.startswith(f"{name.capitalize()} at entry: {rows[name]['trades']} trades")
            assert "of the decision candles" in line
        overview = summary.report_overview(record)
        assert overview["market_type_rules"] == CURRENT_MARKET_TYPE_RULES.ref
        assert [row["market_type"] for row in overview["by_market_type"]] == list(MARKET_TYPES)
        assert sum(row["trades"] for row in overview["by_market_type"]) == overview["trades"]
        assert sum((D(row["result_after_fees"]) for row in overview["by_market_type"]), D("0")) == D(
            overview["result"])
    assert records[0].report["result"]["market_types"] != records[1].report["result"]["market_types"]


def test_a_changed_rules_hash_is_not_reproduced(lab) -> None:
    store, practice = lab
    record = practice.run(study_id="busy-1", phase="tune", strategy_ref="busy-test@1", symbol="SYN/USD",
                          source="synthetic", profiles=["okx-eea-spot"])[0]
    report = record.report
    report["result"]["request"]["market_type_rules"]["sha256"] = "0" * 64
    edited = store.record_run(
        study_id="busy-1", phase="tune", strategy_ref="busy-test@1", symbol="SYN/USD", interval_minutes=15,
        source="synthetic", cost_profile_ref=record.cost_profile_ref, fill_policy_ref=record.fill_policy_ref,
        window=record.window, data_as_of=record.data_as_of, evidence=record.evidence,
        result_sha256=record.result_sha256, report=report)
    check = practice.verify(edited.run_id)
    assert not check.reproduced
    assert "the market-type rules no longer match the report's hash" in check.document["problems"]
    report["result"]["request"]["market_type_rules"]["ref"] = "market-type-rules@9"
    unknown = store.record_run(
        study_id="busy-1", phase="tune", strategy_ref="busy-test@1", symbol="SYN/USD", interval_minutes=15,
        source="synthetic", cost_profile_ref=record.cost_profile_ref, fill_policy_ref=record.fill_policy_ref,
        window=record.window, data_as_of=record.data_as_of, evidence=record.evidence,
        result_sha256=record.result_sha256, report=report)
    later = practice.verify(unknown.run_id)
    assert not later.reproduced
    assert "the market-type rules market-type-rules@9 are unknown to this version" in later.document["problems"]
