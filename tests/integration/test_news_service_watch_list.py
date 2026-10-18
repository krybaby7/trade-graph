"""The installed news check's watch list (W9-A, D257): held, today's focus, the owner's extra and the mandate's coins,
bounded at what a Jev question can name, with held, focus and extra coins first. Synthetic account; no network."""

from __future__ import annotations

from datetime import timedelta

from tests.day_plan_support import day_plan_account

from trade_graph.application.events.store import WakeStore
from trade_graph.application.news_watchlist import service_watch_list, watch_list
from trade_graph.contracts.news import MAX_WATCHED_COINS
from trade_graph.contracts.wakes import FocusSelection


def _focus(account, symbols) -> None:
    day = account.clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    WakeStore(account.database, account.clock).record_focus(
        portfolio_id=account.portfolio, day_start=day, day_end=day + timedelta(days=1), scan_id=None,
        selection=FocusSelection.model_validate({"coins": [{"symbol": symbol, "note": "n"} for symbol in symbols],
                                                 "summary": "Synthetic focus."}), wake_id=None, task_id=None)


def test_todays_focus_coins_join_the_watch_list(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    plain = watch_list(account.database, account.clock, account.portfolio)
    _focus(account, ["DOT/USD"])
    watch, left_out = service_watch_list(account.database, account.clock, account.portfolio, ("EXMPL",))
    assert watch.focus == ("DOT",) and "EXMPL" in watch.listed and left_out == 0
    assert set(plain.coins()) | {"DOT", "EXMPL"} == set(watch.coins())


def test_too_many_coins_keep_held_focus_and_extra_first_and_count_the_rest(tmp_path) -> None:
    account = day_plan_account(tmp_path)
    _focus(account, ["DOT/USD"])
    listed = watch_list(account.database, account.clock, account.portfolio).listed
    assert listed  # the synthetic mandate's coins
    extra = tuple(f"X{number:03d}" for number in range(MAX_WATCHED_COINS - 1))  # with DOT: the bound exactly
    watch, left_out = service_watch_list(account.database, account.clock, account.portfolio, extra)
    assert len(watch.coins()) == MAX_WATCHED_COINS and watch.focus == ("DOT",)
    assert set(extra) <= set(watch.coins())
    assert left_out == len(set(listed) - set(extra) - {"DOT"})
