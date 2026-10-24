"""The owner's money limits at run time (T32; D138 items 1, 2, 3 and 6; D127).

``MoneyLimitGuard`` measures the daily loss limit for a portfolio from the owner policy's ``money_limits``:

- *The day-start value.* At each trading day start (default 00:00 UTC) the account's native value is read
  point-in-time at the day start itself: the books as they stood then, priced with the newest marks observed at
  or before it, each at most ``MARK_MAX_AGE`` old. When that is not possible (the portfolio opened later that
  day, or a held coin had no recent mark because the service was down), the first valuation the guard can make
  later that day is used instead and labelled so. It is recorded once per portfolio and day.
- *The limit.* The loss is the day-start value less the current value, both equity in the account's currency,
  so fees paid and open positions at their marks are included. At the owner's fraction of the day-start value
  the guard records a stop for the rest of the day: new entries are refused and resting entries are cancelled
  (``Execution.enforce_money_limits``); protective exits, exits and reconciliation continue; nothing is sold
  off. The stop lifts at the next day start, when a new day-start value is taken.
- *Unknown is not a yes.* Without a day-start value or a current value with fresh marks, the limit cannot be
  checked and new entries are refused as stale (retried later), never allowed on a guess.

The guard also flags ledger postings that look like interest, staking or earn income (D127) for the owner, and
holds the gate that keeps day-plan order installation off until the owner has recorded money limits.

``propose_money_limits`` / ``record_money_limits`` build and install the owner's policy revision for the owner
command ``trade-graph money-limits`` (revision-checked against the active revision, audited, refused while the
paper service runs). They take the values from the owner; the D138 values are only the command's defaults.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Literal

from trade_graph.adapters.persistence.db import Database
from trade_graph.application.authority import AuthorityRecord
from trade_graph.contracts.models import Mandate, OwnerPolicy
from trade_graph.contracts.money_limits import CoinScope, OwnerMoneyLimits
from trade_graph.domain.clock import Clock, parse_utc, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError, ValidationFailure
from trade_graph.domain.money import canonical_decimal
from trade_graph.kernel.money_limits import (
    MARK_MAX_AGE,
    TradingDay,
    loss_limit_amount,
    loss_limit_reached,
    trading_day,
)
from trade_graph.kernel.riba import interest_posting_reasons

DAY_PLAN_ORDERS_OFF: Final = ("day-plan orders stay off until the owner records money limits "
                              "(trade-graph money-limits --confirm)")
LOSS_LIMIT_REACHED: Final = "daily loss limit reached"
COMMAND: Final = "money-limits"
SERVICE_LEASES: Final = ("paper-service", "role-worker")


@dataclass(frozen=True, slots=True)
class DayStartValue:
    day: TradingDay
    currency: str
    value: Decimal
    valued_at: datetime
    basis: Literal["at_day_start", "first_valuation_after_start"]
    recorded: bool


@dataclass(frozen=True, slots=True)
class LossStatus:
    portfolio_id: str
    policy_revision: str
    day: TradingDay
    currency: str
    fraction: Decimal
    day_start: DayStartValue | None
    value: Decimal | None          # the current value, None when it cannot be confirmed
    stopped: bool                  # a stop is recorded for this trading day
    unknown: str | None            # why the limit cannot be checked at the moment

    @property
    def limit_amount(self) -> Decimal | None:
        return None if self.day_start is None else loss_limit_amount(self.day_start.value, self.fraction)

    @property
    def loss(self) -> Decimal | None:
        if self.day_start is None or self.value is None:
            return None
        return self.day_start.value - self.value

    @property
    def reached(self) -> bool:
        if self.stopped:
            return True
        if self.day_start is None or self.value is None:
            return False
        return loss_limit_reached(day_start_value=self.day_start.value, current_value=self.value,
                                  fraction=self.fraction)

    def entry_refusal(self) -> TradeGraphError | None:
        """Why a new entry is refused now, or None when the limit allows it."""
        if self.reached:
            return AuthorityDenied(f"{LOSS_LIMIT_REACHED}: new entries wait for the next day start at "
                                   f"{utc_iso(self.day.end)}; protective exits and exits continue")
        if self.unknown is not None:
            return StaleState(f"the daily loss limit can't be checked: {self.unknown}")
        return None

    def document(self) -> dict[str, Any]:
        def amount(value: Decimal | None) -> str | None:
            return None if value is None else canonical_decimal(value)

        start = self.day_start
        return {
            "portfolio_id": self.portfolio_id, "policy_revision": self.policy_revision,
            "day_start": utc_iso(self.day.start), "resumes_at": utc_iso(self.day.end), "currency": self.currency,
            "limit_fraction": canonical_decimal(self.fraction),
            "day_start_value": None if start is None else amount(start.value),
            "day_start_basis": None if start is None else start.basis,
            "day_start_valued_at": None if start is None else utc_iso(start.valued_at),
            "value": amount(self.value), "loss": amount(self.loss), "limit_amount": amount(self.limit_amount),
            "reached": self.reached, "stopped": self.stopped, "unknown": self.unknown,
        }


class MoneyLimitGuard:
    def __init__(self, database: Database, ledger, clock: Clock) -> None:
        self.database = database
        self.ledger = ledger
        self.clock = clock

    # --- the day-plan gate (D138's closing paragraph) ----------------------------------------------------------

    @staticmethod
    def recorded(policy: OwnerPolicy) -> OwnerMoneyLimits | None:
        return policy.money_limits

    def require_day_plan_orders(self, policy: OwnerPolicy) -> OwnerMoneyLimits:
        """Day-plan order installation stays off until the owner has recorded money limits."""
        if policy.money_limits is None:
            raise AuthorityDenied(DAY_PLAN_ORDERS_OFF)
        if policy.leverage_allowed:
            raise AuthorityDenied("margin and leverage are refused (D127)")
        return policy.money_limits

    # --- the daily loss limit (D138 items 1 and 6) -------------------------------------------------------------

    def status(self, portfolio_id: str, policy: OwnerPolicy, *, record: bool = False) -> LossStatus | None:
        """The loss limit's state now; ``record`` also stores a missing day-start value and a reached stop."""
        limits = policy.money_limits
        if limits is None:
            return None
        if record:
            # Read first; take the writer lock only when there is something to record.
            seen = self.status(portfolio_id, policy)
            recorded = seen.day_start is not None and seen.day_start.recorded
            if recorded and (seen.stopped or not seen.reached):
                return seen
        now = self.clock.now()
        day = trading_day(now, limits.trading_day_start_utc)
        currency = limits.account_currency
        with self.database.immediate() if record else nullcontext():
            start = self._recorded_value(portfolio_id, day, currency)
            if start is None:
                start = self._value_at_start(portfolio_id, day, currency)
                if start is None and record:
                    start = self._first_valuation(portfolio_id, day, currency, now)
                if start is not None and record:
                    start = self._record_value(portfolio_id, start, policy.revision_id)
            stopped = self._stopped(portfolio_id, day)
            value, why = self._current_value(portfolio_id, currency, now)
            if start is None:
                why = "the day-start value is not known yet"
            status = LossStatus(portfolio_id, policy.revision_id, day, currency, limits.daily_loss_limit_fraction,
                                start, value, stopped, None if stopped else why)
            if record and status.reached and not stopped:
                self._record_stop(status, now)
                status = LossStatus(portfolio_id, policy.revision_id, day, currency,
                                    limits.daily_loss_limit_fraction, start, value, True, None)
        return status

    def _recorded_value(self, portfolio_id: str, day: TradingDay, currency: str) -> DayStartValue | None:
        row = self.database.execute(
            "SELECT value, valued_at, basis FROM trading_day_values WHERE portfolio_id = ? AND day_start = ? "
            "AND currency = ?", (portfolio_id, utc_iso(day.start), currency)).fetchone()
        if row is None:
            return None
        return DayStartValue(day, currency, Decimal(row["value"]), parse_utc(row["valued_at"]), row["basis"], True)

    def _value_at_start(self, portfolio_id: str, day: TradingDay, currency: str) -> DayStartValue | None:
        opened = self.database.execute("SELECT created_at FROM portfolios WHERE portfolio_id = ?",
                                       (portfolio_id,)).fetchone()
        if opened is None:
            raise ValidationFailure("unknown portfolio")
        if parse_utc(opened["created_at"]) > day.start:
            return None
        value = self._valuation(portfolio_id, currency, day.start)
        return None if value is None else DayStartValue(day, currency, value, day.start, "at_day_start", False)

    def _first_valuation(self, portfolio_id: str, day: TradingDay, currency: str,
                         now: datetime) -> DayStartValue | None:
        if not day.start < now < day.end:
            return None
        value = self._valuation(portfolio_id, currency, now)
        return None if value is None else DayStartValue(day, currency, value, now, "first_valuation_after_start",
                                                        False)

    def _valuation(self, portfolio_id: str, currency: str, at: datetime) -> Decimal | None:
        view = self.ledger.native_equity(portfolio_id, currency, at=utc_iso(at))
        if view.missing or view.stale or view.equity is None or view.equity <= 0:
            return None
        return view.equity if self._marks_fresh(portfolio_id, at) else None

    def _current_value(self, portfolio_id: str, currency: str, now: datetime) -> tuple[Decimal | None, str | None]:
        view = self.ledger.native_equity(portfolio_id, currency)
        if view.missing:
            return None, f"a held coin has no {currency} mark ({', '.join(view.missing)})"
        if view.stale or view.equity is None:
            return None, f"the {currency} valuation is stale"
        if not self._marks_fresh(portfolio_id, now):
            return None, "a held coin's mark is too old"
        return view.equity, None

    def _marks_fresh(self, portfolio_id: str, at: datetime) -> bool:
        books = self.ledger.books(portfolio_id, utc_iso(at))
        held = sorted({lot.asset for lot in books.lots if lot.open_quantity() > 0})
        for asset in held:
            # The ledger's own newest mark, the one the valuation used (W10-B, D271: the newest observed, the first
            # recorded of equal times): one indexed row per coin held.
            mark = self.ledger.newest_mark(portfolio_id, asset, utc_iso(at))
            if mark is None or at - parse_utc(mark.at) > MARK_MAX_AGE:
                return False
        return True

    def _record_value(self, portfolio_id: str, start: DayStartValue, revision: str) -> DayStartValue:
        self.database.execute(
            """INSERT INTO trading_day_values (portfolio_id, day_start, day_end, currency, value, valued_at, basis,
               policy_revision, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (portfolio_id, utc_iso(start.day.start), utc_iso(start.day.end), start.currency,
             canonical_decimal(start.value), utc_iso(start.valued_at), start.basis, revision,
             utc_iso(self.clock.now())))
        return DayStartValue(start.day, start.currency, start.value, start.valued_at, start.basis, True)

    def _stopped(self, portfolio_id: str, day: TradingDay) -> bool:
        return self.database.execute("SELECT 1 FROM daily_loss_stops WHERE portfolio_id = ? AND day_start = ?",
                                     (portfolio_id, utc_iso(day.start))).fetchone() is not None

    def _record_stop(self, status: LossStatus, now: datetime) -> None:
        assert status.day_start is not None and status.value is not None
        document = status.document()
        self.database.execute(
            """INSERT INTO daily_loss_stops (portfolio_id, day_start, resumes_at, currency, day_start_value,
               value_at_stop, loss, limit_amount, limit_fraction, reached_at, policy_revision, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (status.portfolio_id, document["day_start"], document["resumes_at"], status.currency,
             document["day_start_value"], document["value"], document["loss"], document["limit_amount"],
             document["limit_fraction"], utc_iso(now), status.policy_revision, utc_iso(now)))
        self.ledger._activity(status.portfolio_id, "daily_loss_limit_reached",
                              {**document, "reached": True, "stopped": True, "reached_at": utc_iso(now)},
                              created_at=utc_iso(now))

    # --- D127: interest-like postings ---------------------------------------------------------------------------

    def flag_interest_postings(self, portfolio_id: str) -> list[dict[str, Any]]:
        """Flag, once each, the cash and coin postings that look like interest, staking or earn income."""
        flagged: list[dict[str, Any]] = []
        query = """SELECT event_id, kind, payload_json, external_ref FROM ledger_events e
                   WHERE portfolio_id = ? AND kind IN ('deposit', 'withdraw', 'internal')
                   AND NOT EXISTS (SELECT 1 FROM riba_posting_flags f WHERE f.ledger_event_id = e.event_id)
                   ORDER BY sequence"""

        def candidates() -> list:
            return [row for row in self.database.execute(query, (portfolio_id,)).fetchall()
                    if interest_posting_reasons(row["kind"], row["external_ref"], json.loads(row["payload_json"]))]

        if not candidates():  # read first; the writer lock only when there is something to flag
            return flagged
        with self.database.immediate():
            for row in candidates():
                payload = json.loads(row["payload_json"])
                reasons = interest_posting_reasons(row["kind"], row["external_ref"], payload)
                now = utc_iso(self.clock.now())
                self.database.execute(
                    "INSERT INTO riba_posting_flags (portfolio_id, ledger_event_id, kind, reasons_json, flagged_at) "
                    "VALUES (?, ?, ?, ?, ?)", (portfolio_id, row["event_id"], row["kind"], json.dumps(reasons), now))
                flag = {"ledger_event_id": row["event_id"], "kind": row["kind"], "reasons": list(reasons),
                        "asset": payload.get("asset"), "amount": payload.get("amount"),
                        "reference": (row["external_ref"] or "")[:200],
                        "note": "looks like interest, staking or earn income (D127): kept as recorded, for review"}
                self.ledger._activity(portfolio_id, "riba_interest_posting", flag, created_at=now)
                flagged.append(flag)
        return flagged


# --- the owner command's revision ----------------------------------------------------------------------------

CoinList = Literal["rule", "list"]


@dataclass(frozen=True, slots=True)
class MoneyLimitValues:
    """What the owner records: fractions of the account (Decimal), the day start and the coin list."""

    daily_loss_limit_fraction: Decimal
    per_coin_fraction: Decimal
    total_fraction: Decimal
    trading_day_start_utc: str
    coin_list: CoinList

    def __post_init__(self) -> None:
        for name in ("daily_loss_limit_fraction", "per_coin_fraction", "total_fraction"):
            if not isinstance(getattr(self, name), Decimal) or not getattr(self, name).is_finite():
                raise ValidationFailure(f"{name} must be a finite Decimal")
        if not Decimal("0") < self.per_coin_fraction <= self.total_fraction <= Decimal("1"):
            raise ValidationFailure("the amount per coin must be above 0 and at most the total, which is at most "
                                    "the whole account")
        if self.coin_list not in ("rule", "list"):
            raise ValidationFailure("the coin list is the scanner rule or the current list")
        # The loss limit and day start are validated by the policy fields themselves.
        OwnerMoneyLimits(daily_loss_limit_fraction=self.daily_loss_limit_fraction,
                         trading_day_start_utc=self.trading_day_start_utc)


# The owner's decided settings (D138 items 1, 2, 3 and 6): the owner command's defaults, recorded only when the
# owner runs it with --confirm at step L3. Nothing applies them on its own.
D138_VALUES: Final = MoneyLimitValues(Decimal("0.03"), Decimal("0.20"), Decimal("0.80"), "00:00", "rule")


@dataclass(frozen=True, slots=True)
class MoneyLimitRevision:
    portfolio_id: str
    current_policy: OwnerPolicy
    policy: OwnerPolicy | None        # None when the active revision already holds these values
    current_mandate: Mandate
    mandate: Mandate | None           # None when the active mandate already matches
    changes: dict[str, dict[str, Any]]


def _view(policy: OwnerPolicy) -> dict[str, Any]:
    document = policy.model_dump(mode="json")
    return {name: document.get(name) for name in ("maximum_single_asset_exposure_fraction",
                                                  "maximum_gross_exposure_fraction", "money_limits", "coin_scope")}


def propose_money_limits(record: AuthorityRecord, portfolio_id: str, values: MoneyLimitValues, *,
                         revision_id: str) -> MoneyLimitRevision:
    """The owner-policy and mandate revisions that would record ``values``; writes nothing."""
    current = record.active_policy()
    mandate = record.active_mandate(portfolio_id)
    limits = OwnerMoneyLimits(daily_loss_limit_fraction=values.daily_loss_limit_fraction,
                              trading_day_start_utc=values.trading_day_start_utc,
                              account_currency=current.virtual_capital.currency)
    proposed = OwnerPolicy.model_validate({
        **current.model_dump(), "revision_id": revision_id,
        "maximum_single_asset_exposure_fraction": values.per_coin_fraction,
        "maximum_gross_exposure_fraction": values.total_fraction,
        "money_limits": limits, "coin_scope": CoinScope() if values.coin_list == "rule" else None,
    })
    before, after = _view(current), _view(proposed)
    changes = {name: {"from": before[name], "to": after[name]} for name in before if before[name] != after[name]}
    scope = "owner_rule" if values.coin_list == "rule" else "listed"
    revised = None
    if (mandate.max_single_asset_exposure_fraction, mandate.max_gross_exposure_fraction, mandate.coin_scope) != (
            values.per_coin_fraction, values.total_fraction, scope):
        latest = record.database.execute("SELECT MAX(revision) FROM mandates WHERE portfolio_id = ?",
                                         (portfolio_id,)).fetchone()[0]
        number = int(latest) + 1
        revised = Mandate.model_validate({
            **mandate.model_dump(), "revision": number, "mandate_id": f"paper-{portfolio_id}-{number}",
            "max_single_asset_exposure_fraction": values.per_coin_fraction,
            "max_gross_exposure_fraction": values.total_fraction, "coin_scope": scope,
        })
        if record.mandate_expired(revised):
            raise ValidationFailure("the active mandate has expired; renew it before recording money limits")
    return MoneyLimitRevision(portfolio_id, current, proposed if changes else None, mandate, revised, changes)


def _service_running(database: Database, now: datetime) -> bool:
    marks = ",".join("?" for _ in SERVICE_LEASES)
    return database.execute(f"SELECT 1 FROM process_leases WHERE lease_name IN ({marks}) AND expires_at > ?",
                            (*SERVICE_LEASES, utc_iso(now))).fetchone() is not None


def record_money_limits(record: AuthorityRecord, portfolio_id: str, values: MoneyLimitValues, *, revision_id: str,
                        expect_revision: str | None = None, role: str = "owner") -> MoneyLimitRevision:
    """Install the owner's revision, its mandate revision and the audit row in one transaction.

    Revision-checked: the revision replaces the one active inside this transaction, and ``expect_revision``
    (the one the owner previewed) must still be active. Refused while the paper service runs, because work in
    flight cites the active revision.
    """
    if role != "owner":
        raise AuthorityDenied("only the owner can record money limits")
    database = record.database
    with database.immediate():
        if _service_running(database, record.clock.now()):
            raise StaleState("the paper service is running; stop it before confirming")
        current = record.active_policy()
        if expect_revision is not None and current.revision_id != expect_revision:
            raise StaleState(f"the owner policy changed since the preview: revision {current.revision_id} is "
                             "active now")
        revision = propose_money_limits(record, portfolio_id, values, revision_id=revision_id)
        if revision.policy is not None:
            record.install_policy(revision.policy, role="owner")
            previous_sha, new_sha = (database.execute(
                "SELECT content_hash FROM owner_policy_revisions WHERE revision_id = ?", (ref,),
            ).fetchone()["content_hash"] for ref in (current.revision_id, revision.policy.revision_id))
            database.execute(
                """INSERT INTO owner_policy_audit (revision_id, previous_revision_id, previous_sha256,
                   revision_sha256, command, changes_json, set_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 'owner', ?)""",
                (revision.policy.revision_id, current.revision_id, previous_sha, new_sha, COMMAND,
                 json.dumps({"portfolio_id": portfolio_id, "changes": revision.changes,
                             "mandate_revision": None if revision.mandate is None else revision.mandate.revision},
                            sort_keys=True), utc_iso(record.clock.now())))
        if revision.mandate is not None:
            record.install_mandate(revision.mandate, role="owner")
    return revision


__all__ = [
    "COMMAND",
    "D138_VALUES",
    "DAY_PLAN_ORDERS_OFF",
    "LOSS_LIMIT_REACHED",
    "DayStartValue",
    "LossStatus",
    "MoneyLimitGuard",
    "MoneyLimitRevision",
    "MoneyLimitValues",
    "propose_money_limits",
    "record_money_limits",
]
