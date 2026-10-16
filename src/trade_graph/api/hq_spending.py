"""HQ's AI spending block (T40): spending per department from receipts, the testing total beside the prepaid
credit, the owner's testing alerts and Codex use per department, as ``trade-graph.hq.v1`` ``ai_spending``.

The figures come from ``application.ai_spending`` (receipts and reservations only; practice money is never read).
They are cached for 60 seconds behind a fingerprint of the spending tables, like ``hq_money``, so a poll is cheap
and a new receipt, state change or owner record shows on the next one. Reading HQ never calls a provider and
writes nothing. A testing alert is a "keep an eye on" card in Needs you: it pauses no department (D131, D176).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from time import monotonic

from trade_graph.adapters.persistence.ai_spending_schema import TABLE as OWNER_RECORDS
from trade_graph.api import hq_copy as c
from trade_graph.api import hq_state
from trade_graph.api.hq_contract import iso_ms
from trade_graph.application.ai_spending import AiSpendingReport, CodexUse, ServiceSpend, Tally, spending_report
from trade_graph.domain.errors import TradeGraphError

MAX_AGE_SECONDS = 60
_monotonic = monotonic  # injectable timer (tests move it forward)
FAILURES = (sqlite3.DatabaseError, ValueError, TypeError, ArithmeticError, LookupError, TradeGraphError)
S = c.SPENDING
TENTH = Decimal("0.1")
HALF_CENT = Decimal("0.005")  # below this a USD amount would display as $0.00


def report(runtime, now: datetime) -> AiSpendingReport | None:
    """The cached spending report; ``None`` when the records can't be read (HQ then leaves the block out)."""
    lock = getattr(runtime, "_hq_spending_lock", None)
    if lock is None:
        lock = runtime._hq_spending_lock = threading.Lock()
    with lock:
        try:
            current = fingerprint(runtime.database)
        except FAILURES:
            return None
        cached = getattr(runtime, "_hq_spending", None)
        if cached and _monotonic() - cached[0] < MAX_AGE_SECONDS and cached[1] == current:
            return cached[2]
        try:
            result = spending_report(runtime.database, now)
        except FAILURES:
            result = None
        runtime._hq_spending = (_monotonic(), current, result)
        return result


def fingerprint(database) -> tuple:
    """Changes whenever a reservation, receipt, Codex turn, Perplexity round or owner record is added or settles."""
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    marks = [
        tuple(database.execute("SELECT MAX(rowid), MAX(updated_at) FROM budget_reservations").fetchone()),
        database.execute("SELECT MAX(rowid) FROM usage_receipts").fetchone()[0],
        tuple(database.execute("SELECT MAX(rowid), MAX(updated_at) FROM subscription_invocations").fetchone()),
    ]
    for table in ("web_research_runs", OWNER_RECORDS):
        marks.append(database.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] if table in tables else None)
    return tuple(marks)


# --- The block ---------------------------------------------------------------------------------------------


def block(spending: AiSpendingReport, *, paper: bool) -> dict:
    return {"as_of": iso_ms(spending.as_of),
            "services": [_service(item) for item in spending.services()],
            "codex": _codex(spending.codex),
            "paper_note": c.says(S["paper_note" if paper else "paper_note_live"]),
            "link": {"href": "/costs", "label": c.LINK_LABEL["/costs"]}}


def usd(value: Decimal | None) -> dict | None:
    return c.money(value, "USD")


def _usd(value: Decimal) -> dict:
    """A USD amount in words. A cost under half a cent says "under $0.01", so it never reads as $0.00."""
    if Decimal("0") < value < HALF_CENT:
        return c.text(S["under_a_cent"])
    return c.money_segment(value, "USD")


def _up_to(value: Decimal) -> list:
    return [_usd(value)] if Decimal("0") < value < HALF_CENT else [S["up_to"], _usd(value)]


def amounts(tally: Tally) -> dict:
    """A part is null when no call is of that kind, so "nothing yet" never reads as a zero cost."""
    return {"calls": tally.calls,
            "actual": usd(tally.actual) if tally.actual_calls else None,
            "estimated": usd(tally.estimated) if tally.estimated_calls else None,
            "unresolved": usd(tally.unresolved) if tally.unresolved_calls else None,
            "unresolved_calls": tally.unresolved_calls,
            "total": usd(tally.total) if tally.calls else None}


def _known(tally: Tally) -> list:
    """The known part as words: an amount, or "nothing" when no call has a receipt yet."""
    return [_usd(tally.known)] if tally.known_calls else [S["nothing"]]


def _line_name(line: str) -> str:
    return c.dept_name(line) if line in c.ROOM else S["line"].get(line, S["line"]["other"])


def _service(item: ServiceSpend) -> dict:
    return {
        "service": item.service, "name": S["service"][item.service], "status": item.status,
        "period_start": _iso(item.testing_start), "period_end": _iso(item.testing_end),
        "lines": [{"id": line, "name": _line_name(line), "amounts": amounts(tally)}
                  for line, tally in item.departments.items()],
        "total": amounts(item.testing), "synthetic_calls": item.synthetic_calls,
        "headline": _headline(item), "alert": _alert(item), "credit": _credit(item),
        "monthly_limit": _monthly(item),
    }


def _open_part(tally: Tally) -> list:
    """", plus up to $X not known yet" (or that the cost can't be worked out); nothing when no call is open."""
    if not tally.unresolved_calls:
        return []
    if tally.unresolved is None:
        return [S["plus_unknown"]]
    return [S["plus"], *_up_to(tally.unresolved), S["not_known_yet"]]


def _headline(item: ServiceSpend) -> list:
    total = item.testing
    if item.status == "off" and total.calls == 0:
        return c.says(S["off"][item.service])
    if item.status == "monthly_limit" and item.testing_end is not None:
        before, after = S["total_ended"]
        head = [before, c.time(item.testing_end, "date"), after]
    else:
        head = [S["total"]]
    tail = S["no_limit"] if item.service == "perplexity" and item.status == "testing" else ""
    return c.says(*head, *_known(total), S["from_receipts"], *_open_part(total), ".", tail)


def _alert(item: ServiceSpend) -> dict | None:
    if item.alert_state is None:
        return None
    known, threshold = item.testing.known, item.alert_usd
    if item.alert_state == "passed":
        before, middle, after = S["alert_passed"]
        words = c.says(before, _usd(threshold), middle, _usd(known), after)
    elif item.alert_state == "maybe_passed" and item.testing.unresolved is not None:
        first, second, third, fourth = S["alert_maybe"]
        words = c.says(first, _usd(threshold), second, *_known(item.testing), third, S["plus"],
                       *_up_to(item.testing.unresolved), fourth)
    elif item.alert_state == "maybe_passed":
        first, second, third = S["alert_maybe_unknown"]
        words = c.says(first, _usd(threshold), second, *_known(item.testing), third)
    else:
        before, after = S["alert_below"]
        words = c.says(before, _usd(threshold), after)
    return {"threshold": usd(threshold), "source": item.alert_source, "state": item.alert_state,
            "since": _iso(item.alert_since), "text": words}


def _credit(item: ServiceSpend) -> dict:
    provider = S["provider_page"][item.service]
    credit = item.credit
    if credit is None:
        return {"recorded": None, "observed_at": None, "since": None, "left": None,
                "text": c.says(S["credit_none"].format(provider=provider))}
    head, checked, since = S["credit_head"]
    words = [head, _usd(credit.amount), checked, c.time(credit.observed_at, "date"), since, *_known(credit.since)]
    left = credit.left
    if left is None:
        words.append(S["credit_unknown"])
    else:
        if credit.since.unresolved_calls:
            words += [S["plus"], *_up_to(credit.since.unresolved), S["not_known_yet"]]
        if left < 0:
            words.append(S["credit_used"])
        else:
            before, after = S["credit_left"]
            words += [before, _usd(left), after]
    words.append(S["credit_info"].format(provider=provider))
    return {"recorded": usd(credit.amount), "observed_at": iso_ms(credit.observed_at),
            "since": amounts(credit.since), "left": usd(left), "text": c.says(*words)}


def _monthly(item: ServiceSpend) -> dict | None:
    if item.monthly_limit_usd is None or item.month is None or item.month_start is None:
        return None
    first, second, third = S["monthly"]
    month = item.month
    shown = [_usd(month.total)] if month.total is not None and month.calls else _known(month)
    return {"limit": usd(item.monthly_limit_usd), "month_start": iso_ms(item.month_start), "month": amounts(month),
            "text": c.says(first, *shown, second, _usd(item.monthly_limit_usd), third)}


def _codex(codex: CodexUse) -> dict:
    lines = []
    for role, item in codex.departments.items():
        share = codex.share(role)
        lines.append({"id": role, "name": _line_name(role), "turns": item.turns,
                      "unreported_turns": item.unreported_turns,
                      "share_percent": None if share is None else float(share.quantize(TENTH))})
    head = c.plural(S["codex"], codex.turns) if codex.turns else S["codex_none"]
    return {"period_start": iso_ms(codex.start), "period_end": iso_ms(codex.end), "turns": codex.turns,
            "unreported_turns": codex.unreported_turns, "lines": lines, "text": c.says(head)}


# --- Needs you ----------------------------------------------------------------------------------------------


def attention(spending: AiSpendingReport | None) -> list[dict]:
    """One "keep an eye on" card per service whose testing spending passed (or may have passed) its alert."""
    cards = []
    for item in spending.services() if spending is not None else ():
        if item.alert_state not in ("passed", "maybe_passed"):
            continue
        name = S["service_short"][item.service]
        key = "attention_passed" if item.alert_state == "passed" else "attention_maybe"
        before, after = S[key]
        title = c.says(before.format(service=name), _usd(item.alert_usd), after)
        total = item.testing
        if not total.unresolved_calls:
            why = c.says(*_known(total), S["attention_why"])
        elif total.unresolved is None:
            why = c.says(*_known(total), S["attention_why_unknown"])
        else:
            first, second = S["attention_why_open"]
            why = c.says(*_known(total), first, S["plus"], *_up_to(total.unresolved), second)
        cards.append(hq_state._card("spending_alert", title, why=why, consequence=c.says(S["attention_consequence"]),
                                    how=c.says(S["attention_how"][item.service]), link="/costs",
                                    since=item.alert_since, suffix=item.service))
    return cards


def _iso(value: datetime | None) -> str | None:
    return None if value is None else iso_ms(value)
