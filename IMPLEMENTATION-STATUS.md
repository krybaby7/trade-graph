# Implementation status — orchestration continuation

Updated: 2026-10-02. **T00–T16 remain complete at their recorded scopes. T17's operating software is implemented; its actual public-data/funded verification remains pending.**
Local integration branch: `codex/orchestrator-continuation`.
Publication target: `cursor/trade-graph-r1-548a`; Git publication is recorded separately below.
Verified source: `ca4d463b9e83218d1e80f863da8b598190575280`.
Source tree: `78187546d5044f716a7d3b70081c3a4b6c17bf21`.
This handoff updates documentation after that clean tested checkpoint. Defaults remain USD10,000 virtual
capital, EUR reporting, paid calls disabled and live trading disabled.

## Working paper operation

The installed CLI now creates a bounded paper mandate and approved baseline, runs a continuous service,
reads genuine diagnostics/financial reports, makes private consistent backups and restores explicitly offline.
Installed artifact data travels in the wheel; normal operation and the isolated artifact Engineer need no
operator source checkout. See [the operations runbook](docs/operations.md).

The service holds one lifetime database flock and durable controller/worker leases. It coalesces schedules,
maintains every persisted paper account, scopes graph work, renews active work and reconciles/protects during
slow provider/feed work and graceful shutdown. Threads use separate database handles. Maintenance failures
cannot release ownership around an active call; late heartbeat work also drains. Signal handlers restore on
failed shutdown. Final feed poll failures retain a consistent degraded result. Mixed-mode databases are
refused by the paper service.

All six model roles have installed consumers and durable invocation recovery. Model routing uses immutable
approved current price cards and verified artifact bytes. Routing is a separate owner-granted change class;
it cannot approve providers/prices, expose credentials or increase spending authority. Provider wire-schema
conversion preserves the original protected validation and refuses unsupported schemas before billing.
See [the wire-schema review](docs/PROVIDER-WIRE-SCHEMAS.md).

Owner commands persist controller-written identity and effect evidence. Startup recovers proven local
outcomes without replaying ambiguous effects; unclear commands require owner review. Terminal replies and
pause achievement are fenced against newer controls. The dashboard exposes paid permission, which requires
an independently bounded real allowance; private runtime opt-in and approved credentialed routes are also
required. Initialization and paper gains create no expense funding.

An explicit `soak` command gates paid operation, reserves a fresh private report before effects, records
elapsed/requested duration and reports known usage accruals, synthetic/estimated costs, configured-FX
forecast bounds and unresolved held amounts separately. Failure/degradation and unavailable expense
extraction remain visible. No funded soak was performed in this continuation.

## Later-task preparation

| Task | Implemented preparation | Remaining completion evidence/work |
|---|---|---|
| T18 | Immutable preregistration/trial registry, three baselines, sealed global cost allocations, failed/setup work, paired uncertainty and sensitivity, continuous sampled drawdown and dated expense bounds | Actual authorized forward collection, untouched horizon, complete receipts, verified provenance and dependence/regime assessment |
| T19 | Normalized Kraken metadata/balances/native fees/order/fill history, protected bounded REST transport, chronological scoped core reconciliation, stable FIFO and durable incomplete-account gate | Owner-selected eligibility/permissions, authenticated read-only conformance, documented wire/native-fee limits and separate authority for any real order |
| T20 | Existing live controls stay closed | T18/T19 prerequisites and explicit owner eligibility, allocation, operating budget, protection/recovery and pilot authority |
| T21 | Owner-pinned fresh-exec seccomp scaffold with actual-host synthetic file/network/process/resource attacks and controller survival | Production financial kernel/gateway/controller extraction, authenticated durable RPC, immutable deployment images, actual deployment-host recovery/rollback tests |
| T22 | Executable plugins and application-code promotion stay disabled | T18/T21 plus explicit broader class grant, immutable staging, shadow evaluation, compatible migrations and deterministic rollout/rollback |

Read [forward evaluation](docs/FORWARD-EVALUATION.md), [Kraken foundations](docs/reviews/2026-10-02-kraken-foundation.md)
and [the process boundary](docs/PROCESS-BOUNDARY.md). Preparation does not complete observation-dependent
or production-isolation tasks. The deployed Engineer retains R1 artifact permissions.

Late newly discovered fills that would reorder already-booked history fail closed, preserving reservations
and an incomplete-account gate, pending an explicit ledger replay extension. Kraken native amounts that
would round at the protected ledger precision are refused. Native live protection remains untested and
unavailable for submission. Historical FX requires observation, validity and retrieval by each financial
instant; current cash can be valued while opening valuation/performance remains provisional. Cross-currency
receipt conversions retain stored rates but lack linked source-rate IDs; reports disclose incomplete provenance.

## Verified results

On Python 3.12.14 with unchanged frozen `uv.lock`:

- Recovered published T16 `f6c36e7` and reproduced its **808-test** baseline without resetting newer work.
- Final documented full suite: **1,232 passed; zero failures, errors or skips**, independently counted in JUnit.
- Final mapped offline gate: **464 passed**, all **40 applicable A01–A38/A43–A44 accepted**,
  `gate_complete=true`. Before/after source identities match the clean checkpoint above.
- Fresh git-archive checkout installed frozen dependencies and completed all 14 offline checks; four fills
  survived restart with zero new submissions. Fresh initialization/reporting and three maintenance ticks
  produced no paid calls, live orders or model decisions.
- Built the wheel/sdist. Installed the wheel outside the checkout with the locked environment's dependencies,
  verified imports came from the installed wheel, completed the same 14 checks and one maintenance tick.
- Ruff, JavaScript syntax, ten planning tests, DAG/reference validation, cost illustration and exact-byte
  repository/generated-artifact hygiene passed. No private runtime data was committed.

Lock SHA256: `1ac5a01f8beae2c1156273feb5e664e500d6672349264e2838dd823f1c67ae39`.
The full JUnit is `/tmp/orchestrator-final-results.xml`; the bound report is
`/tmp/trade-graph-orchestrator-final-acceptance.json`. These are local evidence, not a new CI conclusion.

The first final run found a final-drain feed classification race (1 failure/1,228 passes) and an acceptance
selector renamed during schema preflight work. Both were corrected; rejected reports remain private.
Only the corrected complete runs above support this checkpoint.

The actual opt-in public REST paper smoke failed with `feed:ProxyError`: zero observations and decisions,
degraded recovery, management continuing and exit status 1. Current external verification remains pending.
Direct provider/Kraken documentation was proxy-blocked; pinned official SDK source was read instead.
No authorized funded model configuration, paid provider request, private exchange request, real order,
production deployment or profitability result is claimed.

## Commands

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py
uv run pytest --junitxml=/tmp/orchestrator-final-results.xml
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-orchestrator-final-acceptance.json
python3 scripts/check_plan.py
python3 scripts/test_planning.py
python3 scripts/next_task.py --prompt
python3 scripts/cost_model.py
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use a new demo directory. For private initialization, continuous startup, owner controls, optional public data,
funded observation, backup and recovery, follow [operations.md](docs/operations.md).
The detailed continuation review is [here](docs/reviews/2026-10-02-orchestration.md).

## Publication

The source and handoff are committed locally for a normal fast-forward publication to the existing
implementation branch. Publication verification is recorded in `planning/progress.json`; main is unchanged.
