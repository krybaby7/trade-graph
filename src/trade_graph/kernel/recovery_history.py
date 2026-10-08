"""Authenticated accepted gaps in historic price/reporting observations only.

This describes retained evidence; it never waives a financial checkpoint, creates
replacement observations, resets a portfolio/budget or grants AI/live authority.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from trade_graph.domain.clock import parse_utc
from trade_graph.domain.errors import AuthorityDenied, StaleState
from trade_graph.kernel.runtime_manifest import canonical_json

MAXIMUM_INCIDENT_BYTES = 131072
INCIDENT_FIELDS = {"schema_version", "incident_id", "portfolio_id", "classification", "cause", "backup_cutoff_at",
    "affected_interval", "missing_records", "unknown_additional_loss", "uncertainty_summary", "accepted_effect",
    "financial_ai_history_verified", "preserved_continuity_evidence_sha256", "classification_evidence_sha256",
    "previous_evaluation_period", "new_evaluation_period", "old_run_retained", "account_reset"}
CATEGORIES = {"valuation_marks": "valuation_mark", "fx_rates": "fx_reporting_observation",
              "observations": "public_price_reporting_observation"}
IDENTITIES = {"valuation_marks": "mark_id", "fx_rates": "rate_id", "observations": "observation_id"}


def _identifier(value):
    return type(value) is str and bool(re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value))


def _at(value, now):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("explicit UTC timestamp required")
    parsed = parse_utc(value)
    if parsed > now:
        raise ValueError("incident timestamp is in the future")
    return parsed


def validate_history_incident(document, *, now: datetime, database=None, expected_previous_period=None):
    """Validate scope; independent owner audits substantiate the asserted category.

    Static receipt validation excludes mutable current-row absence checks. During
    inspection the candidate must not already contain a supposedly missing ID.
    """
    try:
        if (type(document) is not dict or set(document) != INCIDENT_FIELDS
                or len(canonical_json(document).encode()) > MAXIMUM_INCIDENT_BYTES
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or not _identifier(document["incident_id"]) or not _identifier(document["portfolio_id"])
                or document["classification"] != "partial_price_reporting_history" or document["cause"] != "unknown"
                or document["accepted_effect"] != "historical_reporting_only"
                or document["financial_ai_history_verified"] is not True or document["old_run_retained"] is not True
                or document["account_reset"] is not False or type(document["unknown_additional_loss"]) is not bool
                or type(document["uncertainty_summary"]) is not str
                or not 1 <= len(document["uncertainty_summary"]) <= 2048
                or any(type(document[key]) is not str or not re.fullmatch(r"[0-9a-f]{64}", document[key]) for key in
                       ("preserved_continuity_evidence_sha256", "classification_evidence_sha256"))):
            raise ValueError
        cutoff = _at(document["backup_cutoff_at"], now)
        interval = document["affected_interval"]
        if type(interval) is not dict or set(interval) != {"start_at", "end_at"}:
            raise ValueError
        start, end = _at(interval["start_at"], now), _at(interval["end_at"], now)
        if not cutoff <= start <= end:
            raise ValueError
        previous, current = document["previous_evaluation_period"], document["new_evaluation_period"]
        for period in (previous, current):
            if (type(period) is not dict or set(period) != {"period_id", "started_at"}
                    or not _identifier(period["period_id"])):
                raise ValueError
        if (previous["period_id"] == current["period_id"] or current["started_at"] is None
                or _at(current["started_at"], now) < end
                or previous["started_at"] is not None and _at(previous["started_at"], now) > cutoff
                or expected_previous_period is not None and (
                    previous["period_id"] != expected_previous_period["period_id"]
                    or expected_previous_period["started_at"] is not None and previous != expected_previous_period)):
            raise ValueError
        records = document["missing_records"]
        if type(records) is not list or not 1 <= len(records) <= 1000:
            raise ValueError
        seen, rowids = set(), set()
        for record in records:
            if (type(record) is not dict or set(record) != {"table", "category", "record_id", "rowid", "observed_at"}
                    or record["table"] not in CATEGORIES or record["category"] != CATEGORIES[record["table"]]
                    or type(record["record_id"]) is not str or not 1 <= len(record["record_id"]) <= 512
                    or record["rowid"] is not None and (type(record["rowid"]) is not int or record["rowid"] <= 0)):
                raise ValueError
            identity = record["table"], record["record_id"]
            rowid = record["table"], record["rowid"]
            if identity in seen or record["rowid"] is not None and rowid in rowids:
                raise ValueError
            seen.add(identity)
            rowids.add(rowid)
            if record["observed_at"] is not None and not start <= _at(record["observed_at"], now) <= end:
                raise ValueError
            if database is not None:
                table, key = record["table"], IDENTITIES[record["table"]]
                if database.execute(f"SELECT 1 FROM {table} WHERE {key}=? LIMIT 1",
                                    (record["record_id"],)).fetchone():
                    raise ValueError
        if database is not None:
            portfolio = database.execute("SELECT mode FROM portfolios WHERE portfolio_id=?",
                                         (document["portfolio_id"],)).fetchone()
            if portfolio is None or portfolio["mode"] != "paper":
                raise ValueError
        return json.loads(canonical_json(document))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise AuthorityDenied("accepted reporting-history incident scope, period or evidence refused") from None


def prior_period(history, portfolio_id, *, witness, exclude_operation=None, proposed_incident=None):
    """Read authenticated historical metadata without interpreting missing payloads."""
    from trade_graph.kernel.financial_recovery import _row, load_recovery
    portfolio = history.database.execute("SELECT experiment_id FROM portfolios WHERE portfolio_id=?",
                                         (portfolio_id,)).fetchone()
    if portfolio is None:
        raise AuthorityDenied("recovery history requires a retained portfolio")
    current = {"period_id": portfolio["experiment_id"], "started_at": None}
    incident_ids, period_ids = set(), {current["period_id"]}
    rows = history.database.execute("""SELECT operation_id,recovery_sha256 FROM protected_financial_recoveries
        LIMIT 129""").fetchall()
    if len(rows) > 128:
        raise StaleState("recovery incident history exceeds bound")
    by_hash = {row["recovery_sha256"]: row for row in rows}
    for digest in witness.get("recoveries", []):
        if digest not in by_hash:
            raise StaleState("recovery period lost its authenticated receipt")
        row = by_hash[digest]
        if row["operation_id"] == exclude_operation:
            continue
        payload = load_recovery(history, _row(history, row["operation_id"]))
        incident = payload["approval"].get("history_incident")
        if incident and incident["portfolio_id"] == portfolio_id:
            if (incident["previous_evaluation_period"]["period_id"] != current["period_id"]
                    or current["started_at"] is not None and incident["previous_evaluation_period"] != current):
                raise StaleState("recovery evaluation-period lineage refused")
            if (incident["incident_id"] in incident_ids
                    or incident["new_evaluation_period"]["period_id"] in period_ids):
                raise StaleState("recovery incident or evaluation-period identity was reused")
            incident_ids.add(incident["incident_id"])
            current = incident["new_evaluation_period"]
            period_ids.add(current["period_id"])
    if proposed_incident is not None and (proposed_incident.get("incident_id") in incident_ids
            or proposed_incident.get("new_evaluation_period", {}).get("period_id") in period_ids):
        raise AuthorityDenied("recovery requires a fresh incident and new evaluation-period identity")
    return current


def verified_recovery_history(financial, portfolio_id: str) -> dict:
    """Read-only dashboard projection; no full financial rescan or authority grant."""
    from trade_graph.kernel.financial_recovery import _row, load_recovery
    history = financial.history
    with history.witness_lock() as directory:
        with financial.database.snapshot():
            witness = history._read_witness(directory)
            if witness is None:
                raise StaleState("recovery history lacks its independent witness")
            history.verified_scopes(witness)
            prior_period(history, portfolio_id, witness=witness)
            incidents, previous, current = [], [], None
            by_hash = {row["recovery_sha256"]: row["operation_id"] for row in financial.database.execute(
                "SELECT recovery_sha256,operation_id FROM protected_financial_recoveries LIMIT 129")}
            for digest in witness.get("recoveries", []):
                payload = load_recovery(history, _row(history, by_hash[digest]))
                incident = payload["approval"].get("history_incident")
                if incident and incident["portfolio_id"] == portfolio_id:
                    incidents.append({"incident": incident, "recovery_sha256": digest,
                                      "recorded_at": payload["created_at"]})
                    previous.append(incident["previous_evaluation_period"])
                    current = incident["new_evaluation_period"]
            return {"status": "PARTIAL_HISTORY" if incidents else "NO_RECORDED_INCIDENT", "incidents": incidents,
                "active_evaluation_period": current, "prior_evaluation_periods": previous,
                "account_reset": False, "old_run_retained": True}
