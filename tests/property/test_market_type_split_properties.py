"""A report's split by market type at entry always sums to its totals (W11-A, C10; D289).

- Kernel: any trades and open positions, put in any of the four types, give rows whose trades, wins, losses, flat
  trades and result add up exactly, in the fixed order rising, sideways, falling, unclear.
- Replay: random strategy settings (entry kind, offset, life, stop, target, holding limit, size, a market-type
  condition, cost profile) on the synthetic coin whose window is falling, sideways, rising and unclear give a split
  that sums to the report's trades, wins, losses and result after fees, a position held at the end included.

Synthetic data only (``synthetic_fixture``); no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.market_type_support import HOUR, REPLAY_WINDOW, busy_strategy, mixed_hours, quarters, store_series
from tests.practice_range_support import open_store

from trade_graph.application.practice_range.replay import ReplayRequest, replay
from trade_graph.domain.market_types import MARKET_TYPES
from trade_graph.kernel.cost_profiles import load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY
from trade_graph.kernel.market_type import MARKET_TYPE_RULES_V1, split_by_market_type
from trade_graph.kernel.trade_metrics import RoundTrip

D = Decimal
T0 = datetime(2026, 7, 1, tzinfo=UTC)
AS_OF = datetime(2026, 7, 1, tzinfo=UTC)

trips = st.lists(st.tuples(st.sampled_from(MARKET_TYPES), st.integers(1, 10_000), st.integers(1, 10_000),
                           st.integers(0, 50)), max_size=30)
opens = st.lists(st.tuples(st.sampled_from(MARKET_TYPES), st.integers(-5_000, 5_000)), max_size=2)


@settings(max_examples=300, deadline=None, derandomize=True)
@given(trades=trips, held=opens)
def test_the_kernel_split_sums_to_the_totals(trades, held) -> None:
    built = [(kind, RoundTrip(symbol="SYN/USD", opened_at=T0 + timedelta(minutes=index),
                              closed_at=T0 + timedelta(minutes=index + 1), quantity=D("1"), bought=D(bought) / 100,
                              sold=D(sold) / 100, fees=D(fees) / 100, fill_refs=(f"f{index}",)))
             for index, (kind, bought, sold, fees) in enumerate(trades)]
    positions = [(kind, D(value) / 100) for kind, value in held]
    rows = split_by_market_type(built, positions)
    assert [row.group for row in rows] == list(MARKET_TYPES)
    assert sum(row.trades for row in rows) == len(built)
    assert sum(row.wins for row in rows) == sum(1 for _, trip in built if trip.outcome == "win")
    assert sum(row.losses for row in rows) == sum(1 for _, trip in built if trip.outcome == "loss")
    assert sum(row.flat_trades for row in rows) == sum(1 for _, trip in built if trip.outcome == "flat")
    assert sum(row.open_positions for row in rows) == len(positions)
    total = sum((trip.result for _, trip in built), D("0")) + sum((value for _, value in positions), D("0"))
    assert sum((row.result for row in rows), D("0")) == total
    for row in rows:
        assert row.trades == row.wins + row.losses + row.flat_trades


@pytest.fixture(scope="module")
def mixed_market(tmp_path_factory):
    database, store = open_store(tmp_path_factory.mktemp("mixed") / "market_data.sqlite")
    hours = mixed_hours()
    store_series(store, hours)
    store_series(store, quarters(hours, first=REPLAY_WINDOW.start_utc - HOUR, last=REPLAY_WINDOW.end_utc))
    yield store
    database.close()


choices = st.fixed_dictionaries({
    "kind": st.sampled_from(["market", "limit"]),
    "offset": st.sampled_from(["0", "0.002", "0.005"]),
    "expires": st.integers(1, 4),
    "stop": st.sampled_from(["0.004", "0.01", "0.4"]),
    "target": st.sampled_from([None, "0.003", "0.02"]),
    "holding": st.sampled_from([None, 2, 12]),
    "fraction": st.sampled_from(["0.2", "1"]),
    "condition": st.sampled_from([(), (("is", "rising"),), (("is_not", "falling"),), (("is", "unclear"),)]),
    "profile": st.sampled_from(["kraken-spot-starting-tier", "okx-eea-spot"]),
})


@settings(max_examples=25, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(chosen=choices)
def test_every_replay_split_sums_to_its_report(mixed_market, chosen) -> None:
    order = {"kind": "market"} if chosen["kind"] == "market" else {
        "kind": "limit", "price": {"candle": "close"}, "offset_fraction": chosen["offset"],
        "expires_after_bars": chosen["expires"]}
    exits = {"stop_loss_fraction": chosen["stop"], "take_profit_fraction": chosen["target"],
             "max_holding_bars": chosen["holding"]}
    strategy = busy_strategy(*chosen["condition"])
    document = strategy.canonical_document()
    document["entry"]["order"] = order
    document["exit"] = {key: value for key, value in exits.items() if value is not None}
    document["sizing"] = {"equity_fraction": chosen["fraction"]}
    strategy = type(strategy).model_validate(document)
    request = ReplayRequest(strategy=strategy, symbol="SYN/USD", source="synthetic", window=REPLAY_WINDOW,
                            data_as_of=AS_OF, profile=load_cost_profiles().get(chosen["profile"]),
                            policy=BAR_REPLAY_POLICY, capital=D("10000"), market_type_rules=MARKET_TYPE_RULES_V1)
    result = replay(mixed_market, request)
    rows = result.to_document()["market_types"]["by_market_type"]
    metrics = result.metrics
    assert [row["market_type"] for row in rows] == list(MARKET_TYPES)
    assert sum(row["trades"] for row in rows) == metrics.trades
    assert sum(row["wins"] for row in rows) == metrics.wins
    assert sum(row["losses"] for row in rows) == metrics.losses
    assert sum(row["flat_trades"] for row in rows) == metrics.flat_trades
    assert sum(row["open_positions"] for row in rows) == len(metrics.open_positions)
    assert sum((D(row["result_after_fees"]) for row in rows), D("0")) == metrics.result
    if chosen["condition"] and chosen["condition"][0][0] == "is":
        wanted = chosen["condition"][0][1]
        assert all(row["trades"] == row["open_positions"] == 0 for row in rows if row["market_type"] != wanted)
