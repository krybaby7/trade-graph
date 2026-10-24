"""The owner's protected switch that moves Research and the Leader to Perplexity (T37, T38; D124, D125, D205).

Shipped switched off. ``perplexity_enabled_for(database, department)`` is the one typed read the departments use:
Research's focus workflow (T37) and the Leader's news rounds (W6-B, T38). It is False when nothing is recorded, in
a database from before migration 0038, and when the newest record no longer matches its hash (it fails closed).
``perplexity_setting(database)`` returns the whole revision in force, with the owner's spend rule.

Only the owner records a revision, through ``trade-graph perplexity-departments --confirm`` (step L3), in the
style of ``mandate-cleanup`` and ``money-limits``: a preview by default, refused while the paper service runs,
revision-checked, and never rewriting an earlier revision. Switching a department on carries the owner's spend
rule: no money limit while testing (D125, D138 item 4: every call still has a reservation and a receipt, and HQ
shows the USD20 alert), or a monthly limit in USD that pauses only Perplexity-backed research rounds.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from pydantic import ValidationError

from trade_graph.contracts.perplexity_switch import PERPLEXITY_DEPARTMENTS, PerplexityDepartments
from trade_graph.contracts.web_research import PerplexitySpendPolicy
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import AuthorityDenied, StaleState, TradeGraphError, ValidationFailure
from trade_graph.domain.money import canonical_decimal

COMMAND: Final = "perplexity-departments"
SERVICE_LEASES: Final = ("paper-service", "role-worker")
_TABLE: Final = "perplexity_department_settings"


def canonical_json(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _has_table(source) -> bool:
    row = source.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (_TABLE,)).fetchone()
    return row is not None


def perplexity_setting(source) -> PerplexityDepartments:
    """The switch in force: the newest owner revision, or the shipped default (off).

    ``source`` is a ``Database`` or any ``sqlite3.Connection``, including a read-only one (HQ). A newest revision
    whose document no longer matches its stored hash is refused (``StaleState``), never trusted.
    """
    if not _has_table(source):
        return PerplexityDepartments.off()
    row = source.execute(f"SELECT revision_id, research, leader, document_json, document_sha256 FROM {_TABLE} "
                         "ORDER BY sequence DESC LIMIT 1").fetchone()
    if row is None:
        return PerplexityDepartments.off()
    revision_id, research, leader, text, digest = tuple(row)
    if _sha256(text) != digest:
        raise StaleState(f"Perplexity switch revision {revision_id} no longer matches its stored hash")
    setting = PerplexityDepartments.model_validate_json(text)
    if (setting.revision_id, setting.research, setting.leader) != (revision_id, bool(research), bool(leader)):
        raise StaleState(f"Perplexity switch revision {revision_id} disagrees with its stored hash")
    return setting


def perplexity_enabled_for(source, department: str) -> bool:
    """Is ``department`` (``research`` or ``leader``) switched to Perplexity by the owner? Fails closed."""
    if department not in PERPLEXITY_DEPARTMENTS:
        raise ValidationFailure(f"only {' and '.join(PERPLEXITY_DEPARTMENTS)} can be switched to Perplexity; the "
                                "Trader stays on its subscription (D124)")
    try:
        return perplexity_setting(source).enabled_for(department)
    except (TradeGraphError, ValidationError, ValueError, sqlite3.DatabaseError):
        return False


@dataclass(frozen=True, slots=True)
class SwitchProposal:
    """What the command shows or recorded: the revision in force, the new one (None: nothing changes), the changes."""

    current: PerplexityDepartments
    proposed: PerplexityDepartments | None
    changes: dict[str, dict[str, Any]]


def spend_document(policy: PerplexitySpendPolicy | None) -> dict[str, Any] | None:
    if policy is None:
        return None
    return {"mode": policy.mode, "monthly_limit_usd": None if policy.monthly_limit_usd is None
            else canonical_decimal(policy.monthly_limit_usd), "decision_ref": policy.decision_ref}


def summary(setting: PerplexityDepartments) -> dict[str, Any]:
    return {"research": setting.research, "leader": setting.leader, "spend": spend_document(setting.spend_policy)}


def _spend_policy(revision_id: str, monthly_limit_usd: Decimal | None) -> PerplexitySpendPolicy:
    if monthly_limit_usd is None:
        # D125 and D138 item 4: no money limit while testing; every call is still reserved and receipted.
        return PerplexitySpendPolicy(revision_id=f"{revision_id}/spend", mode="unlimited_testing",
                                     decision_ref="D125")
    if not isinstance(monthly_limit_usd, Decimal) or not monthly_limit_usd.is_finite() or monthly_limit_usd <= 0:
        raise ValidationFailure("a monthly Perplexity limit is a positive USD amount")
    return PerplexitySpendPolicy(revision_id=f"{revision_id}/spend", mode="monthly_limit",
                                 monthly_limit_usd=monthly_limit_usd, decision_ref="D138")


def propose_perplexity_departments(source, *, research: bool, leader: bool, monthly_limit_usd: Decimal | None,
                                   revision_id: str) -> SwitchProposal:
    """The revision the owner would record, and what it changes. Writes nothing."""
    if type(research) is not bool or type(leader) is not bool:
        raise ValidationFailure("each department is switched on or off explicitly")
    current = perplexity_setting(source)
    spend = _spend_policy(revision_id, monthly_limit_usd) if research or leader else None
    try:
        proposed = PerplexityDepartments(revision_id=revision_id, research=research, leader=leader,
                                         spend_policy=spend)
    except ValidationError as error:
        raise ValidationFailure(f"the switch is not valid: {error.errors()[0]['msg']}") from None
    changes: dict[str, dict[str, Any]] = {}
    for name in PERPLEXITY_DEPARTMENTS:
        if getattr(current, name) != getattr(proposed, name):
            changes[name] = {"from": getattr(current, name), "to": getattr(proposed, name)}
    old, new = spend_document(current.spend_policy), spend_document(proposed.spend_policy)
    if old != new:
        changes["spend"] = {"from": old, "to": new}
    return SwitchProposal(current, proposed if changes else None, changes)


def service_running(source, now: datetime) -> bool:
    marks = ",".join("?" for _ in SERVICE_LEASES)
    return source.execute(f"SELECT 1 FROM process_leases WHERE lease_name IN ({marks}) AND expires_at > ?",
                          (*SERVICE_LEASES, utc_iso(now))).fetchone() is not None


def record_perplexity_departments(database, clock: Clock, *, research: bool, leader: bool,
                                  monthly_limit_usd: Decimal | None, revision_id: str,
                                  expect_revision: str | None = None, role: str = "owner") -> SwitchProposal:
    """Record the owner's revision in one transaction, or nothing when it changes nothing.

    Refused for anyone but the owner, while the paper service runs (work in flight read the revision in force),
    and when ``expect_revision`` (the one the owner previewed) is no longer the one in force.
    """
    if role != "owner":
        raise AuthorityDenied("only the owner switches departments to Perplexity")
    with database.immediate() as connection:
        if service_running(connection, clock.now()):
            raise StaleState("the paper service is running; stop it before confirming")
        proposal = propose_perplexity_departments(connection, research=research, leader=leader,
                                                  monthly_limit_usd=monthly_limit_usd, revision_id=revision_id)
        if expect_revision is not None and proposal.current.revision_id != expect_revision:
            raise StaleState(f"the Perplexity switch changed since the preview: revision "
                             f"{proposal.current.revision_id or 'none (off)'} is in force now")
        new = proposal.proposed
        if new is None:
            return proposal
        text = canonical_json(new.model_dump(mode="json"))
        connection.execute(
            f"""INSERT INTO {_TABLE} (revision_id, previous_revision_id, research, leader, spend_mode, document_json,
                document_sha256, command, set_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'owner', ?)""",
            (new.revision_id, proposal.current.revision_id, int(new.research), int(new.leader),
             None if new.spend_policy is None else new.spend_policy.mode, text, _sha256(text), COMMAND,
             utc_iso(clock.now())))
    return proposal


__all__ = ["COMMAND", "SwitchProposal", "perplexity_enabled_for", "perplexity_setting",
           "propose_perplexity_departments", "record_perplexity_departments", "service_running", "spend_document",
           "summary"]
