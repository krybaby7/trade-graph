"""Synthetic weeks of paper prices and a read meter, for the W10-B (C8) tests and the before/after measure.

Every price, fill and rate here is invented. Fills go through the real ``Ledger`` (as the paper broker writes them);
the price marks and hourly reference rates are inserted in bulk with the same columns ``Ledger.observe_mark`` and the
public paper feed write, so four weeks of 10-second prices build in seconds. Nothing is fetched, sent or ordered.

``ReadMeter`` wraps a ``Database`` or a sqlite3 connection and counts what a piece of code reads through it: the
statements it runs and the rows each table returns to Python, plus SQLite's own work in virtual-machine steps. Tests
assert on these counts, never on seconds, so they do not depend on how busy the machine is.

Measure (the handoff's before/after numbers; run from the repository root)::

    uv run python -m tests.mark_reads_support --days 28
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from tests.scorecard_support import PORTFOLIO, START, Paper
from trade_graph.domain.clock import utc_iso

D = Decimal
COINS = ("BTC", "ETH", "SOL", "XRP")
BASE = {"BTC": D("60000"), "ETH": D("3000"), "SOL": D("150"), "XRP": D("0.5")}
LOT = D("0.0001")
SOURCE = "synthetic:paper_reference:receipt_time"


# --- The read meter -----------------------------------------------------------------------------------------------


class _Fetched:
    """A finished result: the rows a cursor returned, read in full when the statement ran."""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list:
        return list(self._rows)

    def __iter__(self) -> Iterator:
        return iter(self._rows)


@dataclass
class ReadMeter:
    """Stands in for a ``Database`` (or a sqlite3 connection) and counts what is read through it.

    ``statements`` holds every ``(sql, params)`` run; ``rows`` counts the rows returned to Python per table read
    (``valuation_marks``, ``fx_rates`` or ``other``); ``steps`` is SQLite's virtual-machine work in units of
    ``step`` instructions (deterministic for one SQLite version and one database).
    """

    target: object
    step: int = 1000
    statements: list[tuple[str, tuple]] = field(default_factory=list)
    rows: Counter = field(default_factory=Counter)
    steps: int = 0
    work: list[tuple[str, int, int, float]] = field(default_factory=list)  # (sql, rows, steps, seconds) each

    @property
    def connection(self) -> ReadMeter:
        return self

    def _raw(self) -> sqlite3.Connection:
        target = self.target
        return target if isinstance(target, sqlite3.Connection) else target.connection

    def execute(self, sql: str, params: tuple = ()) -> _Fetched:
        raw = self._raw()
        before, began = self.steps, time.perf_counter()
        raw.set_progress_handler(self._count, self.step)
        try:
            rows = raw.execute(sql, params).fetchall()
        finally:
            raw.set_progress_handler(None, self.step)
        self.statements.append((sql, tuple(params)))
        self.rows[table_of(sql)] += len(rows)
        self.work.append((sql, len(rows), self.steps - before, time.perf_counter() - began))  # seconds: information
        return _Fetched(rows)

    def _count(self) -> int:
        self.steps += 1
        return 0

    def reads_of(self, table: str) -> list[tuple[str, tuple]]:
        return [(sql, params) for sql, params in self.statements if table_of(sql) == table]


def table_of(sql: str) -> str:
    if "valuation_marks" in sql:
        return "valuation_marks"
    if "fx_rates" in sql:
        return "fx_rates"
    return "other"


def query_plan(connection, sql: str, params: tuple) -> str:
    raw = connection if isinstance(connection, sqlite3.Connection) else connection.connection
    return " | ".join(row[-1] for row in raw.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall())


# --- Synthetic weeks of prices ------------------------------------------------------------------------------------


@dataclass
class Weeks:
    """A synthetic paper portfolio with ``days`` of prices: its ``Paper`` helper, the end and what was written."""

    paper: Paper
    end: datetime
    marks: int
    rates: int
    trades: int
    held_at_end: tuple[str, ...]
    held_marks: int  # marks observed while their coin was held (what the scorecard's curve needs)


def _price_paths(steps: int, seed: int) -> dict[str, list[Decimal]]:
    rng = random.Random(seed)
    paths: dict[str, list[Decimal]] = {}
    for coin in COINS:
        offset, path = 0, []
        for _ in range(steps):
            offset = max(-1500, min(1500, offset + rng.randint(-6, 6)))
            path.append(BASE[coin] * (10000 + offset) / 10000)
        paths[coin] = path
    return paths


def build_weeks(path, *, days: float, interval_seconds: int = 10, trades: int = 60, hold_hours: float = 4,
                keep_last: bool = True, seed: int = 8) -> Weeks:
    """USD10,000 at ``START``; ``trades`` round trips spread evenly, one coin at a time in turn, each held
    ``hold_hours``; the last one still held at the end when ``keep_last``. A mark of every coin every
    ``interval_seconds`` and a USD/EUR reference rate every hour, as the paper feed records them."""
    paper = Paper(path, at=START)
    span = timedelta(days=days)
    steps = int(span.total_seconds() // interval_seconds) + 1
    paths = _price_paths(steps, seed)

    def step_of(moment: datetime) -> int:
        return int((moment - START).total_seconds() // interval_seconds)

    paper.deposit("10000")
    cash = D("10000")
    held: dict[str, tuple[Decimal, datetime]] = {}
    windows: list[tuple[str, datetime, datetime]] = []
    gap = span / trades
    hold = min(timedelta(hours=hold_hours), gap / 3)  # one coin held at a time
    for index in range(trades):
        coin = COINS[index % len(COINS)]
        bought = START + gap * index + gap / 2
        sold = bought + hold
        last = keep_last and index == trades - 1
        if not last and sold >= START + span:
            sold = START + span - timedelta(seconds=interval_seconds)
        paper.at(bought)
        price = paths[coin][step_of(bought)]
        quantity = (cash / 5 / price).quantize(LOT, rounding=ROUND_FLOOR)
        paper.fill(f"{coin}/USD", "buy", str(quantity), str(price))
        cash -= quantity * price * D("1.004")
        if last:
            held[coin] = (quantity, bought)
            windows.append((coin, bought, START + span))
            continue
        paper.at(sold)
        price = paths[coin][step_of(sold)]
        paper.fill(f"{coin}/USD", "sell", str(quantity), str(price), liquidity="taker")
        cash += quantity * price * D("0.992")
        windows.append((coin, bought, sold))
    end = START + span
    paper.at(end)

    marks = []
    for number in range(steps):
        observed = utc_iso(START + timedelta(seconds=number * interval_seconds))
        for coin in COINS:
            marks.append((f"synthetic-mark-{number}-{coin}", PORTFOLIO, coin, "USD", str(paths[coin][number]), "mid",
                          observed, 0, SOURCE))
    rates = []
    for hour in range(int(span.total_seconds() // 3600) + 1):
        retrieved = START + timedelta(hours=hour)
        valid = utc_iso(retrieved.replace(hour=0, minute=0, second=0, microsecond=0))
        rates.append((f"synthetic-rate-{hour}", "USD", "EUR", str(D("0.92") + D(hour % 7) / 1000),
                      "synthetic-reference", valid, valid, utc_iso(retrieved), "reference", 0))
    with paper.database.transaction() as connection:
        connection.executemany(
            """INSERT INTO valuation_marks
            (mark_id, portfolio_id, asset, quote_currency, mark, convention, observed_at, stale, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", marks)
        connection.executemany(
            """INSERT INTO fx_rates
            (rate_id, base, quote, rate, source, observed_at, valid_as_of, retrieved_at, kind, stale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rates)
    held_marks = sum(1 for coin, bought, sold in windows for number in range(steps)
                     if utc_iso(bought) < utc_iso(START + timedelta(seconds=number * interval_seconds))
                     <= utc_iso(sold))
    return Weeks(paper=paper, end=end, marks=len(marks), rates=len(rates), trades=trades,
                 held_at_end=tuple(sorted(held)), held_marks=held_marks)


def add_prices(paper: Paper, count: int, *, coins: tuple[str, ...] = COINS, interval_seconds: int = 10,
               seed: int = 3) -> None:
    """``count`` more marks of each coin, one every ``interval_seconds`` after the clock, which ends on the last."""
    rng = random.Random(seed + count)
    first = paper.clock.now()
    numbered = paper.database.execute("SELECT COUNT(*) FROM valuation_marks").fetchone()[0]
    rows = []
    for number in range(1, count + 1):
        observed = utc_iso(first + timedelta(seconds=number * interval_seconds))
        for coin in coins:
            price = BASE[coin] * (1000 + rng.randint(-20, 20)) / 1000
            rows.append((f"synthetic-mark-{numbered + len(rows)}", PORTFOLIO, coin, "USD", str(price), "mid", observed,
                         0, SOURCE))
    with paper.database.transaction() as connection:
        connection.executemany(
            """INSERT INTO valuation_marks
            (mark_id, portfolio_id, asset, quote_currency, mark, convention, observed_at, stale, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
    paper.at(first + timedelta(seconds=count * interval_seconds))


def add_rates(paper: Paper, count: int) -> None:
    """``count`` USD/EUR reference rates, one an hour up to the clock, as the paper feed records them."""
    now = paper.clock.now()
    numbered = paper.database.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
    rows = []
    for back in range(count, 0, -1):
        retrieved = now - timedelta(hours=back - 1)
        valid = utc_iso(retrieved.replace(hour=0, minute=0, second=0, microsecond=0))
        rows.append((f"synthetic-rate-{numbered + len(rows)}", "USD", "EUR", str(D("0.90") + D(back % 9) / 100),
                     "synthetic-reference", valid, valid, utc_iso(retrieved), "reference", 0))
    with paper.database.transaction() as connection:
        connection.executemany(
            """INSERT INTO fx_rates
            (rate_id, base, quote, rate, source, observed_at, valid_as_of, retrieved_at, kind, stale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)


# --- The measure ----------------------------------------------------------------------------------------------------


def _measure(label: str, run, meter: ReadMeter) -> dict:
    began = time.perf_counter()
    result = run(meter)
    seconds = time.perf_counter() - began
    row = {"what": label, "statements": len(meter.statements), "mark_rows": meter.rows["valuation_marks"],
           "fx_rows": meter.rows["fx_rates"], "other_rows": meter.rows["other"],
           "vm_steps_thousands": meter.steps, "seconds": round(seconds, 3)}
    print(row, flush=True)
    heaviest = sorted(meter.work, key=lambda item: -item[2])[:3]
    for sql, rows, steps, seconds in heaviest:
        print("    heaviest:", {"vm_steps_thousands": steps, "rows": rows, "seconds": round(seconds, 3),
                                "sql": " ".join(sql.split())[:110]}, flush=True)
    return {"row": row, "result": result}


def main(argv: list[str] | None = None) -> int:
    from tests.money_limit_support import synthetic_policy
    from trade_graph.application.ledger import Ledger
    from trade_graph.application.money_limits import MoneyLimitGuard
    from trade_graph.application.scorecard import compute_scorecard

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=float, default=28)
    parser.add_argument("--folder", type=Path, default=None)
    arguments = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(dir=arguments.folder) as folder:
        began = time.perf_counter()
        weeks = build_weeks(Path(folder) / "state.sqlite", days=arguments.days)
        print({"built": {"marks": weeks.marks, "rates": weeks.rates, "trades": weeks.trades,
                         "held_at_end": weeks.held_at_end, "held_marks": weeks.held_marks,
                         "seconds": round(time.perf_counter() - began, 1)}}, flush=True)
        paper = weeks.paper
        policy = synthetic_policy("synthetic-owner-1")
        results = {}
        results["native_equity"] = _measure(
            "Ledger.native_equity (the money-limit step's valuation)",
            lambda meter: Ledger(meter, paper.clock).native_equity(PORTFOLIO, "USD"), ReadMeter(paper.database))
        results["status"] = _measure(
            "MoneyLimitGuard.status (the money-limit step and HQ's today's loss)",
            lambda meter: MoneyLimitGuard(meter, Ledger(meter, paper.clock), paper.clock).status(PORTFOLIO, policy),
            ReadMeter(paper.database))
        results["equity"] = _measure(
            "Ledger.equity in EUR (the Trader workflow and the forward producer)",
            lambda meter: Ledger(meter, paper.clock).equity(PORTFOLIO), ReadMeter(paper.database))
        connection = sqlite3.connect(f"file:{paper.database.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            results["scorecard"] = _measure(
                "compute_scorecard from the opening deposit (HQ's scorecard)",
                lambda meter: compute_scorecard(meter, portfolio_id=PORTFOLIO, end=weeks.end).document(),
                ReadMeter(connection))
        finally:
            connection.close()
        print({"native_equity": str(results["native_equity"]["result"].equity),
               "loss_status": results["status"]["result"].document(),
               "equity_eur": str(results["equity"]["result"].equity),
               "scorecard_sha256": results["scorecard"]["result"]["scorecard_sha256"],
               "equity_points": results["scorecard"]["result"]["valuation"]["equity_points"]}, flush=True)
        paper.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
