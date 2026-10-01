"""Bounded recursive redaction for private dashboard projections."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_PRIVATE_KEYS = {
    "api_key", "apikey", "secret", "secret_key", "password", "authorization",
    "access_token", "refresh_token", "session_token", "token", "token_hash", "csrf_secret", "account_id",
    "private_key", "credentials", "environment", "env", "request_json",
    "response_json", "provider_response", "raw_response", "conversation", "messages",
    "model_request", "raw_request", "provider_conversation",
    "chain_of_thought", "worktree", "worktree_path", "stage_path", "database_path",
}
_SECRET = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]+|\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+|"
    r"\bAKIA[A-Z0-9]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\bBearer\s+\S+|\b(?:api[_-]?key|password|secret[_-]?key|access[_-]?token)\s*[=:]\s*\S+)",
    re.IGNORECASE,
)
_PRIVATE_PATH = re.compile(
    r"(?<![:/\w])/(?!(?:api/v1|changes|decisions|static|trading|organization|costs|owner|login)(?:/|\b))"
    r"[A-Za-z_.][^\s\"'<>]*"
)
_PRIVATE_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s\"'<>]*")


def redact(value, *, _depth: int = 0):
    """Select projections first; this additional boundary never returns credentials."""
    if _depth > 20:
        return "[redacted]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            normalized = name.lower().replace("-", "_")
            if normalized in _PRIVATE_KEYS or normalized.endswith(("_api_key", "_password", "_secret")):
                result[name] = "[redacted]"
            else:
                result[name] = redact(item, _depth=_depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        if _SECRET.search(value):
            return "[redacted]"
        if value.startswith("file://"):
            return "[private path]"
        value = _PRIVATE_WINDOWS_PATH.sub("[private path]", _PRIVATE_PATH.sub("[private path]", value))
        if value.startswith(("https://", "http://")):
            try:
                parsed = urlsplit(value)
                if parsed.username or parsed.password:
                    return "[redacted]"
                # Evidence URL identity is useful; authentication query parameters are private.
                if any(marker in parsed.query.lower() for marker in ("token=", "key=", "password=", "secret=")):
                    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            except ValueError:
                return "[redacted]"
        return value[:32768]
    return value
