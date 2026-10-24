"""The coin scanner on recorded public Kraken data (T31 acceptance: deterministic on recorded fixtures).

Recorded: the pair list (53 pairs, 2026-10-13 14:58 UTC) and the hourly candles of BTC/USD, SOL/USD, LINK/USD
and USDUC/USD (120 committed candles each, 2026-10-13 about 15:20 UTC), from ``tests/fixtures/kraken/``. The
ticker answer is synthetic (``tests/fixtures/coin_scanner/``): Kraken was unreachable from the cloud.

Five days of hourly candles are not enough for the swing indicator, which needs about thirty (NATR 14 reads 710
candles, D136), so on this data no pair can be ranked. The test shows what the acceptance asks for here: the same
inputs always give the same result, and a missing input excludes the pair with its reason instead of a guess.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.candle_support import fixture_text, pair_records, recorded_clock
from tests.coin_scanner_support import new_store
from tests.unit.test_kraken_ticker import FIXTURE as TICKER_FIXTURE

from trade_graph.adapters.market.kraken_candles import KrakenCandleFetcher
from trade_graph.adapters.market.kraken_ticker import load_ticker_fixture
from trade_graph.application.coin_scanner.scanner import CoinScanner
from trade_graph.application.pair_classification import CLASSIFICATION_RULES_VERSION
from trade_graph.domain.clock import FrozenClock

CATALOG_OBSERVED = datetime(2026, 10, 13, 14, 58, 32, tzinfo=UTC)
AS_OF = datetime(2026, 10, 13, 15, 25, tzinfo=UTC)
RECORDED = {"XBTUSD_60": "BTC/USD", "SOLUSD_60": "SOL/USD", "LINKUSD_60": "LINK/USD", "USDUCUSD_60": "USDUC/USD"}


class _Recorded:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, url: str) -> str:
        return self.text


@pytest.fixture
def recorded_store(tmp_path):
    store = new_store(tmp_path / "private")
    records = pair_records()
    run = store.begin_run(kind="catalog", source="kraken_public_ohlc", evidence_label="recorded_public_fixture",
                          started_at=CATALOG_OBSERVED, parameters={"fixture": "asset_pairs_2026-10-13.json"})
    store.save_catalog([item.model_dump(mode="json") for item in records.values() if item.usd_quoted],
                       observed_at=CATALOG_OBSERVED, run_id=run, rules_version=CLASSIFICATION_RULES_VERSION)
    for name, symbol in RECORDED.items():
        fetch = KrakenCandleFetcher(_Recorded(fixture_text(name)), recorded_clock(name)).fetch(records[symbol], 60)
        candle_run = store.begin_run(kind="public_collect", source="kraken_public_ohlc",
                                     evidence_label="recorded_public_fixture", started_at=fetch.received_at,
                                     parameters={"fixture": f"ohlc_{name}.json"})
        store.save(list(fetch.candles), run_id=candle_run)
    return store


def _scan(store):
    return CoinScanner(store, clock=FrozenClock(AS_OF)).scan(as_of=AS_OF, ticker=load_ticker_fixture(TICKER_FIXTURE))


def _recorded_rows(name: str) -> list[list]:
    result = json.loads(fixture_text(name))["result"]
    key = next(item for item in result if item != "last")
    return result[key][:-1]  # the final row was still forming and is never stored


def test_the_same_recorded_inputs_give_the_same_scan_every_time(recorded_store) -> None:
    first, second = _scan(recorded_store), _scan(recorded_store)
    assert first.document() == second.document()
    assert first.status == "no_candidates" and not first.ranked and not first.shortlist
    assert first.evidence == "synthetic_fixture"  # the ticker is synthetic; the weakest input labels the scan


def test_the_recorded_catalog_exclusions_need_no_candles(recorded_store) -> None:
    excluded = _scan(recorded_store).document()["excluded"]
    assert excluded["stablecoin"] == sorted([
        "APXUSD/USD", "BRL1/USD", "CASH/USD", "DAI/USD", "EURC/USD", "EURR/USD", "FIDD/USD", "OUSD/USD", "PYUSD/USD",
        "RLUSD/USD", "TGBP/USD", "USD1/USD", "USDC/USD", "USDE/USD", "USDG/USD", "USDR/USD", "USDS/USD", "USDT/USD",
        "ZCHF/USD"])
    assert excluded["yield_token"] == sorted(["CMETH/USD", "JITOSOL/USD", "LSETH/USD", "LSSOL/USD", "METH/USD",
                                              "MSOL/USD", "OUSD/USD"])
    assert excluded["fiat_currency"] == ["AUD/USD", "EUR/USD", "GBP/USD"]
    assert excluded["not_normal_status"] == ["ACA/USD", "EURR/USD", "KIN/USD", "MAT/USD", "USDR/USD"]


def test_reviewed_coins_without_stored_candles_are_excluded_not_guessed(recorded_store) -> None:
    excluded = _scan(recorded_store).document()["excluded"]
    assert excluded["missing_candles"] == ["ADA/USD", "AVAX/USD", "DOGE/USD", "DOT/USD", "ETH/USD", "LTC/USD",
                                           "PAXG/USD", "PEPE/USD", "XRP/USD"]


def test_five_days_of_hourly_candles_leave_the_swing_warming_up(recorded_store) -> None:
    result = _scan(recorded_store)
    by_symbol = {item.symbol: item for item in result.evaluations}
    for symbol in RECORDED.values():
        swing = by_symbol[symbol].inputs["swing"]
        assert "missing_swing" in by_symbol[symbol].reasons
        assert swing["status"] == "warming_up" and swing["value"] is None
        assert swing["required_candles"] == 710 and swing["available_candles"] == 120
    assert set(by_symbol["BTC/USD"].reasons) == {"missing_swing"}
    assert set(by_symbol["USDUC/USD"].reasons) == {"missing_swing", "thin_volume", "wide_spread"}


def test_the_recorded_volume_is_the_exact_sum_of_kraken_volume_times_vwap(recorded_store) -> None:
    btc = next(item for item in _scan(recorded_store).evaluations if item.symbol == "BTC/USD")
    rows = _recorded_rows("XBTUSD_60")[-24:]
    expected = sum((Decimal(row[6]) * Decimal(row[5]) for row in rows), Decimal(0))
    volume = btc.inputs["volume"]
    assert Decimal(volume["value_usd"]) == expected and volume["price_basis"] == "vwap"
    assert volume["last_close_utc"] == "2026-10-13T15:00:00.000000Z"
    assert btc.inputs["candles"]["newest_received_at_utc"] == "2026-10-13T15:17:57.474908Z"


def test_the_recorded_trend_is_the_24_hour_rate_of_change_of_the_close(recorded_store) -> None:
    btc = next(item for item in _scan(recorded_store).evaluations if item.symbol == "BTC/USD")
    rows = _recorded_rows("XBTUSD_60")
    now, before = Decimal(rows[-1][4]), Decimal(rows[-25][4])
    expected = (now - before) / before
    trend = btc.inputs["trend"]
    assert trend["indicator"] == "ROCP" and trend["status"] == "ready"
    assert abs(Decimal(trend["value"]) - expected) <= abs(expected) * Decimal("1e-9")
