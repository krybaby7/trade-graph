"""Kraken public metadata and ticker normalization. No private trading key."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trade_graph.contracts.models import InstrumentRules, Observation

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


def normalize_ticker(message: dict, *, observation_id: str, available_at: datetime) -> Observation:
    data = message.get("data", message)
    symbol = str(data["symbol"]).replace("XBT", "BTC")
    return Observation(
        observation_id=observation_id,
        venue="kraken",
        symbol=symbol,
        event_time_utc=available_at,
        available_at_utc=available_at,
        bid=Decimal(str(data["bid"])),
        ask=Decimal(str(data["ask"])),
        last=Decimal(str(data["last"])) if data.get("last") is not None else None,
        bid_size=Decimal(str(data["bid_qty"])) if data.get("bid_qty") is not None else None,
        ask_size=Decimal(str(data["ask_qty"])) if data.get("ask_qty") is not None else None,
        volume=Decimal(str(data["volume"])) if data.get("volume") is not None else None,
        kind="quote",
        source="kraken_public",
    )
