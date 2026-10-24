#!/usr/bin/env python3
"""Refresh the packaged Mission Control snapshot from the planning files.

An installed wheel has no planning folder, so Mission Control shows
src/trade_graph/progress_catalog.json instead. Run this after any change to
planning/tasks.json or planning/progress.json:

    uv run python scripts/update_progress_catalog.py

The snapshot keeps only the public task fields the page shows. Owners, branches,
evidence notes and orchestration records stay in planning/progress.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trade_graph.api import progress  # noqa: E402


def snapshot() -> dict:
    catalog = progress._bounded_json(ROOT / "planning/tasks.json")
    states = progress._bounded_json(ROOT / "planning/progress.json")
    return {"schema_version": 1, "updated_at": progress._text(states.get("updated_at"), 40),
            "tasks": progress._normalize_tasks(catalog.get("tasks"), states.get("tasks"))}


def main() -> int:
    path = ROOT / "src/trade_graph/progress_catalog.json"
    path.write_text(json.dumps(snapshot(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
