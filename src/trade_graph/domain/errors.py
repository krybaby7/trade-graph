"""Typed domain failures. Unknown external outcomes are never silent success."""

from __future__ import annotations


class TradeGraphError(Exception):
    code = "error"


class ValidationFailure(TradeGraphError):
    code = "validation"


class AuthorityDenied(TradeGraphError):
    code = "authority_denied"


class StaleState(TradeGraphError):
    code = "stale_state"


class BudgetExhausted(TradeGraphError):
    code = "budget_exhausted"


class PaidCallsDisabled(TradeGraphError):
    code = "paid_calls_disabled"


class UncertainExternal(TradeGraphError):
    code = "uncertain"


class DuplicateRecord(TradeGraphError):
    code = "duplicate"


class NotFound(TradeGraphError):
    code = "not_found"


class LiveDisabled(TradeGraphError):
    code = "live_disabled"


class UnsafeTarget(TradeGraphError):
    code = "unsafe_target"
