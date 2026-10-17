"""Plain-English copy for Trade Graph HQ (spec §D) and the segment builders that carry it.

Every server-side string the HQ read model shows lives here. Rules (tested):

- every string survives ``redact()`` unchanged;
- owner-facing strings avoid internal jargon; raw technical names go only in a
  segment's ``code`` field, shown with "Show technical names";
- model text appears only through :func:`quote`, already redacted and cut short.

Times are segments rendered by the browser; :func:`plain` renders text for the
review prompt, where times read ``HH:MM UTC``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

from trade_graph.api.hq_contract import QUOTE_LABEL, QUOTE_MAX_CHARS, iso_ms, money_display
from trade_graph.api.security import redact
from trade_graph.domain.money import canonical_decimal

__all__ = ["iso_ms", "money_display"]

ELLIPSIS: Final = "…"

# --- Segment builders ------------------------------------------------------------------


def text(value: str, code: str | None = None) -> dict:
    segment = {"t": "text", "v": value}
    if code is not None:
        segment["code"] = code
    return segment


def time(at: datetime | str, style: str) -> dict:
    return {"t": "time", "at": iso_ms(at), "style": style}


def duration(seconds: float) -> dict:
    return {"t": "duration", "seconds": max(0, int(seconds))}


def term(key: str, value: str, code: str | None = None) -> dict:
    segment = {"t": "term", "key": key, "v": value}
    if code is not None:
        segment["code"] = code
    return segment


def link(href: str, value: str) -> dict:
    return {"t": "link", "href": href, "v": value}


def percent(value: float) -> dict:
    return {"t": "percent", "value": value, "display": pct(value) + "%"}


def money(amount: Decimal | str | None, currency: str) -> dict | None:
    """A ``Money`` object; ``None`` stays unknown, never zero."""
    if amount is None:
        return None
    value = Decimal(amount)
    canonical = canonical_decimal(value)
    return {"amount": canonical, "currency": currency, "display": money_display(canonical, currency)}


def money_segment(amount: Decimal | str, currency: str) -> dict:
    return {"t": "money", **money(amount, currency)}


def says(*parts: str | dict | None) -> list[dict]:
    """Build ``Text`` from strings and segments, merging adjacent plain strings."""
    result: list[dict] = []
    for part in parts:
        if isinstance(part, dict) and part.get("t") == "text" and "code" not in part:
            part = part["v"]
        if part is None or part == "":
            continue
        if isinstance(part, str):
            if result and result[-1]["t"] == "text" and "code" not in result[-1]:
                result[-1] = text(result[-1]["v"] + part)
            else:
                result.append(text(part))
        else:
            result.append(part)
    return result


def join(*texts: list[dict] | None, sep: str = " ") -> list[dict]:
    """Concatenate ``Text`` values with a separator, skipping empty ones."""
    result: list[dict] = []
    for value in texts:
        if not value:
            continue
        if result:
            result = says(*result, sep)
        result = says(*result, *value)
    return result


# --- Scalars ---------------------------------------------------------------------------


def pct(value: float | int | Decimal) -> str:
    number = Decimal(str(value)).quantize(Decimal("0.1"))
    return f"{number:f}".removesuffix(".0")


def quantity(value: Decimal | str) -> str:
    """Coin quantity with at most 8 decimal places and no trailing zeros."""
    number = Decimal(value).quantize(Decimal("0.00000001"))
    return canonical_decimal(number)


def span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 172800:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def ago(seconds: float) -> str:
    return span(seconds) + " ago"


def clock_utc(at: datetime | str) -> str:
    return _instant(at).strftime("%H:%M") + " UTC"


def day_clock_utc(at: datetime | str) -> str:
    return _instant(at).strftime("%a %H:%M") + " UTC"


def day_text(at: datetime | str) -> str:
    moment = _instant(at)
    return f"{moment.day} {moment.strftime('%b')}"


def _instant(at: datetime | str) -> datetime:
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    return at.astimezone(UTC)


def cut(value: str, limit: int) -> str:
    """Cut at a word boundary to at most ``limit`` characters, ending with an ellipsis."""
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    room = value[: limit - 1]
    boundary = room.rfind(" ")
    if boundary >= limit // 2:
        room = room[:boundary]
    return room.rstrip(" ,;:.-") + ELLIPSIS


def quote(value, limit: int = QUOTE_MAX_CHARS) -> dict | None:
    """Model text as a short labelled quote: redacted first, then cut, never paraphrased."""
    if not isinstance(value, str):
        return None
    cleaned = cut(redact(value), limit)
    return {"label": QUOTE_LABEL, "text": cleaned} if cleaned else None


def technical(value, limit: int = 160) -> str | None:
    """Raw technical text for the ``code`` and ``technical`` fields: redacted and bounded."""
    if value is None:
        return None
    cleaned = cut(redact(str(value)), limit)
    return cleaned or None


def plain(value: list[dict] | None) -> str:
    """Render ``Text`` as plain text for the review prompt."""
    parts = []
    for segment in value or []:
        kind = segment["t"]
        if kind in ("text", "term", "link"):
            parts.append(segment["v"])
        elif kind == "time":
            parts.append(clock_utc(segment["at"]) if segment["style"] != "date" else day_text(segment["at"]))
        elif kind == "duration":
            parts.append(span(segment["seconds"]))
        elif kind in ("money", "percent"):
            parts.append(segment["display"])
    return "".join(parts)


def decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


# --- D.1 Global ------------------------------------------------------------------------

BRAND: Final = "Trade Graph HQ"
PURPOSE: Final = "This is a practice run. The goal right now is to check the team works safely, not to make money."
READ_ONLY: Final = "View only. Decisions need the owner's sign-in on this device."
LIVE_MODE: Final = "This page doesn't control a live account. Use Owner controls."
AT_RISK_PAPER: Final = "Is my money at risk? No. This is practice money; real costs are only the bills you enter."
AT_RISK_LIVE: Final = ("Is my money at risk? This is a live account: real money can gain or lose value. "
                       "Use Owner controls to change anything.")
FOOTER_PAPER: Final = "This is practice: no real trades happen. Real costs are only the bills you enter."
NOT_KNOWN_YET: Final = "Not known yet"

# --- D.3 Service, pause, achieved, AI --------------------------------------------------

SERVICE_STATUS: Final = {
    "STARTING": "Starting up",
    "RUNNING": "On and working normally",
    "MANAGEMENT_ONLY": "On. It started while AI wasn't ready and keeps checking.",
    "STOPPING": "Shutting down safely",
    "STOPPED": "Off",
    "FAILED": "Off: it stopped because of an error",
    "INTERRUPTED": "Off: it stopped unexpectedly",
    "ATTACHED_EXISTING": "In use, but no team run is recorded",
    "IDLE": "Never started",
    "UNOBSERVED": "Never started",
}
PAUSE_PROFILE: Final = {
    "RUNNING": "Normal: departments may work",
    "PAUSE_DECISIONS": "Decisions paused: departments can look but not decide",
    "NO_NEW_EXPOSURE": "No new buying: departments keep working; selling and protection continue",
    "MANAGE_ONLY": "Protect-only: no new AI work and no new buying; anything held stays protected",
    "CANCEL_ALL": "Cancel all orders",
    "FLATTEN": "Sell everything",
    "STOPPED": "Stopped",
}
PAUSE_SHORT: Final = {
    "RUNNING": "normal",
    "PAUSE_DECISIONS": "decisions paused",
    "NO_NEW_EXPOSURE": "no new buying",
    "MANAGE_ONLY": "protect-only",
    "CANCEL_ALL": "cancel all orders",
    "FLATTEN": "sell everything",
    "STOPPED": "stopped",
}
PAUSE_SETTING: Final = {
    "RUNNING": "Normal",
    "PAUSE_DECISIONS": "Decisions paused",
    "NO_NEW_EXPOSURE": "No new buying",
    "MANAGE_ONLY": "Protect-only",
    "CANCEL_ALL": "Cancel all orders",
    "FLATTEN": "Sell everything",
    "STOPPED": "Stopped",
}
PAUSE_CONSEQUENCE: Final = {
    "RUNNING": "Departments may work.",
    "PAUSE_DECISIONS": "Departments can look but not decide.",
    "NO_NEW_EXPOSURE": "Departments keep working; selling and protection continue.",
    "MANAGE_ONLY": "No new AI work and no new buying; anything held stays protected.",
    "CANCEL_ALL": "Open orders are cancelled; departments wait.",
    "FLATTEN": "Anything held is sold; departments wait.",
    "STOPPED": "Departments wait; nothing new is bought.",
}
ORIGINATOR: Final = {
    "owner": "by you",
    "system": "by the system (automatic safety stop)",
    "leader": "by the Leader",
}
ORIGINATOR_SHORT: Final = {"owner": "by you", "system": "by the system", "leader": "by the Leader"}
ACHIEVED: Final = {
    "requested": "being applied",
    "running": "normal",
    "decisions-paused": "decisions paused",
    "managing": "protecting",
    "reconciliation-required": "protecting",
    "cancelling-increases": "cancelling buy orders",
    "increases-cleared": "no buy orders left",
    "cancelling-orders": "cancelling orders",
    "orders-cleared": "no orders left",
    "flattening": "selling",
    "flat-verified": "confirmed: holding nothing",
    "blocked-until-flat": "waiting until nothing is held",
    "stopped": "stopped",
    "unresolved-native-holdings": "holding something it can't account for; needs a check",
}
AI_STATE: Final = {
    "paused_model": "The AI model isn't available. This doesn't clear by itself.",
    "paused_quota": "The allowance ran out. It turns back on by itself.",
    "waiting_for_allowance": "Waiting for allowance; retries every 30 s.",
    "probably_low": "Probably waiting: last reading below the line.",
    "ok_last_reading": "AI can work when there is allowance.",
    "unknown": "No allowance reading yet.",
}
AI_PAUSE_REASON: Final = {
    "subscription quota exhausted": "the allowance ran out",
    "subscription model unavailable": "the AI model isn't available",
}
AI_PAUSE_REASON_OTHER: Final = "a provider stop"
ALLOWANCE_UNREPORTED: Final = "A second, shorter limit isn't reported."
ALLOWANCE_NO_READING: Final = "No allowance reading yet."
ALLOWANCE_NONE_REPORTED: Final = "How much allowance is left isn't reported in the latest reading."
ALLOWANCE_NO_RULE: Final = "No reserve rule is set."
ALLOWANCE_REFILL_UNKNOWN: Final = "Refill time not reported"
ALLOWANCE_SHARED: Final = "Your own ChatGPT use counts against the same allowance."

# --- D.4 Task status --------------------------------------------------------------------

TASK_STATE: Final = {
    "queued_due": "Waiting its turn",
    "queued_paused": "On hold while paused (re-checks every 30 s)",
    "queued_allowance": "Waiting for AI allowance (re-checks every 30 s)",
    "queued_later": "Will try again ",
    "leased": "Getting ready",
    "running": "Working on it (try {used} of {max})",
    "lease_lost": "Lost track; will be picked up again when the team runs",
    "WAITING_EXTERNAL": "Stuck: its answer can't be confirmed. It won't retry by itself.",
    "BLOCKED_BUDGET": ("Stuck: this job reached its own cost limit (not your AI allowance). It won't retry by "
                       "itself."),
    "past_deadline": "Past its time limit; will be skipped",
    "SUCCEEDED": "Done",
    "FAILED": "Didn't work",
    "skip_deadline": "Skipped: its time limit passed before it started",
    "skip_manual": "Skipped: automatic optimisation is off; it only runs when you ask",
    "DEAD_LETTER": "Gave up after {max} tries",
    "CANCELLED": "Cancelled",
    "change_proposed": "Improvement idea waiting for the Leader (decide by ",
    "change_proposed_plain": "Improvement idea waiting for the Leader",
    "candidate_READY": "Improvement ready for the Leader's decision",
    "candidate_FAILED": "Improvement failed its checks",
}
# Persisted skip reasons (paper_service.py); a skip is not a problem.
SKIP_REASONS: Final = {
    "Bounded task deadline expired; no new inference": "skip_deadline",
    "Optimisation requires a persisted owner request": "skip_manual",
}
SKIP_PLAIN: Final = {
    "skip_deadline": "its time limit passed before it started",
    "skip_manual": "automatic optimisation is off; it only runs when you ask",
}

# --- D.5 Decisions, orders, origins, reasons --------------------------------------------

DECISION_ACTION: Final = {
    "no_action": "chose to wait",
    "hold": "kept things as they are",
    "enter": "decided to buy",
    "exit": "decided to sell",
    "resize": "changed a position's size",
    "adjust_order": "adjusted an order",
}
ORDER_STATE: Final = {
    "SUBMISSION_PENDING": "about to be placed",
    "SUBMITTING": "being placed",
    "OPEN": "waiting to be filled",
    "PARTIALLY_FILLED": "partly filled",
    "UNKNOWN": "status unclear; being checked",
    "CANCEL_PENDING": "being cancelled",
    "FILLED": "filled",
    "CANCELLED": "cancelled",
    "REJECTED": "refused",
}
CANDIDATE_STATE: Final = {
    "AUTHORIZED": "approved for building",
    "DEVELOPING": "being built",
    "VALIDATING": "being checked",
    "FAILED": "failed its checks",
    "READY": "ready for the Leader's decision",
    "ACTIVATING": "being switched on",
    "OBSERVING": "on trial",
    "ACTIVE": "switched on",
    "REJECTED": "turned down",
    "ROLLED_BACK": "undone after its trial",
    "SUPERSEDED": "replaced by a newer version",
    "RESTART_PENDING": "waiting to reload",
    "ROLLBACK_PENDING": "being undone",
    "RESTORE_PENDING": "being restored",
    "BLOCKED": "stuck mid-switch",
}
ORIGIN: Final = {
    "schedule": "Regular turn ({cadence})",
    "schedule_plain": "Regular turn",
    "secretary": "Extra review: the Secretary flagged a problem",
    "followup": "Follow-up: reading a department's answer",
    "owner": "You asked for this",
    "commission": "Commissioned by the Leader",
    "leader": "Assigned by the Leader: '{objective}'",
    "improvement": "Improvement work",
}
FOLLOWUP_OBJECTIVE: Final = "Incorporate completed departmental consultation"
OPTIMISATION_OBJECTIVE: Final = "owner-optimisation-cycle"
CADENCE: Final = {14400: "every 4 hours", 86400: "daily", 302400: "every 3½ days", 604800: "weekly"}
MODEL_FAILURE: Final = {
    "quota_reserve": "the AI allowance was below the line",
    "credentials": "the AI sign-in needs attention",
    "unsupported": "the AI model wasn't available",
    "rate_limit": "the AI allowance ran out",
    "temporary": "a temporary AI error",
    "timeout_uncertain": "the AI didn't answer in time, and whether it finished is unknown",
    "refusal": "the AI declined",
    "truncation": "the AI's answer was cut off",
    "validation": "the AI's answer didn't pass the checks",
}
REASON: Final = {
    "outside_evidence": "its answer cited information it wasn't given",
    "changed": "something changed while it was working (for example a pause), so its answer was set aside",
    "halt": "a pause was on, so its answer was set aside",
    "tries": "it used all its tries",
    "mandate": "its trading permission had expired",
    "prices": "prices were too old to place a practice order",
    "unresolved": "an earlier practice order was still being checked",
    "budget": "this job reached its own cost limit",
    # Why the software refused an order the Trader chose (nothing was sent or held).
    "minimum": "the order was smaller than the exchange's minimum",
    "coin_share": "it would have put more than the allowed share of the money into one coin",
    "total_share": "it would have put more than the allowed share of the money into coins",
    "balance": "there wasn't enough free money or coins for it",
    "valuation": "the account's value couldn't be confirmed at that moment",
    "not_permitted": "its trading permission doesn't cover that order",
    "checking": "the account's balances were still being double-checked",
    "unknown_coin": "the exchange's rules for that coin aren't known",
    "paused": "a pause was on that doesn't allow this order",
    # The owner's money limits and coin list (T32).
    "loss_limit": "the day's loss limit was reached, so new buying waits until the next day starts",
    "loss_limit_check": "the day's loss limit couldn't be checked at that moment",
    "coin_list": "that coin isn't on the allowed coin list right now",
    "coin_list_check": "the coin list couldn't be checked at that moment (no recent coin scan)",
    "riba": "that kind of product isn't allowed (no futures, borrowing, short selling or earn products)",
    "limits_missing": "your money limits aren't recorded yet, so plan orders stay off",
    # The Trader's answer arrived but could not be used; only that turn failed.
    "answer_checks": "its answer didn't pass the checks",
    "error": "a technical error",
    "unknown": "a problem HQ can't describe in plain words",
}
_CHANGED = re.compile(r"\b(?:authority|version|pause|budget) changed after snapshot")
_HALT = re.compile(r"\b(?:owner|system) halt prevents")
_ERROR_CLASS = re.compile(r"[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+")
_VALUATION = re.compile(r"\bprovisional valuation\b|\bnative \S+ valuation\b|\bFX for native exposure\b"
                        r"|\bno price for exposure valuation\b")
_PAUSE_REFUSAL = re.compile(r"\bpause \S+ blocks (?:new exposure|reduction)\b")
_ANSWER_CHECKS = re.compile(r"\bvalidation errors? for \w+Reply\b|^strategy template is unknown|^trader payload "
                            r"|^trader action is not executable|^evidence ids must be strings"
                            r"|^order (?:quantity|symbol) ")


def reason_plain(raw, *, live: bool = False) -> str | None:
    """Map a persisted failure reason to plain words (§D.5); the raw text goes to ``code``.

    ``live`` picks the live-account wording, which never says "practice" (C1).
    """
    if raw is None or raw == "":
        return None
    value = str(raw)
    model = re.match(r"(?:model|subscription) (\w+):", value)
    if model and model.group(1) in MODEL_FAILURE:
        return MODEL_FAILURE[model.group(1)]
    key = _reason_key(value)
    return LIVE_REASON.get(key, REASON[key]) if live else REASON[key]


def _reason_key(value: str) -> str:
    if "cites evidence outside" in value or "cites research outside" in value:
        return "outside_evidence"
    if _ANSWER_CHECKS.search(value):
        return "answer_checks"
    if "does not allow a discretionary experiment" in value:
        return "not_permitted"
    if _CHANGED.search(value):
        return "changed"
    if _HALT.search(value):
        return "halt"
    if "attempt limit or root step limit" in value:
        return "tries"
    if "mandate expired" in value or "expired mandate allows management only" in value:
        return "mandate"
    if ("stale or missing quote" in value or "market feed blocks new exposure" in value
            or "no positive reference price" in value):
        return "prices"
    if "unresolved execution blocks new exposure" in value:
        return "unresolved"
    if "daily loss limit reached" in value:
        return "loss_limit"
    if "daily loss limit can't be checked" in value:
        return "loss_limit_check"
    if "outside the owner's coin rule" in value:
        return "coin_list"
    if "coin rule can't be checked" in value:
        return "coin_list_check"
    if "(D127)" in value:
        return "riba"
    if value.startswith("day-plan orders stay off"):
        return "limits_missing"
    if _PAUSE_REFUSAL.search(value):
        return "paused"
    if "below venue minimum" in value or "rounding would exceed authorization" in value:
        return "minimum"
    if "single asset exposure" in value:
        return "coin_share"
    if "gross exposure" in value:
        return "total_share"
    if "insufficient available balance" in value or value == "no equity":
        return "balance"
    if _VALUATION.search(value):
        return "valuation"
    if "outside the active mandate" in value or "outside the owner policy" in value:
        return "not_permitted"
    if "incomplete account reconciliation" in value:
        return "checking"
    if value.startswith("unknown instrument"):
        return "unknown_coin"
    if value.startswith(("room ", "BudgetExhausted")):
        return "budget"
    if _ERROR_CLASS.fullmatch(value):
        return "error"
    return "unknown"


def reason_segment(raw, *, live: bool = False) -> dict | None:
    words = reason_plain(raw, live=live)
    return None if words is None else text(words, code=technical(raw))


def cadence(seconds: int | None) -> str | None:
    if not seconds:
        return None
    return CADENCE.get(int(seconds)) or "every " + span(int(seconds))


# --- D.6 Waiting-because gates -----------------------------------------------------------

GATE: Final = {
    "G1": "Waiting: the team isn't running.",
    "GM": "Waiting: the team started without AI being ready; turns run only if it becomes available.",
    "G2": "Waiting: the AI is paused ({reason}).",
    "G3": "Waiting for AI allowance; it retries every 30 seconds.",
    "G4": "Automatic turns are switched off in the installation settings.",
    "G5": "Waiting: paused {by}. It runs once after Resume.",
    "G6": "The timetable step is failing, so this turn can't be booked.",
    "G7": "Its previous turn is still in progress; this slot is skipped.",
    "G8": "Probably waiting for AI allowance: the last reading was {p}% (needs more than {line}%), ",
    "G9": "Due now; should start within seconds.",
    "G10_before": "Overdue by ",
    "G10_after": " for a reason HQ can't see. Mission Control may say more.",
}
CATCH_UP: Final = "If several turns were missed, the team does one catch-up turn, not all of them."
CONDITIONAL: Final = "Only if the team is running and not paused. Missed turns are merged into one."

# --- D.7 Needs-you cards ------------------------------------------------------------------

ATTENTION: Final = {
    "team_off": ("The team is off", "Nothing watches prices or protects positions while it's off.",
                 "No turns happen."),
    "team_silent": ("No sign of life from the team for ", "It normally signals every few seconds.",
                    "If it's stuck, nothing is watched."),
    "team_silent_how": ("Check the PC is awake and Docker is running, then ask your review chat. "
                        "Start would only reconnect to it."),
    "team_attached_how": "Mission Control may say more. Start would only reconnect to it.",
    "safety_stop": "Automatic safety stop since ",
    "safety_stop_how": ("Can't be lifted from the dashboard by design. It's in your next review prompt. "
                        "Owner controls show the technical details."),
    "model_unavailable": ("The AI model isn't available", "No department can think.",
                          "Prices and protection continue."),
    "model_unavailable_how": "Needs a technical fix; added to your next review prompt.",
    "timetable_failing": ("The timetable step keeps failing",
                          "Departments later in the timetable get no new turns."),
    "in_prompt": "Added to your next review prompt.",
    "nothing_now": "Nothing to do now. It's added to your next review prompt.",
    "turn_stuck": "{dept}'s turn is stuck",
    "turn_stuck_how": "Nothing to do right now; it's in your next review prompt.",
    "checkin_due": "Your '{title}' check-in is {state}",
    "checkin_due_why": "Send the review prompt to your review chat.",
    "orders_unclear": ("{n} practice order has an unclear status", "{n} practice orders have an unclear status"),
    "orders_unclear_why": "The team is double-checking.",
    "orders_unclear_consequence": "Resume is blocked until it's settled.",
    "repeat_problems": "{dept}'s turn ({cadence}) keeps having problems ({n} in a row)",
    "repeat_problems_plain": "{dept}'s turn keeps having problems ({n} in a row)",
    "repeat_problems_why": "Last reason: {reason}.",
    "repeat_problems_next": "Next try ",
    "notes_expire_early": "Research notes expire before the Trader reads them",
    "notes_expire_early_why": "The Trader only uses notes that are still in date.",
    "notes_expire_early_at": "At ",
    "notes_expire_early_after": " it decides without research notes.",
    "prices_old": "Prices are {age} old",
    "prices_old_why": "The Trader can't buy on old prices.",
    "prices_old_consequence": "A buy attempt would fail, and the system would switch itself to protect-only.",
    "allowance_near": "AI allowance {p}%: close to the {line}% line",
    "allowance_below": "AI allowance {p}%: below the line",
    "allowance_why": "Below the line, departments wait instead of thinking.",
    "allowance_near_consequence": "Turns may start waiting soon.",
    "allowance_below_refill": "Turns wait until it refills (",
    "allowance_below_unknown": "Turns wait until it refills (refill time not reported).",
    "command_interrupted": ("An earlier button press was interrupted", "Its outcome is unknown.",
                            "Check it before sending a replacement."),
    "owner_paused": "You paused the AI",
    "owner_paused_consequence": "Departments wait; prices and protection continue.",
    "costs_unconfirmed": ("Optional: enter this month's AI subscription bill",
                          "It completes your record of real costs."),
    "costs_unconfirmed_other": ("Optional: confirm your other running costs",
                                "It completes your record of real costs."),
    "costs_gap": "The 'since {day}' answer stays 'not known' because of the history gap.",
    "costs_reader_how": "Only the owner can record bills.",
    "costs_live_how": "Use Owner controls to record bills for a live account.",
    "trial_rollback_likely": "The current improvement trial will probably be undone (estimate)",
    "trial_rollback_why": ("Its trial needs {n} Trader decisions within {horizon}, "
                           "but the Trader works {cadence}."),
}
LINK_LABEL: Final = {
    "/owner": "Owner controls",
    "/progress": "Mission Control",
    "/organization": "Organisation records",
    "/trading": "Trading records",
    "/changes": "Improvements",
    "/journal": "Account journal",
    "/costs": "Costs",
}
RESOURCE_WARNING: Final = {
    "Less than 1 GiB free in database storage.": "Less than 1 GB of disk space free.",
    "Storage capacity observation unavailable.": "HQ couldn't check the free disk space.",
    "Container has recorded memory exhaustion events; counters are cumulative.":
        "The computer ran out of memory for the team at least once.",
    "Container has recorded task-capacity events; counters are cumulative.":
        "The team hit its limit on running tasks at least once.",
    "Container memory consumption is at least 90% of its limit.": "The team is using almost all its memory.",
    "Container pids consumption is at least 90% of its limit.": "The team is close to its limit on running tasks.",
}
RESOURCE_OTHER: Final = "The computer running the team reported a resource warning."

# --- D.8 Decision cards ---------------------------------------------------------------------

DECISION: Final = {
    "start_trading": {
        "title": "Start the team", "button": "Start", "confirm": "Yes, start the team", "weight": "light",
        "what": ("Starts the team's program, or reconnects to the one already running. It watches Kraken prices, "
                 "keeps the practice account and runs turns on the timetable. If AI isn't available it only "
                 "watches and protects. It does not lift a pause."),
        "cost": "No money. Turns then use your AI allowance.",
        "undo": "Yes: Stop safely.", "reversible": "yes",
        "recommended": "Recommended: nothing is being watched right now.",
        "not_needed": "Not needed right now: the team is on.",
        "not_needed_silent": "Not needed: the team's program was started but isn't responding.",
        "silent": "Start would only reconnect to the stuck program. This needs a technical check.",
    },
    "pause_ai": {
        "title": "Pause the AI, keep protection", "button": "Pause AI", "confirm": "Yes, pause the AI",
        "weight": "light",
        "what": "Departments stop starting new AI work. No new buying.",
        "working": "{dept} is working right now: its turn will be set aside; the allowance it already used "
                   "stays used.",
        "continues": "Prices, record-keeping and protection continue. Queued turns wait and re-check every 30 s.",
        "cost": "No money. Saves AI allowance.",
        "undo": "Yes: press Resume. It double-checks orders and balances first.", "reversible": "yes",
        "not_needed": "Not needed right now: all quiet.",
        "not_needed_off": "Not needed right now: the team is off.",
        "not_needed_silent": "Not needed right now: the team isn't responding.",
        "system": "Can't pause over an automatic safety stop (it already protects).",
        "leader_stronger": ("The Leader's '{profile}' setting is stronger and already protects. Pausing here would "
                            "weaken it."),
        "replaces_leader": ("This replaces the Leader's '{profile}' setting with your own pause. Only you can lift "
                            "it (Resume)."),
        "wait_before": "These would wait: ",
        "wait_after": ". Each runs once after Resume.",
        "repeat_problems": "{dept} keeps having problems; pausing saves allowance while you look.",
        "repeat_problems_when": "Recommended: {dept} keeps having problems.",
        "prices_old": "Prices are old; pausing stops new buying while you look.",
        "prices_old_when": "Recommended: prices are old.",
        "orders_unclear": "A practice order's status is unclear; pausing stops new buying while it's checked.",
        "orders_unclear_when": "Recommended: a practice order's status is unclear.",
        "allowance_below": "The allowance is below the line.",
        "allowance_below_when": "Recommended: the allowance is below the line.",
    },
    "resume": {
        "title": "Resume", "button": "Resume", "confirm": "Yes, resume", "weight": "light",
        "what": ("First double-checks orders and balances. If all is clear, departments return to their "
                 "timetable. Each department that missed turns runs once."),
        "below_line": "AI work starts only when the allowance is above the line.",
        "cost": "No money. Turns then use AI allowance.",
        "undo": "Yes: pause again any time.", "reversible": "yes",
        "only_after": "Only after a pause.",
        "system": "This is an automatic safety stop. See its card.",
        "blocked": "Resume will probably be refused because: {list}. Some of these may clear during its "
                   "double-check.",
        "blocked_definite": "Resume will probably be refused because: {list}.",
        "recommended": "Recommended: you paused the AI and nothing seems to block Resume.",
        "when": "When you're ready for departments to work again.",
    },
    "start_optimisation": {
        "title": "Ask for an optimisation review", "button": "Ask for a review",
        "confirm": "Yes, ask for one review", "weight": "light",
        "what": ("The Optimisation department reviews quality and cost once; the Leader then reads its answer and "
                 "may commission an improvement. Any improvement still needs checks and a trial. Nothing changes "
                 "automatically."),
        "cost": "No money. Uses AI allowance: up to {tries} tries plus a Leader follow-up. {deadline} "
                "The system checks your allowance when you confirm and may say no.",
        "deadline": "Must finish within {duration}.",
        "no_deadline": "No time limit set.",
        "few": "a few",
        "undo": "Can't be cancelled once started. Pausing sets aside its current step.", "reversible": "no",
        "when": "Occasionally, for a fresh look; not routinely.",
        "ineligible": "Needs the team running normally, no pause, and no review already in progress.",
    },
    "stop_safely": {
        "title": "Stop the team safely", "button": "Stop", "confirm": "Yes, stop the team", "weight": "careful",
        "what_flat": ("Everything stops and the team's program shuts down. While stopped, nothing is watched. "
                      "To restart: Start the team, wait about a minute, then Resume."),
        "what_held": ("Because something is held or an order is open, the team keeps running in protect-only "
                      "mode; it won't fully stop."),
        "what_unknown": ("If nothing is held, everything stops and nothing is watched. If something is held or "
                         "an order is open, the team keeps running in protect-only mode."),
        "replaces_safety_stop": ("Because nothing is held, stopping replaces the automatic safety stop with your "
                                 "own Stop. If the cause is still there, the system may stop itself again."),
        "cost": "No money.", "undo": "Yes: Start, then Resume.", "reversible": "yes",
        "when": "Only before PC maintenance or a long shutdown.",
        "undo_safety": ("Partly. If nothing is held, Start and then Resume bring the team back without the safety "
                        "stop, but its cause isn't fixed. If something is held, the safety stop stays."),
        "not_recommended_safety": ("Not recommended during an automatic safety stop. Copy the technical steps from "
                                   "its card first."),
    },
    "record_bill": {
        "title": "Record a bill", "button": "Record a bill", "confirm": "Yes, record this bill",
        "weight": "careful",
        "what": ("Saves a real bill for the month you choose; the share you choose counts as Trade Graph's cost. "
                 "Spends nothing and doesn't touch the practice account."),
        "cost": "No money moves.",
        "undo": ("No. Bills are permanent. A second bill for the same service and overlapping dates is refused, "
                 "so a mistake can't simply be replaced. Ask for help first if unsure."),
        "reversible": "no",
        "when": "Optional: it completes your record of real costs.",
        "recommended": "Your AI subscription bills aren't confirmed yet.",
        "recommended_other": "Your other running costs aren't confirmed yet.",
    },
    "declare_bills_complete": {
        "title": "I've entered every bill for a month", "button": "I've entered every bill for a month",
        "confirm": "Yes, confirm", "weight": "careful",
        "what": ("Tells HQ there are no other bills of the kinds you pick for that month. A later bill for that "
                 "month cancels this until you confirm again."),
        "gap": ("Because of the history gap, the since-start answer stays 'not known' even after you confirm."),
        "cost": "No money moves.", "undo": "No (permanent).", "reversible": "no",
        "when": "After you've entered the bills for a finished month.",
        "no_month": "No complete month has passed since the account started.",
        "recommended": "You recorded a bill for a finished month; confirm when every bill for it is in.",
    },
    "plan_checkin": {
        "title": "Plan a review check-in", "button": "Plan", "confirm": "Yes, add these check-ins",
        "weight": "light",
        "what": "Adds reminders to your HQ calendar. HQ sends nothing anywhere.",
        "cost": "Free.", "undo": "Yes: move or cancel any time.", "reversible": "yes",
        "when": "Whenever you want a reminder to review the run.",
        "recommended": "No review check-in is planned yet.",
        "unavailable": "Check-ins need the latest update to the HQ records.",
    },
}
BARRIER: Final = {
    "unresolved orders": "A practice order's status is unclear",
    "unresolved billing": "An AI charge is uncertain",
    "unresolved invoice differences": "A recorded invoice doesn't match",
    "version recovery pending": "An improvement is mid-switch",
    "system pause requires controller recovery": "Automatic safety stop",
    "requested pause management is incomplete": ("The stop step hasn't finished. Start the team and wait about "
                                                 "a minute."),
    "owner state changed during reconciliation; newer pause retained": "Another change happened at the same time",
}
GRAPH_SHARE_PRESETS: Final = ("0.25", "0.5", "0.75", "1")
SCHEDULED_DEPARTMENTS: Final = ("leader", "research", "trader", "learning", "optimisation")

# --- D.2 Right now ------------------------------------------------------------------------

NOW: Final = {
    "no_run": ("The team hasn't been started yet.", "Nothing is watching prices or running turns.",
               "Nothing happens until you start it."),
    "crashed": ("The team stopped unexpectedly ", "Last sign of life ",
                "Nothing is watched. Missed turns are merged into one when it starts again."),
    "crashed_detail_after": ". Nothing is being watched. The cause isn't recorded here.",
    "crashed_unknown": "The time of the last sign of life isn't recorded. Nothing is being watched.",
    "stopped": ("The team is switched off (since ", "Nothing is watched: no prices, no protection.",
                "Nothing is watched. Missed turns are merged into one when it starts again."),
    "attached_unknown": ("Something is using the team's records, but no team run is recorded.",
                         "Mission Control may say more.", "HQ can't tell whether anything is being watched."),
    "silent": ("No sign of life from the team for ",
               "The PC may be asleep, or the program may be stuck. Pressing Start would only reconnect to it.",
               "If it's stuck, nothing is watched until someone checks the PC."),
    "silent_never": "No sign of life from the team since it was asked to start.",
    "stopping": ("The team is shutting down safely.", "",
                 "It will stop shortly. Nothing is watched while it is off."),
    "safety_stop": ("The system switched to {profile} by itself ",
                    "This kind of stop can't be lifted from the dashboard.",
                    "It stays like this. Anything held stays protected."),
    "owner_stop_setting": ("Your Stop setting is still on.",
                           "Departments wait. Press Resume to let them work again.",
                           "Departments keep waiting; prices and protection continue."),
    "owner_pause": ("You paused the AI ", "Prices, record-keeping and protection continue; departments wait.",
                    "Departments keep waiting. Each runs once after you resume."),
    "leader_pause": ("The Leader paused decisions ", "Reason (in their words): ",
                     "The Leader can still review; other departments wait."),
    "model_unavailable": ("AI is switched off: the AI model isn't available.",
                          "This doesn't clear by itself; it needs a technical fix.",
                          "No AI work happens; prices and protection continue."),
    "quota_paused": ("AI is resting: the subscription allowance ran out.",
                     "It turns back on by itself when a newer reading shows allowance.",
                     "Departments wait for allowance; nothing else changes."),
    "timetable_failing": ("The timetable step keeps failing.",
                          "Departments later in the timetable get no new turns.",
                          "Some departments won't get turns until this is fixed."),
    "routine_failing": ("Running with a problem: {problem}.", "", ""),
    "orders_unclear": ("A practice order's status is unclear.",
                       "HQ is double-checking it. Resume is blocked until it's settled.",
                       "It keeps double-checking; no new buying for that coin."),
    "waiting_allowance": ("{dept}'s turn is waiting for AI allowance.",
                          "It retries every 30 seconds; nothing is used while waiting.",
                          "It starts when the allowance allows."),
    "working": ("{dept} is working right now.", "AI turn began ",
                "Its result appears in the Story when it finishes."),
    "working_try": " · try {used} of {max}",
    "working_unknown": "AI turn start time not recorded",
    "starting_turn": ("{dept}'s turn is starting now.", "", ""),
    "probably_low_allowance": ("Turns are probably waiting for AI allowance.",
                               "The last reading was {p}% (new work needs more than {line}%), ",
                               "Turns start again when the allowance is above the line."),
    "probably_low_after": ". The team re-checks before every turn.",
    "calm": ("All quiet. Nobody is working this minute.", "Next: ",
             "If you do nothing: "),
    "calm_none": "No department turn is booked yet.",
    "management_only": ("The team started while AI wasn't ready: prices and protection run; turns happen only if "
                        "AI becomes available."),
    "calm_trader": " the Trader may buy, sell or wait within its rules. Waiting is a normal choice.",
    "calm_other": "{dept} takes its next turn ({cadence}) at ",
    "calm_other_plain": "{dept} takes its next turn at ",
}
TURN_PHRASE: Final = {
    "trader": "the Trader looks at prices",
    "research": "Research writes notes for the Trader",
    "learning": "Learning looks back at decisions",
    "leader": "the Leader reviews reports",
    "optimisation": "Optimisation checks quality and cost",
}
FACTS: Final = {
    "practice_money": "Practice money ",
    "account_value": "Account value ",
    "money_unknown": "not known yet",
    "nothing_held": "nothing held",
    "in_coins": " in coins",
    "holdings_unknown": "holdings not known yet",
    "allowance": "AI allowance",
    "allowance_unknown": " not known yet",
    "allowance_reading": " {p}% (new AI work stops at {line}%; checked {ago})",
    "allowance_no_line": " {p}% (checked {ago})",
    "needs_you": ("{n} thing needs you", "{n} things need you"),
}

# --- D.10 Problem prefixes -------------------------------------------------------------------

PROBLEM_PREFIX: Final = {
    "feed:": ("couldn't fetch prices from Kraken",
              "New buying is blocked while prices are missing; anything held stays protected."),
    "reconciliation:": ("couldn't double-check orders and balances",
                        "No new practice orders are sent until this works."),
    "pause:": ("couldn't apply the pause setting", "The previous setting stays in force."),
    "dispatch:": ("couldn't send practice orders", "Orders wait until this works."),
    "scheduler:": ("the timetable step is failing", "Departments later in the timetable get no new turns."),
    "other": ("a routine step is failing", "Some background work is not happening."),
}

# --- D.11 Economic missing items and money copy --------------------------------------------

ECON: Final = {
    "subscription_bills_unconfirmed": ("You haven't confirmed your AI subscription bills for this period.", True),
    "other_costs_unconfirmed": ("You haven't confirmed other running costs (or that there are none).", True),
    "bill_matches_existing_records": ("A bill matches costs already recorded elsewhere.", False),
    "bill_euro_rate_missing": ("The euro rate for a bill's date is missing.", False),
    "bill_euro_rate_old": ("The euro rate for a bill's date is old.", False),
    "shared_between_accounts": ("Bills cover more than one practice account; this account's share isn't set.",
                                False),
    "valuation_not_final": ("Today's euro rate or prices aren't final yet.", False),
    "ai_usage_records_incomplete": ("Some AI usage records are incomplete.", False),
    "other_cost_value_unknown": ("A recorded running cost can't be valued yet.", False),
    "invoice_differences": ("A recorded invoice doesn't match.", False),
    "history_gap": ("Part of the early history was lost in a repair (accepted).", False),
    "other": ("Another record is incomplete.", False),
}
MONEY: Final = {
    "unavailable": "The practice account's numbers couldn't be worked out just now. HQ will try again shortly.",
    "change_unknown": "Can't split this yet: ",
    "change_unknown_reason": "today's prices or euro rate aren't final",
    "euro_rate_from_earlier_day": ("Uses the latest published euro rate (dated ",
                                   "). Normal until today's rate is published."),
    "euro_rate_too_old": "The euro rate is too old; the total is an estimate.",
    "prices_old": ("Prices are ", " old; the total is an estimate."),
    "no_prices": "No prices are recorded yet for something held; the total is an estimate.",
    "pays_unknown": NOT_KNOWN_YET,
    "pays_covered": "Yes, for this period: {net} left after costs.",
    "pays_not_covered": "Not yet: {net} after costs.",
    "honest_gap": ("Because of the history gap, this can't be answered for the whole time since {day}, even after "
                   "you enter bills."),
    "honest_unverified": ("Because early history couldn't be verified, this can't be answered for the whole time "
                          "since {day}, even after you enter bills."),
}
TREND: Final = {
    "below": "Recent prices are lower than the longer-run average (drifting down).",
    "above": "Recent prices are higher than the longer-run average (drifting up).",
    "level": "About level.",
    "not_enough_history": "Not enough price history yet.",
}
TREND_TAIL: Final = " The Trader decides for itself what this means."

# --- D.9 Story items ---------------------------------------------------------------------------

STORY: Final = {
    "decision_wait": "Trader looked at {symbols} and {action}.",
    "decision_trade": "Trader {action} {symbol}",
    "decision_trade_qty": "Trader decided to {verb} {qty} {asset} at a limit of ",
    "decision_other": "Trader {action}.",
    "no_money_moved": "No money moved.",
    "order_state": "Practice order {state}.",
    "order_refused": ("The software didn't place this practice order: ", ". No money moved."),
    "fill": "Practice trade completed: {side} {qty} {asset} at ",
    "fill_consequence": "Practice money now includes this trade.",
    "turn_done": "{dept} finished its turn ({cadence}).",
    "turn_done_plain": "{dept} finished its turn.",
    "research_notes": ("{n} note for the Trader, in use until ", "{n} notes for the Trader, in use until "),
    "research_none": "No notes were written.",
    "research_expired": (". They expired before the Trader's next turn (", "), so it didn't use them."),
    "lessons": ("{n} lesson recorded.", "{n} lessons recorded."),
    "optimisation_done": "Report filed; nothing changes automatically.",
    "leader_none": "No changes made.",
    "leader_engineer": "Asked the Engineer for an improvement.",
    "turn_problem": "{dept}'s turn ({cadence}) didn't work ({reason}).",
    "turn_problem_plain": "{dept}'s turn didn't work ({reason}).",
    "turn_problem_extra": "{dept}'s extra review didn't work ({reason}).",
    "next_try": "Next try ",
    "tried_again": ("Tried again ", "."),
    "safety_after_trader": "The system switched to protect-only for safety.",
    "stuck": "It won't retry by itself.",
    "turn_skipped": "{dept}'s turn was skipped: {reason}.",
    "nothing_used": "Nothing was used.",
    "allowance_wait": ("{dept}'s turn waited for AI allowance from ", " to ", " ({count} retries)."),
    "allowance_wait_one": "{dept}'s turn waited for AI allowance at ",
    "allowance_wait_at_least": ("{dept}'s turn waited for AI allowance from at least ", " to ",
                                " (at least {count} retries)."),
    "allowance_wait_consequence": "No allowance was used while waiting.",
    "allowance_wait_ran": " It ran at ",
    "ai_paused": "AI paused: {reason}.",
    "ai_paused_consequence": "Prices and protection continued; departments waited.",
    "team_started": "Team started (practice).",
    "team_started_live": "Team started (live account).",
    "team_started_consequence": "Missed turns were merged into one each.",
    "team_stopped": "Team stopped.",
    "gap": ("Nothing was watched from ", " to ", "."),
    "gap_open": ("Nothing has been watched since ", "."),
    "team_crashed": "The team stopped unexpectedly.",
    "crashed_gap": ("Last sign of life ", "; started again ", ". Nothing was watched in between."),
    "crashed_open": ("Last sign of life ", ". Nothing has been watched since."),
    "crashed_unknown": "The time of the last sign of life isn't recorded.",
    "routine_problem": "Problem while running: {prefix}",
    "recovered": " Recovered ",
    "run_ended": (" No longer tracked after the team stopped at ", "."),
    "run_ended_unknown": " No longer tracked after that team run ended.",
    "prices_problem": "Kraken prices stopped arriving",
    "prices_problem_symbol": "Kraken prices stopped arriving for {symbol}",
    "prices_consequence": "New buying was blocked; anything held stayed protected.",
    "history_problem": ("Hourly price history refresh partly failed.", "Hourly price history refresh failed."),
    "history_consequence": "It retries within about 30 seconds.",
    "safety_stop": "The system switched to {profile} by itself.",
    "safety_stop_consequence": "Departments stopped; protection continued.",
    "you_paused": "You paused the AI.",
    "you_resumed": "You resumed the team.",
    "you_resume_refused": "You tried to resume, but it was refused: {barriers}.",
    "you_resume_refused_plain": "You tried to resume, but it was refused.",
    "you_started": "You started the team.",
    "you_stopped": "You stopped the team.",
    "you_optimisation": "You asked for an optimisation review.",
    "you_bill": "You recorded a bill of ",
    "you_bill_kind": {"subscription": "AI subscription", "other": "other running cost"},
    "you_complete": "You confirmed all {kinds} bills for {month}.",
    "you_complete_kinds": {"subscription": "AI subscription", "other": "other running cost"},
    "you_checkin_done": "You marked '{title}' done.",
    "you_owner_action": "You changed a setting in Owner controls.",
    "paused_from": ("Departments waited from ", " to ", "."),
    "still_paused": "Still paused.",
    "improvement": "An improvement {state}.",
    "improvement_trial": "If its trial fails, the previous version comes back automatically.",
    "account_opened": "Practice account opened with ",
    "account_opened_live": "Account opened with ",
    "engineer_candidate": "The Engineer finished an improvement for the Leader to review.",
}

# --- D.12 Rooms -----------------------------------------------------------------------------

ROOM: Final = {
    "trader": ("Trader", "Trading desk", "Decides whether to buy, sell or wait with the practice money."),
    "research": ("Research", "Research lab", "Reads market data and writes short notes for the Trader."),
    "learning": ("Learning", "Learning room", "Looks back at decisions and writes lessons."),
    "leader": ("Leader", "Leader's office", "Sets priorities, reviews reports, can commission improvements."),
    "optimisation": ("Optimisation", "Optimisation workshop", "Checks the quality and cost of the team's work."),
    "engineer": ("Engineer", "Engineering workshop",
                 "Builds small, checked improvements when the Leader commissions one."),
    "secretary": ("Secretary", "Post room", "Keeps the records and tells the Leader about problems."),
    "market": ("Price feed", "Price feed", "Brings in BTC and ETH prices from Kraken's public feed."),
    "accounts": ("Accounts desk", "Accounts desk",
                 "Places practice orders, keeps the balances and double-checks them."),
}
OPTIMISATION_MANUAL_JOB: Final = "Only works when you ask."
ROOM_DETAILS: Final = {
    "trader": "/trading", "research": "/organization", "learning": "/organization", "leader": "/organization",
    "optimisation": "/organization", "engineer": "/changes", "secretary": "/organization", "market": "/trading",
    "accounts": "/trading",
}
LIGHT: Final = {
    "off": "Off: the team isn't running",
    "stuck": "Stuck: its turn won't retry by itself",
    "working": "Working right now",
    "waiting": "Waiting to start",
    "waiting_allowance": "Waiting for AI allowance",
    "paused_quota": "Paused: waiting for AI allowance",
    "paused_model": "Paused: the AI model isn't available",
    "paused_provider": "Paused: the AI is switched off",
    "paused_system": "Paused by the automatic safety stop",
    "paused_owner": "Paused by you",
    "paused_leader": "Paused by the Leader",
    "on_call": "On call: only works when asked",
    "resting": "Resting until its next turn",
    "ok": "Working normally",
    "attention": "Needs a look",
}
ROOM_NOW: Final = {
    "off": "Nothing happens while the team is off.",
    "waiting": "Waiting its turn.",
    "waiting_allowance": "Waiting for AI allowance (re-checks every 30 s).",
    "working": "Working on it",
    "paused_quota": "Paused: the allowance ran out. Prices and protection continue.",
    "paused_model": "Paused: the AI model isn't available. Prices and protection continue.",
    "paused_provider": "Paused: the AI is switched off. Prices and protection continue.",
    "paused_by": "Waiting: paused {by}.",
    "on_call_optimisation": "Only works when you ask.",
    "on_call_engineer": "No improvement has been commissioned.",
    "resting": "Nothing to do until its next turn.",
    "no_turn": "No turn is booked yet.",
    "had_problem": " Its last turn had a problem.",
    "secretary": "Keeping records.",
    "market_ok": "Prices are arriving.",
    "market_old": "Prices are old.",
    "market_degraded": "Kraken prices aren't arriving.",
    "market_none": "No prices recorded yet.",
    "accounts_flat": "Nothing held; no open orders.",
    "accounts_held": "Holding {coins} in coins; ",
    "accounts_orders": ("{n} open order.", "{n} open orders."),
    "accounts_no_orders": "no open orders.",
    "accounts_unclear": "A practice order's status is unclear; it's being double-checked.",
    "accounts_unknown": "Holdings not known yet.",
}
FACT_LABEL: Final = {
    "last_report": "Last report",
    "flagged": "Problems flagged (24 h)",
    "last_price": "Last price",
    "history": "History refresh",
    "euro_rate": "Euro rate",
    "open_orders": "Open orders",
    "unclear": "Orders being checked",
    "holdings": "Holdings",
    "double_check": "Last double-check",
    "automatic": "Automatic reviews",
    "next": "Next",
    "notes": "Notes",
}
FACT_TEXT: Final = {
    "not_recorded": "Not recorded",
    "nothing_held": "Nothing held",
    "automatic_off": "Off: only when you ask",
    "automatic_on": "On: part of the timetable",
    "engineer_next": "Only when the Leader commissions one",
    "history_next": "Next ",
    "history_last": "Last ",
    "euro_dated": "Dated ",
}
LAST: Final = {
    "decided_wait": ("Looked at {symbols} and ", ". No money moved."),
    "decided_trade": "Decided to {verb} {symbol}.",
    "decided_trade_refused": "Decided to {verb} {symbol}, but the software didn't place the order. No money moved.",
    "decided_other": "{action}.",
    "done": "Finished its turn ({cadence}).",
    "done_plain": "Finished its turn.",
    "research": ("Wrote {n} note for the Trader.", "Wrote {n} notes for the Trader."),
    "learning": ("Recorded {n} lesson.", "Recorded {n} lessons."),
    "optimisation": "Filed its report; nothing changes automatically.",
    "problem": "Its turn ({cadence}) didn't work: ",
    "problem_plain": "Its turn didn't work: ",
    "skipped": "Its turn was skipped: {reason}.",
    "stuck": "Its turn is stuck: {state}",
    "stuck_plain": "Its last turn got stuck.",
}
HEADS_UP: Final = {
    "trader_expired": ("Research notes expired at ", ", before this turn."),
    "trader_expires": ("Research notes expire at ", ", before this turn."),
    "research_expire": ("Its notes stop being used at ", ", before the Trader's next turn."),
    "trader_if_nothing": ("It decides at ", " without research notes."),
    "trader_default": "If you do nothing: at ",
}
RULES: Final = {
    "symbols": "{symbols} only.",
    "limit": "{symbols} only; at most {pct}% of the money in coins.",
}
SAFETY: Final = {
    "reason": "By the system (automatic safety stop) after {trigger}.",
    "why": "After {trigger}, the system switched to {profile}.",
    "trader_failed": "the Trader's turn failed",
    "out_of_allowance": "{dept}'s turn reached its cost limit",
    "out_of_allowance_plain": "a turn reached its cost limit",
    "unknown": "a problem it recorded",
    "management_service": "the team was started in protect-only maintenance mode",
}
PAUSED_BY: Final = {
    "owner": "By you.",
    "leader": "By the Leader.",
    "leader_quote": "By the Leader: “{quote}”.",
}
MISC: Final = {
    "time_not_recorded": "time not recorded",
    "the_market": "the market",
    "a_department": "A department",
    "a_coin": "a coin",
    "switched_off": "The team is switched off.",
    "cancelled": "it was cancelled",
    "overdue": " (overdue)",
    "that_period": "that period",
    "period_range": "{start} to {end}",
    "your": "your",
    "running_cost": "running cost",
    "and": " and ",
    "list_separator": ", ",
    "turn_began": ". AI turn began ",
    "last_reason": ("Last reason: ", "."),
    "prices_old": ("Prices are ", " old"),
    "in_coins": " in coins",
    "holding": ("Holding ", " in coins; "),
    "nothing_held_orders": "Nothing held; ",
    "range": ("–", ")."),
}
VERSION_EVENT: Final = {
    "activate": "was switched on for a trial",
    "observation_passed": "passed its trial",
    "rollback": "was undone after its trial",
    "rollback_requested": "is being undone",
    "reload": "was reloaded",
}
TRADE: Final = {
    "done": {"buy": "Bought {qty} {asset} at ", "sell": "Sold {qty} {asset} at "},
    "fee": ", fee ",
    "side": {"buy": "bought", "sell": "sold"},
    "fill_live": "Trade completed: {side} {qty} {asset} at ",
    "research_notes_plain": ("{n} note for the Trader.", "{n} notes for the Trader."),
    "account_opened_plain": "Practice account opened.",
    "account_opened_live_plain": "Account opened.",
}
GLOSSARY_TERMS: Final = {"wait": "wait", "allowance": "allowance"}

# --- Calendar ---------------------------------------------------------------------------------

CALENDAR: Final = {
    "turn": "{dept}'s turn",
    "extra_review": "{dept}'s extra review",
    "extra_review_detail": "The Secretary flagged a problem.",
    "checkin": "Your check-in: {title}",
    "research_expiry": "A research note stops being used",
    "research_expiry_many": "{n} research notes stop being used",
    "research_expiry_warning": "Before the Trader's turn at ",
    "allowance_refill": "AI allowance refills ({label} limit)",
    "allowance_refill_labels": {"weekly": "weekly", "5-hour": "5-hour", "other": "other"},
    "optimisation_deadline": "Optimisation review time limit",
    "trial_end": "Improvement trial ends (estimate)",
    "reload_end": "Improvement reload deadline (estimate)",
    "proposal_expiry": "Improvement idea expires",
    "proposal_expiry_many": "{n} improvement ideas expire",
    "engineer_deadline": "Engineer's time limit",
    "engineer_deadline_many": "{n} Engineer time limits",
    "mandate_expiry": "The Trader's permission slip expires",
    "refill_unknown": "Allowance refill time isn't reported.",
    "no_turns": "Department turns appear here once the team has been started.",
    "history": "Price history refresh",
    "done_problem": "Didn't work: {reason}.",
    "skipped": "Skipped: {reason}.",
    "stuck": "Stuck: it won't retry by itself.",
    "waiting": "Waiting to finish.",
    "only_if_resume": "Only if Resume is possible.",
    "only_if_running": "Only if the team is running.",
    "only_if_ai_on": "Only if the AI is back on.",
    "only_if_allowance": "Only once AI allowance is available.",
    "only_if_timetable": "Only if the timetable step works again.",
    "only_if_ai_available": "Only if AI becomes available.",
}

# --- Notices, prompt facts --------------------------------------------------------------------

NOTICE: Final = {
    "history_gap": ("Some early history is missing",
                    "An earlier repair lost some price records. It was accepted. Totals still include everything."),
    "history_unverified": ("Early history couldn't be checked",
                           "HQ couldn't verify that early history is complete. Totals still include every "
                           "saved record."),
    "read_only": ("View only", READ_ONLY),
    "live_mode": ("Live account", LIVE_MODE),
}
PROMPT: Final = {
    "last_24h": "the last 24 hours",
    "since_checkin": "my last check-in",
    "team_active": "{status}, last sign of life {ago}",
    "team_silent": "No sign of life for {span} (last sign of life {clock})",
    "team_off": "{status} (last sign of life {clock})",
    "allowance": "{p}% left {window} (checked {ago}); new AI work stops at {line}%",
    "allowance_no_line": "{p}% left {window} (checked {ago}); no reserve rule is set",
    "allowance_none": "No allowance reading yet; new AI work stops at {line}%",
    "allowance_none_no_line": "No allowance reading yet; no reserve rule is set",
    "allowance_rule_unknown": "{p}% left {window} (checked {ago}); the reserve rule couldn't be read",
    "allowance_none_rule_unknown": "No allowance reading yet; the reserve rule couldn't be read",
    "allowance_unreported": "; a shorter limit isn't reported",
    "safety_setting": "Automatic safety stop ({profile})",
    "pays_covered": "Yes: {net} left after costs",
    "pays_not_covered": "Not yet: {net} after costs",
}
WINDOW_PHRASE: Final = {"weekly": "this week", "5-hour": "in the 5-hour limit", "other": "in its limit"}
ALLOWANCE_LEFT: Final = {
    "weekly": "{p}% left this week",
    "5-hour": "{p}% left in the 5-hour limit",
    "other": "{p}% left (limit of {duration})",
}
ALLOWANCE_REFILLS: Final = "Refills "
ALLOWANCE_LINE: Final = "New AI work stops at {line}% (it needs more than that)."

# --- AI spending (T40, D175-D179) ------------------------------------------------------------
# Real costs of paid AI from receipts, in USD. Unknown is "not known yet", never zero. Alerts pause nothing.

SPENDING: Final = {
    "service": {"perplexity": "Perplexity web research", "jev": "Jev news alarm"},
    "service_short": {"perplexity": "Perplexity", "jev": "Jev news alarm"},
    "provider_page": {"perplexity": "Perplexity", "jev": "TypeSafe"},
    "line": {"news_alarm": "News alarm", "other": "Other"},
    "off": {"perplexity": "No Perplexity research has run yet.",
            "jev": "No Jev checks yet. They start once you install a Jev key."},
    "total": "Testing total so far: ",
    "total_ended": ("Testing ran until ", ": "),
    "from_receipts": " from receipts",
    "nothing": "nothing",
    "plus": ", plus ",
    "up_to": "up to ",
    "under_a_cent": "under $0.01",
    "not_known_yet": " not known yet",
    "plus_unknown": ", plus calls whose cost can't be worked out yet",
    "no_limit": " No spending limit while testing.",
    "alert_below": ("HQ alerts you here at ", ". It's an alert only: nothing pauses."),
    "alert_maybe": ("Testing spending may have passed your ", " alert: ", " known", " not known yet. Nothing was "
                    "paused."),
    "alert_maybe_unknown": ("Testing spending may have passed your ", " alert: ", " known, plus calls whose cost "
                            "can't be worked out yet. Nothing was paused."),
    "alert_passed": ("Testing spending passed your ", " alert: ", " so far. Nothing was paused."),
    "credit_none": "Prepaid credit: not recorded here. Your {provider} account page shows it.",
    "credit_head": ("Prepaid credit: ", " when you checked it on ", ". Since then "),
    "credit_left": (", so about ", " is left."),
    "credit_used": ", so the recorded credit looks used up. Check your account page.",
    "credit_unknown": ", plus calls whose cost can't be worked out yet.",
    "credit_info": (" Information only: HQ never stops anything at this amount; {provider} stops calls itself when "
                    "the credit runs out."),
    "monthly": ("This month so far: ", " of your ", " monthly limit (calls not known yet count at the most they "
                "could cost). Reaching it pauses Research and Leader news rounds only; the Trader and protection "
                "continue."),
    "paper_note": ("Practice money never pays for AI. These are real costs from receipts, paid from your provider "
                   "credit and kept apart from practice results."),
    "paper_note_live": "These are real costs from receipts. Account gains are never counted against them here.",
    "codex": ("Last 7 days: {n} turn on your ChatGPT subscription.", "Last 7 days: {n} turns on your ChatGPT "
              "subscription."),
    "codex_none": "No turns on your ChatGPT subscription in the last 7 days.",
    "attention_passed": ("{service} testing spending passed your ", " alert"),
    "attention_maybe": ("{service} testing spending may have passed your ", " alert"),
    "attention_why": " so far during testing, from receipts.",
    "attention_why_open": (" known so far during testing", " not known yet."),
    "attention_why_unknown": " known so far during testing, plus calls whose cost can't be worked out yet.",
    "attention_consequence": "Nothing is paused: it's an alert only.",
    "attention_how": {
        "perplexity": ("When you're ready, set a monthly limit of about 1.5 times the monthly cost HQ shows. "
                       "Until then testing has no limit."),
        "jev": "Nothing to do unless you want to switch Jev checks off.",
    },
}

# --- Wake-ups (T39, D215-D219) ----------------------------------------------------------------------------------
# Every wake-up the event engine recorded, each department's next check-in, today's steps, focus coins and alarms.
# All from saved records; reading them starts nothing. Notes and summaries are model text: quoted, never rewritten.

WAKES: Final = {
    "unavailable": ("This database has no wake-up records: it is older than the event engine. Nothing is missing; "
                    "wake-ups appear here once the team runs with them."),
    "none_yet": ("No wake-up has been recorded yet. Once the team runs with wake-ups, every one is listed here "
                 "with its reason."),
    "unreadable": ("1 wake-up record couldn't be read and is left out.",
                   "{n} wake-up records couldn't be read and are left out."),
    # Headline words per reason: (one, several). A wake's headline joins its reasons in the contract's order.
    "reason": {
        "day_start": ("the day start", "the day start"),
        "focus_step": ("the day start: pick today's focus coins", "the day start: pick today's focus coins"),
        "day_plan_step": ("the day start: write today's day plans", "the day start: write today's day plans"),
        "day_end_review": ("the day end: look back at the day", "the day end: look back at {n} days"),
        "check_in": ("its own check-in time", "its own check-in times"),
        "alarm_fired": ("a price alarm went off", "{n} price alarms went off"),
        "feed_lost": ("prices stopped arriving", "{n} price feeds stopped"),
        "feed_restored": ("prices are arriving again", "{n} price feeds came back"),
        "entry_filled": ("a waiting buy filled", "{n} fills of waiting buys"),
        "exit_filled": ("a safety exit filled", "{n} fills of safety exits"),
        "order_filled": ("an order filled", "{n} order fills"),
        "loss_limit_reached": ("the daily loss limit was reached", "the daily loss limit was reached"),
        "focus_updated": ("Research changed today's focus coins", "Research changed today's focus coins"),
        "wake_request": ("{who} asked for it", "{n} requests from other departments"),
        "news_alarm": ("a news alarm", "{n} news alarms"),
        "task_created": ("a task made for it", "{n} tasks made for it"),
    },
    "and": " and ",
    "who": {"leader": "the Leader", "trader": "the Trader", "research": "Research", "learning": "Learning",
            "optimisation": "Optimisation", "software": "software", "other": "another department"},
    # One line per event. Prices and quantities are the saved exact decimals.
    "event": {
        "day_start": ("The trading day started (", ")."),
        "focus_step": ("Day start: name today's focus coins from the scanner's shortlist of 1 coin.",
                       "Day start: name today's focus coins from the scanner's shortlist of {n} coins."),
        "focus_step_none": "Day start: name today's focus coins from the scanner's shortlist (none was handed over).",
        "day_plan_step": "Day start: write today's day plans.",
        "day_plan_step_focus": "Day start: write today's day plans (focus coins: {coins}).",
        "day_end_review": ("Day end: look back at the trading day of ", "."),
        "check_in": ("Its own check-in time came (", ")."),
        "alarm_fired": "{symbol} alarm went off: {condition}.",
        "alarm_fired_unknown": "A price alarm went off.",
        "feed_lost": "Prices for {symbol} stopped arriving ({feed}).",
        "feed_lost_unknown": "Prices stopped arriving for a coin an alarm watches.",
        "feed_restored": "Prices for {symbol} are arriving again ({feed}).",
        "feed_restored_unknown": "Prices are arriving again for a coin an alarm watches.",
        "entry_filled": "Waiting buy filled: {quantity} {base} at {price} {quote} each ({symbol}).",
        "exit_filled": "Safety exit filled at its {leg}: sold {quantity} {base} at {price} {quote} each.",
        "exit_filled_unknown_leg": "Safety exit filled: sold {quantity} {base} at {price} {quote} each.",
        "order_filled": "Order filled: {side} {quantity} {base} at {price} {quote} each.",
        "loss_limit_reached": ("The daily loss limit was reached: no new buying until the next day start; safety "
                               "exits stay."),
        "focus_updated": "Research changed today's focus coins.",
        "wake_request": "{who} asked for it with a note.",
        "news_alarm": "A news alarm.",
        "news_alarm_symbol": "A news alarm about {symbol}.",
        "task_created": "A task was made for it outside its wake-ups (for example a report to look at).",
    },
    "leg": {"stop": "stop", "take_profit": "target"},
    "side": {"buy": "bought", "sell": "sold", "other": "traded"},
    "feed": {"quote": "live prices", "candle": "{interval} candles", "other": "prices"},
    "interval": {1: "1-minute", 5: "5-minute", 15: "15-minute", 60: "1-hour"},
    # The AI allowance reading saved with the wake (D116). Unknown is never plenty.
    "allowance_term": "AI allowance",
    "allowance_head": " when woken: ",
    "allowance_left": ("{percent}% left (a reading from ", ")."),
    "allowance_no_amount": ("a reading from ", " that didn't say how much is left."),
    "allowance_paused": ("new AI work was paused (the ", "stop line", " or a provider pause), so the turn waited; "
                         "prices and protection went on."),
    "allowance_paused_left": (" {percent}% was left at ", "."),
    "allowance_unknown": "not known: no reading had been recorded. Unknown is never counted as plenty.",
    # What came of it.
    "queued": "Woken, waiting to start.",
    "working": "Awake and working on it.",
    "queued_paused": "Woken, waiting to start until the AI allowance allows new work. Protection goes on.",
    "applying": "Its answer is being put in place.",
    "done": ("Done after ", "."),
    "done_plain": "Done.",
    "failed": "Its turn didn't finish. What it was woken for waits for its next wake-up.",
    "failed_task": "Its turn didn't finish.",
    "obsolete": ("Too late to act: its trading day had ended before it ran, so it did nothing. What it was woken "
                 "for waits for its next wake-up."),
    "check_in_named": (" It chose to look again at ", "."),
    "check_in_day_start": (" It looks again at the next day start (", ")."),
    "focus_named": (" It named 1 focus coin.", " It named {n} focus coins."),
    "focus_none": " It named no focus coins.",
    "focus_refused": " Its focus coins weren't used: the software refused them.",
    "plans_installed": (" 1 day plan put in place.", " {n} day plans put in place."),
    "plans_refused": (" 1 refused by the software's checks.", " {n} refused by the software's checks."),
    "plans_obsolete": (" 1 came too late to use.", " {n} came too late to use."),
    "asked": " It asked {who} to look.",
    "answer_refused": " Part of its answer couldn't be used.",
    # Each department's next check-in (D123: its own time, or the next day start).
    "check_in": {
        "awake": ("Awake now: woken ", " for ", ". Its answer sets its next check-in."),
        "awake_waiting": ("Woken ", " for ", ", waiting to start. Its answer sets its next check-in."),
        "named": "It chose this time itself.",
        "named_due": "Its chosen time has passed; it is woken at the next check while the team runs.",
        "never": "It hasn't chosen a time yet, so the next day start wakes it.",
        "came": "Its chosen time came and it was woken; until it names another, the next day start wakes it.",
        "day_start": "It chose the next day start.",
    },
    # Today's steps of the daily cycle.
    "step": {
        "day_start": {"not_yet": "Not recorded yet: the day start runs when the team is running with wake-ups.",
                      "done": ("The day started at ", "."),
                      "late": (" The team started ", " after the day start.")},
        "scan": {"not_yet": "The coin scanner hasn't run for today yet.",
                 "ok": ("The coin scanner shortlisted 1 coin.", "The coin scanner shortlisted {n} coins."),
                 "saved": (" It used the newest saved scan, from ", "."),
                 "no_candidates": "The coin scanner found no coin worth a closer look today.",
                 "failed": "The coin scanner failed, so the day went on without a shortlist.",
                 "none": "No coin scan was available, so the day went on without a shortlist."},
        "focus": {"not_yet": "Research names today's focus coins after the scan.",
                  "started": "Research is picking today's focus coins.",
                  "done": "Research named today's focus coins.",
                  "failed": "Research's focus step didn't finish.",
                  "skipped": "Skipped: Research wasn't running."},
        "day_plan": {"not_yet": "The Trader writes its day plans after Research's focus step.",
                     "started": "The Trader is writing today's day plans.",
                     "none": "The Trader wrote no day plan today.",
                     "failed": "The Trader's day-plan step didn't finish.",
                     "skipped": "Skipped: the Trader wasn't running."},
        "day_end_review": {"not_yet": ("Learning looks back at the day when it ends (", ")."),
                           "started": "Learning is looking back at the day.",
                           "done": "Learning looked back at the day.",
                           "failed": "Learning's look back at the day didn't finish.",
                           "skipped": "Skipped: Learning wasn't running."},
    },
    # The day's focus coins (Research's newest revision of the day).
    "focus": {
        "named": ("Research named 1 focus coin for today.", "Research named {n} focus coins for today."),
        "revised": (" Changed during the day (version {revision}, ", ")."),
        "none_named": "Research named no focus coins today. That is a valid answer: nothing looked worth a plan.",
        "not_yet": "Research hasn't named today's focus coins yet.",
        "failed": "Research didn't name focus coins today.",
        "skipped": "Research wasn't running at the day start, so no focus coins were named.",
        "unreadable": "Today's focus record doesn't match its saved check value, so HQ doesn't show it.",
    },
    # Price alarms that are set (armed), and alarms that went off and wait for their department.
    "alarms": {
        "none": "No price alarm is set.",
        "set": ("1 price alarm is set.", "{n} price alarms are set."),
        "waiting": (" 1 went off and waits for {who}'s next wake-up.",
                    " {n} went off and wait for {who}'s next wake-up."),
    },
    "relation": {"above": "above", "below": "below", "crosses_above": "crosses above",
                 "crosses_below": "crosses below"},
    "quote_price": {"mid": "mid price", "bid": "bid", "ask": "ask", "last": "last trade price"},
    "candle_price": {"close": "close", "high": "high", "low": "low"},
    "candle": "{interval} candle {price} {relation} {level}",
    "indicator": "{label}{output} ({interval} candles) {relation} {level}",
    "counts": {
        "today": ("Today: 1 wake-up.", "Today: {n} wake-ups."),
        "today_none": "Today: no wake-up yet.",
        "week": " Last 7 days: {n}.",
    },
}

# --- D.13 Section help (also used by the page) ------------------------------------------------

SECTION_HELP: Final = {
    "now": ("Built from the system's own records every 30 seconds. Quiet means nobody is working; it doesn't "
            "mean broken."),
    "attention": "Only things that need a person are listed. An empty list means you can relax.",
    "upnext": "The moments something can change. Between them the team is idle.",
    "map": "A light changes only when a real record says so. Tap a room for details.",
    "calendar": ("Solid items are planned; '≈' items are estimates, because each turn sets the next one when it "
                 "actually runs."),
    "story": ("Everything here comes from saved records. 'In their words' quotes are short summaries the "
              "departments wrote."),
    "money": "Practice money and real costs are kept apart. Unknown is never shown as zero.",
    "decide": "Every button shows what will happen first and asks you to confirm. HQ never acts on its own.",
    "spending": ("Real costs of paid AI from receipts, kept apart from practice money. Costs not known yet are "
                 "never shown as zero. Alerts never pause anything."),
    "charts": ("Saved candles with the indicators, alarms and orders the team chose. Missing candles stay as gaps. "
               "Looking at a chart never changes anything."),
    "wakeplan": ("When each department looks next: the time it chose itself, or the next day start. Wake-ups happen "
                 "only while the team is running."),
    "watchlist": ("Today's focus coins from Research and the price alarms the departments set. Looking never changes "
                  "anything."),
    "wakes": ("Every wake-up from the saved records, with its reasons and the AI allowance at that moment. Nothing "
              "limits how often a department is woken."),
}
NO_RECORDED_ACTIVITY: Final = "No recorded activity"

# --- Live-account twins (C1) ------------------------------------------------------------------
# A live account's orders and money are never called "practice". Paper text above stays as it is.
# BARRIER and DECISION copy appear only when the viewer can act, which is paper only, so they need none.

LIVE_ATTENTION: Final = {
    "orders_unclear": ("{n} order has an unclear status", "{n} orders have an unclear status"),
}
LIVE_NOW: Final = {"orders_unclear": "An order's status is unclear."}
LIVE_PROBLEM_PREFIX: Final = {
    "reconciliation:": ("couldn't double-check orders and balances", "No new orders are sent until this works."),
    "dispatch:": ("couldn't send orders", "Orders wait until this works."),
}
LIVE_STORY: Final = {"order_state": "Order {state}.",
                     "order_refused": ("The software didn't place this order: ", ". No money moved.")}
LIVE_ROOM_JOB: Final = {
    "trader": "Decides whether to buy, sell or wait with the account's money.",
    "accounts": "Places orders, keeps the balances and double-checks them.",
}
LIVE_ROOM_NOW: Final = {"accounts_unclear": "An order's status is unclear; it's being double-checked."}
LIVE_REASON: Final = {
    "prices": "prices were too old to place an order",
    "unresolved": "an earlier order was still being checked",
}
LIVE_ECON: Final = {
    "shared_between_accounts": ("Bills cover more than one account; this account's share isn't set.", False),
}
LIVE_MONEY: Final = {
    "unavailable": "The account's numbers couldn't be worked out just now. HQ will try again shortly.",
}


def problem_prefix(prefix: str, *, live: bool) -> tuple[str, str]:
    """(what is failing, consequence) for a routine problem prefix."""
    return LIVE_PROBLEM_PREFIX.get(prefix, PROBLEM_PREFIX[prefix]) if live else PROBLEM_PREFIX[prefix]


def room_job(dept: str, *, live: bool) -> str:
    return LIVE_ROOM_JOB.get(dept, ROOM[dept][2]) if live else ROOM[dept][2]


def econ(code: str, *, live: bool) -> tuple[str, bool]:
    table = {**ECON, **LIVE_ECON} if live else ECON
    return table.get(code, ECON["other"])


def plural(forms: tuple[str, str], count: int) -> str:
    return (forms[0] if count == 1 else forms[1]).format(n=count)


def dept_name(dept: str) -> str:
    return ROOM[dept][0]
