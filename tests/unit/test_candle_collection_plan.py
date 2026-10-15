"""What a collection run covers, in what order, and what it costs. Pure functions over the recorded catalog."""

from __future__ import annotations

import re
from fractions import Fraction

import pytest
from tests.candle_support import pair_records

from trade_graph.application.candle_collection_plan import (
    BYTES_PER_ROW,
    DEFAULT_ONE_MINUTE_SYMBOLS,
    CollectionScope,
    cadence_report,
    plan_collection,
    rows_per_quarter,
    storage_projection,
    window_hours,
)
from trade_graph.domain.errors import ValidationFailure

RECORDS = list(pair_records().values())
ELIGIBLE = sorted(record.symbol for record in RECORDS if record.usd_quoted and record.eligible_for_collection)


def _plan(scope: CollectionScope | None = None, newest: dict | None = None):
    return plan_collection(RECORDS, scope or CollectionScope(), newest or {})


# --- scope ----------------------------------------------------------------------------------------------------------


def test_the_default_scope_is_all_four_intervals_all_pairs_and_one_minute_for_bitcoin_and_ether() -> None:
    scope = CollectionScope()
    assert scope.intervals == (1, 5, 15, 60) and scope.symbols is None
    assert scope.one_minute_symbols == DEFAULT_ONE_MINUTE_SYMBOLS == ("BTC/USD", "ETH/USD")
    assert scope.to_document() == {
        "intervals": [1, 5, 15, 60], "symbols": None, "one_minute_symbols": ["BTC/USD", "ETH/USD"],
    }


def test_intervals_are_sorted_and_deduplicated_in_the_document_order() -> None:
    assert CollectionScope(intervals=(60, 5, 15)).intervals == (5, 15, 60)


@pytest.mark.parametrize("options", [
    {"intervals": ()}, {"intervals": (30,)}, {"intervals": (240, 5)}, {"intervals": (5, 5)}, {"intervals": (True,)},
    {"intervals": ("5",)}, {"intervals": [5]},
    {"symbols": ("btc/usd",)}, {"symbols": ("BTC",)}, {"symbols": ("BTC/USD ",)},
    {"symbols": ("BTC/USD", "BTC/USD")},
    {"symbols": ("A" * 17 + "/USD",)}, {"symbols": ()}, {"symbols": ["BTC/USD"]}, {"symbols": ("BTC/EUR",)},
    {"one_minute_symbols": ("ETH/USD", "ETH/USD")}, {"one_minute_symbols": ("x",)},
    {"one_minute_symbols": ["BTC/USD"]},
])
def test_a_malformed_scope_is_refused(options: dict) -> None:
    with pytest.raises(ValidationFailure):
        CollectionScope(**options)


def test_an_empty_one_minute_list_means_no_one_minute_collection() -> None:
    plan = _plan(CollectionScope(one_minute_symbols=()))
    assert 1 not in {item.interval_minutes for item in plan.series}


# --- the plan -------------------------------------------------------------------------------------------------------


def test_every_eligible_pair_is_planned_at_5_15_and_60_and_only_the_default_pair_at_1() -> None:
    plan = _plan()
    by_interval: dict[int, list[str]] = {}
    for item in plan.series:
        by_interval.setdefault(item.interval_minutes, []).append(item.record.symbol)
    assert sorted(by_interval[1]) == ["BTC/USD", "ETH/USD"]
    for interval in (5, 15, 60):
        assert sorted(by_interval[interval]) == ELIGIBLE
    assert len(plan.series) == 2 + 3 * len(ELIGIBLE) == 41


def test_the_plan_runs_the_most_perishable_data_first() -> None:
    plan = _plan()
    intervals = [item.interval_minutes for item in plan.series]
    assert intervals == sorted(intervals)
    assert [window_hours(interval) for interval in (1, 5, 15, 60)] == [12, 60, 180, 720]


def test_inside_an_interval_the_series_closest_to_losing_data_come_first_and_new_series_last() -> None:
    newest = {("SOL/USD", 15): 1_000_000, ("ETH/USD", 15): 500_000, ("BTC/USD", 15): 900_000}
    plan = _plan(CollectionScope(intervals=(15,)), newest)
    symbols = [item.record.symbol for item in plan.series]
    assert symbols[:3] == ["ETH/USD", "BTC/USD", "SOL/USD"]  # oldest newest-stored candle first
    assert symbols[3:] == sorted(symbols[3:]) and len(symbols) == len(ELIGIBLE)


def test_since_is_the_newest_stored_open_minus_one_interval_or_absent() -> None:
    newest = {("BTC/USD", 15): 1_791_903_600, ("BTC/USD", 60): 1_791_900_000}
    plan = _plan(CollectionScope(intervals=(15, 60), symbols=("BTC/USD", "ETH/USD")), newest)
    since = {(item.record.symbol, item.interval_minutes): item.since for item in plan.series}
    assert since[("BTC/USD", 15)] == 1_791_903_600 - 900
    assert since[("BTC/USD", 60)] == 1_791_900_000 - 3600
    assert since[("ETH/USD", 15)] is None and since[("ETH/USD", 60)] is None


def test_pairs_that_may_not_be_collected_are_skipped_with_their_reasons() -> None:
    plan = _plan()
    skipped = {item.symbol: item.reasons for item in plan.skipped}
    assert len(skipped) == sum(record.usd_quoted for record in RECORDS) - len(ELIGIBLE) == 31
    assert skipped["USDT/USD"] == ("excluded:stablecoin",)
    assert skipped["MSOL/USD"] == ("excluded:yield_token",)
    assert skipped["EUR/USD"] == ("excluded:fiat_currency",)
    assert skipped["ACA/USD"] == ("status:cancel_only",)
    assert skipped["EURR/USD"] == ("excluded:stablecoin", "status:post_only")
    assert skipped["OUSD/USD"] == ("excluded:stablecoin", "excluded:yield_token")
    assert not {item.record.symbol for item in plan.series} & set(skipped)


def test_an_explicit_pair_list_narrows_the_wide_intervals_and_reports_what_it_could_not_use() -> None:
    scope = CollectionScope(
        symbols=("SOL/USD", "USDT/USD", "NOPE/USD", "ACA/USD"), one_minute_symbols=("SOL/USD",))
    plan = _plan(scope)
    assert {item.record.symbol for item in plan.series} == {"SOL/USD"}
    assert sorted(item.interval_minutes for item in plan.series) == [1, 5, 15, 60]
    assert plan.unknown_symbols == ("NOPE/USD",)
    assert {item.symbol for item in plan.skipped} == {"USDT/USD", "ACA/USD"}


def test_the_one_minute_list_is_independent_of_the_pair_list_and_reports_unknowns_once() -> None:
    scope = CollectionScope(symbols=("SOL/USD",), one_minute_symbols=("BTC/USD", "NOPE/USD", "USDC/USD"))
    plan = _plan(scope)
    one_minute = [item.record.symbol for item in plan.series if item.interval_minutes == 1]
    assert one_minute == ["BTC/USD"]
    assert plan.unknown_symbols == ("NOPE/USD",)
    assert [item.symbol for item in plan.skipped] == ["USDC/USD"]
    scope = CollectionScope(symbols=("USDC/USD",), one_minute_symbols=("USDC/USD",))
    assert [item.symbol for item in _plan(scope).skipped] == ["USDC/USD"]


def test_no_one_minute_list_means_the_same_pairs_as_the_other_intervals() -> None:
    everything = _plan(CollectionScope(one_minute_symbols=None))
    one_minute = sorted(item.record.symbol for item in everything.series if item.interval_minutes == 1)
    assert one_minute == ELIGIBLE and len(everything.series) == 4 * len(ELIGIBLE) == 52
    narrowed = _plan(CollectionScope(symbols=("SOL/USD", "LINK/USD"), one_minute_symbols=None))
    assert sorted((item.record.symbol, item.interval_minutes) for item in narrowed.series) == [
        (symbol, interval) for symbol in ("LINK/USD", "SOL/USD") for interval in (1, 5, 15, 60)]
    assert CollectionScope(one_minute_symbols=None).to_document()["one_minute_symbols"] is None
    skipped = _plan(CollectionScope(intervals=(1,), one_minute_symbols=None))
    assert len(skipped.series) == len(ELIGIBLE) and len(skipped.skipped) == 31 and skipped.unknown_symbols == ()


def test_an_empty_catalog_plans_nothing_and_every_requested_name_is_unknown() -> None:
    plan = plan_collection([], CollectionScope(symbols=("SOL/USD",)), {})
    assert plan.series == () and plan.unknown_symbols == ("BTC/USD", "ETH/USD", "SOL/USD")


# --- cost -----------------------------------------------------------------------------------------------------------


def test_a_quarter_of_candles_matches_the_documented_row_counts() -> None:
    assert BYTES_PER_ROW == 118
    assert [rows_per_quarter(interval) for interval in (1, 5, 15, 60)] == [131_040, 26_208, 8_736, 2_184]


def test_the_storage_projection_multiplies_series_by_rows_by_118_bytes() -> None:
    projection = storage_projection({1: 2, 5: 13, 15: 13, 60: 13})
    assert projection["bytes_per_row_estimate"] == 118
    one = projection["per_quarter"]["1"]
    assert one == {"series": 2, "rows": 262_080, "estimated_bytes": 262_080 * 118}
    total_rows = 2 * 131_040 + 13 * (26_208 + 8_736 + 2_184)
    assert projection["per_quarter_total"] == {"rows": total_rows, "estimated_bytes": total_rows * 118}
    empty = storage_projection({})
    assert empty["per_quarter_total"] == {"rows": 0, "estimated_bytes": 0}


def test_the_cadence_report_names_the_window_each_interval_can_bridge_and_the_time_a_pass_takes() -> None:
    report = cadence_report({1: 2, 5: 600, 15: 600, 60: 600}, ceiling=10, decay_per_second=Fraction(3, 10))
    assert report["candles_per_response"] == 720
    assert report["intervals"]["1"]["window_hours"] == 12
    assert report["intervals"]["1"]["longest_pause_without_a_gap_hours"] == 12
    assert report["intervals"]["1"]["recommended_every_hours"] == 6
    assert report["intervals"]["5"]["window_hours"] == 60 and report["intervals"]["5"]["recommended_every_hours"] == 30
    assert report["intervals"]["15"]["longest_pause_without_a_gap_hours"] == 180
    assert report["intervals"]["60"]["longest_pause_without_a_gap_hours"] == 720
    assert report["intervals"]["5"]["requests_per_pass"] == 600
    # (600 - 10 free) / 0.3 per second = 1966.67 s = 32.8 minutes
    assert report["intervals"]["5"]["minutes_per_pass_at_the_limiter_rate"] == pytest.approx(32.8, abs=0.1)
    assert report["intervals"]["1"]["minutes_per_pass_at_the_limiter_rate"] == 0
    assert report["all_intervals"]["requests_per_pass"] == 1802
    assert report["all_intervals"]["longest_pause_without_a_gap_hours"] == 12
    assert re.search(r"gap", report["note"])


def test_the_cadence_report_for_a_scope_without_series_has_no_pause_limit() -> None:
    report = cadence_report({}, ceiling=10, decay_per_second=Fraction(3, 10))
    assert report["intervals"] == {} and report["all_intervals"]["longest_pause_without_a_gap_hours"] is None
