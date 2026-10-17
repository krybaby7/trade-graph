"""What an installation runs (W6-D, D222 to D224): the candle file beside the database, the price collector in the
service, the hourly features on the candle store, and the event engine only when the owner's configuration says so.

Synthetic account and a scripted Kraken (``tests/integration/test_price_collector.Exchange``); no network, no model.
"""

from __future__ import annotations

import asyncio
import stat
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tests.candle_support import AdvancingSleeper
from tests.integration.test_price_collector import Exchange
from tests.integration.test_subscription_operation import operating_flow  # noqa: F401
from tests.integration.test_subscription_operation import run as run_operation

from trade_graph.adapters.market.rate_limit import KrakenRestLimiter
from trade_graph.application import subscription_operation as operation
from trade_graph.application.market_context import market_context
from trade_graph.application.paper_service import PaperService
from trade_graph.application.price_collector import CollectorTransports, PriceCollector
from trade_graph.application.price_history import CandleStoreHistory
from trade_graph.application.service_market_data import market_database_beside, open_service_market_data
from trade_graph.cli import main
from trade_graph.domain.clock import FrozenClock
from trade_graph.paper_runtime import PaperRuntimeConfig, PriceCollectorConfig, assemble_paper_runtime

NOW = datetime(2026, 10, 17, 9, 5, 40, tzinfo=UTC)


def test_the_engine_ships_off_and_the_collector_on_and_the_engine_needs_the_collector() -> None:
    config = PaperRuntimeConfig()
    assert config.event_engine is False and config.price_collector.enabled is True
    assert PaperRuntimeConfig.model_validate_json("{}") == config  # an older owner file keeps every default
    assert PaperRuntimeConfig(public_data_enabled=True, event_engine=True).event_engine
    for bad in ({"event_engine": True},
                {"event_engine": True, "public_data_enabled": True, "price_collector": {"enabled": False}}):
        with pytest.raises(ValidationError, match="price collector"):
            PaperRuntimeConfig.model_validate(bad)
    with pytest.raises(ValidationError):
        PaperRuntimeConfig.model_validate({"price_collector": {"chunk_requests": 0}})
    assert PriceCollectorConfig(ranking_every_hours=2).settings().ranking_every_hours == 2


def _account(tmp_path, capsys, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "trade_graph.sqlite"
    clock = FrozenClock(NOW)
    monkeypatch.setattr("trade_graph.domain.clock.SystemClock", lambda: clock)
    assert main(["init", "--database", str(path)]) == 0
    capsys.readouterr()
    return path, clock


def test_an_installation_with_public_data_keeps_its_candle_file_beside_the_database(tmp_path, capsys,
                                                                                   monkeypatch) -> None:
    path, clock = _account(tmp_path, capsys, monkeypatch)
    exchange = Exchange(clock)
    transports = CollectorTransports(catalog=exchange, ohlc=exchange, ticker=exchange)
    runtime = assemble_paper_runtime(path, config=PaperRuntimeConfig(public_data_enabled=True), clock=clock,
                                     market_database=market_database_beside(path), market_transports=transports,
                                     market_limiter=KrakenRestLimiter(clock, AdvancingSleeper(clock)),
                                     market_evidence_label="synthetic_fixture")
    try:
        candle_file = path.parent / "market_data.sqlite"
        assert runtime.market_data.path == candle_file and candle_file.is_file()
        assert stat.S_IMODE(candle_file.stat().st_mode) == 0o600
        assert isinstance(runtime.price_collector, PriceCollector)
        assert isinstance(runtime.execution.hourly_history, CandleStoreHistory)
        assert runtime.execution.hourly_history is runtime.market_data.history
        for _ in range(4):
            runtime.price_collector.step()
        assert ("BTC/USD", 60) in exchange.ohlc_requests()
        guard = {"mandate": {"symbols": ["BTC/USD"], "strategy_ids": ["t"], "max_quote_age_seconds": 60},
                 "policy": {"maximum_quote_age_seconds": 60}}
        market = market_context(runtime.execution, guard, {"t": {"features": ["sma_20"]}}, as_of=clock.now())
        history = market["BTC/USD"]["history"]
        # The collector stored synthetic-labelled candles; the features read them through the read-only handle.
        assert history["history_store"] == "candle_store"
        events = runtime.database.execute(
            "SELECT COUNT(*) FROM activity_events WHERE kind = 'price_collection'").fetchone()[0]
        assert events >= 1  # the pair list refresh at least
    finally:
        runtime.market_data.close()
        runtime.database.close()


def test_without_public_data_or_with_the_collector_off_nothing_is_opened(tmp_path, capsys, monkeypatch) -> None:
    path, clock = _account(tmp_path, capsys, monkeypatch)
    for config in (PaperRuntimeConfig(),
                   PaperRuntimeConfig(public_data_enabled=True, price_collector={"enabled": False})):
        runtime = assemble_paper_runtime(path, config=config, clock=clock,
                                         market_database=market_database_beside(path))
        try:
            assert runtime.market_data is None and runtime.price_collector is None
            assert runtime.execution.hourly_history is None
        finally:
            runtime.database.close()
    assert not (path.parent / "market_data.sqlite").exists()


def test_the_service_runs_the_installed_collector_in_its_own_thread(tmp_path, capsys, monkeypatch) -> None:
    path, clock = _account(tmp_path, capsys, monkeypatch)
    exchange = Exchange(clock)
    transports = CollectorTransports(catalog=exchange, ohlc=exchange, ticker=exchange)
    runtime = assemble_paper_runtime(path, config=PaperRuntimeConfig(public_data_enabled=True), clock=clock,
                                     market_database=market_database_beside(path), market_transports=transports,
                                     market_limiter=KrakenRestLimiter(clock, AdvancingSleeper(clock)),
                                     market_evidence_label="synthetic_fixture")
    service = PaperService(runtime.database, runtime.execution, clock=clock, portfolio_ids=[runtime.portfolio_id],
                           handlers={}, tick_interval_seconds=0.01, price_collector=runtime.price_collector)

    async def scenario():
        for _ in range(30):
            result = await service.tick()
            assert not [item for item in result.failures if item.startswith("collector")], result.failures
            if exchange.ohlc_requests():
                break
            await asyncio.sleep(0.02)
        await service.stop()

    try:
        asyncio.run(scenario())
    finally:
        runtime.market_data.close()
        runtime.database.close()
    assert exchange.log and exchange.log[0][0] == "catalog"


def test_the_subscription_operation_opens_the_candle_file_beside_its_database_and_runs_the_engine_when_on(
        operating_flow, tmp_path) -> None:  # noqa: F811
    h = operating_flow
    seen = {}
    config = PaperRuntimeConfig(tick_interval_seconds=0.01, schedule_intervals={}, public_data_enabled=True,
                                event_engine=True)
    h.runtime.config = config
    database_path = h.flow.db.path
    market = open_service_market_data(tmp_path / "market" / "market_data.sqlite", database=h.flow.db,
                                      clock=h.flow.clock, portfolio_ids=[h.flow.pid])
    h.runtime.market_data, h.runtime.price_collector = market, None

    def assemble(_path, **kwargs):
        seen.update(kwargs)
        return h.runtime

    original = operation.assemble_paper_runtime
    operation.assemble_paper_runtime = assemble
    try:
        result = run_operation(h, ticks=3)
    finally:
        operation.assemble_paper_runtime = original
    assert seen["market_database"] == database_path.parent / "market_data.sqlite"
    assert result["event_engine"] is True and result["price_collection"] is None
    assert h.database.execute("SELECT COUNT(*) FROM day_cycle_steps").fetchone()[0] >= 1
    h.database.close()


def test_the_subscription_operation_keeps_its_fixed_timers_while_the_engine_is_off(
        operating_flow) -> None:  # noqa: F811
    h = operating_flow
    result = run_operation(h, ticks=2)
    assert result["event_engine"] is False
    assert h.database.execute("SELECT COUNT(*) FROM day_cycle_steps").fetchone()[0] == 0
    h.database.close()


def test_with_the_collector_the_day_plan_wake_waits_for_the_closed_hour_and_an_event_wake_does_not(tmp_path) -> None:
    from types import SimpleNamespace

    from tests.integration.test_execution import _stack

    from trade_graph.adapters.persistence.market_data import MarketDataDatabase
    from trade_graph.adapters.persistence.market_data_readonly import ReadOnlyMarketData
    from trade_graph.application.candle_store import CandleStore
    from trade_graph.application.subscription_operation import _NormalSubscriptionService

    clock, ledger, execution, _broker, pid = _stack(tmp_path)
    clock.advance(3600 - clock.now().minute * 60 + 60)  # one minute after a whole hour, nothing stored yet
    writer = MarketDataDatabase(tmp_path / "market" / "market_data.sqlite")
    reader = ReadOnlyMarketData(writer.path)
    try:
        service = _NormalSubscriptionService(ledger.database, execution, clock=clock, portfolio_ids=[pid],
                                             handlers={}, candle_history=CandleStoreHistory(CandleStore(reader)),
                                             history_symbols=("BTC/USD",))
        lease = SimpleNamespace(reclaimed=False)

        def row(objective: str) -> dict:
            return {"role": "trader", "attempts_used": 0, "objective": objective, "portfolio_id": pid,
                    "output_json": None}

        mandate = execution.authority.active_mandate(pid)
        assert "BTC/USD" in mandate.symbols
        waits = {objective: service._input_wait(lease, row(objective))
                 for objective in ("wake:day_plan", "wake:check_in", "wake:event", "trader-review")}
    finally:
        reader.close()
        writer.close()
    assert waits["wake:day_plan"] is not None and waits["wake:check_in"] is not None
    assert waits["trader-review"] is not None  # the fixed timer's decision, as before
    assert waits["wake:event"] is None


def test_with_the_engine_on_the_feed_also_quotes_the_days_coins_the_owners_scope_covers(tmp_path, capsys,
                                                                                         monkeypatch) -> None:
    from tests.alarm_support import quote_request

    from trade_graph.application.alarms.book import AlarmBook

    path, clock = _account(tmp_path, capsys, monkeypatch)
    exchange = Exchange(clock)
    transports = CollectorTransports(catalog=exchange, ohlc=exchange, ticker=exchange)
    for engine in (False, True):
        runtime = assemble_paper_runtime(
            path, config=PaperRuntimeConfig(public_data_enabled=True, event_engine=engine), clock=clock,
            market_database=market_database_beside(path), market_transports=transports,
            market_limiter=KrakenRestLimiter(clock, AdvancingSleeper(clock)), market_evidence_label="synthetic_fixture")
        try:
            AlarmBook(runtime.database, clock).create(quote_request("150", symbol="SOL/USD"))
            provider = runtime.public_feed.extra_symbols
            if engine:
                policy = runtime.execution.authority.active_policy()
                from trade_graph.kernel.authority import owner_scope_covers

                assert provider() == (["SOL/USD"] if owner_scope_covers(policy, "SOL/USD") else [])
            else:
                assert provider is None  # the default installation quotes only its configured coins
        finally:
            runtime.market_data.close()
            runtime.database.close()
