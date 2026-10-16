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
import sqlite3
from dataclasses import dataclass, field, replace
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

_MATCH = """((b.role = ? AND b.purpose = ?) OR json_extract(p.document_json, '$.provider') = 'perplexity')"""
_RESERVATIONS = f"""SELECT b.reservation_id, b.role, b.purpose, b.state, b.amount, b.currency, b.fx_rate_value,
        b.synthetic, b.created_at, json_extract(p.document_json, '$.provider') AS provider,
        json_extract(p.document_json, '$.currency') AS card_currency
    FROM budget_reservations b LEFT JOIN price_cards p ON p.price_card_id = b.price_card_id
    WHERE {_MATCH} AND b.created_at <= ? ORDER BY b.created_at, b.rowid"""
_RECEIPTS = f"""SELECT r.reservation_id, r.status, r.native_cost, r.native_currency, r.created_at
    FROM usage_receipts r JOIN budget_reservations b ON b.reservation_id = r.reservation_id
    LEFT JOIN price_cards p ON p.price_card_id = b.price_card_id
    WHERE {_MATCH} AND r.created_at <= ?"""
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

    def plus(self, kind: str, amount: Decimal | None) -> Tally:
        if kind == "actual":
            return replace(self, calls=self.calls + 1, actual=self.actual + amount, actual_calls=self.actual_calls + 1)
        if kind == "estimated":
            return replace(self, calls=self.calls + 1, estimated=self.estimated + amount,
                           estimated_calls=self.estimated_calls + 1)
        held = None if self.unresolved is None or amount is None else self.unresolved + amount
        return replace(self, calls=self.calls + 1, unresolved=held, unresolved_calls=self.unresolved_calls + 1)

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


@dataclass(frozen=True)
class _Call:
    service: str
    line: str
    synthetic: bool
    created_at: str
    kind: str
    amount: Decimal | None
    settled_at: str | None


def spending_report(database, now: datetime) -> AiSpendingReport:
    """One report as of ``now``; records stamped later are not counted yet."""
    now_iso = utc_iso(now)
    calls = _calls(database, now_iso)
    owner = _owner_records(database, now_iso)
    perplexity_policy = _perplexity_policy(database, now_iso)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    perplexity = _service("perplexity", calls, owner, perplexity_policy, month_start)
    jev_status = "testing" if any(call.service == "jev" and not call.synthetic for call in calls) else "off"
    jev = _service("jev", calls, owner, (jev_status, None, None, None), month_start)
    return AiSpendingReport(as_of=now, perplexity=perplexity, jev=jev, codex=_codex(database, now))


def _calls(database, now_iso: str) -> list[_Call]:
    params = (JEV_ROLE, JEV_PURPOSE, now_iso)
    receipts: dict[str, sqlite3.Row] = {}
    for row in database.execute(_RECEIPTS, params).fetchall():
        current = receipts.get(row["reservation_id"])
        if current is None or _RECEIPT_RANK.get(row["status"], 9) < _RECEIPT_RANK.get(current["status"], 9):
            receipts[row["reservation_id"]] = row
    calls = []
    for row in database.execute(_RESERVATIONS, params).fetchall():
        jev = row["role"] == JEV_ROLE and row["purpose"] == JEV_PURPOSE
        service = "jev" if jev else "perplexity"
        line = JEV_ROLE if jev else row["role"] if row["role"] in DEPARTMENTS else "other"
        kind, amount, settled_at = _settle(row, receipts.get(row["reservation_id"]))
        calls.append(_Call(service, line, bool(row["synthetic"]), row["created_at"], kind, amount, settled_at))
    return calls


def _settle(row, receipt) -> tuple[str, Decimal | None, str | None]:
    """The kind of one call and its USD amount (see the module docstring)."""
    fx = _decimal(row["fx_rate_value"])
    fx = fx if fx is not None and fx > 0 else None
    usd_card = row["card_currency"] == "USD"
    if receipt is not None:
        amount = _usd(receipt["native_cost"], receipt["native_currency"], fx, usd_card)
        if receipt["status"] in KNOWN_RECEIPTS and amount is not None:
            return "actual", amount, receipt["created_at"]
        if receipt["status"] == "conservative_charge" and amount is not None:
            return "estimated", amount, receipt["created_at"]
    return "unresolved", _usd(row["amount"], row["currency"], fx, usd_card), None


def _usd(amount, currency, fx: Decimal | None, usd_card: bool) -> Decimal | None:
    value = _decimal(amount)
    if value is None or value < 0:
        return None
    if currency == "USD":
        return value
    if currency == "EUR" and usd_card and fx is not None:
        return value / fx  # an EUR hold is the USD maximum x FX x buffer
    return None


def _service(service: str, calls: list[_Call], owner: dict, policy: tuple, month_start: datetime) -> ServiceSpend:
    status, testing_start, testing_end, monthly_limit = policy
    start_iso, end_iso = _iso(testing_start), _iso(testing_end)
    real = [call for call in calls if call.service == service and not call.synthetic]
    synthetic = [call for call in calls if call.service == service and call.synthetic]
    testing = [call for call in real if (start_iso is None or call.created_at >= start_iso)
               and (end_iso is None or call.created_at < end_iso)]
    lines: dict[str, Tally] = {}
    for call in testing:
        lines[call.line] = lines.get(call.line, Tally()).plus(call.kind, call.amount)
    departments = {line: lines[line] for line in (*DEPARTMENTS, "other", JEV_ROLE) if line in lines}
    total = sum(departments.values(), Tally())
    alert_usd, alert_source = owner.get((service, "testing_alert"), (DEFAULT_ALERT_USD[service], None))
    alert_source = "default" if alert_source is None else "owner"
    alert_state = alert_since = None
    if status != "monthly_limit":
        alert_state, alert_since = _alert(testing, total, alert_usd)
    credit = None
    if (service, "prepaid_credit") in owner:
        amount, observed_at = owner[(service, "prepaid_credit")]
        observed_iso = utc_iso(observed_at)
        credit = Credit(amount, observed_at, _tally(call for call in real if call.created_at >= observed_iso))
    month = None
    if monthly_limit is not None:
        prefix = utc_iso(month_start)[:7]
        month = _tally(call for call in real if call.created_at[:7] == prefix)
    known_synthetic = sum((call.amount for call in synthetic if call.kind != "unresolved" and call.amount), ZERO)
    return ServiceSpend(
        service=service, status=status, testing_start=testing_start, testing_end=testing_end,
        departments=departments, testing=total, synthetic_calls=len(synthetic), synthetic_known=known_synthetic,
        alert_usd=alert_usd, alert_source=alert_source, alert_state=alert_state, alert_since=alert_since,
        credit=credit, monthly_limit_usd=monthly_limit, month_start=month_start if month else None, month=month)


def _tally(calls) -> Tally:
    result = Tally()
    for call in calls:
        result = result.plus(call.kind, call.amount)
    return result


def _alert(testing: list[_Call], total: Tally, threshold: Decimal) -> tuple[str, datetime | None]:
    """Passed on known spending; "may have passed" when unresolved calls at their maximum would pass it."""
    if total.known >= threshold:
        running = ZERO
        for call in sorted((call for call in testing if call.kind != "unresolved"),
                           key=lambda call: (call.settled_at or call.created_at)):
            running += call.amount
            if running >= threshold:
                return "passed", _parse(call.settled_at or call.created_at)
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
    start = now - CODEX_WINDOW
    counts: dict[str, list[int]] = {}
    for row in database.execute(_CODEX, (utc_iso(start), utc_iso(now))).fetchall():
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
    return CodexUse(start=start, end=now, departments=departments)


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
        number = Decimal(str(value))
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
