"""Explicitly enabled public REST observations for a simulated paper venue."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.adapters.market.public import FrankfurterClient, HttpxTextTransport, KrakenPublicRest
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal


class PublicPaperFeed:
    """No exchange keys, private endpoints or orders; fills remain paper assumptions.

    The synchronous poll runs in the service's independent feed thread. A REST
    ticker has no exchange event timestamp: its receipt time is identified as
    such in the source, and slow requests never backdate it into a decision.
    """

    def __init__(self, execution, portfolio_ids: list[str], symbols: list[str], *, transport=None,
                 interval_seconds: int = 10) -> None:
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
        self.diagnostic_context = {"stage": "poll", "symbol": None}

    def poll(self) -> list:
        now = self.clock.now()
        if self.next_poll is not None and now < self.next_poll:
            return []
        self.diagnostic_context = {"stage": "poll", "symbol": None}
        # Only successful acquisition advances the poll cursor. A failed poll
        # remains degraded and cannot silently renew old observations or marks.
        if self.next_metadata is None or now >= self.next_metadata:
            self.diagnostic_context = {"stage": "metadata", "symbol": None}
            for rules in self.rest.fetch_instruments(self.symbols):
                self.execution.register_instrument(rules)
                self.execution.register_instrument(rules.model_copy(update={"venue": "paper", "synthetic": True}))
            self.next_metadata = now + timedelta(hours=1)
        observations = []
        for symbol in self.symbols:
            self.diagnostic_context = {"stage": "ticker", "symbol": symbol}
            # Actual receipt time is supplied only after the HTTP transport returns.
            quote = self.rest.fetch_ticker(symbol, observation_id=str(uuid.uuid4()), available_at=now)
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
            observations.append(paper)
        if self.next_fx is None or now >= self.next_fx:
            self.diagnostic_context = {"stage": "reference_fx", "symbol": None}
            self._reference_fx()
            self.next_fx = now + timedelta(hours=1)
        self.next_poll = self.clock.now() + timedelta(seconds=self.interval_seconds)
        self.diagnostic_context = {"stage": "poll", "symbol": None}
        return observations

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
