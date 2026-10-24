"""The strategy lab in the installed subscription service (W11-B, C11; D292, D293).

``installed_strategy_lab`` builds the lab with its own read-only handle on the installation's candle file (never the
one the price watcher reads); ``run_subscription_operation`` runs it beside the price collector and the news check. At
the first start of an installation without a registered strategy it registers the two packaged examples, so the lab
has a start, and HQ's activity log shows ``strategy_lab`` with what ran and the seconds spent. Synthetic account and
owner folder; an empty candle file; no network, model, key, paid call or order.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from tests.day_plan_support import day_plan_account
from tests.integration.test_news_poll_in_service import _activity, _run, day  # noqa: F401 - the fixture
from tests.strategy_lab_support import register, study, submit, tune

from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.service_market_data import open_service_market_data
from trade_graph.application.strategy_lab.installation import installed_strategy_lab
from trade_graph.application.strategy_lab.queue import LabQueue


def _runtime(account, market=None) -> SimpleNamespace:
    return SimpleNamespace(database=account.database, clock=account.clock, portfolio_id=account.portfolio,
                           ledger=account.ledger, execution=account.execution, market_data=market)


def _lab_activity(account) -> list[dict]:
    return [json.loads(row[0]) for row in account.database.execute(
        "SELECT payload_json FROM activity_events WHERE kind = 'strategy_lab' ORDER BY rowid")]


def test_the_installed_lab_reads_the_candle_file_through_its_own_handle_and_registers_the_examples(tmp_path) -> None:
    account = day_plan_account(tmp_path, report=False)
    market = open_service_market_data(tmp_path / "market" / "market_data.sqlite", database=account.database,
                                      clock=account.clock, portfolio_ids=[account.portfolio])
    try:
        worker = installed_strategy_lab(_runtime(account, market))
        assert worker.reader is not None and worker.reader is not market.reader
        assert worker.candles.database is worker.reader and worker.account_profile == "kraken-spot-starting-tier"
        assert worker.step()["status"] == "idle"
        (started,) = _lab_activity(account)
        assert started["status"] == "started" and started["candle_file"] is True
        assert started["registered_starting"] == ["slow-trend-pullback@1", "range-reversion@1"]
        assert "the desk asks for their confirmations itself" in started["note"]
        worker.close()
        assert worker.reader is None
    finally:
        market.close()


def test_without_a_candle_file_the_lab_still_starts_and_says_replays_wait_for_one(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    worker = installed_strategy_lab(_runtime(account))
    assert worker.candles is None
    worker.step()
    (started,) = _lab_activity(account)
    assert started["candle_file"] is False and "no candle file" in started["note"]
    assert started["registered_starting"] == []  # slow-trend-pullback@1 is already registered here


def test_a_lab_that_cannot_be_prepared_is_off_with_the_reason_and_never_stops_the_start(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    worker = installed_strategy_lab(_runtime(account, SimpleNamespace(path=tmp_path / "missing.sqlite")))
    assert worker is None
    (refused,) = _lab_activity(account)
    assert refused["status"] == "not_ready" and "MarketDataUnavailable" in refused["reason"]


def test_the_installed_service_runs_the_lab_in_its_own_thread(day) -> None:  # noqa: F811 - the imported fixture
    flow = day.flow
    account = SimpleNamespace(database=flow.db, clock=flow.clock, portfolio=flow.pid)
    december = (date(2025, 12, 1), date(2025, 12, 15)), (date(2025, 12, 15), date(2025, 12, 31))
    submit(account, [register(), study(tune=december[0], confirm=december[1]), tune()], batch_id="before-start")
    result, database = _run(day, ticks=30)  # this installation's clock reads 2026-01-01
    try:
        assert result["strategy_lab_requests"] >= 3 and result["live_authorization"] is False
        queue = LabQueue(database, flow.clock)
        assert [record.status for record in queue.requests(batch_id="before-start")] == ["done"] * 3
        (tuned,) = PracticeRangeStore(database, flow.clock).runs(strategy_ref="cross-hundred@1")[:1]
        assert tuned.report["result"]["data"]["window_present"] == 0  # an empty candle file: reported, not invented
        lab = _activity(database, "strategy_lab")
        assert lab[0]["status"] == "started" and lab[0]["candle_file"] is True
        assert [item["kind"] for item in lab[1:4]] == ["register_strategy", "create_study", "tune"]
        assert all(item["status"] == "done" and item["seconds"] is not None for item in lab[1:4])
        assert database.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0] == 0
    finally:
        database.close()

