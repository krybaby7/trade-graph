"""Protected read-only venue captures; observations never grant trading authority."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import stat
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trade_graph.adapters.brokers import kraken_live, kraken_transport
from trade_graph.adapters.brokers.kraken_live import KrakenLiveBroker
from trade_graph.adapters.brokers.kraken_transport import (
    PRIVATE_READ_METHODS,
    PUBLIC_METHODS,
    KrakenReadResponse,
    KrakenRestTransport,
    _check_json_depth,
    _finite_constant,
    _unique_fields,
)
from trade_graph.adapters.engineering.artifact_files import open_directory
from trade_graph.contracts.models import OrderLookup
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied, TradeGraphError
from trade_graph.domain.money import canonical_decimal

Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_./:-]+$")]
Basis = Literal["owned_https", "injected_transport"]
Stage = Literal["instruments", "account_fees", "balances", "open_orders", "order_lookups", "native_history"]
STAGES = ("instruments", "account_fees", "balances", "open_orders", "order_lookups", "native_history")
MAX_WIRE_BYTES = 1024 * 1024
MAX_CAPTURE_BYTES = 2 * MAX_WIRE_BYTES
MAX_TOTAL_BYTES = 16 * MAX_WIRE_BYTES
MAX_REPORT_BYTES = 262144
_DOMAIN = b"trade-graph.venue-read-only.v1\0"
_PERMANENT_PENDING = (
    "owner_eligibility_unverified", "native_account_owner_identity_unverified",
    "key_permission_inventory_unverified", "withdrawals_absent_unverified",
    "write_cancel_uncertainty_conformance_unverified", "native_stop_protection_unverified",
    "protected_account_ledger_reconciliation_unverified", "intended_host_dependency_identity_unverified",
)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VenueObservationScope(_Contract):
    deployment_id: Identifier
    portfolio_id: Identifier
    account_id: Identifier
    venue: Literal["kraken"]
    mode: Literal["live"]
    symbol: Identifier
    policy_revision: Identifier
    policy_sha256: Fingerprint
    deployment_artifact_sha256: Fingerprint
    system_version_sha256: Fingerprint


class ReadOnlyObservationGrant(_Contract):
    schema_version: Literal[1]
    authorization_id: Identifier
    action: Literal["observe_read_only_venue_account"]
    scope: VenueObservationScope
    credential_binding_sha256: Fingerprint
    not_before: datetime
    expires_at: datetime
    maximum_requests: Annotated[int, Field(strict=True, ge=1, le=256)] = 128
    maximum_history_pages: Annotated[int, Field(strict=True, ge=1, le=20)] = 20
    maximum_duration_seconds: Annotated[int, Field(strict=True, ge=1, le=300)] = 60
    history_start_utc: datetime | None = None
    lookups: tuple[OrderLookup, ...] = Field(default=(), max_length=20)

    @field_validator("not_before", "expires_at", "history_start_utc")
    @classmethod
    def utc(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("observation authority requires timezone-aware dates")
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def window(self):
        if not timedelta(0) < self.expires_at - self.not_before <= timedelta(minutes=10):
            raise ValueError("read-only authority must have a bounded window")
        if self.history_start_utc is not None and self.history_start_utc > self.not_before:
            raise ValueError("history cannot start after read-only authority")
        for lookup in self.lookups:
            if lookup.symbol != self.scope.symbol or not (lookup.client_order_id or lookup.venue_order_id):
                raise ValueError("order lookup must use the authorized symbol and identity")
            if any(value is not None and len(value) > 128
                   for value in (lookup.client_order_id, lookup.venue_order_id)):
                raise ValueError("order lookup identity exceeds its bound")
        return self


class _SignedGrant(_Contract):
    payload: ReadOnlyObservationGrant
    signature: Fingerprint


def _canonical(value: object) -> bytes:
    def decimal(item):
        if isinstance(item, Decimal):
            return canonical_decimal(item)
        raise TypeError("unsupported canonical value")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=decimal).encode()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _mac(key: bytes, kind: str, value: object) -> str:
    if type(key) is not bytes or len(key) < 32:
        raise ValueError("protected observation keys require at least 32 bytes")
    return hmac.new(key, _DOMAIN + kind.encode() + b"\0" + _canonical(value), hashlib.sha256).hexdigest()


def _private(fd: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("venue evidence requires an owner-private directory")


def _read(path: Path, limit: int) -> bytes:
    if not path.is_absolute():
        raise PermissionError("venue evidence paths must be absolute")
    parent = open_directory(path.parent)
    try:
        _private(parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or before.st_nlink != 1 or before.st_mode & 0o077 or before.st_size > limit):
                raise PermissionError("venue evidence must be a bounded owner-only single-link regular file")
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            if len(data) > limit or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("venue evidence changed while being read")
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _write(directory: Path, name: str, data: bytes) -> None:
    parent = open_directory(directory)
    try:
        _private(parent)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fchmod(fd, 0o400)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)


def _source_digests() -> tuple[str, str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = ("adapters/brokers/kraken_live.py", "adapters/brokers/kraken_transport.py",
             "application/broker_identity.py", "contracts/models.py", "domain/money.py",
             "kernel/books.py", "application/ledger.py", "application/execution.py")
    adapter = _hash(_canonical({name: _hash((root / name).read_bytes()) for name in paths}))
    wire = _hash(_canonical({
        "read_methods": sorted(PRIVATE_READ_METHODS | PUBLIC_METHODS), "time_in_force": ["gtc", "ioc"],
        "page_size": kraken_live.PAGE_SIZE, "native_scale": kraken_live.MAX_NATIVE_SCALE,
        "ledger_precision": kraken_live.CORE_LEDGER_PRECISION, "maximum_ledger_refs_per_trade": 20,
        "native_quote_principal": "declared_quantum_and_exact_ledger_legs",
        "native_stop_tested": False, "order_writes": False,
    }))
    return _hash(Path(__file__).read_bytes()), adapter, wire


@dataclass
class _ReceiptClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True)
class PinnedReadOnlyAuthority:
    path: Path
    grant_sha256: str
    owner_key: bytes = field(repr=False)

    def load(self, now: datetime) -> tuple[ReadOnlyObservationGrant, bytes]:
        raw = _read(self.path, MAX_REPORT_BYTES)
        if _hash(raw) != self.grant_sha256:
            raise AuthorityDenied("read-only authority does not match its protected pin")
        signed = _SignedGrant.model_validate_json(raw)
        if not hmac.compare_digest(signed.signature, _mac(self.owner_key, "owner-grant",
                                                        signed.payload.model_dump(mode="json"))):
            raise AuthorityDenied("read-only owner authority signature is invalid")
        if now.tzinfo is None or not signed.payload.not_before <= now <= signed.payload.expires_at:
            raise AuthorityDenied("read-only owner authority is outside its window")
        return signed.payload, raw


class _ObservationWindow(_Contract):
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("venue observation dates must be timezone aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def window(self):
        if not timedelta(0) <= self.finished_at - self.started_at <= timedelta(minutes=10):
            raise ValueError("venue observation window exceeds its bound")
        return self


class WireReceiptReference(_ObservationWindow):
    index: Annotated[int, Field(strict=True, ge=1, le=256)]
    filename: Annotated[str, Field(pattern=r"^wire-[0-9]{4}\.json$")]
    sha256: Fingerprint
    method: Identifier
    parameters_sha256: Fingerprint
    response_sha256: Fingerprint
    request_sha256: Fingerprint
    transport_basis: Basis


class ReadOnlyAccountObservation(_ObservationWindow):
    schema_version: Literal[1]
    observation_id: Identifier
    scope: VenueObservationScope
    collector_sha256: Fingerprint
    adapter_sha256: Fingerprint
    wire_contract_sha256: Fingerprint
    grant_sha256: Fingerprint
    credential_binding_sha256: Fingerprint
    transport_basis: Basis
    receipts: tuple[WireReceiptReference, ...] = Field(max_length=256)
    completed_stages: tuple[Stage, ...] = Field(max_length=6)
    native_summary_sha256: Fingerprint
    checks: dict[str, Literal["observed", "pending"]]
    pending: tuple[Identifier, ...]


@dataclass(frozen=True)
class VerifiedReadOnlyAccountObservation:
    observation: ReadOnlyAccountObservation
    source_sha256: str
    authenticated_reads: tuple[str, ...]
    source_current: bool
    pending: tuple[str, ...]


@dataclass
class _PipelineProgress:
    summary: dict = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    def result(self):
        return self.summary, tuple(self.completed), tuple(self.pending)


class _MissingRetainedRead(TradeGraphError):
    """An unsuccessful dispatch has no successful wire response to replay."""


async def _pipeline(broker: KrakenLiveBroker, grant: ReadOnlyObservationGrant, *, stages=STAGES,
                    progress: _PipelineProgress | None = None):
    progress = progress if progress is not None else _PipelineProgress()
    summary, completed, pending = progress.summary, progress.completed, progress.pending
    for stage in stages:
        try:
            if stage == "instruments":
                summary[stage] = [item.model_dump(mode="json") for item in await broker.instruments()]
            elif stage == "account_fees":
                summary[stage] = await broker.fee_schedule(account_specific=True)
            elif stage == "balances":
                summary[stage] = (await broker.balances()).model_dump(mode="json")
                summary["held_balances"] = broker.balance_holds
                summary["available_balances"] = broker.available_balances
            elif stage == "open_orders":
                summary[stage] = [item.model_dump(mode="json") for item in await broker.open_orders()]
            elif stage == "order_lookups":
                summary[stage] = [(await broker.order_status(lookup)).model_dump(mode="json")
                                  for lookup in grant.lookups]
                if any(item["status"] == "unknown" for item in summary[stage]):
                    pending.append("order_lookup_unresolved")
            else:
                fills, cursor, seen = [], None, set()
                for _ in range(grant.maximum_history_pages):
                    page = await broker.fills_since(cursor)
                    fills.extend(item.model_dump(mode="json") for item in page.fills)
                    if page.next_cursor is None:
                        break
                    if page.next_cursor in seen:
                        raise ValueError("history cursor repeated")
                    seen.add(page.next_cursor)
                    cursor = page.next_cursor
                else:
                    raise ValueError("normalized history exceeded its page bound")
                summary[stage] = fills
            completed.append(stage)
        except _MissingRetainedRead:
            pending.append("native_stage_transport_evidence_incomplete")
            break
        except (TradeGraphError, ValueError, TypeError, TimeoutError, OSError) as exc:
            pending.append(stage + "_" + type(exc).__name__)
            break
    return progress.result()


class KrakenReadOnlyConformance:
    """One bounded capture under a separately pinned read-only owner grant."""

    def __init__(self, authority: PinnedReadOnlyAuthority, *, collector_key: bytes,
                 api_key: str, api_secret: str, client: httpx.AsyncClient | None = None) -> None:
        _mac(collector_key, "preflight", {})
        if collector_key == authority.owner_key:
            raise ValueError("owner and collector authority must use separate keys")
        if not isinstance(api_key, str) or not 0 < len(api_key) <= 1024:
            raise ValueError("a bounded protected venue credential is required")
        self._authority, self._key = authority, collector_key
        self._api_key, self._api_secret, self._client = api_key, api_secret, client
        self._used = False

    async def collect(self, output_directory: Path) -> PinnedVenueObservation:
        if self._used:
            raise ValueError("a conformance collector is single use")
        started = datetime.now(UTC)
        grant, grant_raw = self._authority.load(started)
        credential = _hash(self._api_key.encode())
        if credential != grant.credential_binding_sha256:
            raise AuthorityDenied("credential does not match read-only observation authority")
        sources = _source_digests()
        deadline = min(grant.expires_at, started + timedelta(seconds=grant.maximum_duration_seconds))
        root = open_directory(output_directory)
        try:
            _private(root)
            observation_id = uuid.uuid4().hex
            directory = output_directory / observation_id
            os.mkdir(observation_id, mode=0o700, dir_fd=root)
        finally:
            os.close(root)
        self._used = True
        _write(directory, "grant.json", grant_raw)
        receipts, total, requests = [], 0, 0
        clock = _ReceiptClock(started)

        def capture(response: KrakenReadResponse):
            nonlocal total
            if response.finished_at > deadline:
                raise TimeoutError("venue response arrived after the read-only observation deadline")
            index = len(receipts) + 1
            record = {
                "index": index, "method": response.method, "parameters": json.loads(response.parameters),
                "request_sha256": response.request_sha256, "response_sha256": _hash(response.response),
                "response": base64.b64encode(response.response).decode(),
                "started_at": utc_iso(response.started_at), "finished_at": utc_iso(response.finished_at),
                "transport_basis": response.transport_basis,
            }
            raw = _canonical({"payload": record, "signature": _mac(self._key, "wire", record)})
            total += len(raw)
            if len(raw) > MAX_CAPTURE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("private wire capture exceeds its protected bound")
            name = f"wire-{index:04d}.json"
            _write(directory, name, raw)
            receipts.append(WireReceiptReference(
                index=index, filename=name, sha256=_hash(raw), method=response.method,
                parameters_sha256=_hash(response.parameters), response_sha256=_hash(response.response),
                request_sha256=response.request_sha256, started_at=response.started_at,
                finished_at=response.finished_at, transport_basis=response.transport_basis,
            ))
            clock.current = response.finished_at

        transport = KrakenRestTransport(api_key=self._api_key, api_secret=self._api_secret, client=self._client,
                                        maximum_response_bytes=MAX_WIRE_BYTES, read_observer=capture)

        async def limited(method, parameters):
            nonlocal requests
            if method not in PUBLIC_METHODS | PRIVATE_READ_METHODS:
                raise AuthorityDenied("read-only observation cannot dispatch an order")
            self._authority.load(datetime.now(UTC))
            if datetime.now(UTC) >= deadline or requests >= grant.maximum_requests:
                raise AuthorityDenied("read-only observation authority is exhausted")
            requests += 1
            return await transport(method, parameters)

        broker = KrakenLiveBroker(limited, account_id=grant.scope.account_id, clock=clock,
                                  symbols=[grant.scope.symbol], history_start_utc=grant.history_start_utc,
                                  maximum_history_pages=grant.maximum_history_pages)
        duration = (deadline - datetime.now(UTC)).total_seconds()
        progress = _PipelineProgress()
        try:
            if duration <= 0:
                raise AuthorityDenied("read-only observation authority expired before dispatch")
            async with asyncio.timeout(duration):
                summary, completed, problems = await _pipeline(broker, grant, progress=progress)
        except TimeoutError:
            progress.pending.append("observation_deadline_exceeded")
            summary, completed, problems = progress.result()
        finally:
            await transport.aclose()
        # Evidence freshness ends with the last retained venue response. Local
        # cancellation/transport cleanup and report sealing can finish after an
        # expiry-limited deadline without extending the owner's read authority
        # or making earlier native facts appear newly observed.
        finished = receipts[-1].finished_at if receipts else started
        summary_raw = _canonical(summary)
        if len(summary_raw) > MAX_CAPTURE_BYTES:
            raise ValueError("native observation summary exceeds its bound")
        _write(directory, "native-summary.json", summary_raw)
        collector, adapter, wire = sources
        basis = transport.observation_basis
        pending = [*_PERMANENT_PENDING, *problems]
        if _source_digests() != sources:
            pending.append("collector_or_adapter_source_changed_during_observation")
        if basis != "owned_https":
            pending.append("injected_transport_is_not_authenticated_venue_evidence")
        if grant.history_start_utc is not None:
            pending.append("historical_account_scope_limited")
        observation = ReadOnlyAccountObservation(
            schema_version=1, observation_id=observation_id, scope=grant.scope,
            started_at=started, finished_at=finished, collector_sha256=collector, adapter_sha256=adapter,
            wire_contract_sha256=wire, grant_sha256=_hash(grant_raw), credential_binding_sha256=credential,
            transport_basis=basis, receipts=tuple(receipts), completed_stages=completed,
            native_summary_sha256=_hash(summary_raw), checks={stage: "observed" if stage in completed else "pending"
                                                          for stage in STAGES}, pending=tuple(pending),
        )
        payload = observation.model_dump(mode="json")
        raw = _canonical({"payload": payload, "signature": _mac(self._key, "observation", payload)})
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError("observation report exceeds its bound")
        _write(directory, "observation.json", raw)
        os.chmod(directory, 0o500)
        return PinnedVenueObservation(directory / "observation.json", _hash(raw), self._key)


@dataclass(frozen=True)
class PinnedVenueObservation:
    path: Path
    observation_sha256: str
    collector_key: bytes = field(repr=False)

    def verify(self, *, now: datetime, maximum_age_seconds: int = 60) -> VerifiedReadOnlyAccountObservation:
        """Validate exact retained sources and re-normalize them without any network."""
        if now.tzinfo is None or type(maximum_age_seconds) is not int or not 1 <= maximum_age_seconds <= 300:
            raise ValueError("observation verification requires a bounded aware current time")
        raw = _read(self.path, MAX_REPORT_BYTES)
        if _hash(raw) != self.observation_sha256:
            raise ValueError("venue observation differs from its protected pin")
        envelope = json.loads(raw)
        if set(envelope) != {"payload", "signature"} or not hmac.compare_digest(
                envelope["signature"], _mac(self.collector_key, "observation", envelope["payload"])):
            raise ValueError("venue observation collector signature is invalid")
        observation = ReadOnlyAccountObservation.model_validate(envelope["payload"])
        if not observation.started_at <= observation.finished_at <= now or (
                now - observation.finished_at).total_seconds() > maximum_age_seconds:
            raise ValueError("venue observation is stale or outside its observation window")
        grant_raw = _read(self.path.parent / "grant.json", MAX_REPORT_BYTES)
        if _hash(grant_raw) != observation.grant_sha256:
            raise ValueError("read-only authority source changed")
        grant = _SignedGrant.model_validate_json(grant_raw).payload
        if (grant.scope != observation.scope or grant.credential_binding_sha256 != observation.credential_binding_sha256
                or not grant.not_before <= observation.started_at <= observation.finished_at <= grant.expires_at):
            raise ValueError("venue observation does not match its read-only authority")
        if observation.completed_stages != STAGES[:len(observation.completed_stages)]:
            raise ValueError("native observation stages do not form a valid prefix")
        if observation.checks != {stage: "observed" if stage in observation.completed_stages else "pending"
                                  for stage in STAGES}:
            raise ValueError("native checks do not match retained observation stages")
        captures, authenticated, total = [], [], 0
        previous_finished = observation.started_at
        deadline = min(grant.expires_at, observation.started_at + timedelta(seconds=grant.maximum_duration_seconds))
        for index, reference in enumerate(observation.receipts, start=1):
            wire_raw = _read(self.path.parent / reference.filename, MAX_CAPTURE_BYTES)
            total += len(wire_raw)
            if total > MAX_TOTAL_BYTES or _hash(wire_raw) != reference.sha256:
                raise ValueError("venue wire source changed or exceeded its total bound")
            signed = json.loads(wire_raw)
            record = signed["payload"]
            if set(signed) != {"payload", "signature"} or set(record) != {
                "index", "method", "parameters", "request_sha256", "response_sha256", "response",
                "started_at", "finished_at", "transport_basis",
            } or not isinstance(record["parameters"], dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or len(key) > 128 or len(value) > 4096
                for key, value in record["parameters"].items()
            ):
                raise ValueError("venue wire record schema is invalid")
            if not hmac.compare_digest(signed["signature"], _mac(self.collector_key, "wire", record)):
                raise ValueError("venue wire collector signature is invalid")
            response = base64.b64decode(record["response"], validate=True)
            if len(response) > MAX_WIRE_BYTES or record["response_sha256"] != _hash(response):
                raise ValueError("venue wire response exceeds its bound")
            expected = WireReceiptReference(
                index=index, filename=f"wire-{index:04d}.json", sha256=_hash(wire_raw), method=record["method"],
                parameters_sha256=_hash(_canonical(record["parameters"])), response_sha256=_hash(response),
                request_sha256=record["request_sha256"], started_at=record["started_at"],
                finished_at=record["finished_at"], transport_basis=record["transport_basis"],
            )
            if (expected != reference or record["index"] != index
                    or record["method"] not in PUBLIC_METHODS | PRIVATE_READ_METHODS
                    or reference.transport_basis != observation.transport_basis
                    or not previous_finished <= reference.started_at <= reference.finished_at
                    <= min(observation.finished_at, deadline)):
                raise ValueError("venue wire identity, scope or window is inconsistent")
            previous_finished = reference.finished_at
            _check_json_depth(bytearray(response))
            decoded = json.loads(response, parse_float=Decimal, object_pairs_hook=_unique_fields,
                                 parse_constant=_finite_constant)
            kraken_transport.check_response(decoded)
            captures.append((record, decoded))
            if reference.transport_basis == "owned_https" and reference.method in PRIVATE_READ_METHODS:
                authenticated.append(reference.method)
        if len(captures) > grant.maximum_requests:
            raise ValueError("venue observation exceeded read-only request authority")
        summary_raw = _read(self.path.parent / "native-summary.json", MAX_CAPTURE_BYTES)
        if _hash(summary_raw) != observation.native_summary_sha256:
            raise ValueError("native observation summary changed")
        clock, offset = _ReceiptClock(observation.started_at), 0

        async def replay(method, parameters):
            nonlocal offset
            if offset >= len(captures):
                raise _MissingRetainedRead("native response was not retained")
            record, decoded = captures[offset]
            if record["method"] != method or _canonical(record["parameters"]) != _canonical(parameters):
                raise ValueError("native replay does not match retained request order")
            clock.current = datetime.fromisoformat(record["finished_at"].replace("Z", "+00:00"))
            offset += 1
            return decoded

        broker = KrakenLiveBroker(replay, account_id=grant.scope.account_id, clock=clock,
                                  symbols=[grant.scope.symbol], history_start_utc=grant.history_start_utc,
                                  maximum_history_pages=grant.maximum_history_pages)
        # Every broker await resolves through the retained-only replay closure
        # above; trusted normalization needs no suspension, scheduler or network.
        # Drive it once so the same synchronous gate works within an async writer.
        # Any newly introduced suspension is a contract change and fails closed.
        replay_stages = STAGES[:min(len(observation.completed_stages) + 1, len(STAGES))]
        normalization = _pipeline(broker, grant, stages=replay_stages)
        try:
            try:
                normalization.send(None)
            except StopIteration as finished:
                summary, completed, problems = finished.value
            else:
                raise ValueError("retained native normalization must complete without suspension")
        finally:
            normalization.close()
        if offset != len(captures):
            raise ValueError("venue observation contains unconsumed native responses")
        if completed != observation.completed_stages or _canonical(summary) != summary_raw:
            raise ValueError("native facts do not match current retained-source normalization")
        current = _source_digests()
        source_current = current == (observation.collector_sha256, observation.adapter_sha256,
                                     observation.wire_contract_sha256)
        pending = list(observation.pending)
        pending.extend(problems)
        pending.extend(reason for reason in _PERMANENT_PENDING if reason not in pending)
        if not source_current:
            pending.append("collector_or_adapter_source_changed")
        if not authenticated:
            pending.append("authenticated_private_observation_missing")
        if len(completed) != len(STAGES):
            pending.append("native_observation_incomplete")
        if grant.history_start_utc is not None:
            pending.append("historical_account_scope_limited")
        return VerifiedReadOnlyAccountObservation(observation, self.observation_sha256, tuple(authenticated),
                                                  source_current, tuple(dict.fromkeys(pending)))
