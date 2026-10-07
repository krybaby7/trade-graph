"""Fixed trusted readonly Ledger verifier; no candidate source or capability."""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

# The immutable script selects its own installed package, including in isolated
# worktree tests. No caller-selected module or import path enters this worker.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trade_graph.application.ledger import Ledger, _projection_sha256  # noqa: E402
from trade_graph.domain.clock import parse_utc, utc_iso  # noqa: E402
from trade_graph.domain.money import canonical_decimal  # noqa: E402

SANDBOX = runpy.run_path(str(Path(__file__).resolve().parents[1] / "adapters" / "engineering" / "sandbox.py"))
# datetime loads its trusted strptime/locale implementation lazily. Load that
# fixed dependency before denying all filesystem access and reading any input.
utc_iso(parse_utc("2000-01-01T00:00:00.000000Z"))
MAXIMUM_BYTES = 12_582_912


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate financial source field")
        result[key] = value
    return result


def main():
    try:
        SANDBOX["confine"]()
        chunks, size = [], 0
        while True:
            chunk = os.read(0, min(65536, MAXIMUM_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAXIMUM_BYTES:
                raise ValueError("complete native Ledger input exceeds bound")
            chunks.append(chunk)
        rows = json.loads(b"".join(chunks), object_pairs_hook=unique)
        if type(rows) is not list or len(rows) > 4096:
            raise ValueError("complete native Ledger source bound exceeded")
        if sum(len(row["payload_json"].encode()) for row in rows) > 8_388_608:
            raise ValueError("complete native Ledger payload bound exceeded")
        ledger = object.__new__(Ledger)
        books = ledger._replay_rows(rows)
        assets = {lot.asset for lot in books.lots}
        owned = {asset: sum(lot.open_quantity() for lot in books.lots if lot.asset == asset) for asset in assets}
        result = {"cash": {key: canonical_decimal(value) for key, value in books.cash.items()},
                  "inventory": {key: canonical_decimal(value) for key, value in owned.items()},
                  "projection_sha256": _projection_sha256(books)}
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > 65536:
            raise ValueError("native Ledger output exceeds bound")
        os.write(1, encoded)
        return 0
    except BaseException as exc:
        os.write(2, f"trusted native Ledger verification refused: {type(exc).__name__}".encode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
