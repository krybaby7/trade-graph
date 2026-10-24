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

Only that exact value turns it on, and it reaches exactly two children, both
trusted code that reads data and never executes candidate code: the Engineer's
artifact checker (`adapters/engineering/checks.py`) and the ledger replay probe
(`kernel/ledger_replay_probe.py`). With it, on a machine **without** seccomp:

- those two run with portable resource limits only (CPU, file size, core,
  inherited descriptors closed) and report `mechanism: "unconfined-development-v1"`
  with `filesystem`, `network` and `process_creation` all `"unrestricted"`;
- the Engineer runner and the activation controller accept such a record, and the
  stored attestation keeps it, so HQ and the demo report say `unconfined`;
- `trade-graph demo --offline` reports `"checker_confinement": "unconfined-development-v1"`;
- the bounded runner marks every child it starts (`TRADE_GRAPH_CONFINED_CHILD=1`),
  and the switch is honoured only inside such a child, so a test or tool that
  calls `confine()` in its own process still fails closed.

Children that execute candidate code never run unconfined, whatever the
environment says: the strategy child (`adapters/isolation/mutable_child.py`),
the department and controller children, the plugin worker and the application
projection child. The reason is concrete. The installed-image probe proves
confinement by running an attack as candidate code that tries to open the
owner's files, create sockets, fork and `kill` its parent; the kernel filter is
what turns each attempt into `EPERM`. Unconfined, that attack would kill the
developer's own process, and it did while this was being built. So on a Mac the
protected-kernel, plugin and deployment-image tests fail closed as they always
did there; they do not pass and they do not harm the host.

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
parent-side limits run in development mode. The protected-kernel, plugin and
deployment-image test modules fail here for the reason above and are read on
Linux CI. `ta-lib` ships an arm64 wheel, so no C library install is needed.

Use a uv-managed CPython (`uv python install 3.12`) rather than Homebrew's: the
Homebrew framework links `site-packages` into the standard library tree, and the
runtime inventory used to refuse that link before the fix in D319; the walk now
prunes the excluded directories first and still refuses any other linked
directory.
