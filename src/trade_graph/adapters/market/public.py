"""Public Kraken market data and Frankfurter reference FX.

Default tests inject scripted transports. This module does not place orders or
use exchange credentials. A scripted response is not a live network result.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

from trade_graph.adapters.market.normalize import normalize_pair, normalize_ticker
from trade_graph.contracts.models import InstrumentRules, Observation
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import parse_decimal

KRAKEN_REST = "https://api.kraken.com/0/public"
KRAKEN_WS = "wss://ws.kraken.com/v2"
FRANKFURTER = "https://api.frankfurter.dev/v2"


class TextTransport(Protocol):
    def get_text(self, url: str) -> str: ...


class TextSession(Protocol):
    def send_text(self, payload: str) -> None: ...

    def recv_text(self) -> str | None: ...


def loads(text: str) -> Any:
    return json.loads(text, parse_float=Decimal)


class HttpxTextTransport:
    """Public GET only. Construct this only for an explicitly labeled network smoke."""

    def __init__(self, timeout: float = 10) -> None:
        self.timeout = timeout

    def get_text(self, url: str) -> str:
        import httpx

        response = httpx.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text


class WebsocketTextSession:
    """Sync public WebSocket. Not used by the default test suite."""

    def __init__(self, url: str = KRAKEN_WS, timeout: float = 5) -> None:
        self.url = url
        self.timeout = timeout
        self._conn: Any = None

    def connect(self) -> None:
        from websockets.sync.client import connect

        self._conn = connect(self.url, open_timeout=self.timeout)

    def send_text(self, payload: str) -> None:
        if self._conn is None:
            self.connect()
        self._conn.send(payload)

    def recv_text(self) -> str | None:
        if self._conn is None:
            self.connect()
        try:
            message = self._conn.recv(timeout=self.timeout)
        except TimeoutError:
            return ""
        except Exception:
            self._conn = None
            return None
        if isinstance(message, bytes):
            return message.decode()
        return str(message)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


@dataclass(frozen=True)
class ReferenceFxRate:
    base: str
    quote: str
    rate: Decimal
    rate_date: str
    source: str
    provider: str


class KrakenPublicRest:
    def __init__(self, transport: TextTransport) -> None:
        self.transport = transport

    def _get(self, url: str) -> dict[str, Any]:
        payload = loads(self.transport.get_text(url))
        if not isinstance(payload, dict):
            raise ValidationFailure("Kraken public response was not an object")
        errors = payload.get("error") or []
        if errors:
            raise ValidationFailure(f"Kraken public error: {errors}")
        return payload

    def fetch_instruments(self, pairs: list[str]) -> list[InstrumentRules]:
        query = urlencode({"pair": ",".join(pairs)})
        payload = self._get(f"{KRAKEN_REST}/AssetPairs?{query}")
        result = payload.get("result") or {}
        if not isinstance(result, dict) or not result:
            raise ValidationFailure("Kraken AssetPairs returned no instruments")
        return [normalize_pair(name, info) for name, info in result.items()]

    def fetch_ticker(self, symbol: str, *, observation_id: str, available_at: datetime) -> Observation:
        pair = _rest_pair(symbol)
        payload = self._get(f"{KRAKEN_REST}/Ticker?pair={pair}")
        result = payload.get("result") or {}
        if not isinstance(result, dict) or not result:
            raise ValidationFailure("Kraken Ticker returned no book")
        info = next(iter(result.values()))
        ask, ask_qty = _level(info.get("a"))
        bid, bid_qty = _level(info.get("b"))
        last = _level(info.get("c"))[0]
        volume = _level(info.get("v"))[1]
        return normalize_ticker(
            {
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
                "last": last,
                "volume": volume,
            },
            observation_id=observation_id,
            available_at=available_at,
            source="kraken_public_rest",
        )


class FrankfurterClient:
    """Daily reference FX. Not an executable venue quote."""

    def __init__(self, transport: TextTransport, *, provider: str = "ecb") -> None:
        self.transport = transport
        self.provider = provider

    def reference_rate(self, base: str, quote: str, *, on: str | None = None) -> ReferenceFxRate:
        path = f"{FRANKFURTER}/providers/{self.provider}/rate/{base.lower()}/{quote.lower()}"
        if on is not None:
            path = f"{path}?date={on}"
        payload = loads(self.transport.get_text(path))
        if not isinstance(payload, dict):
            raise ValidationFailure("Frankfurter reference rate was missing")
        if payload.get("status"):
            raise ValidationFailure("Frankfurter reference rate was rejected")
        if "rate" not in payload:
            raise ValidationFailure("Frankfurter reference rate was missing")
        rate = payload["rate"]
        if isinstance(rate, float):
            raise ValidationFailure("binary float is not an FX rate")
        parsed = parse_decimal(rate)
        if parsed <= 0:
            raise ValidationFailure("FX rate must be positive")
        rate_date = str(payload.get("date") or on or "")
        if not rate_date:
            raise ValidationFailure("Frankfurter rate has no date")
        provider = self.provider.upper()
        return ReferenceFxRate(
            base=str(payload.get("base") or base).upper(),
            quote=str(payload.get("quote") or quote).upper(),
            rate=parsed,
            rate_date=rate_date,
            source=f"frankfurter:{provider}:{rate_date}",
            provider=provider,
        )


class KrakenPublicFeed:
    """Ticker session with reconnect and REST backfill. Gaps are not fresh books."""

    def __init__(
        self,
        session_factory: Callable[[], TextSession],
        rest: KrakenPublicRest,
        clock: Clock,
        symbols: list[str],
    ) -> None:
        self.session_factory = session_factory
        self.rest = rest
        self.clock = clock
        self.symbols = list(symbols)
        self.session: TextSession | None = None
        self.degraded = set(self.symbols)
        self.gaps: list[str] = []
        self._latest: dict[str, Observation] = {}
        self._sequence = 0

    def connect(self) -> None:
        self.session = self.session_factory()
        connect = getattr(self.session, "connect", None)
        if callable(connect):
            connect()
        body = {
            "method": "subscribe",
            "params": {
                "channel": "ticker",
                "symbol": self.symbols,
                "event_trigger": "bbo",
                "snapshot": True,
            },
        }
        self.session.send_text(json.dumps(body))

    def poll(self) -> list[Observation]:
        if self.session is None:
            self.connect()
        assert self.session is not None
        raw = self.session.recv_text()
        if raw is None:
            self._mark_gap("disconnect")
            self.connect()
            return self._backfill()
        if raw == "":
            return []
        message = loads(raw)
        if not isinstance(message, dict) or message.get("channel") != "ticker":
            return []
        emitted: list[Observation] = []
        for item in message.get("data") or []:
            observation = self._accept(item, kind=str(message.get("type") or "update"))
            if observation is not None:
                emitted.append(observation)
        return emitted

    def latest(self, symbol: str) -> Observation | None:
        return self._latest.get(symbol)

    def blocks_increase(self, symbol: str) -> bool:
        return symbol in self.degraded or self.latest(symbol) is None

    def _accept(self, item: dict[str, Any], *, kind: str) -> Observation | None:
        symbol = str(item.get("symbol", "")).replace("XBT", "BTC")
        event_time = _parse_time(item.get("timestamp")) or self.clock.now()
        previous = self._latest.get(symbol)
        if previous is not None and event_time < previous.event_time_utc:
            self._mark_gap(f"out_of_order:{symbol}")
            return None
        self._sequence += 1
        observation = normalize_ticker(
            item,
            observation_id=f"kraken-{symbol}-{self._sequence}",
            available_at=self.clock.now(),
            event_time=event_time,
            source=f"kraken_public_ws:{kind}",
        )
        self._latest[symbol] = observation
        self.degraded.discard(symbol)
        return observation

    def _backfill(self) -> list[Observation]:
        emitted: list[Observation] = []
        for symbol in self.symbols:
            self._sequence += 1
            observation = self.rest.fetch_ticker(
                symbol,
                observation_id=f"kraken-backfill-{symbol}-{self._sequence}",
                available_at=self.clock.now(),
            )
            self._latest[symbol] = observation
            self.degraded.discard(symbol)
            self.gaps.append(f"backfill:{symbol}")
            emitted.append(observation)
        return emitted

    def _mark_gap(self, reason: str) -> None:
        self.gaps.append(reason)
        self.degraded.update(self.symbols)


def _rest_pair(symbol: str) -> str:
    base, quote = symbol.split("/")
    if base == "BTC":
        base = "XBT"
    return f"{base}{quote}"


def _level(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, list) or not value:
        return None, None
    price = value[0]
    size = value[2] if len(value) > 2 else value[1] if len(value) > 1 else None
    if isinstance(price, float) or isinstance(size, float):
        raise ValidationFailure("binary float is not a market price")
    return (None if price is None else str(price), None if size is None else str(size))


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
