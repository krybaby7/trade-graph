"""Broker and inference protocols are structural and stay outside vendor SDKs."""

from datetime import UTC, datetime
from typing import get_type_hints

from trade_graph.adapters.brokers.kraken_live import KrakenLiveBroker
from trade_graph.adapters.brokers.paper import DropAckBroker, PaperBroker
from trade_graph.adapters.models.providers import AnthropicAdapter, OpenAIAdapter, ScriptedAdapter
from trade_graph.application.execution import Execution
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.protocols import Broker, InferenceAdapter, MarketData


class _Replay:
    venue = "replay"

    def __init__(self) -> None:
        self.rules = [
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
        ]
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 1, 2, tzinfo=UTC)
        self.observations = [
            Observation(
                observation_id="early",
                venue="replay",
                symbol="BTC/USD",
                event_time_utc=early,
                available_at_utc=early,
                bid="1",
                ask="2",
                kind="quote",
                source="fixture",
            ),
            Observation(
                observation_id="late",
                venue="replay",
                symbol="BTC/USD",
                event_time_utc=late,
                available_at_utc=late,
                bid="3",
                ask="4",
                kind="quote",
                source="fixture",
            ),
        ]

    async def instrument_rules(self) -> list[InstrumentRules]:
        return list(self.rules)

    async def poll(self) -> list[Observation]:
        return list(self.observations)

    def visible(self, symbol: str, as_of: datetime) -> list[Observation]:
        return [
            item
            for item in self.observations
            if item.symbol == symbol and item.event_time_utc <= as_of and item.available_at_utc <= as_of
        ]


def _implements(cls: type, protocol: type) -> bool:
    return all(hasattr(cls, name) for name in protocol.__protocol_attrs__)  # type: ignore[attr-defined]


def test_brokers_and_adapters_match_protocols() -> None:
    assert _implements(PaperBroker, Broker)
    assert _implements(DropAckBroker, Broker)
    assert _implements(KrakenLiveBroker, Broker)
    assert isinstance(OpenAIAdapter(), InferenceAdapter)
    assert isinstance(AnthropicAdapter(), InferenceAdapter)
    assert isinstance(ScriptedAdapter(), InferenceAdapter)
    hints = get_type_hints(Execution.__init__)
    assert hints["broker"] is Broker


def test_market_protocol_hides_future_observations() -> None:
    market = _Replay()
    assert isinstance(market, MarketData)
    visible = market.visible("BTC/USD", datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert [item.observation_id for item in visible] == ["early"]
