"""Owner and role sessions. Cookie writes require a CSRF token."""

from __future__ import annotations

import hashlib
import secrets

from trade_graph.adapters.persistence.db import Database
from trade_graph.domain.clock import Clock, utc_iso


def issue_session(database: Database, clock: Clock, role: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    csrf = secrets.token_urlsafe(16)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with database.immediate() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, role, csrf_secret, created_at) VALUES (?, ?, ?, ?)",
            (digest, role, csrf, utc_iso(clock.now())),
        )
    return token, csrf


def role_for_token(database: Database, token: str | None) -> str | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = database.execute(
        "SELECT role, csrf_secret FROM sessions WHERE token_hash = ?",
        (digest,),
    ).fetchone()
    if row is None:
        return None
    return row["role"]


def csrf_for_token(database: Database, token: str) -> str | None:
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = database.execute(
        "SELECT csrf_secret FROM sessions WHERE token_hash = ?",
        (digest,),
    ).fetchone()
    return row["csrf_secret"] if row else None
