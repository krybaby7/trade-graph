"""Partial fills on the instrument's lot (W4-A regression for a W1-B quote-path bug; synthetic quotes and bars).

Before the fix a crossed resting limit on a quote without a volume figure filled half of what remained each time,
never rounded to the quantity increment, so the digits grew without end and the books eventually refused the fill
(``decimal.Inexact``). Given the instrument's increment and minimum, the engine now rounds every partial fill down
to the lot; when the rounded share would be below the minimum it finishes the order (no volume figure: the share is
the engine's own assumption) or fills nothing yet (a volume figure that did not trade one lot). Without an increment
the T05 behaviour is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_graph.contracts.candles import Candle
from trade_graph.contracts.fills import SimOrder
from trade_graph.contracts.models import Observation
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, load_cost_profiles
from trade_graph.kernel.fills import (
    BAR_REPLAY_POLICY,
    PAPER_QUOTE_POLICY,
    fill_order_on_candle,
    fill_order_on_observation,
)

D = Decimal
PLACED = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
KRAKEN = load_cost_profiles().get(KRAKEN_STARTING)
LOT = {"quantity_increment": D("0.0001"), "min_quantity": D("0.0001")}


def _limit(remaining: str, *, quantity: str = "13", price: str = "99") -> SimOrder:
    return SimOrder(order_id="entry", symbol="SOL/USD", side="buy", kind="limit", quantity=D(quantity),
                    remaining=D(remaining), limit_price=D(price), eligible_after_utc=PLACED)


def _market(remaining: str) -> SimOrder:
    return SimOrder(order_id="entry-m", symbol="SOL/USD", side="buy", kind="market", quantity=D("13"),
                    remaining=D(remaining), eligible_after_utc=PLACED)


def _quote(*, volume: str | None = None, size: str = "1000") -> Observation:
    return Observation(observation_id="q", venue="paper", symbol="SOL/USD", event_time_utc=PLACED,
                       available_at_utc=PLACED, bid="98.9", ask="99", bid_size=size, ask_size=size, volume=volume,
                       kind="quote", source="synthetic")


def _fill(order: SimOrder, observation: Observation, **lot):
    return fill_order_on_observation(order, observation, profile=KRAKEN, policy=PAPER_QUOTE_POLICY, **lot)


def test_without_a_volume_figure_each_partial_fill_is_rounded_down_to_the_lot() -> None:
    fill = _fill(_limit("0.8125"), _quote(), **LOT)
    assert fill is not None and fill.quantity == D("0.4062")  # half is 0.40625, rounded down to 0.0001
    assert fill.heuristic is True


def test_without_a_volume_figure_the_order_finishes_when_half_would_be_below_the_minimum() -> None:
    fill = _fill(_limit("0.0003"), _quote(), **{"quantity_increment": D("0.0001"), "min_quantity": D("0.0002")})
    assert fill is not None and fill.quantity == D("0.0003")
    last = _fill(_limit("0.0001"), _quote(), **LOT)
    assert last is not None and last.quantity == D("0.0001")


def test_a_volume_figure_that_did_not_trade_one_minimum_lot_fills_nothing_yet() -> None:
    assert _fill(_limit("13"), _quote(volume="0.00005"), **LOT) is None
    assert _fill(_limit("13"), _quote(volume="0.00015"), **{"quantity_increment": D("0.0001"),
                                                            "min_quantity": D("0.0002")}) is None
    fill = _fill(_limit("13"), _quote(volume="0.12345"), **LOT)
    assert fill is not None and fill.quantity == D("0.1234")


def test_a_share_that_covers_the_remainder_fills_it_exactly_even_off_the_lot() -> None:
    # A remainder left off the lot by an earlier, unrounded fill still finishes, and is never rounded away.
    fill = _fill(_limit("0.00000390625"), _quote(volume="5"), **LOT)
    assert fill is not None and fill.quantity == D("0.00000390625")
    dust = _fill(_limit("0.00000390625"), _quote(), **LOT)
    assert dust is not None and dust.quantity == D("0.00000390625")


def test_market_orders_take_the_displayed_size_rounded_down_to_the_lot() -> None:
    fill = _fill(_market("13"), _quote(size="2.71828"), **LOT)
    assert fill is not None and fill.quantity == D("2.7182")
    assert _fill(_market("13"), _quote(size="0.00009"), **LOT) is None
    whole = _fill(_market("1.5"), _quote(size="7"), **LOT)
    assert whole is not None and whole.quantity == D("1.5")


def test_without_an_increment_the_t05_quote_behaviour_is_unchanged() -> None:
    fill = _fill(_limit("0.8125"), _quote())
    assert fill is not None and fill.quantity == D("0.40625")
    market = _fill(_market("13"), _quote(size="2.71828"))
    assert market is not None and market.quantity == D("2.71828")


def test_repeated_volume_less_quotes_finish_a_large_order_on_the_lot_in_few_steps() -> None:
    remaining, steps, quantities = D("13"), 0, []
    while remaining > 0:
        fill = _fill(_limit(str(remaining)), _quote(), **LOT)
        assert fill is not None
        quantities.append(fill.quantity)
        remaining -= fill.quantity
        steps += 1
        assert steps <= 40
    assert sum(quantities) == D("13")
    assert all(quantity == quantity.quantize(D("0.0001")) for quantity in quantities)
    assert all(quantity >= D("0.0001") for quantity in quantities)
    assert steps < 20


def _bar(volume: str) -> Candle:
    return Candle(symbol="SOL/USD", source_pair="SOLUSD", interval_minutes=15, open_time_utc=PLACED,
                  close_time_utc=PLACED + timedelta(minutes=15),
                  available_at_utc=PLACED + timedelta(minutes=15, seconds=5),
                  open=D("100"), high=D("101"), low=D("98"), close=D("100"), volume=D(volume), trade_count=3,
                  source="synthetic")


def test_the_candle_path_rounds_its_volume_share_to_the_lot_when_given_one() -> None:
    order = SimOrder(order_id="entry", symbol="SOL/USD", side="buy", kind="limit", quantity=D("13"),
                     remaining=D("13"), limit_price=D("99"), placement_price=D("100"), eligible_after_utc=PLACED)
    unrounded = fill_order_on_candle(order, _bar("1.23456"), profile=KRAKEN, policy=BAR_REPLAY_POLICY)
    assert unrounded is not None and unrounded.quantity == D("0.123456")
    rounded = fill_order_on_candle(order, _bar("1.23456"), profile=KRAKEN, policy=BAR_REPLAY_POLICY, **LOT)
    assert rounded is not None and rounded.quantity == D("0.1234")
    assert fill_order_on_candle(order, _bar("0.0009"), profile=KRAKEN, policy=BAR_REPLAY_POLICY, **LOT) is None
