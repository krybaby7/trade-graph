"""Run a study phase under each cost profile, store the labelled reports, and rerun a stored report (T29).

A report is evidence for the Trader's discretion (D138 item 5): never a pass mark, a profitability claim or permission
to trade real money. Each report keeps everything a rerun needs (the registered strategy version and its hash, the
candle series and data cut-off, the windows, the cost profile and fill policy revisions and hashes, the capital and
instrument rules) and the SHA-256 of its deterministic result, so ``verify`` can replay it and say whether it was
reproduced exactly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from trade_graph.application.candle_store import CandleStore
from trade_graph.application.practice_range.replay import ReplayRequest, ReplayResult, replay
from trade_graph.application.practice_range.store import PracticeRangeStore, RunRecord, VerificationRecord
from trade_graph.contracts.fills import CostProfile, FillPolicy
from trade_graph.contracts.models import InstrumentRules
from trade_graph.contracts.practice_range import REPORT_LABEL, Phase, ReplayWindow, StudyPlan
from trade_graph.domain.clock import Clock, parse_utc
from trade_graph.domain.errors import ValidationFailure
from trade_graph.kernel.cost_profiles import CostProfileBook, load_cost_profiles
from trade_graph.kernel.fills import BAR_REPLAY_POLICY

REPORT_VERSION = "practice-range-report-v1"
DEFAULT_CAPITAL = Decimal("10000")  # the virtual USD10,000 paper account
POLICIES: dict[str, FillPolicy] = {BAR_REPLAY_POLICY.ref: BAR_REPLAY_POLICY}
SOURCE_VENUES = {"kraken_public_ohlc": "kraken", "kraken_ohlcvt_archive": "kraken", "kraken_public_trades": "kraken",
                 "okx_public_candles": "okx", "synthetic": "synthetic"}
REAL_DATA_LABELS = frozenset({"recorded_public_fixture", "live_public_smoke", "live_public", "local_archive_file"})


def result_sha256(document: dict[str, Any]) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _evidence(labels: Sequence[str]) -> dict[str, Any]:
    real = sorted(set(labels) & REAL_DATA_LABELS)
    synthetic = "synthetic_fixture" in labels
    if real and not synthetic:
        statement = "Replayed on public market data (" + ", ".join(real) + ")."
    elif synthetic and not real:
        statement = "Replayed on synthetic test data, not market history."
    else:
        statement = "Replayed on a mix of public market data and synthetic test data."
    return {"labels": sorted(set(labels)), "real_market_data": bool(real), "synthetic_data": synthetic,
            "statement": statement, "paid_calls": 0, "orders_sent": 0}


def _venue(source: str, profile: CostProfile) -> dict[str, Any]:
    data_venue = SOURCE_VENUES.get(source, "unknown")
    stand_in = data_venue not in (profile.venue, "synthetic")
    note = (f"{data_venue} prices stand in for {profile.venue}'s own: spreads and volumes differ (D126)." if stand_in
            else "The prices and the fees come from the same venue." if data_venue == profile.venue
            else "Synthetic prices: the fees are the profile's, the prices are test data.")
    return {"data_venue": data_venue, "cost_profile_venue": profile.venue, "stand_in": stand_in, "note": note}


def build_report(*, result: ReplayResult, plan: StudyPlan, phase: Phase, labels: Sequence[str],
                 selection: dict[str, Any]) -> tuple[dict[str, Any], str]:
    document = result.to_document()
    digest = result_sha256(document)
    quote = result.request.quote
    report = {
        "report_version": REPORT_VERSION, "label": REPORT_LABEL, "study": plan.to_document(), "phase": phase,
        "evidence": _evidence(labels), "venue": _venue(result.request.source, result.request.profile),
        "currency_note": None if quote == "USD" else (
            f"Amounts are in {quote}, the series' quote currency, compared as given and not converted to USD."),
        "selection": selection, "result": document, "result_sha256": digest,
    }
    return report, digest


class PracticeRange:
    def __init__(self, store: PracticeRangeStore, candles: CandleStore, clock: Clock, *,
                 book: CostProfileBook | None = None) -> None:
        self.store, self.candles, self.clock = store, candles, clock
        self.book = book or load_cost_profiles()

    def profiles(self, names: Sequence[str]) -> list[CostProfile]:
        """``all`` (every published profile, newest revision), a profile id (its newest revision) or ``id@n``."""
        chosen: list[CostProfile] = []
        for name in names:
            if name == "all":
                chosen.extend(self.book.get(profile_id) for profile_id in self.book.ids())
            elif "@" in name:
                chosen.append(self.book.resolve(name))
            else:
                chosen.append(self.book.get(name))
        unique = {profile.ref: profile for profile in chosen}
        if not unique:
            raise ValidationFailure("name at least one cost profile")
        return list(unique.values())

    def run(self, *, study_id: str, phase: Phase, strategy_ref: str, symbol: str, source: str,
            profiles: Sequence[str] = ("all",), capital: Decimal = DEFAULT_CAPITAL,
            rules: InstrumentRules | None = None, window: ReplayWindow | None = None,
            benchmark_symbol: str | None = None, benchmark_source: str | None = None,
            data_as_of=None) -> list[RunRecord]:
        """Replay one version on one series for a study phase, once per cost profile, and store each report."""
        plan = self.store.study(study_id)
        replayed = window or plan.window(phase)
        self.store.check_run(study_id=study_id, phase=phase, strategy_ref=strategy_ref, window=replayed)
        strategy = self.store.strategy(strategy_ref)
        cut_off = data_as_of or self.clock.now()
        records = []
        for profile in self.profiles(profiles):
            request = ReplayRequest(strategy=strategy, symbol=symbol, source=source, window=replayed,
                                    data_as_of=cut_off, profile=profile, policy=BAR_REPLAY_POLICY, capital=capital,
                                    rules=rules, benchmark_symbol=benchmark_symbol,
                                    benchmark_source=benchmark_source)
            result = replay(self.candles, request)
            labels = self._labels(result)
            report, digest = build_report(result=result, plan=plan, phase=phase, labels=labels,
                                          selection=self.store.selection(study_id))
            records.append(self.store.record_run(
                study_id=study_id, phase=phase, strategy_ref=strategy_ref, symbol=symbol,
                interval_minutes=strategy.interval_minutes, source=source, cost_profile_ref=profile.ref,
                fill_policy_ref=BAR_REPLAY_POLICY.ref, window=replayed,
                data_as_of=report["result"]["request"]["data_as_of_utc"], evidence=tuple(sorted(set(labels))),
                result_sha256=digest, report=report))
        return records

    def _labels(self, result: ReplayResult) -> list[str]:
        labels = list(result.data.evidence_labels)
        known = {run.run_id: run.evidence_label for run in self.candles.runs()}
        for document in result.benchmark_data.values():
            labels.extend(known.get(run_id, "unknown") for run_id in document.get("run_ids", ()))
        return labels

    def verify(self, run_id: str) -> VerificationRecord:
        """Replay a stored report from its own inputs; ``reproduced`` only when the result hash is identical."""
        record = self.store.run(run_id)
        request_document = record.report["result"]["request"]
        problems: list[str] = []
        strategy = self.store.strategy(request_document["strategy"]["ref"])
        if strategy.content_sha256 != request_document["strategy"]["sha256"]:
            problems.append("the registered strategy document no longer matches the report's hash")
        profile = self.book.resolve(request_document["cost_profile"]["ref"])
        if profile.content_sha256 != request_document["cost_profile"]["sha256"]:
            problems.append("the cost profile revision no longer matches the report's hash")
        policy = POLICIES.get(request_document["fill_policy"]["ref"])
        if policy is None or policy.content_sha256 != request_document["fill_policy"]["sha256"]:
            problems.append("the fill policy revision is unknown or changed")
            policy = policy or BAR_REPLAY_POLICY
        window_document = request_document["window"]
        rules = request_document["instrument_rules"]
        request = ReplayRequest(
            strategy=strategy, symbol=request_document["symbol"], source=request_document["source"],
            window=ReplayWindow(start=date.fromisoformat(window_document["start"]),
                                end=date.fromisoformat(window_document["end"])),
            data_as_of=parse_utc(request_document["data_as_of_utc"]), profile=profile, policy=policy,
            capital=Decimal(request_document["capital"]),
            rules=None if rules is None else InstrumentRules.model_validate(rules),
            benchmark_symbol=request_document["benchmark"]["hold_btc_symbol"],
            benchmark_source=request_document["benchmark"]["hold_btc_source"])
        rerun = replay(self.candles, request).to_document()
        digest = result_sha256(rerun)
        stored = record.report["result"]
        differences = sorted(key for key in set(stored) | set(rerun) if stored.get(key) != rerun.get(key))
        reproduced = digest == record.result_sha256 and not problems
        return self.store.record_verification(run_id, reproduced=reproduced, result_sha256=digest, document={
            "stored_result_sha256": record.result_sha256, "rerun_result_sha256": digest,
            "differences": differences, "problems": problems})


__all__ = ["DEFAULT_CAPITAL", "POLICIES", "REPORT_VERSION", "PracticeRange", "build_report", "result_sha256"]
