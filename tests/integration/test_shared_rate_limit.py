"""The collector and the Trades backfill on one limiter (W6-D, D221): together they never pass the ceiling.

A collection run and a backfill run share one ``KrakenRestLimiter`` and one scripted clock, as they do inside the
service. Every request either makes is replayed against a model of Kraken's own counter (its documented, faster
drain); the model never goes above the limiter's ceiling. No network, no real sleeping.
"""

from __future__ import annotations

from datetime import timedelta
from fractions import Fraction

import pytest
from tests.candle_support import FIXTURES, AdvancingSleeper, FakeKraken, QueueTransport, listed_pairs
from tests.integration.test_backfill_trades import NOW, seed, steady_trades
from tests.network_guard import block_network
from tests.trades_support import TradeScript

from trade_graph.adapters.market.kraken_trades import KrakenTrades
from trade_graph.adapters.market.rate_limit import (
    ASSUMED_CEILING,
    KRAKEN_DECAY_PER_SECOND,
    KrakenRestLimiter,
)
from trade_graph.adapters.persistence.market_data import MarketDataDatabase
from trade_graph.application.backfill_trades import BackfillScope, backfill_trades
from trade_graph.application.candle_collection_plan import CollectionScope
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.collect_candles import collect_candles, refresh_catalog
from trade_graph.domain.clock import FrozenClock

T0 = 1_791_892_800
ASSET_PAIRS = (FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


class Recording:
    """Remembers the clock reading of every request that reaches the scripted exchange."""

    def __init__(self, inner, clock: FrozenClock, log: list) -> None:
        self.inner, self.clock, self.log = inner, clock, log

    def get_text(self, url: str) -> str:
        self.log.append(self.clock.now())
        return self.inner.get_text(url)


def _kraken_peak(moments: list, start: Fraction) -> Fraction:
    """Kraken's counter, already at ``start`` when the limiter was made (at NOW), plus one per request."""
    real, last, peak = start, NOW, start
    for moment in moments:
        elapsed = Fraction((moment - last) // timedelta(microseconds=1), 1_000_000)
        last = moment
        real = max(Fraction(0), real - KRAKEN_DECAY_PER_SECOND * elapsed) + 1
        peak = max(peak, real)
    return peak


def test_a_collection_and_a_backfill_on_one_limiter_never_take_krakens_counter_past_the_ceiling(tmp_path) -> None:
    database = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    try:
        store = CandleStore(database)
        clock = FrozenClock(NOW)
        refresh_catalog(store, clock, QueueTransport([ASSET_PAIRS], clock), "recorded_public_fixture")
        seed(store, collector_first_open=T0 + 6 * 3600)
        limiter = KrakenRestLimiter(clock, AdvancingSleeper(clock), initial_counter=ASSUMED_CEILING,
                                    min_interval_seconds=Fraction(1))
        moments: list = []
        exchange = Recording(FakeKraken(clock, list(listed_pairs().values()), delay_seconds=0.2), clock, moments)
        report = collect_candles(store, clock, exchange, limiter, CollectionScope(intervals=(5, 15, 60),
                                 symbols=("BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"), one_minute_symbols=()),
                                 40, "synthetic_fixture")
        trades = Recording(TradeScript(steady_trades(1, T0, 400, every_seconds=60), clock=clock, delay_seconds=0.2),
                           clock, moments)
        result = backfill_trades(store, clock, KrakenTrades(trades, clock), limiter,
                                 BackfillScope(symbols=("BTC/USD",), intervals=(15,)), max_requests=30,
                                 evidence_label="synthetic_fixture", page_size=20)
        backfills = [run for run in store.runs() if run.kind == "trades_backfill"]
    finally:
        database.close()
    assert report["status"] == "ok" and report["requests"]["made"] == 12
    assert result.report["requests"]["made"] > 5
    assert len(moments) == limiter.calls == 12 + result.report["requests"]["made"]
    assert _kraken_peak(moments, Fraction(ASSUMED_CEILING)) <= ASSUMED_CEILING
    # The backfill's run record names the shared settings it ran under.
    assert result.report["requests"]["penalties"] == 0
    assert backfills[-1].parameters["limiter"] == {"max_counter": "15", "decay_per_second": "0.3", "cost": "1",
                                                   "headroom": "5", "min_interval_seconds": "1"}
