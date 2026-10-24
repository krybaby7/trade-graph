"""Scripted Kraken Trades pages and recorded fixtures for tests. Nothing here touches the network.

``TradeScript`` is a tiny fake exchange: a list of synthetic trades with consecutive ids, served the way Kraken
serves them (rows in id order, ``count`` rows per page, ``since`` inclusive on the page cursor, ``last`` the cursor
of the final row), so the backfill can be driven end to end with hand-computable numbers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from trade_graph.domain.clock import FrozenClock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kraken"


def recorded(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def unix(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86400 + delta.seconds


def wire_row(trade_id: int, price: str, volume: str, time: str, *, side: str = "b", kind: str = "l") -> str:
    return f'["{price}","{volume}",{time},"{side}","{kind}","",{trade_id}]'


def wire_page(rows: list[str], *, key: str = "XXBTZUSD", last: int | str = 1, errors: list[str] | None = None) -> str:
    body = ",".join(rows)
    error = json.dumps(errors or [])
    return f'{{"error":{error},"result":{{"{key}":[{body}],"last":"{last}"}}}}'


class ScriptedTransport:
    """Serves queued responses (text, or an exception to raise) and records every requested URL."""

    def __init__(self, responses: list[str | BaseException], clock: FrozenClock | None = None, *,
                 delay_seconds: float = 0) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.delay_seconds = delay_seconds
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        if self.clock is not None:
            self.clock.advance(self.delay_seconds)
        if not self.responses:
            raise AssertionError("the scripted transport ran out of responses")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def queries(self) -> list[dict[str, str]]:
        return [{key: values[0] for key, values in parse_qs(urlparse(url).query).items()} for url in self.calls]


class FakeSleeper:
    """Advances the frozen clock instead of sleeping, and remembers every wait."""

    def __init__(self, clock: FrozenClock) -> None:
        self.clock = clock
        self.waits: list[Decimal] = []

    def sleep(self, seconds: Decimal) -> None:
        self.waits.append(seconds)
        self.clock.advance(float(seconds))


# --- a fake exchange ---------------------------------------------------------------------------------------------


class SyntheticTrade:
    __slots__ = ("trade_id", "price", "volume", "time")

    def __init__(self, trade_id: int, price: str, volume: str, time: str) -> None:
        self.trade_id, self.price, self.volume, self.time = trade_id, price, volume, time

    def row(self) -> str:
        return wire_row(self.trade_id, self.price, self.volume, self.time)


def ns_of(time_text: str) -> int:
    return int(Decimal(time_text) * 1_000_000_000)


class TradeScript:
    """A fixed list of trades served page by page, with Kraken's observed ``since`` rules.

    ``since`` below 10**11 is Unix seconds, otherwise nanoseconds; both select trades with time >= since (inclusive).
    The ``last`` cursor is the final served row's time in nanoseconds, as Kraken does.
    """

    def __init__(self, trades: list[SyntheticTrade], *, key: str = "XXBTZUSD", clock: FrozenClock | None = None,
                 delay_seconds: float = 0,
                 hook: Callable[[int, str], str | BaseException | None] | None = None) -> None:
        self.trades = trades
        self.key = key
        self.clock = clock
        self.delay_seconds = delay_seconds
        self.hook = hook
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        if self.clock is not None:
            self.clock.advance(self.delay_seconds)
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        if self.hook is not None:
            overridden = self.hook(len(self.calls), url)
            if isinstance(overridden, BaseException):
                raise overridden
            if overridden is not None:
                return overridden
        since = int(query.get("since", "0"))
        count = int(query.get("count", "1000"))
        threshold = since * 1_000_000_000 if since < 10**11 else since
        chosen = [trade for trade in self.trades if ns_of(trade.time) >= threshold][:count]
        last = ns_of(chosen[-1].time) if chosen else threshold
        return wire_page([trade.row() for trade in chosen], key=self.key, last=last)


def steady_trades(first_id: int, start_unix: int, count: int, *, every_seconds: int = 60,
                  base_price_cents: int = 10_000) -> list[SyntheticTrade]:
    """``count`` trades, one every ``every_seconds`` from ``start_unix`` (integer arithmetic only)."""
    trades = []
    for index in range(count):
        cents = base_price_cents + (index * 37) % 211 - 100
        volume = f"{index % 7 + 1}.{index % 5}0000000"
        trades.append(SyntheticTrade(first_id + index, f"{cents // 100}.{cents % 100:02d}", volume,
                                     f"{start_unix + index * every_seconds}.5000000"))
    return trades
