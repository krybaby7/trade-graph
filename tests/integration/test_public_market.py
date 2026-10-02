"""Scripted public-market coverage. This file does not call Kraken or Frankfurter."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.brokers.paper import PaperBroker
from trade_graph.adapters.market.normalize import normalize_ticker
from trade_graph.adapters.market.public import FrankfurterClient, KrakenPublicFeed, KrakenPublicRest, loads
from trade_graph.adapters.market.replay import PointInTimeMarket, quote_features
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import (
    AuthorityRecord,
    paper_mandate,
    paper_owner_policy,
    seed_paper_authority,
)
from trade_graph.application.execution import Execution
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, InstrumentRules, Quantity
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import StaleState, ValidationFailure
from trade_graph.domain.protocols import MarketData

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ASSET_URL = "https://api.kraken.com/0/public/AssetPairs?pair=XBTUSD"
TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
FX_URL = "https://api.frankfurter.dev/v2/providers/ecb/rate/usd/eur?date=2026-09-29"


class ScriptedTransport:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        return self.responses[url]


class ScriptedSession:
    def __init__(self, messages: list[str | None]) -> None:
        self.messages = list(messages)
        self.sent: list[str] = []

    def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    def recv_text(self) -> str | None:
        if not self.messages:
            return None
        return self.messages.pop(0)


def _ticker(timestamp: str, *, bid: str = "100.0", ask: str = "100.2") -> str:
    return json.dumps(
        {
            "channel": "ticker",
            "type": "update",
            "data": [
                {
                    "symbol": "BTC/USD",
                    "bid": bid,
                    "bid_qty": "2",
                    "ask": ask,
                    "ask_qty": "3",
                    "last": bid,
                    "volume": "10",
                    "timestamp": timestamp,
                }
            ],
        }
    )


def test_numeric_json_stays_decimal_and_float_objects_are_rejected() -> None:
    parsed = loads('{"bid": 100.25}')
    assert isinstance(parsed["bid"], Decimal)
    assert parsed["bid"] == Decimal("100.25")
    with pytest.raises(ValidationFailure):
        normalize_ticker(
            {"symbol": "BTC/USD", "bid": 0.1, "ask": 0.2},
            observation_id="bad",
            available_at=NOW,
        )


def test_rest_metadata_ticker_and_attributed_fx() -> None:
    transport = ScriptedTransport(
        {
            ASSET_URL: json.dumps(
                {
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "wsname": "XBT/USD",
                            "pair_decimals": 1,
                            "lot_decimals": 8,
                            "ordermin": "0.0001",
                            "costmin": "0.5",
                        }
                    },
                }
            ),
            TICKER_URL: json.dumps(
                {
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "a": ["100.2", "1", "1.5"],
                            "b": ["100.0", "1", "2.5"],
                            "c": ["100.1", "0.01"],
                            "v": ["4", "9"],
                        }
                    },
                }
            ),
            FX_URL: json.dumps(
                {"date": "2026-09-29", "base": "USD", "quote": "EUR", "rate": "0.90"}
            ),
        }
    )
    rest = KrakenPublicRest(transport)
    rules = rest.fetch_instruments(["XBTUSD"])
    assert rules[0].symbol == "BTC/USD"
    assert rules[0].price_increment == Decimal("0.1")
    ticker = rest.fetch_ticker("BTC/USD", observation_id="rest-1", available_at=NOW)
    assert ticker.bid == Decimal("100.0")
    assert ticker.ask == Decimal("100.2")
    assert ticker.ask_size == Decimal("1.5")
    assert ticker.source == "kraken_public_rest"
    rate = FrankfurterClient(transport).reference_rate("USD", "EUR", on="2026-09-29")
    assert rate.rate == Decimal("0.90")
    assert rate.source == "frankfurter:ECB:2026-09-29"
    assert rate.provider == "ECB"


@pytest.mark.parametrize("change", [
    {"date": None}, {"date": ""}, {"date": "20260929"}, {"date": "2026-09-31"},
    {"date": "2026-09-29T00:00:00Z"}, {"base": None}, {"quote": None},
    {"base": "EUR", "quote": "USD"}, {"base": "GBP"}, {"date": "2026-09-30"},
])
def test_reference_fx_refuses_missing_or_mismatched_wire_provenance(change) -> None:
    payload = {"date": "2026-09-29", "base": "USD", "quote": "EUR", "rate": "0.90"} | change
    transport = ScriptedTransport({FX_URL: json.dumps(payload)})
    with pytest.raises(ValidationFailure):
        FrankfurterClient(transport).reference_rate("USD", "EUR", on="2026-09-29")
    assert transport.calls == [FX_URL]


def test_reference_fx_retains_carried_historical_date_and_decimal_rate() -> None:
    transport = ScriptedTransport({FX_URL: '{"date":"2026-09-25","base":"usd","quote":"eur","rate":0.9}'})
    rate = FrankfurterClient(transport).reference_rate("USD", "EUR", on="2026-09-29")
    assert rate.rate == Decimal("0.9")
    assert rate.base == "USD" and rate.quote == "EUR"
    assert rate.rate_date == "2026-09-25"
    assert rate.source == "frankfurter:ECB:2026-09-25"


@pytest.mark.parametrize("arguments_override", [
    {"base": "USD/other"}, {"quote": "EUR?date=2026-09-30"}, {"on": "20260929"},
    {"on": "2026-09-31"}, {"base": None},
])
def test_reference_fx_refuses_invalid_request_before_transport(arguments_override) -> None:
    transport = ScriptedTransport({})
    arguments = {"base": "USD", "quote": "EUR", "on": "2026-09-29"} | arguments_override
    with pytest.raises(ValidationFailure):
        FrankfurterClient(transport).reference_rate(**arguments)
    assert not transport.calls


def test_reconnect_backfills_and_out_of_order_quotes_do_not_refresh(tmp_path) -> None:
    clock = FrozenClock(NOW)
    transport = ScriptedTransport(
        {
            TICKER_URL: json.dumps(
                {
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "a": ["101", "1", "1"],
                            "b": ["100", "1", "1"],
                            "c": ["100.5", "1"],
                            "v": ["1", "2"],
                        }
                    },
                }
            )
        }
    )
    fresh = _ticker("2026-01-01T00:00:00Z")
    older = _ticker("2025-12-31T23:00:00Z", bid="90", ask="91")
    live = ScriptedSession([fresh, None])
    reconnected = ScriptedSession([older])
    sessions = [live, reconnected]
    feed = KrakenPublicFeed(lambda: sessions.pop(0), KrakenPublicRest(transport), clock, ["BTC/USD"])
    assert feed.blocks_increase("BTC/USD")
    first = feed.poll()
    assert first[0].bid == Decimal("100.0")
    assert feed.blocks_increase("BTC/USD") is False
    backfill = feed.poll()
    assert "disconnect" in feed.gaps
    assert "backfill:BTC/USD" in feed.gaps
    assert backfill[0].source == "kraken_public_rest"
    assert TICKER_URL in transport.calls
    assert feed.blocks_increase("BTC/USD") is False
    assert json.loads(reconnected.sent[0])["params"]["channel"] == "ticker"
    ignored = feed.poll()
    assert ignored == []
    assert "out_of_order:BTC/USD" in feed.gaps
    assert feed.latest("BTC/USD").source == "kraken_public_rest"

    database = Database(tmp_path / "market.sqlite")
    ledger = Ledger(database, clock)
    execution = Execution(database, ledger, clock, PaperBroker(database, clock), venue="kraken")
    execution.register_instrument(
        InstrumentRules(
            venue="kraken",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            price_increment="0.1",
            quantity_increment="0.00000001",
            min_quantity="0.0001",
            min_notional="1",
        )
    )
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    authority = AuthorityRecord(database, clock)
    authority.install_policy(paper_owner_policy(venues=["kraken"]), role="owner")
    authority.install_mandate(paper_mandate(portfolio), role="owner")
    execution.save_observation(feed.latest("BTC/USD"))
    clock.advance(60)
    with pytest.raises(StaleState):
        execution.authorize(portfolio, _decision(clock, portfolio))


def test_normalized_public_book_drives_paper_partial_fill(tmp_path) -> None:
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "fill.sqlite")
    ledger = Ledger(database, clock)
    broker = PaperBroker(database, clock, participation=Decimal("1"))
    execution = Execution(database, ledger, clock, broker)
    execution.register_instrument(
        InstrumentRules(
            venue="paper",
            symbol="BTC/USD",
            base_asset="BTC",
            quote_asset="USD",
            price_increment="0.1",
            quantity_increment="0.00000001",
            min_quantity="0.0001",
            min_notional="1",
            synthetic=True,
        )
    )
    portfolio = ledger.create_portfolio(reporting_currency="USD")
    ledger.deposit(portfolio, "USD", Decimal("10000"), "open")
    ledger.observe_fx(base="USD", quote="USD", rate=Decimal("1"), source="identity", kind="identity", stale=False)
    seed_paper_authority(database, clock, portfolio)
    execution.save_observation(
        normalize_ticker(
            {"symbol": "BTC/USD", "bid": "100", "ask": "101", "bid_qty": "1", "ask_qty": "1"},
            observation_id="seed",
            available_at=clock.now(),
        ).model_copy(update={"venue": "paper"})
    )
    intent = execution.authorize(portfolio, _decision(clock, portfolio))
    asyncio.run(execution.dispatch())
    clock.advance(1)
    transport = ScriptedTransport(
        {
            TICKER_URL: json.dumps(
                {
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "a": ["101", "1", "0.004"],
                            "b": ["100", "1", "1"],
                            "c": ["100.5", "0.01"],
                            "v": ["1", "2"],
                        }
                    },
                }
            )
        }
    )
    public = KrakenPublicRest(transport).fetch_ticker("BTC/USD", observation_id="book", available_at=clock.now())
    execution.on_observation(public.model_copy(update={"symbol": "BTC/USD"}))
    assert execution.intent_state(intent) == "PARTIALLY_FILLED"
    assert execution.owned_quantity(portfolio, "BTC") == Decimal("0.004")


def test_replay_hides_future_observations() -> None:
    early = normalize_ticker(
        {"symbol": "BTC/USD", "bid": "10", "ask": "12", "timestamp": "2026-01-01T00:00:00Z"},
        observation_id="early",
        available_at=NOW,
        event_time=NOW,
    )
    late_time = NOW + timedelta(days=1)
    late = normalize_ticker(
        {"symbol": "BTC/USD", "bid": "30", "ask": "32"},
        observation_id="late",
        available_at=late_time,
        event_time=late_time,
    )
    market = PointInTimeMarket(
        [
            InstrumentRules(
                venue="replay",
                symbol="BTC/USD",
                base_asset="BTC",
                quote_asset="USD",
                price_increment="0.1",
                quantity_increment="0.00000001",
                min_quantity="0.0001",
                min_notional="1",
            )
        ],
        [early, late],
    )
    assert isinstance(market, MarketData)
    visible = market.visible("BTC/USD", NOW)
    assert [item.observation_id for item in visible] == ["early"]
    assert quote_features(visible)["mid"] == "11"
    assert quote_features(market.visible("BTC/USD", late_time))["mid"] == "31"


def _decision(clock: FrozenClock, portfolio: str) -> Decision:
    return Decision(
        record_id="m1",
        created_at_utc=clock.now(),
        run_id="run",
        task_id="task",
        root_task_id="root",
        portfolio_id=portfolio,
        mode="paper",
        system_version_id="v1",
        trace_id="trace",
        action="enter",
        symbol="BTC/USD",
        quantity=Quantity(amount="0.01", asset="BTC"),
        rationale="public book",
        invalidation="gap",
        horizon_seconds=3600,
        strategy_id="slow-trend",
        snapshot_id="snap",
        mandate_revision="1",
        policy_revision="1",
    )
