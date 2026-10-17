"""Which responses a browser may keep (W8-C, D253).

Every HQ response is ``Cache-Control: no-store`` (``api/app.py``), so nothing private stays in a browser cache. The
one exception is a vendored library script asked for by its pinned version: ``/static/vendor/<library>/<file>.js``
with exactly ``?v=<the version VENDOR.json records>``. That URL never changes its bytes (a new release is a new
version, and the page pins its integrity), so a phone keeps the 198 KB chart library for a year instead of fetching it
on every page load (W5-B's follow-up). It is public, unauthenticated code; no data, page or API is ever cached.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

LONG_LIVED: Final = "public, max-age=31536000, immutable"
NO_STORE: Final = "no-store"
_VENDORED: Final = re.compile(r"^/static/vendor/(?P<library>[a-z0-9][a-z0-9-]*)/[A-Za-z0-9][A-Za-z0-9._-]*\.js$")


def vendor_versions(folder: Path) -> dict[str, str]:
    """``{library folder: pinned version}`` from each ``VENDOR.json`` under ``folder`` (read once at start)."""
    versions: dict[str, str] = {}
    if not folder.is_dir():
        return versions
    for record in sorted(folder.glob("*/VENDOR.json")):
        try:
            version = json.loads(record.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", version):
            versions[record.parent.name] = version
    return versions


def cache_control(path: str, query: Sequence[tuple[str, str]], status: int, versions: dict[str, str]) -> str:
    """``Cache-Control`` for a response: long-lived only for a pinned vendored script asked for by its version."""
    match = _VENDORED.fullmatch(path)
    if match is None or ".." in path or status not in (200, 304):
        return NO_STORE
    version = versions.get(match.group("library"))
    if version is None or list(query) != [("v", version)]:
        return NO_STORE
    return LONG_LIVED


__all__ = ["LONG_LIVED", "NO_STORE", "cache_control", "vendor_versions"]
