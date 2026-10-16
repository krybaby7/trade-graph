"""Practice-range invariants over random price paths and random strategy settings (T29).

- cash and the position never go below zero (long only, no borrowing, D127);
- every fill comes after the close of the decision that placed it (no hindsight), and fills are in time order;
- the books balance exactly: ending equity = capital + sales - purchases - fees + the open position's valuation;
- whatever happens after a candle's close cannot change any fill up to that close;
- the same inputs give the same document.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.practice_range_support import bar, open_store, save

from trade_graph.application.practice_range.replay import ReplayRequest, replay
from trade_graph.contracts.practice_range import PracticeStrategy, ReplayWindow
from trade_graph.kernel.cost_profiles import load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY

WINDOW = ReplayWindow(start=date(2026, 7, 1), end=date(2026, 7, 2))
AS_OF = datetime(2026, 10, 2, tzinfo=UTC)
D = Decimal
PROFILES = ("kraken-spot-starting-tier", "kraken-spot-after-10k-volume", "okx-eea-spot")


def _candles(moves: list[int], wicks: list[int], volumes: list[int], start: int = 10_000) -> list:
    """Prices in hundredths around 100.00; each bar opens at the previous close."""
    rows = []
    price = start
    for index, (move, wick, volume) in enumerate(zip(moves, wicks, volumes, strict=True)):
        opened = price
        closed = max(100, price + move)
        high, low = max(opened, closed) + wick, max(50, min(opened, closed) - wick)
        rows.append(bar("SYN/USD", index - 1, str(D(opened) / 100), str(D(high) / 100), str(D(low) / 100),
                        str(D(closed) / 100), volume=str(volume)))
        price = closed
    return rows


paths = st.tuples(
    st.lists(st.integers(-120, 120), min_size=97, max_size=97),
    st.lists(st.integers(0, 80), min_size=97, max_size=97),
    st.lists(st.sampled_from([0, 5, 40, 300, 1000, 5000]), min_size=97, max_size=97))
settings_strategy = st.fixed_dictionaries({
    "kind": st.sampled_from(["market", "limit"]),
    "offset": st.sampled_from(["0", "0.001", "0.004", "0.01"]),
    "expires": st.integers(1, 6),
    "stop": st.sampled_from(["0.003", "0.01", "0.03"]),
    "target": st.sampled_from([None, "0.004", "0.02"]),
    "rule_exit": st.booleans(),
    "holding": st.sampled_from([None, 1, 5, 30]),
    "fraction": st.sampled_from(["0.05", "0.5", "1"]),
    "level": st.sampled_from(["99.5", "100", "100.5"]),
    "profile": st.sampled_from(PROFILES),
})


def _strategy(chosen: dict) -> PracticeStrategy:
    order: dict = {"kind": chosen["kind"], "expires_after_bars": chosen["expires"]}
    if chosen["kind"] == "limit":
        order.update(price={"candle": "close"}, offset_fraction=chosen["offset"])
    exits: dict = {"stop_loss_fraction": chosen["stop"]}
    if chosen["target"] is not None:
        exits["take_profit_fraction"] = chosen["target"]
    if chosen["rule_exit"]:
        exits["any_of"] = [{"left": {"candle": "close"}, "relation": "crosses_below",
                            "right": {"level": chosen["level"]}}]
    if chosen["holding"] is not None:
        exits["max_holding_bars"] = chosen["holding"]
    return PracticeStrategy.model_validate({
        "schema_version": 1, "strategy_id": "random-check", "version": 1, "title": "Random settings",
        "hypothesis": "Property tests only; this strategy is not meant to make sense.", "interval_minutes": 15,
        "indicators": {},
        "entry": {"all_of": [{"left": {"candle": "close"}, "relation": "crosses_above",
                              "right": {"level": chosen["level"]}}], "order": order},
        "exit": exits, "sizing": {"equity_fraction": chosen["fraction"]}})


def _run(tmp_path_factory, candles: list, chosen: dict):
    database, store = open_store(tmp_path_factory.mktemp("prop") / "market_data.sqlite")
    try:
        save(store, candles)
        request = ReplayRequest(strategy=_strategy(chosen), symbol="SYN/USD", source="synthetic", window=WINDOW,
                                data_as_of=AS_OF, profile=load_cost_profiles().get(chosen["profile"]),
                                policy=BAR_REPLAY_POLICY, capital=D("10000"))
        return replay(store, request), replay(store, request)
    finally:
        database.close()


@settings(max_examples=60, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(path=paths, chosen=settings_strategy)
def test_books_balance_and_nothing_is_borrowed_or_seen_early(tmp_path_factory, path, chosen) -> None:
    moves, wicks, volumes = path
    result, again = _run(tmp_path_factory, _candles(moves, wicks, volumes), chosen)
    assert result.to_document() == again.to_document()
    cash, held = D("10000"), D("0")
    previous = None
    sales = purchases = fees = D("0")
    for item in result.fills:
        fill = item.fill
        assert previous is None or fill.filled_at_utc >= previous
        previous = fill.filled_at_utc
        if item.purpose in ("entry", "rule_exit", "holding_limit_exit"):
            placed = int(fill.order_id.split(":")[-2])  # the close (unix seconds) of the decision that placed it
            assert fill.filled_at_utc.timestamp() >= placed
        if fill.side == "buy":
            cash -= fill.quantity * fill.price + fill.fee
            held += fill.quantity
            purchases += fill.quantity * fill.price
        else:
            cash += fill.quantity * fill.price - fill.fee
            held -= fill.quantity
            sales += fill.quantity * fill.price
        fees += fill.fee
        assert cash >= 0 and held >= 0
    assert (cash, held) == (result.ending.cash, result.ending.held)
    assert result.metrics.ending_equity == D("10000") + sales - purchases - fees + result.ending.value
    assert result.metrics.fees_paid == fees
    assert all(point.equity >= 0 for point in result.equity)


@settings(max_examples=40, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(path=paths, chosen=settings_strategy, cut=st.integers(5, 90))
def test_what_happens_after_a_close_cannot_change_the_fills_before_it(tmp_path_factory, path, chosen, cut) -> None:
    moves, wicks, volumes = path
    candles = _candles(moves, wicks, volumes)
    changed = list(candles)
    for index in range(cut + 1, len(candles)):  # wild prices for every bar after bar ``cut``
        old = candles[index]
        changed[index] = bar("SYN/USD", index - 1, "300", "400", "1", "2", volume="9999")
        assert changed[index].open_time_utc == old.open_time_utc
    cutoff = candles[cut].close_time_utc
    first, _ = _run(tmp_path_factory, candles, chosen)
    second, _ = _run(tmp_path_factory, changed, chosen)

    def before(result) -> list:
        # A fill stamped exactly at the cut may belong to the next bar's open, which was changed: compare up to it.
        return [item.to_document() for item in result.fills if item.fill.filled_at_utc < cutoff]

    assert before(first) == before(second)
    early = [point for point in first.equity if point.at <= cutoff - timedelta(minutes=1)]
    assert early == [point for point in second.equity if point.at <= cutoff - timedelta(minutes=1)]
