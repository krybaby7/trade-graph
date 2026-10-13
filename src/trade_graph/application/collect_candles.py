"""Explicitly requested public Kraken collection into the candle store (docs/CANDLE-STORE.md, docs/PAIR-CATALOG.md,
docs/KRAKEN-CANDLE-COLLECTION.md).

``refresh_catalog`` (slice A) fetches the Kraken pair list, marks stablecoins and yield tokens, keeps the
USD-quoted pairs as a dated catalog observation and records the whole run.

``collect_candles`` (slice B) reads the newest stored catalog, plans one request per (pair, interval), makes the
requests through the rate limiter, saves each series on its own and records the run with a full report.
Nothing here uses a key, a paid service or an order endpoint.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from trade_graph.adapters.market.kraken_candles import OHLC_URL
from trade_graph.adapters.market.kraken_catalog import (
    ASSET_PAIRS_URL,
    CATALOG_MAXIMUM_RESPONSE_BYTES,
    CatalogFetchError,
    KrakenPairCatalog,
)
from trade_graph.adapters.market.public import TextTransport
from trade_graph.adapters.market.rate_limit import KrakenRestLimiter
from trade_graph.application.candle_collection_plan import (
    BYTES_PER_ROW,
    CollectionPlan,
    CollectionScope,
    cadence_report,
    plan_collection,
    storage_projection,
)
from trade_graph.application.candle_collection_run import CollectionRun
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.pair_classification import (
    CLASSIFICATION_RULES_VERSION,
    VERIFICATION_NOTE,
    classification_summary,
    classify_pair,
)
from trade_graph.contracts.candles import CandleSource, to_unix
from trade_graph.contracts.pair_catalog import PairRecord
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

CATALOG_QUOTE = "USD"
CATALOG_EVIDENCE_LABELS = ("synthetic_fixture", "recorded_public_fixture", "live_public_smoke", "live_public")
COLLECT_EVIDENCE_LABELS = CATALOG_EVIDENCE_LABELS
MAXIMUM_REQUESTS_PER_RUN = 10_000
MAXIMUM_REJECTED_REPORTED = 200
_FAILURES = {"request_failed": "public_catalog_request_failed", "response_invalid": "public_catalog_invalid"}


def _aware_now(clock: Clock) -> datetime:
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValidationFailure("the catalog run requires an aware clock")
    return now.astimezone(UTC)


def _failed_report(evidence_label: str, error: str) -> dict:
    return {
        "status": "failed",
        "error": error,
        "evidence_label": evidence_label,
        "source_url": ASSET_PAIRS_URL,
        "request_count": 1,
        "paid_calls_enabled": False,
        "live_trading_enabled": False,
        "rules_version": CLASSIFICATION_RULES_VERSION,
        "price_peg_check": "not_applied",
        "observed_at_utc": None,
        "catalog_row_added": False,
    }


def refresh_catalog(store: CandleStore, clock: Clock, transport: TextTransport, evidence_label: str) -> dict:
    """Fetch, classify and store the USD pair catalog, and record the run.

    The run is stored before the request, so a crash leaves a visible interrupted run. Unexpected failures
    become a failed run with a fixed category (``public_catalog_request_failed``, ``public_catalog_invalid``
    or ``catalog_persistence_failed``); response text never enters the report. A malformed pair makes the run
    ``partial`` and is listed with its reason. An unchanged pair list adds no catalog row.
    """
    if evidence_label not in CATALOG_EVIDENCE_LABELS:
        raise ValidationFailure("unsupported evidence label for a catalog run")
    started_at = _aware_now(clock)
    run_id = store.begin_run(
        kind="catalog", source="synthetic" if evidence_label == "synthetic_fixture" else "kraken_public_ohlc",
        evidence_label=evidence_label, started_at=started_at,
        parameters={
            "endpoint": ASSET_PAIRS_URL, "quote": CATALOG_QUOTE, "rules_version": CLASSIFICATION_RULES_VERSION,
            "maximum_response_bytes": CATALOG_MAXIMUM_RESPONSE_BYTES, "paid_calls_enabled": False,
            "live_trading_enabled": False,
        })
    report = _build_report(store, clock, transport, evidence_label, run_id)
    store.finish_run(run_id, finished_at=_aware_now(clock), status=report["status"], report=report)
    return report


def _build_report(store: CandleStore, clock: Clock, transport: TextTransport, evidence_label: str,
                  run_id: int) -> dict:
    try:
        fetched = KrakenPairCatalog(transport, clock).fetch()
    except CatalogFetchError as error:
        return _failed_report(evidence_label, _FAILURES[error.category])
    except ValidationFailure:
        return _failed_report(evidence_label, "public_catalog_invalid")
    except Exception:
        return _failed_report(evidence_label, "public_catalog_request_failed")
    catalog = fetched.catalog
    records = [classify_pair(pair) for pair in catalog.pairs]
    usd = [record for record in records if record.usd_quoted]
    if not usd:
        return _failed_report(evidence_label, "public_catalog_invalid")
    try:
        added = store.save_catalog(
            [record.model_dump(mode="json") for record in usd], observed_at=fetched.received_at, run_id=run_id,
            rules_version=CLASSIFICATION_RULES_VERSION)
    except Exception:
        return _failed_report(evidence_label, "catalog_persistence_failed")
    other_quotes = Counter(pair.quote for pair in catalog.pairs if not pair.usd_quoted)
    return {
        **_failed_report(evidence_label, ""),
        "status": "partial" if catalog.partial else "ok",
        "error": None,
        "observed_at_utc": utc_iso(fetched.received_at),
        "catalog_row_added": added,
        "pairs_in_response": catalog.pairs_in_response,
        "spot_currency_pairs": len(catalog.pairs),
        "usd_pairs": len(usd),
        "other_quote_pairs": sum(other_quotes.values()),
        "other_quotes": dict(sorted(other_quotes.items())),
        "out_of_scope_pairs": catalog.out_of_scope,
        "rejected_count": len(catalog.rejected),
        "rejected_pairs": [{"pair_key": item.pair_key, "reason": item.reason}
                           for item in catalog.rejected[:MAXIMUM_REJECTED_REPORTED]],
        "rejected_truncated": len(catalog.rejected) > MAXIMUM_REJECTED_REPORTED,
        "rejected_by_reason": dict(sorted(Counter(item.reason for item in catalog.rejected).items())),
        **classification_summary(usd),
        "verification_note": VERIFICATION_NOTE,
    }


def stored_catalog(store: CandleStore, *, as_of: datetime) -> list[PairRecord] | None:
    """The newest catalog observed at or before ``as_of``, or None when none was observed yet (unknown)."""
    observation = store.latest_catalog(as_of=as_of)
    if observation is None:
        return None
    return [PairRecord.model_validate(pair) for pair in observation.pairs]


# --- candle collection (slice B) --------------------------------------------------------------------------------------


def collect_candles(store: CandleStore, clock: Clock, transport: TextTransport, limiter: KrakenRestLimiter,
                    scope: CollectionScope, max_requests: int, evidence_label: str) -> dict:
    """Collect the planned series from the newest stored catalog and return (and record) the run report.

    ``transport`` is used as given: every request takes a ``limiter`` slot first, here. A catalog must have been
    stored already (``refresh_catalog``); without one the run is recorded as failed and nothing is requested.
    A synthetic evidence label writes ``synthetic`` series so scripted data never joins the real ones.
    ``max_requests`` bounds the network calls of this run, a repeated throttled request included.
    """
    if evidence_label not in COLLECT_EVIDENCE_LABELS:
        raise ValidationFailure("unsupported evidence label for a candle collection run")
    if type(max_requests) is not int or not 1 <= max_requests <= MAXIMUM_REQUESTS_PER_RUN:
        raise ValidationFailure(f"the request limit must be a whole number from 1 to {MAXIMUM_REQUESTS_PER_RUN}")
    if not isinstance(scope, CollectionScope):
        raise ValidationFailure("a collection scope is required")
    started_at = _aware_now(clock)
    source: CandleSource = "synthetic" if evidence_label == "synthetic_fixture" else "kraken_public_ohlc"
    observation = store.latest_catalog(as_of=started_at)
    records = [] if observation is None else [PairRecord.model_validate(pair) for pair in observation.pairs]
    newest = {(item.symbol, item.interval_minutes): to_unix(item.last_open_utc) for item in store.series()
              if item.source == source and item.last_open_utc is not None}
    plan = plan_collection(records, scope, newest)
    run_id = store.begin_run(
        kind="public_collect", source=source, evidence_label=evidence_label, started_at=started_at,
        parameters={
            "endpoint": OHLC_URL, "scope": scope.to_document(), "max_requests": max_requests,
            "planned_series": len(plan.series), "paid_calls_enabled": False, "live_trading_enabled": False,
            "limiter": {"ceiling": limiter.ceiling, "assumed_decay_per_second": limiter.snapshot()[
                "assumed_decay_per_second"]},
            "catalog_observed_at_utc": None if observation is None else utc_iso(observation.observed_at_utc),
            "catalog_rules_version": None if observation is None else observation.rules_version,
        })
    outcome = CollectionRun(store, clock, transport, limiter, plan.series, max_requests=max_requests, source=source,
                            run_id=run_id).execute() if observation is not None and plan.series else None
    finished_at = _aware_now(clock)
    report = _collection_report(store, limiter, scope, plan, observation, records, outcome,
                                max_requests=max_requests, evidence_label=evidence_label, source=source,
                                run_id=run_id, started_at=started_at, finished_at=finished_at)
    store.finish_run(run_id, finished_at=finished_at, status=report["status"], report=report)
    return report


def _collection_report(store: CandleStore, limiter: KrakenRestLimiter, scope: CollectionScope, plan: CollectionPlan,
                       observation, records: list[PairRecord], outcome, *, max_requests: int, evidence_label: str,
                       source: CandleSource, run_id: int, started_at: datetime, finished_at: datetime) -> dict:
    entries = [] if outcome is None else outcome.entries
    stop = None if outcome is None else outcome.stop
    ok = [entry for entry in entries if entry["status"] == "ok"]
    failed = [entry for entry in entries if entry["status"] == "failed"]
    unreached = [[item.record.symbol, item.interval_minutes] for item in plan.series[len(entries):]]
    error = None
    if observation is None:
        status, error = "failed", "no_catalog"
    elif not plan.series:
        status, error = "failed", "nothing_to_collect"
    elif not ok:
        status = "failed"
    elif stop is not None or failed or unreached or plan.unknown_symbols:
        status = "partial"
    else:
        status = "ok"
    series_by_interval: dict[int, int] = {}
    for item in plan.series:
        series_by_interval[item.interval_minutes] = series_by_interval.get(item.interval_minutes, 0) + 1
    inserted = sum(entry["inserted"] for entry in entries)
    stored_rows = store.database.query(
        "SELECT COUNT(*) AS n FROM market_candles m JOIN candle_series s ON s.series_id = m.series_id "
        "WHERE s.source = ?", (source,))[0]["n"]
    return {
        "status": status,
        "stop_reason": None if stop is None else stop["reason"],
        "stopped": stop,
        "error": error,
        "run_id": run_id,
        "evidence_label": evidence_label,
        "source": source,
        "started_at_utc": utc_iso(started_at),
        "finished_at_utc": utc_iso(finished_at),
        "paid_calls_enabled": False,
        "live_trading_enabled": False,
        "catalog": None if observation is None else {
            "observed_at_utc": utc_iso(observation.observed_at_utc), "rules_version": observation.rules_version,
            "usd_pairs": len(records), "eligible_pairs": sum(record.eligible_for_collection for record in records),
        },
        "scope": scope.to_document(),
        "requests": {
            "made": 0 if outcome is None else outcome.requests_made, "max": max_requests,
            "throttle_retries": 0 if outcome is None else outcome.throttle_retries, "limiter": limiter.snapshot(),
        },
        "counts": {
            "planned_series": len(plan.series), "attempted": len(entries), "ok": len(ok), "failed": len(failed),
            "not_reached": len(unreached),
        },
        "totals": _totals(entries),
        "by_interval": {str(interval): _by_interval(interval, plan, entries)
                        for interval in sorted(series_by_interval)},
        "series": entries,
        "not_reached": unreached,
        "skipped_pairs": [{"symbol": item.symbol, "reasons": list(item.reasons)} for item in plan.skipped],
        "unknown_symbols": list(plan.unknown_symbols),
        "storage": {
            "bytes_per_row_estimate": BYTES_PER_ROW,
            "this_run": {"rows_inserted": inserted, "estimated_bytes_added": inserted * BYTES_PER_ROW},
            "store_total": {"rows": stored_rows, "estimated_bytes": stored_rows * BYTES_PER_ROW},
            "per_quarter": storage_projection(series_by_interval),
        },
        "cadence": cadence_report(series_by_interval, ceiling=limiter.ceiling,
                                  decay_per_second=limiter.decay_per_second),
    }


def _totals(entries: list[dict]) -> dict:
    return {
        "rows_received": sum(entry["rows_received"] for entry in entries),
        "candles_returned": sum(entry["candles_returned"] for entry in entries),
        "inserted": sum(entry["inserted"] for entry in entries),
        "duplicates": sum(entry["duplicates"] for entry in entries),
        "revised": sum(entry["revised"] for entry in entries),
        "no_trade_candles": sum(entry["no_trade_candles"] for entry in entries),
        "series_with_gap": sum(1 for entry in entries if _status(entry) == "gap"),
        "series_without_data": sum(1 for entry in entries if _status(entry) == "no_data"),
        "series_unverified": sum(1 for entry in entries if _status(entry) == "unverified"),
    }


def _status(entry: dict) -> str | None:
    return None if entry["continuity"] is None else entry["continuity"]["status"]


def _by_interval(interval: int, plan: CollectionPlan, entries: list[dict]) -> dict:
    mine = [entry for entry in entries if entry["interval_minutes"] == interval]
    planned = sum(1 for item in plan.series if item.interval_minutes == interval)
    ok = sum(1 for entry in mine if entry["status"] == "ok")
    return {
        "planned_series": planned, "attempted": len(mine), "ok": ok, "failed": len(mine) - ok,
        "not_reached": planned - len(mine),
        **{key: sum(entry[key] for entry in mine) for key in (
            "rows_received", "candles_returned", "inserted", "duplicates", "revised", "no_trade_candles")},
        "series_with_gap": sum(1 for entry in mine if _status(entry) == "gap"),
    }
