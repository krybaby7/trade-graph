"""AI spending read model (T40): Perplexity and Jev spending from receipts, Codex use per department.

Built only from persisted records and read-only. Each paid call is one budget reservation, and each is counted
once, in exactly one of three kinds (D175):

- ``actual``: a receipt priced from the usage the provider reported (``committed`` or ``reconciled``);
- ``estimated``: a conservative charge, made at the reserved maximum because the usage was never known;
- ``unresolved``: no receipt yet (the call is open, or its usage is not known), held at its reserved maximum.
  Unresolved is never shown as zero: its amount is the hold, or ``None`` when even that cannot be valued.

Amounts are USD, the services' own currency (the price cards are USD): receipts at their USD price, and EUR
holds and conservative charges divided by the FX rate they were reserved with, exactly as the owner's Perplexity
limit counts them (``PerplexitySpendGuard``). Test calls (``synthetic``) are kept apart and never added.

Practice money never enters any figure: no query here reads the paper account, so practice gains can never be
shown as paying for AI (T40 acceptance). The testing alert and the prepaid credit are information only; nothing
here admits, refuses or pauses a call (D116, D125, D131).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from trade_graph.adapters.persistence.ai_spending_schema import TABLE as OWNER_RECORDS
from trade_graph.application.jev_checks import PURPOSE as JEV_PURPOSE
from trade_graph.application.jev_checks import ROLE as JEV_ROLE
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.money import canonical_decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
SERVICES = ("perplexity", "jev")
# D131 (Perplexity testing) and D138 item 10 (Jev testing). The owner may record another amount (migration 0032).
DEFAULT_ALERT_USD = {"perplexity": Decimal("20"), "jev": Decimal("5")}
# The AI departments in HQ map order; any other role spending on Perplexity is reported as "other".
DEPARTMENTS = ("leader", "research", "trader", "learning", "optimisation", "engineer")
CODEX_WINDOW = timedelta(days=7)
KNOWN_RECEIPTS = ("committed", "reconciled")
_RECEIPT_RANK = {"reconciled": 0, "committed": 1, "conservative_charge": 2, "uncertain": 3}
_USAGE_FIELDS = ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "billed_output_tokens")

# One row per call (and, rarely, per extra receipt of the same call; the most settled receipt wins). Perplexity
# calls are those priced on a Perplexity card; Jev checks carry their own role and purpose (T44).
_CALLS = """SELECT b.reservation_id, b.role, b.purpose, b.price_card_id, b.state, b.amount, b.currency,
        b.fx_rate_value, b.synthetic, b.created_at, r.status AS receipt_status, r.native_cost, r.native_currency,
        r.created_at AS receipt_at
    FROM budget_reservations b
    LEFT JOIN usage_receipts r ON r.reservation_id = b.reservation_id AND r.created_at <= ?
    WHERE b.created_at <= ? AND ((b.role = ? AND b.purpose = ?) OR b.price_card_id IN ({cards}))
    ORDER BY b.rowid"""
# Every paid call made in a period (the paper scorecard, T42): any price card, so nothing paid is left out.
_PERIOD_CALLS = """SELECT b.reservation_id, b.role, b.purpose, b.price_card_id, b.state, b.amount, b.currency,
        b.fx_rate_value, b.synthetic, b.created_at, r.status AS receipt_status, r.native_cost, r.native_currency,
        r.created_at AS receipt_at
    FROM budget_reservations b
    LEFT JOIN usage_receipts r ON r.reservation_id = b.reservation_id AND r.created_at <= ?
    WHERE b.created_at > ? AND b.created_at <= ?
    ORDER BY b.rowid"""
_CARDS = """SELECT price_card_id, json_extract(document_json, '$.provider') AS provider,
    json_extract(document_json, '$.currency') AS currency FROM price_cards"""
_CODEX = """SELECT role, state, cost_status, usage_json FROM subscription_invocations
    WHERE synthetic = 0 AND created_at > ? AND created_at <= ?"""


# --- Results ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tally:
    """Real spending in USD; ``unresolved`` is the held maximum, ``None`` only when it cannot be valued.

    The counts say which kinds exist, so "no such call" (nothing to show) is never confused with a zero cost.
    """

    calls: int = 0
    actual: Decimal = ZERO
    estimated: Decimal = ZERO
    unresolved: Decimal | None = ZERO
    unresolved_calls: int = 0
    actual_calls: int = 0
    estimated_calls: int = 0

    @property
    def known(self) -> Decimal:
        return self.actual + self.estimated

    @property
    def known_calls(self) -> int:
        return self.actual_calls + self.estimated_calls

    @property
    def total(self) -> Decimal | None:
        """Known spending plus unresolved calls at their maximum (what the owner limit counts)."""
        return None if self.unresolved is None else self.known + self.unresolved

    def __add__(self, other: Tally) -> Tally:
        held = None if self.unresolved is None or other.unresolved is None else self.unresolved + other.unresolved
        return Tally(calls=self.calls + other.calls, actual=self.actual + other.actual,
                     estimated=self.estimated + other.estimated, unresolved=held,
                     unresolved_calls=self.unresolved_calls + other.unresolved_calls,
                     actual_calls=self.actual_calls + other.actual_calls,
                     estimated_calls=self.estimated_calls + other.estimated_calls)

    def document(self) -> dict[str, Any]:
        return {"calls": self.calls, "actual_calls": self.actual_calls, "actual_usd": _text(self.actual),
                "estimated_calls": self.estimated_calls, "estimated_usd": _text(self.estimated),
                "unresolved_calls": self.unresolved_calls, "unresolved_usd": _text(self.unresolved),
                "total_usd": _text(self.total)}


@dataclass(frozen=True)
class Credit:
    """A prepaid balance the owner read on the provider's page, and the spending recorded since then."""

    amount: Decimal
    observed_at: datetime
    since: Tally

    @property
    def left(self) -> Decimal | None:
        """The balance less everything since, unresolved calls at their maximum. Information only."""
        total = self.since.total
        return None if total is None else self.amount - total

    def document(self) -> dict[str, Any]:
        return {"amount_usd": _text(self.amount), "observed_at": utc_iso(self.observed_at),
                "since": self.since.document(), "left_usd": _text(self.left)}


@dataclass(frozen=True)
class ServiceSpend:
    service: str
    status: str  # off, testing or monthly_limit (Perplexity's recorded owner policy; Jev: off or testing)
    testing_start: datetime | None
    testing_end: datetime | None
    departments: dict[str, Tally]
    testing: Tally
    synthetic_calls: int
    synthetic_known: Decimal
    alert_usd: Decimal
    alert_source: str  # default or owner
    alert_state: str | None  # below, maybe_passed or passed; None once testing has ended
    alert_since: datetime | None
    credit: Credit | None
    monthly_limit_usd: Decimal | None = None
    month_start: datetime | None = None
    month: Tally | None = None

    def document(self) -> dict[str, Any]:
        alert = None if self.alert_state is None else {
            "threshold_usd": _text(self.alert_usd), "source": self.alert_source, "state": self.alert_state,
            "since": _iso(self.alert_since)}
        limit = None if self.monthly_limit_usd is None else {
            "limit_usd": _text(self.monthly_limit_usd), "month_start": _iso(self.month_start),
            "month": self.month.document() if self.month else None}
        return {"status": self.status, "testing_start": _iso(self.testing_start),
                "testing_end": _iso(self.testing_end), "testing": self.testing.document(),
                "departments": {name: tally.document() for name, tally in self.departments.items()},
                "synthetic": {"calls": self.synthetic_calls, "known_usd": _text(self.synthetic_known)},
                "alert": alert, "credit": self.credit.document() if self.credit else None,
                "monthly_limit": limit}


@dataclass(frozen=True)
class CodexDepartment:
    turns: int = 0
    unreported_turns: int = 0
    reported_use: int = 0


@dataclass(frozen=True)
class CodexUse:
    """Codex subscription turns per department. The allowance itself is shared with the owner's own use."""

    start: datetime
    end: datetime
    departments: dict[str, CodexDepartment] = field(default_factory=dict)

    @property
    def turns(self) -> int:
        return sum(item.turns for item in self.departments.values())

    @property
    def unreported_turns(self) -> int:
        return sum(item.unreported_turns for item in self.departments.values())

    @property
    def reported_use(self) -> int:
        return sum(item.reported_use for item in self.departments.values())

    def share(self, role: str) -> Decimal | None:
        """Percent of the team's reported use; ``None`` when nothing was reported (never a guessed zero)."""
        total = self.reported_use
        item = self.departments.get(role)
        if not total or item is None:
            return None
        return Decimal(item.reported_use) * HUNDRED / Decimal(total)

    def document(self) -> dict[str, Any]:
        return {"start": utc_iso(self.start), "end": utc_iso(self.end), "turns": self.turns,
                "unreported_turns": self.unreported_turns,
                "departments": {role: {"turns": item.turns, "unreported_turns": item.unreported_turns,
                                       "share_percent": _text(self.share(role))}
                                for role, item in self.departments.items()}}


@dataclass(frozen=True)
class AiSpendingReport:
    as_of: datetime
    perplexity: ServiceSpend
    jev: ServiceSpend
    codex: CodexUse

    def services(self) -> tuple[ServiceSpend, ServiceSpend]:
        return self.perplexity, self.jev

    def document(self) -> dict[str, Any]:
        return {"report": "ai-spending", "as_of": utc_iso(self.as_of), "currency": "USD",
                "paper_money_used": False,
                "services": {item.service: item.document() for item in self.services()},
                "codex": self.codex.document()}


# --- Read model -----------------------------------------------------------------------------------------


class _Acc:
    """A mutable running tally for one pass over the calls (``Tally`` is the frozen result)."""

    __slots__ = ("calls", "actual", "estimated", "unresolved", "unresolved_calls", "actual_calls", "estimated_calls")

    def __init__(self) -> None:
        self.calls = self.unresolved_calls = self.actual_calls = self.estimated_calls = 0
        self.actual = self.estimated = ZERO
        self.unresolved: Decimal | None = ZERO

    def add(self, kind: str, amount: Decimal | None) -> None:
        self.calls += 1
        if kind == "actual":
            self.actual += amount
            self.actual_calls += 1
        elif kind == "estimated":
            self.estimated += amount
            self.estimated_calls += 1
        else:
            self.unresolved = None if self.unresolved is None or amount is None else self.unresolved + amount
            self.unresolved_calls += 1

    def tally(self) -> Tally:
        return Tally(calls=self.calls, actual=self.actual, estimated=self.estimated, unresolved=self.unresolved,
                     unresolved_calls=self.unresolved_calls, actual_calls=self.actual_calls,
                     estimated_calls=self.estimated_calls)


class _Scope:
    """One service's accumulators: the testing period per line, since the credit reading, this month, tests."""

    def __init__(self, start: str | None, end: str | None, credit_from: str | None, month: str | None) -> None:
        self.start, self.end, self.credit_from, self.month_prefix = start, end, credit_from, month
        self.lines: dict[str, _Acc] = {}
        self.credit = _Acc() if credit_from is not None else None
        self.month = _Acc() if month is not None else None
        self.known: list[tuple[str, Decimal]] = []  # (settled at, amount) of known testing calls
        self.real_calls = self.synthetic_calls = 0
        self.synthetic_known = ZERO

    def add(self, line: str, synthetic: bool, created_at: str, kind: str, amount: Decimal | None,
            settled_at: str | None) -> None:
        if synthetic:
            self.synthetic_calls += 1
            if kind != "unresolved" and amount is not None:
                self.synthetic_known += amount
            return
        self.real_calls += 1
        if (self.start is None or created_at >= self.start) and (self.end is None or created_at < self.end):
            accumulator = self.lines.get(line)
            if accumulator is None:
                accumulator = self.lines[line] = _Acc()
            accumulator.add(kind, amount)
            if kind != "unresolved":
                self.known.append((settled_at or created_at, amount))
        if self.credit is not None and created_at >= self.credit_from:
            self.credit.add(kind, amount)
        if self.month is not None and created_at[:7] == self.month_prefix:
            self.month.add(kind, amount)


def spending_report(database, now: datetime) -> AiSpendingReport:
    """One report as of ``now``; records stamped later are not counted yet."""
    now_iso = utc_iso(now)
    owner = _owner_records(database, now_iso)
    status, start, end, limit = _perplexity_policy(database, now_iso)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    credit_from = {service: _iso(owner[(service, "prepaid_credit")][1]) if (service, "prepaid_credit") in owner
                   else None for service in SERVICES}
    scopes = {"perplexity": _Scope(_iso(start), _iso(end), credit_from["perplexity"],
                                   utc_iso(month_start)[:7] if limit is not None else None),
              "jev": _Scope(None, None, credit_from["jev"], None)}
    for service, *call in _calls(database, now_iso):
        scopes[service].add(*call)
    perplexity = _service("perplexity", scopes["perplexity"], owner, (status, start, end, limit), month_start)
    jev_status = "testing" if scopes["jev"].real_calls else "off"
    jev = _service("jev", scopes["jev"], owner, (jev_status, None, None, None), month_start)
    return AiSpendingReport(as_of=now, perplexity=perplexity, jev=jev, codex=_codex(database, now))


# --- One period (the paper scorecard, T42) ------------------------------------------------------------------

PERIOD_SERVICES = ("perplexity", "jev", "other")


@dataclass(frozen=True)
class PeriodSpending:
    """Every paid AI call made in ``(start, end]``, as known at ``end``, in USD from receipts (D175, D203).

    ``other`` holds paid calls on any price card that is neither Perplexity's nor Jev's, so nothing paid is left
    out. Test calls are counted apart and never added. Codex subscription turns are counted, not priced: the
    allowance has no per-call price and is shared with the owner's own use. Nothing here reads practice money.
    """

    start: datetime
    end: datetime
    services: dict[str, Tally]
    departments: dict[str, dict[str, Tally]]
    synthetic_calls: int
    codex: CodexUse

    @property
    def total(self) -> Tally:
        return sum((self.services[name] for name in PERIOD_SERVICES), Tally())

    def document(self) -> dict[str, Any]:
        return {"start": utc_iso(self.start), "end": utc_iso(self.end), "currency": "USD",
                "paper_money_used": False, "total": self.total.document(),
                "services": {name: {"total": self.services[name].document(),
                                    "departments": {line: tally.document()
                                                    for line, tally in self.departments[name].items()}}
                             for name in PERIOD_SERVICES},
                "synthetic_calls": self.synthetic_calls, "codex": self.codex.document()}


def period_spending(database, start: datetime, end: datetime) -> PeriodSpending:
    """The AI spending of one period: calls reserved after ``start`` and up to ``end``, receipts up to ``end``."""
    if end <= start:
        raise ValueError("a spending period ends after it starts")
    start_iso, end_iso = utc_iso(start), utc_iso(end)
    perplexity_cards, usd_cards = _card_index(database)
    lines: dict[str, dict[str, _Acc]] = {name: {} for name in PERIOD_SERVICES}
    synthetic = 0
    rates: dict[Any, Decimal | None] = {}
    for row in _settled(database.execute(_PERIOD_CALLS, (end_iso, start_iso, end_iso))):
        service, line, is_synthetic, _, kind, amount, _ = _call(row, usd_cards, rates, perplexity_cards)
        if is_synthetic:
            synthetic += 1
            continue
        lines[service].setdefault(line, _Acc()).add(kind, amount)
    order = (*DEPARTMENTS, "other", JEV_ROLE)
    departments = {name: {line: lines[name][line].tally() for line in order if line in lines[name]}
                   for name in PERIOD_SERVICES}
    services = {name: sum(departments[name].values(), Tally()) for name in PERIOD_SERVICES}
    return PeriodSpending(start=start, end=end, services=services, departments=departments,
                          synthetic_calls=synthetic, codex=_codex_between(database, start, end))


def _calls(database, now_iso: str):
    """Each call once: ``(service, line, synthetic, created_at, kind, usd_amount, settled_at)``."""
    perplexity_cards, usd_cards = _card_index(database)
    sql = _CALLS.format(cards=",".join("?" for _ in perplexity_cards) or "NULL")
    rows = database.execute(sql, (now_iso, now_iso, JEV_ROLE, JEV_PURPOSE, *perplexity_cards))
    rates: dict[Any, Decimal | None] = {}  # the few distinct FX values, parsed once
    for row in _settled(rows):
        yield _call(row, usd_cards, rates)


def _card_index(database) -> tuple[set, set]:
    cards = {row["price_card_id"]: (row["provider"], row["currency"]) for row in database.execute(_CARDS).fetchall()}
    return ({card for card, (provider, _) in cards.items() if provider == "perplexity"},
            {card for card, (_, currency) in cards.items() if currency == "USD"})


def _settled(rows):
    """One row per reservation (rows come ordered by reservation): the most settled receipt wins."""
    pending = None
    for row in rows:
        row = tuple(row)
        if pending is not None and pending[0] == row[0]:
            if _RECEIPT_RANK.get(row[10], 9) < _RECEIPT_RANK.get(pending[10], 9):
                pending = row
            continue
        if pending is not None:
            yield pending
        pending = row
    if pending is not None:
        yield pending


def _call(row: tuple, usd_cards: set, rates: dict, perplexity_cards: set | None = None) -> tuple:
    """``(service, line, synthetic, created_at, kind, usd_amount, settled_at)`` for one call.

    Without ``perplexity_cards`` every call that is not a Jev check is Perplexity's (the HQ query selects only
    those); with it, a call on any other card is ``other`` (the scorecard's period counts every paid call).
    """
    (_, role, purpose, card, _, held, held_currency, fx_text, synthetic, created_at, status, cost, cost_currency,
     receipt_at) = row
    jev = role == JEV_ROLE and purpose == JEV_PURPOSE
    service = "jev" if jev else "perplexity" if perplexity_cards is None or card in perplexity_cards else "other"
    line = JEV_ROLE if jev else role if role in DEPARTMENTS else "other"
    if fx_text not in rates:
        fx = _decimal(fx_text)
        rates[fx_text] = fx if fx is not None and fx > 0 else None
    fx, usd_card = rates[fx_text], card in usd_cards
    kind, amount, settled_at = "unresolved", None, None
    if status is not None:
        value = _usd(cost, cost_currency, fx, usd_card)
        if value is not None and status in KNOWN_RECEIPTS:
            kind, amount, settled_at = "actual", value, receipt_at
        elif value is not None and status == "conservative_charge":
            kind, amount, settled_at = "estimated", value, receipt_at
    if kind == "unresolved":
        amount = _usd(held, held_currency, fx, usd_card)
    return service, line, bool(synthetic), created_at, kind, amount, settled_at


def _usd(amount, currency, fx: Decimal | None, usd_card: bool) -> Decimal | None:
    value = _decimal(amount)
    if value is None or value < 0:
        return None
    if currency == "USD":
        return value
    if currency == "EUR" and usd_card and fx is not None:
        return value / fx  # an EUR hold is the USD maximum x FX x buffer
    return None


def _service(service: str, scope: _Scope, owner: dict, policy: tuple, month_start: datetime) -> ServiceSpend:
    status, testing_start, testing_end, monthly_limit = policy
    departments = {line: scope.lines[line].tally() for line in (*DEPARTMENTS, "other", JEV_ROLE)
                   if line in scope.lines}
    total = sum(departments.values(), Tally())
    alert_usd, alert_source = owner.get((service, "testing_alert"), (DEFAULT_ALERT_USD[service], None))
    alert_source = "default" if alert_source is None else "owner"
    alert_state = alert_since = None
    if status != "monthly_limit":
        alert_state, alert_since = _alert(scope.known, total, alert_usd)
    credit = None
    if scope.credit is not None:
        amount, observed_at = owner[(service, "prepaid_credit")]
        credit = Credit(amount, observed_at, scope.credit.tally())
    month = scope.month.tally() if scope.month is not None else None
    return ServiceSpend(
        service=service, status=status, testing_start=testing_start, testing_end=testing_end,
        departments=departments, testing=total, synthetic_calls=scope.synthetic_calls,
        synthetic_known=scope.synthetic_known, alert_usd=alert_usd, alert_source=alert_source,
        alert_state=alert_state, alert_since=alert_since, credit=credit, monthly_limit_usd=monthly_limit,
        month_start=month_start if month is not None else None, month=month)


def _alert(known: list[tuple[str, Decimal]], total: Tally, threshold: Decimal) -> tuple[str, datetime | None]:
    """Passed on known spending; "may have passed" when unresolved calls at their maximum would pass it."""
    if total.known >= threshold:
        running = ZERO
        for settled_at, amount in sorted(known, key=lambda item: item[0]):
            running += amount
            if running >= threshold:
                return "passed", _parse(settled_at)
        return "passed", None
    if total.unresolved_calls and (total.unresolved is None or total.known + total.unresolved >= threshold):
        return "maybe_passed", None
    return "below", None


def _perplexity_policy(database, now_iso: str) -> tuple[str, datetime | None, datetime | None, Decimal | None]:
    """The testing period and the current mode, from the owner policy each Perplexity round recorded."""
    if not _has_table(database, "web_research_runs"):
        return "off", None, None, None
    mode = "json_extract(policy_json, '$.mode')"
    latest = database.execute(
        f"""SELECT {mode} AS mode, json_extract(policy_json, '$.monthly_limit_usd') AS limit_usd
        FROM web_research_runs WHERE started_at <= ? ORDER BY started_at DESC, rowid DESC LIMIT 1""",
        (now_iso,)).fetchone()
    start = database.execute(
        f"SELECT MIN(started_at) FROM web_research_runs WHERE {mode} = 'unlimited_testing' AND started_at <= ?",
        (now_iso,)).fetchone()[0]
    end = database.execute(
        f"""SELECT MIN(started_at) FROM web_research_runs WHERE {mode} = 'monthly_limit' AND started_at <= ?
        AND started_at > ?""", (now_iso, start or "")).fetchone()[0]
    status, limit = "off", None
    if latest is not None and latest["mode"] == "unlimited_testing":
        status = "testing"
    elif latest is not None and latest["mode"] == "monthly_limit":
        limit = _decimal(latest["limit_usd"])
        status = "monthly_limit" if limit is not None and limit > 0 else "off"
    return status, _parse(start), _parse(end), limit


def _owner_records(database, now_iso: str) -> dict[tuple[str, str], tuple[Decimal, Any]]:
    """The newest owner record of each kind: ``(amount, observed_at)`` for credit, ``(amount, "owner")`` else."""
    if not _has_table(database, OWNER_RECORDS):
        return {}
    records: dict[tuple[str, str], tuple[Decimal, Any]] = {}
    for row in database.execute(
            f"""SELECT service, kind, amount_usd, observed_at FROM {OWNER_RECORDS} WHERE created_at <= ?
            ORDER BY sequence""", (now_iso,)).fetchall():
        amount, observed = _decimal(row["amount_usd"]), _parse(row["observed_at"])
        if amount is None or amount < 0 or (row["kind"] == "testing_alert" and amount == 0):
            continue
        if row["kind"] == "prepaid_credit":
            if observed is not None:
                records[(row["service"], row["kind"])] = (amount, observed)
        else:
            records[(row["service"], row["kind"])] = (amount, "owner")
    return records


def _codex(database, now: datetime) -> CodexUse:
    return _codex_between(database, now - CODEX_WINDOW, now)


def _codex_between(database, start: datetime, end: datetime) -> CodexUse:
    counts: dict[str, list[int]] = {}
    for row in database.execute(_CODEX, (utc_iso(start), utc_iso(end))).fetchall():
        if row["state"] == "BLOCKED" or row["cost_status"] == "not_incurred":
            continue  # never sent: no allowance used
        role = row["role"] if row["role"] in DEPARTMENTS else "other"
        used = _reported_use(row["usage_json"])
        item = counts.setdefault(role, [0, 0, 0])
        item[0] += 1
        if used is None:
            item[1] += 1
        else:
            item[2] += used
    departments = {role: CodexDepartment(*counts[role]) for role in (*DEPARTMENTS, "other") if role in counts}
    return CodexUse(start=start, end=end, departments=departments)


def _reported_use(raw) -> int | None:
    try:
        usage = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    if not isinstance(usage, dict):
        return None
    unreported = usage.get("unreported_fields") or []
    if not isinstance(unreported, list) or any(name in unreported for name in _USAGE_FIELDS):
        return None
    values = [usage.get(name, 0) for name in _USAGE_FIELDS]
    if "uncached_input_tokens" not in usage or "billed_output_tokens" not in usage:
        return None
    if any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


# --- Owner records ----------------------------------------------------------------------------------------


def record_owner_amount(database, *, service: str, kind: str, amount: Decimal, observed_at: datetime,
                        now: datetime, record_id: str) -> None:
    """Append one owner record (prepaid credit reading or testing alert amount). Information, never a cap."""
    if service not in SERVICES or kind not in ("prepaid_credit", "testing_alert"):
        raise ValueError("unknown AI spending record")
    if not amount.is_finite() or amount < 0 or (kind == "testing_alert" and amount == 0):
        raise ValueError("the amount must be a positive USD amount (a credit may be zero)")
    if observed_at > now:
        raise ValueError("a credit reading cannot be in the future")
    with database.immediate() as connection:
        connection.execute(
            f"""INSERT INTO {OWNER_RECORDS} (record_id, service, kind, amount_usd, observed_at, set_by, created_at)
            VALUES (?, ?, ?, ?, ?, 'owner', ?)""",
            (record_id, service, kind, canonical_decimal(amount), utc_iso(observed_at), utc_iso(now)))


# --- Helpers ----------------------------------------------------------------------------------------------


def _has_table(database, name: str) -> bool:
    return database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() \
        is not None


def _decimal(value) -> Decimal | None:
    if value is None or isinstance(value, (bool, float)):
        return None
    try:
        number = Decimal(value if isinstance(value, str) else str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _parse(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else utc_iso(value)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else canonical_decimal(value)


# --- Plain text (the owner's report command) --------------------------------------------------------------

_NAMES = {"perplexity": "Perplexity web research", "jev": "Jev news alarm"}
_STATUS = {"off": "not started", "testing": "testing", "monthly_limit": "monthly limit"}


def _usd_text(value: Decimal | None) -> str:
    return "not known" if value is None else f"USD {canonical_decimal(value)}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _tally_text(tally: Tally) -> str:
    parts = [_plural(tally.calls, "call")]
    if tally.actual_calls:
        parts.append(f"from receipts {_usd_text(tally.actual)}")
    if tally.estimated_calls:
        charged = _plural(tally.estimated_calls, "call")
        parts.append(f"estimated {_usd_text(tally.estimated)} ({charged} charged at the most)")
    if tally.unresolved_calls:
        held = _plural(tally.unresolved_calls, "call")
        parts.append(f"not known yet: up to {_usd_text(tally.unresolved)} ({held}, not free)")
    return ", ".join(parts)


def format_report_text(report: AiSpendingReport) -> str:
    lines = [f"AI spending as of {utc_iso(report.as_of)}, in USD from receipts. Practice money is never counted.",
             "Alerts and the prepaid credit are information only: nothing here pauses or limits a call."]
    for item in report.services():
        period = ""
        if item.testing_start is not None:
            period = f" since {utc_iso(item.testing_start)}"
            if item.testing_end is not None:
                period += f" until {utc_iso(item.testing_end)}"
        lines += ["", f"{_NAMES[item.service]}: {_STATUS[item.status]}{period}",
                  f"  Testing total: {_tally_text(item.testing)}"]
        for name, tally in item.departments.items():
            lines.append(f"  {name}: {_tally_text(tally)}")
        if item.alert_state is not None:
            source = "your amount" if item.alert_source == "owner" else "default"
            lines.append(f"  Testing alert at {_usd_text(item.alert_usd)} ({source}): "
                         f"{item.alert_state.replace('_', ' ')}")
        if item.month is not None:
            lines.append(f"  This month: {_tally_text(item.month)}; monthly limit {_usd_text(item.monthly_limit_usd)}"
                         " (reaching it pauses Research and Leader news rounds only)")
        if item.credit is None:
            lines.append("  Prepaid credit: not recorded")
        else:
            credit = item.credit
            lines.append(f"  Prepaid credit: {_usd_text(credit.amount)} checked {utc_iso(credit.observed_at)};"
                         f" since then {_tally_text(credit.since)}; left about {_usd_text(credit.left)}")
        if item.synthetic_calls:
            lines.append(f"  Test calls with made-up answers (not counted): {item.synthetic_calls}")
    codex = report.codex
    turns = _plural(codex.turns, "turn")
    lines += ["", f"ChatGPT subscription (Codex), {utc_iso(codex.start)} to {utc_iso(codex.end)}: {turns},"
              f" {codex.unreported_turns} without reported use (shared with your own use)"]
    for role, item in codex.departments.items():
        share = codex.share(role)
        shown = "use not reported" if share is None else f"{share.quantize(Decimal('0.1'))}% of the reported use"
        lines.append(f"  {role}: {_plural(item.turns, 'turn')}, {shown}")
    return "\n".join(lines)
