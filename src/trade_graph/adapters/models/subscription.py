"""One-attempt subscription inference with protected admission and durable recovery.

This path never constructs provider HTTP or receives authentication tokens. CLI
login stays inside an owner-provisioned native Linux installation. Codex admission
is closed while its built-in provider retries cannot be disabled officially.
Subscription receipts are separate from API expenses and virtual paper equity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import Event
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
                        "source", "observed_at", "weekly", "five_hour"})


class SubscriptionConfig(ContractModel):
    provider: SubscriptionProvider = "codex_subscription"
    model: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    enabled: bool = False
    maximum_seconds: int = Field(default=120, ge=1, le=300)
    maximum_output_tokens: int = Field(default=4096, ge=1, le=16_384)
    allowed_context_keys: dict[str, list[str]] = Field(default_factory=dict)


@dataclass(frozen=True)
class SubscriptionReadiness:
    provider: SubscriptionProvider
    cli_version: str
    ready: bool
    blockers: tuple[str, ...]
    quota: dict
    authentication: str
    isolation: str

    def public_status(self) -> dict:
        return {"provider": self.provider, "cli_version": self.cli_version, "ready": self.ready,
                "blockers": list(self.blockers), "quota": sanitize_quota(self.quota),
                "authentication": self.authentication, "isolation": self.isolation,
                "billing_kind": "subscription", "actual_cost_native": None, "cost_status": "unknown",
                "application_automatic_retry": False, "application_automatic_fallback": False,
                "provider_zero_retry_admitted": self.ready}


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
        blockers.append("Codex built-in openai provider retry defaults cannot be disabled "
                        "through supported configuration")
        if authentication != "chatgpt":
            blockers.append("official ChatGPT subscription login is not established")
    else:
        if authentication != "subscription":
            blockers.append("official Claude subscription login is not established")
        numbers = tuple(int(value) for value in re.findall(r"\d+", cli_version)[:3])
        if len(numbers) != 3 or numbers < (2, 1, 285):
            blockers.append("Claude Code 2.1.285 or later is required for documented nonstream timeout retry control")
    if not native_linux:
        blockers.append("native Linux CLI required; Windows/WSL interop executables are refused")
    if not extra_usage_disabled:
        blockers.append("owner must verify extra usage, credit spending and automatic purchases are disabled")
    if not isolation_ready:
        blockers.append("owner-pinned Linux filesystem/process isolation has not passed its host probe")
    clean = sanitize_quota(quota)
    if _quota_exhausted(clean):
        blockers.append("subscription quota exhausted; new AI work is paused")
    return SubscriptionReadiness(config.provider, cli_version, not blockers, tuple(blockers), clean,
                                 authentication, "linux-bubblewrap" if isolation_ready else "unavailable")


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
                if row["result_json"]:
                    return ModelResult.model_validate_json(row["result_json"])
                result = ModelResult(ok=False, failure="timeout_uncertain",
                    message="prior subscription dispatch has no durable response; no replay permitted")
                self.save(invocation_id, result, "UNCERTAIN")
                return result
            now = utc_iso(self.clock.now())
            self.database.execute("""INSERT INTO subscription_invocations
                (invocation_id,request_hash,task_id,root_task_id,role,run_id,system_version_id,provider,
                 requested_model,state,quota_json,created_at,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?,'DISPATCHED',?,?,?)""",
                (invocation_id, binding, request.task_id, request.root_task_id, request.role, request.run_id,
                 request.system_version_id, provider, request.model, json.dumps(sanitize_quota(quota)), now, now))
        return None

    def save(self, invocation_id: str, result: ModelResult, state: str, *, dispatched: bool = True) -> None:
        with self.database.immediate():
            self.database.execute("""UPDATE subscription_invocations SET state=?,result_json=?,actual_model=?,
                usage_json=?,cost_status=?,updated_at=? WHERE invocation_id=?""",
                (state, result.model_dump_json(), result.provider_model,
                 result.usage.model_dump_json() if result.usage else None,
                 "unknown" if dispatched else "not_incurred", utc_iso(self.clock.now()), invocation_id))

    def pause_ai(self, provider: str, quota: dict) -> None:
        with self.database.immediate():
            self.database.execute("""INSERT INTO subscription_provider_state
                (provider,ai_paused,reason,quota_json,updated_at)
                VALUES (?,1,'subscription quota exhausted',?,?) ON CONFLICT(provider) DO UPDATE SET
                ai_paused=1,reason=excluded.reason,quota_json=excluded.quota_json,updated_at=excluded.updated_at""",
                (provider, json.dumps(sanitize_quota(quota)), utc_iso(self.clock.now())))


class SubscriptionAdapter:
    """Exactly one protected CLI invocation. Validation never starts a repair call."""

    def __init__(self, config: SubscriptionConfig, readiness: SubscriptionReadiness,
                 executor: SubscriptionExecutor) -> None:
        self.config, self.readiness, self.executor = config.model_copy(deep=True), readiness, executor

    def invoke(self, request: ModelRequest, *, invocation_id: str, journal: SubscriptionJournal,
               cancel_event: Event | None = None) -> ModelResult:
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
        if not self.config.enabled or not self.readiness.ready or self.readiness.provider != self.config.provider:
            result = ModelResult(ok=False, failure="credentials", message="; ".join(self.readiness.blockers)[:500]
                                 or "subscription admission is disabled")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        # Readiness cannot bypass Codex's known unsupported zero-retry route.
        if self.config.provider == "codex_subscription":
            result = ModelResult(ok=False, failure="unsupported", message="Codex built-in retries cannot be disabled")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        if journal.provider_status(self.config.provider)["ai_paused"] or _quota_exhausted(self.readiness.quota):
            result = ModelResult(ok=False, failure="rate_limit",
                                 message="subscription quota exhausted; new AI work paused")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        if cancel_event and cancel_event.is_set():
            result = ModelResult(ok=False, failure="temporary", message="subscription task cancelled before dispatch")
            journal.save(invocation_id, result, "BLOCKED", dispatched=False)
            return result
        try:
            outcome = self.executor.execute(request, cancel_event=cancel_event)
            result = parse_claude_outcome(outcome, request)
        except Exception:
            # Process failure after persisted dispatch is ambiguous and is never retried.
            result = ModelResult(ok=False, failure="timeout_uncertain", message="subscription dispatch outcome unknown")
        if result.failure == "rate_limit":
            journal.pause_ai(self.config.provider, self.readiness.quota)
        state = "COMPLETED" if result.ok else "UNCERTAIN" if result.failure == "timeout_uncertain" else "FAILED"
        journal.save(invocation_id, result, state)
        return result

    def _validate_request(self, request: ModelRequest) -> None:
        expected = "openai" if self.config.provider == "codex_subscription" else "anthropic"
        if (request.provider != expected or request.model != self.config.model or request.synthetic
                or lookup_capabilities(expected, request.model) is None
                or request.max_tool_calls != 0
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


def parse_claude_outcome(outcome: CliOutcome, request: ModelRequest) -> ModelResult:
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

        if len(outcome.stdout.encode()) > MAX_OUTPUT_BYTES:
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
        if "num_turns" in document and (type(document["num_turns"]) is not int or document["num_turns"] != 1):
            raise ValueError("only one agentic turn is permitted")
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


def claude_environment(maximum_output_tokens: int) -> dict[str, str]:
    """Fresh environment; API keys, gateway overrides and watchdog are never inherited."""
    return {"CLAUDE_CODE_MAX_RETRIES": "0", "MAX_STRUCTURED_OUTPUT_RETRIES": "1",
            "CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES": "0", "CLAUDE_CODE_MAX_TURNS": "1",
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


def claude_command(binary: str, request: ModelRequest) -> list[str]:
    settings = {"switchModelsOnFlag": False, "availableModels": [request.model], "fallbackModel": []}
    return [binary, "-p", "--restricted", "--safe-mode", "--output-format", "json", "--max-turns", "1",
            "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--setting-sources", "", "--settings", json.dumps(settings), "--no-session-persistence",
            "--model", request.model, "--json-schema", json.dumps(request.output_schema),
            "--system-prompt", request.instructions]
