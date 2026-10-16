"""One scan: every Kraken USD pair of the stored catalog, checked, ranked and shortlisted (T31, D120, D170-D174).

``CoinScanner.scan`` reads only stored data and the ticker answer it is given, so it makes no request of its own
and calls no model: the shortlist is software's (acceptance "no model call"). A live scan reads candles
point-in-time from the live source; a replay reads a stated source by event time and is always labelled
optimistic, because Kraken's history has no delisted pairs (D119, D134) and there is no historical bid and ask.
The result is plain data: ``document()`` for the whole scan and ``entries()`` for the shortlist, each entry with
every input value, its source time and the ranking version. ``application/coin_scanner/store.py`` persists it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from trade_graph.adapters.market.kraken_ticker import TickerSnapshot, quotes_by_symbol
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.coin_scanner.inputs import PairEvaluation, ScanSettings, TickerState, evaluate_pair
from trade_graph.application.coin_scanner.ranking import Ranked, rank, shortlist
from trade_graph.application.indicators.library import library_versions
from trade_graph.contracts.candles import CANDLE_SOURCES, EVIDENCE_LABELS, CatalogObservation
from trade_graph.contracts.coin_scanner import EXCLUSION_REASONS, FACTORS, FLAGS, SCAN_MODES, ScanMode, ScannerRules
from trade_graph.contracts.pair_catalog import PairRecord
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.cost_profiles import CostProfileBook, load_cost_profiles

# Weakest first: a scan is only as real as its least real input.
_EVIDENCE_ORDER = ("synthetic_fixture", "recorded_public_fixture", "local_archive_file", "live_public_smoke",
                   "live_public")
OPTIMISM = {
    "delisted_pairs_missing": ("Kraken's history holds no delisted pairs, so coins that later failed or were removed "
                               "cannot appear; coin-selection results look better than reality (D119, D134)."),
    "catalog_observed_after_the_replayed_moment": (
        "The pair list and its marks were observed after the replayed moment, so pairs listed later are considered "
        "and pairs removed before the list was taken are not."),
    "no_historical_spread": "There is no historical bid and ask, so spread was neither checked nor ranked.",
    "cost_profile_after_the_replayed_moment": (
        "No fee revision was in force on the replayed day; the earliest revision of the profile was used."),
}


@dataclass(frozen=True, slots=True)
class ScanResult:
    mode: ScanMode
    as_of: datetime
    computed_at: datetime
    rules: ScannerRules
    status: str
    error: str | None
    evidence: str
    optimism_codes: tuple[str, ...]
    factors: tuple[str, ...]
    source: str
    cost: Mapping[str, Any] | None
    catalog: Mapping[str, Any] | None
    ticker: Mapping[str, Any]
    evaluations: tuple[PairEvaluation, ...]
    ranked: tuple[Ranked, ...]
    shortlist: tuple[Ranked, ...]

    @property
    def optimistic(self) -> bool:
        return self.mode == "replay"

    @property
    def ranking_version(self) -> str:
        return self.rules.ranking_version

    def _by_symbol(self) -> dict[str, PairEvaluation]:
        return {item.symbol: item for item in self.evaluations}

    def document(self) -> dict[str, Any]:
        """The whole scan as plain JSON: rules, sources, counts, every exclusion by reason and the full ranking."""
        excluded: dict[str, list[str]] = {}
        flagged: dict[str, list[str]] = {}
        for item in self.evaluations:
            for reason in item.reasons:
                excluded.setdefault(reason, []).append(item.symbol)
            for flag in item.flags:
                flagged.setdefault(flag, []).append(item.symbol)
        excluded = {reason: sorted(excluded[reason]) for reason in EXCLUSION_REASONS if reason in excluded}
        flagged = {flag: sorted(flagged[flag]) for flag in FLAGS if flag in flagged}
        return {
            "scan": {
                "mode": self.mode, "as_of_utc": utc_iso(self.as_of), "computed_at_utc": utc_iso(self.computed_at),
                "ranking_version": self.ranking_version, "status": self.status, "error": self.error,
                "evidence": self.evidence, "optimistic": self.optimistic,
                "optimism_reasons": [{"code": code, "text": OPTIMISM[code]} for code in self.optimism_codes],
                "model_calls": 0, "paid_calls": False, "live_trading": False,
            },
            "rules": self.rules.document(),
            "factors": list(self.factors),
            "candles": {"source": self.source, "interval_minutes": self.rules.interval_minutes,
                        "mode": "point_in_time" if self.mode == "live" else "replay",
                        "availability_basis": "receipt_time" if self.mode == "live" else "event_time_only"},
            "cost_profile": None if self.cost is None else dict(self.cost),
            "catalog": None if self.catalog is None else dict(self.catalog),
            "ticker": dict(self.ticker),
            "library": library_versions(),
            "counts": {
                "usd_pairs": len(self.evaluations), "ranked": len(self.ranked), "shortlisted": len(self.shortlist),
                "excluded": sum(1 for item in self.evaluations if item.reasons),
                "by_reason": {reason: len(symbols) for reason, symbols in excluded.items()},
                "flagged": {flag: len(symbols) for flag, symbols in flagged.items()},
            },
            "excluded": excluded,
            "flags": flagged,
            "ranking": [_ranking_row(item) for item in self.ranked],
            "shortlist": [item.symbol for item in self.shortlist],
        }

    def entries(self) -> list[dict[str, Any]]:
        """The shortlist, each entry with every input value, its source time and the ranking version."""
        by_symbol = self._by_symbol()
        return [{"position": item.position, "symbol": item.symbol, "score": item.score, "ranks": dict(item.ranks),
                 "ranking_version": self.ranking_version, "flags": list(by_symbol[item.symbol].flags),
                 "inputs": dict(by_symbol[item.symbol].inputs)} for item in self.shortlist]

    def passed_symbols(self) -> tuple[str, ...]:
        """Pairs that passed every liquidity and exclusion check at this moment (D138 item 3's coin rule)."""
        return tuple(sorted(item.symbol for item in self.ranked))


def _ranking_row(item: Ranked) -> dict[str, Any]:
    candidate = item.candidate
    values = {"volume_usd_24h": canonical_decimal(candidate.volume_usd_24h),
              "swing_to_cost": canonical_decimal(candidate.swing_to_cost),
              "spread": None if candidate.spread is None else canonical_decimal(candidate.spread),
              "trend": canonical_decimal(candidate.trend)}
    return {"position": item.position, "symbol": item.symbol, "score": item.score, "ranks": dict(item.ranks),
            "values": values}


def _aware(label: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailure(f"{label} must be a timezone-aware datetime")
    return value


class CoinScanner:
    """Ranks the stored catalog's USD pairs. Reads only; writing the result is the store's job."""

    def __init__(self, store: CandleStore, *, clock: Clock, rules: ScannerRules | None = None,
                 cost_profiles: CostProfileBook | None = None) -> None:
        self.store = store
        self.clock = clock
        self.rules = rules or ScannerRules()
        self.cost_profiles = cost_profiles or load_cost_profiles()

    def scan(self, *, as_of: datetime, ticker: TickerSnapshot | None, mode: ScanMode = "live",
             source: str | None = None) -> ScanResult:
        as_of = _aware("as_of", as_of)
        if mode not in SCAN_MODES:
            raise ValidationFailure("a scan is live or a replay")
        rules = self.rules
        if mode == "live":
            if source not in (None, rules.live_source):
                raise ValidationFailure(f"a live scan reads the live source {rules.live_source} point-in-time; "
                                        "another source is a replay")
            source = rules.live_source
        else:
            source = source or rules.live_source
            if ticker is not None:
                raise ValidationFailure("a replay has no historical ticker; spread is not ranked in a replay")
        if source not in CANDLE_SOURCES:
            raise ValidationFailure("unsupported candle source")
        computed_at = _aware("the clock", self.clock.now())
        optimism: list[str] = ["delisted_pairs_missing"] if mode == "replay" else []
        factors = FACTORS if mode == "live" else tuple(name for name in FACTORS if name != "spread")

        def finish(status: str, error: str | None, *, cost=None, catalog=None, ticker_doc=None, evaluations=(),
                   ranked=(), chosen=(), runs: set[int] | None = None) -> ScanResult:
            if mode == "replay":
                optimism.append("no_historical_spread")
            return ScanResult(
                mode=mode, as_of=as_of, computed_at=computed_at, rules=rules, status=status, error=error,
                evidence=self._evidence(source, catalog, ticker, runs or set()),
                optimism_codes=tuple(code for code in OPTIMISM if code in optimism), factors=factors, source=source,
                cost=cost, catalog=catalog, ticker=ticker_doc or self._ticker_document(ticker, mode),
                evaluations=tuple(evaluations), ranked=tuple(ranked), shortlist=tuple(chosen))

        cost = self._cost(as_of, mode, optimism)
        if cost is None:
            return finish("failed", "no_cost_profile_in_force")
        observation = self.store.latest_catalog(as_of=as_of)
        if observation is None and mode == "replay":
            observation = self.store.latest_catalog(as_of=computed_at)
            if observation is not None:
                optimism.append("catalog_observed_after_the_replayed_moment")
        if observation is None:
            return finish("failed", "no_pair_catalog", cost=cost)
        try:
            records = sorted((PairRecord.model_validate(pair) for pair in observation.pairs),
                             key=lambda item: item.symbol)
        except ValueError:
            return finish("failed", "catalog_unreadable", cost=cost)
        records = [item for item in records if item.usd_quoted]
        catalog = self._catalog_document(observation, as_of)
        state = self._ticker_state(ticker, records, as_of) if mode == "live" else TickerState(False, "replay")
        settings = ScanSettings(rules=rules, as_of=as_of, mode=mode, source=source,
                                round_trip_rate=cost["round_trip_rate_decimal"],
                                cost={key: value for key, value in cost.items() if key != "round_trip_rate_decimal"},
                                catalog=catalog, ticker=state)
        evaluations = [evaluate_pair(self.store, item, settings) for item in records]
        ranked = rank([item.candidate for item in evaluations if item.candidate is not None], factors=factors)
        chosen = shortlist(ranked, rules.shortlist_size) if ranked else ()
        runs = {run for item in evaluations for run in item.run_ids}
        runs.add(observation.run_id)
        return finish("ok" if ranked else "no_candidates", None, cost=settings.cost, catalog=catalog,
                      ticker_doc=self._ticker_document(ticker, mode, state), evaluations=evaluations, ranked=ranked,
                      chosen=chosen, runs=runs)

    # --- parts -----------------------------------------------------------------------------------------------------

    def _cost(self, as_of: datetime, mode: ScanMode, optimism: list[str]) -> dict[str, Any] | None:
        rules = self.rules
        try:
            profile = self.cost_profiles.effective(rules.cost_profile_id, as_of.date())
        except ValidationFailure:
            if mode == "live":
                return None
            profile = self.cost_profiles.get(rules.cost_profile_id, 1)
            optimism.append("cost_profile_after_the_replayed_moment")
        rate = profile.round_trip_rate(rules.cost_liquidity)
        return {"ref": profile.ref, "content_sha256": profile.content_sha256, "liquidity": rules.cost_liquidity,
                "maker_rate": canonical_decimal(profile.maker_rate),
                "taker_rate": canonical_decimal(profile.taker_rate), "round_trip_rate": canonical_decimal(rate),
                "effective_from": profile.effective_from.isoformat(), "round_trip_rate_decimal": rate}

    @staticmethod
    def _catalog_document(observation: CatalogObservation, as_of: datetime) -> dict[str, Any]:
        age = (as_of - observation.observed_at_utc).total_seconds() / 3600
        return {"observation_id": observation.observation_id, "observed_at_utc": utc_iso(observation.observed_at_utc),
                "run_id": observation.run_id, "rules_version": observation.rules_version,
                "observed_after_as_of": observation.observed_at_utc > as_of,
                "age_hours": round(age, 3)}

    def _ticker_state(self, ticker: TickerSnapshot | None, records: list[PairRecord], as_of: datetime) -> TickerState:
        if ticker is None:
            return TickerState(False, "no_ticker_answer")
        if ticker.received_at > as_of:
            return TickerState(False, "ticker_after_scan_moment", received_at=ticker.received_at,
                               evidence_label=ticker.evidence_label)
        if (as_of - ticker.received_at).total_seconds() > self.rules.max_ticker_age_seconds:
            return TickerState(False, "ticker_older_than_allowed", received_at=ticker.received_at,
                               evidence_label=ticker.evidence_label)
        matched = quotes_by_symbol(ticker.entries, records)
        return TickerState(True, None, quotes=matched.quotes, received_at=ticker.received_at,
                           evidence_label=ticker.evidence_label)

    @staticmethod
    def _ticker_document(ticker: TickerSnapshot | None, mode: ScanMode, state: TickerState | None = None,
                         ) -> dict[str, Any]:
        if mode == "replay":
            return {"usable": False, "reason": "replay"}
        if ticker is None:
            return {"usable": False, "reason": "no_ticker_answer"}
        document = {"usable": bool(state and state.usable), "reason": None if state is None else state.reason,
                    "received_at_utc": utc_iso(ticker.received_at), "evidence_label": ticker.evidence_label,
                    "entries_in_response": ticker.entries_in_response, "entries_read": len(ticker.entries),
                    "rejected": [{"key": item.key, "reason": item.reason} for item in ticker.rejected[:50]],
                    "rejected_count": len(ticker.rejected)}
        if state is not None and state.usable:
            document["matched_pairs"] = len(state.quotes)
        return document

    def _evidence(self, source: str, catalog: Mapping[str, Any] | None, ticker: TickerSnapshot | None,
                  runs: set[int]) -> str:
        labels = set()
        if source == "synthetic":
            labels.add("synthetic_fixture")
        if ticker is not None:
            labels.add(ticker.evidence_label)
        if runs:
            labels.update(run.evidence_label for run in self.store.runs() if run.run_id in runs)
        known = [label for label in _EVIDENCE_ORDER if label in labels and label in EVIDENCE_LABELS]
        if known:
            return known[0]
        return "local_archive_file" if source == "kraken_ohlcvt_archive" else "live_public"


__all__ = ["OPTIMISM", "CoinScanner", "ScanResult"]
