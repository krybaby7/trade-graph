"""Owner-versus-role checks. Software validates permissions; models do not grant them."""

from __future__ import annotations

from decimal import Decimal

from trade_graph.contracts.models import Mandate, OwnerPolicy, PauseProfile
from trade_graph.domain.errors import AuthorityDenied
from trade_graph.kernel.riba import refuse_riba_symbol

LEADER_FORBIDDEN = frozenset(
    {
        "raise_owner_budget",
        "lift_owner_halt",
        "withdraw",
        "enable_live",
        "enable_withdrawals",
        "rewrite_ledger",
        "rewrite_gates",
        "lower_price_card",
        # T32: the owner's money limits, day start and coin list are owner policy; a mandate may only narrow.
        "edit_owner_policy",
        "raise_money_limits",
    }
)
ENGINEER_FORBIDDEN = frozenset(
    {
        "edit_kernel",
        "edit_ledger",
        "edit_receipts",
        "edit_owner_policy",
        "edit_test_gates",
        "edit_ci",
        "edit_migrations",
    }
)
TRADER_FORBIDDEN = frozenset(
    {
        "raw_exchange",
        "direct_ledger_write",
        "edit_owner_policy",
        "withdraw",
        "enable_live",
    }
)

PROTECTED_PATH_PREFIXES = (
    "src/trade_graph/kernel/",
    "src/trade_graph/adapters/persistence/",
    "migrations/",
    "tests/",
    ".github/",
    "config/owner_policy",
    "planning/model-prices.json",
    # T32 money limits and coin list: owner policy and execution controls outside the kernel folder.
    "src/trade_graph/contracts/money_limits.py",
    "src/trade_graph/application/money_limits.py",
    # T34 day plans: their validation, installation and protective exits are execution controls.
    "src/trade_graph/contracts/day_plans.py",
    "src/trade_graph/application/day_plans.py",
    "src/trade_graph/application/day_plan_protection.py",
    # T42 scorecard: how the paper result is valued and compared is a scoring definition, like the kernel sums.
    "src/trade_graph/application/scorecard.py",
)


def assert_action(role: str, action: str, *, owner_halt: bool) -> None:
    if role == "leader" and action in LEADER_FORBIDDEN:
        raise AuthorityDenied(action)
    if role == "leader" and action == "resume_pause" and owner_halt:
        raise AuthorityDenied("owner halt cannot be lifted by the leader")
    if role == "engineer" and action in ENGINEER_FORBIDDEN:
        raise AuthorityDenied(action)
    if role == "trader" and action in TRADER_FORBIDDEN:
        raise AuthorityDenied(action)
    if role != "owner" and action in {"raise_owner_budget", "enable_live", "withdraw"}:
        raise AuthorityDenied(action)


def owner_scope_covers(policy: OwnerPolicy, symbol: str) -> bool:
    """Can the owner's coin list ever include ``symbol``? Listed, or under the coin rule (T32) a spot pair in its
    quote currency; whether it may be bought at a given moment is the scanner's answer, checked per entry."""
    if policy.coin_scope is None:
        return symbol in policy.allowed_symbols
    try:
        refuse_riba_symbol(symbol)
    except AuthorityDenied:
        return False
    return symbol in policy.allowed_symbols or symbol.endswith("/" + policy.coin_scope.quote_asset)


def mandate_within_owner(mandate: Mandate, policy: OwnerPolicy) -> None:
    if mandate.max_gross_exposure_fraction > policy.maximum_gross_exposure_fraction:
        raise AuthorityDenied("mandate gross exposure exceeds owner envelope")
    if mandate.max_single_asset_exposure_fraction > policy.maximum_single_asset_exposure_fraction:
        raise AuthorityDenied("mandate single-asset exposure exceeds owner envelope")
    unknown = [symbol for symbol in mandate.symbols if not owner_scope_covers(policy, symbol)]
    if unknown:
        raise AuthorityDenied(f"symbols outside owner universe: {unknown}")
    if mandate.coin_scope == "owner_rule" and policy.coin_scope is None:
        raise AuthorityDenied("the mandate follows a coin rule the owner policy does not have")
    if policy.leverage_allowed is False and mandate.order_types_long_only is False:
        raise AuthorityDenied("short exposure is outside the owner envelope")


def exposure_allowed(
    *,
    equity: Decimal,
    gross_exposure: Decimal,
    asset_exposure: Decimal,
    policy: OwnerPolicy,
) -> None:
    if equity <= 0:
        raise AuthorityDenied("no equity")
    if gross_exposure / equity > policy.maximum_gross_exposure_fraction:
        raise AuthorityDenied("gross exposure")
    if asset_exposure / equity > policy.maximum_single_asset_exposure_fraction:
        raise AuthorityDenied("single asset exposure")


def path_is_protected(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in PROTECTED_PATH_PREFIXES
    )


def pause_allows_increase(profile: PauseProfile) -> bool:
    return profile == "RUNNING"


def pause_allows_new_decisions(profile: PauseProfile) -> bool:
    return profile == "RUNNING"


def pause_allows_reduction(profile: PauseProfile) -> bool:
    return profile in {"RUNNING", "NO_NEW_EXPOSURE", "MANAGE_ONLY", "FLATTEN"}


def pause_allows_protection(profile: PauseProfile) -> bool:
    """May software place or send a protective exit for a position? Every pause keeps protection (D117), except
    the owner's explicit cancel-all, a flatten that sells everything itself, and a stopped (flat) portfolio."""
    return profile in {"RUNNING", "PAUSE_DECISIONS", "NO_NEW_EXPOSURE", "MANAGE_ONLY"}
