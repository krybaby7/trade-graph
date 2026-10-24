"""Point-in-time market view. Later availability is not visible early."""

from __future__ import annotations

from datetime import datetime

from trade_graph.adapters.market.normalize import normalize_ticker
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal


class PointInTimeMarket:
    venue = "replay"

    def __init__(self, rules: list[InstrumentRules], observations: list[Observation]) -> None:
        self.rules = list(rules)
        self.observations = list(observations)

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


def quote_features(observations: list[Observation]) -> dict[str, str]:
    if not observations:
        raise ValidationFailure("features require a visible observation")
    last = max(observations, key=lambda item: (item.available_at_utc, item.event_time_utc, item.observation_id))
    if last.bid is None or last.ask is None:
        raise ValidationFailure("features require a two-sided quote")
    mid = (last.bid + last.ask) / 2
    spread = last.ask - last.bid
    return {
        "bid": canonical_decimal(last.bid),
        "ask": canonical_decimal(last.ask),
        "mid": canonical_decimal(mid),
        "spread": canonical_decimal(spread),
        "observation_id": last.observation_id,
    }


def ticker_from_text(text: str, *, observation_id: str, available_at: datetime) -> Observation:
    from trade_graph.adapters.market.public import loads

    message = loads(text)
    data = message["data"][0] if isinstance(message, dict) and message.get("data") else message
    event_time = None
    timestamp = data.get("timestamp") if isinstance(data, dict) else None
    if isinstance(timestamp, str):
        event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return normalize_ticker(
        data,
        observation_id=observation_id,
        available_at=available_at,
        event_time=event_time,
    )
