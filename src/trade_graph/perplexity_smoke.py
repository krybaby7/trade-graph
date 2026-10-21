"""Owner-run Perplexity smoke call for step L2 (T36, D125). Opt-in, one small round, honestly labelled.

Without ``--confirm-paid-call`` it only checks the key file and prints what it would do.
With it, it runs one research round on a separate smoke database, then prints every
receipt and Perplexity's own reported totals side by side. The report says ``real`` only
when the pinned network transport ran; a scripted transport is always ``synthetic`` and
never counts as a paid-provider test. The key is read from the owner's private file and
appears only in the request header.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trade_graph import __version__
from trade_graph.adapters.models.perplexity import AGENT_ENDPOINT
from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.adapters.research.perplexity_search import SEARCH_ENDPOINT
from trade_graph.domain.money import canonical_decimal

COMMAND = "perplexity-smoke"
DEFAULT_CARDS = Path(__file__).resolve().parents[2] / "config" / "perplexity-price-cards.json"
DEFAULT_QUESTION = ("What were the two most important crypto market news items in the last 24 hours? "
                    "Search once and cite your sources.")
DEPLOYMENT = "perplexity-smoke"


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="owner, step L2: one opt-in Perplexity research call with receipts; "
                                          "previews unless --confirm-paid-call")
    parser.add_argument("--key-file", required=True, help="absolute path of the owner's private key file")
    parser.add_argument("--database", required=True, help="a separate smoke database, never the installation's")
    parser.add_argument("--price-cards", default=str(DEFAULT_CARDS))
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--limit-usd", default="0.25", help="money limit for Perplexity calls in this smoke database")
    parser.add_argument("--fx-rate", default="1",
                        help="USD to EUR for the reservations' EUR view; the default 1 is identity, not a quote")
    parser.add_argument("--report", help="also write the report to this file (mode 600)")
    parser.add_argument("--confirm-paid-call", action="store_true", help="make the paid call (D125 test key)")


def real_transport() -> HttpxProviderHttp:
    return HttpxProviderHttp(timeout=120, allowed_urls=(AGENT_ENDPOINT, SEARCH_ENDPOINT))


def run_smoke(args: argparse.Namespace, *, transport=None) -> int:
    from trade_graph.application.private_key_file import read_private_key
    from trade_graph.application.web_research import (
        PerplexityRoute,
        PerplexityWebResearch,
        WebResearchAsk,
        load_price_cards,
    )
    from trade_graph.kernel.pricing import worst_case_cost

    try:
        key = read_private_key(Path(args.key_file).expanduser())
        limit, fx_rate = Decimal(args.limit_usd), Decimal(args.fx_rate)
        if not limit.is_finite() or limit <= 0 or not fx_rate.is_finite() or fx_rate <= 0:
            raise ValueError("limit and FX rate must be positive numbers")
        cards = {card.price_card_id: card for card in load_price_cards(Path(args.price_cards))}
        route = PerplexityRoute(deployment_id=DEPLOYMENT, fx_rate=fx_rate)
        agent, search = cards[route.agent_price_card_id], cards[route.search_price_card_id]
    except (OSError, ValueError, KeyError, InvalidOperation) as exc:
        print(f"Perplexity smoke refused: {exc}", file=sys.stderr)
        return 2
    # The Leader's news rounds allow 3,000 output tokens. The first real smoke (2026-10-21) showed Claude spending 800
    # of 803 output tokens on hidden reasoning, so a smaller cap cuts the answer off (D231).
    ask_bounds = {"max_searches": 1, "max_input_tokens": 12000, "max_output_tokens": 3000}
    step = worst_case_cost(agent, ask_bounds["max_input_tokens"], ask_bounds["max_output_tokens"], 1)
    maximum = (2 * step + worst_case_cost(search, 0, 0, 1)) * route.fx_buffer
    if not args.confirm_paid_call:
        _emit(args, {"status": "not_run", "reason": "opt-in: add --confirm-paid-call to make one paid call (D125)",
                     "key_file": "readable, owner-only, outside Git", "endpoints": [AGENT_ENDPOINT, SEARCH_ENDPOINT],
                     "maximum_usd": canonical_decimal(maximum), "limit_usd": canonical_decimal(limit)}, write=False)
        return 0
    try:
        database_path = _smoke_database(Path(args.database).expanduser().absolute())
    except (OSError, PermissionError) as exc:
        print(f"Perplexity smoke refused: {exc}", file=sys.stderr)
        return 2
    from trade_graph.adapters.persistence.db import Database
    from trade_graph.application.budget import BudgetGateway
    from trade_graph.application.gateway import ModelGateway
    from trade_graph.contracts.web_research import PerplexitySpendPolicy
    from trade_graph.domain.clock import SystemClock, utc_iso

    clock = SystemClock()
    database = Database(database_path)
    try:
        budget = BudgetGateway(database, clock)
        for card in cards.values():
            budget.seed_card(card)
        budget.configure(deployment_id=DEPLOYMENT, currency="EUR", total=Decimal("5"), period=Decimal("5"),
                         priority_reserve=Decimal("0"), daily=Decimal("5"), root=Decimal("5"), roles={})
        transport = transport if transport is not None else real_transport()
        real = type(transport) is HttpxProviderHttp
        gateway = ModelGateway(budget, paid_calls_enabled=True, transport=transport,
                               api_keys={"perplexity": key.get_secret_value()})
        policy = PerplexitySpendPolicy(revision_id="l2-smoke", mode="monthly_limit", monthly_limit_usd=limit,
                                       decision_ref="D125-smoke")
        task_id = f"smoke-{uuid.uuid4().hex[:12]}"
        outcome = PerplexityWebResearch(gateway, route=route, policy=policy).run(WebResearchAsk(
            role="research", portfolio_id=DEPLOYMENT, task_id=task_id, root_task_id=task_id,
            system_version_id=f"trade-graph-{__version__}", question=args.question, **ask_bounds))
        receipts = database.execute("""SELECT b.reservation_id, b.price_card_id, b.state, r.status, r.native_cost,
            r.provider_request_id, r.synthetic FROM budget_reservations b
            LEFT JOIN usage_receipts r ON r.reservation_id = b.reservation_id
            WHERE b.task_id = ? ORDER BY b.created_at, b.rowid""", (task_id,)).fetchall()
        costs = {row["reservation_id"]: row["native_cost"] for row in receipts}
        steps = database.execute("""SELECT reservation_id, sequence, outcome, provider_model,
            reported_total_cost_usd FROM web_research_steps WHERE run_id = ? ORDER BY sequence""",
            (outcome.run_id,)).fetchall()
        report = {
            "label": "real" if real else "synthetic", "real_paid_call": real, "at": utc_iso(clock.now()),
            "note": ("Receipts are priced from the dated cards; compare them with Perplexity's reported totals. "
                     "A synthetic run is not a paid-provider test."),
            "outcome": outcome.model_dump(mode="json"),
            "receipts": [{"price_card_id": row["price_card_id"], "reservation_state": row["state"],
                          "status": row["status"] or "unresolved", "native_cost_usd": row["native_cost"],
                          "provider_request_id": row["provider_request_id"]} for row in receipts],
            "steps": [{"sequence": row["sequence"], "outcome": row["outcome"], "provider_model": row["provider_model"],
                       "reported_total_cost_usd": row["reported_total_cost_usd"],
                       "receipt_native_cost_usd": costs.get(row["reservation_id"])} for row in steps],
        }
    finally:
        database.close()
    _emit(args, report, write=True)
    return 0 if outcome.status == "completed" else 1


def run(args: argparse.Namespace) -> int:
    return run_smoke(args)


def _smoke_database(path: Path) -> Path:
    """A new private file, or an earlier smoke database; never a database that holds a portfolio."""
    if path.exists():
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            held = "portfolios" in tables and connection.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]
        except sqlite3.DatabaseError:
            raise PermissionError("use a separate smoke database (this file is not one)") from None
        finally:
            connection.close()
        if held:
            raise PermissionError("use a separate smoke database, never the installation's")
        return path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    return path


def _emit(args: argparse.Namespace, report: dict, *, write: bool) -> None:
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if write and args.report:
        descriptor = os.open(Path(args.report).expanduser(), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
