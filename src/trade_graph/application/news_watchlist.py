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


def _focus_today(database, clock: Clock) -> tuple[str, ...]:
    """Today's focus coins (T37), read from the saved selections; none when there is no selection or no table."""
    from trade_graph.contracts.wakes import FocusSelection
    from trade_graph.domain.clock import utc_iso

    at = utc_iso(clock.now())
    try:
        rows = database.execute(
            "SELECT document_json FROM focus_selections WHERE trading_day_start <= ? AND trading_day_end > ? "
            "ORDER BY sequence DESC LIMIT 16", (at, at)).fetchall()
    except Exception:  # noqa: BLE001 - an older database without focus selections watches its other coins
        return ()
    coins: list[str] = []
    for row in rows:
        try:
            coins.extend(FocusSelection.model_validate_json(row["document_json"]).symbols())
        except ValueError:
            continue
    return _codes(coins)


def service_watch_list(database, clock: Clock, portfolio_id: str | None,
                       extra: tuple[str, ...] = ()) -> tuple[WatchList, int]:
    """The installed service's watch list (W9-A, D257): held coins, today's focus coins, the owner's extra coins and
    the mandate's coins. When they are more than a Jev question can name (``MAX_WATCHED_COINS``), held, focus and extra
    coins come first and the mandate's coins fill the rest in sorted order; the second value counts those left out."""
    from trade_graph.contracts.news import MAX_WATCHED_COINS

    focus = _focus_today(database, clock)
    try:
        return watch_list(database, clock, portfolio_id, extra, focus=focus), 0
    except ValueError:
        pass
    held: tuple[str, ...] = ()
    listed: tuple[str, ...] = ()
    if portfolio_id is not None:
        books = Ledger(database, clock).books(portfolio_id)
        held = _codes(lot.asset for lot in books.lots if lot.open_quantity() > Decimal("0"))
        row = database.execute("""SELECT document_json FROM mandates WHERE portfolio_id = ? AND active = 1
            ORDER BY revision DESC LIMIT 1""", (portfolio_id,)).fetchone()
        if row is not None:
            listed = _codes(Mandate.model_validate_json(row["document_json"]).symbols)
    first = sorted({*held, *focus, *_codes(extra)})[:MAX_WATCHED_COINS]
    keep = set(first)
    held = tuple(coin for coin in sorted(set(held)) if coin in keep)
    focus = tuple(coin for coin in sorted(set(focus)) if coin in keep)
    others = sorted(set(listed) - keep)
    room = MAX_WATCHED_COINS - len(keep)
    kept_extra = tuple(coin for coin in first if coin not in held and coin not in focus)
    return WatchList(held=held, listed=(*kept_extra, *others[:room]), focus=focus), max(0, len(others) - room)
