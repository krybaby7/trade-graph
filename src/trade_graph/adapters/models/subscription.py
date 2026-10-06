"""Configurable subscription inference with protected admission and durable recovery.

This path never constructs provider HTTP or receives authentication tokens. CLI
login stays inside an owner-provisioned native Linux installation. Known terminal
failures can retry or use independently admitted subscription fallback routes.
Subscription receipts are separate from API expenses and virtual paper equity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import Event, Lock
from typing import Literal, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import Field
from referencing.exceptions import Unresolvable

from trade_graph.adapters.models.providers import lookup_capabilities
from trade_graph.application.gateway import _bounded_json, _validate_schema
from trade_graph.contracts.models import ContractModel, ModelRequest, ModelResult, ModelUsage
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import StaleState

SubscriptionProvider = Literal["codex_subscription", "claude_subscription"]
MAX_INPUT_BYTES = 131_072
MAX_OUTPUT_BYTES = 262_144
_PRIVATE_KEYS = frozenset({"credentials", "api_key", "authorization", "auth_token", "database", "owner_policy",
                          "private_account", "kraken_credentials", "private_key", "ssh_key"})
_QUOTA_KEYS = frozenset({"remaining_percent", "used_percent", "window_duration_mins", "resets_at", "credits_balance",
                        "source", "observed_at", "weekly", "five_hour", "ordinary_usage_allowed"})


class SubscriptionConfig(ContractModel):
    provider: SubscriptionProvider = "codex_subscription"
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    enabled: bool = False
    maximum_seconds: int = Field(default=600, ge=1, le=3600)
    maximum_output_tokens: int = Field(default=16_384, ge=1, le=131_072)
    maximum_turns: int = Field(default=8, ge=1, le=128)
    maximum_output_bytes: int = Field(default=2_097_152, ge=16_384, le=16_777_216)
    application_max_attempts: int = Field(default=3, ge=1, le=10)
    cli_transport_retries: int = Field(default=2, ge=0, le=10)
    cli_structured_output_attempts: int = Field(default=3, ge=1, le=10)
    allowed_context_keys: dict[str, list[str]] = Field(default_factory=dict)
    department_tools: dict[str, list[Literal["web_search"]]] = Field(default_factory=dict)
    maximum_tool_calls: int = Field(default=16, ge=0, le=128)


@dataclass(frozen=True)
class SubscriptionReadiness:
    provider: SubscriptionProvider
    cli_version: str
    ready: bool
    blockers: tuple[str, ...]
    quota: dict
    authentication: str
    isolation: str
    application_max_attempts: int = 3
    fallback_enabled: bool = False

    def public_status(self) -> dict:
        return {"provider": self.provider, "cli_version": self.cli_version, "ready": self.ready,
                "blockers": list(self.blockers), "quota": sanitize_quota(self.quota),
                "authentication": self.authentication, "isolation": self.isolation,
                "billing_kind": "subscription", "actual_cost_native": None, "cost_status": "unknown",
                "application_automatic_retry": self.application_max_attempts > 1,
                "application_max_attempts": self.application_max_attempts,
                "application_automatic_fallback": self.fallback_enabled,
                "provider_limits_admitted": self.ready}


def sanitize_quota(quota: dict) -> dict:
    def clean_fields(document: dict, *, root: bool) -> dict:
        result = {}
        for key, value in document.items():
            if key not in _QUOTA_KEYS:
                continue
            if key in {"weekly", "five_hour"}:
                if root and isinstance(value, dict) and (fields := clean_fields(value, root=False)):
                    result[key] = fields
                elif root and value is None:
                    result[key] = None
                continue
            if value is None:
                result[key] = None
            elif key == "ordinary_usage_allowed" and type(value) is bool:
                result[key] = value
            elif key in {"remaining_percent", "used_percent"}:
                if type(value) in (int, float) and 0 <= value <= 100 and math.isfinite(value):
                    result[key] = value
            elif key in {"window_duration_mins", "resets_at"}:
                bound = 525_600 if key == "window_duration_mins" else 32_503_680_000
                if type(value) is int and 0 <= value <= bound:
                    result[key] = value
            elif key == "credits_balance" and type(value) is str and len(value) <= 64:
                try:
                    number = Decimal(value)
                    if number.is_finite() and number >= 0:
                        result[key] = value
                except InvalidOperation:
                    pass
            elif key == "source" and type(value) is str and value in {
                "codex-app-server", "claude-cli", "owner-reviewed-status"
            }:
                result[key] = value
            elif key == "observed_at" and type(value) is str and len(value) <= 40:
                try:
                    if datetime.fromisoformat(value).tzinfo is not None:
                        result[key] = value
                except ValueError:
                    pass
        return result

    return clean_fields(quota, root=True)


def assess_subscription(config: SubscriptionConfig, *, cli_version: str, authentication: str, quota: dict,
                        extra_usage_disabled: bool, isolation_ready: bool, native_linux: bool) -> SubscriptionReadiness:
    """Protected-controller metadata only; never accepts evidence from model output."""
    blockers = []
    if not config.enabled:
        blockers.append("subscription inference is disabled by protected runtime configuration")
    if config.provider == "codex_subscription":
        if authentication != "chatgpt":
            blockers.append("official ChatGPT subscription login is not established")
        numbers = tuple(int(value) for value in re.findall(r"\d+", cli_version)[:3])
        if len(numbers) != 3 or numbers < (0, 160, 1):
            blockers.append("Codex CLI 0.160.1 or later is required for the verified native execution controls")
    else:
        if authentication != "subscription":
            blockers.append("official Claude subscription login is not established")
        numbers = tuple(int(value) for value in re.findall(r"\d+", cli_version)[:3])
        if len(numbers) != 3 or numbers < (2, 1, 285):
            blockers.append("Claude Code 2.1.285 or later is required for documented native execution controls")
    if not native_linux:
        blockers.append("native Linux CLI required; Windows/WSL interop executables are refused")
    clean = sanitize_quota(quota)
    ordinary_codex_only = (config.provider == "codex_subscription" and clean.get("ordinary_usage_allowed") is True
                          and clean.get("credits_balance") == "0" and not _quota_exhausted(clean))
    if ((config.provider == "codex_subscription" and not ordinary_codex_only)
            or (config.provider == "claude_subscription" and not extra_usage_disabled)):
        blockers.append("subscription-only allowance is not verified; paid extras remain unauthorized")
    if not isolation_ready:
        blockers.append("owner-pinned Linux filesystem/process isolation has not passed its host probe")
    clean = sanitize_quota(quota)
    if _quota_exhausted(clean) or clean.get("ordinary_usage_allowed") is False:
        blockers.append("subscription quota exhausted; new AI work is paused")
    return SubscriptionReadiness(config.provider, cli_version, not blockers, tuple(blockers), clean,
                                 authentication, "linux-bubblewrap" if isolation_ready else "unavailable",
                                 config.application_max_attempts)


def _quota_exhausted(quota: dict) -> bool:
    return any(_quota_exhausted(value) for value in quota.values() if isinstance(value, dict)) or (
        isinstance(quota.get("remaining_percent"), (int, float)) and quota["remaining_percent"] <= 0
    ) or (isinstance(quota.get("used_percent"), (int, float)) and quota["used_percent"] >= 100)


@dataclass(frozen=True)
class CliOutcome:
    stdout: str
    exit_code: int
    stopped: str = ""
    error_category: str = ""


class SubscriptionExecutor(Protocol):
    def execute(self, request: ModelRequest, *, cancel_event: Event | None = None) -> CliOutcome: ...


class SubscriptionJournal:
    """Controller-owned SQLite journal; migration 0018 creates the additive tables."""

    def __init__(self, database, clock) -> None:
        self.database, self.clock = database, clock

    def row(self, invocation_id: str) -> dict | None:
        row = self.database.execute("SELECT * FROM subscription_invocations WHERE invocation_id=?",
                                    (invocation_id,)).fetchone()
        return dict(row) if row else None

    def provider_status(self, provider: str) -> dict:
        row = self.database.execute("SELECT * FROM subscription_provider_state WHERE provider=?",
                                    (provider,)).fetchone()
        return dict(row) if row else {"provider": provider, "ai_paused": False, "reason": "", "quota_json": "{}"}

    def begin(self, invocation_id: str, request: ModelRequest, provider: str, quota: dict) -> ModelResult | None:
        binding = hashlib.sha256((provider + request.model_dump_json()).encode()).hexdigest()
        with self.database.immediate():
            row = self.row(invocation_id)
            if row:
                if row["request_hash"] != binding:
                    raise StaleState("subscription invocation identity reused with a different request")
                return self.recover_result(invocation_id)
            existing_task = self.database.execute("SELECT invocation_id FROM subscription_invocations WHERE task_id=?",
                                                  (request.task_id,)).fetchone()
            if existing_task is not None and request.role != "engineer":
                raise StaleState("subscription task already has a persisted invocation; no new attempt permitted")
            now = utc_iso(self.clock.now())
            self.database.execute("""INSERT INTO subscription_invocations
                (invocation_id,request_hash,task_id,root_task_id,role,run_id,system_version_id,provider,
                 requested_model,state,quota_json,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,'DISPATCHED',?,?,?)""",
                (invocation_id, binding, request.task_id, request.root_task_id, request.role, request.run_id,
                 request.system_version_id, provider, request.model, json.dumps(sanitize_quota(quota)), now, now))
        return None

    def recover_result(self, invocation_id: str) -> ModelResult | None:
        """Recover terminal receipts after a crash without replaying any dispatch."""
        with self.database.immediate():
            row = self.row(invocation_id)
            if row is None:
                return None
            if row["result_json"]:
                return ModelResult.model_validate_json(row["result_json"])
            attempts = self.database.execute(
                "SELECT * FROM subscription_attempts WHERE invocation_id=? ORDER BY attempt_index",
                (invocation_id,)).fetchall()
            if attempts and all(attempt["state"] in {"COMPLETED", "FAILED"} and attempt["result_json"]
                                for attempt in attempts):
                results = [ModelResult.model_validate_json(attempt["result_json"]) for attempt in attempts]
                result = results[-1].model_copy(update={"usage": aggregate_usage(results)})
                self.save(invocation_id, result, "COMPLETED" if result.ok else "FAILED")
                return result
            result = ModelResult(ok=False, failure="timeout_uncertain",
                message="prior subscription dispatch has no durable response; no replay permitted")
            for attempt in attempts:
                if attempt["state"] == "DISPATCHED" or not attempt["result_json"]:
                    self.save_attempt(attempt["attempt_id"], result, "UNCERTAIN")
            self.save(invocation_id, result, "UNCERTAIN")
            return result

    def save(self, invocation_id: str, result: ModelResult, state: str, *, dispatched: bool = True) -> None:
        with self.database.immediate():
            self.database.execute("""UPDATE subscription_invocations SET state=?,result_json=?,actual_model=?,
                usage_json=?,cost_status=?,updated_at=? WHERE invocation_id=?""",
                (state, result.model_dump_json(), result.provider_model,
                 result.usage.model_dump_json() if result.usage else None,
                 "unknown" if dispatched else "not_incurred", utc_iso(self.clock.now()), invocation_id))

    def begin_attempt(self, invocation_id: str, index: int, request: ModelRequest, provider: str) -> str:
        attempt_id = f"{invocation_id}:attempt:{index}"
        binding = hashlib.sha256((provider + request.model_dump_json()).encode()).hexdigest()
        now = utc_iso(self.clock.now())
        with self.database.immediate():
            self.database.execute("""INSERT INTO subscription_attempts
                (attempt_id,invocation_id,attempt_index,request_hash,provider,requested_model,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'DISPATCHED',?,?)""",
                (attempt_id, invocation_id, index, binding, provider, request.model, now, now))
        return attempt_id

    def save_attempt(self, attempt_id: str, result: ModelResult, state: str) -> None:
        with self.database.immediate():
            self.database.execute("""UPDATE subscription_attempts SET state=?,result_json=?,actual_model=?,
                usage_json=?,updated_at=? WHERE attempt_id=?""",
                (state, result.model_dump_json(), result.provider_model,
                 result.usage.model_dump_json() if result.usage else None, utc_iso(self.clock.now()), attempt_id))

    def refresh_quota(self, provider: str, quota: dict) -> None:
        """Only a newer positive provider observation can lift a quota-derived stop."""
        clean = sanitize_quota(quota)
        state = self.provider_status(provider)
        observed = clean.get("observed_at")
        if (not state["ai_paused"] or not observed or clean.get("ordinary_usage_allowed") is not True
                or _quota_exhausted(clean) or not any(
                    isinstance(value, dict) and (value.get("remaining_percent", 0) > 0
                                                or "used_percent" in value and value["used_percent"] < 100)
                    for value in [clean, *clean.values()])):
            return
        if datetime.fromisoformat(observed) <= datetime.fromisoformat(state["updated_at"]):
            return
        with self.database.immediate():
            self.database.execute("""UPDATE subscription_provider_state
                SET ai_paused=0,reason='',quota_json=?,updated_at=?
                WHERE provider=? AND reason='subscription quota exhausted'""",
                (json.dumps(clean), utc_iso(self.clock.now()), provider))

    def pause_ai(self, provider: str, quota: dict) -> None:
        with self.database.immediate():
            self.database.execute("""INSERT INTO subscription_provider_state
                (provider,ai_paused,reason,quota_json,updated_at)
                VALUES (?,1,'subscription quota exhausted',?,?) ON CONFLICT(provider) DO UPDATE SET
                ai_paused=1,reason=excluded.reason,quota_json=excluded.quota_json,updated_at=excluded.updated_at""",
                (provider, json.dumps(sanitize_quota(quota)), utc_iso(self.clock.now())))


class SubscriptionAdapter:
    """Durable bounded attempts; unknown dispatches are never automatically replayed."""

    def __init__(self, config: SubscriptionConfig, readiness: SubscriptionReadiness,
                 executor: SubscriptionExecutor, *, fallbacks: tuple[SubscriptionAdapter, ...] = (),
                 readiness_probe: Callable[[], SubscriptionReadiness] | None = None) -> None:
        self.config, self.readiness, self.executor = config.model_copy(deep=True), readiness, executor
        if any(route.readiness.isolation != "linux-bubblewrap" for route in fallbacks):
            raise ValueError("fallback subscription routes require independently verified isolation")
        self.fallbacks = tuple(fallbacks)
        self.readiness_probe = readiness_probe
        self._readiness_lock, self._last_readiness = Lock(), float("-inf")

    def refresh_readiness(self, *, force: bool = False, minimum_interval_seconds: float = 60) -> SubscriptionReadiness:
        """Refresh official metadata only, preserving pinned profiles and unknown usage."""
        with self._readiness_lock:
            if self.readiness_probe and (force or time.monotonic() - self._last_readiness >= minimum_interval_seconds):
                try:
                    self.readiness = self.readiness_probe()
                finally:
                    self._last_readiness = time.monotonic()
        for route in self.fallbacks:
            route.refresh_readiness(force=force, minimum_interval_seconds=minimum_interval_seconds)
        return self.readiness

    def public_status(self, *, journal: SubscriptionJournal | None = None, refresh: bool = True) -> dict:
        if refresh:
            self.refresh_readiness()
        routes = (self, *self.fallbacks)
        if journal:
            for route in routes:
                journal.refresh_quota(route.config.provider, route.readiness.quota)
        available = [route for route in routes if route.config.enabled and route.readiness.ready
                     and (not journal or not journal.provider_status(route.config.provider)["ai_paused"])]
        return {**self.readiness.public_status(), "ready": self.config.enabled and bool(available),
                "application_automatic_fallback": bool(self.fallbacks),
                "available_subscription_routes": [route.config.provider for route in available]}


    def invoke(self, request: ModelRequest, *, invocation_id: str, journal: SubscriptionJournal,
               cancel_event: Event | None = None, before_attempt: Callable[[], None] | None = None) -> ModelResult:
        self.refresh_readiness()
        previous = journal.begin(invocation_id, request, self.config.provider, self.readiness.quota)
        if previous is not None:
            return previous
        try:
            self._validate_request(request)
        except (ValueError, SchemaError, ValidationError, Unresolvable):
            result = ModelResult(ok=False, failure="validation",
                                 message="subscription request outside protected bounds")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        for route in (self, *self.fallbacks):
            journal.refresh_quota(route.config.provider, route.readiness.quota)
        primary_quota_blocked = (journal.provider_status(self.config.provider)["ai_paused"]
                                 or _quota_exhausted(self.readiness.quota))
        if primary_quota_blocked:
            journal.pause_ai(self.config.provider, self.readiness.quota)
        routes = tuple(route for route in (self, *self.fallbacks) if route.config.enabled and route.readiness.ready
                       and route.readiness.provider == route.config.provider
                       and not journal.provider_status(route.config.provider)["ai_paused"]
                       and not _quota_exhausted(route.readiness.quota))
        if not self.config.enabled or not routes:
            result = ModelResult(ok=False, failure="rate_limit" if primary_quota_blocked else "credentials",
                message="subscription quota exhausted; new AI work paused" if primary_quota_blocked
                else "; ".join(self.readiness.blockers)[:500] or "subscription admission is disabled")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        if cancel_event and cancel_event.is_set():
            result = ModelResult(ok=False, failure="temporary",
                                     message="subscription task cancelled before dispatch")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        results = []
        route_index = 0
        for attempt in range(1, self.config.application_max_attempts + 1):
            route = routes[route_index]
            effective = request.model_copy(update={
                "provider": "openai" if route.config.provider == "codex_subscription" else "anthropic",
                "model": route.config.model,
                "timeout_seconds": min(request.timeout_seconds, route.config.maximum_seconds),
                "max_output_tokens": min(request.max_output_tokens, route.config.maximum_output_tokens)})
            try:
                route._validate_request(effective)
                if before_attempt:
                    before_attempt()
            except Exception:
                result = ModelResult(ok=False, failure="validation",
                                     message="subscription attempt authorization refused")
                break
            if cancel_event and cancel_event.is_set():
                result = ModelResult(ok=False, failure="temporary",
                                     message="subscription task cancelled before dispatch")
                break
            attempt_id = journal.begin_attempt(invocation_id, attempt, effective, route.config.provider)
            try:
                outcome = route.executor.execute(effective, cancel_event=cancel_event)
                parser = parse_codex_outcome if route.config.provider == "codex_subscription" else parse_claude_outcome
                result = parser(outcome, effective, maximum_turns=route.config.maximum_turns,
                                maximum_output_bytes=route.config.maximum_output_bytes)
            except Exception:
                result = ModelResult(ok=False, failure="timeout_uncertain",
                                     message="subscription dispatch outcome unknown")
            state = "COMPLETED" if result.ok else "UNCERTAIN" if result.failure == "timeout_uncertain" else "FAILED"
            journal.save_attempt(attempt_id, result, state)
            results.append(result)
            if result.failure == "rate_limit":
                journal.pause_ai(route.config.provider, route.readiness.quota)
            if result.ok or result.failure == "timeout_uncertain":
                break
            if (result.failure in {"credentials", "rate_limit", "unsupported", "temporary"}
                    and route_index + 1 < len(routes)):
                route_index += 1
            elif result.failure != "temporary":
                break
        aggregate = aggregate_usage(results)
        result = result.model_copy(update={"usage": aggregate})
        state = "COMPLETED" if result.ok else "UNCERTAIN" if result.failure == "timeout_uncertain" else "FAILED"
        journal.save(invocation_id, result, state, dispatched=bool(results))
        return result

    def _validate_request(self, request: ModelRequest) -> None:
        expected = "openai" if self.config.provider == "codex_subscription" else "anthropic"
        if (request.provider != expected or request.model != self.config.model or request.synthetic
                or lookup_capabilities(expected, request.model) is None
                or request.max_tool_calls < 0 or request.max_tool_calls > self.config.maximum_tool_calls
                or request.max_tool_calls and (self.config.provider != "codex_subscription"
                    or self.config.department_tools.get(request.role) != ["web_search"])
                or not 1 <= request.max_output_tokens <= self.config.maximum_output_tokens
                or not 1 <= request.timeout_seconds <= self.config.maximum_seconds):
            raise ValueError("subscription request bounds or provider mismatch")
        allowlist = self.config.allowed_context_keys.get(request.role)
        if allowlist is None or not set(request.context) <= set(allowlist):
            raise ValueError("role context is not explicitly allowlisted")
        if len(request.model_dump_json().encode()) > MAX_INPUT_BYTES:
            raise ValueError("subscription input size bound exceeded")
        _bounded_json(request.context)
        _bounded_json(request.output_schema)
        Draft202012Validator.check_schema(request.output_schema)
        context_values = [request.context]
        while context_values:
            value = context_values.pop()
            if isinstance(value, dict):
                context_values.extend(value.values())
            elif isinstance(value, list):
                context_values.extend(value)
            elif isinstance(value, str) and (
                value.startswith(("/", "\\\\", "file:", "../", "runtime/"))
                or re.match(r"^[A-Za-z]:[\\/]", value)
                or re.search(r"(?:^|\s)/(?:home|mnt|root|var|run|tmp|etc)/", value)
            ):
                raise ValueError("private filesystem paths are not model inputs")
        pending = [request.context, request.output_schema]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if set(item) & _PRIVATE_KEYS or "$ref" in item and not str(item["$ref"]).startswith("#/"):
                    raise ValueError("private inputs or external schema retrieval refused")
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)


def parse_claude_outcome(outcome: CliOutcome, request: ModelRequest, *, maximum_turns: int = 8,
                         maximum_output_bytes: int = 2_097_152) -> ModelResult:
    if outcome.stopped:
        return ModelResult(ok=False, failure="timeout_uncertain", message="bounded subscription process interrupted")
    usage, provider_model = None, None
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate provider JSON key")
                result[key] = value
            return result

        if len(outcome.stdout.encode()) > maximum_output_bytes:
            raise ValueError("subscription output bound exceeded")
        document = json.loads(outcome.stdout, object_pairs_hook=unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
        _bounded_json(document)
        if not isinstance(document, dict) or document.get("type") != "result":
            raise ValueError("expected one final CLI result")
        models = document.get("modelUsage", {})
        if isinstance(models, dict) and 1 <= len(models) <= 4 and all(
            type(model) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", model) for model in models
        ):
            provider_model = ",".join(sorted(models))
            counters = list(models.values())
            keys = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
            if all(isinstance(row, dict) and all(type(row.get(key, 0)) is int and 0 <= row.get(key, 0) <= 100_000_000
                    for key in keys) and "inputTokens" in row and "outputTokens" in row for row in counters):
                usage = ModelUsage(uncached_input_tokens=sum(row["inputTokens"] for row in counters),
                    billed_output_tokens=sum(row["outputTokens"] for row in counters),
                    cache_read_tokens=sum(row.get("cacheReadInputTokens", 0) for row in counters),
                    cache_write_tokens=sum(row.get("cacheCreationInputTokens", 0) for row in counters))
        if document.get("is_error") or outcome.exit_code != 0 or outcome.error_category == "quota":
            quota_words = ("rate_limit", "rate limit", "usage limit", "quota exhausted", "hit your limit")
            error_text = str([document.get("error"), document.get("result"), document.get("errors")]).lower()
            quota_error = outcome.error_category == "quota" or any(word in error_text for word in quota_words)
            failure = "rate_limit" if quota_error else "temporary"
            if document.get("subtype") == "error_max_structured_output_retries":
                failure = "validation"
            elif document.get("subtype") == "error_max_turns":
                failure = "truncation"
            return ModelResult(ok=False, failure=failure, usage=usage, provider_model=provider_model,
                               message="subscription quota exhausted" if quota_error else "subscription CLI failed")
        if not isinstance(models, dict) or list(models) != [request.model] or usage is None:
            raise ValueError("actual model missing, changed, or more than one model invoked")
        if "num_turns" in document and (type(document["num_turns"]) is not int
                                        or not 1 <= document["num_turns"] <= maximum_turns):
            raise ValueError("agentic turn count exceeds configured bounds")
        payload = document.get("structured_output")
        if not isinstance(payload, dict):
            raise ValueError("structured output is missing")
        _validate_schema(payload, request.output_schema)
        return ModelResult(ok=True, payload=payload, usage=usage, provider_model=request.model)
    except (ValueError, TypeError, KeyError, SchemaError, ValidationError, Unresolvable):
        if outcome.error_category == "quota":
            return ModelResult(ok=False, failure="rate_limit", message="subscription quota exhausted",
                               usage=usage, provider_model=provider_model)
        return ModelResult(ok=False, failure="validation", message="invalid structured subscription output",
                           usage=usage, provider_model=provider_model)


def claude_environment(maximum_output_tokens: int, config: SubscriptionConfig | None = None) -> dict[str, str]:
    """Fresh environment; API keys, gateway overrides and watchdog are never inherited."""
    limits = config or SubscriptionConfig(model="unselected")
    return {"CLAUDE_CODE_MAX_RETRIES": str(limits.cli_transport_retries),
            "MAX_STRUCTURED_OUTPUT_RETRIES": str(limits.cli_structured_output_attempts),
            "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES": str(limits.cli_transport_retries),
            "CLAUDE_CODE_MAX_TURNS": str(limits.maximum_turns),
            "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1", "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
            "CLAUDE_CODE_DISABLE_MODEL_ACCESS_FALLBACK": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1", "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1", "CLAUDE_CODE_DISABLE_AGENT_VIEW": "1",
            "CLAUDE_CODE_DISABLE_ARTIFACT": "1", "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
            "CLAUDE_CODE_DISABLE_CRON": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
            "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1", "CLAUDE_CODE_DISABLE_FAST_MODE": "1",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(maximum_output_tokens), "CLAUDE_CODE_SAFE_MODE": "1",
            "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"}


def claude_command(binary: str, request: ModelRequest, config: SubscriptionConfig | None = None) -> list[str]:
    limits = config or SubscriptionConfig(model=request.model)
    settings = {"switchModelsOnFlag": False, "availableModels": [request.model], "fallbackModel": []}
    return [binary, "-p", "--restricted", "--safe-mode", "--output-format", "json",
            "--max-turns", str(limits.maximum_turns),
            "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--settings", json.dumps(settings), "--no-session-persistence",
            "--model", request.model, "--json-schema", json.dumps(request.output_schema),
            "--system-prompt", request.instructions]


def aggregate_usage(results: list[ModelResult]) -> ModelUsage | None:
    """Missing usage in any dispatched attempt leaves the aggregate unknown."""
    if not results or any(result.usage is None for result in results):
        return None
    fields = ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "billed_output_tokens",
              "reasoning_tokens", "tool_units")
    return ModelUsage(**{field: sum(getattr(result.usage, field) for result in results) for field in fields})


def codex_command(binary: str, request: ModelRequest, *, schema_path: str = "/request/schema.json") -> list[str]:
    """Official noninteractive subscription invocation; no shell or ambient configuration."""
    settings = {"forced_login_method": "chatgpt", "model_provider": "openai",
                "web_search": "live" if request.max_tool_calls else "disabled",
                "features.shell_tool": False, "features.unified_exec": False, "features.apps": False,
                "features.view_image": False, "features.image_generation": False, "features.code_mode": False,
                "features.plugins": False, "features.shell_snapshot": False,
                "tools.update_plan.enabled": False, "tools.experimental_request_user_input.enabled": False,
                "features.code_mode_only": False, "features.code_mode_host": False,
                "model_catalog_json": "/request/model-catalog.json",
                "agents.enabled": False, "features.multi_agent_v2": False,
                "history.persistence": "none", "hide_agent_reasoning": True,
                "model_reasoning_summary": "none", "analytics.enabled": False, "feedback.enabled": False}
    command = [binary, "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
               "--skip-git-repo-check", "--sandbox", "read-only", "--output-schema", schema_path,
               "--model", request.model]
    for key, value in settings.items():
        command += ["-c", f"{key}={json.dumps(value)}"]
    return [*command, "-"]


def parse_codex_outcome(outcome: CliOutcome, request: ModelRequest, *, maximum_turns: int = 8,
                        maximum_output_bytes: int = 2_097_152) -> ModelResult:
    """Validate final JSONL without retaining reasoning, tool transcripts or identifiers."""
    if outcome.stopped:
        return ModelResult(ok=False, failure="timeout_uncertain", message="bounded subscription process interrupted")
    usage, actual_model, payload_text = None, None, None
    completed, failed, tool_observed, search_ids = 0, False, False, set()
    turn_usage: list[ModelResult] = []
    try:
        if len(outcome.stdout.encode()) > maximum_output_bytes:
            raise ValueError("subscription output bound exceeded")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate provider JSON key")
                value[key] = item
            return value
        for line in outcome.stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line, object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite value")))
            _bounded_json(event)
            kind = event.get("type")
            if event.get("model") is not None:
                if event["model"] != request.model:
                    raise ValueError("provider model changed")
                actual_model = event["model"]
            if kind == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    payload_text = item.get("text")
                elif item.get("type") == "web_search":
                    search_ids.add(item.get("id", f"search-{len(search_ids)}"))
                elif item.get("type") in {"command_execution", "file_change", "mcp_tool_call"}:
                    tool_observed = True
            elif kind == "turn.completed":
                completed += 1
                counters = event.get("usage", {})
                if (isinstance(counters, dict) and all(type(counters.get(key, 0)) is int and
                    0 <= counters.get(key, 0) <= 100_000_000 for key in
                    ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"))
                    and "input_tokens" in counters and "output_tokens" in counters
                    and counters.get("cached_input_tokens", 0) <= counters["input_tokens"]):
                    current = ModelUsage(
                        uncached_input_tokens=counters["input_tokens"] - counters.get("cached_input_tokens", 0),
                        cache_read_tokens=counters.get("cached_input_tokens", 0),
                        billed_output_tokens=counters["output_tokens"],
                        reasoning_tokens=counters.get("reasoning_output_tokens", 0))
                    turn_usage.append(ModelResult(ok=True, usage=current))
                else:
                    turn_usage.append(ModelResult(ok=True, usage=None))
                usage = aggregate_usage(turn_usage)
            elif kind in {"turn.failed", "error"}:
                failed = True
        if tool_observed or len(search_ids) > request.max_tool_calls or completed > maximum_turns:
            raise ValueError("unsupported tool execution or turn bound exceeded")
        if usage is not None:
            usage = usage.model_copy(update={"tool_units": len(search_ids)})
        if failed or outcome.exit_code != 0 or outcome.error_category:
            quota = outcome.error_category == "quota" or any(word in outcome.stdout.lower() for word in
                ("rate_limit", "usage_limit", "usage limit", "quota exhausted", "hit your limit"))
            return ModelResult(ok=False, failure="rate_limit" if quota else "temporary", usage=usage,
                provider_model=actual_model,
                message="subscription quota exhausted" if quota else "subscription CLI failed")
        if not completed:
            return ModelResult(ok=False, failure="timeout_uncertain", usage=usage,
                provider_model=actual_model, message="subscription terminal outcome missing")
        payload = json.loads(payload_text, object_pairs_hook=unique)
        if not isinstance(payload, dict):
            raise ValueError("structured output missing")
        _validate_schema(payload, request.output_schema)
        return ModelResult(ok=True, payload=payload, usage=usage, provider_model=actual_model)
    except (ValueError, TypeError, KeyError, AttributeError, SchemaError, ValidationError, Unresolvable):
        return ModelResult(ok=False, failure="validation", message="invalid structured subscription output",
                           usage=usage, provider_model=actual_model)
