"""Trusted artifact checks. Candidate code cannot replace this module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = ("mandate_obligations", "active_safety")


def main(argv: list[str] | None = None) -> int:
    root = Path((argv or sys.argv[1:])[0])
    policy_path = root / "artifacts" / "context_policy.json"
    if not policy_path.is_file():
        print("missing context policy")
        return 1
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy.get("max_general_lessons"), int):
        print("max_general_lessons must be an integer")
        return 1
    if not 1 <= policy["max_general_lessons"] <= 8:
        print("lesson cap out of range")
        return 1
    include = policy.get("always_include") or []
    if any(item not in include for item in REQUIRED):
        print("required lessons missing")
        return 1
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "subprocess" in text or "os.system" in text or "DROP TABLE" in text:
            print(f"forbidden content in {path}")
            return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
