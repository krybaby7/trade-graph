"""Synthetic issuer fixtures verify closed gates, not actual live prerequisites."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord, paper_mandate, paper_owner_policy
from trade_graph.application.budget import BudgetGateway
from trade_graph.application.ledger import Ledger
from trade_graph.domain.clock import FrozenClock, utc_iso
from trade_graph.domain.money import Money
from trade_graph.live_gate import (
    LivePilotScope,
    OwnerPilotAuthorization,
    PinnedReadinessSource,
    ReadinessBundle,
    ReadinessEvidence,
    budget_configuration_digest,
    evaluate_live_enablement,
    evaluate_live_readiness,
)

ASSERTIONS = {
    "eligibility": ("current_legal_and_account_eligibility",),
    "funding": ("real_funded_account",),
    "key_permissions": ("read_trade_only", "withdrawals_absent"),
    "venue_metadata_fees": ("current_rules_precision_minimums_fees", "minimum_size_within_owner_allocation"),
    "read_only_reconciliation": ("authenticated_account_orders_fills_balances", "complete_reconciliation"),
    "broker_conformance": ("order_uncertainty_cancel_fill_restart", "current_adapter_wire_contract"),
    "pause_protection_recovery": ("pause_and_independent_recovery", "offline_protection_limitations_reviewed"),
    "host_backup_alerts": ("intended_host_verified", "backup_restore_verified", "alerts_verified"),
    "economic_evaluation": ("forward_evaluation_complete", "all_actual_costs_and_receipts_verified",
                            "paper_venue_differences_verified"),
}
ISSUERS = dict.fromkeys(ASSERTIONS, "venue")
ISSUERS.update(eligibility="eligibility", pause_protection_recovery="operations",
               host_backup_alerts="operations", economic_evaluation="economics")


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


class Fixture:
    def __init__(self, tmp_path):
        self.clock = FrozenClock(datetime(2026, 10, 2, 12, tzinfo=UTC))
        self.private = tmp_path / "private"
        self.private.mkdir(mode=0o700)
        self.db = Database(self.private / "fixture.sqlite")
        self.pid = Ledger(self.db, self.clock).create_portfolio(reporting_currency="EUR", mode="live")
        self.keys = {issuer: hashlib.sha256(f"SYNTHETIC TEST ISSUER ONLY {issuer}".encode()).digest()
                     for issuer in ("owner", "eligibility", "venue", "operations", "economics")}
        policy = paper_owner_policy(venues=["kraken"], symbols=["BTC/USD"]).model_copy(
            update={"live_enabled": True},
        )
        authority = AuthorityRecord(self.db, self.clock)
        authority.install_policy(policy, role="owner")
        authority.install_mandate(paper_mandate(self.pid, symbols=["BTC/USD"]), role="owner")
        BudgetGateway(self.db, self.clock).configure(
            deployment_id="fixture-deployment", currency="EUR", total=Decimal(5), period=Decimal(5),
            priority_reserve=Decimal(1), daily=Decimal("1.5"), root=Decimal("1.5"), roles={"trader": Decimal(5)},
        )
        self.scope = LivePilotScope(
            deployment_id="fixture-deployment", portfolio_id=self.pid, account_id="synthetic-private-account",
            venue="kraken", symbol="BTC/USD", policy_revision=policy.revision_id,
            policy_sha256=hashlib.sha256(policy.model_dump_json().encode()).hexdigest(),
            deployment_artifact_sha256="d" * 64, system_version_sha256="e" * 64,
        )
        self.db.execute("INSERT INTO active_versions "
                        "(portfolio_id, version_id, artifact_hash, fingerprint_json, activated_at) "
                        "VALUES (?, 'evidence-only', ?, 'fixture', ?)",
                        (self.pid, "e" * 64, utc_iso(self.clock.now())))
        self.db.execute("INSERT INTO pause_states VALUES (?, 'RUNNING', 'owner', 'fixture', 'portfolio', ?, ?, '{}')",
                        (self.pid, utc_iso(self.clock.now()), utc_iso(self.clock.now())))
        self.db.execute("INSERT INTO activity_events "
                        "(event_id, portfolio_id, kind, payload_json, created_at, hash) "
                        "VALUES ('reconciled', ?, 'execution_reconciliation_health', ?, ?, 'fixture-hash')",
                        (self.pid, json.dumps({"venue": "kraken", "account_id": self.scope.account_id,
                                             "mode": "live", "state": "complete"}), utc_iso(self.clock.now())))
        now = self.clock.now()
        self.document = {
            "schema_version": 1,
            "owner_authorization": {"issuer": "owner", "signature": "0" * 64, "payload": {
                "authorization_id": "synthetic-authorization", "action": "authorize_bounded_live_pilot",
                "scope": self.scope.model_dump(mode="json"), "allocation_origin": "owner_real_funds",
                "allocation": {"amount": "25", "currency": "USD"},
                "maximum_loss": {"amount": "5", "currency": "USD"},
                "operating_allowance": {"amount": "5", "currency": "EUR"},
                "daily_expense_limit": {"amount": "1.5", "currency": "EUR"},
                "budget_configuration_sha256": budget_configuration_digest(self.db, self.scope.deployment_id),
                "purpose": "supported_economics", "stop_profile": "MANAGE_ONLY", "leverage_allowed": False,
                "withdrawals_allowed": False, "verified_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
            }},
            "evidence": [{"issuer": ISSUERS[kind], "signature": "0" * 64, "payload": {
                "evidence_id": f"synthetic-{kind}", "kind": kind, "scope": self.scope.model_dump(mode="json"),
                "verification_basis": "actual_external_or_deployment_verification",
                "source_sha256": [hashlib.sha256(f"SYNTHETIC source {kind}".encode()).hexdigest()],
                "assertions": assertions, "verified_at": now.isoformat(),
                "expires_at": (now + timedelta(days=1)).isoformat(),
                **({"funded_amount": {"amount": "25", "currency": "USD"}} if kind == "funding" else {}),
                **({"economic_verdict": "supported", "forward_protocol_sha256": "a" * 64,
                    "forward_report_sha256": "b" * 64, "sealed_inventory_sha256": "c" * 64,
                    "registry_snapshot_sha256": "f" * 64} if kind == "economic_evaluation" else {}),
            }} for kind, assertions in ASSERTIONS.items()],
        }

    def evidence(self, kind):
        return next(entry["payload"] for entry in self.document["evidence"] if entry["payload"]["kind"] == kind)

    def source(self, *, sign=True):
        doc = copy.deepcopy(self.document)
        if sign:
            for entry in [doc["owner_authorization"], *doc["evidence"]]:
                owner = entry is doc["owner_authorization"]
                contract = OwnerPilotAuthorization if owner else ReadinessEvidence
                entry["payload"] = contract.model_validate(entry["payload"]).model_dump(mode="json")
                kind = "owner_authorization" if owner else entry["payload"]["kind"]
                message = b"trade-graph.live-readiness.v1\0" + kind.encode() + b"\0" + canonical(entry["payload"])
                entry["signature"] = hmac.new(self.keys[entry["issuer"]], message, hashlib.sha256).hexdigest()
        raw = canonical(doc)
        path = self.private / "immutable-bundle.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        return PinnedReadinessSource(path, hashlib.sha256(raw).hexdigest(), self.keys)

    def projection(self, **kwargs):
        return evaluate_live_readiness(self.db, self.clock, scope=kwargs.get("scope", self.scope),
                                       source=kwargs.get("source") or self.source())


@pytest.fixture
def fixture(tmp_path):
    value = Fixture(tmp_path)
    yield value
    value.db.close()


def test_valid_synthetic_issuer_scenario_is_advisory_and_never_enables_or_writes(fixture):
    before = list(fixture.db.execute("SELECT * FROM owner_policy_revisions"))
    source = fixture.source()
    original = source.path.read_bytes()
    result = fixture.projection(source=source)
    assert result["ready"] is False and result["status"] == "blocked"
    assert result["recorded_checks_passed"] is True
    assert result["enabled"] is False and result["diagnostic"] is False
    assert result["reasons"] == ["authoritative_upstream_economic_verification"]
    assert result["live_allocation"] == {"amount": "25", "currency": "USD"}
    assert list(fixture.db.execute("SELECT * FROM owner_policy_revisions")) == before
    assert source.path.read_bytes() == original
    assert fixture.scope.account_id not in json.dumps(result)
    assert str(fixture.private) not in json.dumps(result)


@pytest.mark.parametrize("allocation", ["NaN", "Infinity", "-1", "1e10000", 1.5, True, None])
def test_malformed_legacy_money_fails_closed_without_arithmetic_errors(allocation):
    result = evaluate_live_enablement({"live_allocation": allocation, "owner_confirmed": True})
    assert result["enabled"] is False
    assert result["live_allocation"] == "0"


def test_legacy_all_pass_flags_and_diagnostic_never_claim_verified_authority():
    record = {"eligibility_confirmed": True, "allocation_is_owner_set": True, "withdrawals_allowed": False,
              "read_only_reconciliation_passed": True, "operating_budget_set": True, "owner_confirmed": True,
              "diagnostic_pilot": True, "economic_verdict": "supported", "live_allocation": "25"}
    assert evaluate_live_enablement(record)["status"] == "unverified_record"
    assert evaluate_live_enablement(record)["enabled"] is False


@pytest.mark.parametrize("field", ["deployment_id", "portfolio_id", "account_id", "venue", "symbol",
                                   "policy_revision", "policy_sha256", "deployment_artifact_sha256",
                                   "system_version_sha256"])
def test_owner_and_evidence_cannot_be_replayed_in_other_scope(fixture, field):
    other = "0" * 64 if field.endswith("sha256") else "different"
    result = fixture.projection(scope=fixture.scope.model_copy(update={field: other}))
    assert result["ready"] is False
    assert "owner_scope" in result["reasons"]


@pytest.mark.parametrize("basis", ["unverified_imports", "synthetic"])
def test_signed_registry_imports_and_synthetic_checks_are_insufficient(fixture, basis):
    fixture.evidence("economic_evaluation")["verification_basis"] = basis
    result = fixture.projection()
    assert result["ready"] is False
    assert "economic_evaluation_actual_verification" in result["reasons"]


def test_diagnostic_grant_requires_other_actual_proofs_and_retains_insufficient_label(fixture):
    fixture.document["owner_authorization"]["payload"]["purpose"] = "diagnostic_execution_measurement"
    fixture.evidence("economic_evaluation")["economic_verdict"] = "insufficient_evidence"
    result = fixture.projection()
    assert result["recorded_checks_passed"] is True and result["diagnostic_authorization_recorded"] is True
    assert result["ready"] is False and result["diagnostic"] is False and result["enabled"] is False
    assert result["economic_evidence"] == "insufficient_evidence"
    fixture.evidence("read_only_reconciliation")["verification_basis"] = "synthetic"
    result = fixture.projection()
    assert result["ready"] is False and result["diagnostic"] is False


def test_insufficient_economics_requires_explicit_diagnostic_purpose(fixture):
    fixture.evidence("economic_evaluation")["economic_verdict"] = "insufficient_evidence"
    assert "economic_or_explicit_diagnostic" in fixture.projection()["reasons"]


@pytest.mark.parametrize("kind", ["owner", "funding", "economic_evaluation"])
def test_future_dated_approval_and_proofs_fail(fixture, kind):
    payload = fixture.document["owner_authorization"]["payload"] if kind == "owner" else fixture.evidence(kind)
    payload["verified_at"] = (fixture.clock.now() + timedelta(seconds=1)).isoformat()
    assert fixture.projection()["recorded_checks_passed"] is False


def test_reconciliation_age_is_bounded_even_when_signed_expiry_is_later(fixture):
    fixture.clock.advance(61)
    result = fixture.projection()
    assert "funding_freshness" in result["reasons"]
    assert "read_only_reconciliation_freshness" in result["reasons"]


def test_expired_grant_is_not_extended_by_fresh_proofs(fixture):
    fixture.document["owner_authorization"]["payload"]["verified_at"] = (
        fixture.clock.now() - timedelta(days=1)
    ).isoformat()
    fixture.document["owner_authorization"]["payload"]["expires_at"] = fixture.clock.now().isoformat()
    assert "owner_freshness" in fixture.projection()["reasons"]


def test_missing_and_duplicate_signed_evidence_fail(fixture):
    item = fixture.document["evidence"].pop()
    assert "complete_unique_evidence" in fixture.projection()["reasons"]
    fixture.document["evidence"].extend([item, item])
    assert "complete_unique_evidence" in fixture.projection()["reasons"]


def test_signature_issuer_separation_and_pinned_document_content(fixture):
    source = fixture.source()
    doc = json.loads(source.path.read_text())
    doc["owner_authorization"]["payload"]["allocation"]["amount"] = "10000"
    source.path.write_bytes(canonical(doc))
    assert fixture.projection(source=source)["ready"] is False
    # Even pinning altered bytes does not repair the owner MAC.
    altered = PinnedReadinessSource(source.path, hashlib.sha256(source.path.read_bytes()).hexdigest(), fixture.keys)
    assert fixture.projection(source=altered)["checks"] == {"protected_evidence": False}
    fixture.document["evidence"][0]["issuer"] = "economics"
    assert fixture.projection()["checks"] == {"protected_evidence": False}


def test_signing_keys_are_frozen_and_common_key_cannot_merge_authority(fixture):
    source = fixture.source()
    fixture.keys["owner"] = b"z" * 32
    assert fixture.projection(source=source)["recorded_checks_passed"] is True
    keys = {issuer: b"z" * 32 for issuer in source.issuer_keys}
    unsafe = PinnedReadinessSource(source.path, source.bundle_sha256, keys)
    assert fixture.projection(source=unsafe)["checks"] == {"protected_evidence": False}


@pytest.mark.parametrize("permission", [0o644, 0o666])
def test_private_bundle_permissions_are_required(fixture, permission):
    source = fixture.source()
    source.path.chmod(permission)
    assert fixture.projection(source=source)["checks"] == {"protected_evidence": False}


def test_private_directory_symlinks_and_special_files_are_rejected(fixture):
    source = fixture.source()
    fixture.private.chmod(0o755)
    assert fixture.projection(source=source)["checks"] == {"protected_evidence": False}
    fixture.private.chmod(0o700)
    symlink = fixture.private / "link.json"
    symlink.symlink_to(source.path)
    unsafe = PinnedReadinessSource(symlink, source.bundle_sha256, fixture.keys)
    assert fixture.projection(source=unsafe)["checks"] == {"protected_evidence": False}


@pytest.mark.parametrize("amount", ["1e10000", "1e-10000", "0", "-1", "NaN", "Infinity", 1.5])
def test_tiny_funding_input_cannot_expand_before_signature_validation(fixture, amount):
    fixture.evidence("funding")["funded_amount"] = {"amount": amount, "currency": "USD"}
    source = fixture.source(sign=False)
    assert fixture.projection(source=source)["checks"] == {"protected_evidence": False}


def test_current_scoped_reconciliation_proof_is_required(fixture):
    fixture.db.execute("DELETE FROM activity_events WHERE kind = 'execution_reconciliation_health'")
    result = fixture.projection()
    assert "current_complete_account_history" in result["reasons"]
    assert result["recorded_checks_passed"] is False


def add_usage(fixture, amount, state="COMMITTED", *, synthetic=0):
    fixture.db.execute(
        "INSERT INTO budget_reservations "
        "(reservation_id, deployment_id, role, amount, currency, state, price_card_id, purpose, synthetic, "
        "created_at, updated_at) VALUES (?, ?, 'trader', ?, 'EUR', ?, 'fixture', 'fixture', ?, ?, ?)",
        (f"fixture-{amount}-{state}-{synthetic}", fixture.scope.deployment_id, amount, state, synthetic,
         utc_iso(fixture.clock.now()), utc_iso(fixture.clock.now())),
    )


def test_actual_daily_room_and_priority_reserve_are_preserved(fixture):
    add_usage(fixture, "1.5")
    result = fixture.projection()
    assert "remaining_current_day" in result["reasons"]
    assert "remaining_real_allowance" not in result["reasons"]
    fixture.db.execute("DELETE FROM budget_reservations")
    add_usage(fixture, "4")
    assert "remaining_real_allowance" in fixture.projection()["reasons"]


def test_current_period_and_unknown_usage_are_not_bypassed_by_invoice_labels(fixture):
    fixture.db.execute("UPDATE deployment_budget SET period_allowance = '1'")
    fixture.document["owner_authorization"]["payload"]["budget_configuration_sha256"] = (
        budget_configuration_digest(fixture.db, fixture.scope.deployment_id)
    )
    add_usage(fixture, "1", "UNCERTAIN")
    result = fixture.projection()
    assert "remaining_current_period" in result["reasons"]
    assert "no_unresolved_real_usage" in result["reasons"]


def test_synthetic_usage_never_deducts_actual_expense_room(fixture):
    add_usage(fixture, "10000", "UNCERTAIN", synthetic=1)
    assert fixture.projection()["recorded_checks_passed"] is True


def test_near_limit_decimal_tail_is_compared_exactly(fixture):
    cap = "100000000000000.123456789123456789"
    fixture.db.execute("UPDATE deployment_budget SET total_allowance = ?, period_allowance = ?, "
                       "daily_limit = ?, priority_reserve = '0'", (cap, cap, cap))
    policy = AuthorityRecord(fixture.db, fixture.clock).active_policy().model_copy(update={
        "revision_id": "large-synthetic-cap", "monthly_operating": Money(amount=cap, currency="EUR"),
        "daily_paid_limit": Money(amount=cap, currency="EUR"),
    })
    AuthorityRecord(fixture.db, fixture.clock).install_policy(policy, role="owner")
    fixture.scope = fixture.scope.model_copy(update={
        "policy_revision": policy.revision_id,
        "policy_sha256": hashlib.sha256(policy.model_dump_json().encode()).hexdigest(),
    })
    grant = fixture.document["owner_authorization"]["payload"]
    grant["scope"] = fixture.scope.model_dump(mode="json")
    for entry in fixture.document["evidence"]:
        entry["payload"]["scope"] = fixture.scope.model_dump(mode="json")
    grant["operating_allowance"] = grant["daily_expense_limit"] = {"amount": cap, "currency": "EUR"}
    grant["budget_configuration_sha256"] = budget_configuration_digest(fixture.db, fixture.scope.deployment_id)
    add_usage(fixture, "100000000000000.123456789123456788")
    assert fixture.projection()["recorded_checks_passed"] is True
    add_usage(fixture, "0.000000000000000001")
    assert "remaining_current_day" in fixture.projection()["reasons"]


def test_signed_missing_assertions_and_ledger_source_binding_are_rejected(fixture):
    fixture.evidence("key_permissions")["assertions"] = ["read_trade_only"]
    fixture.evidence("economic_evaluation")["registry_snapshot_sha256"] = None
    result = fixture.projection()
    assert "key_permissions_assertions" in result["reasons"]
    assert "economic_source_bindings" in result["reasons"]


def test_signed_native_allocation_requires_real_funding_currency_and_size(fixture):
    fixture.evidence("funding")["funded_amount"] = {"amount": "24", "currency": "USD"}
    assert "real_allocation_funded" in fixture.projection()["reasons"]
    fixture.evidence("funding")["funded_amount"] = {"amount": "25", "currency": "EUR"}
    assert "real_allocation_funded" in fixture.projection()["reasons"]


def test_virtual_capital_and_reset_cannot_create_live_readiness(fixture):
    fixture.db.execute("UPDATE portfolios SET mode = 'paper' WHERE portfolio_id = ?", (fixture.pid,))
    Ledger(fixture.db, fixture.clock).deposit(fixture.pid, "USD", Decimal("10000"), "virtual-open")
    result = fixture.projection()
    assert "separate_live_portfolio" in result["reasons"]
    fixture.document["owner_authorization"]["payload"]["allocation_origin"] = "paper_reset"
    with pytest.raises(ValueError):
        ReadinessBundle.model_validate(fixture.document)


def test_owner_policy_revocation_version_change_and_budget_change_invalidate_bundle(fixture):
    source = fixture.source()
    authority = AuthorityRecord(fixture.db, fixture.clock)
    authority.install_policy(paper_owner_policy(revision_id="revoked"), role="owner")
    assert "explicit_live_owner_policy" in fixture.projection(source=source)["reasons"]
    fixture.db.execute("UPDATE active_versions SET artifact_hash = ?", ("f" * 64,))
    fixture.db.execute("UPDATE role_allocations SET amount = '4'")
    result = fixture.projection(source=source)
    assert "current_system_version" in result["reasons"]
    assert "owner_budget_identity" in result["reasons"]


def test_actual_usage_holds_pause_unknown_orders_and_account_health_override_signed_proofs(fixture):
    fixture.db.execute("UPDATE pause_states SET profile = 'MANAGE_ONLY' WHERE portfolio_id = ?", (fixture.pid,))
    fixture.db.execute("INSERT INTO order_intents VALUES ('unknown', ?, 'cid', 'UNKNOWN', 'BTC/USD', '{}', ?, ?)",
                       (fixture.pid, utc_iso(fixture.clock.now()), utc_iso(fixture.clock.now())))
    fixture.db.execute("INSERT INTO activity_events "
                       "(event_id, portfolio_id, kind, payload_json, created_at, hash) "
                       "VALUES ('health', ?, 'execution_reconciliation_health', ?, ?, 'fixture-hash')",
                       (fixture.pid, json.dumps({"venue": "kraken", "account_id": fixture.scope.account_id,
                                                "mode": "live", "state": "incomplete"}), utc_iso(fixture.clock.now())))
    result = fixture.projection()
    assert "persisted_running_profile" in result["reasons"]
    assert "no_unknown_orders" in result["reasons"]
    assert "current_complete_account_history" in result["reasons"]


def test_pending_owner_command_and_invoice_discrepancy_are_not_green(fixture):
    fixture.db.execute("INSERT INTO dashboard_commands VALUES ('pending', ?, 'hash', NULL, 'PROCESSING', ?)",
                       (f"owner:{fixture.scope.deployment_id}", utc_iso(fixture.clock.now())))
    fixture.db.execute("INSERT INTO invoice_reconciliations "
                       "VALUES ('difference', ?, 'invoice', '1', '0', '1', 'EUR', ?)",
                       (fixture.scope.deployment_id, utc_iso(fixture.clock.now())))
    result = fixture.projection()
    assert "no_pending_owner_commands" in result["reasons"]
    assert "no_invoice_differences" in result["reasons"]


def test_unknown_order_in_same_live_account_blocks_other_portfolio_pilot(fixture):
    sibling = Ledger(fixture.db, fixture.clock).create_portfolio(reporting_currency="EUR", mode="live")
    payload = {"venue": "kraken", "account_id": fixture.scope.account_id, "mode": "live"}
    fixture.db.execute("INSERT INTO order_intents VALUES ('other-unknown', ?, 'other-cid', 'UNKNOWN', "
                       "'BTC/USD', ?, ?, ?)",
                       (sibling, json.dumps(payload), utc_iso(fixture.clock.now()), utc_iso(fixture.clock.now())))
    assert "no_unknown_orders" in fixture.projection()["reasons"]
