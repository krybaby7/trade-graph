"""Kraken public metadata and ticker normalization. No private trading key."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import parse_decimal

_ASSET = {"XBT": "BTC", "XXBT": "BTC", "ETH": "ETH", "XETH": "ETH", "ZUSD": "USD", "USD": "USD"}


def normalize_pair(name: str, info: dict) -> InstrumentRules:
    wsname = str(info.get("wsname") or name)
    base, quote = wsname.split("/")
    base = _ASSET.get(base, base)
    quote = _ASSET.get(quote, quote)
    symbol = f"{base}/{quote}"
    return InstrumentRules(
        venue="kraken",
        symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        price_increment=Decimal(10) ** -int(info.get("pair_decimals", 1)),
        quantity_increment=Decimal(10) ** -int(info.get("lot_decimals", 8)),
        min_quantity=Decimal(str(info.get("ordermin", "0.0001"))),
        min_notional=Decimal(str(info.get("costmin", "1"))),
        synthetic=False,
    )


def _price(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise ValidationFailure("binary float is not a market price")
    return parse_decimal(value)


def normalize_ticker(
    message: dict,
    *,
    observation_id: str,
    available_at: datetime,
    event_time: datetime | None = None,
    source: str = "kraken_public",
) -> Observation:
    data = message.get("data", message)
    symbol = str(data["symbol"]).replace("XBT", "BTC")
    occurred = event_time or available_at
    if occurred > available_at:
        raise ValidationFailure("event time cannot be after the observation was available")
    return Observation(
        observation_id=observation_id,
        venue="kraken",
        symbol=symbol,
        event_time_utc=occurred,
        available_at_utc=available_at,
        bid=_price(data.get("bid")),
        ask=_price(data.get("ask")),
        last=_price(data.get("last")),
        bid_size=_price(data.get("bid_qty")),
        ask_size=_price(data.get("ask_qty")),
        volume=_price(data.get("volume")),
        kind="quote",
        source=source,
    )
