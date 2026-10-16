"""Shared helpers for the price-alarm tests: requests, synthetic candles and quotes, a frozen clock. No network."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_graph.contracts.alarms import AlarmRequest, parse_condition
from trade_graph.contracts.candles import Candle
from trade_graph.contracts.models import Observation

NOW = datetime(2026, 10, 16, 12, 7, 0, tzinfo=UTC)


def quote_request(level: str = "65000", relation: str = "above", *, department: str = "trader",
                  symbol: str = "BTC/USD", price: str = "mid", **extra: Any) -> AlarmRequest:
    condition = parse_condition({"kind": "quote_price", "symbol": symbol, "price": price, "relation": relation,
                                 "level": level})
    return AlarmRequest(department=department, condition=condition, **extra)


def candle_request(level: str, relation: str = "above", *, department: str = "trader", symbol: str = "SYN/USD",
                   interval: int = 15, price: str = "close", **extra: Any) -> AlarmRequest:
    condition = parse_condition({"kind": "candle_price", "symbol": symbol, "interval_minutes": interval,
                                 "source": "synthetic", "price": price, "relation": relation, "level": level})
    return AlarmRequest(department=department, condition=condition, **extra)


def indicator_request(indicator: str, level: str, relation: str = "above", *, parameters: dict | None = None,
                      output: str | None = None, department: str = "trader", symbol: str = "SYN/USD",
                      interval: int = 15, **extra: Any) -> AlarmRequest:
    condition = parse_condition({"kind": "indicator", "symbol": symbol, "interval_minutes": interval,
                                 "source": "synthetic", "indicator": indicator, "parameters": parameters or {},
                                 "output": output, "relation": relation, "level": level})
    return AlarmRequest(department=department, condition=condition, **extra)


def candle(close_time: datetime, close: str, *, interval: int = 15, received_after_seconds: int = 20,
           high: str | None = None, low: str | None = None, symbol: str = "SYN/USD") -> Candle:
    """One completed synthetic candle closing at ``close_time``, received a little after it closed."""
    price = Decimal(close)
    top = Decimal(high) if high is not None else price
    bottom = Decimal(low) if low is not None else price
    step = timedelta(minutes=interval)
    return Candle(symbol=symbol, source_pair=symbol.replace("/", ""), interval_minutes=interval,
                  open_time_utc=close_time - step, close_time_utc=close_time,
                  available_at_utc=close_time + timedelta(seconds=received_after_seconds), open=price,
                  high=max(top, price), low=min(bottom, price), close=price, volume=Decimal("1"), trade_count=1,
                  source="synthetic")


def save(store: Any, candles: Any) -> None:
    """Store candles as one synthetic collection run, received when each says it was."""
    candles = list(candles)
    run = store.begin_run(kind="public_collect", source="synthetic", evidence_label="synthetic_fixture",
                          started_at=min(item.available_at_utc for item in candles), parameters={"note": "alarm test"})
    store.save(candles, run_id=run)


def quote(symbol: str, at: datetime, *, bid: str | None = None, ask: str | None = None, last: str | None = None,
          kind: str = "quote") -> Observation:
    """A public price update received at ``at`` (the paper feed stamps receipt time as both times)."""
    return Observation(observation_id=str(uuid.uuid4()), venue="paper", symbol=symbol, event_time_utc=at,
                       available_at_utc=at, bid=bid, ask=ask, last=last, kind=kind,
                       source="kraken_public_rest:paper_reference:receipt_time")
