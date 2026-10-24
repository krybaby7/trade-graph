"""Bounded four-arm derivation from persisted paper services, without trust flags.

Collector signatures prove origin/integrity under protected key custody, not
official transport, invoice authentication, profitability or deployment approval.
The producer performs no model calls, broker dispatch or financial writes.
"""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import stat
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from trade_graph.adapters.engineering.artifact_files import ensure_directory, open_directory
from trade_graph.application.ledger import Ledger
from trade_graph.contracts.models import Decision, FillRecord, Observation
from trade_graph.domain.clock import parse_utc, utc_iso
from trade_graph.evaluation_contracts import (
    ARMS,
    Arm,
    ArmPerformance,
    DecisionEvidence,
    EvaluationContract,
    Fingerprint,
    ForwardObservation,
    Reference,
    Window,
)
from trade_graph.evaluation_registry import document_hash
from trade_graph.kernel.books import _convert
from trade_graph.runtime_evidence import MAX_SOURCE_BYTES, TABLES, RuntimeEvidenceCollector, _canonical

MAX_RECORDS = 512
MAX_STORED_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_RECEIPT_BYTES = 512 * 1024


def paper_producer_controller_sha256() -> str:
    """Pin the actual protected derivation; this is not an owner approval."""
    import trade_graph.application.ledger as ledger
    import trade_graph.contracts.models as models
    import trade_graph.evaluation_registry as registry
    import trade_graph.kernel.books as books
    import trade_graph.runtime_evidence as evidence

    paths = [Path(__file__), *(Path(module.__file__) for module in (ledger, models, registry, books, evidence))]
    return sha256(b"".join(path.name.encode() + b"\0" + path.read_bytes() for path in paths)).hexdigest()


class PaperProducerPolicy(EvaluationContract):
    schema_version: Literal[1] = 1
    sampling_seconds: int = Field(strict=True, ge=1, le=86400)
    maximum_lateness_seconds: int = Field(default=5, strict=True, ge=0, le=5)
    symbols: tuple[Reference, ...]
    maximum_quote_age_seconds: int = Field(default=60, strict=True, ge=1, le=300)
    opportunity_definition: Literal["retained_quote_updates"] = "retained_quote_updates"
    valuation_convention: Literal["mid"] = "mid"
    usefulness_definition: Literal["unassessed"] = "unassessed"
    regime_definition: Literal["unassessed"] = "unassessed"
    independence_definition: Literal["unassessed"] = "unassessed"
    baseline_constraints: Literal["cash_no_trades_buy_and_hold_one_acquisition_intent"] = (
        "cash_no_trades_buy_and_hold_one_acquisition_intent"
    )
    operational_error_definition: Literal["retained_rejected_or_unknown_order_intents"] = (
        "retained_rejected_or_unknown_order_intents"
    )
    ratio_precision: Literal["18_decimal_places_drawdown_ceiling_exposure_half_even"] = (
        "18_decimal_places_drawdown_ceiling_exposure_half_even"
    )

    @model_validator(mode="after")
    def bounded_symbols(self):
        if not 1 <= len(self.symbols) <= 8 or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("declare one to eight distinct exact instrument symbols")
        if any(len(symbol.split("/")) != 2 for symbol in self.symbols):
            raise ValueError("paper symbols require exact base/quote identities")
        return self

    @property
    def sha256(self) -> str:
        return document_hash(self.model_dump_json())


class PaperArmBinding(EvaluationContract):
    arm: Arm
    portfolio_id: Reference
    version_id: Reference
    artifact_sha256: Fingerprint


class PaperProducerBinding(EvaluationContract):
    schema_version: Literal[1] = 1
    deployment_id: Reference
    trial_id: Reference
    protocol_sha256: Fingerprint
    market_stream_id: Reference
    data_policy_sha256: Fingerprint
    friction_policy_sha256: Fingerprint
    regime_classifier_sha256: Fingerprint
    database_identity: tuple[int, int]
    registered_at: datetime
    policy: PaperProducerPolicy
    controller_sha256: Fingerprint
    initial_source_sha256: Fingerprint
    arms: tuple[PaperArmBinding, ...]
    sampling_times: tuple[datetime, ...]

    @field_validator("registered_at", "sampling_times")
    @classmethod
    def utc(cls, value):
        values = value if isinstance(value, tuple) else (value,)
        if any(item.tzinfo is None for item in values):
            raise ValueError("producer timestamps require timezone awareness")
        converted = tuple(item.astimezone(UTC) for item in values)
        return converted if isinstance(value, tuple) else converted[0]

    @model_validator(mode="after")
    def distinct(self):
        if tuple(item.arm for item in self.arms) != ARMS or len({item.portfolio_id for item in self.arms}) != 4:
            raise ValueError("bind four distinct actual paper portfolios in arm order")
        if not 3 <= len(self.sampling_times) <= 256:
            raise ValueError("complete producer sampling schedule exceeds protected bounds")
        if tuple(sorted(set(self.sampling_times))) != self.sampling_times:
            raise ValueError("sampling schedule must be distinct and chronological")
        if self.registered_at >= self.sampling_times[0]:
            raise ValueError("producer must be registered before untouched forward samples")
        return self


class PaperCheckpoint(EvaluationContract):
    trial_id: Reference
    binding_sha256: Fingerprint
    sample_index: int = Field(strict=True, ge=0, le=255)
    as_of: datetime
    collected_at: datetime
    source_sha256: Fingerprint
    equities_eur: tuple[str, ...]
    exposures: tuple[str, ...]


class PaperBlockReceipt(EvaluationContract):
    schema_version: Literal[1] = 1
    trial_id: Reference
    binding_sha256: Fingerprint
    block_index: int = Field(strict=True, ge=0)
    checkpoint_sha256: tuple[Fingerprint, ...]
    observation: ForwardObservation
    observation_sha256: Fingerprint
    signed_native_fee_totals_eur: tuple[str, ...]
    verification_basis: Literal["persisted_paper_derivation"] = "persisted_paper_derivation"
    actual_external_provenance_verified: Literal[False] = False
    production_host_authorization: Literal[False] = False
    limitations: tuple[Reference, ...]

    @model_validator(mode="after")
    def bound(self):
        if (
            self.observation.block_index != self.block_index
            or document_hash(self.observation.model_dump_json()) != self.observation_sha256
            or len(self.signed_native_fee_totals_eur) != 4
            or len(self.checkpoint_sha256) < 2
        ):
            raise ValueError("paper block receipt requires exact derived observation and four fee totals")
        return self


class PaperProducerVerification(EvaluationContract):
    binding_sha256: Fingerprint
    block_receipt_sha256: Fingerprint
    collector_origin_verified: Literal[True] = True
    historical_sources_consistent: bool = Field(strict=True)
    observation_published: bool = Field(strict=True)
    actual_external_provenance_verified: Literal[False] = False
    production_host_authorization: Literal[False] = False
    limitations: tuple[Reference, ...]


class _FrozenRuntime:
    """Read-only retained database view: no migration, broker or effect interface."""

    def __init__(self, database, raw: str):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        data = json.loads(raw)
        try:
            for table in TABLES:
                names = [row["name"] for row in database.execute(f"PRAGMA table_info({table})")]
                columns = ",".join('"' + name.replace('"', '""') + '"' for name in names)
                self.connection.execute(f"CREATE TABLE {table} ({columns})")
                placeholders = ",".join("?" for _ in names)
                for row in data[table]:
                    if set(row) != set(names):
                        raise ValueError("frozen runtime schema does not match protected source")
                    self.connection.execute(
                        f"INSERT INTO {table} VALUES ({placeholders})", tuple(row[name] for name in names)
                    )
            self.connection.execute("PRAGMA query_only=ON")
        except BaseException:
            self.connection.close()
            raise

    def execute(self, sql, params=()):
        return self.connection.execute(sql, params)


class PaperForwardProducer:
    """Owner-controlled four-arm collector; no caller-provided returns/trust flags.

    Key custody and process protection must be established by deployment policy.
    Merely constructing this class does not establish the intended host boundary.
    """

    def __init__(
        self,
        collector: RuntimeEvidenceCollector,
        path: Path | str,
        *,
        policy: PaperProducerPolicy,
        authentication_key: bytes,
        expected_controller_sha256: str,
    ):
        if (
            type(collector) is not RuntimeEvidenceCollector
            or type(authentication_key) is not bytes
            or not 32 <= len(authentication_key) <= 64
        ):
            raise ValueError("actual protected collector and private authentication key required")
        self.collector, self.database, self.registry, self.clock = (
            collector,
            collector.database,
            collector.registry,
            collector.clock,
        )
        self.policy = PaperProducerPolicy.model_validate(policy.model_dump())
        self.key, self.controller_sha256 = authentication_key, expected_controller_sha256
        self._pins()
        self.path = Path(path).absolute()
        ensure_directory(self.path.parent)
        self._parent_fd = open_directory(self.path.parent)
        parent = os.fstat(self._parent_fd)
        if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            os.close(self._parent_fd)
            raise ValueError("paper producer requires an owner-private nonsymlink directory")
        self._parent_identity = parent.st_dev, parent.st_ino
        fd = None
        try:
            try:
                fd = os.open(
                    self.path.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=self._parent_fd,
                )
                created = True
            except FileExistsError:
                fd = os.open(
                    self.path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self._parent_fd
                )
                created = False
            info = os.fstat(fd)
            registry_path = self.registry.connection.execute("PRAGMA database_list").fetchone()[2]
            sources = [self.database.path, Path(registry_path)]
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or any((path.stat().st_dev, path.stat().st_ino) == (info.st_dev, info.st_ino) for path in sources)
            ):
                raise ValueError("paper producer requires a separate private single-link regular file")
            self._file_identity = info.st_dev, info.st_ino
            for suffix in ("-wal", "-shm", "-journal"):
                try:
                    journal = os.stat(self.path.name + suffix, dir_fd=self._parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (
                    not stat.S_ISREG(journal.st_mode)
                    or journal.st_uid != os.geteuid()
                    or journal.st_nlink != 1
                    or journal.st_mode & 0o077
                ):
                    raise ValueError("paper producer journal requires private single-link regular storage")
            # Anchor SQLite and its journals to the verified parent descriptor.
            self.connection = sqlite3.connect(
                f"/proc/self/fd/{self._parent_fd}/{self.path.name}", isolation_level=None, timeout=10
            )
            self.connection.row_factory = sqlite3.Row
            if not created:
                tables = {
                    row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if tables != {"paper_producer_records", "paper_producer_sources"}:
                    raise ValueError("existing database is not an isolated paper producer sidecar")
            self._pins()
        except BaseException:
            if hasattr(self, "connection"):
                self.connection.close()
            os.close(self._parent_fd)
            raise
        finally:
            if fd is not None:
                os.close(fd)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS paper_producer_records (
                kind TEXT NOT NULL, record_key TEXT NOT NULL, trial_id TEXT NOT NULL,
                document_json TEXT NOT NULL, document_sha256 TEXT NOT NULL,
                authentication TEXT NOT NULL, PRIMARY KEY(kind,record_key));
            CREATE TABLE IF NOT EXISTS paper_producer_sources (
                source_sha256 TEXT PRIMARY KEY, expanded_bytes INTEGER NOT NULL,
                compressed_sha256 TEXT NOT NULL, compressed_data BLOB NOT NULL);
            CREATE TRIGGER IF NOT EXISTS paper_producer_record_no_update BEFORE UPDATE ON paper_producer_records
                BEGIN SELECT RAISE(ABORT,'paper producer history is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS paper_producer_record_no_delete BEFORE DELETE ON paper_producer_records
                BEGIN SELECT RAISE(ABORT,'paper producer history is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS paper_producer_source_no_update BEFORE UPDATE ON paper_producer_sources
                BEGIN SELECT RAISE(ABORT,'paper producer source is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS paper_producer_source_no_delete BEFORE DELETE ON paper_producer_sources
                BEGIN SELECT RAISE(ABORT,'paper producer source is append-only'); END;
        """)

    def close(self):
        self.connection.close()
        os.close(self._parent_fd)

    def _pins(self):
        if paper_producer_controller_sha256() != self.controller_sha256:
            raise ValueError("protected producer controller pin changed")
        if hasattr(self, "connection"):
            directory = open_directory(self.path.parent)
            try:
                parent = os.fstat(directory)
                info = os.stat(self.path.name, dir_fd=directory, follow_symlinks=False)
                if (
                    (parent.st_dev, parent.st_ino) != self._parent_identity
                    or (info.st_dev, info.st_ino) != self._file_identity
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077
                ):
                    raise ValueError("private paper producer storage identity or permissions changed")
            finally:
                os.close(directory)

    @contextmanager
    def _atomic(self):
        self._pins()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._bounds()
            yield
            self._bounds()
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _bounds(self):
        objects = self.connection.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM paper_producer_sources LIMIT ?)", (MAX_RECORDS + 1,)
        ).fetchone()[0]
        if objects > MAX_RECORDS:
            raise ValueError("complete paper producer source history exceeds object bounds")
        if self.connection.execute(
            "SELECT 1 FROM paper_producer_records WHERE kind NOT IN ('binding','checkpoint','block') LIMIT 1"
        ).fetchone():
            raise ValueError("paper producer contains an unknown source record")
        count = self.connection.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM paper_producer_records LIMIT ?)", (MAX_RECORDS + 1,)
        ).fetchone()[0]
        if count > MAX_RECORDS:
            raise ValueError("complete paper producer history exceeds record bounds")
        if self.connection.execute(
            "SELECT 1 FROM paper_producer_records WHERE length(CAST(document_json AS BLOB))>? LIMIT 1",
            (MAX_RECEIPT_BYTES,),
        ).fetchone():
            raise ValueError("paper producer document exceeds verification bounds")
        if self.connection.execute(
            "SELECT 1 FROM paper_producer_sources WHERE typeof(expanded_bytes)<>'integer' "
            "OR expanded_bytes<=0 OR expanded_bytes>? "
            "OR length(compressed_data)>? LIMIT 1",
            (MAX_SOURCE_BYTES, MAX_SOURCE_BYTES),
        ).fetchone():
            raise ValueError("paper producer source metadata exceeds verification bounds")
        sources = self.connection.execute(
            "SELECT COALESCE(SUM(length(compressed_data)),0),COALESCE(SUM(expanded_bytes),0) "
            "FROM paper_producer_sources"
        ).fetchone()
        documents = self.connection.execute(
            "SELECT COALESCE(SUM(length(CAST(document_json AS BLOB))),0) FROM paper_producer_records"
        ).fetchone()[0]
        if sources[0] + documents > MAX_STORED_BYTES or sources[1] + documents > MAX_EXPANDED_BYTES:
            raise ValueError("complete paper producer source history exceeds byte bounds")

    def _mac(self, kind: str, key: str, trial_id: str, raw: str):
        return hmac.new(
            self.key, _canonical(["paper_forward_producer/v1", kind, key, trial_id, raw]).encode(), "sha256"
        ).hexdigest()

    def _append(self, kind: str, key: str, trial_id: str, document):
        raw = document.model_dump_json()
        if len(raw.encode()) > MAX_RECEIPT_BYTES:
            raise ValueError("paper producer receipt exceeds bounded document size")
        existing = self._row(kind, key, trial_id)
        if existing is not None:
            if existing["document_json"] != raw:
                raise ValueError("paper producer immutable record conflicts")
            return
        self.connection.execute(
            "INSERT INTO paper_producer_records VALUES (?,?,?,?,?,?)",
            (kind, key, trial_id, raw, document_hash(raw), self._mac(kind, key, trial_id, raw)),
        )

    def _row(self, kind: str, key: str, trial_id: str):
        size = self.connection.execute(
            "SELECT length(CAST(document_json AS BLOB)) FROM paper_producer_records WHERE kind=? AND record_key=?",
            (kind, key),
        ).fetchone()
        if size is None:
            return None
        if size[0] > MAX_RECEIPT_BYTES:
            raise ValueError("paper producer retained record exceeds byte bounds")
        row = self.connection.execute(
            "SELECT * FROM paper_producer_records WHERE kind=? AND record_key=?", (kind, key)
        ).fetchone()
        raw = row["document_json"]
        if (
            row["trial_id"] != trial_id
            or document_hash(raw) != row["document_sha256"]
            or not hmac.compare_digest(row["authentication"], self._mac(kind, key, trial_id, raw))
        ):
            raise ValueError("paper producer retained record authentication or scope changed")
        return row

    def _retain_source(self, raw: str):
        digest = document_hash(raw)
        if len(raw.encode()) > MAX_SOURCE_BYTES:
            raise ValueError("complete paper producer source exceeds runtime bounds")
        existing = self.connection.execute(
            "SELECT 1 FROM paper_producer_sources WHERE source_sha256=?", (digest,)
        ).fetchone()
        if existing:
            if self._source(digest) != raw:
                raise ValueError("paper producer retained source changed")
        else:
            compressed = zlib.compress(raw.encode())
            self.connection.execute(
                "INSERT INTO paper_producer_sources VALUES (?,?,?,?)",
                (digest, len(raw.encode()), sha256(compressed).hexdigest(), compressed),
            )
        return digest

    def _source(self, digest: str):
        size = self.connection.execute(
            "SELECT expanded_bytes,length(compressed_data) FROM paper_producer_sources WHERE source_sha256=?", (digest,)
        ).fetchone()
        if size is None or not 0 < size[0] <= MAX_SOURCE_BYTES or not 0 < size[1] <= MAX_SOURCE_BYTES:
            raise ValueError("paper producer source is missing or oversized")
        row = self.connection.execute(
            "SELECT * FROM paper_producer_sources WHERE source_sha256=?", (digest,)
        ).fetchone()
        compressed = row["compressed_data"]
        if sha256(compressed).hexdigest() != row["compressed_sha256"]:
            raise ValueError("paper producer compressed source changed")
        decoder = zlib.decompressobj()
        try:
            data = decoder.decompress(compressed, size[0] + 1)
            raw = data.decode()
        except (zlib.error, UnicodeDecodeError) as error:
            raise ValueError("paper producer retained source encoding changed") from error
        if (
            len(data) != size[0]
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
            or document_hash(raw) != digest
            or _canonical(json.loads(raw)) != raw
        ):
            raise ValueError("paper producer retained source expansion or digest changed")
        return raw

    def binding(self, trial_id: str) -> PaperProducerBinding:
        self._pins()
        self._bounds()
        row = self._row("binding", trial_id, trial_id)
        if row is None:
            raise ValueError("paper producer must be bound before collection")
        binding = PaperProducerBinding.model_validate_json(row["document_json"])
        protocol = self.registry.protocol(trial_id)
        if (
            binding.deployment_id != self.collector.deployment_id
            or binding.database_identity != self.collector._identity()
            or binding.policy != self.policy
            or binding.controller_sha256 != self.controller_sha256
            or binding.protocol_sha256 != document_hash(protocol.model_dump_json())
            or binding.registered_at > self.clock.now()
        ):
            raise ValueError("paper producer binding no longer matches protected scope")
        return binding

    def bind(self, trial_id: str, portfolio_ids: dict[str, str]) -> PaperProducerBinding:
        with self._atomic(), self.database.snapshot():
            existing = self._row("binding", trial_id, trial_id)
            if existing:
                result = self.binding(trial_id)
                if {item.arm: item.portfolio_id for item in result.arms} != portfolio_ids:
                    raise ValueError("four-arm paper portfolio mapping cannot change")
                return result
            protocol = self.registry.protocol(trial_id)
            runtime_binding = self.collector._binding(trial_id)
            if set(portfolio_ids) != set(ARMS) or portfolio_ids["agent"] != protocol.portfolio_id:
                raise ValueError("exact registered agent and three baseline portfolio identities required")
            if self.clock.now() >= protocol.forward_blocks[0].start or self.registry.observations(trial_id):
                raise ValueError("bind producer before untouched forward data")
            duration = (protocol.forward_blocks[-1].end - protocol.forward_blocks[0].start).total_seconds()
            if any(
                (block.end - block.start).total_seconds() % self.policy.sampling_seconds
                for block in protocol.forward_blocks
            ):
                raise ValueError("sampling grid must include every fixed block boundary")
            count = int(duration // self.policy.sampling_seconds) + 1
            if count > 256:
                raise ValueError("complete sampling schedule exceeds protected bounds")
            times = tuple(
                protocol.forward_blocks[0].start + timedelta(seconds=i * self.policy.sampling_seconds)
                for i in range(count)
            )
            expected = {
                "agent": protocol.selected_version_sha256,
                **{item.arm: item.artifact_sha256 for item in protocol.baselines},
            }
            arms = []
            ledger = Ledger(self.database, self.clock)
            for arm in ARMS:
                pid = portfolio_ids[arm]
                row = self.database.execute("SELECT * FROM portfolios WHERE portfolio_id=?", (pid,)).fetchone()
                version = self.database.execute(
                    "SELECT a.version_id,h.artifact_hash FROM active_versions a JOIN version_history h "
                    "ON h.portfolio_id=a.portfolio_id AND h.version_id=a.version_id WHERE a.portfolio_id=?",
                    (pid,),
                ).fetchone()
                view = ledger.equity(pid)
                if (
                    row is None
                    or row["mode"] != "paper"
                    or row["status"] != "open"
                    or row["reporting_currency"] != "EUR"
                    or row["reset_of"] is not None
                    or version is None
                    or version["artifact_hash"] != expected[arm]
                    or view.provisional
                    or view.equity != protocol.capital_eur
                ):
                    raise ValueError("each arm requires actual open EUR paper capital and predeclared active artifact")
                arms.append(
                    PaperArmBinding(
                        arm=arm,
                        portfolio_id=pid,
                        version_id=version["version_id"],
                        artifact_sha256=version["artifact_hash"],
                    )
                )
            raw = self.collector._read()
            if not self.collector._initial_chronology(json.loads(raw), self.clock.now()):
                raise ValueError("producer binding contains future collected facts")
            self._check_continuity(None, json.loads(raw))
            binding = PaperProducerBinding(
                deployment_id=runtime_binding.deployment_id,
                trial_id=trial_id,
                protocol_sha256=runtime_binding.protocol_sha256,
                market_stream_id=protocol.market_stream_id,
                data_policy_sha256=protocol.data_policy_sha256,
                friction_policy_sha256=protocol.friction_policy_sha256,
                regime_classifier_sha256=protocol.regime_classifier_sha256,
                database_identity=runtime_binding.database_identity,
                registered_at=self.clock.now(),
                policy=self.policy,
                controller_sha256=self.controller_sha256,
                initial_source_sha256=self._retain_source(raw),
                arms=tuple(arms),
                sampling_times=times,
            )
            self._append("binding", trial_id, trial_id, binding)
            return binding

    def _checkpoint(self, binding: PaperProducerBinding, index: int):
        row = self._row("checkpoint", f"{binding.trial_id}:{index}", binding.trial_id)
        if row is None:
            raise ValueError("missing preregistered paper checkpoint")
        value = PaperCheckpoint.model_validate_json(row["document_json"])
        if (
            value.binding_sha256 != document_hash(binding.model_dump_json())
            or value.sample_index != index
            or value.as_of != binding.sampling_times[index]
            or not value.as_of <= value.collected_at <= self.clock.now()
            or (value.collected_at - value.as_of).total_seconds() > binding.policy.maximum_lateness_seconds
        ):
            raise ValueError("paper checkpoint identity, sampling time or chronology changed")
        return value

    def _views(self, binding: PaperProducerBinding, raw: str, at: datetime):
        frozen = _FrozenRuntime(self.database, raw)
        try:
            ledger, equities, exposures = Ledger(frozen, self.clock), [], []
            data = json.loads(raw)
            self._arm_scope(binding, data)
            for arm in binding.arms:
                view = ledger.equity(arm.portfolio_id, utc_iso(at))
                if view.equity is None or view.provisional or view.stale or view.equity <= 0:
                    raise ValueError("paper checkpoint requires complete fresh EUR-valued equity")
                for lot in ledger.books(arm.portfolio_id, utc_iso(at)).lots:
                    if lot.open_quantity() == 0:
                        continue
                    marks = [
                        row
                        for row in data["valuation_marks"]
                        if row["portfolio_id"] == arm.portfolio_id
                        and row["asset"] == lot.asset
                        and parse_utc(row["observed_at"]) <= at
                    ]
                    if not marks:
                        raise ValueError("held paper asset lacks retained point-in-time mark")
                    mark = max(marks, key=lambda row: parse_utc(row["observed_at"]))
                    symbol = f"{lot.asset}/{mark['quote_currency']}"
                    observations = [
                        Observation.model_validate_json(row["document_json"])
                        for row in data["observations"]
                        if row["symbol"] == symbol and row["venue"] == "paper" and parse_utc(row["available_at"]) <= at
                    ]
                    if symbol not in binding.policy.symbols or not observations:
                        raise ValueError("paper mark lacks predeclared retained quote identity")
                    quote = max(observations, key=lambda value: (value.event_time_utc, value.available_at_utc))
                    if (
                        quote.bid is None
                        or quote.ask is None
                        or quote.ask < quote.bid
                        or quote.bid <= 0
                        or not quote.event_time_utc <= quote.available_at_utc <= at
                        or (at - quote.event_time_utc).total_seconds() > binding.policy.maximum_quote_age_seconds
                        or mark["convention"] != "mid"
                        or Decimal(mark["mark"]) != (quote.bid + quote.ask) / 2
                        or parse_utc(mark["observed_at"]) < quote.available_at_utc
                    ):
                        raise ValueError("paper mark and current retained midpoint quote disagree")
                exposure = view.inventory_reporting / view.equity
                if not 0 <= exposure <= 1:
                    raise ValueError("paper arm is outside long-only exposure assumptions")
                equities.append(str(view.equity))
                exposures.append(str(exposure))
            return tuple(equities), tuple(exposures)
        finally:
            frozen.connection.close()

    @staticmethod
    def _arm_scope(binding, data):
        portfolios = {row["portfolio_id"]: row for row in data["portfolios"]}
        versions = {row["portfolio_id"]: row for row in data["active_versions"]}
        for arm in binding.arms:
            portfolio, version = portfolios.get(arm.portfolio_id), versions.get(arm.portfolio_id)
            if (
                portfolio is None
                or version is None
                or portfolio["mode"] != "paper"
                or portfolio["status"] != "open"
                or portfolio["reset_of"] is not None
                or portfolio["reporting_currency"] != "EUR"
                or (version["version_id"], version["artifact_hash"]) != (arm.version_id, arm.artifact_sha256)
            ):
                raise ValueError("paper arm reset, mode, capital identity or selected version changed")
            if arm.arm == "cash" and any(row["portfolio_id"] == arm.portfolio_id for row in data["fills"]):
                raise ValueError("cash baseline cannot contain native trading facts")
            if arm.arm == "buy_and_hold":
                fills = [
                    FillRecord.model_validate_json(row["document_json"])
                    for row in data["fills"]
                    if row["portfolio_id"] == arm.portfolio_id
                ]
                intents = {fill.intent_id for fill in fills}
                if any(fill.side != "buy" for fill in fills) or len(intents) > 1 or (fills and None in intents):
                    raise ValueError("buy-and-hold baseline requires one actual acquisition intent and no sales")

    def checkpoint(self, trial_id: str) -> PaperCheckpoint:
        with self._atomic(), self.database.snapshot(), localcontext(Context(prec=100)):
            binding = self.binding(trial_id)
            index = self.connection.execute(
                "SELECT COUNT(*) FROM paper_producer_records WHERE kind='checkpoint' AND trial_id=?", (trial_id,)
            ).fetchone()[0]
            if index >= len(binding.sampling_times):
                raise ValueError("all preregistered samples already collected")
            at = binding.sampling_times[index]
            if not 0 <= (self.clock.now() - at).total_seconds() <= binding.policy.maximum_lateness_seconds:
                raise ValueError("paper checkpoint is outside its predeclared sampling window")
            raw = self.collector._read()
            data = json.loads(raw)
            if not self.collector._initial_chronology(data, self.clock.now()):
                raise ValueError("paper checkpoint contains future collected facts")
            reasons, _, _, _ = self.collector._audit(data, self.collector._binding(trial_id))
            if any(item.startswith("invalid:") for item in reasons):
                raise ValueError("paper runtime source audit failed")
            self._check_continuity(binding, data)
            equities, exposures = self._views(binding, raw, at)
            value = PaperCheckpoint(
                trial_id=trial_id,
                binding_sha256=document_hash(binding.model_dump_json()),
                sample_index=index,
                as_of=at,
                collected_at=self.clock.now(),
                source_sha256=self._retain_source(raw),
                equities_eur=equities,
                exposures=exposures,
            )
            self._append("checkpoint", f"{trial_id}:{index}", trial_id, value)
            return value

    def _check_continuity(self, binding: PaperProducerBinding | None, data):
        current = self.collector._source_facts(data)
        digests = []
        # Retain prior trials and expenses across the complete deployment.
        bindings = self.connection.execute("SELECT trial_id FROM paper_producer_records WHERE kind='binding'")
        for row in bindings:
            previous = self.binding(row["trial_id"])
            digests.append(previous.initial_source_sha256)
            rows = self.connection.execute(
                "SELECT record_key FROM paper_producer_records WHERE kind='checkpoint' AND trial_id=?",
                (previous.trial_id,),
            )
            for checkpoint_row in rows:
                index = int(checkpoint_row["record_key"].rsplit(":", 1)[1])
                digests.append(self._checkpoint(previous, index).source_sha256)
        for digest in digests:
            original = self.collector._source_facts(json.loads(self._source(digest)))
            if any(current.get(key) != value for key, value in original.items()):
                raise ValueError("paper producer immutable runtime history changed")

    def _derive_block(self, binding, block_index, checkpoints):
        protocol = self.registry.protocol(binding.trial_id)
        block = protocol.forward_blocks[block_index]
        raw = self._source(checkpoints[-1].source_sha256)
        data = json.loads(raw)
        frozen = _FrozenRuntime(self.database, raw)
        try:
            ledger = Ledger(frozen, self.clock)
            start, end = utc_iso(block.start), utc_iso(block.end)
            arms, signed_fees = [], []
            for position, arm in enumerate(binding.arms):
                result = ledger.performance(arm.portfolio_id, start, end)
                fees, rebates, turnover, slippage = Decimal(0), Decimal(0), Decimal(0), Decimal(0)
                for row in data["fills"]:
                    if row["portfolio_id"] != arm.portfolio_id:
                        continue
                    fill = FillRecord.model_validate_json(row["document_json"])
                    if not block.start < fill.filled_at_utc <= block.end:
                        continue
                    if fill.venue != "paper" or fill.symbol not in binding.policy.symbols:
                        raise ValueError("forward arm includes an out-of-scope native fill")
                    base, quote = fill.symbol.split("/")
                    rates = ledger._rates(utc_iso(fill.filled_at_utc))

                    def eur(amount, currency):
                        value, stale = _convert(amount, currency, "EUR", rates, utc_iso(fill.filled_at_utc))
                        if value is None or stale:
                            raise ValueError("paper fill requires retained fresh identified EUR FX")
                        return value

                    turnover += eur(fill.quote_principal, quote)
                    # The vector interface preserves signed per-asset fees. Older
                    # installations retain their original one-leg contract.
                    if hasattr(fill, "fee_legs"):
                        legs = ((leg.amount, leg.asset, leg.identified_rate) for leg in fill.fee_legs())
                    else:
                        legs = ((fill.fee_amount, fill.fee_asset, fill.fee_identified_rate),)
                    for amount, asset, identified in legs:
                        if asset == quote:
                            cost = eur(amount, quote)
                        elif asset == base:
                            cost = eur(amount * fill.price, quote)
                        elif identified is not None:
                            cost = eur(amount * identified, quote)
                        elif amount == 0:
                            cost = Decimal(0)
                        else:
                            raise ValueError("paper fee leg lacks identified quote valuation")
                        fees += max(cost, Decimal(0))
                        rebates += max(-cost, Decimal(0))
                    quotes = [
                        Observation.model_validate_json(item["document_json"])
                        for item in data["observations"]
                        if item["venue"] == "paper"
                        and item["symbol"] == fill.symbol
                        and parse_utc(item["available_at"]) <= fill.filled_at_utc
                    ]
                    if not quotes:
                        raise ValueError("paper fill lacks retained pre-fill quote")
                    quote_fact = max(quotes, key=lambda value: (value.event_time_utc, value.available_at_utc))
                    if (
                        quote_fact.bid is None
                        or quote_fact.ask is None
                        or quote_fact.event_time_utc > quote_fact.available_at_utc
                        or (fill.filled_at_utc - quote_fact.event_time_utc).total_seconds()
                        > binding.policy.maximum_quote_age_seconds
                    ):
                        raise ValueError("paper fill quote is stale or incomplete")
                    mid = (quote_fact.bid + quote_fact.ask) / 2
                    adverse = (
                        fill.quote_principal - mid * fill.quantity
                        if fill.side == "buy"
                        else mid * fill.quantity - fill.quote_principal
                    )
                    slippage += max(Decimal(0), eur(adverse, quote))
                equities = [Decimal(sample.equities_eur[position]) for sample in checkpoints]
                peak, drawdown = equities[0], Decimal(0)
                for equity in equities:
                    peak = max(peak, equity)
                    drawdown = max(drawdown, (peak - equity) / peak)
                if result.provisional or result.equity_start != equities[0] or result.equity_end != equities[-1]:
                    raise ValueError("frozen forward equity does not match preregistered samples")
                errors = sum(
                    row["portfolio_id"] == arm.portfolio_id
                    and start < row["created_at"] <= end
                    and row["state"] in {"REJECTED", "UNKNOWN"}
                    for row in data["order_intents"]
                )
                arms.append(
                    ArmPerformance(
                        arm=arm.arm,
                        opening_equity_eur=equities[0],
                        closing_equity_eur=equities[-1],
                        external_flows_eur=result.external_flow,
                        embedded_operating_expenses_eur=result.embedded_operating,
                        trading_fees_eur=fees,
                        trading_rebates_eur=rebates,
                        measured_slippage_eur=slippage,
                        turnover_eur=turnover,
                        maximum_drawdown_fraction=drawdown.quantize(Decimal("1e-18"), rounding=ROUND_CEILING),
                        mean_exposure_fraction=(
                            sum(Decimal(sample.exposures[position]) for sample in checkpoints[:-1])
                            / (len(checkpoints) - 1)
                        ).quantize(Decimal("1e-18"), rounding=ROUND_HALF_EVEN),
                        operational_errors=errors,
                        source_ref=f"paper-producer:{binding.trial_id}:{block_index}:{arm.arm}",
                    )
                )
                signed_fees.append(str(fees - rebates))
            decisions = []
            versions = {
                (row["portfolio_id"], row["version_id"]): row["artifact_hash"] for row in data["version_history"]
            }
            snapshots = {row["snapshot_id"]: row for row in data["snapshots"]}
            for row in data["decisions"]:
                if row["portfolio_id"] != binding.arms[0].portfolio_id or not start <= row["created_at"] < end:
                    continue
                decision = Decision.model_validate_json(row["payload_json"])
                outcome = decision.created_at_utc + timedelta(seconds=decision.horizon_seconds)
                snapshot = snapshots.get(decision.snapshot_id)
                version = versions.get((decision.portfolio_id, decision.system_version_id))
                if snapshot is None or version not in protocol.allowed_versions_sha256 or outcome > block.end:
                    raise ValueError(
                        "actual decision lacks preregistered version/input or complete in-block outcome horizon"
                    )
                decisions.append(
                    DecisionEvidence(
                        decision_id=decision.record_id,
                        window=Window(start=decision.created_at_utc, end=outcome),
                        latest_input_available_at=parse_utc(snapshot["as_of"]),
                        version_sha256=version,
                        source_ref=f"paper-decision:{decision.record_id}",
                        useful=False,
                    )
                )
            opportunities = sum(
                row["venue"] == "paper"
                and row["symbol"] in binding.policy.symbols
                and block.start <= parse_utc(row["available_at"]) < block.end
                for row in data["observations"]
            )
            observation = ForwardObservation(
                block_index=block_index,
                window=block,
                evidence_kind="forward_paper",
                available_at=checkpoints[-1].collected_at,
                data_policy_sha256=binding.data_policy_sha256,
                friction_policy_sha256=binding.friction_policy_sha256,
                regime_classifier_sha256=binding.regime_classifier_sha256,
                regime="unassessed",
                source_ref=f"paper-producer:{binding.trial_id}:{block_index}",
                paper_venue_differences=(
                    "paper_fills_are_assumptions",
                    "drawdown_and_exposure_use_preregistered_samples",
                ),
                independence_status="unassessed",
                opportunities=opportunities,
                decisions=tuple(decisions),
                arms=tuple(arms),
            )
            return observation, tuple(signed_fees)
        finally:
            frozen.connection.close()

    def collect_block(self, trial_id: str, block_index: int) -> PaperBlockReceipt:
        with self._atomic(), self.database.snapshot(), localcontext(Context(prec=100)):
            binding = self.binding(trial_id)
            protocol = self.registry.protocol(trial_id)
            if type(block_index) is not int or not 0 <= block_index < len(protocol.forward_blocks):
                raise ValueError("exact declared forward block index required")
            key = f"{trial_id}:{block_index}"
            existing = self._row("block", key, trial_id)
            if existing:
                return PaperBlockReceipt.model_validate_json(existing["document_json"])
            prior = self.connection.execute(
                "SELECT COUNT(*) FROM paper_producer_records WHERE kind='block' AND trial_id=?", (trial_id,)
            ).fetchone()[0]
            if prior != block_index:
                raise ValueError("collect every predeclared block in chronological order")
            block = protocol.forward_blocks[block_index]
            if self.clock.now() < block.end:
                raise ValueError("forward block outcome is not available")
            samples = tuple(
                self._checkpoint(binding, i)
                for i, at in enumerate(binding.sampling_times)
                if block.start <= at <= block.end
            )
            observation, fees = self._derive_block(binding, block_index, samples)
            receipt = PaperBlockReceipt(
                trial_id=trial_id,
                binding_sha256=document_hash(binding.model_dump_json()),
                block_index=block_index,
                checkpoint_sha256=tuple(document_hash(sample.model_dump_json()) for sample in samples),
                observation=observation,
                observation_sha256=document_hash(observation.model_dump_json()),
                signed_native_fee_totals_eur=fees,
                limitations=(
                    "official_transport_authentication_pending",
                    "complete_invoices_pending",
                    "independence_assessment_pending",
                    "regime_classification_pending",
                    "useful_decision_assessment_pending",
                    "baseline_policy_execution_review_pending",
                    "protected_intended_host_approval_pending",
                    "non_order_operational_error_assessment_pending",
                ),
            )
            self._append("block", key, trial_id, receipt)
            return receipt

    def block_receipt(self, trial_id: str, block_index: int) -> PaperBlockReceipt:
        """Read and independently verify one already collected immutable block."""
        with self._atomic():
            row = self._row("block", f"{trial_id}:{block_index}", trial_id)
            if row is None:
                raise ValueError("paper block receipt has not been collected")
            receipt = PaperBlockReceipt.model_validate_json(row["document_json"])
        self.verify(receipt)
        return receipt

    def publish(self, receipt: PaperBlockReceipt):
        """Recoverable two-stage publication: retain the source receipt before import.

        A crash after registry append is idempotently recovered by exact equality;
        no candidate, provider or trading action is rerun to reconstruct evidence.
        """
        self.verify(receipt)
        prior = self.registry.observations(receipt.trial_id)
        if len(prior) > receipt.block_index:
            if prior[receipt.block_index] != receipt.observation:
                raise ValueError("retained observation conflicts with protected producer receipt")
            return
        self.registry.observe(receipt.trial_id, receipt.observation)

    def verify(self, receipt: PaperBlockReceipt) -> PaperProducerVerification:
        receipt = PaperBlockReceipt.model_validate(receipt.model_dump())
        with self._atomic(), self.database.snapshot(), localcontext(Context(prec=100)):
            binding = self.binding(receipt.trial_id)
            row = self._row("block", f"{receipt.trial_id}:{receipt.block_index}", receipt.trial_id)
            if (
                row is None
                or row["document_json"] != receipt.model_dump_json()
                or receipt.binding_sha256 != document_hash(binding.model_dump_json())
            ):
                raise ValueError("paper block is not retained under the exact producer binding")
            block = self.registry.protocol(receipt.trial_id).forward_blocks[receipt.block_index]
            samples = tuple(
                self._checkpoint(binding, i)
                for i, at in enumerate(binding.sampling_times)
                if block.start <= at <= block.end
            )
            for sample in samples:
                equities, exposures = self._views(binding, self._source(sample.source_sha256), sample.as_of)
                if (equities, exposures) != (sample.equities_eur, sample.exposures):
                    raise ValueError("paper checkpoint does not match its retained native source")
            observation, fees = self._derive_block(binding, receipt.block_index, samples)
            if (
                observation != receipt.observation
                or fees != receipt.signed_native_fee_totals_eur
                or tuple(document_hash(sample.model_dump_json()) for sample in samples) != receipt.checkpoint_sha256
            ):
                raise ValueError("paper block does not match actual retained service derivation")
            self._check_continuity(binding, json.loads(self.collector._read()))
            observations = self.registry.observations(receipt.trial_id)
            published = len(observations) > receipt.block_index
            if published and observations[receipt.block_index] != observation:
                raise ValueError("paper registry observation differs from exact protected producer receipt")
            return PaperProducerVerification(
                binding_sha256=receipt.binding_sha256,
                block_receipt_sha256=document_hash(receipt.model_dump_json()),
                historical_sources_consistent=True,
                observation_published=published,
                limitations=receipt.limitations,
            )
