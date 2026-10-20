"""The paper scorecard (T42): the paper account's result after fees against holding cash and holding BTC over the
same period, with the AI spending of those days beside it.

Read only, from persisted records; nothing here writes, sends, pauses, limits or decides anything.

- **One set of sums (D169, D201).** Trades, wins, losses, win rate, average win and loss, fees paid, worst drawdown,
  worst losing streak and the comparisons come from the practice range's kernel sums (``kernel/trade_metrics.py``).
  ``TradeFill.from_fill_record`` puts the ledger's fills, with every fee leg, in the quote currency.
- **The period and its valuation (D200).** The period runs from ``start`` (default: the opening deposit, or the first
  BTC price after it) to ``end``; money added or taken out inside it, or operating costs paid from the paper cash,
  make it unscorable. The account is valued from the ledger's own postings and marks: at the start as the ledger
  values it, at every posting and every new price of a coin held, and at the end with each coin still held valued
  as if sold at its mark with the taker fee, as holding BTC is. Coins held at the start are carried in as bought at
  the start price. The running books must agree with the ledger at both ends, or nothing is scored. A price older
  than 15 minutes, or marked stale, makes the scorecard provisional, never silently complete.
- **Benchmarks and EUR (D202).** Cash earns nothing (D127). Holding BTC spends the starting equity on BTC at the
  ledger's BTC price at the start with the taker fee of the cost profile and sells at the end price with the fee;
  without a BTC price it is unavailable, never invented. EUR figures use the reference rates the books use, with
  holding USD cash in EUR as the baseline, so the dollar's own move is not counted as trading (A43).
- **AI spending (D203)** is every paid call of the period from receipts (``application.ai_spending``): unknown usage
  stays unknown, never zero, and the simulated paper result never pays for it.
- **Gates (D204).** The scorecard states that working software, economic evidence and permission for real money are
  separate gates, and reads the owner's test rule (D138 item 7) without deciding anything.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any

from trade_graph.application.ai_spending import PeriodSpending, period_spending
from trade_graph.application.ledger import Ledger
from trade_graph.application.market_types.store import DEFAULT_LABEL_AGE, read_latest_market_types
from trade_graph.application.trading_desk.store import read_day_plan_desk_records
from trade_graph.contracts.models import FillRecord, InstrumentRules
from trade_graph.contracts.trading_desk import STANCES
from trade_graph.domain.clock import FrozenClock, parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError, ValidationFailure
from trade_graph.domain.market_types import MARKET_TYPES
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.books import Mark, native_equity
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, load_cost_profiles
from trade_graph.kernel.trade_metrics import (
    METRICS_VERSION,
    QUANTUM,
    EquityPoint,
    GroupResult,
    HoldBenchmark,
    TradeFill,
    TradeMetrics,
    hold_benchmark,
    split_results,
    summarize,
)

SCORECARD_VERSION = "paper-scorecard-v1"
BENCHMARK_COIN = "BTC"
DEFAULT_COST_PROFILE = KRAKEN_STARTING  # the paper broker's default fees (D146)
DEFAULT_LOT = Decimal("0.00000001")  # as the practice range floors quantities without instrument rules (D167)
MARK_MAX_AGE = timedelta(minutes=15)  # as the daily loss limit reads marks (D182)
MINIMUM_DAYS = 28  # D138 item 7: at least four weeks ...
MINIMUM_TRADES = 30  # ... and at least 30 completed trades, whichever takes longer
CARRIED_IN = "carried-in:"
_EXACT = Context(prec=160)
_SECONDS_PER_DAY = Decimal(86400)

GATES_STATEMENT = (
    "Working software, economic evidence and permission to trade real money are three separate gates. This "
    "scorecard is evidence about simulated paper trading over one period: it does not show that the software works, "
    "a good result does not prove that the Trader is profitable, and nothing here allows trading real money or "
    "spending a real budget.")
AI_NOTE = ("AI spending is real money, from receipts, for the whole installation (AI budgets belong to the "
           "installation, not to one paper account); costs not known yet are shown as unknown, never as zero. The "
           "paper result is simulated and never pays for it.")
CASH_NOTE = "Cash earns no interest (D127), so beating cash means a result above zero after fees."
READING_NOTE = ("Your rule for the scored paper test (D138 item 7), read from these figures. It is not a pass mark "
                "for the Trader, not proof of profit and not permission for real money.")


class ScorecardUnavailable(ValidationFailure):
    """The period cannot be scored from the records; the message says why in plain words."""


def _text(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else utc_iso(value)


# --- Results ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BtcComparison:
    """How holding BTC over the same period was valued, or why it could not be."""

    available: bool
    reason: str | None
    symbol: str
    cost_profile_ref: str
    cost_profile_sha256: str
    taker_rate: Decimal
    lot_size: Decimal
    start_mark_at: datetime | None = None
    end_mark_at: datetime | None = None
    hold: HoldBenchmark | None = None

    def document(self, versus: Decimal | None) -> dict[str, Any]:
        return {"available": self.available, "reason": self.reason, "symbol": self.symbol,
                "price_source": "the paper ledger's marks", "start_mark_at": _iso(self.start_mark_at),
                "end_mark_at": _iso(self.end_mark_at), "cost_profile_ref": self.cost_profile_ref,
                "cost_profile_sha256": self.cost_profile_sha256, "taker_rate": _text(self.taker_rate),
                "lot_size": _text(self.lot_size), "versus": _text(versus),
                "beaten": None if versus is None else versus > 0}


@dataclass(frozen=True)
class EurReporting:
    """The USD figures shown in the reporting currency at the books' reference rates (A43)."""

    currency: str
    available: bool
    reason: str | None = None
    rate_start: Decimal | None = None
    rate_end: Decimal | None = None
    stale: bool = False
    starting_equity: Decimal | None = None
    ending_equity: Decimal | None = None
    result: Decimal | None = None
    currency_effect: Decimal | None = None  # holding the starting dollars as cash, in EUR
    versus_cash: Decimal | None = None
    hold_btc_result: Decimal | None = None
    versus_hold_btc: Decimal | None = None
    ai_spending_total: Decimal | None = None

    def document(self) -> dict[str, Any]:
        return {"currency": self.currency, "available": self.available, "reason": self.reason,
                "rate_start": _text(self.rate_start), "rate_end": _text(self.rate_end), "stale": self.stale,
                "starting_equity": _text(self.starting_equity), "ending_equity": _text(self.ending_equity),
                "result": _text(self.result), "currency_effect": _text(self.currency_effect),
                "versus_cash": _text(self.versus_cash), "hold_btc_result": _text(self.hold_btc_result),
                "versus_hold_btc": _text(self.versus_hold_btc), "ai_spending_total": _text(self.ai_spending_total),
                "note": "Starting equity at the start rate; everything else at the end rate. The currency effect is "
                        "what holding the starting dollars as cash did in EUR, so it is not counted as trading."}


@dataclass(frozen=True)
class OwnerTestReading:
    """D138 item 7, read from the figures: long enough, and beating cash and BTC after fees."""

    days: Decimal
    trades: int
    long_enough: bool
    beats_cash: bool
    beats_btc: bool | None
    keep_going: bool | None

    def document(self) -> dict[str, Any]:
        return {"rule": "D138 item 7", "minimum_days": MINIMUM_DAYS, "minimum_trades": MINIMUM_TRADES,
                "days": _text(self.days), "trades": self.trades, "long_enough": self.long_enough,
                "beats_cash": self.beats_cash, "beats_btc": self.beats_btc, "keep_going": self.keep_going,
                "note": READING_NOTE}


# --- The split by strategy version, market type at entry and stance (W13-A, C13; D300) ------------------------------


SPLIT_VERSION = "scorecard-split-v1"
NO_DAY_PLAN = "no_day_plan"  # a trade whose first buy no day plan placed (an older order, or a plan before 0034)
CARRIED_IN_GROUP = "carried_in"  # coins already held at the start: their entry lies before the period
NOT_RECORDED = "not_recorded"  # no stance was recorded with the day plan (a plan before 0052, or none given)
# The label software recorded for the coin at the first buy (D287: current for 2 hours); a label more than 2 hours
# old is "stale" and none at all is "missing": neither is shown as the type then, never a guess.
MARKET_TYPES_AT_ENTRY = (*MARKET_TYPES, "stale", "missing", CARRIED_IN_GROUP)
STANCES_AT_ENTRY = (*STANCES, NOT_RECORDED, CARRIED_IN_GROUP)
LABEL_MAX_AGE = DEFAULT_LABEL_AGE
SPLIT_BASIS = (
    "Each trade and each position still held counts once, by the choice behind its first buy: the strategy version "
    "of the day plan whose waiting buy filled (no_day_plan when no day plan placed it), the market type software "
    "labelled the coin with at that moment (stale when the newest label was more than 2 hours old, missing when "
    "there was none) and the Trading desk's stance recorded with that day plan (not_recorded when none was). Coins "
    "already held at the start count as carried_in. A position still held counts with its value as if sold at the "
    "end with the taker fee, so each list sums exactly to the trades, wins, losses and result after fees.")
_STRATEGY_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@[A-Za-z0-9._-]{1,16}$")


@dataclass(frozen=True)
class EntryChoice:
    """What was chosen when a trade's first buy filled: the strategy version, the market type then, the stance."""

    strategy: str  # a strategy version (strategy_id@version), NO_DAY_PLAN or CARRIED_IN_GROUP
    market_type: str  # one of MARKET_TYPES_AT_ENTRY
    stance: str  # one of STANCES_AT_ENTRY
    plan_id: str | None = None
    label_id: str | None = None
    labelled_at: datetime | None = None
    stance_id: str | None = None
    playbook_revision: int | None = None

    def __post_init__(self) -> None:
        if self.strategy not in (NO_DAY_PLAN, CARRIED_IN_GROUP) and (
                not isinstance(self.strategy, str) or _STRATEGY_REF.fullmatch(self.strategy) is None):
            raise ValidationFailure("a trade's strategy is a strategy version, no_day_plan or carried_in")
        if self.market_type not in MARKET_TYPES_AT_ENTRY:
            raise ValidationFailure(f"a market type at entry is one of {', '.join(MARKET_TYPES_AT_ENTRY)}")
        if self.stance not in STANCES_AT_ENTRY:
            raise ValidationFailure(f"a stance at entry is one of {', '.join(STANCES_AT_ENTRY)}")

    @classmethod
    def carried_in(cls) -> EntryChoice:
        return cls(strategy=CARRIED_IN_GROUP, market_type=CARRIED_IN_GROUP, stance=CARRIED_IN_GROUP)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.strategy, self.market_type, self.stance

    def document(self) -> dict[str, Any]:
        return {"strategy_ref": self.strategy, "market_type": self.market_type, "stance": self.stance,
                "plan_id": self.plan_id, "label_id": self.label_id, "labelled_at_utc": _iso(self.labelled_at),
                "stance_id": self.stance_id, "playbook_revision": self.playbook_revision}


@dataclass(frozen=True)
class ScorecardSplit:
    """The scorecard's trades and result after fees by strategy version, market type at entry and stance.

    ``by_strategy`` lists the strategy versions found in name order, then ``no_day_plan`` and ``carried_in`` when
    used; ``by_market_type`` and ``by_stance`` list every word in its fixed order, empty ones included; ``by_choice``
    the combinations found. Each sums exactly to ``totals`` (the kernel's ``split_results``). ``outside_trades`` is
    whatever part of the scorecard's result no trade explains: zero for the paper broker's fills.
    """

    available: bool
    reason: str | None
    by_strategy: tuple[GroupResult, ...] = ()
    by_market_type: tuple[GroupResult, ...] = ()
    by_stance: tuple[GroupResult, ...] = ()
    by_choice: tuple[tuple[tuple[str, str, str], GroupResult], ...] = ()
    entries: tuple[dict[str, Any], ...] = ()
    totals: dict[str, Any] | None = None
    outside_trades: Decimal | None = None

    @classmethod
    def unavailable(cls, reason: str) -> ScorecardSplit:
        return cls(available=False, reason=reason)

    def document(self) -> dict[str, Any]:
        return {
            "split_version": SPLIT_VERSION, "available": self.available, "reason": self.reason, "basis": SPLIT_BASIS,
            "label_max_age_minutes": int(LABEL_MAX_AGE.total_seconds() // 60), "totals": self.totals,
            "outside_trades": _text(self.outside_trades),
            "by_strategy": [row.to_document("strategy_ref") for row in self.by_strategy],
            "by_market_type": [row.to_document("market_type") for row in self.by_market_type],
            "by_stance": [row.to_document("stance") for row in self.by_stance],
            "by_choice": [{"strategy_ref": key[0], "market_type": key[1], "stance": key[2],
                           **{name: value for name, value in row.to_document().items() if name != "group"}}
                          for key, row in self.by_choice],
            "entries": [dict(item) for item in self.entries]}


def split_scorecard(metrics: TradeMetrics, *, trade_choices: Sequence[EntryChoice],
                    open_choices: Sequence[EntryChoice], open_values: Sequence[Decimal]) -> ScorecardSplit:
    """Split ``metrics``' trades and positions still held by the choice behind each one's first buy.

    ``trade_choices`` follow ``metrics.round_trips`` and ``open_choices`` with ``open_values`` (each position's value
    as if sold at the end, after the selling fee) follow ``metrics.open_positions``.
    """
    trips, held = metrics.round_trips, metrics.open_positions
    if len(trade_choices) != len(trips):
        raise ValidationFailure("the split needs one choice per trade")
    if len(open_choices) != len(held) or len(open_values) != len(held):
        raise ValidationFailure("the split needs one choice per position still held, with its value")
    with localcontext(_EXACT):
        open_results = [_decimal_value(value) + position.sold - position.bought - position.fees
                        for position, value in zip(held, open_values, strict=True)]
    choices = (*trade_choices, *open_choices)

    def rows(groups: Sequence[str], key) -> tuple[GroupResult, ...]:
        if not groups:
            return ()
        return split_results(groups, [(key(choice), trip) for trip, choice in zip(trips, trade_choices, strict=True)],
                             [(key(choice), result) for choice, result in zip(open_choices, open_results,
                                                                              strict=True)])

    versions = sorted({choice.strategy for choice in choices} - {NO_DAY_PLAN, CARRIED_IN_GROUP})
    strategies = (*versions, *(group for group in (NO_DAY_PLAN, CARRIED_IN_GROUP)
                               if any(choice.strategy == group for choice in choices)))
    order = ({name: index for index, name in enumerate(strategies)},
             {name: index for index, name in enumerate(MARKET_TYPES_AT_ENTRY)},
             {name: index for index, name in enumerate(STANCES_AT_ENTRY)})
    keys = sorted({choice.key for choice in choices},
                  key=lambda item: tuple(rank[part] for rank, part in zip(order, item, strict=True)))
    names = {key: "|".join(key) for key in keys}
    combined = rows([names[key] for key in keys], lambda choice: names[choice.key])
    with localcontext(_EXACT):
        explained = sum((trip.result for trip in trips), Decimal("0")) + sum(open_results, Decimal("0"))
        outside = metrics.result - explained
    entries = [{"kind": "trade", "symbol": trip.symbol, "opened_at_utc": utc_iso(trip.opened_at),
                "closed_at_utc": utc_iso(trip.closed_at), "first_fill_ref": trip.fill_refs[0],
                "result_after_fees": _text(trip.result), "outcome": trip.outcome, **choice.document()}
               for trip, choice in zip(trips, trade_choices, strict=True)]
    entries += [{"kind": "open", "symbol": position.symbol, "opened_at_utc": utc_iso(position.opened_at),
                 "closed_at_utc": None, "first_fill_ref": position.fill_refs[0], "result_after_fees": _text(result),
                 "outcome": None, **choice.document()}
                for position, result, choice in zip(held, open_results, open_choices, strict=True)]
    totals = {"trades": metrics.trades, "wins": metrics.wins, "losses": metrics.losses,
              "flat_trades": metrics.flat_trades, "open_positions": len(held), "result_after_fees": _text(explained)}
    return ScorecardSplit(
        available=True, reason=None, by_strategy=rows(strategies, lambda choice: choice.strategy),
        by_market_type=rows(MARKET_TYPES_AT_ENTRY, lambda choice: choice.market_type),
        by_stance=rows(STANCES_AT_ENTRY, lambda choice: choice.stance),
        by_choice=tuple(zip(keys, combined, strict=True)), entries=tuple(entries), totals=totals,
        outside_trades=outside)


def _decimal_value(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValidationFailure("a position's value at the end is a finite Decimal")
    return value


@dataclass(frozen=True)
class Scorecard:
    portfolio_id: str
    experiment_id: str
    currency: str
    start: datetime
    end: datetime
    start_basis: str  # opening_deposit, opening_deposit_and_btc_price or requested
    metrics: TradeMetrics
    ending_equity_marked: Decimal
    exit_fees: Decimal
    carried_in: tuple[str, ...]
    equity_points: int
    unvalued_points: int
    provisional: tuple[str, ...]
    btc: BtcComparison
    eur: EurReporting
    ai: PeriodSpending
    owner_test_reading: OwnerTestReading
    split: ScorecardSplit  # W13-A (D300): by strategy version, market type at entry and stance

    @property
    def status(self) -> str:
        return "provisional" if self.provisional else "complete"

    def document(self) -> dict[str, Any]:
        metrics = self.metrics
        btc_versus = metrics.versus_benchmarks.get("hold_btc")
        body: dict[str, Any] = {
            "report": "paper-scorecard", "scorecard_version": SCORECARD_VERSION, "metrics_version": METRICS_VERSION,
            "portfolio_id": self.portfolio_id, "experiment_id": self.experiment_id, "mode": "paper",
            "simulated": True, "status": self.status, "provisional_reasons": list(self.provisional),
            "period": {"start": utc_iso(self.start), "end": utc_iso(self.end), "start_basis": self.start_basis,
                       "days": _text(self.owner_test_reading.days)},
            "currency": self.currency,
            "valuation": {
                "starting_equity": _text(metrics.starting_equity), "ending_equity": _text(metrics.ending_equity),
                "ending_equity_marked": _text(self.ending_equity_marked),
                "exit_fees_on_open_positions": _text(self.exit_fees), "exit_fee_rate": _text(self.btc.taker_rate),
                "carried_in": list(self.carried_in), "equity_points": self.equity_points,
                "unvalued_points": self.unvalued_points, "mark_max_age_minutes": 15,
                "basis": "the ledger's postings and marks; coins held at the start carried in at the start price; "
                         "coins held at the end valued as sold at their mark with the taker fee"},
            "metrics": metrics.to_document(),
            "split": self.split.document(),
            "benchmarks": {
                "cash": {"result": "0", "versus": _text(metrics.versus_cash), "beaten": metrics.versus_cash > 0,
                         "note": CASH_NOTE},
                "hold_btc": self.btc.document(btc_versus)},
            "eur_reporting": self.eur.document(),
            "ai_spending": {**self.ai.document(), "scope": "installation", "note": AI_NOTE},
            "paper_result_pays_for_ai": False,
            "owner_test_reading": self.owner_test_reading.document(),
            "gates": {
                "statement": GATES_STATEMENT,
                "working_software": {"judged_here": False,
                                     "where": "the automatic tests and the installation checks"},
                "economic_evidence": {"judged_here": False,
                                      "this_scorecard": "paper evidence for one period; a good result is not proof "
                                                        "of profit"},
                "real_money": {"authorized": False,
                               "who": "only the owner, separately, through the live enablement checklist"}},
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        body["scorecard_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return body


# --- Reading the records ------------------------------------------------------------------------------------


class _Reader:
    """The two read calls the ledger makes, over one connection (a Database snapshot or a read-only file)."""

    def __init__(self, connection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: tuple = ()):
        return self.connection.execute(sql, params)


@dataclass(frozen=True)
class _Mark:
    price: Decimal
    quote: str
    at: str
    stale: bool


class _Books:
    """Cash and coins from the ledger's own postings (accounts ``cash``/``cash:*`` and ``inventory``)."""

    def __init__(self) -> None:
        self.cash: dict[str, Decimal] = {}
        self.held: dict[str, Decimal] = {}

    def post(self, account: str, asset: str, amount: Decimal) -> None:
        if account == "cash" or account.startswith("cash:"):
            self.cash[asset] = self.cash.get(asset, Decimal("0")) + amount
        elif account == "inventory":
            self.held[asset] = self.held.get(asset, Decimal("0")) + amount

    def holdings(self) -> dict[str, Decimal]:
        return {asset: quantity for asset, quantity in sorted(self.held.items()) if quantity != 0}

    def only_cash_in(self, currency: str, when: str) -> None:
        other = sorted(asset for asset, amount in self.cash.items() if asset != currency and amount != 0)
        if other:
            raise ScorecardUnavailable(
                f"the account holds cash in {', '.join(other)} at {when}; the scorecard counts only {currency} cash "
                f"and coins priced in {currency}, rather than guess a rate")

    def equity(self, currency: str, marks: dict[str, _Mark | None]) -> Decimal | None:
        total = self.cash.get(currency, Decimal("0"))
        for asset, quantity in self.holdings().items():
            mark = marks.get(asset)
            if mark is None:
                return None
            total += quantity * mark.price
        return total


def compute_scorecard(database, *, portfolio_id: str, end: datetime, start: datetime | None = None,
                      cost_profile: str = DEFAULT_COST_PROFILE) -> Scorecard:
    """The scorecard of one paper portfolio for ``(start, end]``, from records stamped up to ``end``.

    ``database`` is a ``Database`` (read in one snapshot) or an open sqlite3 connection with ``sqlite3.Row`` rows,
    for example a read-only one. Raises ``ScorecardUnavailable`` with a plain reason when the period cannot be scored.
    """
    for label, value in (("end", end), ("start", start)):
        if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
            raise ScorecardUnavailable(f"the {label} of the period must be a UTC time")
    snapshot = getattr(database, "snapshot", None)
    if snapshot is None:
        return _compute(database, portfolio_id, start, end, cost_profile)
    with snapshot() as connection:
        return _compute(connection, portfolio_id, start, end, cost_profile)


def _compute(connection, portfolio_id: str, start: datetime | None, end: datetime, profile_id: str) -> Scorecard:
    end_iso = utc_iso(end)
    portfolio = connection.execute(
        "SELECT mode, reporting_currency, experiment_id FROM portfolios WHERE portfolio_id = ?",
        (portfolio_id,)).fetchone()
    if portfolio is None or portfolio["mode"] != "paper":
        raise ScorecardUnavailable(f"there is no paper portfolio {portfolio_id}")
    events = connection.execute(
        """SELECT sequence, kind, payload_json, effective_at, external_ref FROM ledger_events
        WHERE portfolio_id = ? AND effective_at <= ? ORDER BY sequence""", (portfolio_id, end_iso)).fetchall()
    opening = next((row for row in events if row["kind"] == "deposit"), None)
    if opening is None:
        raise ScorecardUnavailable("the account has no opening deposit yet, so there is nothing to score")
    currency = json.loads(opening["payload_json"])["asset"]
    btc_symbol = f"{BENCHMARK_COIN}/{currency}"
    start, basis = _period_start(connection, portfolio_id, start, opening["effective_at"], currency, end_iso)
    if end <= start:
        raise ScorecardUnavailable("a scorecard period ends after it starts")
    start_iso = utc_iso(start)
    _refuse_flows(events, start_iso, end_iso)

    # The running books: the state at the start, then each moment of the period in order.
    books = _Books()
    moments: dict[str, list[tuple[str, str, Decimal]]] = {}
    for row in connection.execute(
            """SELECT t.created_at, p.account, p.asset, p.amount FROM journal_transactions t
            JOIN journal_postings p ON p.transaction_id = t.transaction_id
            WHERE t.portfolio_id = ? AND t.created_at <= ? ORDER BY t.created_at, t.rowid, p.rowid""",
            (portfolio_id, end_iso)):
        amount = Decimal(row["amount"])
        if row["created_at"] <= start_iso:
            books.post(row["account"], row["asset"], amount)
        else:
            moments.setdefault(row["created_at"], []).append((row["account"], row["asset"], amount))
    books.only_cash_in(currency, start_iso)
    start_held = books.holdings()
    ever_held = set(start_held) | {asset for postings in moments.values() for account, asset, _ in postings
                                   if account == "inventory"}
    marks: dict[str, _Mark | None] = {asset: _latest_mark(connection, portfolio_id, asset, start_iso)
                                      for asset in sorted(ever_held)}
    for asset, mark in marks.items():
        if mark is not None and mark.quote != currency:
            raise ScorecardUnavailable(f"the {asset} price is recorded in {mark.quote}, not {currency}; the "
                                       "scorecard does not guess a rate")
    provisional: list[str] = []
    for asset in start_held:
        _check_mark(marks[asset], asset, currency, start, "start", provisional)
    with localcontext(_EXACT):
        starting_equity = books.equity(currency, marks)
    ledger = Ledger(_Reader(connection), FrozenClock(end))
    _agree(ledger, portfolio_id, currency, start_iso, starting_equity, marks, "start")
    if starting_equity is None or starting_equity <= 0:
        raise ScorecardUnavailable("the account was worth nothing at the start of the period")

    start_prices = {asset: marks[asset].price for asset in start_held}
    points, unvalued = _curve(connection, portfolio_id, books, marks, moments, currency, start, start_iso, end_iso,
                              starting_equity, sorted(ever_held))
    books.only_cash_in(currency, end_iso)
    end_held = books.holdings()
    for asset in end_held:
        _check_mark(marks[asset], asset, currency, end, "end", provisional)
    with localcontext(_EXACT):
        marked_end = books.equity(currency, marks)
    _agree(ledger, portfolio_id, currency, end_iso, marked_end, marks, "end")

    profile = _profile(profile_id, start)
    with localcontext(_EXACT):
        exit_fees = sum((quantity * marks[asset].price * profile.taker_rate for asset, quantity in end_held.items()),
                        Decimal("0"))
        ending_equity = marked_end - exit_fees
    points.append(EquityPoint(end, ending_equity))
    if unvalued:
        provisional.append(f"{unvalued} moments when a coin held had no price yet are left out of the worst "
                           "drawdown")

    fills = _fills(events, start, start_iso, end_iso, currency)
    carried = [TradeFill(fill_ref=f"{CARRIED_IN}{asset}", symbol=f"{asset}/{currency}", side="buy",
                         quantity=quantity, quote_amount=quantity * start_prices[asset], fee=Decimal("0"), at=start)
               for asset, quantity in start_held.items()]
    btc = _btc(connection, portfolio_id, btc_symbol, currency, profile, start, start_iso, end, end_iso,
               starting_equity, provisional)
    try:
        metrics = summarize(currency=currency, starting_equity=starting_equity, ending_equity=ending_equity,
                            fills=[*carried, *fills], equity_points=points,
                            benchmarks={"hold_btc": btc.hold} if btc.hold is not None else None)
    except ValidationFailure as error:
        raise ScorecardUnavailable(f"the fills of the period cannot be summed: {error}") from None
    ai = period_spending(connection, start, end)
    return Scorecard(
        portfolio_id=portfolio_id, experiment_id=portfolio["experiment_id"], currency=currency, start=start, end=end,
        start_basis=basis, metrics=metrics, ending_equity_marked=marked_end, exit_fees=exit_fees,
        carried_in=tuple(start_held), equity_points=len(points), unvalued_points=unvalued,
        provisional=tuple(provisional), btc=btc,
        eur=_eur(ledger, portfolio["reporting_currency"], currency, start_iso, end_iso, metrics, btc, ai),
        ai=ai, owner_test_reading=_reading(start, end, metrics),
        split=_split(connection, portfolio_id, events, metrics, marks, profile.taker_rate))


def _split(connection, portfolio_id: str, events, metrics: TradeMetrics, marks: dict[str, _Mark | None],
           taker_rate: Decimal) -> ScorecardSplit:
    """The split of the scored trades (D300), or why it can't be read; the scorecard itself never depends on it."""
    try:
        trade_choices, open_choices = _entry_choices(connection, portfolio_id, events, metrics)
        with localcontext(_EXACT):
            values = []
            for position in metrics.open_positions:
                marked = position.quantity * marks[position.symbol.split("/")[0]].price
                values.append(marked - marked * taker_rate)  # as the ending equity values it (exit_fees)
        return split_scorecard(metrics, trade_choices=trade_choices, open_choices=open_choices, open_values=values)
    except (TradeGraphError, sqlite3.DatabaseError, ArithmeticError, LookupError, TypeError, ValueError,
            AttributeError) as error:
        return ScorecardSplit.unavailable(f"the split by strategy version, market type and stance can't be read "
                                          f"from the records ({type(error).__name__})")


def _entry_choices(connection, portfolio_id: str, events,
                   metrics: TradeMetrics) -> tuple[list[EntryChoice], list[EntryChoice]]:
    """The choice behind each trade's and open position's first buy, from the records (D300): the fill's order intent,
    the day-plan entry that owns it and its plan (strategy version), the plan's desk record (W12-A: the stance in the
    same answer, the playbook revision) and the coin's newest label at the fill (W11-A, point in time)."""
    intents: dict[str, str | None] = {}
    for row in events:
        if row["kind"] == "fill":
            fill = json.loads(row["payload_json"]).get("fill") or {}
            intents[row["external_ref"]] = fill.get("intent_id") if isinstance(fill, dict) else None
    items = [*metrics.round_trips, *metrics.open_positions]
    plans = _plans_of(connection, sorted({intents.get(item.fill_refs[0]) for item in items} - {None}))
    records = read_day_plan_desk_records(connection, portfolio_id=portfolio_id,
                                         plan_ids=sorted({plan_id for plan_id, _ in plans.values()})) if plans else {}
    choices: list[EntryChoice] = []
    for item in items:
        first = item.fill_refs[0]
        if first.startswith(CARRIED_IN):
            choices.append(EntryChoice.carried_in())
            continue
        plan = plans.get(intents.get(first))
        label = read_latest_market_types(connection, symbols=[item.symbol], as_of=item.opened_at).get(item.symbol)
        market = ("missing" if label is None else label.market_type if label.is_fresh(item.opened_at, LABEL_MAX_AGE)
                  else "stale")
        record = None if plan is None else records.get(plan[0])
        choices.append(EntryChoice(
            strategy=NO_DAY_PLAN if plan is None else plan[1], market_type=market,
            stance=record.stance if record is not None and record.stance else NOT_RECORDED,
            plan_id=None if plan is None else plan[0], label_id=None if label is None else label.label_id,
            labelled_at=None if label is None else label.as_of, stance_id=None if record is None else record.stance_id,
            playbook_revision=None if record is None else record.playbook_revision))
    trades = len(metrics.round_trips)
    return choices[:trades], choices[trades:]


def _plans_of(connection, intent_ids: list[str]) -> dict[str, tuple[str, str]]:
    """``{intent_id: (plan_id, strategy_ref)}`` of the day-plan entries that own these buys (none before 0034)."""
    if not intent_ids:
        return {}
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {"day_plans", "day_plan_entries"} <= tables:
        return {}
    found: dict[str, tuple[str, str]] = {}
    for offset in range(0, len(intent_ids), 500):
        chunk = intent_ids[offset:offset + 500]
        for row in connection.execute(
                f"""SELECT e.intent_id, p.plan_id, p.strategy_ref FROM day_plan_entries e
                JOIN day_plans p ON p.plan_id = e.plan_id WHERE e.intent_id IN ({",".join("?" for _ in chunk)})""",
                tuple(chunk)):
            found[row["intent_id"]] = (row["plan_id"], row["strategy_ref"])
    return found


def _period_start(connection, portfolio_id: str, start: datetime | None, opened_iso: str, currency: str,
                  end_iso: str) -> tuple[datetime, str]:
    opened = parse_utc(opened_iso)
    if start is not None:
        if start < opened:
            raise ScorecardUnavailable("the period starts before the opening deposit, when the account had no money "
                                       "yet")
        return start, "requested"
    first_btc = connection.execute(
        """SELECT MIN(observed_at) FROM valuation_marks WHERE portfolio_id = ? AND asset = ? AND quote_currency = ?
        AND observed_at <= ?""", (portfolio_id, BENCHMARK_COIN, currency, end_iso)).fetchone()[0]
    if first_btc is not None and first_btc > opened_iso:
        return parse_utc(first_btc), "opening_deposit_and_btc_price"
    return opened, "opening_deposit"


def _refuse_flows(events, start_iso: str, end_iso: str) -> None:
    for row in events:
        if not start_iso < row["effective_at"] <= end_iso:
            continue
        if row["kind"] in ("deposit", "withdraw"):
            raise ScorecardUnavailable(
                f"a deposit or withdrawal was recorded at {row['effective_at']}, inside the period; score a period "
                "without one (resetting a paper account opens a new one)")
        if row["kind"] == "expense":
            payload = json.loads(row["payload_json"])
            if payload.get("embedded") and not payload.get("settles"):
                raise ScorecardUnavailable(
                    f"an operating cost was paid from the paper cash at {row['effective_at']}, inside the period; "
                    "the scorecard scores trading only")


def _latest_mark(connection, portfolio_id: str, asset: str, at_iso: str, quote: str | None = None) -> _Mark | None:
    """The mark the ledger uses at ``at_iso``: the newest observed then, the first recorded of equal times."""
    where = "" if quote is None else " AND quote_currency = ?"
    row = connection.execute(
        f"""SELECT mark, quote_currency, observed_at, stale FROM valuation_marks
        WHERE portfolio_id = ? AND asset = ? AND observed_at <= ?{where}
        ORDER BY observed_at DESC, rowid ASC LIMIT 1""",
        (portfolio_id, asset, at_iso) + (() if quote is None else (quote,))).fetchone()
    if row is None:
        return None
    return _Mark(Decimal(row["mark"]), row["quote_currency"], row["observed_at"], bool(row["stale"]))


def _check_mark(mark: _Mark | None, asset: str, currency: str, at: datetime, edge: str, provisional: list) -> None:
    if mark is None:
        raise ScorecardUnavailable(f"no {asset} price was recorded by the {edge} of the period, so the {asset} held "
                                   "then cannot be valued")
    if mark.quote != currency:
        raise ScorecardUnavailable(f"the {asset} price is recorded in {mark.quote}, not {currency}; the scorecard "
                                   "does not guess a rate")
    _age(mark, asset, at, edge, provisional)


def _age(mark: _Mark, asset: str, at: datetime, edge: str, provisional: list) -> None:
    age = at - parse_utc(mark.at)
    if age > MARK_MAX_AGE:
        minutes = int(age.total_seconds() // 60)
        provisional.append(f"the {asset} price used at the {edge} of the period is {minutes} minutes old (more "
                           "than 15)")
    if mark.stale:
        provisional.append(f"the {asset} price used at the {edge} of the period is marked stale")


def _agree(ledger: Ledger, portfolio_id: str, currency: str, at_iso: str, value: Decimal | None,
           marks: dict[str, _Mark | None], edge: str) -> None:
    """The ledger's own books at ``at_iso``, valued at the same prices, must give the same equity."""
    books = ledger.books(portfolio_id, at_iso)
    priced = [Mark(asset, mark.price, mark.quote, mark.at, mark.stale, "scorecard")
              for asset, mark in marks.items() if mark is not None]
    view = native_equity(books, currency=currency, marks=priced, rates=[], at=at_iso)
    if view.equity is None or value is None or view.equity != value:
        raise ScorecardUnavailable(f"the scorecard's running books do not agree with the ledger at the {edge} of "
                                   "the period, so nothing is scored")


def _curve(connection, portfolio_id: str, books: _Books, marks: dict[str, _Mark | None],
           moments: dict[str, list[tuple[str, str, Decimal]]], currency: str, start: datetime, start_iso: str,
           end_iso: str, starting_equity: Decimal, assets: list[str]) -> tuple[list[EquityPoint], int]:
    """Equity at the start, after every posting and at every new price of a coin held; the end point comes later.

    Applies the period's postings to ``books`` and leaves the newest marks in ``marks``. Only the prices that can
    move the curve are read (W10-B, D272): each coin's prices while it is held, its newest price when a posting makes
    it held, and every coin's newest price at the end. A price of a coin not held never made a point, so the curve,
    its points and the marks left behind are the same as when every price of every coin held was read.
    """
    prices: dict[str, list[tuple[str, Decimal, bool]]] = {}
    if assets:
        holes = ",".join("?" for _ in assets)
        other = connection.execute(
            f"""SELECT asset, quote_currency FROM valuation_marks WHERE portfolio_id = ? AND observed_at > ?
            AND observed_at <= ? AND asset IN ({holes}) AND quote_currency != ? LIMIT 1""",
            (portfolio_id, start_iso, end_iso, *assets, currency)).fetchone()
        if other is not None:
            raise ScorecardUnavailable(f"the {other['asset']} price is recorded in {other['quote_currency']}, not "
                                       f"{currency}; the scorecard does not guess a rate")
    windows, entries = _held(books, moments, start_iso, end_iso)
    for asset, since, until in windows:
        for mark, observed_at, stale in connection.execute(
                """SELECT mark, observed_at, stale FROM valuation_marks
                WHERE portfolio_id = ? AND asset = ? AND observed_at > ? AND observed_at <= ?
                ORDER BY observed_at, rowid""", (portfolio_id, asset, since, until)):
            prices.setdefault(observed_at, []).append((asset, Decimal(mark), bool(stale)))
    held = books.holdings()
    points = [EquityPoint(start, starting_equity)]
    unvalued = 0
    with localcontext(_EXACT):
        for moment in sorted(moments.keys() | prices.keys()):
            postings = moments.get(moment)
            if postings:
                for account, asset, amount in postings:
                    books.post(account, asset, amount)
                    if account == "inventory":
                        quantity = books.held[asset]
                        if quantity:
                            held[asset] = quantity
                        else:
                            held.pop(asset, None)
                books.only_cash_in(currency, moment)
                for asset in entries.get(moment, ()):  # held from now: its price is the newest one by now
                    marks[asset] = _latest_mark(connection, portfolio_id, asset, moment)
            priced = False
            for asset, price, stale in prices.get(moment, ()):
                current = marks[asset]
                if current is not None and current.at == moment:
                    continue  # the ledger uses the first mark recorded at one time
                marks[asset] = _Mark(price, currency, moment, stale)
                priced = priced or asset in held
            if moment == end_iso or not (postings or priced):
                continue
            value = books.cash.get(currency, Decimal("0"))
            for asset, quantity in held.items():
                mark = marks[asset]
                if mark is None:
                    value = None
                    break
                value += quantity * mark.price
            if value is None:
                unvalued += 1
                continue
            points.append(EquityPoint(datetime.fromisoformat(moment), value))
    for asset in assets:
        marks[asset] = _latest_mark(connection, portfolio_id, asset, end_iso)
    return points, unvalued


def _held(books: _Books, moments: dict[str, list[tuple[str, str, Decimal]]], start_iso: str,
          end_iso: str) -> tuple[list[tuple[str, str, str]], dict[str, list[str]]]:
    """When each coin is held in the period, from the books at the start and the period's postings, as the curve
    applies them: windows ``(coin, since, until)`` (from the start or the moment a posting made the coin held, to the
    moment one emptied it or the end) and, for each moment, the coins it makes held."""
    quantities = dict(books.held)
    since = {asset: start_iso for asset, quantity in sorted(quantities.items()) if quantity}
    windows: list[tuple[str, str, str]] = []
    entries: dict[str, list[str]] = {}
    with localcontext(_EXACT):
        for moment in sorted(moments):
            touched: dict[str, None] = {}
            for account, asset, amount in moments[moment]:
                if account == "inventory":
                    quantities[asset] = quantities.get(asset, Decimal("0")) + amount
                    touched[asset] = None
            for asset in touched:
                if quantities[asset] and asset not in since:
                    since[asset] = moment
                    entries.setdefault(moment, []).append(asset)
                elif not quantities[asset] and asset in since:
                    windows.append((asset, since.pop(asset), moment))
    windows.extend((asset, began, end_iso) for asset, began in since.items())
    return windows, entries


def _fills(events, start: datetime, start_iso: str, end_iso: str, currency: str) -> list[TradeFill]:
    found: list[tuple[datetime, int, TradeFill]] = []
    for row in events:
        if row["kind"] != "fill" or not start_iso < row["effective_at"] <= end_iso:
            continue
        payload = json.loads(row["payload_json"])
        record = FillRecord.model_validate(payload["fill"])
        if payload["quote_asset"] != currency:
            raise ScorecardUnavailable(f"a fill on {record.symbol} is priced in {payload['quote_asset']}, not "
                                       f"{currency}; the scorecard does not guess a rate")
        try:
            trade = TradeFill.from_fill_record(record, fill_ref=row["external_ref"],
                                               base_asset=payload["base_asset"], quote_asset=payload["quote_asset"])
        except ValidationFailure as error:
            raise ScorecardUnavailable(f"the fill {row['external_ref']} cannot be scored: {error}") from None
        if trade.at < start:  # a late fill recorded inside the period
            trade = replace(trade, at=start)
        found.append((trade.at, row["sequence"], trade))
    return [trade for _, _, trade in sorted(found, key=lambda item: (item[0], item[1]))]


def _profile(profile_id: str, start: datetime):
    try:
        return load_cost_profiles().effective(profile_id, start.date())
    except ValidationFailure as error:
        raise ScorecardUnavailable(f"the fees to compare with cannot be read: {error}") from None


def _btc(connection, portfolio_id: str, symbol: str, currency: str, profile, start: datetime, start_iso: str,
         end: datetime, end_iso: str, capital: Decimal, provisional: list) -> BtcComparison:
    lot = DEFAULT_LOT
    row = connection.execute("SELECT document_json FROM instruments WHERE venue = 'paper' AND symbol = ?",
                             (symbol,)).fetchone()
    if row is not None:
        lot = InstrumentRules.model_validate_json(row["document_json"]).quantity_increment
    unavailable = BtcComparison(available=False, reason=None, symbol=symbol, cost_profile_ref=profile.ref,
                                cost_profile_sha256=profile.content_sha256, taker_rate=profile.taker_rate,
                                lot_size=lot)
    first = _latest_mark(connection, portfolio_id, BENCHMARK_COIN, start_iso, quote=currency)
    last = _latest_mark(connection, portfolio_id, BENCHMARK_COIN, end_iso, quote=currency)
    for mark, edge in ((first, "start"), (last, "end")):
        if mark is None:
            return replace(unavailable, reason=f"no {symbol} price was recorded by the {edge} of the period, so "
                                               "holding BTC cannot be valued")
    reasons: list[str] = []
    _age(first, BENCHMARK_COIN, start, "start", reasons)
    _age(last, BENCHMARK_COIN, end, "end", reasons)
    try:
        hold = hold_benchmark(symbol=symbol, capital=capital, buy_price=first.price, sell_price=last.price,
                              fee_rate=profile.taker_rate, quantity_increment=lot, bought_at=start, sold_at=end)
    except ValidationFailure as error:
        return replace(unavailable, reason=f"holding BTC cannot be valued: {error}")
    provisional.extend(reasons)
    return replace(unavailable, available=True, start_mark_at=parse_utc(first.at), end_mark_at=parse_utc(last.at),
                   hold=hold)


def _eur(ledger: Ledger, reporting: str, currency: str, start_iso: str, end_iso: str, metrics: TradeMetrics,
         btc: BtcComparison, ai: PeriodSpending) -> EurReporting:
    rate_start, stale_start = ledger.fx_rate(currency, reporting, start_iso)
    rate_end, stale_end = ledger.fx_rate(currency, reporting, end_iso)
    for rate, edge in ((rate_start, "start"), (rate_end, "end")):
        if rate is None:
            return EurReporting(currency=reporting, available=False,
                                reason=f"no {currency} to {reporting} reference rate was recorded by the {edge} of "
                                       "the period")
    with localcontext(_EXACT):
        starting = metrics.starting_equity * rate_start
        ending = metrics.ending_equity * rate_end
        baseline = metrics.starting_equity * rate_end
        versus_btc = metrics.versus_benchmarks.get("hold_btc")
        ai_total = ai.total.total
        return EurReporting(
            currency=reporting, available=True, rate_start=rate_start, rate_end=rate_end,
            stale=stale_start or stale_end, starting_equity=starting, ending_equity=ending, result=ending - starting,
            currency_effect=baseline - starting, versus_cash=ending - baseline,
            hold_btc_result=None if btc.hold is None else btc.hold.result * rate_end,
            versus_hold_btc=None if versus_btc is None else versus_btc * rate_end,
            ai_spending_total=None if ai_total is None else ai_total * rate_end)


def _reading(start: datetime, end: datetime, metrics: TradeMetrics) -> OwnerTestReading:
    span = end - start
    with localcontext(_EXACT):
        seconds = Decimal(span.days) * _SECONDS_PER_DAY + Decimal(span.seconds) \
            + Decimal(span.microseconds) / Decimal(1_000_000)
        days = (seconds / _SECONDS_PER_DAY).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    long_enough = days >= MINIMUM_DAYS and metrics.trades >= MINIMUM_TRADES
    beats_cash = metrics.versus_cash > 0
    versus_btc = metrics.versus_benchmarks.get("hold_btc")
    beats_btc = None if versus_btc is None else versus_btc > 0
    keep_going = None if not long_enough or beats_btc is None else beats_cash and beats_btc
    return OwnerTestReading(days=days, trades=metrics.trades, long_enough=long_enough, beats_cash=beats_cash,
                            beats_btc=beats_btc, keep_going=keep_going)


# --- Plain text (the owner's command) -----------------------------------------------------------------------


def _signed(value: Decimal) -> str:
    return ("+" if value > 0 else "") + canonical_decimal(value)


def _compare(value: Decimal, currency: str) -> str:
    if value == 0:
        return "the same"
    return f"{canonical_decimal(abs(value))} {currency} {'better' if value > 0 else 'worse'}"


def _percent(value: Decimal | None) -> str:
    if value is None:
        return "not known"
    return canonical_decimal((value * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)) + "%"


def _money(value: Decimal | None, currency: str) -> str:
    return "not known" if value is None else f"{canonical_decimal(value)} {currency}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def format_scorecard_text(card: Scorecard) -> str:
    metrics, currency = card.metrics, card.currency
    days = card.owner_test_reading.days
    span = f"{canonical_decimal(days)} day" + ("" if days == 1 else "s")
    lines = [f"Paper scorecard from {utc_iso(card.start)} to {utc_iso(card.end)} ({span}), in {currency}. "
             "This is simulated paper trading, not money earned.",
             "Status: complete." if not card.provisional else
             "Status: provisional, because " + "; ".join(card.provisional) + "."]
    held = ""
    if metrics.open_positions:
        coins = ", ".join(f"{canonical_decimal(item.quantity)} {item.symbol.split('/')[0]}"
                          for item in metrics.open_positions)
        held = (f" ({coins} still held, valued as sold at the end with the {_money(card.exit_fees, currency)} "
                "fee)")
    lines.append(f"Result after fees: {_signed(metrics.result)} {currency} ({_percent(metrics.result_fraction)}), "
                 f"from {_money(metrics.starting_equity, currency)} to {_money(metrics.ending_equity, currency)}"
                 f"{held}.")
    lines.append(f"Compared with holding cash: {_compare(metrics.versus_cash, currency)} (cash earns no interest).")
    versus_btc = metrics.versus_benchmarks.get("hold_btc")
    if versus_btc is None:
        lines.append(f"Compared with holding BTC: not available, because {card.btc.reason}.")
    else:
        lines.append(f"Compared with holding BTC: {_compare(versus_btc, currency)} (holding BTC made "
                     f"{_signed(card.btc.hold.result)} {currency} after its fees).")
    lines.append(f"Trades: {metrics.trades} ({metrics.wins} won, {metrics.losses} lost, {metrics.flat_trades} even); "
                 f"win rate {_percent(metrics.win_rate)}; average win {_money(metrics.average_win, currency)}; "
                 f"average loss {_money(metrics.average_loss, currency)}.")
    lines.extend(_split_lines(card.split, currency))
    if card.carried_in:
        lines.append(f"Coins already held at the start, counted as bought at the start price: "
                     f"{', '.join(card.carried_in)}.")
    drawdown = metrics.worst_drawdown
    lines.append(f"Fees paid: {_money(metrics.fees_paid, currency)}. Worst fall from a peak: "
                 f"{_money(drawdown.amount, currency)} ({_percent(drawdown.fraction)}). Longest losing streak: "
                 f"{_plural(metrics.worst_losing_streak, 'trade')}.")
    eur = card.eur
    if eur.available:
        stale = " (a rate used is stale)" if eur.stale else ""
        lines.append(f"In {eur.currency}{stale}: from {_money(eur.starting_equity, eur.currency)} to "
                     f"{_money(eur.ending_equity, eur.currency)}; the dollar's own move was "
                     f"{_signed(eur.currency_effect)} {eur.currency}, so compared with holding dollars the result is "
                     f"{_compare(eur.versus_cash, eur.currency)}.")
    else:
        lines.append(f"In {eur.currency}: not available, because {eur.reason}.")
    total = card.ai.total
    unknown = ""
    if total.unresolved_calls:
        unknown = (f"; not known yet: up to {_money(total.unresolved, 'USD')} "
                   f"({_plural(total.unresolved_calls, 'call')}, not free)")
    lines.append(f"AI spending in the same period: {_plural(total.calls, 'paid call')}, known "
                 f"{_money(total.known, 'USD')}{unknown}; {_plural(card.ai.codex.turns, 'Codex turn')} on the "
                 f"subscription. Test calls not counted: {card.ai.synthetic_calls}.")
    lines.append(AI_NOTE)
    reading = card.owner_test_reading
    if reading.keep_going is None:
        why = ("the test is shorter than 4 weeks and 30 completed trades" if not reading.long_enough
               else "holding BTC could not be valued")
        verdict = f"no reading yet, because {why}"
    else:
        verdict = "keep going" if reading.keep_going else "not keep going"
    lines.append(f"Your test rule (D138 item 7): {verdict}. Beats cash: {'yes' if reading.beats_cash else 'no'}; "
                 f"beats BTC: {'not known' if reading.beats_btc is None else 'yes' if reading.beats_btc else 'no'}. "
                 f"{READING_NOTE}")
    lines.append(GATES_STATEMENT)
    return "\n".join(lines)


_SPLIT_WORDS = {
    NO_DAY_PLAN: "no day plan", CARRIED_IN_GROUP: "carried in (held at the start)",
    "stale": "stale (label older than 2 hours)", "missing": "missing (no label)", "buy_dips": "buy dips",
    "follow_trend": "follow the trend", NOT_RECORDED: "not recorded",
}


def _split_row(name: str, row: GroupResult, currency: str) -> str:
    words = f"{_SPLIT_WORDS.get(name, name)} {_plural(row.trades, 'trade')}"
    if row.trades:
        words += f" ({row.wins} won, {row.losses} lost)"
    if row.open_positions:
        words += f", {row.open_positions} still held"
    return f"{words}, {_signed(row.result)} {currency}"


def _split_lines(split: ScorecardSplit, currency: str) -> list[str]:
    """The split in plain words (D300): only the groups with a trade or a position still held."""
    if not split.available:
        return [f"The split by strategy version, market type and stance is not available: {split.reason}."]
    lines = []
    for title, rows in (("By strategy version", split.by_strategy), ("By market type at entry", split.by_market_type),
                        ("By the desk's stance", split.by_stance)):
        shown = [_split_row(row.group, row, currency) for row in rows if row.trades or row.open_positions]
        if shown:
            lines.append(f"{title}: {'; '.join(shown)}.")
    if split.outside_trades:
        lines.append(f"Not from a trade: {_signed(split.outside_trades)} {currency}.")
    return lines


__all__ = [
    "AI_NOTE", "BENCHMARK_COIN", "DEFAULT_COST_PROFILE", "GATES_STATEMENT", "MARK_MAX_AGE", "MINIMUM_DAYS",
    "MINIMUM_TRADES", "SCORECARD_VERSION", "BtcComparison", "EurReporting", "OwnerTestReading", "Scorecard",
    "ScorecardUnavailable", "compute_scorecard", "format_scorecard_text",
    # W13-A (D300): the split by strategy version, market type at entry and stance.
    "CARRIED_IN_GROUP", "LABEL_MAX_AGE", "MARKET_TYPES_AT_ENTRY", "NOT_RECORDED", "NO_DAY_PLAN", "SPLIT_BASIS",
    "SPLIT_VERSION", "STANCES_AT_ENTRY", "EntryChoice", "ScorecardSplit", "split_scorecard",
]
