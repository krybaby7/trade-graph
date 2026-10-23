"""Bounded public Kraken quotes and EUR reference FX for commissioned live mode."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from trade_graph.adapters.market.paper_feed import PublicPaperFeed
from trade_graph.adapters.market.public import HttpxTextTransport
from trade_graph.domain.errors import AuthorityDenied


class LivePublicTextTransport(HttpxTextTransport):
    def __init__(self, proxy: str):
        super().__init__(timeout=5, proxy=proxy, trust_env=False)

    def get_text(self, url):
        parsed = urlsplit(url)
        allowed = (parsed.hostname == "api.kraken.com" and parsed.path in {
            "/0/public/Assets", "/0/public/AssetPairs", "/0/public/Ticker", "/0/public/OHLC"}
            or parsed.hostname == "api.frankfurter.dev"
            and parsed.path == "/v2/providers/ecb/rate/usd/eur")
        if (parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.username or parsed.password
                or parsed.fragment or not allowed):
            raise AuthorityDenied("live public fetch is outside protected Kraken/FX origins")
        return super().get_text(url)


class PublicLiveFeed(PublicPaperFeed):
    def poll(self):
        now = self.clock.now()
        if self.next_poll is not None and now < self.next_poll:
            return []
        observations = []
        for symbol in self.symbols:
            quote = self.rest.fetch_ticker(symbol, observation_id=str(uuid.uuid4()), available_at=now)
            received = self.clock.now()
            quote = quote.model_copy(update={"event_time_utc": received, "available_at_utc": received,
                                             "source": "kraken_public_rest:receipt_time"})
            if quote.bid is not None and quote.ask is not None:
                base, currency = quote.symbol.split("/")
                for pid in self.portfolio_ids:
                    self.execution.ledger.observe_mark(pid, base, (quote.bid + quote.ask) / Decimal(2),
                                                        currency, source=quote.source)
            observations.append(quote)
        if self.next_fx is None or now >= self.next_fx:
            self._reference_fx()
            self.next_fx = now + timedelta(hours=1)
        self.next_poll = self.clock.now() + timedelta(seconds=self.interval_seconds)
        return observations
