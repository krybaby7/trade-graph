"""HQ story (spec §B.5.9): what happened, newest first, built only from saved records.

Repeated records are collapsed (allowance waits, degraded/recovered pairs) and gaps
between team runs are described as gaps, never as "running". A day without items
simply has none; nothing is invented.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from trade_graph.api import hq_copy as c
from trade_graph.api.hq_contract import AI_DEPARTMENTS, STORY_MAX_ITEMS, iso_ms
from trade_graph.api.hq_facts import loads
from trade_graph.api.hq_state import STUCK, TERMINAL, Projection, _dt

WINDOW = timedelta(hours=72)
QUOTA_GAP_SECONDS = 90
CRASHED = frozenset({"FAILED", "INTERRUPTED"})


def story(p: Projection, *, window_start: datetime, window_end: datetime, checkins: list[dict],
          limit: int = STORY_MAX_ITEMS) -> dict:
    items: list[dict] = []
    items += _decisions(p)
    items += _fills(p)
    items += _tasks(p)
    items += _allowance_waits(p)
    items += _ai_paused(p)
    items += _runs(p)
    items += _routine_problems(p)
    items += _feed_problems(p)
    items += _history_problems(p)
    items += _safety_stop(p)
    items += _owner_actions(p, checkins)
    items += _improvements(p)
    items += _account_opened(p)
    unique: dict[str, dict] = {}
    for item in items:
        if item is None:
            continue
        moment = _dt(item["at"])
        if moment is not None and window_start <= moment < window_end:
            unique.setdefault(item["id"], item)
    ordered = sorted(unique.values(), key=lambda item: (item["at"], item["id"]), reverse=True)
    truncated = len(ordered) > limit
    ordered = ordered[:limit]
    next_before = ordered[-1]["at"] if truncated else iso_ms(window_start)
    return {"window_start": iso_ms(window_start), "next_before": next_before, "items": ordered}


def _story_item(item_id: str, at, kind: str, dept: str, tone: str, headline: list, *, until=None,
                consequence=None, quote=None, count=None, link=None, problem=False,
                technical=None) -> dict | None:
    if _dt(at) is None:
        return None  # an unreadable timestamp is left out, never guessed
    return {"id": item_id, "at": iso_ms(_dt(at)), "until": iso_ms(_dt(until)) if _dt(until) else None, "kind": kind,
            "dept": dept, "tone": tone, "headline": headline, "consequence": consequence or None, "quote": quote,
            "count": count, "link": link, "problem": problem, "technical": c.technical(technical)}


def _dept(role) -> str | None:
    return role if role in AI_DEPARTMENTS else None


# --- Trader decisions and fills ----------------------------------------------------------------------


def _decisions(p: Projection) -> list:
    items = []
    tasks = {task["task_id"]: task for task in p.facts["recent_tasks"]}
    for row in p.facts["decisions"]:
        if _dt(row["created_at"]) is None:
            continue
        payload = loads(row.get("payload_json"))
        action = row["action"]
        words = c.DECISION_ACTION.get(action, action.replace("_", " "))
        symbols = p.decision_symbols(payload)
        consequence = c.says(c.STORY["no_money_moved"])
        if action in ("no_action", "hold"):
            headline = c.says(c.STORY["decision_wait"].format(symbols=p.symbols_text(symbols), action=words))
        elif action in ("enter", "exit"):
            symbol = symbols[0] if symbols else c.MISC["a_coin"]
            head = c.STORY["decision_trade"].format(action=words, symbol=symbol)
            quantity = c.decimal_or_none(payload.get("quantity"))
            limit = c.decimal_or_none(payload.get("limit_price"))
            quote = symbol.split("/")[1] if "/" in symbol else None
            if quantity is not None and limit is not None and quote:
                headline = c.says(head + c.TRADE["limit"].format(qty=c.quantity(quantity)),
                                  c.money_segment(limit, quote), c.TRADE["limit_after"])
            else:
                headline = c.says(head + ".")
            consequence = _order_consequence(p, tasks.get(row.get("task_id")), quote)
        else:
            headline = c.says(c.STORY["decision_other"].format(action=words))
        items.append(_story_item(f"decision:{row['decision_id']}", row["created_at"], "trader_decision", "trader",
                                 "neutral", headline, consequence=consequence, quote=p.decision_quote(payload),
                                 link=f"/decisions/{row['decision_id']}", technical=action))
    return items


def _order_consequence(p: Projection, task: dict | None, quote: str | None) -> list | None:
    intent_id = loads(task.get("output_json")).get("intent_id") if task else None
    if not intent_id:
        return None
    filled = [fill for fill in p.facts["fills"] if fill.get("intent_id") == intent_id]
    if filled and quote:
        document = filled[0]["document"]
        quantity, price = c.decimal_or_none(document.get("quantity")), c.decimal_or_none(document.get("price"))
        fee, fee_asset = c.decimal_or_none(document.get("fee_amount")), document.get("fee_asset")
        if quantity is not None and price is not None:
            side = "buy" if document.get("side") == "buy" else "sell"
            parts: list = [c.TRADE["done"][side].format(qty=c.quantity(quantity)), c.money_segment(price, quote)]
            if fee is not None and isinstance(fee_asset, str) and fee_asset == quote:
                parts += [c.TRADE["fee"], c.money_segment(fee, quote)]
            return c.says(*parts, ".")
    row = p.runtime.database.execute("SELECT state FROM order_intents WHERE intent_id=?", (intent_id,)).fetchone()
    if row is None:
        return None
    template = c.STORY["order_state"] if p.paper else c.LIVE_STORY["order_state"]
    return c.says(template.format(state=c.ORDER_STATE.get(row["state"], row["state"].lower())))


def _fills(p: Projection) -> list:
    items = []
    for fill in p.facts["fills"]:
        document = fill["document"]
        symbol = document.get("symbol")
        quantity, price = c.decimal_or_none(document.get("quantity")), c.decimal_or_none(document.get("price"))
        if not isinstance(symbol, str) or "/" not in symbol or quantity is None or price is None:
            continue
        base, quote = symbol.split("/", 1)
        side = c.TRADE["side"]["buy" if document.get("side") == "buy" else "sell"]
        template = c.STORY["fill"] if p.paper else c.TRADE["fill_live"]
        lead = template.format(side=side, qty=c.quantity(quantity), asset=base)
        consequence = c.says(c.STORY["fill_consequence"]) if p.paper else None
        items.append(_story_item(f"fill:{fill['fill_id']}", fill["created_at"], "fill", "accounts", "good",
                                 c.says(lead, c.money_segment(price, quote), "."), consequence=consequence))
    return items


# --- Department turns -----------------------------------------------------------------------------------


def _tasks(p: Projection) -> list:
    items = []
    for task in p.facts["recent_tasks"]:
        role = _dept(task["role"])
        status = task["status"]
        if role is None or (status not in TERMINAL and status not in STUCK):
            continue
        at = p.task_moment(task)
        dept = c.dept_name(role)
        cadence = p.task_cadence(task)
        extra = (task.get("dedup_key") or "").startswith("secretary:")
        raw = p.raw_reason(task)
        if status == "SUCCEEDED":
            if role == "trader" and p.task_decision(task) is not None:
                continue
            headline = c.says(c.STORY["turn_done"].format(dept=dept, cadence=cadence) if cadence
                              else c.STORY["turn_done_plain"].format(dept=dept))
            items.append(_story_item(f"task:{task['task_id']}", at, "turn_done", role, "good", headline,
                                     consequence=_done_consequence(p, task, at),
                                     quote=c.quote(loads(task.get("result_json")).get("summary")),
                                     link="/changes" if role == "engineer" else None))
        elif p.is_skip(task) or status == "CANCELLED":
            reason = c.SKIP_PLAIN.get(p.skip_key(task) or "", c.MISC["cancelled"])
            items.append(_story_item(f"task:{task['task_id']}", at, "turn_skipped", role, "neutral",
                                     c.says(c.STORY["turn_skipped"].format(dept=dept, reason=reason)),
                                     consequence=c.says(c.STORY["nothing_used"])))
        else:
            reason = c.reason_plain(raw, live=not p.paper) or (c.REASON["budget"] if status == "BLOCKED_BUDGET"
                                                               else c.REASON["unknown"])
            if extra:
                headline = c.STORY["turn_problem_extra"].format(dept=dept, reason=reason)
            elif cadence:
                headline = c.STORY["turn_problem"].format(dept=dept, cadence=cadence, reason=reason)
            else:
                headline = c.STORY["turn_problem_plain"].format(dept=dept, reason=reason)
            items.append(_story_item(f"task:{task['task_id']}", at, "turn_problem", role, "attention",
                                     c.says(headline), consequence=_problem_consequence(p, task, role, at),
                                     link="/organization", problem=True, technical=raw or status))
    return items


def _done_consequence(p: Projection, task: dict, at: datetime) -> list | None:
    result = loads(task.get("result_json"))
    role = task["role"]
    if role == "research":
        ids = [item for item in result.get("finding_ids") or [] if isinstance(item, str)]
        if not ids:
            return c.says(c.STORY["research_none"])
        expiries = [moment for item in ids if (finding := p.findings_by_id.get(item))
                    and (moment := _dt(finding.get("expires_at")))]
        if not expiries:
            return c.says(c.plural(c.TRADE["research_notes_plain"], len(ids)))
        until = max(expiries)
        parts: list = [c.plural(c.STORY["research_notes"], len(ids)), c.time(until, "clock")]
        next_turn = _next_trader_turn_after(p, at)
        if next_turn is not None and until < next_turn:
            before, after = c.STORY["research_expired"]
            parts += [before, c.time(next_turn, "clock"), after]
        else:
            parts.append(".")
        return c.says(*parts)
    if role == "learning":
        return c.says(c.plural(c.STORY["lessons"], len(result.get("lesson_revision_ids") or [])))
    if role == "optimisation":
        return c.says(c.STORY["optimisation_done"])
    if role == "leader":
        engineer = bool(result.get("change_id") or result.get("change_ids") or result.get("commission"))
        return c.says(c.STORY["leader_engineer" if engineer else "leader_none"])
    return None


def _next_trader_turn_after(p: Projection, moment: datetime) -> datetime | None:
    later = [at for row in p.facts["decisions"] if (at := _dt(row["created_at"])) and at > moment]
    later += [p.task_moment(task) for task in p.roles["trader"].recent
              if (_dt(task["created_at"]) or moment) > moment]
    if later:
        return min(later)
    trader = p.roles["trader"].next_at
    return trader if trader is not None and trader > moment else None


def _problem_consequence(p: Projection, task: dict, role: str, at: datetime) -> list | None:
    if task["status"] in STUCK:
        return c.says(c.STORY["stuck"])
    if role == "trader" and p.system_pause and p.pause_since and abs((p.pause_since - at).total_seconds()) < 300:
        return c.says(c.STORY["safety_after_trader"])
    next_at = p.roles[role].next_at
    if next_at is not None and next_at > at:
        return c.says(c.STORY["next_try"], c.time(next_at, "day_clock"), ".")
    return None


# --- Allowance and AI pauses ----------------------------------------------------------------------------


def _allowance_waits(p: Projection) -> list:
    streaks: dict[str, list[list]] = {}
    for event in sorted((event for event in p.facts["events"] if event["kind"] == "subscription_quota_deferred"),
                        key=lambda event: event["created_at"]):
        role = event["payload"].get("role")
        moment = _dt(event["created_at"])
        if moment is None or not isinstance(role, str):
            continue
        groups = streaks.setdefault(role, [])
        if groups and (moment - groups[-1][-1][0]).total_seconds() <= QUOTA_GAP_SECONDS:
            groups[-1].append((moment, event))
        else:
            groups.append([(moment, event)])
    items = []
    for role, groups in streaks.items():
        dept = c.dept_name(role) if role in c.ROOM else c.MISC["a_department"]
        story_dept = role if role in c.ROOM else "hq"
        for group in groups:
            first, last, count = group[0][0], group[-1][0], len(group)
            if count == 1:
                headline = c.says(c.STORY["allowance_wait_one"].format(dept=dept), c.time(first, "clock"), ".")
            else:
                before, middle, after = c.STORY["allowance_wait"]
                headline = c.says(before.format(dept=dept), c.time(first, "clock"), middle, c.time(last, "clock"),
                                  after.format(count=count))
            consequence = [c.text(c.STORY["allowance_wait_consequence"])]
            task_id = group[-1][1]["payload"].get("task_id")
            started = p.facts.get("attempt_starts", {}).get(task_id) if task_id else None
            if started and _dt(started) and _dt(started) >= last:
                consequence = c.says(*consequence, c.STORY["allowance_wait_ran"], c.time(started, "clock"), ".")
            items.append(_story_item(f"quota-wait:{role}:{iso_ms(first)}", first, "allowance_wait", story_dept,
                                     "neutral", headline, until=last if count > 1 else None, consequence=consequence,
                                     count=count, technical="subscription_quota_deferred"))
    return items


def _ai_paused(p: Projection) -> list:
    items = []
    reason_raw = p.provider_reason or None
    reason = c.AI_PAUSE_REASON.get(reason_raw or "", c.AI_PAUSE_REASON_OTHER)
    for event in p.facts["events"]:
        if event["kind"] != "subscription_ai_paused":
            continue
        items.append(_story_item(f"ai-paused:{event['event_id']}", event["created_at"], "ai_paused", "hq",
                                 "attention", c.says(c.STORY["ai_paused"].format(reason=reason)),
                                 consequence=c.says(c.STORY["ai_paused_consequence"]), problem=True,
                                 technical=reason_raw))
    return items


# --- Team runs and routine problems ---------------------------------------------------------------------


def _runs(p: Projection) -> list:
    items = []
    runs = p.facts["runs"]  # newest first
    current = p.facts.get("run") or {}
    for index, run in enumerate(runs):
        status = run["status"]
        if run["run_id"] == current.get("run_id") and current.get("status"):
            status = current["status"]
        requested = _dt(run["requested_at"])
        if requested is not None:
            headline = c.STORY["team_started" if p.paper else "team_started_live"]
            items.append(_story_item(f"run:{run['run_id']}:started", requested, "team_started", "hq", "good",
                                     c.says(headline), consequence=c.says(c.STORY["team_started_consequence"])))
        if status not in CRASHED and status != "STOPPED":
            continue
        last_sign = _dt(run["heartbeat_at"]) or _dt(run["finished_at"])
        ended = _dt(run["finished_at"]) or last_sign
        if ended is None:
            continue
        newer = runs[index - 1] if index > 0 else None
        restarted = _dt(newer["requested_at"]) if newer else None
        if status == "STOPPED":
            moment = last_sign or ended
            if restarted:
                before, middle, after = c.STORY["gap"]
                consequence = c.says(before, c.time(moment, "clock"), middle, c.time(restarted, "clock"), after)
            else:
                before, after = c.STORY["gap_open"]
                consequence = c.says(before, c.time(moment, "clock"), after)
            items.append(_story_item(f"run:{run['run_id']}:stopped", moment, "team_stopped", "hq", "neutral",
                                     c.says(c.STORY["team_stopped"]), until=restarted, consequence=consequence))
        else:
            if last_sign and restarted:
                before, middle, after = c.STORY["crashed_gap"]
                consequence = c.says(before, c.time(last_sign, "clock"), middle, c.time(restarted, "clock"), after)
            elif last_sign:
                before, after = c.STORY["crashed_open"]
                consequence = c.says(before, c.time(last_sign, "clock"), after)
            else:
                consequence = c.says(c.STORY["crashed_unknown"])
            error = current.get("error_type") if run["run_id"] == current.get("run_id") else run.get("error_type")
            items.append(_story_item(f"run:{run['run_id']}:ended", last_sign or ended, "team_crashed", "hq",
                                     "attention", c.says(c.STORY["team_crashed"]), until=restarted,
                                     consequence=consequence, problem=True,
                                     technical=f"{status}: {error}" if error else status))
    return items


class _Span:
    """One problem span: its degraded events, and how it ended (a recovery, or the end of its team run)."""

    def __init__(self, run_id: str | None) -> None:
        self.group: list[dict] = []
        self.run_id = run_id
        self.recovered: dict | None = None
        self.run_ended = False  # closed because its team run ended, never told as a recovery

    @property
    def first(self) -> dict:
        return self.group[0]


def _paired(p: Projection, events: list, start_kind_test, end_kind_test) -> list[_Span]:
    """Group oldest-first events into problem spans, each within one team run (C2).

    A recovery in the same run closes a span. An event of another run closes it at its own run's end, and so
    does the end of the list when its run is not the current active run: the worker forgets its problems on
    restart and never records a recovery for the old run.
    """
    spans: list[_Span] = []
    current: _Span | None = None
    for event in sorted(events, key=lambda event: event["created_at"]):
        run_id = p.event_run(event)
        if current is not None and run_id != current.run_id:
            current.run_ended = True
            spans.append(current)
            current = None
        if start_kind_test(event):
            if current is None:
                current = _Span(run_id)
            current.group.append(event)
        elif end_kind_test(event) and current is not None:
            current.recovered = event
            spans.append(current)
            current = None
    if current is not None:
        current.run_ended = current.run_id is None or not p.active or current.run_id != p.run_id
        spans.append(current)
    return spans


def _span_end(p: Projection, span: _Span, start: datetime) -> tuple[datetime | None, list]:
    """(until, consequence tail) for a span: recovered at, or the end of its team run."""
    if span.recovered is not None:
        end = _dt(span.recovered["created_at"])
        return end, c.says(c.STORY["recovered"], c.time(end, "clock"), ".") if end else []
    if not span.run_ended:
        return None, []
    end = p.run_end(span.run_id)
    if end is None or end < start:
        return None, c.says(c.STORY["run_ended_unknown"])
    before, after = c.STORY["run_ended"]
    return end, c.says(before, c.time(end, "clock"), after)


def _routine_problems(p: Projection) -> list:
    events = [event for event in p.facts["events"] if event["kind"] in ("service_degraded", "service_recovered")]
    items = []
    for span in _paired(p, events, lambda e: e["kind"] == "service_degraded",
                        lambda e: e["kind"] == "service_recovered"):
        first = span.first
        failures = []
        for event in span.group:
            for failure in event["payload"].get("failures") or []:
                if isinstance(failure, str) and failure not in failures:
                    failures.append(failure)
        prefixes = [next((known for known in c.PROBLEM_PREFIX if failure.startswith(known)), "other")
                    for failure in failures]
        if not prefixes or all(prefix == "feed:" for prefix in prefixes):
            continue  # missing prices are told by the price-feed story
        prefix = next(prefix for prefix in prefixes if prefix != "feed:")
        words, consequence = c.problem_prefix(prefix, live=not p.paper)
        start = _dt(first["created_at"])
        if start is None:
            continue
        end, ended = _span_end(p, span, start)
        headline = c.says(c.STORY["routine_problem"].format(prefix=words), " (", c.time(start, "clock"))
        headline = (c.says(*headline, c.MISC["range"][0], c.time(end, "clock"), c.MISC["range"][1]) if end
                    else c.says(*headline, c.MISC["range"][1]))
        tail = c.says(consequence, *ended)
        items.append(_story_item(f"routine:{first['event_id']}", start, "routine_problem", "hq", "attention", headline,
                                 until=end, consequence=tail, problem=True, technical=";".join(failures)))
    return items


def _feed_problems(p: Projection) -> list:
    events = [event for event in p.facts["events"] if event["kind"] == "public_feed_health"]
    items = []
    for span in _paired(p, events, lambda e: e["payload"].get("status") == "degraded",
                        lambda e: e["payload"].get("status") == "recovered"):
        first = span.first
        start = _dt(first["payload"].get("first_failed_at_utc")) or _dt(first["created_at"])
        if start is None:
            continue
        end, ended = _span_end(p, span, start)
        symbol = first["payload"].get("symbol")
        headline = (c.STORY["prices_problem_symbol"].format(symbol=symbol)
                    if isinstance(symbol, str) and symbol in ("BTC/USD", "ETH/USD", "BTC/EUR", "ETH/EUR")
                    else c.STORY["prices_problem"])
        consequence = c.says(c.STORY["prices_consequence"], *ended)
        items.append(_story_item(f"feed:{iso_ms(start)}", start, "prices_problem", "market", "attention",
                                 c.says(headline + "."), until=end, consequence=consequence, problem=True,
                                 technical=first["payload"].get("error_type")))
    return items


def _history_problems(p: Projection) -> list:
    items = []
    for event in p.facts["events"]:
        if event["kind"] != "public_history_refresh":
            continue
        status = event["payload"].get("status")
        if status in (None, "ok"):
            continue
        partial, failed = c.STORY["history_problem"]
        items.append(_story_item(f"history:{event['event_id']}", event["created_at"], "history_problem", "market",
                                 "attention", c.says(partial if status == "partial" else failed),
                                 consequence=c.says(c.STORY["history_consequence"]), problem=True,
                                 technical=str(status)))
    return items


def _safety_stop(p: Projection) -> list:
    if not p.system_pause or p.pause_since is None:
        return []
    return [_story_item("pause:system", p.pause_since, "safety_stop", "hq", "attention",
                        c.says(c.STORY["safety_stop"].format(profile=c.PAUSE_SHORT[p.profile])),
                        consequence=c.says(c.STORY["safety_stop_consequence"]), problem=True,
                        technical=p.profile)]


# --- Owner actions, improvements, account -------------------------------------------------------------------


def _owner_actions(p: Projection, checkins: list[dict]) -> list:
    items = []
    commands = sorted((row for row in p.facts["commands"] if _dt(row.get("created_at"))),
                      key=lambda row: row["created_at"])
    for index, row in enumerate(commands):
        action, status = row.get("action"), str(row.get("status") or "")
        at = row["created_at"]
        item_id = f"command:{row['command_id']}"
        if action == "pause" and status == "COMPLETE":
            resume = next((later for later in commands[index + 1:]
                           if later.get("action") == "resume" and later.get("status") == "COMPLETE"), None)
            if resume:
                before, middle, after = c.STORY["paused_from"]
                consequence = c.says(before, c.time(at, "clock"), middle, c.time(resume["created_at"], "clock"), after)
            elif p.owner_pause:
                consequence = c.says(c.STORY["still_paused"])
            else:
                consequence = None
            items.append(_story_item(item_id, at, "owner_action", "you", "neutral", c.says(c.STORY["you_paused"]),
                                     consequence=consequence))
        elif action == "resume" and status == "COMPLETE":
            items.append(_story_item(item_id, at, "owner_action", "you", "good", c.says(c.STORY["you_resumed"])))
        elif action == "resume" and status.startswith("FAILED"):
            items.append(_story_item(item_id, at, "owner_action", "you", "attention",
                                     c.says(c.STORY["you_resume_refused_plain"]),
                                     technical=status))
    for row in p.facts["service_requests"]:
        headline = {"start_trading": c.STORY["you_started"], "stop_service": c.STORY["you_stopped"],
                    "start_optimisation": c.STORY["you_optimisation"]}.get(row["action"])
        if headline:
            items.append(_story_item(f"service-request:{row['request_id']}", row["created_at"], "owner_action",
                                     "you", "neutral", c.says(headline)))
    for row in p.facts["bills"]:
        document = row["document"]
        if row["record_type"] == "expense":
            amount = c.decimal_or_none(document.get("amount_native"))
            currency = document.get("native_currency")
            kind = c.STORY["you_bill_kind"].get(row.get("expense_kind") or "", c.MISC["running_cost"])
            if amount is None or not isinstance(currency, str):
                continue
            headline = c.says(c.STORY["you_bill"], c.money_segment(amount, currency), f" ({kind}).")
        else:
            kinds = c.MISC["and"].join(c.STORY["you_complete_kinds"].get(kind, kind)
                                 for kind in document.get("expense_kinds") or [])
            headline = c.says(c.STORY["you_complete"].format(kinds=kinds or c.MISC["your"],
                                                             month=_period_text(document)))
        items.append(_story_item(f"bill:{row['sequence']}", row["created_at"], "owner_action", "you", "neutral",
                                 headline))
    for item in checkins:
        if item["state"] == "done" and item.get("done_at"):
            items.append(_story_item(f"checkin-done:{item['checkin_id']}", item["done_at"], "owner_action", "you",
                                     "good", c.says(c.STORY["you_checkin_done"].format(title=item["title"]))))
    return items


def _period_text(document: dict) -> str:
    start, end = _dt(document.get("period_start")), _dt(document.get("period_end"))
    if start is None or end is None:
        return c.MISC["that_period"]
    if start.day == 1 and start.hour == 0 and end.day == 1 and end.hour == 0 and \
            (end.year * 12 + end.month) - (start.year * 12 + start.month) == 1:
        return start.strftime("%B %Y")
    return c.MISC["period_range"].format(start=c.day_text(start), end=c.day_text(end))


def _improvements(p: Projection) -> list:
    items = []
    for candidate in p.facts["improvements"]["candidates"]:
        state = c.CANDIDATE_STATE.get(candidate["state"], candidate["state"].lower())
        items.append(_story_item(f"candidate:{candidate['candidate_id']}", candidate["created_at"], "improvement",
                                 "engineer", "neutral", c.says(c.STORY["improvement"].format(state="is " + state)),
                                 link=f"/changes/{candidate['candidate_id']}", technical=candidate["state"]))
    for event in p.facts["improvements"]["events"]:
        words = c.VERSION_EVENT.get(event["kind"])
        if words is None:
            continue
        consequence = c.says(c.STORY["improvement_trial"]) if event["kind"] == "activate" else None
        items.append(_story_item(f"version:{event['event_id']}", event["created_at"], "improvement", "leader",
                                 "neutral", c.says(c.STORY["improvement"].format(state=words)),
                                 consequence=consequence, link="/changes", technical=event["kind"]))
    return items


def _account_opened(p: Projection) -> list:
    native = [item for item in p.cache.get("opening_native") or []
              if c.decimal_or_none(item.get("amount")) is not None and isinstance(item.get("asset"), str)]
    lead = c.STORY["account_opened" if p.paper else "account_opened_live"]
    if native:
        parts: list = [lead]
        for index, item in enumerate(native):
            if index:
                parts.append(c.MISC["and"])
            parts.append(c.money_segment(Decimal(item["amount"]), item["asset"]))
        headline = c.says(*parts, ".")
    else:
        headline = c.says(c.TRADE["account_opened_plain" if p.paper else "account_opened_live_plain"])
    return [_story_item("account:opened", p.started_at, "account_opened", "hq", "neutral", headline)]
