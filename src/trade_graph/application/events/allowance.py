"""The AI allowance reading recorded with every wake (D116: HQ shows each wake with the allowance it used).

Read from what the subscription admission already persists: each provider's pause state and the newest quota
reading. Nothing here calls a provider. No reading is ``unknown``, never plenty; a paused provider is ``paused``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

from trade_graph.adapters.models.subscription import sanitize_quota
from trade_graph.adapters.persistence.db import Database
from trade_graph.contracts.wakes import AllowanceReading, AllowanceWindow, ProviderAllowance
from trade_graph.domain.clock import Clock, parse_utc


def _loads(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_utc(value)
    except ValueError:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return moment if moment.tzinfo is not None else None


def _percent(value: Any) -> Decimal | None:
    if type(value) not in (int, float):
        return None
    return Decimal(str(value))


def _windows(quota: dict[str, Any]) -> list[AllowanceWindow]:
    windows: list[AllowanceWindow] = []
    named = quota.get("windows") if isinstance(quota.get("windows"), dict) else {}
    for name in ("weekly", "five_hour"):
        if isinstance(quota.get(name), dict) and name not in named:
            named = {**named, name: quota[name]}
    for name, window in sorted(named.items()):
        if not isinstance(window, dict):
            continue
        resets = window.get("resets_at")
        windows.append(AllowanceWindow(window=str(name)[:40],
                                       remaining_percent=_percent(window.get("remaining_percent")),
                                       resets_at=resets if type(resets) is int else None))
    if not windows and _percent(quota.get("remaining_percent")) is not None:
        windows.append(AllowanceWindow(window="reported", remaining_percent=_percent(quota["remaining_percent"])))
    return windows


def _has_table(connection: Database | sqlite3.Connection, name: str) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() \
        is not None


def read_allowance(database: Database, clock: Clock, *, ai_paused: bool | None = None) -> AllowanceReading:
    """The allowance as last persisted, read at ``clock.now()``. ``ai_paused`` is the service's own verdict."""
    now = clock.now()
    providers: list[ProviderAllowance] = []
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    if _has_table(database, "subscription_provider_state"):
        for row in database.execute("SELECT provider, ai_paused, reason, quota_json, updated_at "
                                    "FROM subscription_provider_state ORDER BY provider").fetchall():
            providers.append(ProviderAllowance(provider=str(row["provider"])[:64], ai_paused=bool(row["ai_paused"]),
                                               reason=(row["reason"] or None) and str(row["reason"])[:200],
                                               updated_at=str(row["updated_at"])[:40]))
            quota = sanitize_quota(_loads(row["quota_json"]))
            if (moment := _moment(quota.get("observed_at"))) is not None and moment <= now:
                candidates.append((moment, quota))
    if _has_table(database, "subscription_attempts"):
        for row in database.execute(
                """SELECT a.quota_json FROM subscription_attempts a JOIN subscription_invocations i USING(invocation_id)
                   WHERE i.synthetic = 0 AND a.quota_json <> '{}' ORDER BY a.rowid DESC LIMIT 20""").fetchall():
            quota = sanitize_quota(_loads(row["quota_json"]))
            if (moment := _moment(quota.get("observed_at"))) is not None and moment <= now:
                candidates.append((moment, quota))
    newest = max(candidates, key=lambda item: item[0], default=None)
    windows = _windows(newest[1]) if newest else []
    remaining = [window.remaining_percent for window in windows if window.remaining_percent is not None]
    paused = ai_paused if ai_paused is not None else (any(item.ai_paused for item in providers) if providers else None)
    if paused:
        status, note = "paused", ("New AI work waits (allowance floor or provider pause); management, protection "
                                  "and records continue.")
    elif newest is None:
        status, note = "unknown", "No allowance reading has been recorded; unknown is not plenty."
    else:
        status, note = "available", "The newest recorded allowance reading."
    return AllowanceReading(read_at=now, status=status, ai_paused=paused,
                            lowest_remaining_percent=min(remaining) if remaining else None,
                            observed_at=newest[0] if newest else None, providers=tuple(providers),
                            windows=tuple(windows), note=note)


__all__ = ["read_allowance"]
