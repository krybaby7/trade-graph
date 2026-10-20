"""The strategy lab's worker in the installed service (W11-B, C11; D280, D116, D165-D169, D290-D293).

The service calls ``step`` from its own thread, like the price collector's (D222): each step runs the oldest open
request (one an earlier run left ``running`` first, so an interrupted request is run again). The worker reads the
installation's candle file through its **own** read-only handle (never the price watcher's, so a long replay cannot
hold up an alarm check), and writes only practice-range records and its queue into the state database. It never
places, sends or cancels an order, and no trading, protection, reconciliation or money-limit step waits for it.

How a request is carried out:

- ``register_strategy`` and ``create_study`` go straight to the protected practice-range store, which applies the
  grammar (D165) and the study rules (D166) and refuses with a plain reason;
- ``tune`` and ``confirm`` are checked by the store first (``check_run``: tune on earlier days, confirm the whole
  window once on untouched later days), then replayed outside any transaction under every cost profile asked for and
  always the account's own (default: all published profiles), at one data cut-off; then all their reports and the
  request's end are written in **one** transaction. A stop before the last profile's replay puts the request back
  (``queued``); a crash leaves it ``running`` and the next start runs it again. Either way nothing of it was recorded:
  never half recorded.

A refused request (a ``TradeGraphError``: the grammar, the study rules, a missing version or candle file) ends
``refused`` with the reason; anything unexpected ends ``failed`` with the error's type, and the queue goes on. There is
no cap on the queue (D116). At the first step of a start, an installation with no registered strategy registers the
two packaged examples (``starting.py``, D293), so the lab has a start; the desk asks for their confirmations itself.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.practice_range.replay import ReplayRequest, ReplayResult, replay
from trade_graph.application.practice_range.service import DEFAULT_CAPITAL, build_report
from trade_graph.application.practice_range.store import PracticeRangeStore
from trade_graph.application.strategy_lab.queue import LabQueue, LabRequestRecord
from trade_graph.application.strategy_lab.starting import register_starting
from trade_graph.contracts.fills import CostProfile
from trade_graph.contracts.strategy_lab import LabRequest
from trade_graph.domain.clock import Clock
from trade_graph.domain.errors import TradeGraphError, ValidationFailure
from trade_graph.kernel.cost_profiles import KRAKEN_STARTING, CostProfileBook, load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY

ACTIVITY_KIND = "strategy_lab"
_MILLI = Decimal("0.001")


class _Stopped(Exception):
    """The service is stopping: the request goes back to the queue with nothing recorded."""


def account_cost_profile(execution) -> str:
    """The account's own fees: the paper broker's published profile (Kraken's starting tier by default, D146)."""
    profile = getattr(getattr(execution, "broker", None), "cost_profile", None)
    if isinstance(profile, CostProfile) and profile.basis == "published_schedule":
        return profile.profile_id
    return KRAKEN_STARTING


class StrategyLabWorker:
    def __init__(self, database: Database, clock: Clock, *, candles: CandleStore | None,
                 account_profile: str = KRAKEN_STARTING, record: Callable[[dict[str, Any]], None] | None = None,
                 replay_fn: Callable[[CandleStore, ReplayRequest], ReplayResult] = replay,
                 book: CostProfileBook | None = None, capital: Decimal = DEFAULT_CAPITAL,
                 timer: Callable[[], float] = time.monotonic, register_examples: bool = True) -> None:
        self.database, self.clock, self.candles = database, clock, candles
        self.book = book or load_cost_profiles()
        self.book.get(account_profile)  # an unknown account profile is refused at assembly, never at a request
        self.account_profile, self.record, self.replay_fn = account_profile, record, replay_fn
        self.capital, self.timer, self.register_examples = capital, timer, register_examples
        self.queue = LabQueue(database, clock)
        self.store = PracticeRangeStore(database, clock)
        self.stop = threading.Event()
        self.ran = 0
        self.report_failures = 0
        self._started = False

    def request_stop(self) -> None:
        self.stop.set()

    def close(self) -> None:
        """Release what the worker holds (the installed lab's own candle handle); nothing to release here."""

    def resume(self) -> None:
        self.stop.clear()

    # --- the step -----------------------------------------------------------------------------------------------

    def step(self) -> dict[str, Any]:
        """Run the oldest open request; ``idle`` when there is none, ``stopped`` after a stop."""
        if self.stop.is_set():
            return {"status": "stopped"}
        if not self._started:
            self._start()
        record = self.queue.next_open()
        if record is None:
            return {"status": "idle"}
        return self._run(record)

    def run_until_idle(self, *, limit: int = 10_000) -> list[dict[str, Any]]:
        """Step until the queue is empty or a stop (tests and owner tools; ``limit`` guards a loop, not the queue)."""
        steps = []
        for _ in range(limit):
            report = self.step()
            steps.append(report)
            if report["status"] in ("idle", "stopped"):
                return steps
        raise RuntimeError("the lab queue kept growing while it was being emptied")

    def _start(self) -> None:
        registered: list[str] = []
        if self.register_examples:
            with self.database.immediate():
                registered = register_starting(self.store)
        interrupted = self.queue.counts()["running"]
        self._started = True
        self._report({"status": "started", "registered_starting": registered, "interrupted": interrupted,
                      "account_cost_profile": self.account_profile, "candle_file": self.candles is not None,
                      "queue": self.queue.counts(),
                      "note": self._start_note(registered, interrupted)})

    def _start_note(self, registered: list[str], interrupted: int) -> str:
        parts = ["The strategy lab is ready."]
        if registered:
            parts.append(f"No strategy was registered, so it registered the two examples ({', '.join(registered)}); "
                         "the desk asks for their confirmations itself.")
        if interrupted:
            parts.append(f"{interrupted} request interrupted by the last stop runs again, nothing of it recorded.")
        if self.candles is None:
            parts.append("This installation has no candle file, so replays are refused until the price collector "
                         "keeps one.")
        return " ".join(parts)

    # --- one request ----------------------------------------------------------------------------------------------

    def _run(self, record: LabRequestRecord) -> dict[str, Any]:
        claimed = self.queue.claim(record.request_id)
        started = self.timer()
        request = claimed.request
        try:
            problem = request.problem()
            if problem is not None:
                raise ValidationFailure(problem)
            if request.kind == "register_strategy":
                status, detail = "done", self._record_now(claimed, started, self._register, request)
            elif request.kind == "create_study":
                status, detail = "done", self._record_now(claimed, started, self._study, request)
            else:
                status, detail = "done", self._replay(claimed, started)
        except _Stopped:
            self.queue.put_back(claimed.request_id)
            self._report({"status": "interrupted", "request_id": claimed.request_id, "kind": request.kind,
                          "brief": request.brief(), "seconds": self._seconds(started), "queue": self.queue.counts(),
                          "note": "The service stopped during this request; it waits again, nothing of it recorded."})
            return {"status": "stopped", "request_id": claimed.request_id}
        except TradeGraphError as error:
            status, detail = "refused", str(error) or type(error).__name__
            self._end(claimed, "refused", reason=detail, seconds=self._seconds(started))
        except Exception as error:  # noqa: BLE001 - one broken request fails alone; the queue goes on
            status, detail = "failed", f"the request failed unexpectedly ({type(error).__name__})"
            self._end(claimed, "failed", reason=detail, seconds=self._seconds(started))
        self.ran += 1
        seconds = self.queue.request(claimed.request_id).seconds
        self._report({"status": status, "request_id": claimed.request_id, "kind": request.kind,
                      "family": claimed.family, "brief": request.brief(), "attempt": claimed.attempts,
                      "reason": detail if status != "done" else None,
                      "runs": len(detail.get("runs", ())) if isinstance(detail, dict) else 0,
                      "seconds": seconds, "queue": self.queue.counts(),
                      "note": self._note(status, request, detail, seconds)})
        return {"status": "ran", "request_id": claimed.request_id, "outcome": status}

    def _end(self, record: LabRequestRecord, status: str, *, reason: str, seconds: str) -> None:
        with self.database.immediate():
            self.queue.finish(record.request_id, status, reason=reason, seconds=seconds)

    def _record_now(self, record: LabRequestRecord, started: float, action, request: LabRequest) -> dict[str, Any]:
        with self.database.immediate():
            result = action(request)
            self.queue.finish(record.request_id, "done", result=result, seconds=self._seconds(started))
        return result

    def _register(self, request: LabRequest) -> dict[str, Any]:
        registered = self.store.register(request.strategy())
        return {"strategy_ref": registered.ref, "content_sha256": registered.content_sha256,
                "created": registered.created}

    def _study(self, request: LabRequest) -> dict[str, Any]:
        plan, created = self.store.create_study(request.study_plan())
        return {"study_id": plan.study_id, "created": created, **plan.to_document()}

    def _replay(self, record: LabRequestRecord, started: float) -> dict[str, Any]:
        request = record.request
        phase = "tune" if request.kind == "tune" else "confirm"
        plan = self.store.study(request.study_id)
        window = request.window() or plan.window(phase)
        self.store.check_run(study_id=plan.study_id, phase=phase, strategy_ref=request.strategy_ref, window=window)
        if self.candles is None:
            raise ValidationFailure("this installation has no candle file (the price collector keeps it); a replay "
                                    "needs one")
        strategy = self.store.strategy(request.strategy_ref)
        profiles = self.profiles(request.cost_profiles)
        cut_off = self.clock.now()
        results: list[ReplayResult] = []
        for profile in profiles:
            if self.stop.is_set():
                raise _Stopped
            results.append(self.replay_fn(self.candles, ReplayRequest(
                strategy=strategy, symbol=request.symbol, source=request.replay_source(), window=window,
                data_as_of=cut_off, profile=profile, policy=BAR_REPLAY_POLICY, capital=self.capital)))
        known = {run.run_id: run.evidence_label for run in self.candles.runs()}
        runs = []
        with self.database.immediate():
            for result in results:
                labels = list(result.data.evidence_labels)
                for document in result.benchmark_data.values():
                    labels.extend(known.get(run_id, "unknown") for run_id in document.get("run_ids", ()))
                report, digest = build_report(result=result, plan=plan, phase=phase, labels=labels,
                                              selection=self.store.selection(plan.study_id))
                stored = self.store.record_run(
                    study_id=plan.study_id, phase=phase, strategy_ref=strategy.ref, symbol=request.symbol,
                    interval_minutes=strategy.interval_minutes, source=request.replay_source(),
                    cost_profile_ref=result.request.profile.ref, fill_policy_ref=BAR_REPLAY_POLICY.ref, window=window,
                    data_as_of=report["result"]["request"]["data_as_of_utc"], evidence=tuple(sorted(set(labels))),
                    result_sha256=digest, report=report)
                metrics = report["result"]["metrics"]
                runs.append({"run_id": stored.run_id, "cost_profile_ref": stored.cost_profile_ref,
                             "result_sha256": digest, "trades": metrics["trades"], "result": metrics["result"],
                             "currency": metrics["currency"]})
            detail = {"phase": phase, "study_id": plan.study_id, "strategy_ref": strategy.ref, "symbol": request.symbol,
                      "source": request.replay_source(), "window": window.to_document(),
                      "account_cost_profile": self.account_profile, "runs": runs}
            self.queue.finish(record.request_id, "done", result=detail, seconds=self._seconds(started))
        return detail

    def profiles(self, names: Sequence[str]) -> list[CostProfile]:
        """The profiles asked for (``all`` when none is named), and always the account's own (D292)."""
        chosen: list[CostProfile] = []
        for name in (*(names or ("all",)), self.account_profile):
            if name == "all":
                chosen.extend(self.book.get(profile_id) for profile_id in self.book.ids())
            elif "@" in name:
                chosen.append(self.book.resolve(name))
            else:
                chosen.append(self.book.get(name))
        unique = {profile.ref: profile for profile in chosen}
        own = self.book.get(self.account_profile)
        return [own, *(profile for ref, profile in unique.items() if ref != own.ref)]

    # --- reporting ----------------------------------------------------------------------------------------------

    def _seconds(self, started: float) -> str:
        elapsed = max(Decimal(str(self.timer() - started)), Decimal("0"))
        return str(elapsed.quantize(_MILLI, rounding=ROUND_HALF_EVEN))

    @staticmethod
    def _note(status: str, request: LabRequest, detail: Any, seconds: str | None) -> str:
        brief = request.brief()
        subject = brief.get("strategy_ref") or brief.get("study_id") or brief.get("strategy_id") or request.kind
        took = f" in {seconds} s" if seconds else ""
        if status == "done":
            if isinstance(detail, dict) and detail.get("runs"):
                return (f"The lab finished a {request.kind} replay of {subject} on {brief.get('symbol')}{took}: "
                        f"{len(detail['runs'])} reports.")
            return f"The lab finished {request.kind} for {subject}{took}."
        return f"The lab {status} {request.kind} for {subject}{took}: {detail}"[:1000]

    def _report(self, payload: dict[str, Any]) -> None:
        """Record a ``strategy_lab`` activity; a failure to log never fails the request, whose own record is kept."""
        if self.record is None:
            return
        try:
            self.record(payload)
        except Exception:  # noqa: BLE001 - the activity log is a view; the lab's queue and reports are the record
            self.report_failures += 1


__all__ = ["ACTIVITY_KIND", "StrategyLabWorker", "account_cost_profile"]
