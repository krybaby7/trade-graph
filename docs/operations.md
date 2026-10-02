# Local paper operations

Use Python 3.12, one scheduler process and private SQLite storage. These instructions
keep paid calls and live trading disabled. An offline result verifies functional
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

Initialization creates a new account and opening event. Do not repeat it to repair
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

The default service performs software maintenance/reconciliation with no paid role
dispatch. It holds an OS flock on the database for its lifetime and a durable process
lease; a second operating service is refused. A bounded local exercise can use
`--max-ticks 3`. A deliberate `--public-data` option enables the public market feed
without an exchange trading key. Local observations do not themselves prove that
an external public-data smoke test succeeded.

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
access or TLS; the supplied command binds loopback.

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
