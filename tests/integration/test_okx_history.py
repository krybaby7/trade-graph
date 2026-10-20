"""OKX history into the candle store and through the practice range under the OKX profile (T29, D126).

The folder is the labelled synthetic OKX-format fixture; the live path runs on a scripted transport that says it is
synthetic. Nothing reaches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from tests.okx_support import FIXTURE, pages
from tests.practice_range_support import open_store

from trade_graph.adapters.market.okx_candles import OkxCandleFetcher
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.okx_history import collect_okx_candles, import_okx_recording
from trade_graph.application.practice_range.service import PracticeRange
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow, StudyPlan
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

NOW = datetime(2026, 7, 10, tzinfo=UTC)


@pytest.fixture
def market(tmp_path):
    database, store = open_store(tmp_path / "market" / "market_data.sqlite")
    yield store
    database.close()


def test_a_recorded_folder_is_stored_as_its_own_okx_series_with_its_label(market) -> None:
    report = import_okx_recording(market, FIXTURE, FrozenClock(NOW))
    assert (report["candles"], report["forming_dropped"], report["evidence_label"]) == (600, 1, "synthetic_fixture")
    assert report["first_open_utc"] == "2026-07-01T00:00:00.000000Z"
    assert report["last_close_utc"] == "2026-07-07T06:00:00.000000Z"
    run = market.runs()[-1]
    assert (run.kind, run.source, run.evidence_label, run.status) == ("archive_import", "okx_public_candles",
                                                                      "synthetic_fixture", "ok")
    series = market.series()
    assert [(item.symbol, item.source, item.source_pair, item.candle_count) for item in series] == [
        ("SOL/USDT", "okx_public_candles", "SOL-USDT", 600)]
    again = import_okx_recording(market, FIXTURE, FrozenClock(NOW))
    assert (again["inserted"], again["duplicates"]) == (0, 600)  # the same pages add nothing


class _ScriptedOkx:
    """Serves the fixture pages by their ``after`` parameter, like OKX paging back; synthetic and says so."""

    synthetic = True

    def __init__(self) -> None:
        self.by_after = {}
        after = None
        for text in pages():
            self.by_after[after] = text
            after = json.loads(text)["data"][-1][0]
        self.by_after[after] = json.dumps({"code": "0", "msg": "", "data": []})
        self.urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        after = url.split("after=")[1] if "after=" in url else None
        return self.by_after[after]


def test_a_stopped_run_resumes_from_before_ms_without_asking_for_newer_pages(market) -> None:
    transport = _ScriptedOkx()
    resume_at = json.loads(pages()[0])["data"][-1][0]  # the oldest open time on the first page
    report = collect_okx_candles(market, OkxCandleFetcher(transport, FrozenClock(NOW)), FrozenClock(NOW),
                                 inst_id="SOL-USDT", interval_minutes=15, pages=50, before_ms=int(resume_at),
                                 sleep=lambda _seconds: None)
    assert report["status"] == "ok" and report["requests"] == 7 and 0 < report["candles"] < 600
    assert f"after={resume_at}" in transport.urls[0] and len(transport.urls) == 7
    assert market.runs()[-1].parameters["before_ms"] == int(resume_at)
    with pytest.raises(ValidationFailure):
        collect_okx_candles(market, OkxCandleFetcher(transport, FrozenClock(NOW)), FrozenClock(NOW),
                            inst_id="SOL-USDT", interval_minutes=15, pages=1, before_ms=0)


def test_paging_back_saves_each_page_and_a_scripted_source_is_labelled_synthetic(market, tmp_path) -> None:
    transport = _ScriptedOkx()
    pauses: list[float] = []
    report = collect_okx_candles(market, OkxCandleFetcher(transport, FrozenClock(NOW)), FrozenClock(NOW),
                                 inst_id="SOL-USDT", interval_minutes=15, pages=50, record_to=tmp_path / "rec",
                                 sleep=pauses.append)
    assert report["candles"] == 600 and report["requests"] == 8 and report["status"] == "ok"
    assert len(transport.urls) == 8 and pauses == [0.25] * 7  # 7 full or partial pages, then an empty one
    assert market.runs()[-1].evidence_label == "synthetic_fixture"
    manifest = json.loads((tmp_path / "rec" / "manifest.json").read_text())
    assert manifest["evidence_label"] == "synthetic_fixture" and len(manifest["pages"]) == 8


def test_okx_prices_replay_under_the_okx_profile_with_their_own_currency(market, tmp_path) -> None:
    import_okx_recording(market, FIXTURE, FrozenClock(NOW))
    state = Database(tmp_path / "state" / "trade_graph.sqlite")
    clock = FrozenClock(NOW)
    store = PracticeRangeStore(state, clock)
    strategy = PracticeStrategy.model_validate({
        "schema_version": 1, "strategy_id": "okx-wave", "version": 1, "title": "Average cross on OKX",
        "hypothesis": "A close back above its average starts a swing; a format check on the OKX fixture.",
        "interval_minutes": 15, "indicators": {"average": {"indicator": "SMA", "parameters": {"timeperiod": 20}}},
        "entry": {"all_of": [{"left": {"candle": "close"}, "relation": "crosses_above",
                              "right": {"indicator": "average"}}],
                  "order": {"kind": "limit", "price": {"candle": "close"}, "offset_fraction": "0.001",
                            "expires_after_bars": 4}},
        "exit": {"stop_loss_fraction": "0.01", "take_profit_fraction": "0.015",
                 "any_of": [{"left": {"candle": "close"}, "relation": "crosses_below",
                             "right": {"indicator": "average"}}]},
        "sizing": {"equity_fraction": "0.2"}})
    store.register(strategy)
    store.create_study(StudyPlan(study_id="okx-wave-1", strategy_id="okx-wave",
                                 tune=ReplayWindow(start=date(2026, 7, 2), end=date(2026, 7, 5)),
                                 confirm=ReplayWindow(start=date(2026, 7, 5), end=date(2026, 7, 7))))
    practice = PracticeRange(store, market, clock)
    record = practice.run(study_id="okx-wave-1", phase="tune", strategy_ref="okx-wave@1", symbol="SOL/USDT",
                          source="okx_public_candles", profiles=["okx-eea-spot"])[0]
    report = record.report
    assert record.cost_profile_ref == "okx-eea-spot@1"
    assert report["venue"] == {"data_venue": "okx", "cost_profile_venue": "okx", "stand_in": False,
                               "note": "The prices and the fees come from the same venue."}
    assert report["evidence"]["labels"] == ["synthetic_fixture"] and not report["evidence"]["real_market_data"]
    assert "USDT" in report["currency_note"]
    assert report["result"]["request"]["currency"] == "USDT"
    assert report["result"]["benchmark_data"]["hold_btc"]["status"] == "unavailable"  # no BTC/USDT from OKX here
    assert report["result"]["metrics"]["trades"] > 0
    assert all(fill["liquidity"] == "maker" for fill in report["result"]["fills"] if fill["purpose"] == "entry")
    assert practice.verify(record.run_id).reproduced
    state.close()
