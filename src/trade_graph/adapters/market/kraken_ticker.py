"""One bounded public Kraken Ticker request for every pair: the bid and ask behind the coin scanner's spread.

``GET /0/public/Ticker`` with no ``pair`` parameter answers for every tradeable pair at once, so one hourly scan
costs one public request and needs no key. Only the best ask (``a``), best bid (``b``) and last trade (``c``) are
read, as exact decimals from Kraken's text. A malformed entry is listed as a rejection with a fixed reason; a
response whose shape cannot be trusted is refused as a whole. Failure categories are fixed strings and never carry
response text. A scripted or fixture answer is never labelled live.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

from trade_graph.adapters.market.kraken_errors import RATE_LIMITED, rate_limit_in
from trade_graph.adapters.market.public import KRAKEN_REST, HttpxTextTransport, TextTransport
from trade_graph.contracts.pair_catalog import ListedPair
from trade_graph.domain.clock import Clock, parse_utc
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import parse_decimal

TICKER_URL = f"{KRAKEN_REST}/Ticker"
# About 1,460 pairs at roughly 300 bytes each is under half a megabyte; 4 MiB leaves eight times that and stays a
# quarter of the transport's own ceiling, so a runaway answer cannot exhaust memory.
TICKER_MAXIMUM_RESPONSE_BYTES = 4 * 1024 * 1024
TICKER_TIMEOUT_SECONDS = 30.0
FIXTURE_LABELS = ("synthetic_fixture", "recorded_public_fixture")
_PRICE_TEXT_LIMIT = 40
_KEY_LIMIT = 32


@dataclass(frozen=True, slots=True)
class TickerEntry:
    key: str            # Kraken's result key, for example XXBTZUSD
    bid: Decimal
    ask: Decimal
    last: Decimal


@dataclass(frozen=True, slots=True)
class RejectedTicker:
    key: str            # the result key when it is plain text, otherwise "unsafe_key"
    reason: str         # a fixed code such as invalid_bid


@dataclass(frozen=True, slots=True)
class ParsedTicker:
    entries: Mapping[str, TickerEntry]
    rejected: tuple[RejectedTicker, ...]
    entries_in_response: int


@dataclass(frozen=True, slots=True)
class TickerSnapshot:
    """One answer and when it arrived. ``received_at`` is the source time of every bid and ask in it."""

    requested_at: datetime
    received_at: datetime
    entries: Mapping[str, TickerEntry]
    rejected: tuple[RejectedTicker, ...]
    entries_in_response: int
    evidence_label: str


@dataclass(frozen=True, slots=True)
class MatchedQuotes:
    quotes: Mapping[str, TickerEntry]      # engine symbol -> entry
    ambiguous: tuple[str, ...]             # symbols that more than one entry names, or that share an entry
    unmatched_keys: int                    # entries naming no pair of the list (other quotes, other pairs)


class TickerFetchError(ValidationFailure):
    """``category``: ``request_failed``, ``response_invalid``, ``rate_limited`` or ``throttled``. The last two mean
    the exchange asked for fewer calls; the caller makes no further request in the same run."""

    def __init__(self, category: str) -> None:
        super().__init__(f"Kraken ticker {category}")
        self.category = category


class _RateLimited(ValidationFailure):
    def __init__(self, category: str) -> None:
        super().__init__("Kraken ticker request was rate limited")
        self.category = category


def make_ticker_transport(timeout: float = TICKER_TIMEOUT_SECONDS) -> HttpxTextTransport:
    return HttpxTextTransport(timeout, maximum_response_bytes=TICKER_MAXIMUM_RESPONSE_BYTES)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValidationFailure("Kraken ticker response has duplicate JSON fields")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("not a finite JSON number")


def _price(level: object) -> Decimal | None:
    """The first element of a Kraken ticker array, as a positive exact decimal written as text."""
    if not isinstance(level, list) or not level:
        return None
    text = level[0]
    if not isinstance(text, str) or not 0 < len(text) <= _PRICE_TEXT_LIMIT:
        return None
    try:
        value = parse_decimal(text)
    except (ArithmeticError, ValueError):
        return None
    return value if value > 0 else None


def _entry(key: str, info: object) -> TickerEntry | RejectedTicker:
    if not isinstance(info, dict):
        return RejectedTicker(key, "not_an_object")
    values = {}
    for field, name in (("b", "bid"), ("a", "ask"), ("c", "last")):
        value = _price(info.get(field))
        if value is None:
            return RejectedTicker(key, f"invalid_{name}")
        values[name] = value
    return TickerEntry(key=key, **values)


def parse_ticker(raw: str) -> ParsedTicker:
    try:
        too_large = not isinstance(raw, str) or len(raw.encode("utf-8")) > TICKER_MAXIMUM_RESPONSE_BYTES
    except UnicodeError:
        raise ValidationFailure("Kraken ticker response is not valid text") from None
    if too_large:
        raise ValidationFailure("Kraken ticker response exceeded its byte limit")
    try:
        payload = json.loads(raw, parse_float=Decimal, parse_constant=_refuse_constant,
                             object_pairs_hook=_unique_object)
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        raise ValidationFailure("Kraken ticker response JSON is invalid") from None
    if not isinstance(payload, dict) or payload.get("error") != []:
        signal = rate_limit_in(payload.get("error")) if isinstance(payload, dict) else None
        if signal is not None:
            raise _RateLimited(signal.category)
        raise ValidationFailure("Kraken ticker response has missing or rejected error status")
    result = payload.get("result")
    if not isinstance(result, dict) or not result:
        raise ValidationFailure("Kraken ticker result must be a non-empty object")
    entries: dict[str, TickerEntry] = {}
    rejected: list[RejectedTicker] = []
    for key, info in result.items():
        safe = key if isinstance(key, str) and 0 < len(key) <= _KEY_LIMIT and key.isascii() and key.isalnum() \
            else "unsafe_key"
        outcome = _entry(safe, info) if safe != "unsafe_key" else RejectedTicker(safe, "unsafe_key")
        if isinstance(outcome, TickerEntry):
            entries[safe] = outcome
        else:
            rejected.append(outcome)
    return ParsedTicker(entries=MappingProxyType(dict(sorted(entries.items()))),
                        rejected=tuple(sorted(rejected, key=lambda item: (item.key, item.reason))),
                        entries_in_response=len(result))


def _utc_now(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationFailure("the ticker requires an aware receipt clock")
    return now.astimezone(UTC)


class KrakenTickerFetcher:
    """Credential-free GET only: one request, no retries, no query parameters."""

    def __init__(self, transport: TextTransport, clock: Clock, *, evidence_label: str = "live_public") -> None:
        if evidence_label not in ("live_public", "live_public_smoke", *FIXTURE_LABELS):
            raise ValidationFailure("unsupported evidence label for a ticker answer")
        self.transport = transport
        self.clock = clock
        self.evidence_label = evidence_label

    def fetch(self) -> TickerSnapshot:
        requested_at = _utc_now(self.clock)
        try:
            raw = self.transport.get_text(TICKER_URL)
        except Exception as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            raise TickerFetchError(RATE_LIMITED if status == 429 else "request_failed") from None
        # Receipt is taken after the response, so a slow answer is never dated earlier than it arrived.
        received_at = _utc_now(self.clock)
        if received_at < requested_at:
            raise TickerFetchError("response_invalid")
        try:
            parsed = parse_ticker(raw)
        except _RateLimited as signal:
            raise TickerFetchError(signal.category) from None
        except ValidationFailure:
            raise TickerFetchError("response_invalid") from None
        return TickerSnapshot(requested_at=requested_at, received_at=received_at, entries=parsed.entries,
                              rejected=parsed.rejected, entries_in_response=parsed.entries_in_response,
                              evidence_label=self.evidence_label)


def load_ticker_fixture(path: str | Path) -> TickerSnapshot:
    """A labelled fixture file: ``evidence_label``, ``received_at_utc`` and Kraken's ``response``. Never live."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValidationFailure("the ticker fixture cannot be read") from error
    if not isinstance(document, dict) or document.get("evidence_label") not in FIXTURE_LABELS:
        raise ValidationFailure("a ticker fixture must be labelled synthetic_fixture or recorded_public_fixture")
    try:
        received_at = parse_utc(document.get("received_at_utc"))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValidationFailure("a ticker fixture needs received_at_utc as a UTC time") from error
    parsed = parse_ticker(json.dumps(document.get("response")))
    return TickerSnapshot(requested_at=received_at, received_at=received_at, entries=parsed.entries,
                          rejected=parsed.rejected, entries_in_response=parsed.entries_in_response,
                          evidence_label=document["evidence_label"])


def _pair_names(pair: ListedPair) -> set[str]:
    """The names Kraken may use for a pair as a result key (the OHLC adapter matches the same way)."""
    return {pair.source_pair, pair.ws_name.replace("/", ""), pair.base + pair.quote}


def quotes_by_symbol(entries: Mapping[str, TickerEntry], pairs: Iterable[ListedPair]) -> MatchedQuotes:
    """The entry for each pair. A pair two entries could name, or an entry two pairs could own, gets no quote."""
    owners: dict[str, list[str]] = {}
    for pair in pairs:
        for name in _pair_names(pair):
            owners.setdefault(name, [])
            if pair.symbol not in owners[name]:
                owners[name].append(pair.symbol)
    claims: dict[str, list[str]] = {}
    unmatched = 0
    ambiguous: set[str] = set()
    for key in entries:
        symbols = owners.get(key, [])
        if not symbols:
            unmatched += 1
        elif len(symbols) > 1:
            ambiguous.update(symbols)
        else:
            claims.setdefault(symbols[0], []).append(key)
    quotes: dict[str, TickerEntry] = {}
    for symbol, keys in claims.items():
        if len(keys) > 1:
            ambiguous.add(symbol)
        elif symbol not in ambiguous:
            quotes[symbol] = entries[keys[0]]
    return MatchedQuotes(quotes=MappingProxyType(dict(sorted(quotes.items()))), ambiguous=tuple(sorted(ambiguous)),
                         unmatched_keys=unmatched)


__all__ = [
    "FIXTURE_LABELS", "TICKER_MAXIMUM_RESPONSE_BYTES", "TICKER_URL", "KrakenTickerFetcher", "MatchedQuotes",
    "ParsedTicker", "RejectedTicker", "TickerEntry", "TickerFetchError", "TickerSnapshot", "load_ticker_fixture",
    "make_ticker_transport", "parse_ticker", "quotes_by_symbol",
]
