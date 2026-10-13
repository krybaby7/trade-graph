"""Explicitly requested public Kraken collection into the candle store (docs/CANDLE-STORE.md, docs/PAIR-CATALOG.md).

Slice A, here: ``refresh_catalog`` fetches the Kraken pair list, marks stablecoins and yield tokens, keeps
the USD-quoted pairs as a dated catalog observation and records the whole run. The candle collection itself
is added to this module by the next slice. Nothing here uses a key, a paid service or an order endpoint.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from trade_graph.adapters.market.kraken_catalog import (
    ASSET_PAIRS_URL,
    CATALOG_MAXIMUM_RESPONSE_BYTES,
    CatalogFetchError,
    KrakenPairCatalog,
)
from trade_graph.adapters.market.public import TextTransport
from trade_graph.application.candle_store import CandleStore
from trade_graph.application.pair_classification import (
    CLASSIFICATION_RULES_VERSION,
    VERIFICATION_NOTE,
    classification_summary,
    classify_pair,
)
from trade_graph.contracts.pair_catalog import PairRecord
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

CATALOG_QUOTE = "USD"
CATALOG_EVIDENCE_LABELS = ("synthetic_fixture", "recorded_public_fixture", "live_public_smoke", "live_public")
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
