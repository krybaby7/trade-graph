# Implementation status — crypto continuation and recorded checkpoints

**Owner continuation focus, 2026-10-05:** finish the existing crypto paper
operation and forward evaluation. Read [the reassessed next steps](docs/CRYPTO-NEXT-STEPS.md).
The [multi-asset direction](docs/MULTI-ASSET-DIRECTION.md) is saved and deferred.
The direction notes preserve task acceptance status. The Mission Control
dashboard continuation below adds presentation and bounded test tracking;
the round 4 full-suite, installed-wheel and protected-image evidence remains
historical and does not certify this newer source.

## Kraken public-catalog compatibility fix — 2026-10-06

Source fix: `571765f05f45c5e2871c6ed56b13d363490d4e92` on
`codex/fix-kraken-one-character-assets-2026-10-06`, based on the clean owner
checkpoint `5f7225e`. An owner-local redacting script located the retained
observation and matched its displayed projection ID without exposing private
scope, keys or native account responses. It reported only `instruments`,
`ValidationFailure`, and request method `Assets`.

The hash-matched retained **public** Assets response contains valid one-character
asset names/altnames. The old minimum of two characters fails before the selected
BTC/USD AssetPairs request. A length-only fix still failed on the public catalog's
`USD_CREDIT`; the final bounded grammar admits one to 32 characters, beginning
with an ASCII alphanumeric and allowing alphanumerics, dots and underscores
thereafter. Alias identity, exact selected-pair scope, coherent metadata
publication, Decimal normalization and all read/write authority controls remain.

Verified on locked Python 3.12.15: **264 relevant synthetic tests passed**, including
29 new catalog/boundary/invalid-input/default-disabled-write cases. Both one-character
and underscore regressions failed before their fixes. Command:
`uv run pytest tests/integration/test_kraken_live_adapter.py
tests/integration/test_kraken_identity_recovery.py tests/integration/test_venue_conformance.py
tests/integration/test_kraken_onboarding.py tests/integration/test_kraken_onboarding_flow.py
tests/unit/test_repository_hygiene.py`. Full source/test Ruff, planning validation,
repository/staged hygiene and diff checks passed. Independent review found no blockers.

A public-only replay of the entire retained Assets response plus an actual
public HTTPS AssetPairs GET through `KrakenRestTransport` passed and returned
exactly BTC/USD. No private Kraken requests, order dispatch, paid calls or
owner-local command rerun occurred. Before/after private digest/mode comparisons
confirmed retained evidence and runtime records unchanged. Services were not
restarted. The owner will rerun the bounded command; authenticated stages,
full-suite/wheel/image verification and T17–T22 acceptance remain pending.

## Owner-local Kraken read-only implementation — 2026-10-05

Integrated source checkpoint `7cd5605` on
`codex/orchestrator-takeover-2026-10-04`, descended from `caae082`.
The CLI and dashboard implementation commits are `12277de` and `4ab5e0d`.
Development used isolated WSL2 Linux-home worktrees, followed by a fast-forward
into the existing checkout. The earlier local setup evidence below was preserved.

`uv run trade-graph kraken-read-only --database runtime/trade_graph.sqlite
--owner-directory ~/.local/share/trade-graph-owner/kraken --symbol BTC/USD`
now provides hidden owner-terminal credential entry, exact typed authorization,
separate signed/pinned private intent and bounded native collection. It validates
the existing policy, active artifact, version/generation and current retained
proof before importing an allowlisted historical projection. Defaults are
60 seconds, 128 requests, five history pages and a seven-day history window;
the signed grant expires after two minutes. Credentials remain in the local
collector process and are not saved. Private grants and retained native evidence
remain outside the checkout and financial runtime directory.

Mission Control calls this **Kraken read-only account check**. Browser Run stays
disabled and provides CLI instructions. Results show verified completed stages,
original observation/verification times, actual owned HTTPS versus synthetic
injected transport, private-read counts and pending checks. Missing stages and
unrequested order lookups remain pending. Historical freshness expires from
the original observation time; imports do not renew it or claim full reconciliation.

Verified **655 relevant synthetic tests passed**, with no final failures/skips,
including the complete command/backend/collector/verifier/import/API/HTML path
and a retained partial permission failure. Full source/test Ruff, JavaScript
syntax, staged and repository hygiene, and diff checks passed. An isolated
in-app Chromium check confirmed the disabled button, readable pending gates,
expanded-result refresh and the change from fresh to stale after 60 seconds,
with no console errors. The initial broader fixture run had one database-mode
warning failure under umask 0022; the documented private umask 0077 resolved it.
The full suite, installed wheel and protected image were not rerun/rebuilt.

Before and after integration, private digests confirmed the financial database,
existing progress history, planning acceptance, lockfile, owner-session file and
earlier local evidence remained unchanged. No runtime account was initialized
or reset, and no authenticated Kraken request or paid model call was made.
The existing loopback dashboard was reloaded onto the new source using the same
database and session file. Windows `http://localhost:8000/login` returned HTTP
200; a read-only preflight validated the existing policy/artifact scope without
requesting credentials. Financial records and retained history stayed unchanged.
Actual account connectivity, owner identity, permission inventory, withdrawal
absence, ledger reconciliation and T17–T22 acceptance remain pending. The owner
can configure a read-only key privately and run the command next; see
[permissions and instructions](docs/KRAKEN-ONBOARDING.md).

## Owner Windows/WSL2 local setup — 2026-10-05

Scoped local operation was verified on the owner's Windows PC using WSL2
Ubuntu 26.04.1 LTS, Linux x86_64 and the Linux-home checkout
`~/trade-graph`. Implementation branch:
`codex/orchestrator-takeover-2026-10-04`; source checkpoint:
`caae08262b67edb38aa2854f99cf68ec5f13a020`. Python 3.12.15 and the
unchanged committed lockfile were installed with
`uv sync --frozen --group dev --python 3.12`.

No existing Linux runtime database was found. A fresh USD10,000 virtual paper
account with EUR reporting was initialized once. Its runtime directory and
owner-session file were verified at 0700 and 0600 respectively. The dashboard
is running on loopback; the actual Windows browser address is
`http://localhost:8000/login`. Windows localhost returned HTTP 200;
authenticated login, `/progress` and `/api/v1/progress` returned HTTP 200,
and unauthenticated progress access returned HTTP 401. The session token was
kept out of command output and chat.

Actual owner-authorized Mission Control results, retained in the private
`runtime/progress-runs.sqlite` sidecar: local rehearsal passed all **14**
scripted checks, with zero external provider calls and no Kraken orders;
all **four actual Kraken public checks** passed (server time, online status,
BTC/ETH pair rules and bid/ask spreads). Private-account connectivity and
order execution were not tested. **36 targeted dashboard/progress/CLI tests**,
Ruff for source/tests, the 23-task DAG validator and **10 planning tests**
passed. The full suite and protected image/host acceptance were not run.

`trade-graph doctor` returned **degraded**, with database integrity/schema
current and warnings for a provisional financial result, pending model
routing and no persisted market observations. Its separate network/public
probe fields remain pending; Mission Control's public-check evidence does
not populate those fields. The bounded paper service `run --mode paper
--once` completed one tick with reconciled recovery, zero decisions and no
failures. Paid calls and live trading remained disabled; actual spend was
zero. No continuous trading service was started.

Private local setup, doctor and maintenance reports are retained under
`runtime/`; no tokens or private financial records are published in Git.
This scoped evidence supersedes earlier statements that this local dashboard
path had not been tested. Historical cloud proxy failures remain historical.
T17–T22 acceptance states and all funding, private-account, economic,
protected-host/image, production and live-authority gates remain unchanged.

## Kraken onboarding continuation — 2026-10-05

**Latest host direction:** test on the owner's existing Windows PC using WSL2
Ubuntu; defer a cloud-server purchase. The [local guide](docs/WINDOWS-LOCAL-TESTING.md)
provides initial dashboard and no-paid checks. The local evidence above now
records scoped Windows/WSL2 verification. Current model adapters use direct APIs;
Claude Code/Codex subscription-backed runtime adapters are not implemented.
[The interface review](docs/reviews/2026-10-05-local-subscription-options.md)
records official noninteractive CLI capabilities worth investigating; current
subscription eligibility/permitted use and a compatible gateway remain unverified.

The owner reports completed Kraken verification and Kraken Pro access. No
credentials or authenticated account observation were supplied or run here.
The [onboarding guide](docs/KRAKEN-ONBOARDING.md) records the PC-first arrangement,
iPad viewing path and first bounded read-only account check. The local dashboard
is verified on loopback; iPad access remains unverified.
The owner-local command and verified historical Mission Control import are now
implemented and tested synthetically, as recorded above. Actual authenticated
account behavior is still unverified. These changes grant no task acceptance,
spending or live authority.

## Mission Control continuation — 2026-10-05

The authenticated dashboard now includes `/progress`: an interactive department
map, the 23-task roadmap, evidence milestones, actual runtime activity and a
persistent Kraken test tracker. Login opens this view. Source checkouts refresh
planning state; installed wheels use a labelled packaged progress snapshot.
Project acceptance remains **17 done / 4 in progress / 2 blocked**.

Owner-started checks are limited to the existing isolated offline loop and four
fixed Kraken public GETs. Results live in a private sidecar database, not the
financial journal. Account reconciliation, order validation and real-order tests
remain unavailable here. Serving or refreshing the view starts no test, trading
service or paid model request. See [the guide](docs/MISSION-CONTROL.md).

Verified: **191 targeted tests passed**, full source/test Ruff, Node syntax,
planning/DAG and ten planning checks. Actual Chromium desktop/mobile checks
passed graph selection, all/blocked roadmap filters, expanded-detail retention,
owner-started rehearsal/status refresh, offline snapshots and recovery, without
horizontal overflow or browser script errors. The actual rehearsal passed all
14 scripted checks without external AI calls or Kraken orders. An actual public
check failed at connection through this cloud environment's proxy; no public,
private-account or real-order success is claimed. An offline wheel includes the
new modules, catalog, template and static assets. This is scoped dashboard
verification; the 2,544-test suite and protected image were not rerun/rebuilt.
See [the continuation review](docs/reviews/2026-10-05-mission-control.md).

## Historical published round 4 checkpoint

Updated: 2026-10-05. All six T17–T22 implementation/review streams are integrated;
local verification is complete. **Actual acceptance gates remain open.** T00–T16
retain their recorded completion scopes: 17 of 23 tasks are complete. No funded
soak, authenticated venue collection, live pilot or production rollout is running.

Verified source: `153c6a5adf9a7bd7b99a1f1c3a21cc2c3c05eb0c`.
Tree: `f00d10dc928a60fb6617279f1707ad1aa5301995`.
Branch: `codex/orchestrator-takeover-2026-10-04`; continuation from `1cb4e5a`.
Previous checkpoints, branches and worktrees remain preserved. Final documentation
follows verified source without package/README/image-input changes. This checkpoint
was verified locally on 2026-10-04. GitHub publication was verified on 2026-10-05
at `48fecba2c62df14fa1b56dbcd653f9ec381c79de` on the integration branch;
this publication record follows that commit. The existing implementation branch
is `cursor/trade-graph-r1-548a`. No merge to `main` or new CI outcome is claimed.
The runtime workflow now allows 45 minutes for the measured 25-minute full suite.
All 26 linked workspaces were audited; no omitted implementation was found.
See [the publication audit](docs/reviews/2026-10-05-publication-audit.md).
Default: USD10,000 virtual capital, EUR reporting; real operating budget separate;
paid calls and live trading disabled.

## Working implementation

The installed paper service, six durable handlers, R1 artifact Engineer,
authenticated dashboard, deterministic accounting, management, backup/offline
restore and reconciliation remain available. See [operations](docs/operations.md).

This round adds exact service binding and actual process restart proof;
preregistered four-arm paper producers and immutable historical verification;
signed native asset fees/rebates, append-only earlier-fill correction and protected
paper auxiliary fee holds; authenticated owner incident review and exact paper/live
scope mapping; complete bounded financial/cost checkpoints with a separate local
rollback witness and provider reservation/settlement publication; strict optional
provider proxy preparation; and commissioned synthetic Secretary application
generation, compatible sidecar activation, fenced health recovery and source rollback.

See [the round 4 review](docs/reviews/2026-10-04-next-contracts.md),
[financial continuity](docs/FINANCIAL-CHECKPOINTS.md),
[provider profile](docs/FUNDED-PAPER-PROFILE.md),
[application commissions](docs/APPLICATION-COMMISSIONS.md) and
[application preparation](docs/APPLICATION-PREPARATION.md).

## Verified results

- **2,544 full tests passed**, zero failures/errors/skips; independent JUnit count.
- **464 mapped tests and all 40 applicable A01–A38/A43–A44 cases passed**, with
  identical clean source before/after. A39–A42 production/intended-host acceptance
  remains pending.
- Ruff for source/tests/four verification scripts, ten planning tests, 23-task
  DAG/reference validation, diff checks and tracked/staged/exact-artifact hygiene passed.
- A fresh exact-commit archive built/installed its wheel offline after frozen
  dependency acquisition. Isolated installed imports, migrations 0001–0016, all
  14 demo checks/four fills, zero restart submissions/provider calls, maintenance,
  actual backup/offline restore, preflight and unfunded-soak refusal passed.
  Installed fee/replay/cold restart and actual two-process lost-ack service proof
  passed. Actual commissioned application generation/activation/health/restart/
  rollback retained all financial, expense and application facts without repeated work.
- A fresh whole-source immutable image passed four actual inspected containers,
  30 child OS attacks, five owner-loader attacks, the memory bound, lost-ack
  reconciliation, management during failure, finance-preserving rollback, zero
  restart dispatch and default service boot/clean stop. Root checked all 150
  protected installed source/artifact hashes and 166 build-context source files.
- No new public GET probe, funded provider, private venue or intended-host success
  is claimed. Historical public probes failed through the configured proxy.

Python 3.12.14; unchanged lock SHA256:
`1ac5a01f8beae2c1156273feb5e664e500d6672349264e2838dd823f1c67ae39`.
Full JUnit `/tmp/trade-graph-r4-final.xml`;
mapped gate `/tmp/trade-graph-r4-final-acceptance.json`;
installed evidence `/tmp/trade-graph-r4-installed-9z4c__6j/installation.json`;
image pin `/tmp/trade-graph-r4-image-153c6a5/image-pin.json`;
image proof `/tmp/trade-graph-r4-image-proof-153c6a5/proof.json`.
Image ID `sha256:8466abac94a81d2c88c13a81afb619d0ab47d0dd29971a4035da15263072b8d5`.
Private machine artifacts remain outside Git and may be transient. Exact hashes,
review findings and limits are recorded in [the review](docs/reviews/2026-10-04-next-contracts.md).
The [previous takeover review](docs/reviews/2026-10-04-orchestrator-takeover.md)
remains historical evidence.

## Remaining acceptance and implementation

| Task | Remaining work |
| --- | --- |
| T17 — in progress | Owner-designated installation, approved public connectivity/current model routes/prices, separate funding/private credentials/paid permissions, actual paper soak/receipts/invoices, intended-host restart, encrypted off-host restore and delivered alerts. Local restart proof is implemented. |
| T18 — in progress | Untouched actual future four-arm blocks, authenticated market/billing provenance, complete failed/shared costs, actual baseline execution and dependence/regime/useful-decision assessment. Historical producers and exact paper/live mapping are implemented; larger economic-source history needs design. |
| T19 — in progress | Eligible least-privilege actual native account/venue conformance; complete funding/transfer/permission/protection proofs; factual future partial-fill bounds and identified fee/rate semantics. Larger replay, finer execution ties and late fee-only amendments remain contract work. |
| T20 — blocked | Genuine T17/T18/T19/T21 evidence, full external account history, explicit owner capital/loss/all-role expense permissions and protected live commissioning; then separately authorized measured pilot/protection/stop evidence. Mapping and incident review never grant/resume live authority. |
| T21 — in progress | Actual intended-host/image/distribution admission and adversarial/recovery evidence there; externally protected rollback anchor; protected key rotation, database migration, interrupted-checkpoint recovery and manifest transition procedures. Provider profile needs separately reviewed public market/FX routing plus actual owner setup before funded soak. |
| T22 — blocked | Genuine T18/T21 evidence, broader owner class grants, production scheduling/routing/admission, funded commissioning and broader compatible migration/deployment proof. The commissioned Secretary route is implemented in explicit synthetic preparation. |

Bounds are distinct: complete economic source 8 MiB/20,000 rows; native initial
audit/new correction 4,096 rows/8 MiB; complete financial checkpoint scans
1,000,000 processed rows/512 MiB/five seconds. Scans and long normal Ledger/FIFO
projections are not incremental. The local witness cannot defeat restoring the
whole host/database/witness. The provider profile refuses public-data routing;
adding credentials alone will not close these technical gaps.

Next, prepare the owner-designated host and reviewed provider/public-data routes,
then perform authorized funded paper observation. Genuine T18 forward collection
and separately authorized T19 conformance can proceed in parallel when their
inputs are available. T20/T22 remain prerequisite and owner-authority gated.

## Working commands

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py scripts/build_protected_image.py scripts/verify_protected_image.py
uv run pytest --junitxml=/tmp/trade-graph-verification-new.xml
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-acceptance-new.json
python3 scripts/check_plan.py
python3 scripts/test_planning.py
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use fresh artifact paths. Protected composition requires explicit owner pins.
Offline plugin/application preparation does not install default production routes
or broader class grants. Live readiness remains closed.
