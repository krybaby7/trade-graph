"""The Trading desk's ``lab`` context key (W11-B, C11; D290-D292): its strategy lab in plain words and numbers.

A wake turn of the desk (the Trader) is handed, under ``lab``:

- ``results``: its newest ended lab requests, newest first: what each asked (``brief``), how it ended (``done``,
  ``refused`` with the reason, ``failed``), the seconds spent and, for a replay, every report it wrote, one per cost
  profile, as ``summary.report_overview`` (the numbers) and ``summary.report_lines`` (the plain sentences). Both come
  from the practice range's own report functions, so whatever a report says (W11-A adds the split of its trades by
  market type there) reaches the desk here unchanged;
- ``queue``: how many requests are queued and running, and the next few of its own;
- ``families``: per strategy family, how many versions, studies and runs were tried, which versions have a
  confirmation report, its newest studies and the newest untouched confirmation window (``confirmation_offer``,
  walk-forward: new days from the price collector become new confirmation days);
- the account's own cost profile (always replayed), the instruction and counts of everything not shown.

The key stays inside ``LAB_CONTEXT_BYTES`` of the 128 KiB request bound: the newest results are shown in full, older
ones with their numbers only, the rest are counted (``results_omitted``); every request stays in the lab's records
(D116: nothing is dropped, a turn is only shown less). Reading starts nothing.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any

from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.strategy_lab.queue import LabQueue, LabRequestRecord
from trade_graph.contracts.strategy_lab import LAB_CONTRACT_VERSION
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import TradeGraphError
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING

LAB_CONTEXT_BYTES = 24 * 1024  # the lab's share of one turn's 128 KiB request; a bound on what is shown, not a cap
RESULTS_READ = 40
WAITING_SHOWN = 10
FAMILIES_SHOWN = 12
VERSIONS_SHOWN = 3
CONFIRMED_SHOWN = 5
LAB_INSTRUCTION = (
    "lab is your strategy lab inside this installation: it replays strategy versions on the stored candles with real "
    "fee tables and records practice-range reports. Ask it with lab_requests, as many as you like: register_strategy "
    "(strategy_json: a PracticeStrategy document as JSON text; data, never code), create_study (study_id, strategy_id "
    "and whole UTC days: tune_start/tune_end, then confirm_start/confirm_end no earlier than the tuning end), tune "
    "(study_id, strategy_ref, symbol, optional source, a window_start/window_end inside the tuning window, "
    "cost_profiles) and confirm (the whole confirmation window, once: the first version confirmed there spends it). "
    "Requests run in order after your answer and you are woken with lab_results when they are done; a refused "
    "request says why here. Software keeps the test fair: tuning never touches confirmation days, a confirmation "
    "needs untouched later days (families[].confirmation_offer names the newest) and every try is listed in the "
    "confirmation report. The account's own cost profile is always replayed. A day plan may follow a version once it "
    "has a confirmation report; a report is evidence you weigh, not a pass mark, and not permission for real money.")


def _size(document: Any) -> int:
    return len(json.dumps(document, separators=(",", ":"), sort_keys=True).encode())


def _report(store: PracticeRangeStore, run_id: str, *, lines: bool) -> dict[str, Any]:
    from trade_graph.application.practice_range.summary import report_lines, report_overview

    try:
        record = store.run(run_id)
    except TradeGraphError:
        return {"run_id": run_id, "missing": True}
    item: dict[str, Any] = {"run_id": run_id, "cost_profile_ref": record.cost_profile_ref}
    try:
        item["overview"] = report_overview(record)
        if lines:
            item["lines"] = report_lines(record)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        item["lines"] = [f"Practice-range report {record.run_id} on {record.symbol} ({record.phase})."]
    return item


def _result(store: PracticeRangeStore, record: LabRequestRecord, *, lines: bool) -> dict[str, Any]:
    item: dict[str, Any] = {"request_id": record.request_id, "kind": record.kind, "status": record.status,
                            "brief": record.request.brief(), "finished_at": record.finished_at,
                            "seconds": record.seconds, "attempts": record.attempts}
    if record.reason is not None:
        item["reason"] = record.reason
    result = record.result or {}
    if record.kind in ("register_strategy", "create_study"):
        item["outcome"] = {key: result[key] for key in ("strategy_ref", "created", "study_id", "tune", "confirm",
                                                        "replayed_days", "meets_three_month_minimum")
                           if key in result}
    elif result.get("runs"):
        item["window"] = result.get("window")
        item["reports"] = [_report(store, run["run_id"], lines=lines) for run in result["runs"]]
    return item


def _results(store: PracticeRangeStore, queue: LabQueue, portfolio_id: str,
             budget: int) -> tuple[list[dict[str, Any]], int]:
    shown: list[dict[str, Any]] = []
    used = 0
    ended = queue.ended(portfolio_id, limit=RESULTS_READ)
    total = sum(queue.ended_count(portfolio_id).values())
    for position, record in enumerate(ended):
        item = _result(store, record, lines=True)
        size = _size(item)
        if used + size > budget:
            item = _result(store, record, lines=False)  # older results: the numbers without the sentences
            size = _size(item)
            if used + size > budget:
                return shown, total - position
        shown.append(item)
        used += size
    return shown, total - len(shown)


def _families(database, store: PracticeRangeStore, clock: Clock) -> tuple[list[dict[str, Any]], int]:
    rows = database.execute(
        "SELECT strategy_id, COUNT(*) AS versions, MAX(sequence) AS newest FROM practice_strategy_versions "
        "GROUP BY strategy_id ORDER BY newest DESC").fetchall()
    today = clock.now().astimezone(UTC).date()
    families = []
    for row in rows[:FAMILIES_SHOWN]:
        family = row["strategy_id"]
        prefix = f"{family}@"
        newest = [item[0] for item in database.execute(
            "SELECT strategy_ref FROM practice_strategy_versions WHERE strategy_id = ? ORDER BY sequence DESC LIMIT ?",
            (family, VERSIONS_SHOWN)).fetchall()]
        runs = {item["phase"]: item["n"] for item in database.execute(
            "SELECT phase, COUNT(*) AS n FROM practice_range_runs WHERE substr(strategy_ref, 1, ?) = ? GROUP BY phase",
            (len(prefix), prefix)).fetchall()}
        confirmed = [item[0] for item in database.execute(
            "SELECT strategy_ref FROM practice_range_runs WHERE substr(strategy_ref, 1, ?) = ? AND phase = 'confirm' "
            "GROUP BY strategy_ref ORDER BY MAX(sequence) DESC LIMIT ?", (len(prefix), prefix, CONFIRMED_SHOWN))]
        studies = database.execute("SELECT COUNT(*) FROM practice_range_studies WHERE strategy_id = ?",
                                   (family,)).fetchone()[0]
        offer = store.confirmation_offer(family, today=today, studies_shown=2)
        families.append({
            "strategy_id": family, "versions": row["versions"], "newest_versions": newest, "studies": studies,
            "runs": {"tune": runs.get("tune", 0), "confirm": runs.get("confirm", 0)},
            "confirmed_versions": confirmed, "newest_studies": list(offer.studies),
            "confirmation_offer": None if offer.window is None else offer.window.to_document(),
            "untouched_from": None if offer.untouched_from is None else offer.untouched_from.isoformat()})
    return families, max(len(rows) - FAMILIES_SHOWN, 0)


def lab_context(database, clock: Clock, portfolio_id: str, *, account_profile: str = KRAKEN_STARTING) -> dict[str, Any]:
    """The desk's ``lab`` key; on a database before migration 0051, only that the lab is not available."""
    queue = LabQueue(database, clock)
    if not queue.available():
        return {"contract_version": LAB_CONTRACT_VERSION, "available": False,
                "note": "This database has no strategy lab yet (it is older than state migration 0051)."}
    store = PracticeRangeStore(database, clock)
    counts = queue.counts(portfolio_id)
    waiting = [{"request_id": record.request_id, "status": record.status, "brief": record.request.brief()}
               for record in queue.waiting(portfolio_id, limit=WAITING_SHOWN)]
    families, families_omitted = _families(database, store, clock)
    document: dict[str, Any] = {
        "contract_version": LAB_CONTRACT_VERSION, "available": True, "instruction": LAB_INSTRUCTION,
        "account_cost_profile": account_profile,
        "newest_complete_day_end": clock.now().astimezone(UTC).date().isoformat(),
        "queue": {**counts, "next": waiting}, "families": families, "families_omitted": families_omitted,
        "results_total": queue.ended_count(portfolio_id),
    }
    results, omitted = _results(store, queue, portfolio_id, LAB_CONTEXT_BYTES - _size(document) - 512)
    document["results"], document["results_omitted"] = results, omitted
    return document


__all__ = ["LAB_CONTEXT_BYTES", "LAB_INSTRUCTION", "lab_context"]
