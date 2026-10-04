"""Public Kraken market data and Frankfurter reference FX.

Default tests inject scripted transports. This module does not place orders or
use exchange credentials. A scripted response is not a live network result.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from time import monotonic
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
    """Opt-in public GET with bounded decoded bytes, no redirects and proxy policy intact.

    HTTP timeouts apply to each network phase. The elapsed deadline also refuses
    a continuously trickled response; a blocked read still has its phase timeout.
    """

    def __init__(self, timeout: float = 10, *, maximum_response_bytes: int = 1_048_576) -> None:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("finite positive public HTTP timeout required")
        if type(maximum_response_bytes) is not int or not 1 <= maximum_response_bytes <= 16_777_216:
            raise ValueError("public response byte limit must be between 1 and 16777216")
        self.timeout = timeout
        self.maximum_response_bytes = maximum_response_bytes

    def get_text(self, url: str) -> str:
        import httpx

        started = monotonic()
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
                response.raise_for_status()
                if monotonic() - started >= self.timeout:
                    raise ValidationFailure("public response exceeded its elapsed deadline")
                # The server must honor the identity request. Refuse compressed
                # data before httpx decompresses a chunk into unbounded memory.
                encoding = response.headers.get("content-encoding", "").strip().lower()
                if encoding not in {"", "identity"}:
                    raise ValidationFailure("public response used unsupported content encoding")
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        declared = int(length)
                    except ValueError as exc:
                        raise ValidationFailure("public response declared an invalid byte length") from exc
                    if declared < 0 or declared > self.maximum_response_bytes:
                        raise ValidationFailure("public response exceeded its byte limit")
                payload = bytearray()
                # Do not request coalesced chunks: a trickle must reach the
                # deadline check instead of waiting for a large chunk to fill.
                for chunk in response.iter_bytes():
                    if monotonic() - started >= self.timeout:
                        raise ValidationFailure("public response exceeded its elapsed deadline")
                    if len(payload) + len(chunk) > self.maximum_response_bytes:
                        raise ValidationFailure("public response exceeded its byte limit")
                    payload.extend(chunk)
                if monotonic() - started >= self.timeout:
                    raise ValidationFailure("public response exceeded its elapsed deadline")
                try:
                    return payload.decode(response.encoding or "utf-8")
                except (UnicodeError, LookupError) as exc:
                    raise ValidationFailure("public response encoding was invalid") from exc


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
        # The default public pairs can be identified before metadata retrieval.
        # Other symbols require the exact aliases returned by AssetPairs first.
        self._pair_symbols = {
            "XXBTZUSD": "BTC/USD", "XBTUSD": "BTC/USD", "BTCUSD": "BTC/USD",
            "XETHZUSD": "ETH/USD", "ETHUSD": "ETH/USD",
        }

    def _get(self, url: str) -> dict[str, Any]:
        payload = loads(self.transport.get_text(url))
        if not isinstance(payload, dict):
            raise ValidationFailure("Kraken public response was not an object")
        if payload.get("error") != []:
            raise ValidationFailure("Kraken public response has missing or rejected error status")
        return payload

    def fetch_instruments(self, pairs: list[str]) -> list[InstrumentRules]:
        if not pairs or any(not isinstance(pair, str) for pair in pairs):
            raise ValidationFailure("Kraken metadata requires explicit pair scope")
        requested = {_rest_pair(pair) if "/" in pair else pair.replace("BTC", "XBT") for pair in pairs}
        expected_symbols = {
            _rest_pair(pair): pair.replace("XBT", "BTC") for pair in pairs if "/" in pair
        }
        expected_symbols.update({pair: self._pair_symbols[pair] for pair in requested if pair in self._pair_symbols})
        if any(re.fullmatch(r"[A-Z0-9]{4,24}", pair) is None for pair in requested):
            raise ValidationFailure("Kraken metadata requires valid pair identifiers")
        query = urlencode({"pair": ",".join(_rest_pair(pair) if "/" in pair else pair for pair in pairs)})
        payload = self._get(f"{KRAKEN_REST}/AssetPairs?{query}")
        result = payload.get("result") or {}
        if not isinstance(result, dict) or not result:
            raise ValidationFailure("Kraken AssetPairs returned no instruments")
        instruments, covered, aliases = [], set(), {}
        for name, info in result.items():
            if not isinstance(info, dict):
                raise ValidationFailure("Kraken metadata instrument is not an object")
            try:
                instrument = normalize_pair(name, info)
                names = {name, _rest_pair(instrument.symbol)}
            except (ValueError, KeyError, TypeError) as exc:
                raise ValidationFailure("Kraken metadata instrument identity is invalid") from exc
            if isinstance(info.get("altname"), str):
                names.add(info["altname"])
            matches = requested & names
            if (not matches or instrument.symbol in {item.symbol for item in instruments}
                    or any(expected_symbols.get(pair, instrument.symbol) != instrument.symbol for pair in matches)):
                raise ValidationFailure("Kraken metadata differs from the requested pair scope")
            covered.update(matches)
            for alias in names:
                if (alias in aliases and aliases[alias] != instrument.symbol
                        or alias in self._pair_symbols and self._pair_symbols[alias] != instrument.symbol):
                    raise ValidationFailure("Kraken metadata aliases are ambiguous")
                aliases[alias] = instrument.symbol
            instruments.append(instrument)
        if covered != requested:
            raise ValidationFailure("Kraken metadata omitted a requested pair")
        self._pair_symbols.update(aliases)
        return instruments

    def fetch_ticker(self, symbol: str, *, observation_id: str, available_at: datetime) -> Observation:
        pair = _rest_pair(symbol)
        payload = self._get(f"{KRAKEN_REST}/Ticker?pair={pair}")
        result = payload.get("result") or {}
        if not isinstance(result, dict) or len(result) != 1:
            raise ValidationFailure("Kraken Ticker must return exactly one requested book")
        name, info = next(iter(result.items()))
        if self._pair_symbols.get(name) != symbol or not isinstance(info, dict):
            raise ValidationFailure("Kraken ticker identity differs from the requested symbol")
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
        if not isinstance(provider, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,15}", provider) is None:
            raise ValidationFailure("invalid Frankfurter provider key")
        self.transport = transport
        self.provider = provider

    def reference_rate(self, base: str, quote: str, *, on: str | None = None) -> ReferenceFxRate:
        if any(not isinstance(code, str) or re.fullmatch(r"[A-Za-z]{3}", code) is None for code in (base, quote)):
            raise ValidationFailure("three-letter FX currency codes required")
        requested_date = None if on is None else _reference_date(on)
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
        # Require the wire's pair/date: filling gaps with requested values would
        # invent provenance or apply a rate in the wrong currency direction.
        if (not isinstance(payload.get("base"), str) or not isinstance(payload.get("quote"), str)
                or payload["base"].upper() != base.upper() or payload["quote"].upper() != quote.upper()):
            raise ValidationFailure("Frankfurter reference rate has a missing or mismatched currency pair")
        observed_date = _reference_date(payload.get("date"))
        if requested_date is not None and observed_date > requested_date:
            raise ValidationFailure("Frankfurter reference rate follows the requested date")
        rate_date = observed_date.isoformat()
        provider = self.provider.upper()
        return ReferenceFxRate(
            base=payload["base"].upper(),
            quote=payload["quote"].upper(),
            rate=parsed,
            rate_date=rate_date,
            source=f"frankfurter:{provider}:{rate_date}",
            provider=provider,
        )


def _reference_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValidationFailure("Frankfurter rate requires a sourced ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationFailure("Frankfurter rate requires a sourced ISO calendar date") from exc
    if parsed.isoformat() != value:
        raise ValidationFailure("Frankfurter rate requires a sourced ISO calendar date")
    return parsed


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
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{2,12}/[A-Z0-9]{2,12}", symbol) is None:
        raise ValidationFailure("Kraken symbol requires a valid base/quote pair")
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
