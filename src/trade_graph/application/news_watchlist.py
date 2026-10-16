"""The news alarm's watch list, read from the paper account: held coins and the mandate's coins (T44).

Read only. The day's focus coins join once T37 exists; until then the owner may name extra coins to watch
in the news alarm settings.
"""

from __future__ import annotations

from decimal import Decimal

from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Mandate
from trade_graph.contracts.news import WatchList, coin_code
from trade_graph.domain.clock import Clock


def _codes(symbols) -> tuple[str, ...]:
    codes = []
    for symbol in symbols:
        try:
            codes.append(coin_code(symbol))
        except ValueError:
            continue  # a symbol the alarm cannot name is left out of the question, not guessed
    return tuple(codes)


def watch_list(database, clock: Clock, portfolio_id: str | None, extra: tuple[str, ...] = (),
               focus: tuple[str, ...] = ()) -> WatchList:
    held: tuple[str, ...] = ()
    listed: tuple[str, ...] = ()
    if portfolio_id is not None:
        books = Ledger(database, clock).books(portfolio_id)
        held = _codes(lot.asset for lot in books.lots if lot.open_quantity() > Decimal("0"))
        row = database.execute("""SELECT document_json FROM mandates WHERE portfolio_id = ? AND active = 1
            ORDER BY revision DESC LIMIT 1""", (portfolio_id,)).fetchone()
        if row is not None:
            listed = _codes(Mandate.model_validate_json(row["document_json"]).symbols)
    return WatchList(held=held, listed=(*listed, *extra), focus=focus)
