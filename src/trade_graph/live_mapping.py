"""Owner-pinned exact paper/live identity mapping, without economic authority."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from trade_graph.application.venue_conformance import _read
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.evaluation_contracts import ARMS
from trade_graph.evaluation_registry import document_hash
from trade_graph.live_gate import LivePilotScope, _canonical

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")]
_DOMAIN = b"trade-graph.paper-live-owner-mapping.v1\0"


class PaperLiveArmMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    arm: Literal["agent", "cash", "buy_and_hold", "deterministic"]
    portfolio_id: Identifier
    version_id: Identifier
    artifact_sha256: Fingerprint


class PaperLiveMapping(BaseModel):
    """An owner declaration of exact identities; no provenance or live grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    action: Literal["map_paper_evidence_to_live_scope"]
    mapping_id: Identifier
    live_scope: LivePilotScope
    paper_deployment_id: Identifier
    trial_id: Identifier
    protocol_sha256: Fingerprint
    database_identity: tuple[Annotated[StrictInt, Field(ge=0)], Annotated[StrictInt, Field(ge=0)]]
    producer_binding_sha256: Fingerprint
    producer_controller_sha256: Fingerprint
    market_stream_id: Identifier
    symbols: tuple[Identifier, ...] = Field(min_length=1, max_length=8)
    data_policy_sha256: Fingerprint
    friction_policy_sha256: Fingerprint
    regime_classifier_sha256: Fingerprint
    arms: tuple[PaperLiveArmMapping, ...] = Field(min_length=4, max_length=4)
    not_before: datetime
    expires_at: datetime

    @field_validator("not_before", "expires_at")
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError("owner mapping dates require timezones")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def exact_mapping(self):
        if (
            self.paper_deployment_id != self.live_scope.deployment_id
            or tuple(arm.arm for arm in self.arms) != ARMS
            or len({arm.portfolio_id for arm in self.arms}) != 4
            or self.live_scope.portfolio_id in {arm.portfolio_id for arm in self.arms}
            or self.arms[0].artifact_sha256 != self.live_scope.system_version_sha256
            or len(set(self.symbols)) != len(self.symbols)
            or self.live_scope.symbol not in self.symbols
        ):
            raise ValueError("owner mapping requires exact separate four-arm identities and selected live version")
        if not timedelta(0) < self.expires_at - self.not_before <= timedelta(days=31):
            raise ValueError("owner mapping requires a positive bounded verification window")
        return self


@dataclass(frozen=True)
class PinnedPaperLiveMapping:
    """Protected file/key pins supplied by the owner service, never HTTP input."""

    path: Path
    mapping_sha256: str
    owner_key: bytes = field(repr=False)

    def __post_init__(self):
        if (
            not self.path.is_absolute()
            or len(self.mapping_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.mapping_sha256)
            or type(self.owner_key) is not bytes
            or len(self.owner_key) < 32
        ):
            raise ValueError("owner mapping requires exact absolute private source and key pins")

    def load(self, *, scope: LivePilotScope, now: datetime) -> PaperLiveMapping:
        if type(scope) is not LivePilotScope or now.tzinfo is None:
            raise AuthorityDenied("exact protected live scope and aware verification time required")
        raw = _read(self.path, 65536)
        if hashlib.sha256(raw).hexdigest() != self.mapping_sha256:
            raise AuthorityDenied("owner mapping differs from its protected source pin")
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise AuthorityDenied("owner mapping envelope is malformed")
        payload = PaperLiveMapping.model_validate(envelope["payload"])
        signature = hmac.new(
            self.owner_key, _DOMAIN + _canonical(payload.model_dump(mode="json")), hashlib.sha256
        ).hexdigest()
        if type(envelope["signature"]) is not str or not hmac.compare_digest(envelope["signature"], signature):
            raise AuthorityDenied("owner mapping signature is invalid")
        if payload.live_scope != scope or not payload.not_before <= now < payload.expires_at:
            raise AuthorityDenied("owner mapping scope or freshness changed")
        return payload

    def verify_binding(self, binding, *, scope: LivePilotScope, now: datetime) -> PaperLiveMapping:
        """Compare an exact independently retained producer binding to owner pins."""
        from trade_graph.paper_forward_producer import PaperProducerBinding

        if type(binding) is not PaperProducerBinding:
            raise AuthorityDenied("exact protected paper producer binding required")
        mapping = self.load(scope=scope, now=now)
        expected = {
            "paper_deployment_id": binding.deployment_id,
            "trial_id": binding.trial_id,
            "protocol_sha256": binding.protocol_sha256,
            "database_identity": binding.database_identity,
            "producer_binding_sha256": document_hash(binding.model_dump_json()),
            "producer_controller_sha256": binding.controller_sha256,
            "market_stream_id": binding.market_stream_id,
            "symbols": binding.policy.symbols,
            "data_policy_sha256": binding.data_policy_sha256,
            "friction_policy_sha256": binding.friction_policy_sha256,
            "regime_classifier_sha256": binding.regime_classifier_sha256,
            "arms": tuple(PaperLiveArmMapping.model_validate(arm.model_dump()) for arm in binding.arms),
        }
        if any(getattr(mapping, name) != value for name, value in expected.items()):
            raise AuthorityDenied("owner paper/live mapping differs from independently retained producer identities")
        return mapping
