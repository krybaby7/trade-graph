# Repair checkpoint; deployment blocked by storage corruption — 2026-10-08

The authenticated owner MANAGE_ONLY control was applied before repair work and
achieved `managing`. Public collection, reconciliation and dashboard initially
continued, with no in-flight subscription attempts. A consistent private backup,
the existing financial witness, all owner files and all 15 retained attempts were
preserved before source changes. No AI call was initiated for this repair.

The existing worker subsequently exited during an unexpected storage failure.
The dashboard first reported SQLite malformed-image errors; later fresh reads
reported `file is not a database`. Earlier full host/container integrity checks
had both passed. The damaged database and available evidence are retained, and
the old dashboard container is stopped. A forensic copy with only its header
repaired fails full integrity checks across several trees; it is not authoritative.
The verified pre-repair backup remains intact. No restore, reseal, database
replacement, manifest transition, deployment or resume has been performed.

The registered operating checkout is `first-paper-cycle`, branch
`codex/normal-paper-2026-10-07`, rather than the main Linux checkout. Integrated repair source is `7be13b1`; the equivalent sanitized publication
source is `4570876` on `codex/paper-repair-2026-10-08`, preserving the newer remote
manual review schedule. All five requested source repairs and the reproduced
SQLite-lock defect repair are implemented. Final regression/review evidence is
recorded in the linked repair evidence: **363 focused regressions passed** and
**108 independent continuity checks passed**, with no unresolved review findings.
Ruff, staged hygiene and planning checks passed. The installed image remains the previous image from source
`6bf6697`; its dashboard container is stopped and new repairs are not deployed. See [repair evidence](docs/reviews/2026-10-08-paper-repair.md).
Only `gpt-6.1-sol`, empty fallback and the existing strictly-more-than-40% reported
quota guard remain configured. Live trading, separately billed APIs and extras
remain disabled. Leader's original cause is still unknown.

Earlier dated sections below are historical evidence, not current service status.

---

# Normal paper operation running — 2026-10-07

The owner authorized morning operation and replaced the overnight start hold.
Existing owner controls reconciled/resumed the hold and started one normal paper
service at 08:12:48 UTC. The graph and loopback dashboard are running; normal
future schedules remain enabled. There is no mandatory one-cycle stop.

The owner's [manual review schedule and fresh-context prompts](docs/PAPER-REVIEW-SCHEDULE-2026-10-07.md)
cover 7 October 12:30/16:30/20:30 local, optional bedtime, and 8 October 12:30.
This documentation starts no review job, model work or runtime change and records
no additional operating results.

The first actual operating results are recorded in the
[morning evidence review](docs/reviews/2026-10-07-morning-paper-operation.md).
The active protected source remains **`6bf6697`**. New source `c056faf` adds
citation guidance and sanitized native diagnostics and passes relevant checks,
but its image update is blocked by the project's unimplemented financial manifest
continuity transition. The exact verified prior image/pins were restarted on the
**current database and witness**, preserving all records. No database rollback or
additional inference occurred. At 08:50:15 UTC the graph reported RUNNING, a fresh
heartbeat, AI available and no AI blockers; normal future schedules remain enabled.

- Native WSL Codex 0.160.1 uses ChatGPT subscription authentication. All six AI
  departments and every retry are pinned to **gpt-6.1-sol**; protected
  `fallback_profiles: []` and the sole-model catalog were verified. Provider model
  echo is unavailable; configured/pinned model is not mislabeled as an echo.
- Fresh weekly metadata is mandatory. Every reported supported window must have
  **strictly more than 40% remaining** before each dispatch/application retry:
  **30% reserve + 10 percentage points of headroom**. Missing shorter readings,
  reporting lag, shared-account consumption and unbounded active-call percentage
  consumption prevent an exact all-window or post-call floor guarantee.
  Metadata/model unavailability pauses new AI; management and dashboard continue.
- Refreshed public Kraken BTC/USD and ETH/USD each have 168 completed hourly
  candles through Oct 7 08:00 UTC, with zero gaps. Quotes were freshly acquired
  before AI work. The latest available ECB reference is dated Oct 6; EUR reporting
  remains provisional. This path makes no private Kraken requests.
- Research and Trader have completed validated invocations and applied results.
  Research retained two attributed findings; Trader recorded a legitimate
  `no_action`. Learning also succeeded. No orders/fills were created, and native
  accounting/financial tables match the prestart backup. Any paper orders/fills
  are **local Trade Graph simulations**, not Kraken-hosted orders.
- Initial Leader work failed on three native attempts. Optimisation inference
  completed but its applied result failed citation validation. Both remain failed
  records, with unknown costs; neither is counted as departmental success.
  A narrow citation-interface fix preserves strict validation but is not deployed.
  Original native error details were discarded, so their exact cause is still
  unknown. Deploying both fixes requires the missing protected continuity
  transition; this is existing project design, not an obsolete diagnostic gate.
- At the first post-run observation: five invocations, seven application attempts,
  four completed attempts and three failed attempts. Internal native retry counts,
  unreported usage fields, attributable shared fees and all-in costs remain unknown.
  Recorded zero API expenses do not mean subscription work was free.
- Relevant quota/migration/recovery/dashboard checks passed (218 focused cases at
  `6bf6697`); the integrated citation/diagnostics follow-up passed 149 selected
  cases at `2a5cb82`, followed by 164 final selected cases at `c056faf`. The existing
  manifest-transition refusal regression also passes. Overlapping runs are not
  summed. Both image probes/production inspections passed; active-image source,
  running security/network and model policy were reverified after recovery.
  No unrelated full-suite gate.

Original checkpoint `e36e447`, Windows local changes, original databases, owner
settings and private evidence are preserved. Sanitized publication uses a new
fast-forward checkpoint on the established working branches. Exact account
readings are excluded from current public files; previously published history is
retained because force-push/history rewriting is not authorized. Main is unchanged.
Real trading, separately billed APIs, paid extras and purchases remain disabled.
Earlier dated sections below are retained historical evidence, not current policy.

---

# Historical owner model selection checkpoint — 2026-10-07

The owner selected **GPT-6.1 Sol only**, exact ID `gpt-6.1-sol`, for all six
departments and retries through the current Codex subscription. [D97](docs/DECISIONS.md)
and the [normal operating runbook](docs/NORMAL-PAPER-OPERATION.md) record this
choice and disable authorization for alternate-model/provider fallback.

At that documentation-only checkpoint, the last prepared primary route already selected this model and had no admitted
fallback. The private PC must verify its protected primary profile, empty fallback
list and single-model catalog. This documentation-only checkpoint makes no new
claim about PC configuration, current service state or actual inference results.
No runtime records, owner files, acceptance states or source behavior changed.

---

# Normal paper preparation complete; graph awaiting owner start — 2026-10-07

The latest owner instruction is **do not start the graph**; wait for an explicit
green light tomorrow morning. The prepared normal paper dashboard is available at
<http://127.0.0.1:8000>. At 2026-10-06 23:16:50 UTC it reports **STOPPED**, zero
active graph services, MANAGE_ONLY owner hold, AI available and live unavailable.
A web server running is not a graph run. No start or scheduled model work occurred.

Source **fbed4be**, integration branch `codex/normal-paper-2026-10-07`, preserves
original `e36e447`, both original databases, owner settings and private evidence.
[Normal operating evidence](docs/reviews/2026-10-07-normal-paper-operation.md) and
[normal runbook](docs/NORMAL-PAPER-OPERATION.md) supersede diagnostic limits below;
older sections are retained historical records, not current operating policy.

- Native WSL **Codex 0.160.1**, ChatGPT subscription, configured **gpt-6.1-sol**:
  actual authentication/account metadata, ordinary allowance, credential refresh
  directory, protected provider egress and final-image process isolation verified.
  No interactive login is currently required. The configured model has not run.
- Normal departmental scheduling/recurring Optimisation, supported native retries,
  configurable application attempts and independently verified subscription fallback
  are implemented. No mandatory diagnostic, one-cycle cap or postcycle pause.
  The current owner hold remains authoritative. Claude is installed but signed out;
  no fallback route is currently configured/admitted. Billed APIs/extras remain off.
- Public refresh at **2026-10-06 23:09:42 UTC**: BTC/USD and ETH/USD each have
  **168 completed hourly candles**, Sep 29 23:00 through Oct 6 23:00 UTC, zero gaps.
  Captured quotes were 0.792074s / 0.378020s old; ECB USD/EUR **0.88739**, dated
  Oct 6, retrieved at 23:09:42 UTC. Data ages while stopped and must refresh at start.
- Research/model/application attempts **0**, Trader decisions **0**, proposed orders
  **0**, paper fills **0**. No validated Research or legitimate model no-trade result
  exists yet. Existing **USD10,000** virtual cash remains intact, no open positions
  or orders; dashboard valuation EUR8873.9. Trading/net economic PnL and shared
  subscription cost remain unknown/null. Paper orders will be **local Trade Graph
  simulations**, not Kraken-hosted orders. No private Kraken request occurred.
- Final source focused verification includes 126 usage/adapter/profile/departments
  checks and the final 129 receipt/pricing/adapter regressions; overlapping runs are
  not summed. Ruff, planning checks, actual nine-boundary image probes and strict
  production container inspection pass. No unrelated full-suite prerequisite added.
  Exact private quota/runtime receipts and original evidence remain outside Git.

No primary subscription connection blocker remains. Hosted research-search result
integration and a signed-in compatible fallback are missing optional capabilities;
short-window capacity, auto-reload setting, attributable usage and costs remain
unknown. These are disclosed rather than fabricated or relabeled as zero. The
remaining actual normal graph/Research/Trader evidence awaits the owner's start.

---

# First actual Kraken-data paper cycle — 2026-10-07

**Operating milestone pending owner subscription login.** Tested source `643f185`,
branch `codex/first-paper-cycle-2026-10-07`, descends from preserved `e36e447`.
[Concrete operating evidence](docs/reviews/2026-10-07-first-paper-cycle.md) and
[exact owner login command](docs/FIRST-PAPER-CYCLE.md#owner-login-and-included-usage-check)
supersede the earlier native-install/egress limitations below.

- Native WSL Claude 2.1.292 is signature/checksum verified and separately installed;
  final protected image, actual nine-check isolation, strict production mount
  inspection and public HTTP transport pass. Native subscription login is absent;
  extra usage/auto-reload disabled status and Claude capacity need owner verification.
- Final public refresh: 168 completed BTC/USD and ETH/USD hourly candles each,
  latest close 2026-10-06 22:00 UTC, zero gaps. Final stored quotes were 19.625036s /
  18.9596s old at 22:26:17 UTC. ECB USD/EUR 0.88739 dated 2026-10-06 was retrieved
  22:25:59 UTC. Freshness must be renewed after login.
- Diagnostic Research, normal Research, Trader and Leader inference attempts: 0.
  No validated Research, Trader decision, simulated order or fill. This is not a
  recorded model no-trade. Original USD10,000 portfolio/financial journal,
  owner budget and mandate remain intact. Explicit owner pause is now MANAGE_ONLY,
  achieved managing; service stopped and portfolio flat/no open orders.
- Three journaled native invocations maximum, each 120s / 4096 tokens / one turn / 256KiB;
  zero CLI transport/timeout retries and one structured attempt, no application
  retries/repair/fallback/API billing. A valid diagnostic gates the cycle;
  fresh quotes precede Trader. Model-free continuous management works without auth.
- 445 selected source tests pass, zero failures/errors/skips, 47.190s; Ruff and
  planning checks pass. All 95 pre-existing private files and progress sidecar
  preserved. Dashboard read evidence retained privately. No private Kraken request,
  purchase, real order or withdrawal. Unknown subscription/preparation/shared costs
  are not counted as free. Shared Codex capacity was observed privately and is not Claude
  quota; the short-window reading was unavailable.

Functioning commands include the runbook's native version/auth-status checks,
`PYTHONPATH=src "$VIRTUAL_ENV/bin/python" -m pytest` from the repository root with
the reviewed virtual environment activated and the recorded selected tests, and
the protected image's `check-subscription`. The credential-free
actual image probes/public transport passed. `research-subscription` and
`boot-subscription` are implemented/tested bounded phases, **not credentialed
successes**. Protected operating-state migration and actual inference remain
pending. No task acceptance status or live authority was advanced.

---

# Implementation status — owner startup controls and recorded checkpoints

## Current owner startup implementation — 2026-10-06

Tested source **b1ca4c5**, branch `codex/start-controls-2026-10-06`, based on clean WSL
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
had verified ChatGPT login earlier; the final login-status check cannot parse
the current `ultra` reasoning setting, even with a per-command `high` override.
Existing configuration/login files are unchanged. Current CLI login is unverified.
Current official 0.160.1 retains built-in request/stream
retries 4/5 without a supported zero-retry override. No update solves that blocker.
Windows Claude 2.1.280 is logged out; documented controls need native 2.1.285+.
Neither native WSL CLI is installed. Official native login, disabled extra usage/
credits/purchases, root-pinned CLI and actual intended-image isolation remain
required. A subscription-specific protected provider-egress assembly is also
**not implemented/verified**: standard image networking is off and live egress
permits Kraken/FX. Login alone cannot commission the route. See
[subscription evidence and official sources](docs/SUBSCRIPTION-ADAPTER.md).

Read-only official Codex account metadata was observed during that historical
preparation. Exact account capacity, reset and credit readings are retained
privately. The shorter-window reading was unavailable. These Windows observations
did not establish WSL admission or Research usage. Claude quota and subscription
fee/preparation/model cost attribution remained unknown, not free. Real expenses,
live allocation and virtual paper capital remained separate.

Public history was **not refreshed**. Retained coverage: 168 completed hourly
candles each BTC/USD and ETH/USD, 2026-09-29 14:00 through 2026-10-06 14:00 UTC,
zero gaps at collection; current freshness is not claimed. Separately billed model
calls, purchases, private Kraken requests, real orders and withdrawals: **0**.
No continuous paper AI operation or live authorization occurred.

Verification: **435 final-source tests pass**, zero failures/skips, in 39.96 seconds:
startup/subscription/isolation/controller/live, private SQLite, dashboard financial
projections, manual cycles and public proxy controls. Unknown subscription
inference/shared fees keep economic P&L and all-in costs provisional/null;
recorded expenses, allowance, trading P&L and virtual capital remain unchanged.
Independent review passes 84 financial/dashboard tests and 17 proxy tests, with
no remaining critical finding. Earlier 275, 245 and 48-test slices passed.
The frozen 90d4f55 full run stopped after **2,905 passed / one failed** on a stale
test expectation about explicit `trust_env`. Correction 98af9a4 passes all 227
unit/funded-profile regressions. Collected node IDs match successful XML results
across these runs and the final 435-test slice: **all 2,998 collected cases covered**.
This is combined verification, not a clean monolithic final-source full run.
An earlier interrupted run exposed private-file/soak regressions; both were fixed
and retested. Source/tests Ruff, JavaScript syntax, 23-task DAG and 16 planning/
progress tests pass. The final offline wheel installs into the previously frozen
dependency environment; all 58 packages are compatible. Installed imports, private
0018 storage, CLI/manual scheduling and unknown-fee projections pass. One final
disposable deterministic paper tick created no orders, AI calls or expense receipts.
A new protected image was not built or commissioned. Historical image evidence
does not certify this source. See [verification](docs/reviews/2026-10-06-start-controls.md).

From the repository root:

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
separate evidence.

The clean WSL installation fast-forwarded to tested source **b1ca4c5** on
`codex/orchestrator-takeover-2026-10-04`. The dashboard alone restarted on loopback
8000 with its existing session/database: WSL and Windows login returned HTTP 200;
authenticated progress is paper/IDLE, AI blocked, live disabled, automatic
Optimisation false, with zero graph worker starts or subscription invocations.
All existing financial/progress table rows and all 30 protected-file contents/modes
match the consistent backups. The only old-table addition is schema version 0018;
new provider metadata records the observed quota and refusal. No owner policy or
budget changed. Sanitized verification documentation follows this tested source;
the final local checkpoint is reported with the handoff. No push.

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
