# Implementation status — owner startup controls and recorded checkpoints

## Current owner startup implementation — 2026-10-06

Source **09160e4**, branch `codex/start-controls-2026-10-06`, based on clean WSL
checkpoint `937437e708bda23a0286de7d9c10fa4e6de9b219`. This section supersedes the
older preflight limitations below. The independent Windows checkout is preserved.

- **Dashboard/controller implemented:** Start Trading starts or attaches to one
  locked service; Start Optimisation queues one bounded owner cycle. Automatic
  scheduling and pending automatic descendants are disabled. Starts, failures,
  quota blocks and interruption recovery are durable and visible. Pause keeps
  protection active; nonflat Stop retains management until verified flat.
- **Subscription adapter implemented/tested, operationally blocked:** all six roles
  use one provider/model, one journaled invocation per task, local structured
  validation, deadlines/cancellation and no retry/repair/fallback. Actual WSL
  namespace tests deny synthetic private files and host access. Invalid/changed
  subscription admission blocks AI while deterministic management continues.
- **Protected live path implemented/tested, disabled and not commissioned:** exact
  image/config/account/owner admission precedes credential construction. Limits,
  durable attempts, uncertain reconciliation and duplicate suppression remain
  protected. Live Resume queues locally; only the exclusive worker can apply it
  after whole-account reconciliation/current grant checks. It cannot create authority.

**Research model/result: none; inference attempts: 0.** Windows Codex 0.125.0
uses ChatGPT login, but current official 0.160.1 retains built-in request/stream
retries 4/5 without a supported zero-retry override. No update solves that blocker.
Windows Claude 2.1.280 is logged out; documented controls need native 2.1.285+.
Neither native WSL CLI is installed. Official native login, disabled extra usage/
credits/purchases, root-pinned CLI and actual intended-image isolation remain
required. A subscription-specific protected provider-egress assembly is also
**not implemented/verified**: standard image networking is off and live egress
permits Kraken/FX. Login alone cannot commission the route. See
[subscription evidence and official sources](docs/SUBSCRIPTION-ADAPTER.md).

Read-only official Codex app-server metadata during this task reported weekly
**91% remaining / 9% used**, reset **2026-10-12 16:25:16 UTC**; shorter-window data
was unavailable and credits were `0`. The exact observation time was not retained.
This Windows snapshot does not establish WSL admission or Research usage. Claude
quota and subscription fee/preparation/model cost attribution remain **unknown,
not free**. Real expenses, live allocation and USD10,000 virtual paper capital
with EUR reporting remain separate.

Public history was **not refreshed**. Retained coverage: 168 completed hourly
candles each BTC/USD and ETH/USD, 2026-09-29 14:00 through 2026-10-06 14:00 UTC,
zero gaps at collection; current freshness is not claimed. Separately billed model
calls, purchases, private Kraken requests, real orders and withdrawals: **0**.
No continuous paper AI operation or live authorization occurred.

Verification: 245 startup/subscription/isolation/controller tests and 48 final
integration/dashboard tests passed. Independent review covered startup and 18 new
live Resume tests with no remaining critical issue. Source/tests Ruff, JavaScript
syntax, 23-task DAG and ten planning tests passed. The offline wheel installs with
locked dependencies and passes installed imports/0018/manual-scheduling/CLI checks;
one disposable deterministic paper tick created no orders, subscription calls or
expense receipts. Full suite is running from 09160e4; no pass is claimed yet.
A new protected image was not built or commissioned. Historical image evidence
does not certify this source. See [verification](docs/reviews/2026-10-06-start-controls.md).

From `/home/adami/trade-graph`:

```bash
uv run --frozen trade-graph dashboard --database runtime/trade_graph.sqlite --session-file runtime/owner-session.json
uv run --frozen trade-graph subscription-status --provider codex
uv run --frozen trade-graph subscription-status --provider claude
uv run --frozen trade-graph run --mode paper --database runtime/trade_graph.sqlite --once
```

Open `http://127.0.0.1:8000/progress` using the existing private owner session.
Start Trading currently supplies deterministic paper management; AI/Optimisation
stay unavailable until protected subscription admission. `--once` is a bounded
deterministic paper tick. Protected live commands/provisioning are documented in
[the commissioning guide](docs/LIVE-STARTUP-COMMISSIONING.md).

Next: native Claude login/disabled extras, approved provider egress and actual
intended-image isolation, then exactly one refreshed public Research diagnostic.
Continuous paper evaluation is a separate gate. Live additionally needs actual
read/trade-only Kraken provisioning, whole-account/uncertainty/restart/alert
verification, independent signed reviews, separate real allocation/loss allowance,
current protected image and explicit owner live grant. T17–T22 acceptance is
unchanged. Tests, Research inference, continuous paper and live operation remain
separate evidence. Installation postflight/final checkpoint pending; no push.

**Owner continuation focus, 2026-10-05:** finish the existing crypto paper
operation and forward evaluation. Read [the reassessed next steps](docs/CRYPTO-NEXT-STEPS.md).
The [multi-asset direction](docs/MULTI-ASSET-DIRECTION.md) is saved and deferred.
The direction notes preserve task acceptance status. The Mission Control
dashboard continuation below adds presentation and bounded test tracking;
the round 4 full-suite, installed-wheel and protected-image evidence remains
historical and does not certify this newer source.

## Owner-local bounded Research preflight — 2026-10-06

The WSL checkout fast-forwarded from `9dedf97` to `e920cb7`. The real existing
paper database now retains **168 completed hourly candles each for BTC/USD and
ETH/USD**, covering 2026-09-29 14:00 through 2026-10-06 14:00 UTC, with **zero gaps**.
A single bounded public-data tick refreshed quotes/FX, created no decisions and
reported no failures. Private consistent backups and preservation manifests
confirm financial/order/decision/budget/expense/authority records, retained
private Kraken evidence, progress history and owner-session bytes unchanged.
The stale dashboard process was restored on loopback using the same database
and session; WSL and Windows `/login` returned HTTP 200. Windows checkout local
changes were untouched.

**AI analysis is blocked before inference.** Installed official Codex 0.125.0
is logged in through ChatGPT, and its official account/rate-limit metadata was
verified without starting a thread or turn. Its built-in provider's automatic
retries cannot be disabled using the documented provider configuration;
installed Claude Code 2.1.280 is logged out. Shared Codex config was restored
byte-for-byte after read-only compatibility checks. No Research model call,
real model/result, direct API spend, private Kraken request or live operation
occurred. Exact available quota and unknown subscription/preparation costs are
recorded in an ignored private preflight receipt, not a fabricated usage bill.

A public-only packet, prompt and locally checked result schema are prepared
privately. No production subscription adapter or continuous graph operation
was configured, and T17–T22 acceptance is unchanged. **162 relevant tests passed**;
full-suite/wheel/image checks were not rerun. See the
[commands, preservation evidence, official sources and exact next step](docs/reviews/2026-10-06-research-subscription-preflight.md).
Documentation checkpoint branch: `codex/research-diagnostic-2026-10-06`, source
base `e920cb7`. Next: official Claude subscription login plus quota/extra-usage
verification and a tested zero-retry isolated Research invocation, or an official
Codex zero-retry configuration. Refresh the public snapshot before inference.

## Bounded public Kraken hourly history — 2026-10-06

Verified source: `ce4dbb7cca688a657e65095bd3d5eabb435ab166` on
`codex/kraken-hourly-history-2026-10-06`, based on `9dedf976`.
Documentation follows that verified source. The integration publication target
is `codex/orchestrator-takeover-2026-10-04`; no merge to `main` is required.
The sanitized source/documentation checkpoint `25611438809ecadb3436c7338b0378b36efc4a83`
was fast-forward published and independently matched to the remote branch on
2026-10-06. This publication record follows that checkpoint. No force push,
`main` merge or new GitHub CI result is claimed.

An additive 0017 migration retains immutable completed BTC/USD and ETH/USD
hourly candle revisions alongside existing financial records and snapshots.
The public collector makes at most two fixed OHLC GETs, accepts 1–719 hours,
bounds each response to 1 MiB with a ten-second timeout, and always excludes
Kraken's final uncommitted candle. It has no account/credential request route.
Restart deduplicates unchanged evidence without renewing its availability.

Research and Trader now receive the fields requested by their pinned active
strategy templates in their actual retained worker snapshots and model
requests. Decimal precision 50/ROUND_HALF_EVEN, documented 20/50-hour means,
20-hour pullback and 24-hour range definitions, source manifests and actual
receipt-time availability accompany explicit insufficient/gapped/stale or
unsupported history. Future closes and later receipts cannot enter earlier
as-of inputs. Retained contexts remain unchanged after later collection and
restart. See [formulas, limits and operations](docs/KRAKEN-PRICE-HISTORY.md).

Verified on locked Python **3.12.15** under WSL2:

- **354 relevant tests passed** in 45.05 seconds, covering exact arithmetic,
  malformed/public transport bounds, revisions/deduplication/restart, additive
  migration preservation, actual Research/Trader worker snapshots and requests,
  concurrent writer/clock boundaries, future/later-available exclusion,
  explicit missing history, evidence citations and existing paper controls.
- Full source/test Ruff, the 23-task DAG/reference validator, ten planning
  checks, repository/staged hygiene and diff checks passed. Independent
  collector, storage and context reviews found no blocking findings.
- The final wheel built offline. A fresh isolated wheel installation acquired
  the unchanged locked runtime dependencies; installed CLI, 0017 migration,
  two scripted collections, exact features, context module and installed
  strategy templates passed. The initial offline-only install lacked cached
  dependencies; no lockfile or dependency versions were changed.
- An **actual public HTTPS smoke** in a separate disposable paper database
  collected **60 BTC/USD + 60 ETH/USD completed candles**, with zero missing
  requested hours and two GETs. Both latest closes were
  `2026-10-06T12:00:00Z`; post-response availability was
  `12:33:32.636891Z` and `12:33:34.371941Z` respectively. These are actual
  public observations, distinct from synthetic checks; raw candle/runtime
  files are excluded from Git.

Bounded owner-PC command, from the updated WSL2 checkout and existing private
paper database:

```bash
uv run --frozen trade-graph collect-kraken-history --database runtime/trade_graph.sqlite --hours 168
```

This session preserved the original Windows local modification, owner WSL2
checkout, financial/runtime databases and private owner evidence. No private
account request, owner database initialization/reset, paid model call or live
order occurred. USD10,000 virtual capital/EUR reporting and separate real
budget controls remain unchanged. Full-suite, protected-image rebuild and
intended-host acceptance were not rerun; funded/forward economic evidence,
complete venue conformance and **all remaining T17–T22 gates stay pending**.

## Historical authenticated Kraken read-only observation — 2026-10-06

The owner-local retained **BTC/USD authenticated read-only observation**
completed from `2026-10-06T12:05:45.367049Z` to
`2026-10-06T12:05:46.457662Z`, verified/imported at
`2026-10-06T12:05:46.500666Z`. A read-only, allowlisted extraction from the
private Mission Control sidecar reports collector-owned HTTPS, **four
authenticated private reads**, and verified completed instrument, account-fee,
balance, open-order and bounded-native-history stages. The source matched at
import; the owner-local source checkpoint was `9dedf976ef18944e96e7127ca1e2ce7006d0d243`.

This records the verified redacted historical projection. Its original
**60-second freshness window has expired**; importing or documenting it does
not renew freshness. This coding session made no private-account requests
and did not rerun credentials or reverify the native private evidence.
Credentials, account identifiers, balances, raw payloads and verification
keys are excluded from the public checkpoint. Both earlier and successful
observations and the owner's financial/runtime records remain retained.

Still pending: unrequested order lookups and earlier history; owner
eligibility/account identity; key-permission inventory and withdrawal
absence; protected-ledger reconciliation; write/cancel uncertainty and native
stop conformance; partial-fill reserve bounds; intended-host/dependency
admission; full T17–T22 acceptance, funded/economic evidence, protected image,
production and live authorization. The read-only observation establishes
scoped authenticated connectivity, not full venue conformance. Earlier
pending statements below describe their original historical checkpoints.

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
