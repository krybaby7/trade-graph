"""Externally protected commissioning evidence for one bounded live installation.

This is a trusted owner distribution reader, never a model/HTTP write route.
Commissioning replaces advisory declarations with independently issued reviews
of pinned complete evidence. Native read receipts are also replayed locally.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel.deployment_image import OWNER_MOUNT, assert_boot_environment, read_owner_file
from trade_graph.kernel.runtime_manifest import canonical_json, protected_package_sha256

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Filename = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")]
_DOMAIN = b"trade-graph.live-commission.v1\0"
_ASSERTIONS = {
    "venue": {"eligibility_and_owner_identity", "funding_and_complete_account_reconciliation",
              "read_trade_key_without_withdrawals", "order_cancel_uncertainty_restart_conformance",
              "fee_precision_and_owned_quantity_protection"},
    "operations": {"owner_designated_intended_host", "immutable_image_and_distribution",
                   "actual_os_child_isolation_attacks", "independent_position_management_and_restart",
                   "off_host_restore_and_delivered_alerts", "restricted_kraken_and_public_fx_egress_proxy"},
    "economics": {"complete_forward_sources_and_baseline_execution", "all_role_usage_and_costs_resolved",
                  "paper_live_scope_mapping_and_differences"},
}


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommissionSourcePin(_Contract):
    filename: Filename
    sha256: Fingerprint


class CommissionReview(_Contract):
    """An independent issuer's attestation after reviewing actual retained facts.

    An attestor is responsible for the external facts, not an AI-generated
    declaration. Its independent key and reviewed bytes are owner distributed.
    Synthetic preparation may never be signed as actual commissioning.
    """

    issuer: Literal["venue", "operations", "economics"]
    basis: Literal["actual_external_or_deployment_verification", "synthetic"]
    scope_sha256: Fingerprint
    readiness_bundle_sha256: Fingerprint
    runtime_manifest_sha256: Fingerprint
    protected_package_sha256: Fingerprint
    authorization_id: str = Field(min_length=1, max_length=128)
    verified_at: datetime
    expires_at: datetime
    assertions: tuple[str, ...] = Field(min_length=1, max_length=16)
    sources: tuple[CommissionSourcePin, ...] = Field(min_length=1, max_length=64)
    verdict: Literal["passed", "refused"]

    @field_validator("verified_at", "expires_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("aware commissioning timestamps required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def window(self):
        if not timedelta(0) < self.expires_at - self.verified_at <= timedelta(days=31):
            raise ValueError("commission review lasts at most 31 days")
        return self


class SignedCommissionReview(_Contract):
    payload: CommissionReview
    signature: Fingerprint


class LiveCommissionProfile(_Contract):
    schema_version: Literal[1]
    scope_sha256: Fingerprint
    authorization_id: str = Field(min_length=1, max_length=128)
    readiness_bundle_sha256: Fingerprint
    runtime_manifest_sha256: Fingerprint
    protected_package_sha256: Fingerprint
    venue_observation_path: Path
    venue_observation_sha256: Fingerprint
    proxy_ipv4: str
    proxy_port: int = Field(ge=1024, le=65535)
    reviews: tuple[CommissionSourcePin, ...] = Field(min_length=3, max_length=3)
    kraken_key_sha256: Fingerprint
    kraken_secret_sha256: Fingerprint
    live_config_sha256: Fingerprint

    @field_validator("proxy_ipv4")
    @classmethod
    def private_proxy(cls, value):
        address = ipaddress.IPv4Address(value)
        if (str(address) != value or not address.is_private or address.is_loopback
                or address.is_link_local or address.is_unspecified or address.is_multicast):
            raise ValueError("fixed separately reviewed private proxy required")
        return value

    @field_validator("venue_observation_path")
    @classmethod
    def private_path(cls, value):
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("absolute retained venue evidence path required")
        return value

    @property
    def proxy_url(self):
        return f"http://{self.proxy_ipv4}:{self.proxy_port}"


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def assert_live_process_boundary(directory: Path, manifest_sha256: str) -> None:
    """Refuse a normal CLI interpreter, regardless of a commissioning document.

    The independent owner must additionally inspect the actual container,
    mounts, network proxy and attacks before issuing the operations review.
    The model child still uses the existing syscall-denying exec supervisor.
    """
    if directory != Path(OWNER_MOUNT):
        raise AuthorityDenied("live startup requires the protected read-only owner mount")
    manifest = assert_boot_environment()
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if (manifest.sha256 != manifest_sha256 or os.getuid() == 0
            or status.get("NoNewPrivs", "").strip() != "1"
            or status.get("Seccomp", "").strip() != "2"
            or any(int(status.get(name, "1").strip(), 16) != 0
                   for name in ("CapEff", "CapPrm", "CapBnd", "CapAmb"))):
        raise AuthorityDenied("actual live parent OS confinement differs from commissioned boundary")


@dataclass(frozen=True)
class PinnedLiveCommission:
    directory: Path
    profile_sha256: str

    def load(self) -> LiveCommissionProfile:
        raw = read_owner_file(self.directory, "live-commission.json", 65536)
        if _hash(raw) != self.profile_sha256:
            raise AuthorityDenied("live commission differs from owner configuration pin")
        return LiveCommissionProfile.model_validate_json(raw)

    def admitted(self, database, scope, authorization_id: str) -> bool:
        if database is None:
            return False
        row = database.execute(
            "SELECT payload_json FROM activity_events WHERE portfolio_id=? AND kind='live_commission_admitted' "
            "ORDER BY rowid DESC LIMIT 1", (scope.portfolio_id,),
        ).fetchone()
        return bool(row and json.loads(row[0]) == {
            "commission_sha256": self.profile_sha256, "authorization_id": authorization_id,
            "scope_sha256": _hash(canonical_json(scope.model_dump(mode="json")).encode()),
        })

    def verify(self, scope, bundle, clock, *, database=None, management_only=False) -> dict:
        """Verify no-network current evidence, independent MACs and actual reads.

        Unlike dashboard declarations this has no unsigned/import/synthetic
        acceptance route and no injectable readiness callback.
        """
        from trade_graph.application.venue_conformance import (
            MAX_REPORT_BYTES,
            STAGES,
            PinnedVenueObservation,
            ReadOnlyAccountObservation,
            _read,
        )

        profile = self.load()
        now = clock.now()
        scope_digest = _hash(canonical_json(scope.model_dump(mode="json")).encode())
        grant = bundle.owner_authorization.payload
        if (scope_digest != profile.scope_sha256 or grant.scope != scope
                or grant.authorization_id != profile.authorization_id
                or profile.protected_package_sha256 != protected_package_sha256()):
            raise AuthorityDenied("live commission scope, authorization or protected source changed")
        assert_live_process_boundary(self.directory, profile.runtime_manifest_sha256)
        keys, issuers, source_digests, operation_sources, source_bytes = [], set(), set(), set(), 0
        for pin in profile.reviews:
            raw = read_owner_file(self.directory, pin.filename, 65536)
            if _hash(raw) != pin.sha256:
                raise AuthorityDenied("live commissioning review pin changed")
            signed = SignedCommissionReview.model_validate_json(raw)
            review = signed.payload
            key = read_owner_file(self.directory, f"commission-{review.issuer}.key", 64)
            keys.append(key)
            expected = hmac.new(key, _DOMAIN + canonical_json(review.model_dump(mode="json")).encode(),
                                hashlib.sha256).hexdigest()
            if (not 32 <= len(key) <= 64 or review.issuer in issuers
                    or not hmac.compare_digest(expected, signed.signature)
                    or review.basis != "actual_external_or_deployment_verification" or review.verdict != "passed"
                    or not review.verified_at <= now or not management_only and now >= review.expires_at
                    or set(review.assertions) != _ASSERTIONS[review.issuer]
                    or len(set(review.assertions)) != len(review.assertions)
                    or any(getattr(review, name) != getattr(profile, name) for name in (
                        "scope_sha256", "authorization_id", "readiness_bundle_sha256",
                        "runtime_manifest_sha256", "protected_package_sha256"))):
                raise AuthorityDenied("independent actual live commissioning review is invalid or stale")
            for source in review.sources:
                raw_source = read_owner_file(self.directory, source.filename, 1048576)
                source_bytes += len(raw_source)
                if source_bytes > 8 * 1048576:
                    raise AuthorityDenied("complete live commissioning sources exceed bounded verification")
                if _hash(raw_source) != source.sha256:
                    raise AuthorityDenied("reviewed complete live source bytes changed")
                source_digests.add(source.sha256)
                if review.issuer == "operations":
                    operation_sources.add(source.sha256)
            issuers.add(review.issuer)
        if issuers != set(_ASSERTIONS) or len(set(keys)) != 3:
            raise AuthorityDenied("independent live issuers require distinct protected keys")
        from trade_graph.kernel.live_network import PreparedLiveNetworkProfile

        network_raw = read_owner_file(self.directory, "live-network-profile.json", 32768)
        network_profile = PreparedLiveNetworkProfile.model_validate_json(network_raw)
        if (_hash(network_raw) not in operation_sources
                or network_profile.scope_sha256 != profile.scope_sha256
                or network_profile.authorization_id != profile.authorization_id
                or network_profile.manifest_sha256 != profile.runtime_manifest_sha256
                or network_profile.protected_package_sha256 != profile.protected_package_sha256
                or network_profile.live_config_sha256 != profile.live_config_sha256
                or network_profile.deployment_id != scope.deployment_id
                or (network_profile.proxy_ipv4, network_profile.proxy_port)
                != (profile.proxy_ipv4, profile.proxy_port)):
            raise AuthorityDenied("actual inspected live network profile is not independently reviewed and bound")
        collector_key = read_owner_file(self.directory, "venue-collector.key", 64)
        # A fresh actual observation is compulsory on original commissioning.
        # Thereafter that immutable admission evidence is checked at its original
        # timestamp; current account/balance/order health comes from the trusted
        # service's fresh protected reconciliation, not owner re-signing per tick.
        verification_time = now
        if self.admitted(database, scope, grant.authorization_id):
            original = ReadOnlyAccountObservation.model_validate(
                json.loads(_read(profile.venue_observation_path, MAX_REPORT_BYTES))["payload"],
            )
            verification_time = original.finished_at
        native = PinnedVenueObservation(profile.venue_observation_path, profile.venue_observation_sha256,
                                       collector_key).verify(now=verification_time, maximum_age_seconds=60)
        observed_scope = native.observation.scope.model_dump(mode="json")
        mode = observed_scope.pop("mode")
        if (not native.source_current or mode != "live" or observed_scope != scope.model_dump(mode="json")
                or native.observation.transport_basis != "owned_https"
                or not {"BalanceEx", "OpenOrders", "ClosedOrders", "TradesHistory", "TradeVolume"}
                <= set(native.authenticated_reads)
                or native.observation.completed_stages != STAGES
                or profile.venue_observation_sha256 not in source_digests):
            raise AuthorityDenied("fresh complete actual authenticated Kraken evidence required")
        return {"status": "verified", "checks": {"independent_live_commission": True}, "unresolved": [],
                "authoritative_external_verification": True, "live_authorization": False}

    def credentials(self) -> tuple[str, str]:
        """Call only after startup admission; no ambient credential fallback."""
        profile = self.load()
        result = []
        for filename, pin in (("kraken.key", profile.kraken_key_sha256),
                              ("kraken.secret", profile.kraken_secret_sha256)):
            raw = read_owner_file(self.directory, filename, 4096)
            if _hash(raw) != pin or not raw or b"\n" in raw or b"\r" in raw:
                raise AuthorityDenied("exact protected Kraken credential pin required")
            result.append(raw.decode("ascii"))
        return tuple(result)
