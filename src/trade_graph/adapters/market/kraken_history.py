"""One bounded public Kraken OHLC request per supported USD spot pair.

Kraken returns at most 720 entries and always includes its uncommitted candle
as the final array entry, even with ``since``. Discard that entry unconditionally.
The receipt timestamp describes when this process obtained the historical data;
it is never inferred from a candle's exchange time.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.adapters.market.public import KRAKEN_REST, TextTransport
from trade_graph.contracts.price_history import HourlyCandle
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal, parse_decimal

SUPPORTED_SYMBOLS = ("BTC/USD", "ETH/USD")
MAXIMUM_HISTORY_HOURS = 719
MAXIMUM_RESPONSE_BYTES = 1_048_576
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PAIRS = {"BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD"}
_RESPONSE_ALIASES = {
    "BTC/USD": {"XXBTZUSD", "XBTUSD", "BTCUSD", "BTC/USD", "XBT/USD"},
    "ETH/USD": {"XETHZUSD", "ETHUSD", "ETH/USD"},
}


def validate_history_hours(hours: int) -> None:
    if type(hours) is not int or not 1 <= hours <= MAXIMUM_HISTORY_HOURS:
        raise ValidationFailure("hourly history requires between 1 and 719 completed hours")


def _utc_now(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationFailure("hourly history requires an aware receipt clock")
    return now.astimezone(UTC)


def _unix(value: datetime) -> int:
    elapsed = value - _EPOCH
    return elapsed.days * 86400 + elapsed.seconds


def _timestamp(value: object) -> datetime:
    if type(value) is not int or value < 0 or value % 3600:
        raise ValidationFailure("Kraken hourly candle requires an aligned integer timestamp")
    try:
        return _EPOCH + timedelta(seconds=value)
    except (ValueError, OverflowError) as exc:
        raise ValidationFailure("Kraken hourly candle timestamp is out of range") from exc


def _values(row: list) -> tuple[Decimal, ...]:
    try:
        values = tuple(parse_decimal(value) for value in row[1:7])
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValidationFailure("Kraken hourly candle requires finite exact decimals") from exc
    # A short exponential wire value must not expand to an unbounded fixed-point
    # string during hashing or persistence. Refuse excessive precision/range;
    # do not round source values to fit the limit.
    for value in values:
        _sign, digits, exponent = value.as_tuple()
        if max(len(digits) + exponent, 1) + max(-exponent, 0) > 128:
            raise ValidationFailure("Kraken hourly decimal exceeded its fixed-point digit bound")
    opening, high, low, closing, vwap, volume = values
    if (min(opening, high, low, closing, vwap) <= 0 or volume < 0
            or high < max(opening, low, closing, vwap) or low > min(opening, high, closing, vwap)):
        raise ValidationFailure("Kraken hourly candle price or volume bounds are invalid")
    return values


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationFailure("Kraken hourly response has duplicate JSON fields")
        result[key] = value
    return result


class KrakenHourlyHistory:
    """Credential-free GET only; no retries, pagination or arbitrary symbols."""

    def __init__(self, transport: TextTransport, clock: Clock) -> None:
        self.transport = transport
        self.clock = clock

    def fetch(self, symbol: str, *, hours: int) -> list[HourlyCandle]:
        validate_history_hours(hours)
        if not isinstance(symbol, str) or symbol not in SUPPORTED_SYMBOLS:
            raise ValidationFailure("Kraken hourly history supports BTC/USD and ETH/USD only")
        requested_at = _utc_now(self.clock)
        requested_start = requested_at.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)
        pair = _PAIRS[symbol]
        url = f"{KRAKEN_REST}/OHLC?pair={pair}&interval=60&since={_unix(requested_start)}"
        raw = self.transport.get_text(url)
        # Take availability after the network operation. A delayed response cannot
        # be admitted to a context retained before its receipt.
        received_at = _utc_now(self.clock)
        if received_at < requested_at:
            raise ValidationFailure("Kraken hourly receipt clock moved backwards")
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAXIMUM_RESPONSE_BYTES:
            raise ValidationFailure("Kraken hourly response exceeded its byte limit")
        try:
            payload = json.loads(raw, parse_float=Decimal, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            raise ValidationFailure("Kraken hourly response JSON is invalid") from exc
        if not isinstance(payload, dict) or payload.get("error") != []:
            raise ValidationFailure("Kraken hourly response has missing or rejected error status")
        result = payload.get("result")
        if not isinstance(result, dict) or len(result) != 2 or "last" not in result:
            raise ValidationFailure("Kraken hourly response must identify one pair and its cursor")
        cursor = result["last"]
        if type(cursor) is not int or cursor < 0:
            raise ValidationFailure("Kraken hourly response cursor must be a nonnegative integer")
        pair_keys = set(result) - {"last"}
        if not pair_keys <= _RESPONSE_ALIASES[symbol]:
            raise ValidationFailure("Kraken hourly response differs from the requested pair")
        rows = result[next(iter(pair_keys))]
        if not isinstance(rows, list) or len(rows) > 720:
            raise ValidationFailure("Kraken hourly response exceeded its candle bound")
        if rows and (not isinstance(rows[-1], list) or len(rows[-1]) != 8):
            raise ValidationFailure("Kraken hourly final candle structure is invalid")
        # The final row is deliberately not inspected for completion, ordering or
        # value quality: Kraken declares it uncommitted independently of since.
        committed_rows = rows[:-1]
        retained_start = received_at.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours)
        candles: list[HourlyCandle] = []
        previous_open: datetime | None = None
        for row in committed_rows:
            if not isinstance(row, list) or len(row) != 8:
                raise ValidationFailure("Kraken hourly candle structure is invalid")
            opened_at = _timestamp(row[0])
            try:
                closes_at = opened_at + timedelta(hours=1)
            except OverflowError as exc:
                raise ValidationFailure("Kraken hourly candle close timestamp is out of range") from exc
            if previous_open is not None and opened_at <= previous_open:
                raise ValidationFailure("Kraken hourly candles must have unique increasing timestamps")
            previous_open = opened_at
            if closes_at > received_at:
                raise ValidationFailure("Kraken nonfinal hourly candle is incomplete or future-dated")
            prices = _values(row)
            count = row[7]
            if type(count) is not int or count < 0:
                raise ValidationFailure("Kraken hourly trade count must be a nonnegative integer")
            canonical_row = [row[0], *(canonical_decimal(value) for value in prices), count]
            digest = hashlib.sha256(json.dumps(canonical_row, separators=(",", ":")).encode("utf-8")).hexdigest()
            if opened_at < retained_start:
                continue
            candles.append(HourlyCandle(
                symbol=symbol,
                interval_minutes=60,
                open_time_utc=opened_at,
                close_time_utc=closes_at,
                available_at_utc=received_at,
                open=prices[0], high=prices[1], low=prices[2], close=prices[3], vwap=prices[4], volume=prices[5],
                trade_count=count,
                source="kraken_public_ohlc",
                source_ref=f"kraken:/0/public/OHLC:{pair}:60:{row[0]}",
                source_hash=digest,
            ))
        return candles
