"""The shadow news alarm report, built only from stored records (T44).

It lists headlines, Jev's answers as stored, would-wake verdicts as stored, response times and Jev spending
per UTC day. It never recomputes an answer or a verdict, never calls anything and writes nothing. Real,
synthetic and unresolved spending are shown apart; unresolved is never shown as zero cost.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from trade_graph.application.jev_checks import PURPOSE, ROLE
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.money import canonical_decimal

UNRESOLVED_STATES = ("RESERVED", "UNCERTAIN", "CONSERVATIVE")
SETTLED_STATES = ("COMMITTED", "RECONCILED")
ALERT_NOTE = "An alert only: nothing pauses at this amount (D138 item 10). Unresolved calls are not included."
READ_OUTCOMES = ("fetched", "not_modified")
LOST_NOTE = ("A feed that cannot be read is reported, never taken as \"no news\": its headlines for that time are "
             "unknown until it is read again.")


def _rows(database, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in database.execute(sql, params).fetchall()]


def _has_news_tables(database) -> bool:
    rows = database.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news_headlines'").fetchall()
    return bool(rows)


def _money(value: Decimal) -> str:
    return canonical_decimal(value)


def _cost(check: dict[str, Any]) -> dict[str, Any]:
    if check["reservation_id"] is None:
        return {"status": "not_called", "native_usd": None, "input_tokens": None, "output_tokens": None}
    settled = check["current_state"] in SETTLED_STATES and check["native_cost"] is not None
    status = ("synthetic" if check["synthetic"] else "committed") if settled else "unresolved"
    return {"status": status, "native_usd": _money(Decimal(check["native_cost"])) if settled else None,
            "input_tokens": check["input_tokens"], "output_tokens": check["output_tokens"]}


def _verdict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"would_wake": bool(row["would_wake"]), "wake_roles": json.loads(row["wake_roles_json"]),
            "subjects": json.loads(row["subjects_json"]), "held_subjects": json.loads(row["held_subjects_json"]),
            "event_type": row["event_type"], "holder_yes": row["holder_yes"], "rule_version": row["rule_version"],
            "urgent_threshold": row["urgent_threshold"]}


def _empty(since: datetime, until: datetime, alert_usd: Decimal, note: str) -> dict[str, Any]:
    return {"report": "news-alarm-shadow", "mode": "shadow", "wakes_sent": 0,
            "window": {"since": utc_iso(since), "until": utc_iso(until)},
            "summary": {"headlines": 0, "checked": 0, "answered": 0, "would_wake": 0,
                        "would_wake_by_role": {"leader": 0, "trader": 0}, "not_checked": 0, "failures": {},
                        "feeds_lost_now": 0},
            "feeds": [], "headlines": [], "latency_ms": {"checks": 0, "median": None, "max": None},
            "spend": {"days": [], "testing_total_real_usd": "0", "testing_unresolved_calls": 0,
                      "alert_usd": _money(alert_usd), "alert": False, "alert_note": ALERT_NOTE},
            "notes": [note]}


def news_alarm_report(database, *, since: datetime, until: datetime, alert_usd: Decimal = Decimal("5"),
                      check_window_seconds: int = 3600) -> dict[str, Any]:
    if not _has_news_tables(database):
        return _empty(since, until, alert_usd, "No news alarm records yet: this database has no news alarm log.")
    window = (utc_iso(since), utc_iso(until))
    headlines = _rows(database, """SELECT * FROM news_headlines WHERE received_at >= ? AND received_at < ?
        ORDER BY received_at, published_at, link""", window)
    checks = _rows(database, """SELECT a.attempt_id, a.headline_id, a.attempt_number, a.reservation_id,
            a.requested_model, a.synthetic, a.started_at, o.status, o.failure, o.http_status, o.answering_model,
            o.answers_json, o.input_tokens, o.output_tokens, o.latency_ms, o.receipt_id,
            r.native_cost, b.state AS current_state, b.amount AS held_eur
        FROM news_jev_attempts a JOIN news_headlines h ON h.headline_id = a.headline_id
        LEFT JOIN news_jev_outcomes o ON o.attempt_id = a.attempt_id
        LEFT JOIN usage_receipts r ON r.receipt_id = o.receipt_id
        LEFT JOIN budget_reservations b ON b.reservation_id = a.reservation_id
        WHERE h.received_at >= ? AND h.received_at < ? ORDER BY a.started_at, a.rowid""", window)
    verdicts = {row["attempt_id"]: row for row in _rows(database, """SELECT v.* FROM news_alarm_verdicts v
        JOIN news_headlines h ON h.headline_id = v.headline_id WHERE h.received_at >= ? AND h.received_at < ?""",
        window)}
    by_headline: dict[str, list[dict[str, Any]]] = {}
    for check in checks:
        verdict = _verdict(verdicts.get(check["attempt_id"]))
        answers = (json.loads(check["answers_json"], parse_float=str, parse_int=str)
                   if check["answers_json"] is not None else None)
        by_headline.setdefault(check["headline_id"], []).append({
            "attempt": check["attempt_number"], "status": check["status"] or "in_progress",
            "failure": check["failure"], "http_status": check["http_status"],
            "model_requested": check["requested_model"], "model_answered": check["answering_model"],
            "latency_ms": check["latency_ms"], "answers": answers, "cost": _cost(check), "verdict": verdict,
            "started_at": check["started_at"]})
    items, failures, roles = [], Counter(), Counter()
    summary = {"headlines": len(headlines), "checked": 0, "answered": 0, "would_wake": 0, "not_checked": 0}
    for headline in headlines:
        attempts = by_headline.get(headline["headline_id"], [])
        verdict = next((check["verdict"] for check in reversed(attempts) if check["verdict"] is not None), None)
        received = parse_utc(headline["received_at"])
        published = parse_utc(headline["published_at"]) if headline["published_at"] else None
        reference = published if published is not None and published <= received else received
        if any(check["attempt"] is not None for check in attempts):
            summary["checked"] += 1
        else:
            summary["not_checked"] += 1
        summary["answered"] += int(any(check["status"] == "answered" for check in attempts))
        for check in attempts:
            if check["status"] != "answered":
                failures[check["failure"] or check["status"]] += 1
        if verdict is not None and verdict["would_wake"]:
            summary["would_wake"] += 1
            roles.update(verdict["wake_roles"])
        not_checked = None
        if not attempts:
            not_checked = ("published before the check window"
                           if received - reference > timedelta(seconds=check_window_seconds)
                           else "Jev was off or had not run yet")
        items.append({"headline_id": headline["headline_id"], "title": headline["title"],
                      "publisher": headline["publisher"], "feed_id": headline["feed_id"], "link": headline["link"],
                      "published_at": headline["published_at"], "received_at": headline["received_at"],
                      "evidence": headline["evidence"], "checks": attempts, "verdict": verdict,
                      "not_checked": not_checked})
    summary["would_wake_by_role"] = {"leader": roles["leader"], "trader": roles["trader"]}
    summary["failures"] = dict(failures)
    feeds = _feeds(database, window)
    summary["feeds_lost_now"] = sum(1 for feed in feeds if feed["lost_now"])
    latencies = [check["latency_ms"] for check in checks if check["latency_ms"] is not None]
    report = {"report": "news-alarm-shadow", "mode": "shadow", "wakes_sent": 0,
              "window": {"since": window[0], "until": window[1]}, "summary": summary, "feeds": feeds,
              "headlines": items,
              "latency_ms": {"checks": len(latencies),
                             "median": statistics.median_low(latencies) if latencies else None,
                             "max": max(latencies) if latencies else None},
              "spend": _spend(database, window, alert_usd)}
    notes = ["Shadow mode: nobody was woken. A would-wake verdict is a log entry for checking Jev's judgement.",
             "Headline text is data, never instructions."]
    if any(item["evidence"] != "live_public" for item in items) or any(
            check["cost"]["status"] == "synthetic" for item in items for check in item["checks"]):
        notes.append("Synthetic headlines and scripted Jev replies are test data, not real news or Jev answers.")
    if summary["feeds_lost_now"]:
        notes.append(LOST_NOTE)
    if report["spend"]["testing_unresolved_calls"]:
        notes.append("Unresolved calls keep their full reservation until the usage is known; they are not free.")
    report["notes"] = notes
    return report


def _feeds(database, window: tuple[str, str]) -> list[dict[str, Any]]:
    """Each feed's polls in the window as stored (W9-A, D259): how many were read, how many lost, and whether its last
    poll was lost, since when, and when it was last read."""
    rows = _rows(database, """SELECT feed_id, publisher, started_at, outcome, http_status, error_category
        FROM news_feed_polls WHERE started_at >= ? AND started_at < ? ORDER BY started_at, rowid""", window)
    feeds: dict[str, dict[str, Any]] = {}
    for row in rows:
        feed = feeds.setdefault(row["feed_id"], {"feed_id": row["feed_id"], "publisher": row["publisher"], "polls": 0,
                                                 "read": 0, "lost": 0, "last_read_at": None, "lost_since": None})
        feed["polls"] += 1
        read = row["outcome"] in READ_OUTCOMES
        feed["read" if read else "lost"] += 1
        if read:
            feed["last_read_at"], feed["lost_since"] = row["started_at"], None
        elif feed["lost_since"] is None:
            feed["lost_since"] = row["started_at"]
        feed.update(publisher=row["publisher"], last_outcome=row["outcome"], last_http_status=row["http_status"],
                    last_error_category=row["error_category"], lost_now=not read)
    return [feeds[feed_id] for feed_id in sorted(feeds)]


def _spend(database, window: tuple[str, str], alert_usd: Decimal) -> dict[str, Any]:
    sql = """SELECT b.created_at, b.state, b.amount, b.synthetic, r.native_cost
        FROM budget_reservations b LEFT JOIN usage_receipts r
             ON r.reservation_id = b.reservation_id AND r.status = 'committed'
        WHERE b.role = ? AND b.purpose = ?"""
    every = _rows(database, sql, (ROLE, PURPOSE))
    days: dict[str, dict[str, Any]] = {}
    real_total, unresolved_total = Decimal("0"), 0
    for row in every:
        settled = row["state"] in SETTLED_STATES and row["native_cost"] is not None
        if settled and not row["synthetic"]:
            real_total += Decimal(row["native_cost"])
        unresolved = row["state"] in UNRESOLVED_STATES
        unresolved_total += int(unresolved)
        if not window[0] <= row["created_at"] < window[1]:
            continue
        day = days.setdefault(row["created_at"][:10], {"calls": 0, "real": Decimal("0"), "synthetic": Decimal("0"),
                                                      "unresolved_calls": 0, "held": Decimal("0")})
        day["calls"] += 1
        if settled:
            day["synthetic" if row["synthetic"] else "real"] += Decimal(row["native_cost"])
        if unresolved:
            day["unresolved_calls"] += 1
            day["held"] += Decimal(row["amount"])
    return {"days": [{"day": name, "calls": day["calls"], "real_usd": _money(day["real"]),
                      "synthetic_usd": _money(day["synthetic"]), "unresolved_calls": day["unresolved_calls"],
                      "unresolved_held_eur": _money(day["held"])} for name, day in sorted(days.items())],
            "testing_total_real_usd": _money(real_total), "testing_unresolved_calls": unresolved_total,
            "alert_usd": _money(alert_usd), "alert": real_total >= alert_usd, "alert_note": ALERT_NOTE}


def _top(answers: dict[str, Any] | None, name: str) -> tuple[str, str] | None:
    try:
        probabilities = answers[name]["probabilities"]
    except (KeyError, TypeError):
        return None
    if not isinstance(probabilities, dict) or not probabilities:
        return None
    option = max(probabilities, key=lambda key: Decimal(str(probabilities[key])))
    return option, str(probabilities[option])


def _time(value: str | None) -> str:
    return parse_utc(value).strftime("%Y-%m-%d %H:%M UTC") if value else "unknown time"


def _who(roles: list[str]) -> str:
    if roles == ["leader", "trader"]:
        return "the Leader and the Trader"
    return "the Leader" if roles == ["leader"] else "nobody"


def format_report_text(report: dict[str, Any]) -> str:
    summary, spend, latency = report["summary"], report["spend"], report["latency_ms"]
    roles = summary["would_wake_by_role"]
    lines = [f"News alarm report (shadow mode), {_time(report['window']['since'])} to "
             f"{_time(report['window']['until'])}",
             "Shadow mode: nobody was woken.",
             f"{summary['headlines']} headlines, {summary['checked']} checked by Jev, {summary['would_wake']} would "
             f"have woken someone (Leader {roles['leader']}, Trader {roles['trader']}).",
             f"Jev spending: testing total USD {spend['testing_total_real_usd']} of real calls (alert at USD "
             f"{spend['alert_usd']}{', reached' if spend['alert'] else ''}; an alert only), "
             f"{spend['testing_unresolved_calls']} unresolved calls (not free)."]
    for day in spend["days"]:
        lines.append(f"  {day['day']}: {day['calls']} calls, real USD {day['real_usd']}, synthetic USD "
                     f"{day['synthetic_usd']}, {day['unresolved_calls']} unresolved (EUR {day['unresolved_held_eur']}"
                     " held)")
    if report.get("feeds"):
        lines.append("Feeds:")
        for feed in report["feeds"]:
            if feed["lost_now"]:
                http = f", HTTP {feed['last_http_status']}" if feed["last_http_status"] else ""
                last = _time(feed["last_read_at"]) if feed["last_read_at"] else "not in this window"
                lines.append(f"  {feed['publisher']}: not read in its last poll ({feed['last_outcome']}{http}) since "
                             f"{_time(feed['lost_since'])}; read in {feed['read']} of {feed['polls']} polls, last read "
                             f"{last}. Not \"no news\": its headlines are unknown.")
            else:
                lines.append(f"  {feed['publisher']}: read in {feed['read']} of {feed['polls']} polls.")
    if latency["checks"]:
        lines.append(f"Jev response time: median {latency['median']} ms, slowest {latency['max']} ms over "
                     f"{latency['checks']} checks.")
    for item in report["headlines"]:
        label = "" if item["evidence"] == "live_public" else f"[{item['evidence'].replace('_', ' ')}] "
        when = (f"published {_time(item['published_at'])}, received {_time(item['received_at'])}"
                if item["published_at"] else f"received {_time(item['received_at'])}")
        lines += ["", f"{label}{item['publisher']}, {when}",
                  f"  {item['title']}", f"  {item['link']}"]
        for check in item["checks"]:
            if check["status"] == "answered":
                concerns, event = _top(check["answers"], "concerns"), _top(check["answers"], "event_type")
                holder = check["answers"]["holder_wants_to_know"]["probabilities"].get("yes")
                lines.append(f"  Jev ({check['model_answered']}, {check['latency_ms']} ms, {check['cost']['status']}):"
                             f" concerns {concerns[0]} {concerns[1]}; event {event[0]} {event[1]}; a holder would "
                             f"want to know within the hour {holder}")
            elif check["status"] == "budget_refused":
                lines.append("  Not sent to Jev: the money limit had no room.")
            else:
                http = f" (HTTP {check['http_status']})" if check["http_status"] else ""
                lines.append(f"  Jev check {check['attempt']} failed: {check['failure'] or check['status']}{http}; "
                             f"cost {check['cost']['status']}.")
        if item["verdict"] is not None:
            lines.append(f"  Would have woken {_who(item['verdict']['wake_roles'])}.")
        elif item["not_checked"]:
            lines.append(f"  Not checked: {item['not_checked']}.")
    lines += ["", *report["notes"]]
    return "\n".join(lines) + "\n"
