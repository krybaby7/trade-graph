"""The strategy lab in the installed subscription service (W11-B, C11; D290-D293).

``installed_strategy_lab(runtime)`` builds the lab worker once at start, beside the price collector and the news check:

- it reads the installation's candle file (``market_data.sqlite`` beside the database, D224) through its **own**
  read-only handle, so a long replay never holds the handle the price watcher and the hourly features read; without a
  candle file (the price collector off) it still registers strategies and studies and refuses replays with the reason;
- it writes practice-range records and its queue into the installation's state database, and records each start and
  each finished request as a ``strategy_lab`` activity (HQ's activity log: what is queued, running, done or refused,
  and the seconds spent);
- the account's own cost profile is the paper broker's (Kraken's starting tier by default), always replayed.

It never holds up the service's start: an unexpected error leaves the lab off until the next start, with its type
recorded. It needs no key, network route or paid call, and it never places, sends or cancels an order.
"""

from __future__ import annotations

from typing import Any

from trade_graph.adapters.persistence.market_data_readonly import ReadOnlyMarketData
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.strategy_lab.worker import ACTIVITY_KIND, StrategyLabWorker, account_cost_profile


class InstalledStrategyLab(StrategyLabWorker):
    """The worker with the read-only candle handle it owns; ``close`` releases it when the service ends."""

    def __init__(self, *args, reader: ReadOnlyMarketData | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reader = reader

    def close(self) -> None:
        reader, self.reader = self.reader, None
        if reader is not None:
            reader.close()


def installed_strategy_lab(runtime) -> InstalledStrategyLab | None:
    """The installed service's strategy lab, or None with the reason recorded as a ``strategy_lab`` activity."""
    ledger = getattr(runtime, "ledger", None) or runtime.execution.ledger

    def record(payload: dict[str, Any]) -> None:
        ledger._activity(runtime.portfolio_id, ACTIVITY_KIND, payload)

    reader = None
    try:
        market = getattr(runtime, "market_data", None)
        candles = None
        if market is not None:
            reader = ReadOnlyMarketData(market.path)
            candles = CandleStore(reader)
        return InstalledStrategyLab(runtime.database, runtime.clock, candles=candles,
                                    account_profile=account_cost_profile(runtime.execution), record=record,
                                    reader=reader)
    except Exception as error:  # noqa: BLE001 - the lab is never a reason the service cannot start
        if reader is not None:
            reader.close()
        try:
            reason = f"the strategy lab could not be prepared ({type(error).__name__}); it is off until the next start"
            record({"status": "not_ready", "reason": reason})
        except Exception:  # noqa: BLE001 - recording the reason is best effort here
            pass
        return None


__all__ = ["InstalledStrategyLab", "installed_strategy_lab"]
