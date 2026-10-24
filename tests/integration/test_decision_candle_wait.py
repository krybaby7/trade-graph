"""A fresh Trader decision waits, boundedly, for the hour that just closed and says plainly if it is missing."""

import asyncio
import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from tests.integration.test_execution import _stack
from tests.integration.test_history_contexts import HistoryFlow, _candles, _save
from tests.integration.test_hourly_history_refresh import history_flow
from tests.integration.test_subscription_operation import operating_flow  # noqa: F401
from tests.integration.test_subscription_operation import run as run_operation

from trade_graph.application import subscription_operation as operation
from trade_graph.application.market_context import market_context
from trade_graph.application.price_history import HistoryStore
from trade_graph.application.subscription_operation import _NormalSubscriptionService
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import utc_iso
from trade_graph.paper_runtime import PaperRuntimeConfig

GUARD = {"mandate": {"symbols": ["BTC/USD"], "strategy_ids": ["t"], "max_quote_age_seconds": 60},
         "policy": {"maximum_quote_age_seconds": 60}}
TEMPLATES = {"t": {"features": ["hourly_return"]}}
BOTH = {"BTC/USD", "ETH/USD"}


def _own_wait(database, task_id):
    row = database.execute("SELECT output_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return json.loads(row["output_json"] or "{}").get("_candle_wait")


def _task(database, task_id):
    return database.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()


def _retained_market(database, task_id):
    row = database.execute("SELECT payload_json FROM snapshots WHERE json_extract(payload_json, '$.task_id') = ?",
                           (task_id,)).fetchone()
    return json.loads(row["payload_json"])["market"]


def _recording_trader(database, clock):
    seen = []

    def trader(task):
        seen.append((utc_iso(clock.now()), HistoryStore(database).snapshot("BTC/USD", clock.now(), ["hourly_return"]),
                     _own_wait(database, task["task_id"])))
        return {}

    return trader, seen


class _ContextTrader:
    """Builds the shared market context exactly as the Trader's snapshot does."""

    def __init__(self, execution, clock):
        self.execution, self.clock, self.seen = execution, clock, []

    def context(self, task):
        return {"market": market_context(self.execution, GUARD, TEMPLATES, as_of=self.clock.now(),
                                         candle_wait=_own_wait(self.execution.database, task["task_id"]))}

    def __call__(self, task):
        self.seen.append(utc_iso(self.clock.now()))
        return {}


def _service(ledger, execution, clock, history, handler, **kwargs):
    return _NormalSubscriptionService(ledger.database, execution, clock=clock, public_feed=history,
                                      handlers={"trader": handler}, schedule_intervals={},
                                      tick_interval_seconds=0.01, **kwargs)


def test_trader_woken_inside_krakens_commit_lag_waits_for_the_closed_hour(tmp_path):
    clock, ledger, execution, pid, history = history_flow(tmp_path)
    history.poll()
    trader, seen = _recording_trader(ledger.database, clock)
    service = _service(ledger, execution, clock, history, trader)

    async def scenario():
        await service.start()
        try:
            clock.advance(53 * 60 + 5)
            history.transport.lagging_symbols = set(BOTH)
            task_id = service.scheduler.add_task(role="trader", objective="trader-review", portfolio_id=pid)
            await service.tick(wait_roles=True)
            assert seen == []
            row = _task(ledger.database, task_id)
            assert (row["status"], row["attempts_used"]) == ("QUEUED", 0)
            assert _own_wait(ledger.database, task_id)["expected_close_time_utc"] == "2026-01-01T01:00:00.000000Z"
            history.transport.lagging_symbols.clear()
            clock.advance(40)
            await service.tick(wait_roles=True)
        finally:
            await service.stop()
        return task_id

    task_id = asyncio.run(scenario())
    assert len(seen) == 1
    assert seen[0][1]["latest_close_time_utc"] == "2026-01-01T01:00:00.000000Z"
    assert seen[0][1]["stale"] is False
    row = _task(ledger.database, task_id)
    assert (row["status"], row["attempts_used"]) == ("SUCCEEDED", 1)
    assert ledger.database.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_trader_proceeds_at_the_bounded_deadline_when_the_hour_stays_missing(tmp_path):
    clock, ledger, execution, pid, history = history_flow(tmp_path)
    history.poll()
    trader, seen = _recording_trader(ledger.database, clock)
    service = _service(ledger, execution, clock, history, trader)

    async def scenario():
        await service.start()
        try:
            clock.advance(53 * 60 + 5)
            history.transport.lagging_symbols = set(BOTH)
            task_id = service.scheduler.add_task(role="trader", objective="trader-review", portfolio_id=pid)
            await service.tick(wait_roles=True)
            for _ in range(80):
                if seen:
                    break
                clock.advance(5)
                await service.tick(wait_roles=True)
        finally:
            await service.stop()
        return task_id

    task_id = asyncio.run(scenario())
    assert len(seen) == 1
    at, snapshot, wait = seen[0]
    assert at == "2026-01-01T01:05:00.000000Z"
    assert snapshot["stale"] is True
    assert snapshot["latest_close_time_utc"] == "2026-01-01T00:00:00.000000Z"
    assert wait == {"expected_close_time_utc": "2026-01-01T01:00:00.000000Z",
                    "waiting_since_utc": "2026-01-01T01:00:05.000000Z",
                    "wait_deadline_utc": "2026-01-01T01:05:00.000000Z",
                    "missing_symbols": ["BTC/USD", "ETH/USD"]}
    assert _task(ledger.database, task_id)["attempts_used"] == 1
    assert ledger.database.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_started_trader_task_and_research_are_never_held(tmp_path):
    clock, ledger, execution, pid, history = history_flow(tmp_path)
    history.poll()
    trader, seen = _recording_trader(ledger.database, clock)
    research = []
    service = _NormalSubscriptionService(ledger.database, execution, clock=clock, public_feed=history,
                                         handlers={"trader": trader, "research": lambda _task: research.append(
                                             utc_iso(clock.now())) or {}},
                                         schedule_intervals={}, tick_interval_seconds=0.01)

    async def scenario():
        await service.start()
        try:
            clock.advance(53 * 60 + 5)
            history.transport.lagging_symbols = set(BOTH)
            started = service.scheduler.add_task(role="trader", objective="trader-review", portfolio_id=pid)
            # A started task keeps its recovery path and is not given a fresh wait.
            ledger.database.execute("UPDATE tasks SET attempts_used = 1 WHERE task_id = ?", (started,))
            service.scheduler.add_task(role="research", objective="research-review", portfolio_id=pid)
            await service.tick(wait_roles=True)
            await service.tick(wait_roles=True)
        finally:
            await service.stop()

    asyncio.run(scenario())
    assert [at for at, _snapshot, _wait in seen] == ["2026-01-01T01:00:05.000000Z"]
    assert research == ["2026-01-01T01:00:05.000000Z"]


def test_first_run_timing_turn_is_not_held_past_the_window_and_says_missing(tmp_path):
    clock, ledger, execution, pid, history = history_flow(tmp_path)
    history.poll()
    trader = _ContextTrader(execution, clock)
    service = _service(ledger, execution, clock, history, trader)

    async def scenario():
        await service.start()
        try:
            # The first paper run: the Trader woke at :13 while the candle arrived at :40.
            clock.advance(66 * 60)
            history.transport.lagging_symbols = set(BOTH)
            first = service.scheduler.add_task(role="trader", objective="trader-review", portfolio_id=pid)
            await service.tick(wait_roles=True)
            assert trader.seen == ["2026-01-01T01:13:00.000000Z"]
            clock.advance(27 * 60)
            history.transport.lagging_symbols.clear()
            await service.tick(wait_roles=True)
            clock.advance(60)
            second = service.scheduler.add_task(role="trader", objective="trader-review", portfolio_id=pid)
            await service.tick(wait_roles=True)
        finally:
            await service.stop()
        return first, second

    first, second = asyncio.run(scenario())
    assert trader.seen == ["2026-01-01T01:13:00.000000Z", "2026-01-01T01:41:00.000000Z"]
    missing = _retained_market(ledger.database, first)["BTC/USD"]["history"]["expected_candle"]
    assert missing["status"] == "missing"
    assert missing["expected_close_time_utc"] == "2026-01-01T01:00:00.000000Z"
    assert missing["latest_stored_close_time_utc"] == "2026-01-01T00:00:00.000000Z"
    assert missing["waited_seconds"] == 0 and missing["wait_deadline_utc"] is None
    assert "01:00 UTC" in missing["note"] and "00:00 UTC" in missing["note"]
    present = _retained_market(ledger.database, second)["BTC/USD"]["history"]["expected_candle"]
    assert present["status"] == "present"
    assert present["latest_stored_close_time_utc"] == "2026-01-01T01:00:00.000000Z"


def _candle(close_time, available_at, close="100"):
    price = Decimal(close)
    marker = f"{utc_iso(close_time)}:{close}"
    return HourlyCandle(
        symbol="BTC/USD", open_time_utc=close_time - timedelta(hours=1), close_time_utc=close_time,
        available_at_utc=available_at, open=price, high=price + 1, low=price - 1, close=price, vwap=price,
        volume=Decimal("0.125"), trade_count=7, source="kraken_public_ohlc",
        source_ref=f"urn:synthetic-test-ohlc:{marker}", source_hash=hashlib.sha256(marker.encode()).hexdigest())


def test_snapshot_states_whether_the_expected_candle_is_present(tmp_path):
    clock, ledger, execution, _broker, _pid = _stack(tmp_path)
    start = clock.now()
    clock.advance(3 * 3600 + 5 * 60)
    store = HistoryStore(ledger.database)
    store.save([_candle(start + timedelta(hours=hour), start + timedelta(hours=hour, minutes=3)) for hour in (1, 2)])

    def expected(candle_wait=None):
        market = market_context(execution, GUARD, TEMPLATES, as_of=clock.now(), candle_wait=candle_wait)
        return market["BTC/USD"]["history"]["expected_candle"]

    plain = expected()
    assert {key: value for key, value in plain.items() if key != "note"} == {
        "status": "missing", "expected_close_time_utc": "2026-01-01T03:00:00.000000Z",
        "latest_stored_close_time_utc": "2026-01-01T02:00:00.000000Z", "waited_seconds": 0,
        "wait_deadline_utc": None}
    waited = expected({"expected_close_time_utc": "2026-01-01T03:00:00.000000Z",
                       "waiting_since_utc": "2026-01-01T03:00:05.000000Z",
                       "wait_deadline_utc": "2026-01-01T03:05:00.000000Z", "missing_symbols": ["BTC/USD"]})
    assert waited["status"] == "missing_after_wait"
    assert waited["waited_seconds"] == 295
    assert waited["wait_deadline_utc"] == "2026-01-01T03:05:00.000000Z"
    assert "03:00 UTC" in waited["note"] and "02:00 UTC" in waited["note"] and "295 seconds" in waited["note"]
    other_hour = expected({"expected_close_time_utc": "2026-01-01T02:00:00.000000Z",
                           "waiting_since_utc": "2026-01-01T02:00:05.000000Z",
                           "wait_deadline_utc": "2026-01-01T02:05:00.000000Z", "missing_symbols": ["BTC/USD"]})
    assert (other_hour["status"], other_hour["waited_seconds"], other_hour["wait_deadline_utc"]) == (
        "missing", 0, None)
    store.save([_candle(start + timedelta(hours=3), start + timedelta(hours=3, minutes=6))])
    assert expected()["status"] == "missing"
    store.save([_candle(start + timedelta(hours=3), start + timedelta(hours=3, minutes=3), close="101")])
    present = expected()
    assert present["status"] == "present"
    assert present["latest_stored_close_time_utc"] == "2026-01-01T03:00:00.000000Z"


def test_real_trader_context_carries_the_wait_record(tmp_path):
    flow = HistoryFlow(tmp_path)
    try:
        _save(flow, _candles(flow, omit=49))
        task_id = flow.office.scheduler.add_task(role="trader", objective="Inspect completed history.",
                                                portfolio_id=flow.pid, payload={"evidence_refs": [flow.ref]},
                                                max_attempts=1, allocated_spend=Decimal("1"))
        flow.db.execute("UPDATE tasks SET output_json = ? WHERE task_id = ?", (json.dumps({
            "reason": "awaiting completed hourly candle", "_candle_wait": {
                "expected_close_time_utc": "2026-01-03T12:00:00.000000Z",
                "waiting_since_utc": "2026-01-03T12:00:05.000000Z",
                "wait_deadline_utc": "2026-01-03T12:05:00.000000Z", "missing_symbols": ["BTC/USD"]}}), task_id))
        assert flow.worker.run_available({"trader": flow.assembly.handlers["trader"]}) == 1
        assert _task(flow.db, task_id)["status"] == "SUCCEEDED"
        request, snapshot = flow.retained(task_id)
        for market in (request.context["market"], snapshot["market"]):
            status = market["BTC/USD"]["history"]["expected_candle"]
            assert status["status"] == "missing_after_wait"
            assert status["waited_seconds"] == 1795
    finally:
        flow.db.close()


def test_decision_wait_is_owner_configurable_within_the_hour():
    assert PaperRuntimeConfig().decision_candle_wait_seconds == 300
    assert PaperRuntimeConfig(decision_candle_wait_seconds=0).decision_candle_wait_seconds == 0
    for value in (-1, 3001):
        with pytest.raises(ValueError):
            PaperRuntimeConfig(decision_candle_wait_seconds=value)


def test_operation_passes_the_configured_wait_to_the_service(request, monkeypatch):
    h = request.getfixturevalue("operating_flow")
    h.runtime.config = h.runtime.config.model_copy(update={"decision_candle_wait_seconds": 0})
    seen = []
    original = operation._NormalSubscriptionService.__init__

    def recorded(self, *args, **kwargs):
        original(self, *args, **kwargs)
        seen.append(self.candle_wait_seconds)

    monkeypatch.setattr(operation._NormalSubscriptionService, "__init__", recorded)
    run_operation(h, ticks=1)
    h.database.close()
    assert seen == [0]
