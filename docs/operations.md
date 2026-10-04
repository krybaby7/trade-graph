# Local paper operations

Use Python 3.12, one scheduler process and private SQLite storage. Default commands
keep paid calls and live trading disabled. The optional funded section requires
explicit owner spending permission and a separately configured real allowance.
An offline result verifies functional
behavior; a public-data probe, credentialed model call, funded paper soak and forward
economic evaluation each require their own evidence. Missing evidence stays pending.

## Fresh installation and account creation

From a fresh checkout:

```bash
uv sync --frozen --group dev --python 3.12
uv run trade-graph --version
uv run trade-graph demo --offline --work /tmp/trade-graph-fresh-demo
```

Use a new demo directory each time. Inspect its private `evidence.json` and confirm
all checks passed. Preserve failed-run evidence. Scripted provider receipts are
synthetic. The installed package carries its approved baseline artifacts, so normal
operation does not require an Engineer to read an operator's source checkout.

Create a paper account once, with USD10,000 virtual capital and EUR reporting:

```bash
umask 077
install -d -m 0700 runtime runtime/backups
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph doctor --database runtime/trade_graph.sqlite
uv run trade-graph report --database runtime/trade_graph.sqlite --format json
```

Initialization creates a new account, opening event, bounded default paper mandate
and installed baseline artifact version. The initial mandate permits long-only
paper discretion inside the protected owner exposure/instrument envelope; it
does not enable inference, create a real operating allowance or grant live use.
Do not repeat initialization to repair
an existing deployment or replenish its separate real operating budget. Initial USD
cash can have unavailable EUR valuations until a sourced USD/EUR rate is persisted;
the report preserves native balances and marks the reporting result provisional.

`doctor` and `report` open an existing database read-only. They do not create a
missing database, run migrations, inspect credential values, contact providers or
change financial records. Idle databases are read without creating WAL sidecars;
running databases use existing SQLite shared-memory read locks and one consistent
read snapshot. Pending SQLite recovery or unsupported schemas are reported as
errors. Doctor returns a nonzero exit status for unavailable diagnostics, and
otherwise distinguishes degraded local readiness from healthy local checks.

The JSON report includes every receipt, reservation, position and order, with native
balances/fees, trading P&L, allocated economic P&L, actual resource accruals and
synthetic costs kept distinct. Unknown usage retains its reservation and makes
economic results provisional. Missing or stale valuation inputs do not become zero
P&L. Stored FX provenance and sampled-drawdown limitations remain visible.

## Continuous service and dashboard

First run one bounded tick to inspect recovery, then start the continuous service:

```bash
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite --once
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite
```

`run` continues until SIGINT/SIGTERM or an explicit bound. `--once` performs one
tick; `--max-ticks 3` is a bounded local exercise. The service assembles installed
baseline artifacts into the private `installed-artifacts` directory beside the
database. It does not require production source-checkout access for the Engineer.
With configured and owner-permitted model routes, it dispatches Research, Trader,
Learning, Optimisation and Leader work on durable schedules; authorized Engineer
work comes from bounded change tasks. Artifacts can override registered settings
and routing within existing owner grants.

The default service performs software maintenance/reconciliation with no paid role
dispatch. It holds an OS flock on the database for its lifetime and a durable process
lease; a second operating service is refused. Public market acquisition is also
opt-in:

```bash
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite \
  --public-data --max-ticks 3
```

This contacts Kraken public metadata/tickers and Frankfurter USD/EUR reference
data without an exchange trading key. Metadata, quote receipt/availability times
and dated FX provenance are persisted; paper fills still use simulation
assumptions. Carried weekend/holiday FX references remain stale/provisional.
The same opt-in can use `public_data_enabled: true` in the private configuration.
Offline fixtures and local readiness do not verify the current external services.

Public REST acquisition requests uncompressed data, refuses redirects and encoded
responses, and caps each response at 1 MiB. Network phases have a timeout and
streamed chunks receive an elapsed-deadline check; a blocked read can last until
its phase timeout. The transport retains the process's configured HTTP(S) proxy
policy. Public metadata must cover exactly the requested instruments; ticker
responses must contain one book whose returned native key identifies that symbol.
An unrelated book cannot be relabeled with the requested symbol. Nondefault native
ticker aliases require a prior matching metadata response. Native pair keys and
provided base/quote fields must independently agree with the normalized symbol.
A `feed:ProxyError`
means the public observation failed; old observations
cannot become fresh evidence. The 2026-10-02 workspace probes received proxy CONNECT
403 failures for both `api.kraken.com` and `api.frankfurter.dev`. Obtain approved
HTTPS access to those hosts on the selected deployment host, then repeat the bounded
public-data exercise and inspect the resulting observations and FX provenance.

Frankfurter responses must supply the requested currency direction and a genuine
ISO calendar date. Missing fields, a mismatched pair or a date after a requested
historical date are refused. A carried prior business-day date is retained and
remains provisional when used for today's valuation. Official source review
confirms the provider-scoped endpoint; it does not establish current API availability.

To configure a deployment, copy `config/paper-runtime.example.json` to a private
mode-0600 file outside Git. Its default `models: null` selects maintenance only.
`doctor --config <private-file>` uses the same protected configuration validator as
`run --config <private-file>`. For model-enabled paper operation, the nested `models`
registry, persisted price cards, owner policy and expense allowance must agree;
paper equity supplies no spending permission. Routing readiness checks existing
approved cards, provider capabilities and active verified artifact overrides.
Doctor conservatively treats paid price verifications older than 30 days, future
verification dates or future effective dates as unready, and displays that diagnostic
threshold. Prices are not refreshed automatically by diagnostics. Credential and
funded-soak outcomes remain pending until actual separately recorded checks run.

The owner dashboard runs as one separate loopback web worker:

```bash
uv run trade-graph dashboard --database runtime/trade_graph.sqlite \
  --session-file runtime/owner-session.json
```

Open `http://127.0.0.1:8000/login` and paste `session_token` from the private session
file. Keep that file out of logs, reports and repository artifacts. Run one web
worker with reload disabled. Remote use requires deliberate authenticated private
access or TLS; the supplied command binds loopback. The owner page exposes
persisted budget, policy revision, pause and paid-call permission. Save a positive
real allowance within the protected owner envelope before enabling paid permission
in the owner configuration form. Total/period/daily/root limits, per-role caps
and the priority reserve are separate limits; the priority reserve stays inside
the total. The owner command rejects paid enablement without the required real
allowance. The equivalent authenticated endpoint is
`POST /api/v1/owner/config` with `paid_calls_enabled: true`, a fresh `request_id`
and the current integer `expected_revision` from `GET /api/v1/owner/config`.
Successful budget/policy commands advance that revision. This permission alone
does not configure model routes or provider credentials.

## Protected model routing and funded paper observation

The runtime and persisted owner policy must both set `paid_calls_enabled: true`
before paid handlers are assembled. A provider key, paper capital, a planning
price snapshot or a configured model name supplies no spending permission.
Keep both flags false while preparing private configuration and reviewing
prices. Only the owner sets the real EUR allowance and permitted change classes;
routine role reservations and receipts remain enforced by the gateway.

Copy the checked-in maintenance example to a private file:

```bash
umask 077
cp config/paper-runtime.example.json runtime/paper-runtime.private.json
chmod 0600 runtime/paper-runtime.private.json
```

The following bounded routing structure references a card already approved and
persisted by the owner. `owner-reviewed-card-v1` is an example ID, not an installed
card or a quoted rate. Replace it with the exact ID of your verified card:

```json
{
  "models": {
    "deployment_id": "deployment",
    "paid_calls_enabled": false,
    "approved_price_card_ids": ["owner-reviewed-card-v1"],
    "role_routes": {
      "research": "owner-reviewed-card-v1",
      "trader": "owner-reviewed-card-v1",
      "learning": "owner-reviewed-card-v1",
      "optimisation": "owner-reviewed-card-v1",
      "leader": "owner-reviewed-card-v1",
      "engineer": "owner-reviewed-card-v1"
    }
  },
  "price_cards": [],
  "public_data_enabled": false,
  "paper_symbols": ["BTC/USD", "ETH/USD"],
  "tick_interval_seconds": 1,
  "public_poll_interval_seconds": 10
}
```

All six roles can use one approved capable provider with one private credential.
Different approved cards may be assigned where tested utility warrants them.
An empty `price_cards` list reuses persisted cards. To seed a new approved card,
include its actual complete record in that list and include the same ID in
`models.approved_price_card_ids`. The current
[PriceCard contract](../src/trade_graph/contracts/models.py) requires
`price_card_id`, `provider`, `model`, `endpoint`, `currency`,
`input_per_million`, `output_per_million`, `effective_at`, `verified_at`,
`source_id`, `tier` and `context_band`. Optional prices are
`cache_read_per_million`, `cache_write_per_million` and `search_per_call`.
Use verified nonnegative Decimal strings for rates, current ISO dates and an
auditable source reference. Paid routing requires a registered provider/model
with structured-output capability, standard tier and price verification no older
than 30 days; future verification/effective dates fail readiness. An existing card
ID cannot be silently repriced. Use another immutable card ID for new prices.
`planning/model-prices.json` is a dated planning snapshot and is not an approved
runtime price-card import.

For non-EUR cards, explicitly set `models.fx_rate` to the verified native-to-EUR
conversion as a Decimal string and `models.fx_buffer` to the chosen conservative
buffer of at least one. The default rate of one represents identity conversion;
it is not a USD/EUR quote. This operating-cost conversion is separate from the
sourced portfolio-valuation FX ledger. `models.fx_rate_id` can bind the conversion
to an exact persisted dated FX source; the protected gateway freezes the source
bytes and value before dispatch and retains that link on the receipt. Legacy
numeric-only conversions remain explicitly unlinked and cannot supply authenticated
cost-source evidence. A retained FX record still requires publisher corroboration
for external provenance. Configuration supports at most 32 approved
card IDs, 50 imported cards and 10 paper symbols, within a 262,144-byte private
file. Card IDs/prices, private routing and credential injection remain protected;
approved routing artifacts select existing card IDs and cannot introduce prices,
vendors or additional authority.

With both paid permissions disabled, seed/review the protected setup and inspect
readiness without a paid probe:

```bash
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite \
  --config runtime/paper-runtime.private.json --once
uv run trade-graph doctor --database runtime/trade_graph.sqlite \
  --config runtime/paper-runtime.private.json
```

Configure the real allowance on the authenticated owner page, then deliberately
enable its paid permission. Set the private `models.paid_calls_enabled` flag true
only for the intended funded installation, supply the selected provider's
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` through a private process environment/secret
store, and set `public_data_enabled` true. The commands do not automatically load
a `.env` file. Keep credentials out of JSON configuration, model context,
command-line arguments and Git. Restart the paper service after changing its
private configuration; enabling an owner flag cannot add handlers to an already
assembled maintenance-only process. Persisted pauses still require explicit
owner reconciliation/resume and cannot be lifted by `run` or `soak`.

Run the opt-in funded observation with no other service owning the same database:

```bash
uv run trade-graph soak --mode paper --database runtime/trade_graph.sqlite \
  --config runtime/paper-runtime.private.json --duration-seconds 60 \
  --report runtime/soak-evidence-new.json
```

Duration must be an integer from 1 through 604800 seconds. The command requires
both paid permissions, positive available real funding, approved credentialed
routes for all six roles, public-data configuration and a RUNNING portfolio. It
creates a new mode-0600 evidence file in a private directory before potentially
paid work and refuses overwriting an earlier report. Duration bounds observation,
while the real allowance bounds spending. Signal shutdown drains active work, so
elapsed duration can exceed the requested timer; stopping early is reported as
interrupted. Inspect the report even when the command fails.

The soak report includes requested/observed duration and whether the window
completed, service/failure observations, task/version/price-card attempt
attribution, known actual EUR accrued expenses, synthetic accruals, estimated or
unresolved accruals/reservations, and the forecast upper bound for the dispatched
requests. Forecasts use persisted request limits, immutable cards, frozen linked
FX values where available and the configured conservative buffer; legacy FX
falls back to the configured conversion. They are not invoices. Unknown usage
remains unresolved and reserved. The report binds start/end cutoffs to exact bounded
private SQLite snapshot digests and retains newly observed protected transport
attempts with endpoint/request/response hashes, timestamps and receipt links.
Pre-existing attempts are excluded from the observation's wire count.
`provider_transport_observation` can record `responses_observed_external_unverified`
when complete wire observations link to known-usage receipts. Native request IDs,
credential presence, transport classes and local signing do not authenticate the
provider, billing or invoice. `credentialed_provider_verification` remains
`pending`, `unresolved_billing` or `unavailable`. A zero exit status or `status: recorded`
does not certify every role/provider, invoice reconciliation, current venue
conditions or economic support. The report always labels `economic_evidence`
as `insufficient_evidence`.

Recorded service failures produce a degraded report and nonzero exit status even
when the requested window completes. Cancellation retains an interrupted report.
If expense extraction fails, the interrupted
report keeps `expenses: null` and an explicit unavailable diagnostic; it does not
replace missing accounting with zero spend. Preserve that report and the database
for local recovery before another funded observation.

T17's actual current-public-data verification and funded credentialed soak are
still pending. The [T17 continuation review](reviews/2026-10-02-t17-operations.md)
records the bounded network failures, local recovery drill and remaining owner/host
setup. T18's [forward-evaluation machinery](FORWARD-EVALUATION.md) is
partial preparation: preregister untouched fixed forward blocks and baselines,
retain all attempted/failed variants and complete actual costs, and verify
independence/regime assumptions before assessing economics. Reports may return
`supported`, `not_supported` or `insufficient_evidence`; missing, synthetic,
unresolved or inadequate forward evidence cannot become a support claim. Its
Decimal uncertainty bounds are conditional on predeclared bounded independent
blocks, with family/baseline correction; fee/slippage/model-call/missed-fill
sensitivities are declared scenarios. Neither uncertainty bounds nor positive
paper P&L authorize live trading.

T19 adapter preparation still needs owner-selected eligibility, permitted
authenticated reconciliation/conformance and protection evidence. The T21
[host-tested process scaffold](PROCESS-BOUNDARY.md) still needs production kernel
extraction, authenticated scoped RPC, independently pinned deployment/controller
artifacts and validation on the intended deployment host. T20 live enablement
and T22 broader deployed Engineer authority remain closed.

## Pause and stop

Before stopping a nonflat account, explicitly choose its order/position policy:

```bash
uv run trade-graph pause --database runtime/trade_graph.sqlite --profile manage-only
```

Manage-only blocks new exposure while reconciliation and management continue.
Inspect active/unknown orders and positions before stopping the process. An offline
process cannot continue local management. An unreachable broker does not prove a
flat account. Shutdown drains active work before releasing its lease and flock.
If shutdown is forced, retain all attempt, lease and command records. Restart
obtains the process lock, recovers stale owned work, honors the strongest persisted
pause and reconciles before allowing increases. Do not delete pending records to
make a dashboard appear healthy.

## Linux service template

[deploy/trade-graph-paper.service](../deploy/trade-graph-paper.service) is an opt-in
single-process systemd template. An administrator must create the `trade-graph`
service user, install the frozen package at `/opt/trade-graph`, create private
`/var/lib/trade-graph`, and initialize its database as that user before installing
or starting the unit. Keep the installation owned by the administrator and read-only
to the service. The supplied unit uses no credential file or model configuration,
and starts paper maintenance only. It does not enable paid calls or live mode.

Systemd creates a mode-0700 state directory and applies umask 0077. The installation
is read-only; persistent runtime writes use the private state directory, and temporary
files use a private temporary directory.
Review paths against the actual host before starting the unit. This template is
syntax-checked where systemd tooling is available; it is not evidence of deployment
on a funded production host or the broader protected-host isolation required by T21.

Stop the unit before restore, and disable automatic restart while investigating:

```bash
sudo systemctl stop trade-graph-paper.service
```

## Retained host and funding preflight facts

The fact-only `HostObservationCollector` reads an existing paper database and
private runtime configuration without migrating, creating a budget, changing
owner permissions or calling a paid provider. It lists missing configuration,
owner paid permission, real allowance, current approved role routes, selected
credential-variable presence and pause/resume prerequisites. Credential and
proxy values never enter the report. Missing sources remain pending or unavailable.

The installed preflight module provides capture and later verification. Run it on
the workspace for preparation, and repeat on the owner-designated deployment host.
Use a new report path for each observation. The key is created as a private
32-byte random file if absent; an existing private key can authenticate further
records. Keys must be owned by the current user and have a single file link.
Ancestor symlinks and hardlinked source aliases are refused. Keep the key outside
Git with the private reports:

```bash
umask 077
uv run python -m trade_graph.application.operations_preflight capture \
  --database runtime/trade_graph.sqlite \
  --config runtime/paper-runtime.private.json \
  --report runtime/host-observation-new.json \
  --key runtime/operations-observation.key --backup-restore
```

Omit `--config` when no protected configuration exists. Omitting `--database`
records missing setup without initializing a paper account. Add `--public-data`
only for the bounded unauthenticated official GETs. Capture prints a redacted
summary with the exact `evidence_sha256` and missing funded-setup prerequisites;
retain that digest separately. Exit status 3 means required local setup is pending,
2 means a requested diagnostic is refused/unavailable or capture failed, and 0
means the required local setup checks were observed. A retained report can have
`status: recorded` while its diagnostics remain pending or unavailable. No exit
status certifies funded acceptance, intended-host deployment, billing or economics.

Verify against the digest returned by the original capture, with the same
database/configuration paths and private key:

```bash
uv run python -m trade_graph.application.operations_preflight verify \
  --database runtime/trade_graph.sqlite \
  --config runtime/paper-runtime.private.json \
  --report runtime/host-observation-new.json \
  --key runtime/operations-observation.key \
  --sha256 "paste-the-evidence_sha256-returned-by-capture"
```

Verification rechecks retained local sources and does not repeat public GETs.
Changes to those sources require a new observation. Public failures retain stable
proxy/network/timeout/HTTP/schema categories and attempt/completion timestamps;
HTTP failures retain only their numeric status, with no exception text, raw request
URL, proxy value or credential value in the command summary.

The backup/restore drill creates new private copies beside the report and compares
their complete financial projections. It does not restore over the operating
database. Database snapshots/copies are capped at 16 MiB; SQLite queries have a
five-second progress deadline, backup copy/verification uses a ten-second progress
deadline, and the fixed systemd status probe has bounded output and a five-second
process deadline. Filesystem operations and blocked SQLite/network phases can
extend elapsed collection time. Optional `--public-data` makes only three
bounded official public GETs through the configured proxy and validates their
actual response pair/date/schema. It creates no portfolio observations and proves
neither current executable books nor funded-provider availability.

The report retains timestamps, actual installation fingerprint, exact stored
owner-policy hash, selected artifact hash, configuration/DB snapshot digests and
protected source/interpreter identities. Verification rechecks those sources,
filesystem permissions/inodes, any observed service state and retained
backup/restore bytes/checksums/projections. Changed sources invalidate the record;
a change during collection is retained as refused evidence. A verified document
returns a separate dictionary on each access to preserve its authenticated bytes.

The local key authenticates collector output only. An installation fingerprint is
not machine attestation. A loaded active systemd unit does not prove that its
configuration uses the observed DB/config or an owner-pinned release. Intended
host, unit binding/restart reconciliation, immutable image/mounts, off-host recovery
and actual alert delivery require separately retained deployment facts. This
collector preserves those missing claims; local recovery copies and workspace
process checks cannot satisfy them. The [second T17 gate review](reviews/2026-10-02-t17-gates-r2.md)
records actual setup/proxy outcomes and the remaining owner/host inputs.

## Consistent private backups

```bash
umask 077
install -d -m 0700 runtime/backups
uv run trade-graph backup --database runtime/trade_graph.sqlite \
  --destination runtime/backups/paper-YYYYMMDDTHHMMSSZ.sqlite
```

Use a new destination for each backup. The command uses SQLite's backup API to
capture committed WAL state, verifies the resulting database and writes the backup
and adjacent `.sha256` file with mode 0600. It refuses overwriting either file,
symlink targets, source/destination equality and public destination directories.
Publication and checksum writes are synchronized to disk. An interrupted publication
with a missing checksum is incomplete and cannot be restored by the command.

Do not copy only the main database while a writer is active. Keep the backup and
checksum together inside a private directory. Backups include private financial,
operational and authentication state. Keep an encrypted off-host copy under owner
control; do not upload runtime state to the repository or CI artifact storage.
The checksum detects accidental damage; it cannot authenticate a backup against
someone who can replace both files.

The business database retains authoritative financial/task records and approved
artifact bytes/manifests. If an installation has separate graph-checkpoint files,
inventory those files and back them up separately after quiescing their writers.
Graph checkpoints never override financial state or justify replaying an external
effect. Record source revision, lockfile identity, timestamp and checksum in a
private backup inventory. Retain referenced evidence and financial history; set an
owner retention policy before introducing compaction.

## Offline restore drill

1. Stop the service and every dashboard, CLI or maintenance process using the target.
   Ensure automatic restart cannot reopen it. Preserve its database and all sidecars
   as a private recovery set.
2. Prefer a new destination in a private directory. The command requires an explicit
   offline acknowledgement, verifies checksum and SQLite integrity, stages private
   bytes, then atomically publishes the restored database and synchronizes the
   containing directory.
3. An existing target must have no `-wal`, `-shm` or `-journal` sidecars. The service
   flock must also be free. Refusal preserves the current database. Never delete
   sidecars to bypass the check; they can contain newer financial state. A free
   flock and absent sidecars cannot prove that another dashboard or maintenance
   process has closed the file. The operator must confirm that separately.
4. Run read-only diagnostics and reporting on the restored copy. Inspect native
   balances, fills, unknown orders, reservations, real receipts, tasks, pause and
   active artifact identity. Then perform one controlled reconciliation pass with
   increases held.
5. Restart one service process only after recovery succeeds. Choose a fresh private
   owner session file if the existing token is absent from the restored database.

```bash
uv run trade-graph restore --backup runtime/backups/paper-YYYYMMDDTHHMMSSZ.sqlite \
  --database runtime/restored/trade_graph.sqlite --offline
uv run trade-graph doctor --database runtime/restored/trade_graph.sqlite
uv run trade-graph report --database runtime/restored/trade_graph.sqlite --format json
uv run trade-graph pause --database runtime/restored/trade_graph.sqlite --profile manage-only
uv run trade-graph run --mode paper --database runtime/restored/trade_graph.sqlite --once
```

A backup restores its recorded point in time. It cannot undo external trades or
charges. Artifact rollback changes an approved artifact pointer while retaining
current fills, positions, expenses and evidence; it does not restore an old financial
portfolio.

## Degraded operation

Budget exhaustion blocks new paid work while software reconciliation and protection
continue. Unknown usage remains visible as uncertain, rather than being treated as
free. Unknown orders retain possible exposure and require evidence before any
replacement. Refresh stale market/FX inputs before increasing exposure. Repair
interrupted owner commands through controller recovery and retain their durable
identity. Emergency manage-only remains available while ordinary commands wait for
recovery. Local status and alerts are read from persisted records; they do not
constitute provider, exchange, economic or live-authority evidence.
