"""One bounded public Kraken OHLC request for a catalog pair at 1, 5, 15 or 60 minutes.

``GET /0/public/OHLC`` needs no key (https://docs.kraken.com/api/docs/rest-api/get-ohlc-data, read 2026-10-13).
What the recorded responses of 2026-10-13 show, and what this module relies on
(details: docs/KRAKEN-CANDLE-COLLECTION.md):

- A full response has 721 rows: the 720 most recent committed candles and, last, the candle still forming.
  The forming row is always present. Discard it unconditionally and never look at its values: for a pair with
  no data in the window it is the only row, with time 0 and zero prices.
- ``since`` is inclusive: ``since=T`` returns the candle that opens at T if it is committed.
- Kraken fills intervals without trades with a flat row (price of the previous close, vwap 0, volume 0, trade
  count 0). Such a quiet row is stored as Kraken sent it; ``trade_count == 0`` marks it.
- ``last`` in the result is the open time of the last committed candle.

The receipt time is taken from the clock after the network call returns, never from the exchange. A row whose
close is after the receipt time refuses the whole response. There are no retries, no pagination and no
credentials. Failure categories are fixed strings; response text never enters an exception.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from trade_graph.adapters.market.kraken_errors import MAXIMUM_UNIX_TIME, rate_limit_signal
from trade_graph.adapters.market.public import KRAKEN_REST, HttpxTextTransport, TextTransport
from trade_graph.contracts.candles import COLLECTED_INTERVALS, Candle, CandleSource, from_unix
from trade_graph.contracts.pair_catalog import ListedPair
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import parse_decimal

OHLC_URL = f"{KRAKEN_REST}/OHLC"
OHLC_COMMITTED_ROWS = 720
OHLC_MAXIMUM_ROWS = OHLC_COMMITTED_ROWS + 1  # plus the candle still forming
# A full response was about 60 KB on 2026-10-13. A megabyte leaves room for longer decimals and is the
# transport's own default, far below its 16 MiB ceiling.
OHLC_MAXIMUM_RESPONSE_BYTES = 1_048_576
OHLC_TIMEOUT_SECONDS = 15.0
# Far above any real count and far below the 64-bit integer the store writes. A larger number is a corrupt answer: it
# is refused as an invalid response here, not met later as an unexplained storage failure.
OHLC_MAXIMUM_TRADE_COUNT = 10**12
OHLC_SOURCES = ("kraken_public_ohlc", "synthetic")
OHLC_FAILURE_CATEGORIES = (
    "request_failed", "rate_limited", "throttled", "unknown_pair", "service_unavailable", "exchange_error",
    "response_invalid", "pair_mismatch", "candle_not_closed", "clock_invalid",
)
_PAIR_CODE = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,23}")
_STORABLE_SYMBOL = re.compile(r"[A-Z0-9]{1,16}/[A-Z]{3,5}")  # the Candle model's own symbol shape
_ERROR_CLASS = re.compile(r"(E[A-Za-z]{1,15}):.*", re.DOTALL)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_ROW_LENGTH = 8


def exception_class_name(error: BaseException) -> str | None:
    """The class name of an internal failure when it is a plain identifier, never its text."""
    name = type(error).__name__
    return name if _IDENTIFIER.fullmatch(name) else None


class OhlcFetchError(ValidationFailure):
    """The request or response failed. ``category`` is a fixed string; no response text is carried.

    ``error_class`` is the exchange's own error class (``EGeneral``); ``internal_class`` is the class name of a local
    failure (a timeout, a storage error) so that "request failed" and "persistence failed" can be told apart.
    """

    def __init__(self, category: str, *, retry_at_unix: int | None = None, error_class: str | None = None,
                 internal_class: str | None = None) -> None:
        super().__init__(f"Kraken OHLC {category}")
        self.category = category
        self.retry_at_unix = retry_at_unix
        self.error_class = error_class
        self.internal_class = internal_class


@dataclass(frozen=True, slots=True)
class OhlcFetch:
    pair: ListedPair
    interval_minutes: int
    since: int | None
    requested_at: datetime
    received_at: datetime
    rows_received: int            # every row of the response, including the forming one that is dropped
    candles: tuple[Candle, ...]   # committed, validated, oldest first
    cursor: int                   # the response's ``last``

    @property
    def no_trade_candles(self) -> int:
        return sum(1 for candle in self.candles if candle.trade_count == 0)


def make_ohlc_transport(timeout: float = OHLC_TIMEOUT_SECONDS) -> HttpxTextTransport:
    return HttpxTextTransport(timeout, maximum_response_bytes=OHLC_MAXIMUM_RESPONSE_BYTES)


def candle_symbol_supported(symbol: str) -> bool:
    """Whether a candle for this engine symbol can be stored (the Candle model limits the base to 16 characters)."""
    return isinstance(symbol, str) and _STORABLE_SYMBOL.fullmatch(symbol) is not None


def _check_interval(interval_minutes: object) -> int:
    if type(interval_minutes) is not int or interval_minutes not in COLLECTED_INTERVALS:
        raise ValidationFailure("candles are collected at 1, 5, 15 and 60 minutes only")
    return interval_minutes


def ohlc_url(source_pair: str, interval_minutes: int, since: int | None) -> str:
    if not isinstance(source_pair, str) or _PAIR_CODE.fullmatch(source_pair) is None:
        raise ValidationFailure("the Kraken pair code is not a plain upper-case code")
    _check_interval(interval_minutes)
    url = f"{OHLC_URL}?pair={source_pair}&interval={interval_minutes}"
    if since is None:
        return url
    if type(since) is not int or not 0 <= since <= MAXIMUM_UNIX_TIME:
        raise ValidationFailure("since must be a plausible unix time")
    return f"{url}&since={since}"


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> Any:
    raise ValueError("not a finite JSON number")


def _utc_now(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationFailure("the candle collector requires an aware receipt clock")
    return now.astimezone(UTC)


def _classify_error(text: str) -> OhlcFetchError | None:
    signal = rate_limit_signal(text)
    if signal is not None:
        return OhlcFetchError(signal.category, retry_at_unix=signal.retry_at_unix)
    squashed = re.sub(r"\s+", "", text).lower()
    if squashed.startswith("equery:unknownassetpair"):
        return OhlcFetchError("unknown_pair")
    if squashed.startswith("eservice:"):
        return OhlcFetchError("service_unavailable")
    return None


def _exchange_failure(errors: object) -> OhlcFetchError:
    if not isinstance(errors, list) or not all(isinstance(item, str) for item in errors):
        return OhlcFetchError("response_invalid")
    for text in errors:
        recognised = _classify_error(text)
        if recognised is not None:
            return recognised
    match = _ERROR_CLASS.fullmatch(errors[0]) if errors else None
    return OhlcFetchError("exchange_error", error_class=match.group(1) if match else None)


def _pair_names(pair: ListedPair) -> set[str]:
    return {pair.source_pair, pair.ws_name, pair.ws_name.replace("/", ""), pair.base + pair.quote}


def _candle(row: object, pair: ListedPair, interval_minutes: int, received_at: datetime,
            source: CandleSource) -> Candle:
    if not isinstance(row, list) or len(row) != _ROW_LENGTH:
        raise OhlcFetchError("response_invalid")
    step = interval_minutes * 60
    opened = row[0]
    if type(opened) is not int or not 0 <= opened <= MAXIMUM_UNIX_TIME or opened % step:
        raise OhlcFetchError("response_invalid")
    count = row[7]
    if type(count) is not int or not 0 <= count <= OHLC_MAXIMUM_TRADE_COUNT:
        raise OhlcFetchError("response_invalid")
    open_time, close_time = from_unix(opened), from_unix(opened + step)
    if close_time > received_at:
        raise OhlcFetchError("candle_not_closed")
    try:
        opening, high, low, closing, vwap, volume = (parse_decimal(value) for value in row[1:7])
        candle = Candle(
            symbol=pair.symbol, source_pair=pair.source_pair, interval_minutes=interval_minutes,
            open_time_utc=open_time, close_time_utc=close_time, available_at_utc=received_at,
            open=opening, high=high, low=low, close=closing, vwap=vwap, volume=volume, trade_count=count,
            source=source)
    except (ValueError, TypeError, ArithmeticError, ValidationError):
        raise OhlcFetchError("response_invalid") from None
    if count == 0 and volume != 0:
        raise OhlcFetchError("response_invalid")
    return candle


class KrakenCandleFetcher:
    """Credential-free GET only: one request per call, no retries, no pagination."""

    def __init__(self, transport: TextTransport, clock: Clock, *, source: CandleSource = "kraken_public_ohlc",
                 ) -> None:
        if source not in OHLC_SOURCES:
            raise ValidationFailure("the OHLC collector writes kraken_public_ohlc or synthetic series only")
        self.transport = transport
        self.clock = clock
        self.source: CandleSource = source

    def fetch(self, pair: ListedPair, interval_minutes: int, *, since: int | None = None) -> OhlcFetch:
        url = ohlc_url(pair.source_pair, interval_minutes, since)
        if not candle_symbol_supported(pair.symbol):
            raise ValidationFailure("this pair's symbol cannot be stored as candles")
        requested_at = _utc_now(self.clock)
        try:
            raw = self.transport.get_text(url)
        except Exception as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            raise OhlcFetchError("rate_limited" if status == 429 else "request_failed",
                                 internal_class=exception_class_name(error)) from None
        # Receipt is taken after the response, so a slow response is never dated earlier than it arrived.
        received_at = _utc_now(self.clock)
        if received_at < requested_at:
            raise OhlcFetchError("clock_invalid")
        rows, cursor = self._rows(raw, pair)
        committed = rows[:-1]  # the final row is the candle still forming: dropped, not inspected
        candles: list[Candle] = []
        previous_open = -1
        for row in committed:
            candle = _candle(row, pair, interval_minutes, received_at, self.source)
            if candle.open_unix <= previous_open:
                raise OhlcFetchError("response_invalid")
            previous_open = candle.open_unix
            candles.append(candle)
        return OhlcFetch(pair=pair, interval_minutes=interval_minutes, since=since, requested_at=requested_at,
                         received_at=received_at, rows_received=len(rows), candles=tuple(candles), cursor=cursor)

    @staticmethod
    def _rows(raw: object, pair: ListedPair) -> tuple[list, int]:
        try:
            too_large = not isinstance(raw, str) or len(raw.encode("utf-8")) > OHLC_MAXIMUM_RESPONSE_BYTES
        except UnicodeError:
            raise OhlcFetchError("response_invalid") from None
        if too_large:
            raise OhlcFetchError("response_invalid")
        try:
            payload = json.loads(raw, parse_float=Decimal, parse_constant=_refuse_constant,
                                 object_pairs_hook=_unique_object)
        except (ValueError, TypeError, ArithmeticError, RecursionError):
            raise OhlcFetchError("response_invalid") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("error"), list):
            raise OhlcFetchError("response_invalid")
        if payload["error"]:
            raise _exchange_failure(payload["error"])
        result = payload.get("result")
        if not isinstance(result, dict) or len(result) != 2 or "last" not in result:
            raise OhlcFetchError("response_invalid")
        cursor = result["last"]
        if type(cursor) is not int or not 0 <= cursor <= MAXIMUM_UNIX_TIME:
            raise OhlcFetchError("response_invalid")
        key = next(name for name in result if name != "last")
        if key not in _pair_names(pair):
            raise OhlcFetchError("pair_mismatch")
        rows = result[key]
        if not isinstance(rows, list) or not 1 <= len(rows) <= OHLC_MAXIMUM_ROWS:
            raise OhlcFetchError("response_invalid")
        if not isinstance(rows[-1], list) or len(rows[-1]) != _ROW_LENGTH:
            raise OhlcFetchError("response_invalid")
        return rows, cursor


__all__ = [
    "OHLC_FAILURE_CATEGORIES", "OHLC_MAXIMUM_RESPONSE_BYTES", "OHLC_MAXIMUM_ROWS", "KrakenCandleFetcher", "OhlcFetch",
    "OhlcFetchError", "candle_symbol_supported", "exception_class_name", "make_ohlc_transport", "ohlc_url",
]
