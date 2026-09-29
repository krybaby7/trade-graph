# 09 — Dashboard, configuration and operational runbooks

## Dashboard

Start with a responsive server-rendered interface backed by FastAPI. Server projections calculate money; do not duplicate authoritative arithmetic in the browser. Every value carries native units, EUR valuation basis, timestamp and provisional/stale status. The default account is USD10,000 virtual with EUR reporting, not EUR100.

**Overview:** allocated/current portfolio value, external flows, realized/unrealized results, trading fees, AI/other costs, net economic performance, drawdown and declared benchmark. Show simulated trading results, simulated net-economic results after allocated real expenses, and actual real-money spend distinctly. Separate setup/recurring views without excluding attributable costs from all-in performance.

**Trading:** native cash/locked balances, positions/lots/theses/invalidation, active/pending/unknown orders and fills, current mandate/strategy/experiment/exposure. Show requested/submitted sizing, rounding, fees and execution deviations. Stale marks and uncertain orders are visibly degraded, not a healthy green portfolio.

**Organisation:** active graph version, role/task state, owners/leases, active/blocked/overdue assignments, trigger events, decisions and concise rationale, research freshness, lesson revisions and counterevidence. Derive graph status from real journal/task records rather than invented agent animation.

**Costs:** accrued/settled/uncertain expenses, reservations, remaining budgets/priority reserve, role/task/root/run/provider/model/version attribution, tokens/cache/tool units, failed/retried work and invoice adjustments. Leadership and Engineer spend stay visible even when paid outside the broker account. Paper resets cannot refill the real budget.

**Improvements:** authorised tasks, actual diffs/artifacts, independent test evidence, activation/rollback history and before/after metrics. Redact private evidence; never automatically publish trading journals or source secrets to GitHub.

**Owner controls:** budgets, allowed venues/instruments/permission classes, routing/configuration, explicit pause/manage-only/flatten controls and live prerequisites. Stop on a nonflat account must choose its order/position policy; routine trades need no owner confirmation.

## Target API

These are implementation targets, not currently running endpoints:

```text
GET  /api/v1/overview
GET  /api/v1/positions
GET  /api/v1/orders
GET  /api/v1/tasks
GET  /api/v1/decisions/{id}
GET  /api/v1/research
GET  /api/v1/lessons
GET  /api/v1/costs
GET  /api/v1/changes
GET  /api/v1/events
GET  /api/v1/health
POST /api/v1/owner/budgets
POST /api/v1/owner/config
POST /api/v1/owner/pause
POST /api/v1/owner/resume
POST /api/v1/owner/enable-live
POST /api/v1/leader/tasks
POST /api/v1/leader/activate
```

Use authenticated role/owner identities, pagination, redaction, request/idempotency IDs and optimistic revision checks. Events may use authenticated SSE. Owner resume cannot bypass reconciliation; Leader cannot lift an owner halt. Graph editing cannot invent new capabilities. Bind loopback by default; remote access requires authenticated TLS/private access and CSRF protection for cookie-based write sessions.

## Configuration and secrets

Owner policy lives separately from agent-editable artifacts, with a hash/revision and restricted writer. It defines account allocations, expense periods, provider/venue allowlists, exposure bounds, live eligibility and change classes. Leader configuration validates as a subset. Candidate artifacts cannot select a new policy, DB or secret path.

config/defaults.example.json and .env.example contain public examples only. Paid calls and live mode default off. Initialization requires an explicit owner-approved real expense allowance. The paper balance is typed amount/currency; the reporting currency is a separate field. Initial USD pairs do not imply USDT/USDC. Preserve FX movement and compare to a USD-cash benchmark in EUR.

Store actual credentials outside Git and model contexts; broker keys belong only to the protected execution/kernel boundary. ChatGPT/coding-agent integrations do not supply deployed credentials automatically. Optional GitHub mirroring uses a separately scoped integration service, never the Engineer's direct access to an owner token.

## Target startup CLI

Implement equivalent commands, then replace targets with verified instructions in IMPLEMENTATION-STATUS.md:

```text
trade-graph init --mode paper --capital 10000 --capital-currency USD --reporting-currency EUR
trade-graph doctor
trade-graph demo --offline
trade-graph run --mode paper
trade-graph pause --profile manage-only
trade-graph backup --destination <private-path>
trade-graph reconcile
trade-graph report --format json
```

Default startup must not place orders, enable paid calls, purchase infrastructure or use a developer's key without deliberate configuration. Doctor is read-only except an explicitly selected credential probe, whose paid cost is reserved/reported. Preflight checks schema/lease, prices/permissions, model capabilities, market metadata, budget and recovery status.

Currently supplied scripts/check_plan.py, scripts/next_task.py and scripts/cost_model.py only inspect the plan, emit implementation work and calculate examples. They do not implement the future CLI or start a trading process.

## Runbooks

**Budget exhaustion:** stop new paid inference/research/engineering, retain software reconciliation/protection and display actual/reserved/uncertain totals. Use priority reasoning only within its remaining approved allocation. Owner can raise allowance; Leader cannot. No forced liquidation unless predeclared.

**Unknown order:** reserve possible exposure; inspect stable request/client IDs, venue history/fills and balance discrepancies; use bounded queries; block conflicting increases. Resolve through evidence and append-only corrections, not deletion or blind resubmission.

**Feed/provider outage:** expose data age/provider health, reconnect/backfill deterministically, avoid fresh increases on stale context and use only bounded approved fallback. Do not invent current market data or relabel failure as a deliberate hold.

**Bad improvement/deployment:** pause new mutable-graph decisions, preserve execution/reconciliation, restore known-good artifact/image pointer, confirm compatibility and reconcile before resuming under the strongest pause. Retain all costs and evidence. Rollback never restores an old financial portfolio.

**Host restart:** obtain one lease, load persisted pause, recover pending attempts, reconcile external balances/orders/fills and refresh metadata/feed health before increases. Restore a consistent backup only when needed; it cannot undo real trades.

**Stop/shutdown:** choose manage-only or verified flatten while nonflat. Report which native protections survive an emergency shutdown and which management cannot continue offline. An unreachable venue is not a confirmed flat account.

**Paper reset:** create a new account/experiment ID and opening event; preserve old results, actual receipts and deployment budget. Shared research costs are charged once globally and allocated with auditable weights. Synthetic replay receipts are isolated from real expense accounting.

## Maintenance

Software monitors data freshness, ledger/broker differences, unresolved orders, leases/queue backlog, forecast/uncertain expense, disk capacity, active versions and backups. Alerts do not automatically wake a committee; one bounded reasoning task is created when useful. R1 uses private dashboard/local alerts. Email/webhooks are optional later integrations requiring their own credentials, privacy controls and costs; no ChatGPT mail connection is assumed.

Back up SQLite consistently through its backup/snapshot facilities, not by copying only a main DB file while WAL writes continue. Keep appropriate private/encrypted off-host copies and test restore. Record retention/compaction policy; do not silently drop evidence for active lessons or financial outcomes. Never commit runtime journals, private account data or secrets to the public repository.
