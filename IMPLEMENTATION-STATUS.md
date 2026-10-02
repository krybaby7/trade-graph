# Implementation status — six-task orchestration

Updated: 2026-10-02. **T00–T16 are complete at their recorded scopes. T17–T22 have
verified implementation checkpoints; their remaining acceptance gates are not complete.**

Integration branch: `codex/orchestrator-continuation`.
Published implementation branch: `cursor/trade-graph-r1-548a`.
Verified application source: `9664707d5fb12b709424ef0ae8ce45bed2ce77c4`.
Source tree: `4868b219a49639b403c27748aaae18e074952e37`.
Defaults remain USD10,000 virtual capital, EUR reporting, paid calls disabled and
live trading disabled. Main is unchanged.

Six dedicated agent threads worked in isolated worktrees. They completed their
assigned checkpoints and are now idle. A task marked `in_progress` means its
acceptance work is unfinished; `execution_state` separately records actual agent
activity. No funded/forward observation, private venue test or live pilot is
running. See [the workstreams and integration results](docs/reviews/2026-10-02-task-workstreams.md).

## Working paper operation

The installed CLI creates a bounded paper mandate and approved baseline, runs a
continuous service, reads actual diagnostics/financial reports, makes private
consistent backups and restores explicitly offline. Packaged artifacts allow
normal operation and the isolated R1 artifact Engineer without a source checkout.
See [the operations runbook](docs/operations.md).

The service maintains persisted paper accounts during slow provider/feed work,
recovery and shutdown. All six model roles have protected routing and durable
invocation recovery. Paid operation requires both persisted owner permission and
private runtime opt-in, approved current price cards, configured credentials and
a separately bounded real allowance. Initialization and paper gains supply no
funding. Owner commands recover only controller-proven outcomes; ambiguous effects
remain pending review. Funded-soak reports distinguish known, estimated, synthetic
and unresolved costs. No funded soak was performed.

Public acquisition now refuses compressed, redirected, oversized and trickled
responses. Frankfurter normalization requires the actual matching wire pair and
source date; requests cannot manufacture missing provenance. Current public
probes remain blocked by the configured proxy.

## Task checkpoints and remaining work

| Task | Verified implementation | Remaining completion work |
|---|---|---|
| T17 | Continuous paper operations; bounded public transport and sourced FX; local active-service backup/offline restore/restart drill | Actual public collection, owner-funded credentialed observation, invoice comparison and intended-host operations |
| T18 | Immutable preregistration/registry, costs/uncertainty/sensitivities and snapshots bound to exact retained bytes and the full current source registry | Real preregistered future blocks, authenticated complete receipts/provenance and dependence/regime review; imports remain unverified |
| T19 | Native Kraken metadata/fees/history, complete ownership validation, durable scoped identity and cold FILLED/CANCELLED recovery | Owner-selected eligibility/permissions, authorized authenticated read-only conformance and unresolved wire/native-fee/protection proof |
| T20 | Caller flags fail closed; pinned private evidence and current budget/history/owner-recovery checks are advisory; readiness and enablement stay false | T18/T19 evidence, authoritative current upstream verification, explicit real allocation/loss/expense limits and live pilot authority |
| T21 | Actual protected paper financial service, durable authenticated one-use RPC, confined worker, source admission and independent management/recovery/rollback | Complete model/budget/departmental graph extraction, immutable dependency/OS images and owner mounts, intended-host adversarial/recovery tests |
| T22 | Sealed pure-feature staging and parent-authenticated finite replay receipts from fresh confined processes | T18/T21 plus explicit broader class grant; shadow evaluation, immutable runtime builds, migrations and automatic deployment/rollback |

T21's opt-in import API leaves the default paper runtime unchanged. Its trusted
parent alone holds the actual ledger, authority, budget aggregate and execution
objects. Requests bind current financial state, account/venue/mode, policy,
mandate, source/version and expiry. Decisions, intents, reservations and RPC
results commit atomically. Management continues during candidate failure and
cancellation; rollback preserves all financial history. Complete credentialed
model and departmental graph extraction is still implementation work, not merely
a missing credential check.

Delayed submission replies preserve newer fill/cancellation/recovery facts and
partial-fill reservations. Conflicting native acknowledgements remain incomplete
through reconciliation and require explicit protected resolution. Late history
that would reorder booked fills still fails closed pending ledger replay. Native
amounts exceeding the protected 28-digit ledger context are refused. Native stops
remain untested and disabled on submission. Direct REST versus SDK time-in-force
casing is unresolved. Historical FX availability remains point-in-time; receipt
conversions without linked source-rate IDs are disclosed as incomplete provenance.

Read [forward evaluation](docs/FORWARD-EVALUATION.md), [Kraken continuation](docs/reviews/2026-10-02-kraken-continuation.md),
[the protected process boundary](docs/PROCESS-BOUNDARY.md), [live readiness](docs/LIVE-PILOT-READINESS.md)
and [plugin staging](docs/PLUGIN-STAGING.md). Local fixtures and signed declarations
do not supply actual upstream verification or broader deployed authority.

## Verified results

At the clean source checkpoint above, Python 3.12.14 and the frozen lockfile:

- **1,473 tests passed; zero failures, errors or skips**, independently counted in JUnit.
- **464 mapped tests passed; all 40 applicable A01–A38/A43–A44 criteria accepted**,
  `gate_complete=true`. Before/after source identities are identical and clean.
- Ruff, ten planning tests, DAG/reference validation and repository/exact generated
  artifact hygiene passed. No new CI conclusion is claimed.
- A fresh Git archive installed frozen dependencies. The built wheel replaced its
  editable package and was exercised outside the checkout; imports were asserted
  to originate in installed `site-packages`.
- Installed offline execution retained four fills with zero restart submissions
  and zero external provider calls. The installed protected runtime committed one
  synthetic hold, reopened/reconciled without a new decision or submission, and
  completed two confined plugin replays with production authorization false.
- Independent cross-review verified T20's closed gate and T21's financial scope,
  concurrency, cancellation and delayed-reply behavior. Findings were fixed
  before the integrated full run.

Lock SHA256: `1ac5a01f8beae2c1156273feb5e664e500d6672349264e2838dd823f1c67ae39`.
Full JUnit: `/tmp/trade-graph-six-tasks-full.xml`.
Bound acceptance report: `/tmp/trade-graph-six-tasks-acceptance.json`.
Installed proof: `/tmp/trade-graph-six-tasks-install-9avxwdwe/installed-work/installed-proof.json`.
These are local evidence, not CI, funded or production-host results. Prior
checkpoints and branches/worktrees remain preserved.

Actual public GETs again failed with `ProxyError`/CONNECT 403. The local operations
drill succeeded, and doctor correctly reported degraded readiness; this workspace
is not an owner-designated deployment host. No provider invoice, paid model
request, private venue call, real order, production deployment, profitability result
or broader Engineer grant is claimed.

## Next work and commands

Continue T21's complete model/budget/departmental graph separation independently
of T17. Close T17 when network, intended host, approved prices/routes, credentials
and owner funding are configured. After T17, collect T18's untouched future period
and finish T19's actual venue verification in parallel. T20 and T22 retain their
respective prerequisites and explicit owner authority gates.

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py
uv run pytest --junitxml=/tmp/trade-graph-verification-new.xml
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-acceptance-new.json
python3 scripts/check_plan.py
python3 scripts/test_planning.py
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use fresh report/demo paths. Protected paper composition is an opt-in Python API;
plugin staging and live-readiness projection are independent offline/advisory
interfaces, not new production permissions or CLI activation paths.

## Publication

Verified source `9664707` was published by normal fast-forward from `4bf8ef7` to
`cursor/trade-graph-r1-548a`; the remote ref was independently read and matched.
This documentation follows the tested source. No merge or history rewrite was used.
