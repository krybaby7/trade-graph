"""HQ's Trading desk room (W13-A, C13; D280, D302): what the desk believes, what it is testing and what each strategy
earns in each market type, as ``trade-graph.hq.desk.v1`` (``api/hq_desk_contract.py``) from ``GET /api/v1/hq/desk``.

``desk(runtime)`` reads, inside one read snapshot of the state database, from saved records only, through the reads
the records' own packages give HQ (all work on a read-only connection and answer "nothing" on an older database):

- the playbook in force and its revisions (``trading_desk.store.read_playbook``, ``read_playbook_revisions``: W12-A,
  state migration 0052, hash-checked);
- today's newest stance per coin (``read_latest_stances``) with the label software showed the desk then, and the
  coin's label now (``market_types.store.read_latest_market_types``: W11-A, 0050; current for 2 hours, D287);
- the strategy lab as the desk sees it (``strategy_lab.context.lab_context``: W11-B, 0051), with each shown report's
  result after fees against holding the coin from the practice range's stored report;
- the news the desk read at its newest turn (``trading_desk.news.news_context`` at that moment, for its coins);
- the scorecard's split (D300) from HQ's 60-second trading cache (``hq_trading.facts``), so it is the same scorecard
  the Money tab shows and is never computed twice a minute.

Reading starts nothing: no engine, scheduler, worker, lab step, provider or broker is imported or called, and the
snapshot ends in a rollback (tested: the database is byte-for-byte the same). A part whose records a database does not
have yet says so (``not_available``); a part that can't be read (an edited, hash-checked record) says so
(``unreadable``) while the other parts still show. Every word comes from ``hq_copy.DESK``; the desk's and the Leader's
words appear only as labelled quotes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Request

from trade_graph.api import hq_copy as c
from trade_graph.api import hq_trading
from trade_graph.api.hq_contract import iso_ms
from trade_graph.api.hq_desk_contract import (
    FAMILIES_MAX,
    HEADLINES_MAX,
    IDEAS_MAX,
    INBOX_MAX,
    LAB_NEXT_MAX,
    LAB_REPORTS_MAX,
    LAB_RESULTS_MAX,
    MARKET_TYPES,
    SCHEMA,
    SPLIT_ROWS_MAX,
    STANCES_MAX,
    VERDICTS_MAX,
)
from trade_graph.api.hq_plans import trading_day_of
from trade_graph.api.security import redact
from trade_graph.application.market_types.store import DEFAULT_LABEL_AGE, read_latest_market_types
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.strategy_lab.context import lab_context
from trade_graph.application.trading_desk.news import news_context
from trade_graph.application.trading_desk.store import read_latest_stances, read_playbook, read_playbook_revisions
from trade_graph.contracts.trading_desk import PlaybookRecord, StanceRecord
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.domain.errors import TradeGraphError
from trade_graph.kernel.money_limits import TradingDay

K = c.DESK
P, S, L, N, Q = K["playbook"], K["stance"], K["lab"], K["news"], K["split"]
FAILURES = (sqlite3.DatabaseError, TradeGraphError, ValueError, TypeError, KeyError, LookupError, ArithmeticError,
            AttributeError)
NEWS_BUDGET = 64 * 1024  # news_context's own size bound; the room bounds its lists itself
NEWS_COIN_HOURS = 24  # the coins the desk gave a stance on in the day before its turn are "its coins"
HEADLINE_COINS = 4  # coins named in the stances' headline; the rest are counted


# --- small helpers ------------------------------------------------------------------------------------------------


def _forms(forms: Sequence[str], count: int, **values: Any) -> str:
    return (forms[0] if count == 1 else forms[1]).format(n=count, **values)


def _decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str | int | Decimal) or isinstance(value, bool):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_utc(value)
    except (ValueError, OverflowError):
        return None


def _day(value: Any) -> str:
    """A whole UTC day ("2026-09-08") as "8 Sep"; anything else as given, shortened."""
    try:
        moment = date.fromisoformat(str(value))
    except ValueError:
        return c.cut(str(value), 24)
    return f"{moment.day} {moment.strftime('%b')}"


def _window(window: Any) -> str:
    if not isinstance(window, Mapping):
        return "?"
    return L["window"].format(start=_day(window.get("start")), end=_day(window.get("end")))


def _tone(amount: Decimal) -> str:
    return "good" if amount > 0 else "attention" if amount < 0 else "neutral"


def _sentence(value: str) -> str:
    value = " ".join(str(value).split()).rstrip(".")
    return (value[:1].upper() + value[1:] + ".") if value else value


def _tables(connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


# --- the playbook --------------------------------------------------------------------------------------------------


def _playbook_empty(state: str, words: str) -> dict:
    return {"state": state, "headline": c.says(words), "revision": None, "set_at": None, "revisions_total": 0,
            "why": None, "choices": [], "previous": None}


def _choice(market_type: str, record: PlaybookRecord) -> dict:
    word = P["type"][market_type]
    choice = record.choice_for(market_type)
    if choice is None:
        return {"market_type": market_type, "stance": None, "strategy_refs": [],
                "text": c.says(P["no_choice"].format(type=word)), "reason": None, "reports": 0}
    refs = list(choice.strategy_refs)
    tail = "" if choice.stance == "wait" else P["with"].format(refs=" or ".join(refs)) if refs else P["no_version"]
    return {"market_type": market_type, "stance": choice.stance, "strategy_refs": refs,
            "text": c.says(P["choice"][choice.stance].format(type=word, refs=tail)), "reason": c.quote(choice.reason),
            "reports": len(choice.report_ids)}


def playbook_view(record: PlaybookRecord | None, revisions: Sequence[PlaybookRecord]) -> dict:
    """The playbook in force (``record``) with the revisions made so far (``revisions``, in order)."""
    if record is None:
        return _playbook_empty("empty", P["empty"])
    total = max(len(revisions), record.revision)
    first, one, many = P["headline"]
    headline = c.says(c.term("playbook", "Playbook"), first.format(revision=record.revision),
                      c.time(record.created_at, "relative_clock"), (one if total == 1 else many).format(n=total))
    previous = next((item for item in revisions if item.revision_id == record.previous_revision_id), None)
    before, after = P["previous"]
    return {"state": "shown", "headline": headline, "revision": record.revision,
            "set_at": iso_ms(record.created_at), "revisions_total": total, "why": c.quote(record.why),
            "choices": [_choice(market_type, record) for market_type in MARKET_TYPES],
            "previous": None if previous is None else c.says(before.format(revision=previous.revision),
                                                             c.time(previous.created_at, "day_clock"), after)}


def _read_playbook(connection, portfolio_id: str, now: datetime, tables: set[str]) -> dict:
    if "desk_playbook_revisions" not in tables:
        return _playbook_empty("not_available", P["not_available"])
    record = read_playbook(connection, portfolio_id, as_of=now)
    revisions = read_playbook_revisions(connection, portfolio_id, until=now) if record is not None else []
    return playbook_view(record, revisions)


# --- today's stances -----------------------------------------------------------------------------------------------


def _stances_empty(state: str, words: str, day: TradingDay | None) -> dict:
    return {"state": state, "headline": c.says(words), "day_start": None if day is None else iso_ms(day.start),
            "day_end": None if day is None else iso_ms(day.end), "items": [], "more_stances": 0}


def _type_word(market_type: str | None) -> str:
    return P["type"].get(market_type or "", S["unknown_type"])


def _shown(record: StanceRecord) -> dict:
    status = record.label_status
    market_type = record.label_market_type if status == "current" else None
    return {"status": status, "market_type": market_type,
            "text": c.says(S["shown"][status].format(type=_type_word(market_type)))}


def _now(label, now: datetime) -> dict:
    if label is None:
        return {"status": "missing", "market_type": None, "text": c.says(S["now"]["missing"])}
    seconds = (now - label.as_of).total_seconds()
    if label.is_fresh(now, DEFAULT_LABEL_AGE) and label.market_type in MARKET_TYPES:
        age = S["now"]["just_now"] if seconds < 60 else c.ago(seconds)
        return {"status": "current", "market_type": label.market_type,
                "text": c.says(S["now"]["current"].format(type=_type_word(label.market_type), age=age))}
    return {"status": "stale", "market_type": None, "text": c.says(S["now"]["stale"].format(age=c.span(seconds)))}


def _stance_text(record: StanceRecord) -> list[dict]:
    if record.stance == "wait":
        refs = ""
    else:
        refs = S["with"].format(ref=record.strategy_ref) if record.strategy_ref else S["no_version"]
    return c.says(S["text"][record.stance].format(refs=refs))


def stances_view(latest: Mapping[str, StanceRecord], labels_now: Mapping[str, Any], day: TradingDay,
                 now: datetime) -> dict:
    """Today's newest stance per coin (``latest``), newest first, with each coin's label now (``labels_now``)."""
    if not latest:
        return _stances_empty("empty", S["empty"], day)
    ordered = sorted(latest.values(), key=lambda item: (-item.created_at.timestamp(), item.symbol))
    items = [{"symbol": record.symbol, "stance": record.stance, "at": iso_ms(record.created_at),
              "text": _stance_text(record), "shown": _shown(record), "now": _now(labels_now.get(record.symbol), now),
              "reason": c.quote(record.reason) or {"label": "In their words", "text": "…"},
              "news": len(record.news_refs)} for record in ordered[:STANCES_MAX]]
    named = [f"{record.symbol} {S['verb'][record.stance]} ({_shown(record)['market_type'] or S['unknown_type']})"
             for record in ordered[:HEADLINE_COINS]]
    lead, one, many = S["headline"]
    words = (one if len(ordered) == 1 else many).format(n=len(ordered)) + ", ".join(named)
    if len(ordered) > HEADLINE_COINS:
        words += S["more"].format(n=len(ordered) - HEADLINE_COINS)
    headline = c.says(lead, c.term("stance", "stance"), words + ".")
    return {"state": "shown", "headline": headline, "day_start": iso_ms(day.start),
            "day_end": iso_ms(day.end), "items": items, "more_stances": max(0, len(ordered) - STANCES_MAX)}


def _read_stances(runtime, connection, portfolio_id: str, now: datetime, tables: set[str]) -> dict:
    day = trading_day_of(runtime.database, now)
    if "desk_stances" not in tables:
        return _stances_empty("not_available", S["not_available"], day)
    latest = read_latest_stances(connection, portfolio_id, as_of=now, since=day.start)
    labels = read_latest_market_types(connection, symbols=list(latest), as_of=now) if latest else {}
    return stances_view(latest, labels, day, now)


# --- the lab ------------------------------------------------------------------------------------------------------


def _lab_empty(state: str, words: str) -> dict:
    return {"state": state, "headline": c.says(words), "queued": 0, "running": 0, "next": [], "results": [],
            "more_results": 0, "families": [], "more_families": 0}


def _what(kind: str, brief: Mapping[str, Any]) -> str:
    words = L["kind"].get(kind, "a request")
    subject = next((brief.get(key) for key in ("strategy_ref", "study_id", "strategy_id")
                    if isinstance(brief.get(key), str)), None)
    if subject:
        words += L["of"].format(subject=c.cut(subject, 80))
    if isinstance(brief.get("symbol"), str):
        words += L["on"].format(symbol=c.cut(brief["symbol"], 24))
    return words


def _report(item: Mapping[str, Any], metrics: Mapping[str, Mapping[str, Any]]) -> dict | None:
    run_id = item.get("run_id")
    found = metrics.get(run_id) if isinstance(run_id, str) else None
    if found is None:
        return None  # a report that can't be read is counted, never invented
    figures, symbol = found["metrics"], found["symbol"]
    currency, result = figures["currency"], Decimal(figures["result"])
    trades = int(figures["trades"])
    profile = str(found.get("profile") or item.get("cost_profile_ref") or "?").split("@", 1)[0]
    words = c.says(L["report"].format(profile=profile, trades=_forms(L["trades"], trades)),
                   c.money_segment(result, currency))
    hold = (figures.get("benchmarks") or {}).get("hold_coin")
    held, versus = (None, None) if not isinstance(hold, Mapping) else (_decimal(hold.get("result")),
                                                                         _decimal(hold.get("versus")))
    if held is None or versus is None:
        words = c.says(*words, L["no_hold"].format(symbol=symbol))
        versus = None
    else:
        words = c.says(*words, L["hold"].format(symbol=hold.get("symbol") or symbol), c.money_segment(held, currency))
        words = (c.says(*words, L["versus_same"]) if versus == 0 else
                 c.says(*words, L["versus"], c.money_segment(abs(versus), currency),
                        L["versus_tail"][0 if versus > 0 else 1]))
    if not c.plain(words).endswith("."):
        words = c.says(*words, ".")
    return {"run_id": run_id, "text": words, "trades": trades, "result": c.money(result, currency),
            "versus_hold": c.money(versus, currency), "tone": _tone(result)}


def _result(item: Mapping[str, Any], metrics: Mapping[str, Mapping[str, Any]]) -> dict:
    kind, status = str(item.get("kind")), str(item.get("status"))
    brief = item.get("brief") if isinstance(item.get("brief"), Mapping) else {}
    subject = next((brief.get(key) for key in ("strategy_ref", "study_id", "strategy_id")
                    if isinstance(brief.get(key), str)), "?")
    reports, more = [], 0
    reason = None
    if status == "done":
        outcome = item.get("outcome") if isinstance(item.get("outcome"), Mapping) else {}
        done = L["done"]
        if kind == "register_strategy":
            ref = outcome.get("strategy_ref") or subject
            text = done["register_again" if outcome.get("created") is False else "register_strategy"].format(
                subject=ref)
        elif kind == "create_study":
            text = done["create_study"].format(subject=outcome.get("study_id") or subject,
                                               tune=_window(outcome.get("tune")),
                                               confirm=_window(outcome.get("confirm")))
        elif kind in ("tune", "confirm"):
            listed = [entry for entry in item.get("reports") or [] if isinstance(entry, Mapping)]
            views = [view for view in (_report(entry, metrics) for entry in listed) if view is not None]
            reports, more = views[:LAB_REPORTS_MAX], len(listed) - len(views[:LAB_REPORTS_MAX])
            text = done[kind].format(subject=subject, symbol=brief.get("symbol") or "?",
                                     window=_window(item.get("window")), reports=_forms(L["reports"], len(listed)))
        else:
            text = done["other"].format(what=_what(kind, brief))
    else:
        status = status if status in ("refused", "failed") else "failed"
        text = L[status].format(what=_what(kind, brief))
        reason = c.says(_sentence(c.cut(str(item.get("reason") or "no reason was recorded"), 300)))
    at = _moment(item.get("finished_at"))
    return {"request_id": str(item.get("request_id")), "at": None if at is None else iso_ms(at), "status": status,
            "tone": {"done": "good", "refused": "neutral"}.get(status, "attention"), "text": c.says(text),
            "reason": reason, "reports": reports, "more_reports": max(0, more)}


def _family(item: Mapping[str, Any]) -> dict:
    runs = item.get("runs") if isinstance(item.get("runs"), Mapping) else {}
    versions, studies = int(item.get("versions") or 0), int(item.get("studies") or 0)
    tunes, confirms = int(runs.get("tune") or 0), int(runs.get("confirm") or 0)
    confirmed = [str(ref) for ref in item.get("confirmed_versions") or []][:5]
    text = L["family"].format(strategy=item["strategy_id"], versions=_forms(L["versions"], versions),
                              studies=_forms(L["studies"], studies), tunes=_forms(L["tunes"], tunes),
                              confirms=_forms(L["confirms"], confirms))
    text += L["family_confirmed"].format(refs=", ".join(confirmed)) if confirmed else L["family_none"]
    return {"strategy_id": str(item["strategy_id"]), "text": c.says(text), "versions": versions, "studies": studies,
            "tune_runs": tunes, "confirm_runs": confirms, "confirmed": confirmed}


def lab_view(lab: Mapping[str, Any], metrics: Mapping[str, Mapping[str, Any]]) -> dict:
    """The lab as ``lab_context`` gives it to the desk, with the shown reports' figures (``metrics`` by run id:
    ``{"metrics": <the report's metrics>, "symbol": ..., "profile": ...}``)."""
    if not lab.get("available"):
        return _lab_empty("not_available", L["not_available"])
    queue = lab.get("queue") or {}
    queued, running = int(queue.get("queued") or 0), int(queue.get("running") or 0)
    waiting = [item for item in queue.get("next") or [] if isinstance(item, Mapping)][:LAB_NEXT_MAX]
    upcoming = [c.says((L["running"] if item.get("status") == "running" else L["queued"]).format(
        what=_what(str((item.get("brief") or {}).get("kind")), item.get("brief") or {}))) for item in waiting]
    ended = [item for item in lab.get("results") or [] if isinstance(item, Mapping)]
    total = sum(int(value) for value in (lab.get("results_total") or {}).values())
    families = [item for item in lab.get("families") or [] if isinstance(item, Mapping) and item.get("strategy_id")]
    if not (queued or running or total or families):
        return _lab_empty("empty", L["empty"])
    shown = [_result(item, metrics) for item in ended[:LAB_RESULTS_MAX]]
    before, after = L["headline"]
    headline = c.says(before, c.term("strategy_lab", "strategy lab"),
                      after.format(queued=queued, running=running, results=total))
    return {"state": "shown", "headline": headline,
            "queued": queued, "running": running, "next": upcoming[:max(0, min(LAB_NEXT_MAX, queued + running))],
            "results": shown, "more_results": max(0, total - len(shown)),
            "families": [_family(item) for item in families[:FAMILIES_MAX]],
            "more_families": max(0, len(families) - FAMILIES_MAX) + int(lab.get("families_omitted") or 0)}


def _read_lab(runtime, portfolio_id: str, tables: set[str]) -> dict:
    if "strategy_lab_requests" not in tables:
        return _lab_empty("not_available", L["not_available"])
    lab = lab_context(runtime.database, runtime.clock, portfolio_id)
    store = PracticeRangeStore(runtime.database, runtime.clock)
    metrics: dict[str, dict[str, Any]] = {}
    for item in (lab.get("results") or [])[:LAB_RESULTS_MAX]:
        for entry in (item.get("reports") or [])[:LAB_REPORTS_MAX]:
            run_id = entry.get("run_id") if isinstance(entry, Mapping) else None
            if not isinstance(run_id, str) or entry.get("missing"):
                continue
            try:
                record = store.run(run_id)
                metrics[run_id] = {"metrics": record.report["result"]["metrics"], "symbol": record.symbol,
                                   "profile": record.cost_profile_ref}
            except FAILURES:
                continue  # counted as a report not shown
    return lab_view(lab, metrics)


# --- the news the desk read ----------------------------------------------------------------------------------------


def _news_empty(state: str, words: str, read_at: datetime | None = None) -> dict:
    return {"state": state, "headline": c.says(words), "read_at": None if read_at is None else iso_ms(read_at),
            "round": None, "inbox": [], "more_inbox": 0, "jev": [], "verdicts": [], "more_verdicts": 0}


def _source(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        host = value.split("/", 3)[2]
        return c.cut(host.removeprefix("www."), 120)
    return None


def _round(value: Mapping[str, Any] | None) -> dict | None:
    at = None if value is None else _moment(value.get("finished_at"))
    if value is None or at is None:
        return None
    headlines = [item for item in value.get("headlines") or [] if isinstance(item, Mapping)
                 and c.quote(item.get("takeaway")) is not None]
    ideas = [c.quote(item.get("idea")) for item in value.get("strategy_ideas_for_desk") or []
             if isinstance(item, Mapping) and c.quote(item.get("idea")) is not None]
    note = value.get("desk_note") if isinstance(value.get("desk_note"), Mapping) else None
    return {"at": iso_ms(at), "summary": c.quote(value.get("summary")),
            "headlines": [{"text": c.quote(item.get("takeaway")), "source": _source(item.get("source_ref"))}
                          for item in headlines[:HEADLINES_MAX]],
            "more_headlines": max(0, len(headlines) - HEADLINES_MAX),
            "desk_note": None if note is None else c.quote(note.get("note")), "ideas": ideas[:IDEAS_MAX],
            "more_ideas": max(0, len(ideas) - IDEAS_MAX)}


def _idea(item: Mapping[str, Any]) -> dict | None:
    at, idea = _moment(item.get("received_at")), c.quote(item.get("idea"))
    if at is None or idea is None:
        return None
    cited = len(item.get("lab_requests") or [])
    lead = N["inbox"] if item.get("sent_to") == "trader" else N["inbox_research"]
    tail = _forms(N["inbox_cited"], cited) if cited else N["inbox_untested"]
    return {"at": iso_ms(at), "idea": idea, "text": c.says(lead, c.time(at, "day_clock"), tail)}


def _verdict(item: Mapping[str, Any], mode: str) -> dict | None:
    at, title = _moment(item.get("checked_at")), c.quote(item.get("title"))
    if at is None or title is None:
        return None
    subjects = ", ".join(str(code) for code in item.get("subjects") or []) or "Kraken"
    words = N["verdict"].format(publisher=c.cut(str(item.get("publisher") or "a feed"), 60), subjects=subjects)
    if item.get("would_wake"):
        who = ", ".join(sorted({c.dept_name(str(route.get("department"))) for route in item.get("routes") or []
                                if isinstance(route, Mapping)})) or "someone"
        words += (N["would_wake_shadow"] if mode == "shadow" else N["would_wake"]).format(who=who)
    else:
        words += N["no_wake"]
    return {"at": iso_ms(at), "title": title, "text": c.says(words + ".")}


def news_view(news: Mapping[str, Any], read_at: datetime, *, turn: bool) -> dict:
    """The desk's ``news`` key (``news_context``) at ``read_at``: its newest turn (``turn``) or now."""
    jev = news.get("jev") if isinstance(news.get("jev"), Mapping) else {}
    mode = str(jev.get("mode") or "shadow")
    verdict_rows = [item for item in jev.get("verdicts") or [] if isinstance(item, Mapping)]
    verdicts = [view for view in (_verdict(item, mode) for item in verdict_rows) if view is not None]
    total = int(jev.get("verdicts_total") or len(verdict_rows)) + int(jev.get("verdicts_omitted") or 0)
    if not news.get("available") and not verdicts:
        return _news_empty("not_available", N["not_available"])
    round_ = _round(news.get("leader_round") if isinstance(news.get("leader_round"), Mapping) else None)
    inbox_rows = [item for item in news.get("lab_inbox") or [] if isinstance(item, Mapping)]
    inbox = [view for view in (_idea(item) for item in inbox_rows) if view is not None]
    wake = int(jev.get("would_wake_total") or 0)
    counts = N["jev_counts"][0 if total == 1 else 1 if total else 2].format(n=total, wake=wake)
    jev_words = c.says(N["jev"].get(mode, N["jev"]["other"]).format(mode=c.cut(mode, 24)) + counts)
    if round_ is None and not inbox and not verdicts:
        view = _news_empty("empty", N["empty"], read_at)
        view["jev"] = jev_words
        return view
    lead = c.says(N["headline_turn"], c.time(read_at, "relative_clock")) if turn else c.says(N["headline_next"])
    lead = (c.says(*lead, N["round"], c.time(round_["at"], "relative_clock")) if round_ is not None
            else c.says(*lead, N["no_round"]))
    shown_total = len(verdict_rows)
    lead = c.says(*lead, N["verdicts"][0 if shown_total == 1 else 1 if shown_total else 2].format(n=shown_total))
    return {"state": "shown", "headline": lead, "read_at": iso_ms(read_at), "round": round_,
            "inbox": inbox[:INBOX_MAX],
            "more_inbox": max(0, len(inbox) - INBOX_MAX) + int(news.get("lab_inbox_omitted") or 0),
            "jev": jev_words, "verdicts": verdicts[:VERDICTS_MAX],
            "more_verdicts": max(0, total - len(verdicts[:VERDICTS_MAX]))}


def _desk_turn(connection, portfolio_id: str, now: datetime, tables: set[str]) -> datetime | None:
    """When the desk's newest turn was created (a Trader task), or None when it has had none."""
    if "tasks" not in tables:
        return None
    row = connection.execute("SELECT MAX(created_at) FROM tasks WHERE role = 'trader' AND portfolio_id = ? "
                             "AND created_at <= ?", (portfolio_id, utc_iso(now))).fetchone()
    return _moment(row[0]) if row is not None else None


def _read_news(connection, portfolio_id: str, now: datetime, tables: set[str]) -> dict:
    turn = _desk_turn(connection, portfolio_id, now, tables)
    at = turn or now
    coins = set()
    if "desk_stances" in tables:
        coins |= set(read_latest_stances(connection, portfolio_id, as_of=at,
                                         since=at - timedelta(hours=NEWS_COIN_HOURS)))
    if "day_plans" in tables:
        coins |= {row[0] for row in connection.execute(
            "SELECT DISTINCT symbol FROM day_plans WHERE portfolio_id = ? AND installed_at <= ? "
            "AND trading_day_end > ?", (portfolio_id, utc_iso(at), utc_iso(at)))}
    news = news_context(connection, portfolio_id, coins=sorted(coins), at=at, budget=NEWS_BUDGET)
    return news_view(news, at, turn=turn is not None)


# --- what each strategy earned in each market type ----------------------------------------------------------------


def _split_empty(state: str, headline: list[dict], *, start: str | None = None, currency: str | None = None) -> dict:
    return {"state": state, "headline": headline, "period_start": None if start is None else iso_ms(start),
            "currency": currency, "result": None, "by_choice": [], "more_choices": 0, "by_strategy": [],
            "more_strategies": 0, "by_market_type": [], "by_stance": [], "note": c.says(Q["note"])}


def _counts(row: Mapping[str, Any]) -> str:
    trades, held = int(row["trades"]), int(row["open_positions"])
    words = Q["counts"].format(trades=_forms(Q["trades"], trades), won=row["wins"], lost=row["losses"]) if trades \
        else Q["no_trades"]
    return words + (_forms(Q["held"], held) if held else "")


def _row(key: str, name: str, row: Mapping[str, Any], currency: str) -> dict:
    result = Decimal(row["result_after_fees"])
    return {"key": key, "text": c.says(Q["row"].format(name=name, counts=_counts(row)),
                                       c.money_segment(result, currency), "."),
            "trades": int(row["trades"]), "open_positions": int(row["open_positions"]),
            "result": c.money(result, currency), "tone": _tone(result)}


def _used(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if isinstance(row, Mapping) and (row.get("trades") or row.get("open_positions"))]


def _strategy_name(ref: str) -> str:
    return Q["strategy"].get(ref, ref)


def _choice_name(row: Mapping[str, Any]) -> str:
    strategy, market_type, stance = row["strategy_ref"], row["market_type"], row["stance"]
    if strategy == "carried_in":
        return Q["strategy"]["carried_in"]
    return Q["choice"].format(strategy=_strategy_name(strategy), market=Q["choice_market"].get(market_type,
                                                                                               market_type),
                              stance=Q["choice_stance"].get(stance, stance))


def split_view(scorecard: Mapping[str, Any] | None, *, paper: bool) -> dict:
    """The scorecard's split (D300) as HQ's trading cache holds the scorecard (``hq_trading.facts``)."""
    if not paper:
        return _split_empty("not_available", c.says(Q["not_applied"]))
    document = (scorecard or {}).get("document")
    if document is None:
        reason = (scorecard or {}).get("unavailable")
        if reason:
            return _split_empty("empty", c.says(Q["unavailable"].format(reason=_sentence(reason))))
        return _split_empty("unreadable", c.says(Q["unreadable"].format(reason="the scorecard can't be read.")))
    currency, start = document["currency"], document["period"]["start"]
    split = document.get("split") or {}
    if not split.get("available"):
        return _split_empty("unreadable", c.says(Q["unreadable"].format(
            reason=_sentence(split.get("reason") or "it isn't in this scorecard"))), start=start, currency=currency)
    totals = split["totals"]
    trades, held = int(totals["trades"]), int(totals["open_positions"])
    if not trades and not held:
        before, after = Q["empty"]
        return _split_empty("empty", c.says(before, c.time(start, "date"), after), start=start, currency=currency)
    result = Decimal(totals["result_after_fees"])
    choices = _used(split["by_choice"])
    strategies = _used(split["by_strategy"])
    first, middle, split_by, last = Q["headline"]
    headline = c.says(first, c.time(start, "date"), middle.format(
        trades=_forms(Q["trades"], trades), held=_forms(Q["held"], held) if held else ""),
        c.money_segment(result, currency), split_by, c.term("market_type", "market type"), last)
    return {
        "state": "shown", "headline": headline, "period_start": iso_ms(start), "currency": currency,
        "result": c.money(result, currency),
        "by_choice": [_row("|".join((row["strategy_ref"], row["market_type"], row["stance"])), _choice_name(row), row,
                           currency) for row in choices[:SPLIT_ROWS_MAX]],
        "more_choices": max(0, len(choices) - SPLIT_ROWS_MAX),
        "by_strategy": [_row(row["strategy_ref"], _strategy_name(row["strategy_ref"]), row, currency)
                        for row in strategies[:SPLIT_ROWS_MAX]],
        "more_strategies": max(0, len(strategies) - SPLIT_ROWS_MAX),
        "by_market_type": [_row(row["market_type"], Q["market"].get(row["market_type"], row["market_type"]), row,
                                currency) for row in _used(split["by_market_type"])][:SPLIT_ROWS_MAX],
        "by_stance": [_row(row["stance"], Q["stance"].get(row["stance"], row["stance"]), row, currency)
                      for row in _used(split["by_stance"])][:SPLIT_ROWS_MAX],
        "note": c.says(Q["note"])}


def _read_split(runtime, now: datetime) -> dict:
    facts = hq_trading.facts(runtime, now)
    return split_view(facts.get("scorecard"), paper=bool(facts.get("paper")))


# --- the document --------------------------------------------------------------------------------------------------


def _headline(playbook: dict, stances: dict, lab: dict) -> list[dict]:
    parts = [P["short"].format(revision=playbook["revision"]) if playbook["state"] == "shown" else P["short_none"],
             _forms(S["short"], len(stances["items"]) + stances["more_stances"]) if stances["state"] == "shown"
             else S["short_none"]]
    if lab["state"] == "shown":
        parts.append(L["short"].format(queued=lab["queued"], running=lab["running"]))
    return c.says(" ".join(parts))


def document(*, now: datetime, playbook: dict, stances: dict, lab: dict, news: dict, split: dict) -> dict:
    return {"schema": SCHEMA, "as_of": iso_ms(now), "headline": _headline(playbook, stances, lab),
            "playbook": playbook, "stances": stances, "lab": lab, "news": news, "split": split}


def _part(build, unreadable) -> dict:
    """One part of the room; a part whose records can't be read says so and never stops the others."""
    try:
        return build()
    except FAILURES as error:
        return unreadable(type(error).__name__)


def desk(runtime) -> dict:
    """The Trading desk room (reads only)."""
    with runtime.database.snapshot() as connection:
        now = runtime.clock.now()
        pid = runtime.portfolio_id
        tables = _tables(connection)
        day = None
        try:
            day = trading_day_of(runtime.database, now)
        except FAILURES:
            day = None
        playbook = _part(lambda: _read_playbook(connection, pid, now, tables),
                         lambda error: _playbook_empty("unreadable", P["unreadable"].format(error=error)))
        stances = _part(lambda: _read_stances(runtime, connection, pid, now, tables),
                        lambda error: _stances_empty("unreadable", S["unreadable"].format(error=error), day))
        lab = _part(lambda: _read_lab(runtime, pid, tables),
                    lambda error: _lab_empty("unreadable", L["unreadable"].format(error=error)))
        news = _part(lambda: _read_news(connection, pid, now, tables),
                     lambda error: _news_empty("unreadable", N["unreadable"].format(error=error)))
        split = _part(lambda: _read_split(runtime, now),
                      lambda error: _split_empty("unreadable", c.says(Q["unreadable"].format(
                          reason=f"the scorecard can't be read ({error})."))))
        return document(now=now, playbook=playbook, stances=stances, lab=lab, news=news, split=split)


def register(app, runtime, identity) -> None:
    """Add ``GET /api/v1/hq/desk`` (any signed-in role; reads only)."""

    @app.get("/api/v1/hq/desk")
    def hq_desk(request: Request):
        identity(request)
        return redact(desk(runtime))


__all__ = ["desk", "document", "lab_view", "news_view", "playbook_view", "register", "split_view", "stances_view"]
