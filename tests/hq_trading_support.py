"""Helpers for the HQ trading tests (W8-C): a dashboard-like runtime over a synthetic paper account, synthetic price
collector records, and the fullest trading facts the block can carry (for the payload budget).

Every account, price, fill and record here is synthetic (``tests/money_limit_support.py``,
``tests/scorecard_support.py``); nothing is fetched, sent or ordered.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from trade_graph.domain.clock import utc_iso

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "scorecard" / "golden_scorecard.json"


def runtime_for(account, *, config: Any = None) -> SimpleNamespace:
    """What HQ reads from a dashboard runtime: the database, its clock, the ledger and the paper portfolio."""
    portfolio = getattr(account, "portfolio", None)
    return SimpleNamespace(database=account.database, clock=account.clock, ledger=account.ledger,
                           execution=getattr(account, "execution", None), portfolio_id=portfolio, config=config)


def collector_record(ledger, portfolio_id: str, payload: dict, *, at: datetime) -> None:
    """One ``price_collection`` activity record, as ``service_market_data.activity_recorder`` writes it."""
    ledger._activity(portfolio_id, "price_collection", {**payload, "collector": "service-collector-v1"},
                     created_at=utc_iso(at))


def table_counts(database) -> dict[str, int]:
    connection = database.execute("SELECT 1").connection
    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


def golden_scorecard() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def fullest_facts(now: datetime) -> dict:
    """Trading facts with every list at its bound and long words: the most the block can weigh."""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    card = golden_scorecard()
    card["status"] = "provisional"
    card["provisional_reasons"] = [
        f"the {coin} price used at the end of the period is 17 minutes old (more than 15)"
        for coin in ("BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "AVAX")]
    card["benchmarks"]["hold_btc"].update(available=False, reason="no BTC price was recorded by the start of the "
                                          "period, so holding BTC cannot be valued", versus=None, beaten=None)
    card["owner_test_reading"].update(beats_btc=None)
    card["eur_reporting"].update(available=False, reason="no reference rate from USD to EUR was recorded at the end "
                                 "of the period", stale=True)
    loss = {"portfolio_id": "paper-1", "policy_revision": "owner-2", "day_start": utc_iso(day_start),
            "resumes_at": utc_iso(day_start + timedelta(days=1)), "currency": "USD", "limit_fraction": "0.03",
            "day_start_value": "10123.456789012345", "day_start_basis": "first_valuation_after_start",
            "day_start_valued_at": utc_iso(day_start + timedelta(hours=2)), "value": "9801.123456789012",
            "loss": "322.333332223333", "limit_amount": "303.70370367037035", "reached": True, "stopped": True,
            "unknown": None}
    events = [{"created_at": utc_iso(now - timedelta(minutes=2 + 3 * index)),
               "payload": {"job": "candles", "status": "partial", "requests": 30, "inserted": 12,
                           "failed_series": 3, "gaps": 2, "stop_reason": "rate_limited"}} for index in range(40)]
    events.insert(0, {"created_at": utc_iso(now - timedelta(minutes=1)),
                      "payload": {"job": "scan", "status": "failed", "ticker_error": "request_failed",
                                  "ranking_pass": {"attempted": 0, "ok": 0, "runs": 0}}})
    events.append({"created_at": utc_iso(now - timedelta(hours=5)),
                   "payload": {"job": "catalog", "status": "failed", "error": "public_catalog_request_failed"}})
    return {"as_of": now, "paper": True,
            "loss": {"recorded": True, "limits": {"fraction": "0.03", "currency": "USD", "day_start": "00:00"},
                     "document": loss, "error": None},
            "scorecard": {"document": card, "unavailable": None},
            "collector": {"enabled": True, "events": events, "truncated": True}}


def read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


__all__ = ["collector_record", "fullest_facts", "golden_scorecard", "read_only_connection", "runtime_for",
           "table_counts"]
