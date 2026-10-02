"""Opt-in funded paper observation; receipts and actual costs determine the report."""

from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path

from trade_graph.adapters.models.transport import HttpxProviderHttp
from trade_graph.application.owner_commands import recover_owner_commands
from trade_graph.application.paper_service import PaperService
from trade_graph.contracts.models import ModelRequest
from trade_graph.domain.clock import utc_iso
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.pricing import worst_case_cost


def soak_preflight(runtime) -> None:
    """No key, paid call, budget creation or external request occurs in preflight."""
    if not runtime.paid_calls_enabled or not runtime.execution.authority.active_policy().paid_calls_enabled:
        raise AuthorityDenied("funded paper soak needs explicit owner and private runtime paid permission")
    if runtime.public_feed is None or runtime.model_handlers is None:
        raise AuthorityDenied("funded paper soak requires public data and configured model routes")
    budget = runtime.budget._budget(runtime.deployment_id)
    if any(Decimal(budget[field]) <= 0 for field in ("total_allowance", "daily_limit", "root_limit")):
        raise AuthorityDenied("funded paper soak needs a separately configured positive operating budget")
    if runtime.budget.remaining(runtime.deployment_id) <= 0:
        raise AuthorityDenied("funded paper soak has no available operating allowance")
    readiness = runtime.model_handlers.router.readiness(runtime.portfolio_id)
    if not readiness["ready"] or any(route["provider"] == "scripted" for route in readiness["roles"].values()):
        raise AuthorityDenied("funded paper soak requires approved credentialed routes for all roles")
    if runtime.execution.profile(runtime.portfolio_id) != "RUNNING":
        raise AuthorityDenied("funded paper soak cannot lift a persisted pause")


def _receipt_ids(runtime) -> set[str]:
    return {row[0] for row in runtime.database.execute(
        """SELECT r.receipt_id FROM usage_receipts r JOIN budget_reservations b USING (reservation_id)
        WHERE b.deployment_id = ?""", (runtime.deployment_id,),
    )}


def expense_observations(runtime, prior_receipt_ids: set[str], prior_invocation_ids: set[str]) -> dict:
    """Keep known actual usage, synthetic usage and unresolved billing separate."""
    actual, synthetic, estimates = Decimal("0"), Decimal("0"), Decimal("0")
    rows = runtime.database.execute(
        """SELECT r.*, b.role, b.task_id, b.root_task_id, b.system_version_id,
        b.price_card_id, b.attempt_kind FROM usage_receipts r
        JOIN budget_reservations b USING (reservation_id) WHERE b.deployment_id = ? ORDER BY r.created_at, r.rowid""",
        (runtime.deployment_id,),
    ).fetchall()
    attempts, actual_provider_receipts = [], 0
    for row in rows:
        if row["receipt_id"] in prior_receipt_ids:
            continue
        amount = Decimal(row["reporting_cost"] or "0")
        if row["synthetic"]:
            synthetic += amount
        elif row["status"] == "committed":
            actual += amount
            actual_provider_receipts += int(bool(row["provider_request_id"]))
        else:
            estimates += amount
        attempts.append({key: row[key] for key in (
            "receipt_id", "provider", "model", "role", "task_id", "root_task_id", "system_version_id",
            "price_card_id", "attempt_kind", "reporting_cost", "reporting_currency", "status", "synthetic",
        )})
    holds = runtime.database.execute(
        """SELECT reservation_id, amount, currency, state, price_card_id, role, task_id, root_task_id,
        system_version_id, attempt_kind FROM budget_reservations WHERE deployment_id = ? AND synthetic = 0
        AND state IN ('RESERVED', 'UNCERTAIN', 'CONSERVATIVE')""", (runtime.deployment_id,),
    ).fetchall()
    uncertain = len(holds)
    held_amount = sum((Decimal(row["amount"]) for row in holds), Decimal("0"))
    forecast = Decimal("0")
    for row in runtime.database.execute(
        """SELECT i.invocation_id, i.request_json, b.price_card_id FROM model_invocations i
        JOIN budget_reservations b USING (reservation_id)
        WHERE b.deployment_id = ? AND b.synthetic = 0""", (runtime.deployment_id,),
    ):
        if row["invocation_id"] in prior_invocation_ids:
            continue
        request = ModelRequest.model_validate_json(row["request_json"])
        card = runtime.budget.card(row["price_card_id"])
        native = worst_case_cost(card, request.context["max_input_tokens"], request.max_output_tokens,
                                 request.max_tool_calls)
        fx = Decimal("1") if card.currency == "EUR" else runtime.model_handlers.router.config.fx_rate
        forecast += native * fx * runtime.model_handlers.router.config.fx_buffer
    return {"currency": "EUR", "known_actual_accrued": canonical_decimal(actual),
            "synthetic_accrued": canonical_decimal(synthetic),
            "estimated_or_unresolved_accrued": canonical_decimal(estimates), "unresolved_reservations": uncertain,
            "unresolved_reserved_upper_bound": canonical_decimal(held_amount),
            "unresolved_attempts": [dict(row) for row in holds],
            "forecast_upper_bound_for_dispatched_requests": canonical_decimal(forecast),
            "forecast_basis": "immutable price cards, persisted request limits and configured conservative FX",
            "provider_receipts_with_known_usage": actual_provider_receipts, "attempts": attempts}


async def run_funded_soak(runtime, *, duration_seconds: int, report_path: Path) -> dict:
    """Run only after explicit funded preflight; report limitations rather than infer success."""
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= 604800:
        raise ValueError("soak duration must be an integer from 1 to 604800 seconds")
    soak_preflight(runtime)
    report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if report_path.parent.is_symlink() or report_path.parent.stat().st_mode & 0o077:
        raise ValueError("soak evidence requires a private directory with mode 0700")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    # Reserve a fresh private evidence file before any potentially paid work.
    descriptor = os.open(report_path, flags, 0o600)
    summary, failure = None, None
    try:
        started = utc_iso(runtime.clock.now())
        started_monotonic = time.monotonic()
        prior = _receipt_ids(runtime)
        prior_invocations = {row[0] for row in runtime.database.execute(
            """SELECT i.invocation_id FROM model_invocations i
            JOIN budget_reservations b USING (reservation_id) WHERE b.deployment_id = ?""",
            (runtime.deployment_id,),
        )}
        service = PaperService(
            runtime.database, runtime.execution, clock=runtime.clock, portfolio_ids=[runtime.portfolio_id],
            handlers=runtime.handlers, artifact_runtime=runtime.artifact_runtime, public_feed=runtime.public_feed,
            secretary=runtime.secretary, tick_interval_seconds=runtime.config.tick_interval_seconds,
            recover_commands=lambda: recover_owner_commands(runtime),
        )

        async def finish_window():
            await asyncio.sleep(duration_seconds)
            service.request_stop()

        timer = asyncio.create_task(finish_window())
        try:
            summary = await service.run()
        except Exception as exc:
            failure = type(exc).__name__
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        elapsed = time.monotonic() - started_monotonic
        window_completed = elapsed >= duration_seconds
        if not window_completed and failure is None:
            failure = "StoppedBeforeRequestedDuration"
        expense_failure = None
        try:
            expenses = expense_observations(runtime, prior, prior_invocations)
        except Exception as exc:
            # Paid attempts and receipts remain authoritative in the database.
            # An observer failure cannot certify zero cost or discard the run.
            expenses = None
            expense_failure = type(exc).__name__
            failure = failure or expense_failure
        real_transport = isinstance(runtime.model_handlers.gateway.transport, HttpxProviderHttp)
        provider_status = "pending"
        if expenses is None:
            provider_status = "unavailable"
        elif expenses["unresolved_reservations"]:
            provider_status = "unresolved_billing"
        elif real_transport and expenses["provider_receipts_with_known_usage"]:
            provider_status = "responses_observed"
        service_failed = bool(summary and summary.get("failures"))
        result = {
            "schema_version": 1, "mode": "paper", "live_enabled": False,
            "status": "interrupted" if failure else "degraded" if service_failed else "recorded",
            "failure_type": failure or ("ServiceFailures" if service_failed else None),
            "started_at": started, "ended_at": utc_iso(runtime.clock.now()),
            "requested_duration_seconds": duration_seconds, "service": summary,
            "observed_duration_seconds": f"{elapsed:.3f}", "requested_duration_completed": window_completed,
            "credentialed_provider_verification": provider_status, "expenses": expenses,
            "expense_observation_status": "unavailable" if expenses is None else "recorded",
            "expense_failure_type": expense_failure,
            "economic_evidence": "insufficient_evidence",
            "limitations": ["paper fills use declared simulation assumptions",
                            "this observation does not prove profitability or authorize live trading",
                            "provider invoice reconciliation and forward economic evaluation remain separate"],
        }
        with os.fdopen(descriptor, "w") as target:
            descriptor = -1
            json.dump(result, target, sort_keys=True, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)
