"""Shared setup for the T44 news alarm tests: a state database, a budget and scripted Jev replies."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.decisions.jev import JEV_PRICE_CARD, JevAdapter, ScriptedJevHttp
from trade_graph.adapters.persistence.db import Database
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.jev_checks import JevBudgetBinding, JevCheckRunner
from trade_graph.application.news_store import NewsStore
from trade_graph.contracts.news import WatchList
from trade_graph.domain.clock import FrozenClock

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "news"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
WATCH = WatchList(held=("ETH", "EXMPL"), listed=("BTC/USD", "ETH/USD"))
FX = Decimal("0.9")


class Ticks:
    """A deterministic monotonic clock: every reading advances by ``step`` seconds."""

    def __init__(self, step: float = 0.25) -> None:
        self.value, self.step = 100.0, step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def open_state(tmp_path: Path, *, allowance: str = "5") -> tuple[Database, FrozenClock, BudgetGateway]:
    clock = FrozenClock(NOW)
    database = Database(tmp_path / "state.sqlite")
    budget = BudgetGateway(database, clock)
    budget.seed_card(JEV_PRICE_CARD)
    amount = Decimal(allowance)
    budget.configure(deployment_id="deployment", currency="EUR", total=amount, period=amount,
                     priority_reserve=Decimal("0"), daily=amount, root=amount, roles={})
    return database, clock, budget


def runner(database, clock, budget, script, *, synthetic: bool = True, on_call=None, step: float = 0.25):
    transport = ScriptedJevHttp(list(script), on_call=on_call)
    if not synthetic:
        transport.synthetic = False  # stands in for the pinned network transport in budget-path tests only
    binding = JevBudgetBinding(deployment_id="deployment", price_card_id=JEV_PRICE_CARD.price_card_id,
                               fx_rate=FX, fx_buffer=Decimal("1.02"), synthetic=synthetic, timeout_seconds=5)
    store = NewsStore(database, clock)
    return JevCheckRunner(database, clock, budget, JevAdapter(transport), binding, store,
                          monotonic=Ticks(step)), transport, store


def reply(concerns: dict[str, str], yes: str, event: str = "other", *, model: str = "jev-1.13.0",
          request_id: str | None = None, input_tokens: int | None = 420, watch: WatchList = WATCH) -> dict:
    from trade_graph.application.news_questions import EVENT_TYPES

    options = watch.subject_options()
    body = {
        "model": model,
        "answers": {
            "concerns": {"probabilities": {option: Decimal(concerns.get(option, "0")) for option in options},
                         "confidence": Decimal("0.5")},
            "event_type": {"probabilities": {name: Decimal("1" if name == event else "0") for name in EVENT_TYPES},
                           "confidence": Decimal("0.5")},
            "holder_wants_to_know": {"probabilities": {"yes": Decimal(yes), "no": 1 - Decimal(yes)}},
        },
    }
    if request_id is not None:
        body["id"] = request_id
    if input_tokens is not None:
        body["usage"] = {"input_tokens": input_tokens, "output_tokens": 0}
    return body


def table_rows(database: Database) -> dict[str, list[tuple]]:
    names = [row[0] for row in database.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: [tuple(row) for row in database.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
            for name in names}
