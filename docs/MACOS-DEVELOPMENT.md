# Developing on macOS

Status: implemented 2026-10-24 on `mvp/consolidate-and-macos` (D315–D318). The
installed service still runs on Linux x86-64 (WSL2 or the protected image); a Mac
is a development machine for the offline demo, the paper service and the suite.

## Why a Mac needed changes

Four pieces of the engine assumed Linux, and each failed on macOS in a way that
looked like something else:

| Symptom on a Mac | Cause | Fix |
|---|---|---|
| `trade-graph run` refuses: "another paper service owns this database" | The service lease was a `flock()` on the SQLite file itself. Linux keeps `flock()` and `fcntl()` locks apart; macOS and the BSDs share one table, so SQLite's own lock in the same process blocked the lease. | The lease lives on a sidecar file, `<database>.lock`, beside the database (D315). |
| "database secure path refused" for anything under `/tmp` or `/var` | The no-follow directory walk refused Apple's root-owned links `/etc`, `/tmp` and `/var` (into `/private`). | Only those three links are rewritten to their `/private` targets; every other link is still refused (D316). |
| `demo --offline` crashes at the Engineer step; protected children fail closed | The checker and every confined child enter a Linux x86-64 seccomp-bpf filter and fail closed elsewhere. | An explicit development switch runs them unconfined and records that fact (D317). |
| Resource samples lack `threads` | `/proc/self/status` does not exist. | `threading.active_count()` and `getrusage` stand in (D318). |

## The development switch

```bash
export TRADE_GRAPH_UNCONFINED_CHECKER=development
```

Only that exact value turns it on. With it, on a machine **without** seccomp:

- the Engineer checker, the ledger replay probe and the confined department,
  controller, projection and plugin children run with portable resource limits
  only (CPU, file size, core, inherited descriptors closed) and report
  `mechanism: "unconfined-development-v1"` with `filesystem`, `network` and
  `process_creation` all `"unrestricted"`;
- the Engineer runner and the activation controller accept such a record, and the
  stored attestation keeps it, so HQ and the demo report say `unconfined`;
- `trade-graph demo --offline` reports `"checker_confinement": "unconfined-development-v1"`.

Where seccomp exists (Linux x86-64) the switch changes nothing: the children
confine themselves regardless, and the record says `linux-x86_64-seccomp-data-pipe-v1`.
Without the switch, a machine without seccomp still fails closed, as before.

An unconfined run proves the loop's logic, accounting and recovery. It proves
nothing about confinement, and the protected image build and the live gates do
not read the switch.

## Running here

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests
TRADE_GRAPH_UNCONFINED_CHECKER=development uv run pytest
TRADE_GRAPH_UNCONFINED_CHECKER=development uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
umask 077 && install -d -m 0700 runtime
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite --once
uv run trade-graph dashboard --database runtime/trade_graph.sqlite
```

Tests that can only pass with the kernel filter (host files, sockets and
processes really denied) are skipped on a Mac with the reason shown; tests of the
parent-side limits run in development mode. `ta-lib` ships an arm64 wheel, so no
C library install is needed.
