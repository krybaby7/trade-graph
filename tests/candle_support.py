"""Shared helpers for the collector tests: recorded fixtures, table-built responses and a scripted exchange.

Nothing here touches the network. ``FakeKraken`` answers OHLC requests the way the recorded responses show Kraken
does (docs/KRAKEN-CANDLE-COLLECTION.md): 720 committed candles plus the forming one, inclusive ``since``, quiet
intervals filled flat with zero volume, and the time-0 row for a pair with no data.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from trade_graph.adapters.market.kraken_catalog import parse_asset_pairs
from trade_graph.application.pair_classification import classify_pair
from trade_graph.contracts.pair_catalog import ListedPair, PairRecord
from trade_graph.domain.clock import FrozenClock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "kraken"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
WINDOW = 720


def unix(value: datetime) -> int:
    delta = value - EPOCH
    return delta.days * 86400 + delta.seconds


def at(seconds: int) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def fixture_text(name: str) -> str:
    return (FIXTURES / f"ohlc_{name}.json").read_text(encoding="utf-8")


def manifest() -> dict:
    return json.loads((FIXTURES / "ohlc_manifest.json").read_text(encoding="utf-8"))


def recorded_clock(name: str, delay_seconds: int = 0) -> FrozenClock:
    """A clock at the moment the recorded response finished arriving (plus an optional delay)."""
    finished = datetime.fromisoformat(manifest()[name]["request_finished_utc"])
    return FrozenClock(finished + timedelta(seconds=delay_seconds))


def listed_pairs() -> dict[str, ListedPair]:
    raw = (FIXTURES / "asset_pairs_2026-10-13.json").read_text(encoding="utf-8")
    return {pair.symbol: pair for pair in parse_asset_pairs(raw).pairs}


def pair_records() -> dict[str, PairRecord]:
    return {symbol: classify_pair(pair) for symbol, pair in listed_pairs().items()}


# --- rows and responses built from a table -------------------------------------------------------------------------


def row(open_time: int, *, price: str = "100.0", quiet: bool = False, count: int = 7) -> list:
    if quiet:
        return [open_time, price, price, price, price, "0.0", "0.00000000", 0]
    return [open_time, price, "101.5", "99.5", "100.5", "100.2", "1.25000000", count]


def wire(key: str, rows: list, *, last: int | None = None, error: list | None = None) -> str:
    cursor = last if last is not None else (rows[-2][0] if len(rows) > 1 else 0)
    return json.dumps({"error": error or [], "result": {key: rows, "last": cursor}})


def error_wire(*messages: str) -> str:
    return json.dumps({"error": list(messages)})


def grid_rows(interval: int, first_open: int, count: int, **row_options) -> list:
    return [row(first_open + index * interval * 60, **row_options) for index in range(count)]


class QueueTransport:
    """Returns the queued responses in order. The clock moves on after each call, as in a slow network."""

    def __init__(self, responses: list, clock: FrozenClock, *, delay_seconds: float = 0) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.delay_seconds = delay_seconds
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        self.clock.advance(self.delay_seconds)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


# --- a scripted exchange -------------------------------------------------------------------------------------------


def _price_for(open_time: int, interval: int) -> int:
    return 100 + (open_time // (interval * 60)) % 40


class FakeKraken:
    """Answers ``/0/public/OHLC`` for the given pairs from the clock; other URLs are refused."""

    def __init__(self, clock: FrozenClock, pairs: Iterable[ListedPair], *, delay_seconds: float = 1,
                 quiet_every: dict[str, int] | None = None, listed_at: dict[str, int] | None = None,
                 no_data: Iterable[str] = ()) -> None:
        self.clock = clock
        self.delay_seconds = delay_seconds
        self.keys = {pair.source_pair: (pair.base + pair.quote if pair.source_pair == "XBTUSD" else pair.source_pair)
                     for pair in pairs}
        self.quiet_every = quiet_every or {}
        self.listed_at = listed_at or {}
        self.no_data = set(no_data)
        self.script: dict[tuple[str, int], list] = {}
        self.revised: dict[tuple[str, int, int], list] = {}
        self.calls: list[str] = []
        self.requests: list[tuple[str, int, int | None]] = []
        self.before: Callable[[str, int], None] | None = None

    def queue(self, source_pair: str, interval: int, *responses) -> None:
        self.script.setdefault((source_pair, interval), []).extend(responses)

    def _row(self, source_pair: str, interval: int, open_time: int) -> list:
        revised = self.revised.get((source_pair, interval, open_time))
        if revised is not None:
            return revised
        every = self.quiet_every.get(source_pair)
        slot = open_time // (interval * 60)
        if every and slot % every == 0:
            return row(open_time, price=f"{_price_for(open_time - interval * 60, interval)}.0", quiet=True)
        base = _price_for(open_time, interval)
        return [open_time, f"{base}.0", f"{base + 2}.0", f"{base - 1}.0", f"{base + 1}.0", f"{base}.5", "2.50000000", 9]

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        assert parsed.path == "/0/public/OHLC", url
        query = parse_qs(parsed.query)
        source_pair, interval = query["pair"][0], int(query["interval"][0])
        since = int(query["since"][0]) if "since" in query else None
        self.requests.append((source_pair, interval, since))
        if self.before is not None:
            self.before(source_pair, interval)
        queued = self.script.get((source_pair, interval))
        issued_at = self.clock.now()
        self.clock.advance(self.delay_seconds)
        if queued:
            response = queued.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        if source_pair not in self.keys:
            return error_wire("EQuery:Unknown asset pair")
        step = interval * 60
        forming = unix(issued_at) - unix(issued_at) % step
        if source_pair in self.no_data:
            return wire(self.keys[source_pair], [[0, "0.00000", "0.00000", "0.00000", "0.00000", "0.00000",
                                                   "0.00000000", 0]], last=0)
        first = forming - WINDOW * step
        if since is not None:
            first = max(first, since - since % step)
        if source_pair in self.listed_at:
            first = max(first, self.listed_at[source_pair] - self.listed_at[source_pair] % step)
        opens = list(range(first, forming + 1, step)) or [forming]  # the forming row is always present
        return wire(self.keys[source_pair], [self._row(source_pair, interval, value) for value in opens],
                    last=forming - step)
