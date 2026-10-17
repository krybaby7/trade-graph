"""Explicitly enabled public REST observations for a simulated paper venue."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.adapters.market.public import FrankfurterClient, HttpxTextTransport, KrakenPublicRest
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal

FX_RETRY_SECONDS = 300  # retry an unavailable reference rate sooner than the hourly refresh
# The day's coins (W6-D, D223) are quoted together in one extra request per poll; this bounds one request's pair list.
DAY_COINS_PER_POLL = 16


class PublicPaperFeed:
    """No exchange keys, private endpoints or orders; fills remain paper assumptions.

    The synchronous poll runs in the service's independent feed thread. A REST
    ticker has no exchange event timestamp: its receipt time is identified as
    such in the source, and slow requests never backdate it into a decision.
    """

    def __init__(self, execution, portfolio_ids: list[str], symbols: list[str], *, transport=None,
                 interval_seconds: int = 10, extra_symbols: Callable[[], Iterable[str]] | None = None) -> None:
        if type(interval_seconds) is not int or interval_seconds < 1:
            raise ValueError("positive integer public poll interval required")
        self.execution, self.clock = execution, execution.clock
        self.portfolio_ids, self.symbols = list(portfolio_ids), list(symbols)
        self.transport = transport or HttpxTextTransport(timeout=5)
        self.rest = KrakenPublicRest(self.transport)
        self.fx = FrankfurterClient(self.transport)
        self.interval_seconds = interval_seconds
        self.next_poll = None
        self.next_metadata = None
        self.next_fx = None
        self.reference_fx_error: str | None = None
        self.diagnostic_context = {"stage": "poll", "symbol": None}
        # W6-D (D223): with the event engine, the coins the Trader plans, holds or watches today are quoted too, in
        # one request of their own. Its failure never fails the feed: the configured coins keep their quotes.
        self.extra_symbols = extra_symbols
        self.extra_error: str | None = None
        self._extra_known: set[str] = set()

    def poll(self) -> list:
        now = self.clock.now()
        if self.next_poll is not None and now < self.next_poll:
            return []
        self.diagnostic_context = {"stage": "poll", "symbol": None}
        # Only successful acquisition advances the poll cursor. A failed poll
        # remains degraded and cannot silently renew old observations or marks.
        if self.next_metadata is None or now >= self.next_metadata:
            self.diagnostic_context = {"stage": "metadata", "symbol": None}
            self._register(self.rest.fetch_instruments(self.symbols))
            self.next_metadata = now + timedelta(hours=1)
            self._extra_known.clear()  # the day's coins' rules are refreshed hourly too
        observations = []
        for symbol in self.symbols:
            self.diagnostic_context = {"stage": "ticker", "symbol": symbol}
            # Actual receipt time is supplied only after the HTTP transport returns.
            quote = self.rest.fetch_ticker(symbol, observation_id=str(uuid.uuid4()), available_at=now)
            observations.append(self._record(quote))
        observations += self._day_coins(now)
        if self.next_fx is None or now >= self.next_fx:
            self.diagnostic_context = {"stage": "reference_fx", "symbol": None}
            self._refresh_reference_fx(now)
        self.next_poll = self.clock.now() + timedelta(seconds=self.interval_seconds)
        self.diagnostic_context = {"stage": "poll", "symbol": None}
        return observations

    def _register(self, instruments) -> None:
        for rules in instruments:
            self.execution.register_instrument(rules)
            self.execution.register_instrument(rules.model_copy(update={"venue": "paper", "synthetic": True}))

    def _record(self, quote):
        received = self.clock.now()
        quote = quote.model_copy(update={"event_time_utc": received, "available_at_utc": received,
                                         "source": "kraken_public_rest:receipt_time"})
        self.execution.save_observation(quote)
        paper = quote.model_copy(update={"observation_id": str(uuid.uuid4()), "venue": "paper",
                                         "source": "kraken_public_rest:paper_reference:receipt_time"})
        if paper.bid is not None and paper.ask is not None:
            base, currency = paper.symbol.split("/")
            price = (paper.bid + paper.ask) / Decimal("2")
            for pid in self.portfolio_ids:
                self.execution.ledger.observe_mark(pid, base, price, currency, source=paper.source)
        return paper

    def _day_coins(self, now: datetime) -> list:
        """Quote the day's coins in one request; a failure is recorded and never fails the configured coins."""
        if self.extra_symbols is None:
            return []
        try:
            wanted = list(dict.fromkeys(symbol for symbol in self.extra_symbols()
                                        if isinstance(symbol, str) and symbol not in self.symbols))
        except Exception:  # noqa: BLE001 - the saved records are read again at the next poll
            wanted = []
        wanted = wanted[:DAY_COINS_PER_POLL]
        if not wanted:
            return []
        self.diagnostic_context = {"stage": "day_coins", "symbol": None}
        try:
            new = [symbol for symbol in wanted if symbol not in self._extra_known]
            if new:
                self._register(self.rest.fetch_instruments(new))
                self._extra_known.update(new)
            quotes = self.rest.fetch_tickers(wanted, observation_ids=[str(uuid.uuid4()) for _ in wanted],
                                             available_at=now)
            observations = [self._record(quote) for quote in quotes]
        except Exception as exc:  # noqa: BLE001 - recorded below; the configured coins' quotes stand
            self._day_coins_health(type(exc).__name__, wanted)
            return []
        self._day_coins_health(None, wanted)
        return observations

    def _day_coins_health(self, error: str | None, symbols: list[str]) -> None:
        if error == self.extra_error:
            return
        self.extra_error = error
        payload = {"status": "degraded" if error else "recovered", "error_type": error, "symbols": symbols,
                   "observed_at_utc": utc_iso(self.clock.now()), "affects": "quotes of the day's coins only"}
        for pid in self.portfolio_ids:
            self.execution.ledger._activity(pid, "public_feed_day_coins", payload)

    def _refresh_reference_fx(self, now: datetime) -> None:
        try:
            self._reference_fx()
        except Exception as exc:
            # The EUR reference rate only labels reporting; its outage must not discard
            # quotes or block USD buys (owner, 2026-10-13). Retry sooner than hourly.
            self.reference_fx_error = type(exc).__name__
            self.next_fx = now + timedelta(seconds=FX_RETRY_SECONDS)
        else:
            self.reference_fx_error = None
            self.next_fx = now + timedelta(hours=1)

    def _reference_fx(self) -> None:
        rate = self.fx.reference_rate("USD", "EUR")
        retrieved = self.clock.now()
        valid = datetime.fromisoformat(rate.rate_date).replace(tzinfo=UTC)
        if valid.date() > retrieved.date():
            raise ValueError("future public reference FX date")
        # Reference data from the last business day keeps its original date.
        # A carried weekend/holiday reference is explicitly stale/provisional.
        stale = valid.date() != retrieved.date()
        with self.execution.database.immediate() as connection:
            connection.execute(
                """INSERT INTO fx_rates
                (rate_id, base, quote, rate, source, observed_at, valid_as_of, retrieved_at, kind, stale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reference', ?)""",
                (str(uuid.uuid4()), rate.base, rate.quote, canonical_decimal(rate.rate), rate.source,
                 utc_iso(valid), utc_iso(valid), utc_iso(retrieved), int(stale)),
            )

    def close(self) -> None:
        return None
