"""One bounded page of Kraken's public ``Trades`` list (docs/KRAKEN-TRADES-BACKFILL.md).

The adapter only fetches and validates. It never retries, waits or paginates: the caller owns the request budget,
the rate limiter and the cursor. Times and prices stay exact (``parse_float=Decimal``), the response is size bounded,
duplicate JSON keys and any non-empty ``error`` list are refused, and every refusal carries a stable ``reason`` code
instead of the response body.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trade_graph.adapters.market.public import KRAKEN_REST, TextTransport
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import ValidationFailure

TRADES_URL = f"{KRAKEN_REST}/Trades"
MAX_TRADES_PER_PAGE = 1000
MAXIMUM_RESPONSE_BYTES = 1_048_576
MAX_ERROR_STRINGS = 3
MAX_ERROR_CHARS = 120
# Kraken's `last` is the final trade's time in nanoseconds; it may carry sub-microsecond digits that the row's
# seven-decimal `time` rounds away. One second of slack catches a cursor that stopped meaning "time of the last trade".
CURSOR_TOLERANCE_NS = 1_000_000_000
MAX_CURSOR = 2**63 - 1
MAX_UNIX_TIME = 4_102_444_800  # 2100-01-01; a nanosecond cursor is far above this, seconds are below it
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PAIR_CODE = re.compile(r"[A-Z0-9]{2,24}")
_SYMBOL = re.compile(r"([A-Z0-9]{1,16})/USD")
_DECIMAL_TEXT = re.compile(r"[0-9]{1,20}(?:\.[0-9]{1,20})?")
_CURSOR_TEXT = re.compile(r"[0-9]{1,19}")
_THROTTLED = re.compile(r"EService: ?Throttled: ?([0-9]{9,11})")
_SYMBOL_TO_CODE = {"BTC": "XBT", "DOGE": "XDG"}


class TradesPageRefused(ValidationFailure):
    """The page is not usable. ``reason`` is a stable code; ``kraken_errors`` is the bounded, printable error list."""

    def __init__(self, reason: str, message: str, *, kraken_errors: tuple[str, ...] = (),
                 retry_after_unix: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.kraken_errors = kraken_errors
        self.retry_after_unix = retry_after_unix


class TradesRateLimited(TradesPageRefused):
    """``EAPI:Rate limit exceeded``: the shared request counter went over its maximum."""


class TradesThrottled(TradesPageRefused):
    """``EService: Throttled: <unix time>``: too many concurrent requests; retry after the named time."""


@dataclass(frozen=True, slots=True)
class PublicTrade:
    """One executed trade. ``time`` is Unix seconds with up to seven fraction digits, kept exact."""

    trade_id: int
    price: Decimal
    volume: Decimal
    time: Decimal
    side: str
    order_type: str
    misc: str


@dataclass(frozen=True, slots=True)
class TradesPage:
    pair_code: str
    result_key: str
    trades: tuple[PublicTrade, ...]
    last: int
    requested_since: int | None
    requested_count: int
    received_at: datetime

    @property
    def full(self) -> bool:
        """A page that holds as many rows as were asked for may have more behind it; a shorter one reached the end."""
        return len(self.trades) >= self.requested_count


def pair_code_for_symbol(symbol: str) -> str:
    """'BTC/USD' -> 'XBTUSD' and 'DOGE/USD' -> 'XDGUSD' (the archive's names); every other base is used as is."""
    found = _SYMBOL.fullmatch(symbol) if isinstance(symbol, str) else None
    if found is None:
        raise ValidationFailure("a USD symbol such as BTC/USD is required")
    base = found.group(1)
    return f"{_SYMBOL_TO_CODE.get(base, base)}USD"


def _expected_result_keys(pair_code: str) -> frozenset[str]:
    """Kraken answers with the request name, or with the legacy 'X<BASE>ZUSD' key for its three-letter old assets."""
    keys = {pair_code}
    if pair_code.endswith("USD") and len(pair_code) == 6:
        keys.add(f"X{pair_code[:3]}ZUSD")
    return frozenset(keys)


def trades_url(pair_code: str, *, since: int | None, count: int) -> str:
    if not isinstance(pair_code, str) or not _PAIR_CODE.fullmatch(pair_code):
        raise ValidationFailure("the pair code must be 2 to 24 uppercase letters or digits")
    if since is not None and (type(since) is not int or not 0 <= since <= MAX_CURSOR):
        raise ValidationFailure("since must be a non-negative integer (Unix seconds, or a nanosecond cursor)")
    if type(count) is not int or not 1 <= count <= MAX_TRADES_PER_PAGE:
        raise ValidationFailure(f"count must be between 1 and {MAX_TRADES_PER_PAGE}")
    since_part = "" if since is None else f"&since={since}"
    return f"{TRADES_URL}?pair={pair_code}{since_part}&count={count}"


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise TradesPageRefused("duplicate_keys", "Kraken trades response has duplicate JSON fields")
        result[key] = value
    return result


def _refuse_constant(_name: str) -> None:
    raise ValueError("non-finite JSON number")


def _printable(text: str) -> str:
    return "".join(character if " " <= character <= "~" else "?" for character in text)[:MAX_ERROR_CHARS]


def _refuse_errors(errors: list) -> TradesPageRefused:
    shown = tuple(_printable(error) for error in errors[:MAX_ERROR_STRINGS] if isinstance(error, str))
    for error in errors:
        if not isinstance(error, str):
            continue
        if error.startswith("EAPI:Rate limit exceeded"):
            return TradesRateLimited("rate_limited", "Kraken reported that the rate limit was exceeded",
                                     kraken_errors=shown)
        if error.startswith("EService: Throttled") or error.startswith("EService:Throttled"):
            found = _THROTTLED.match(error)
            return TradesThrottled("throttled", "Kraken throttled the request", kraken_errors=shown,
                                   retry_after_unix=None if found is None else int(found.group(1)))
    return TradesPageRefused("kraken_error", "Kraken returned an error for the trades request", kraken_errors=shown)


def _decimal_field(value: Any) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL_TEXT.fullmatch(value):
        raise TradesPageRefused("row_invalid", "a trade price or volume is not a plain decimal string")
    parsed = Decimal(value)
    if parsed <= 0:
        raise TradesPageRefused("row_invalid", "a trade price and volume must be positive")
    return parsed


def _parse_row(row: Any) -> PublicTrade:
    if not isinstance(row, list) or len(row) != 7:
        raise TradesPageRefused("row_invalid", "a trade row must hold exactly seven fields")
    price, volume, when, side, order_type, misc, trade_id = row
    if isinstance(when, bool) or not isinstance(when, (Decimal, int)) or not 0 < when < MAX_UNIX_TIME:
        raise TradesPageRefused("row_invalid", "a trade time must be a number of Unix seconds")
    when = Decimal(when)
    if type(trade_id) is not int or trade_id < 0:
        raise TradesPageRefused("row_invalid", "a trade id must be a non-negative integer")
    if not (isinstance(side, str) and isinstance(order_type, str) and isinstance(misc, str)
            and len(side) <= 4 and len(order_type) <= 4 and len(misc) <= 64):
        raise TradesPageRefused("row_invalid", "a trade side, type or misc field is not a short string")
    if -when.as_tuple().exponent > 9:
        raise TradesPageRefused("row_invalid", "a trade time has more than nine fraction digits")
    return PublicTrade(trade_id=trade_id, price=_decimal_field(price), volume=_decimal_field(volume), time=when,
                       side=side, order_type=order_type, misc=misc)


def _time_ns(value: Decimal) -> int:
    return int((value * 1_000_000_000).to_integral_value())


def parse_trades_page(raw: str, *, pair_code: str, since: int | None, count: int,
                      received_at: datetime) -> TradesPage:
    """Validate one response body. Pure: no clock, no network."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAXIMUM_RESPONSE_BYTES:
        raise TradesPageRefused("response_too_large", "Kraken trades response exceeded its byte limit")
    try:
        payload = json.loads(raw, parse_float=Decimal, parse_constant=_refuse_constant,
                             object_pairs_hook=_unique_object)
    except TradesPageRefused:
        raise
    except (ValueError, TypeError, ArithmeticError, RecursionError) as error:
        raise TradesPageRefused("invalid_json", "Kraken trades response is not valid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("error"), list):
        raise TradesPageRefused("shape", "Kraken trades response has no error list")
    if payload["error"]:
        raise _refuse_errors(payload["error"])
    result = payload.get("result")
    if not isinstance(result, dict) or len(result) != 2 or "last" not in result:
        raise TradesPageRefused("shape", "Kraken trades response must hold one pair and the cursor")
    (result_key,) = set(result) - {"last"}
    if result_key not in _expected_result_keys(pair_code):
        raise TradesPageRefused("pair_mismatch", "Kraken trades response is for a different pair than requested")
    last = result["last"]
    if not isinstance(last, str) or not _CURSOR_TEXT.fullmatch(last):
        raise TradesPageRefused("cursor_invalid", "the cursor must be a string of at most 19 digits")
    rows = result[result_key]
    if not isinstance(rows, list):
        raise TradesPageRefused("shape", "Kraken trades response rows must be a list")
    if len(rows) > min(count, MAX_TRADES_PER_PAGE):
        raise TradesPageRefused("too_many_rows", "Kraken returned more trades than requested")
    trades = tuple(_parse_row(row) for row in rows)
    for before, after in zip(trades, trades[1:], strict=False):
        if after.trade_id <= before.trade_id or after.time < before.time:
            raise TradesPageRefused("page_not_ordered", "trades must rise in id and not fall in time")
    cursor = int(last)
    if trades and abs(cursor - _time_ns(trades[-1].time)) > CURSOR_TOLERANCE_NS:
        raise TradesPageRefused("cursor_inconsistent", "the cursor does not match the time of the final trade")
    return TradesPage(pair_code=pair_code, result_key=result_key, trades=trades, last=cursor,
                      requested_since=since, requested_count=count, received_at=received_at)


class KrakenTrades:
    """Credential-free GET only. One call is one request; no retries, no pagination."""

    def __init__(self, transport: TextTransport, clock: Clock) -> None:
        self.transport = transport
        self.clock = clock

    def _now(self) -> datetime:
        now = self.clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise TradesPageRefused("clock_invalid", "the trades receipt clock must return aware times")
        return now.astimezone(UTC)

    def fetch(self, pair_code: str, *, since: int | None, count: int = MAX_TRADES_PER_PAGE) -> TradesPage:
        url = trades_url(pair_code, since=since, count=count)
        requested_at = self._now()
        raw = self.transport.get_text(url)
        # Availability is taken after the network operation, never before it.
        received_at = self._now()
        if received_at < requested_at:
            raise TradesPageRefused("receipt_clock_backwards", "the trades receipt clock moved backwards")
        return parse_trades_page(raw, pair_code=pair_code, since=since, count=count, received_at=received_at)
