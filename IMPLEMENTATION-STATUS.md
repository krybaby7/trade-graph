# Implementation status — remaining gates round 2

Updated: 2026-10-02. T00–T16 remain complete at their recorded scopes. **T17–T22
have reviewed implementation checkpoints; remaining acceptance gates are open.**
All six task agents are idle. `in_progress` means unfinished acceptance;
`execution_state=idle` records that no task agent is currently running.

Verified source: `d0e63aa07f2c4d00b48816a0c9bd62f08e13da90`; tree `3fa27a5d7127fcecc9caa1fcc8cdc4ab62003b39`.
Integration branch: `codex/orchestrator-continuation`.
Published branch: `cursor/trade-graph-r1-548a`; main unchanged.
Default: USD10,000 virtual capital, EUR reporting, paid calls and live disabled.
No funded soak, future observation, private venue collection or pilot is running.

## Working implementation

The installed paper CLI/service, six durable model handlers, R1 allowlisted
artifact Engineer, private dashboard, bounded acquisition, accounting, pause,
backup/offline restore and reconciliation remain usable. See [operations](docs/operations.md).
Round2 adds exact pre-dispatch FX provenance and bounded provider attempt/wire
journals; interrupted billing retains observed facts and unknown attempts never
redispatch. Legacy unlinked costs remain explicitly incomplete.

The opt-in protected runtime now confines graph planning/result transformation
for all six actual departmental handlers around protected parent routing, keys,
schemas, budgets, durable invocations and validated effects. Only actual atomic
parent-written successful effects qualify rollback baselines. Mutable failures
recover with original release/generation/authority fences; management continues
and remaining queued work survives management-only recovery. The default CLI
has no automatic switch to this runtime. Complete immutable deployment-image
and intended-host proof remain open.

New collectors retain bounded current runtime/expense facts, local host and
backup artifacts, and separately owner-granted read-only native venue evidence.
They enforce source/scope/chronology continuity and expose incomplete external
provenance. Kraken SDK REST gtc/ioc mapping is source-confirmed; actual acceptance
is pending. Live readiness revalidates retained upstream sources, separates
issuer economic declarations and stays disabled. Sealed actual plugin workers
run finite independent baseline/candidate functional shadow; this neither proves
economics nor grants production Engineer authority.

## Remaining work

| Task | Completion work |
|---|---|
| T17 | Working public connectivity, approved current routes/prices, owner-funded credentials, actual provider/invoice observation and intended-host service/backup/alert verification. |
| T18 | After genuine T17 collection, collect preregistered untouched future blocks and all four runtime arms with authenticated complete costs/market provenance; assess independence, regimes and sensitivities. Larger inventories require bounded retention design. |
| T19 | Owner-selected venue eligibility and account scope, authorized authenticated read-only observation, permanent least-privilege/withdrawal permission inventory, native fee/write/cancel/protection and intended-host conformance. SDK REST gtc/ioc mapping is source-confirmed; actual venue acceptance remains pending. |
| T20 | T18/T19 verified evidence, exact live instrument/account/policy economics, owner-designated host, explicit live allocation/loss/expense limits, protection and pilot/grant lifecycle. Readiness and enablement remain false. |
| T21 | Immutable complete dependency/OS images, trusted owner mounts/distribution, intended-host adversarial/recovery proof and default-runtime adoption. Opt-in six-role model/graph stages with protected budgets/effects are implemented. |
| T22 | T18/T21 prerequisites and explicit broader owner class grant; class-scoped Engineer commissioning/spend/repair, immutable intended-host deployment, selected-application migration rehearsal and automatic tested deployment/rollback. Sealed executable feature bundles and finite functional shadow are implemented. |

Close T17's external setup first; then actual T18 forward collection and T19 venue
verification can run in parallel. T21 intended-host/image verification can
advance independently. T20 and T22 retain their prerequisite/owner authority
gates. Current public probes still fail configured proxy CONNECT403.

## Verified checkpoint

- **1,721 full tests passed; zero failures/errors/skips**, independently counted.
- **464 mapped tests passed; all 40 applicable A01–A38/A43–A44 criteria accepted**,
  with identical clean before/after source identity. A39–A42 P6/intended-host
  completion remains pending.
- Ruff, ten planning tests, DAG/reference and tracked/staged/exact-artifact hygiene
  passed. No new CI result is claimed.
- A fresh archive installed frozen dependencies, then its wheel replaced the
  editable package. Imports outside the checkout came from installed site-packages.
  Actual installed execution applied migration 0012, preserved four offline fills
  with zero restart submissions/external calls, applied/recovered one protected
  hold, completed protected Research with a synthetic receipt and zero real
  allowance, and ran four functional shadow arms from sealed plugin bundles.
- All six task streams and independent reviews closed their scoped findings.
  Prior source/checkpoints/worktrees are preserved; local fixtures certify no
  funded provider, authenticated venue, intended host or profitability result.

Lock SHA256: `1ac5a01f8beae2c1156273feb5e664e500d6672349264e2838dd823f1c67ae39`.
Full JUnit: `/tmp/trade-graph-gates-r2-full.xml`.
Source-bound gate: `/tmp/trade-graph-gates-r2-acceptance.json`.
Installed proof: `/tmp/trade-graph-gates-r2-install-4cwq5kue/installed-work/installed-proof.json`.
See [the complete round 2 review](docs/reviews/2026-10-02-remaining-gates.md) and
[workstream history](docs/reviews/2026-10-02-task-workstreams.md).

Verified source was published by normal fast-forward from `0eb0bdf`; the remote
ref was independently matched. This documentation follows tested source. No
paid provider call, private venue call, real order, infrastructure purchase,
production deployment or broader deployed Engineer grant occurred.

## Working commands

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py
uv run pytest --junitxml=/tmp/trade-graph-verification-new.xml
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-acceptance-new.json
python3 scripts/check_plan.py
python3 scripts/test_planning.py
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use fresh artifact/demo paths. Protected departmental composition and plugin
build/shadow are opt-in Python APIs; live readiness remains advisory.
