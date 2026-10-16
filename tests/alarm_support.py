"""Shared helpers for the price-alarm tests: a state database, a frozen clock and simple requests. No network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trade_graph.contracts.alarms import AlarmRequest, parse_condition

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
