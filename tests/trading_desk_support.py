"""Helpers for the Trading desk tests (W12-A, C12): synthetic market-type labels, playbooks and stances.

Labels are written as the hourly scan writes them (one hash-checked row each) but with a short synthetic document, so a
test can say "SOL/USD was sideways at 09:05" without 863 hourly candles; the scripted week
(``test_trading_desk_week.py``) labels real synthetic candles with the protected labeller instead. Everything is
synthetic and labelled ``synthetic_fixture``. No network, model, key, paid call or order.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from trade_graph.contracts.trading_desk import CoinStance, PlaybookRevision
from trade_graph.domain.clock import utc_iso

SIDEWAYS_DIPS = {
    "why": "Fees decide most results: wait in falling markets, buy dips in sideways ones.",
    "choices": [
        {"market_type": "falling", "stance": "wait", "strategy_refs": [], "reason": "Losing less than holding is "
         "still losing; cash costs no fees.", "report_ids": []},
        {"market_type": "sideways", "stance": "buy_dips", "strategy_refs": ["slow-trend-pullback@1"],
         "reason": "Rest maker bids under the range.", "report_ids": []},
    ],
}


def playbook(**changes: Any) -> PlaybookRevision:
    return PlaybookRevision.model_validate({**SIDEWAYS_DIPS, **changes})


def stance(symbol: str = "BTC/USD", stance_word: str = "wait", seen: str = "falling", **changes: Any) -> CoinStance:
    document = {"symbol": symbol, "stance": stance_word, "market_type_seen": seen, "strategy_ref": None,
                "news_refs": [], "reason": f"Synthetic: {symbol} is {seen}."}
    document.update(changes)
    return CoinStance.model_validate(document)


def insert_label(database, symbol: str, market_type: str, as_of: datetime, *, reason: str | None = None) -> str:
    """One stored label at ``as_of`` (``unclear`` needs a reason), as ``market_type_labels`` keeps it."""
    label_id = "mt-" + uuid.uuid4().hex
    document = {"symbol": symbol, "market_type": market_type, "reason": reason, "rules": {"ref": "synthetic"},
                "candle_close_utc": utc_iso(as_of), "evidence": "synthetic_fixture"}
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with database.immediate() as connection:
        connection.execute(
            """INSERT INTO market_type_labels (label_id, symbol, as_of, market_type, reason, candle_close, lag_bars,
               interval_minutes, source, rules_ref, rules_sha256, scan_id, evidence, document_json, document_sha256,
               recorded_at) VALUES (?, ?, ?, ?, ?, ?, 0, 60, 'synthetic', 'market-type-rules@1', ?, NULL,
               'synthetic_fixture', ?, ?, ?)""",
            (label_id, symbol, utc_iso(as_of), market_type, reason, utc_iso(as_of), "0" * 64, text,
             hashlib.sha256(text.encode("ascii")).hexdigest(), utc_iso(as_of)))
    return label_id


__all__ = ["SIDEWAYS_DIPS", "insert_label", "playbook", "stance"]
