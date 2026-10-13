"""Records and vocabulary shared by the Trades backfill loop and its report (docs/KRAKEN-TRADES-BACKFILL.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trade_graph.adapters.market.kraken_trades import PublicTrade
from trade_graph.application.trade_aggregation import TradeAggregator
from trade_graph.contracts.candles import from_unix
from trade_graph.domain.clock import utc_iso

TRADES_SOURCE = "kraken_public_trades"
ARCHIVE_SOURCE = "kraken_ohlcvt_archive"
COLLECTOR_SOURCE = "kraken_public_ohlc"
STOP_REASONS = (
    "end_reached", "max_requests", "stream_exhausted", "rate_limited", "rate_limit_wait_too_long", "transport_error",
    "page_refused", "cursor_stalled", "cursor_regressed", "boundary_mismatch", "time_regressed", "store_refused",
    "interrupted", "unexpected_error", "no_target", "already_covered",
)
FAILURE_REASONS = frozenset(STOP_REASONS) - {"end_reached", "max_requests", "stream_exhausted", "no_target",
                                            "already_covered"}


def iso(seconds: int | None) -> str | None:
    return None if seconds is None else utc_iso(from_unix(seconds))


@dataclass(frozen=True, slots=True)
class Target:
    """The range of one pair and interval. ``state`` is ``ready``, ``no_target`` or ``already_covered``."""

    symbol: str
    interval: int
    start: int | None
    end: int | None
    start_source: str | None
    end_source: str | None
    state: str
    reason: str | None

    @property
    def step(self) -> int:
        return self.interval * 60

    @property
    def slots(self) -> int:
        return 0 if self.start is None or self.end is None else max(0, (self.end - self.start) // self.step)


@dataclass
class IntervalOutcome:
    built: int = 0
    inserted: int = 0
    duplicates: int = 0
    revised: int = 0
    withheld_gap: int = 0
    withheld_receipt: int = 0


@dataclass
class Budget:
    maximum: int
    used: int = 0
    rate_limited: int = 0
    throttled: int = 0
    transport_errors: int = 0

    @property
    def spent(self) -> bool:
        return self.used >= self.maximum


@dataclass
class PairState:
    """Everything one pair's run knows: targets, the aggregator, the page bookkeeping and why it stopped."""

    symbol: str
    pair_code: str
    targets: dict[int, Target]
    cursors: dict[int, int | None]
    aggregator: TradeAggregator | None = None
    committed: dict[int, int] = field(default_factory=dict)
    outcomes: dict[int, IntervalOutcome] = field(default_factory=dict)
    stop_reason: str = "end_reached"
    message: str | None = None
    refusal_reason: str | None = None
    requests: int = 0
    pages: int = 0
    received: int = 0
    used: int = 0
    duplicates: int = 0
    unexpected_overlap: int = 0
    gap_count: int = 0
    gap_examples: list[dict[str, Any]] = field(default_factory=list)
    first_trade: PublicTrade | None = None
    last_trade: PublicTrade | None = None
    tail: dict[int, PublicTrade] = field(default_factory=dict)
    last_cursor: int | None = None
    resume_since: int | None = None
    not_started: bool = False

    def stop(self, reason: str, message: str | None = None, refusal: str | None = None) -> None:
        self.stop_reason, self.message, self.refusal_reason = reason, message, refusal
