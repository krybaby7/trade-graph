# Orchestration continuation — 2026-10-02

Recovered published T16 `f6c36e7` from `cursor/trade-graph-r1-548a`, reproduced 808 tests and continued on
`codex/orchestrator-continuation`. Prior branches/worktrees and completion evidence were preserved.
One integration owner managed schema/dependencies; isolated service, routing, operations, owner-recovery
and readiness worktrees supplied reviewed checkpoints. Migration 0009 adds owner-command evidence without
changing the legacy command row shape. The dependency lock is unchanged.

The clean tested source is `ca4d463b9e83218d1e80f863da8b598190575280`, tree
`78187546d5044f716a7d3b70081c3a4b6c17bf21`. The handoff documentation follows that checkpoint.

## Operating slice

T17 software now includes continuous fenced paper maintenance/scheduling, background feed/role threads,
all six persisted approved routes and actual department handlers, tested routing activation/restart,
interrupted owner-command recovery, bounded owner paid permission, installed baseline artifact data,
read-only diagnostics/reporting, private consistent backup/offline restore, deployment instructions and
an opt-in funded observation command. Default assembly creates no model handlers, funding or external feed.
A public REST option uses no exchange trading key and preserves source/receipt timestamps and paper fill assumptions.

A blocked call never releases the service flock, including cancellation, maintenance failure, late heartbeat
storage work or shutdown cleanup errors. Reconciliation/protection continue during draining; the first
fatal error survives cleanup. Final poll failures are included in the returned degraded service summary.
Unknown model outcomes retain durable requests and reservations; recovery does not replay them.

Approved routing artifacts select existing immutable current cards within an explicit owner class grant.
Original JSON Schema and Pydantic checks remain authoritative after provider-specific wire conversion.
Unsupported input schemas refuse before reservations; invalid dispatched replies keep their actual supplied
usage. Owner paid permission requires separate bounded real funding and does not create runtime routes.
The private runtime independently opts in and injects credentials outside model context.

Funded observation preserves known accruals, estimated/synthetic costs, unresolved held amounts and request
forecast upper bounds with declared configured FX. A complete timer does not turn a degraded service into
success. Extraction failure preserves interrupted evidence with unavailable expenses rather than zero spend.
No funded observation was run here.

## Reviewed later foundations

T18 preregisters fixed held-out blocks, variants, baselines, bounds and sensitivities before collection;
seals an authoritative cost inventory; retains failed/setup/shared work; and reports supported,
not-supported or insufficient evidence conditionally on verified upstream collection and assumptions.
Review corrections carry peak/drawdown across the entire sampled path and place variable expenses in their
actual blocks. A EUR40 first-block charge cannot be averaged away to satisfy registered bounds. Missing
flow timing or cost timing prevents support. No actual forward trial was collected.

T19 normalizes Kraken spot metadata, balances, native fees, statuses and bounded precise chronological
history using protected transport defaults. Core reconciliation resolves identities then ingests one scoped
chronological batch; terminal states settle afterward. FIFO uses fill time and durable event order, rather
than random UUID order. Unowned/conflicting account history persists a scope-specific incomplete gate,
blocking new buys across restart while reductions remain available. Cancel/replace cannot touch another
binding; paper protection refuses live/foreign bindings. Cold/missing/increased fee bounds refuse writes.
Late history needing financial replay and values that would round are explicit refusals. Official-source
review and synthetic fixtures do not establish authenticated venue acceptance or native live protection.

T21's standalone owner-pinned numeric RPC harness launches arbitrary Python in a fresh interpreter and
installs irreversible seccomp before untrusted input. Actual-host synthetic tests probe files, descriptors,
network, process memory/signals, resources, malformed outputs and controller survival. The real financial
kernel/gateway/controller still need extraction, authenticated durable RPC and deployment/image/rollback
validation. This scaffold grants no deployed Engineer code authority. T20 and T22 remain closed.

## Verification

- Full locked suite: **1,232 passed; zero failures/errors/skips**, independently parsed JUnit.
- Bound offline run: **464 passed**, all **40 applicable criteria accepted**, matching clean source identities.
- Fresh frozen checkout and separately installed wheel: all 14 offline checks, four retained fills and zero
  restart submissions. Actual import path was asserted outside the source checkout for the wheel.
- Fresh paper init/report/three ticks and installed-wheel init/one tick: paid/live disabled, zero new model decisions.
- Ruff, JavaScript syntax, ten planning tests, DAG/reference checks, cost illustration and repository/generated
  artifact scans passed. Unit, service and wire fixtures use synthetic data and no real credentials.

The first final run's feed-drain race and renamed catalogue selector were corrected before the passing
complete reruns. Rejected artifacts are retained privately. The current opt-in external public feed smoke
failed with ProxyError and cannot certify current network operation. Provider/Kraken docs were proxy-blocked;
pinned official SDK source was inspected. Paid APIs, private exchanges, real orders, forward economics and
production deployment were not verified. No new CI conclusion is claimed.

See [status](../../IMPLEMENTATION-STATUS.md), [operations](../operations.md),
[provider wire schemas](../PROVIDER-WIRE-SCHEMAS.md), [forward evaluation](../FORWARD-EVALUATION.md),
[Kraken sources/limits](2026-10-02-kraken-foundation.md) and [process boundary](../PROCESS-BOUNDARY.md).
The task records distinguish implemented preparation from missing actual verification and authority.

Git publication was verified through `1b6b800d6246f08d61719fd4015cac265b2d2e07` by normal
fast-forward to `cursor/trade-graph-r1-548a`; this documentation follow-up records the result. Main was not merged.
