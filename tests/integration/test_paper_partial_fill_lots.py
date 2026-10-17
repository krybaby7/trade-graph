"""Regression (W4-A): resting limits crossed by quotes without a volume figure finish on the lot (synthetic quotes).

Reproduced by W3-A on ``868cda0``: a crossed resting limit filled half of what remained on every volume-less quote,
never rounded to the quantity increment, and after about 29 quotes ``Execution._consume_reservation`` trapped
``decimal.Inexact`` inside ``record_fill``. Each partial fill is now rounded down to the instrument's quantity
increment and the order finishes cleanly at the minimum, so the books, the reservation and the order agree exactly.
"""

from __future__ import annotations

from decimal import Decimal

from tests.money_limit_support import open_account

D = Decimal


def _fills(account, intent_id: str) -> list[Decimal]:
    rows = account.database.execute("SELECT document_json FROM fills WHERE intent_id = ? ORDER BY rowid",
                                    (intent_id,)).fetchall()
    from trade_graph.contracts.models import FillRecord

    return [FillRecord.model_validate_json(row["document_json"]).quantity for row in rows]


def test_a_resting_limit_crossed_by_volume_less_quotes_finishes_on_the_lot(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    intent = account.authorize("enter", "SOL/USD", "13", limit="99")
    account.dispatch()
    for _ in range(60):
        account.clock.advance(1)
        account.quote("SOL/USD", "98.9", "99.0")  # the ask reached the resting limit; no traded volume known
        if account.state(intent) == "FILLED":
            break
    assert account.state(intent) == "FILLED"
    quantities = _fills(account, intent)
    assert sum(quantities) == D("13") and account.held("SOL") == D("13")
    assert all(quantity == quantity.quantize(D("0.0001")) and quantity >= D("0.0001") for quantity in quantities)
    assert len(quantities) < 20
    held = account.database.execute("SELECT COUNT(*) FROM position_reservations WHERE intent_id = ? AND "
                                    "state = 'held'", (intent,)).fetchone()[0]
    assert held == 0


def test_a_volume_figure_below_one_lot_leaves_the_order_resting_until_enough_trades(tmp_path) -> None:
    account = open_account(tmp_path, limits=None)
    intent = account.authorize("enter", "SOL/USD", "2", limit="99")
    account.dispatch()
    account.clock.advance(1)
    account.quote("SOL/USD", "98.9", "99.0", volume="0.00004")
    assert account.state(intent) == "OPEN" and account.held("SOL") == 0
    account.clock.advance(1)
    account.quote("SOL/USD", "98.9", "99.0", volume="0.73219")
    assert account.state(intent) == "PARTIALLY_FILLED" and account.held("SOL") == D("0.7321")
    account.clock.advance(1)
    account.quote("SOL/USD", "98.9", "99.0", volume="50")
    assert account.state(intent) == "FILLED" and account.held("SOL") == D("2")
