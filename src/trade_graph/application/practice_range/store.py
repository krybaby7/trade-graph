"""The practice-range store in the state database (migration 0030): strategy versions, studies, reports.

Rules (T29, D119, D166), checked here with plain messages and again by SQL triggers:

- A strategy version is registered once: the same ``strategy_id@version`` can never name a different document.
- A study fixes an earlier tuning window and a later confirmation window before any replay. A family may never tune
  on days one of its studies set aside for confirmation, and never confirm on days it tuned or confirmed on before.
- A tuning replay stays inside its study's tuning window. A confirmation replays the whole confirmation window, and
  the first version confirmed there spends it: another version needs a new study with a later, untouched window.
- Reports and verifications are append-only.

For the strategy lab inside the service (W11-B, D292), ``confirmation_offer`` names, per strategy family, the newest
stretch of whole days the family never tuned or confirmed on, up to the newest complete UTC day: as the price collector
adds days, they become new confirmation days (walk-forward). It is an offer, read from the same records the rules
check; the rules themselves are unchanged. The module is a protected path (W11-B): the deployed Engineer cannot change
how the test is kept fair.

Nothing here calls a model, spends money or touches an order.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.practice_range import Phase, PracticeStrategy, ReplayWindow, StudyPlan
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import NotFound, ValidationFailure


def _json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class RegisteredStrategy:
    ref: str
    strategy_id: str
    version: int
    content_sha256: str
    registered_at: str
    created: bool


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    study_id: str
    phase: Phase
    strategy_ref: str
    symbol: str
    interval_minutes: int
    source: str
    cost_profile_ref: str
    fill_policy_ref: str
    window: ReplayWindow
    data_as_of: str
    evidence: tuple[str, ...]
    result_sha256: str
    report: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class ConfirmationOffer:
    """The newest untouched days of a strategy family for a new study's confirmation window (walk-forward, D292).

    ``untouched_from`` is the day after the last day any of the family's studies tuned on or set aside for
    confirmation (None: the family has no study, so every day is untouched); ``window`` runs from there to ``until``
    (today, the end of the newest complete UTC day, excluded) when that is at least one day, else None. ``studies``
    lists the family's newest studies with their windows and the version that spent the confirmation window (None:
    still set aside, the first version confirmed there spends it).
    """

    strategy_id: str
    untouched_from: date | None
    until: date
    window: ReplayWindow | None
    studies: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    run_id: str
    reproduced: bool
    result_sha256: str
    document: dict[str, Any]
    verified_at: str


class PracticeRangeStore:
    def __init__(self, database: Database, clock: Clock) -> None:
        self.database, self.clock = database, clock

    # --- strategy versions -----------------------------------------------------------------------------------

    def register(self, strategy: PracticeStrategy) -> RegisteredStrategy:
        """Register a version; registering the identical document again changes nothing."""
        if not isinstance(strategy, PracticeStrategy):
            raise ValidationFailure("only a PracticeStrategy can be registered")
        with self.database.immediate():
            row = self.database.execute("SELECT * FROM practice_strategy_versions WHERE strategy_ref = ?",
                                        (strategy.ref,)).fetchone()
            if row is not None:
                if row["content_sha256"] != strategy.content_sha256:
                    raise ValidationFailure(f"{strategy.ref} is already registered with different content; a "
                                            "changed strategy is a new version")
                return self._registered(row, created=False)
            now = utc_iso(self.clock.now())
            self.database.execute(
                "INSERT INTO practice_strategy_versions (strategy_ref, strategy_id, version, content_sha256, "
                "document_json, registered_at) VALUES (?, ?, ?, ?, ?, ?)",
                (strategy.ref, strategy.strategy_id, strategy.version, strategy.content_sha256,
                 _json(strategy.canonical_document()), now))
        return RegisteredStrategy(ref=strategy.ref, strategy_id=strategy.strategy_id, version=strategy.version,
                                  content_sha256=strategy.content_sha256, registered_at=now, created=True)

    def strategy(self, ref: str) -> PracticeStrategy:
        row = self.database.execute("SELECT * FROM practice_strategy_versions WHERE strategy_ref = ?",
                                    (ref,)).fetchone()
        if row is None:
            raise NotFound(f"no registered strategy version {ref}")
        try:
            strategy = PracticeStrategy.model_validate_json(row["document_json"])
        except ValidationError as error:
            raise ValidationFailure(f"the stored document of {ref} no longer validates: "
                                    f"{error.errors()[0]['msg']}") from None
        if strategy.content_sha256 != row["content_sha256"] or strategy.ref != ref:
            raise ValidationFailure(f"the stored document of {ref} does not match its registered hash")
        return strategy

    def strategies(self) -> list[RegisteredStrategy]:
        rows = self.database.execute("SELECT * FROM practice_strategy_versions ORDER BY sequence").fetchall()
        return [self._registered(row, created=False) for row in rows]

    @staticmethod
    def _registered(row: sqlite3.Row, *, created: bool) -> RegisteredStrategy:
        return RegisteredStrategy(ref=row["strategy_ref"], strategy_id=row["strategy_id"], version=row["version"],
                                  content_sha256=row["content_sha256"], registered_at=row["registered_at"],
                                  created=created)

    # --- studies ---------------------------------------------------------------------------------------------

    def create_study(self, plan: StudyPlan) -> tuple[StudyPlan, bool]:
        """Record a study's windows before any replay; the identical study again changes nothing."""
        with self.database.immediate():
            existing = self.database.execute("SELECT document_json FROM practice_range_studies WHERE study_id = ?",
                                             (plan.study_id,)).fetchone()
            if existing is not None:
                stored = StudyPlan.model_validate_json(existing["document_json"])
                if stored != plan:
                    raise ValidationFailure(f"study {plan.study_id} already exists with other windows")
                return stored, False
            for other in self.studies(plan.strategy_id):
                if plan.tune.overlaps(other.confirm):
                    raise ValidationFailure(
                        f"the tuning window touches study {other.study_id}'s confirmation window "
                        f"({other.confirm.start} to {other.confirm.end}); a confirmation window is never reused for "
                        "tuning")
                if plan.confirm.overlaps(other.confirm) or plan.confirm.overlaps(other.tune):
                    raise ValidationFailure(
                        f"the confirmation window touches days study {other.study_id} already tuned or confirmed on; "
                        "a confirmation needs later, untouched days")
            try:
                self.database.execute(
                    "INSERT INTO practice_range_studies (study_id, strategy_id, tune_start, tune_end, confirm_start, "
                    "confirm_end, document_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (plan.study_id, plan.strategy_id, plan.tune.start.isoformat(), plan.tune.end.isoformat(),
                     plan.confirm.start.isoformat(), plan.confirm.end.isoformat(), _json(plan.model_dump(mode="json")),
                     utc_iso(self.clock.now())))
            except sqlite3.IntegrityError as error:
                raise ValidationFailure(f"study refused: {error}") from None
        return plan, True

    def study(self, study_id: str) -> StudyPlan:
        row = self.database.execute("SELECT document_json FROM practice_range_studies WHERE study_id = ?",
                                    (study_id,)).fetchone()
        if row is None:
            raise NotFound(f"no practice-range study {study_id}")
        return StudyPlan.model_validate_json(row["document_json"])

    def studies(self, strategy_id: str | None = None) -> list[StudyPlan]:
        if strategy_id is None:
            rows = self.database.execute("SELECT document_json FROM practice_range_studies ORDER BY sequence")
        else:
            rows = self.database.execute("SELECT document_json FROM practice_range_studies WHERE strategy_id = ? "
                                         "ORDER BY sequence", (strategy_id,))
        return [StudyPlan.model_validate_json(row["document_json"]) for row in rows.fetchall()]

    # --- runs ------------------------------------------------------------------------------------------------

    def check_run(self, *, study_id: str, phase: Phase, strategy_ref: str, window: ReplayWindow) -> StudyPlan:
        """Refuse, before any replay is computed, a run the store would refuse to record."""
        plan = self.study(study_id)
        strategy = self.strategy(strategy_ref)
        if strategy.strategy_id != plan.strategy_id:
            raise ValidationFailure(f"study {study_id} replays {plan.strategy_id}, not {strategy.strategy_id}")
        if phase == "tune":
            if not plan.tune.contains(window):
                raise ValidationFailure(f"tuning stays inside the study's tuning window ({plan.tune.start} to "
                                        f"{plan.tune.end})")
            for other in self.studies(plan.strategy_id):
                if window.overlaps(other.confirm):
                    raise ValidationFailure(f"those days are study {other.study_id}'s confirmation window; a "
                                            "confirmation window cannot be reused for tuning")
        else:
            if window != plan.confirm:
                raise ValidationFailure(f"a confirmation replays the whole confirmation window ({plan.confirm.start} "
                                        f"to {plan.confirm.end})")
            bound = self.database.execute(
                "SELECT strategy_ref FROM practice_range_runs WHERE study_id = ? AND phase = 'confirm' "
                "ORDER BY sequence LIMIT 1", (study_id,)).fetchone()
            if bound is not None and bound["strategy_ref"] != strategy_ref:
                raise ValidationFailure(
                    f"study {study_id}'s confirmation window is spent on {bound['strategy_ref']}; confirming "
                    f"{strategy_ref} needs a new study with a later, untouched confirmation window")
        return plan

    def record_run(self, *, study_id: str, phase: Phase, strategy_ref: str, symbol: str, interval_minutes: int,
                   source: str, cost_profile_ref: str, fill_policy_ref: str, window: ReplayWindow, data_as_of: str,
                   evidence: tuple[str, ...], result_sha256: str, report: dict[str, Any],
                   run_id: str | None = None) -> RunRecord:
        identifier = run_id or f"pr-{uuid.uuid4().hex}"
        created = utc_iso(self.clock.now())
        with self.database.immediate():
            self.check_run(study_id=study_id, phase=phase, strategy_ref=strategy_ref, window=window)
            try:
                self.database.execute(
                    "INSERT INTO practice_range_runs (run_id, study_id, phase, strategy_ref, symbol, interval_minutes, "
                    "source, cost_profile_ref, fill_policy_ref, window_start, window_end, data_as_of, evidence, "
                    "result_sha256, report_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (identifier, study_id, phase, strategy_ref, symbol, interval_minutes, source, cost_profile_ref,
                     fill_policy_ref, window.start.isoformat(), window.end.isoformat(), data_as_of,
                     ",".join(evidence), result_sha256, _json(report), created))
            except sqlite3.IntegrityError as error:
                raise ValidationFailure(f"replay report refused: {error}") from None
        return self.run(identifier)

    def run(self, run_id: str) -> RunRecord:
        row = self.database.execute("SELECT * FROM practice_range_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"no practice-range report {run_id}")
        return self._run(row)

    def runs(self, *, study_id: str | None = None, strategy_ref: str | None = None,
             phase: Phase | None = None) -> list[RunRecord]:
        clauses, params = [], []
        for column, value in (("study_id", study_id), ("strategy_ref", strategy_ref), ("phase", phase)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.database.execute(f"SELECT * FROM practice_range_runs{where} ORDER BY sequence",
                                     tuple(params)).fetchall()
        return [self._run(row) for row in rows]

    def reports_for(self, strategy_ref: str, *, phase: Phase | None = None) -> list[RunRecord]:
        """Every stored report of one strategy version (for day plans, T34: a report must exist, D138 item 5)."""
        return self.runs(strategy_ref=strategy_ref, phase=phase)

    def has_report(self, strategy_ref: str, *, confirmed: bool = False) -> bool:
        phase = "confirm" if confirmed else None
        clause = " AND phase = 'confirm'" if phase else ""
        row = self.database.execute(f"SELECT 1 FROM practice_range_runs WHERE strategy_ref = ?{clause} LIMIT 1",
                                    (strategy_ref,)).fetchone()
        return row is not None

    def confirmation_offer(self, strategy_id: str, *, today: date, studies_shown: int = 4) -> ConfirmationOffer:
        """The newest untouched days of a family for a confirmation, up to ``today`` (excluded); see the class."""
        plans = self.studies(strategy_id)
        latest = max((max(plan.tune.end, plan.confirm.end) for plan in plans), default=None)
        window = None
        if latest is not None and latest < today:
            window = ReplayWindow(start=latest, end=today)
        bound = {row["study_id"]: row["strategy_ref"] for row in self.database.execute(
            "SELECT r.study_id, r.strategy_ref FROM practice_range_runs r JOIN practice_range_studies s "
            "ON s.study_id = r.study_id WHERE s.strategy_id = ? AND r.phase = 'confirm' AND r.sequence = "
            "(SELECT MIN(f.sequence) FROM practice_range_runs f WHERE f.study_id = r.study_id AND f.phase = 'confirm')",
            (strategy_id,)).fetchall()}
        shown = tuple({"study_id": plan.study_id, "tune": plan.tune.to_document(),
                       "confirm": plan.confirm.to_document(), "confirmed_by": bound.get(plan.study_id)}
                      for plan in plans[::-1][:studies_shown])
        return ConfirmationOffer(strategy_id=strategy_id, untouched_from=latest, until=today, window=window,
                                 studies=shown)

    def selection(self, study_id: str) -> dict[str, Any]:
        """What was tried in a study so far, so a confirmation shows the selection behind it (D119)."""
        rows = self.database.execute(
            "SELECT phase, strategy_ref, COUNT(*) AS n FROM practice_range_runs WHERE study_id = ? "
            "GROUP BY phase, strategy_ref ORDER BY phase, strategy_ref", (study_id,)).fetchall()
        tuned = {row["strategy_ref"]: row["n"] for row in rows if row["phase"] == "tune"}
        confirmed = {row["strategy_ref"]: row["n"] for row in rows if row["phase"] == "confirm"}
        return {"tune_runs": sum(tuned.values()), "versions_tuned": sorted(tuned), "confirm_runs":
                sum(confirmed.values()), "versions_confirmed": sorted(confirmed)}

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"], study_id=row["study_id"], phase=row["phase"], strategy_ref=row["strategy_ref"],
            symbol=row["symbol"], interval_minutes=row["interval_minutes"], source=row["source"],
            cost_profile_ref=row["cost_profile_ref"], fill_policy_ref=row["fill_policy_ref"],
            window=ReplayWindow(start=date.fromisoformat(row["window_start"]),
                                end=date.fromisoformat(row["window_end"])),
            data_as_of=row["data_as_of"], evidence=tuple(item for item in row["evidence"].split(",") if item),
            result_sha256=row["result_sha256"], report=json.loads(row["report_json"]), created_at=row["created_at"])

    # --- verifications ---------------------------------------------------------------------------------------

    def record_verification(self, run_id: str, *, reproduced: bool, result_sha256: str,
                            document: dict[str, Any]) -> VerificationRecord:
        verified = utc_iso(self.clock.now())
        with self.database.immediate():
            self.run(run_id)
            self.database.execute(
                "INSERT INTO practice_range_verifications (run_id, reproduced, result_sha256, document_json, "
                "verified_at) VALUES (?, ?, ?, ?, ?)", (run_id, int(reproduced), result_sha256, _json(document),
                                                       verified))
        return VerificationRecord(run_id=run_id, reproduced=reproduced, result_sha256=result_sha256,
                                  document=document, verified_at=verified)

    def verifications(self, run_id: str) -> list[VerificationRecord]:
        rows = self.database.execute("SELECT * FROM practice_range_verifications WHERE run_id = ? ORDER BY sequence",
                                     (run_id,)).fetchall()
        return [VerificationRecord(run_id=row["run_id"], reproduced=bool(row["reproduced"]),
                                   result_sha256=row["result_sha256"], document=json.loads(row["document_json"]),
                                   verified_at=row["verified_at"]) for row in rows]


class ReadOnlyReports:
    """Stored reports read from a state database opened read-only: nothing is created, upgraded or written."""

    def __init__(self, path: Path | str) -> None:
        target = Path(path)
        if not target.is_file() or target.stat().st_size == 0:
            raise NotFound(f"no state database at {path}")
        self._connection = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row
        found = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'practice_range_runs'").fetchone()
        self.available = found is not None

    def runs(self, *, study_id: str | None = None, strategy_ref: str | None = None) -> list[RunRecord]:
        if not self.available:
            return []
        clauses, params = [], []
        for column, value in (("study_id", study_id), ("strategy_ref", strategy_ref)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(f"SELECT * FROM practice_range_runs{where} ORDER BY sequence",
                                        tuple(params)).fetchall()
        return [PracticeRangeStore._run(row) for row in rows]

    def run(self, run_id: str) -> RunRecord:
        row = None if not self.available else self._connection.execute(
            "SELECT * FROM practice_range_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"no practice-range report {run_id}")
        return PracticeRangeStore._run(row)

    def close(self) -> None:
        self._connection.close()


__all__ = ["ConfirmationOffer", "PracticeRangeStore", "ReadOnlyReports", "RegisteredStrategy", "RunRecord",
           "VerificationRecord"]
