# 09 — Dashboard, configuration and operational runbooks

## Dashboard structure

Use a small responsive server-rendered interface initially. Add a larger JavaScript application only if interaction complexity warrants it; portfolio accounting should not be duplicated in browser arithmetic. Server-side projections provide all authoritative money values and statuses, with native currency, EUR valuation basis, timestamps and provisional flags.

**Overview:** allocated and current equity, external cash flows, realized/unrealized trading results, trading fees, AI and other operating costs, net economic performance, drawdown and comparison to cash/declared benchmark. Show broker equity alongside all-in economic value so a profitable-looking exchange balance cannot hide externally paid AI costs. Display setup and recurring costs separately without removing either from all-in totals.

**Trading:** positions with entry thesis and invalidation, available/locked balances, active/pending/unknown orders, fills, current mandate/strategy/experiment and exposure. Show requested versus submitted sizing, rounding, fees and execution deviations. A stale mark or unresolved order receives a clear warning rather than a green healthy card.

**Organisation:** current graph version, per-role state, active/blocked/overdue tasks, triggering events, queue/lease health, recent Leader/Trader decisions and concise rationale, research freshness, active lesson revisions and contradictory evidence. The graph view derives from actual task records, not invented agent animation.

**Costs:** accrued/settled/uncertain costs, reservations, remaining budget/reserve, by role/task/root/run/model/provider/system version, token/cache/tool usage, failed and retried work, invoice adjustments and forecast versus actual. Make Engineer and Leadership expense visible. All-in economic reporting retains these expenses even when funded from a separate account.

**Improvements:** proposed/authorized tasks, diffs, validation results, activation/rollback timeline, before/after metrics and active artifact fingerprints. Evidence links open bounded redacted records. Private source text and credentials are never placed in a public URL or GitHub issue automatically.

**Controls:** owner budgets, allowed venues/instruments/capability classes, model routing, pause profiles, manage-only/flatten, configuration history and live-enable prerequisites. A stop command for a nonflat portfolio requires choosing how existing positions/orders will be managed; individual trades do not require owner confirmation.

## API contract targets

These routes are implementation targets, not currently running endpoints:

```text
GET  /api/v1/overview                 authoritative performance projection
GET  /api/v1/positions                positions, lots, management plans
GET  /api/v1/orders                   intents, uncertain status, fills
GET  /api/v1/tasks                    durable ownership and status
GET  /api/v1/decisions/{id}           rationale and original evidence refs
GET  /api/v1/research                 current/revised findings
GET  /api/v1/lessons                   revisions and counterevidence
GET  /api/v1/costs                    native and EUR expense/usage detail
GET  /api/v1/changes                  candidate/tests/activation history
GET  /api/v1/events                   paginated journal or authenticated SSE
GET  /api/v1/health                   readiness and degraded reasons
POST /api/v1/owner/budgets            owner-only policy revision
POST /api/v1/owner/config             versioned owner configuration
POST /api/v1/owner/pause              explicit profile + scope
POST /api/v1/owner/resume             cannot skip reconciliation readiness
POST /api/v1/owner/enable-live        separately gated owner operation
POST /api/v1/leader/tasks             scoped, bounded delegation
POST /api/v1/leader/activate          tested permitted candidate only
```

Paginate histories and redact payloads at the API boundary. Use request IDs/idempotency keys for owner operations, optimistic configuration revisions and server-side authorization. Never let a dashboard graph edge editor create new runtime tools without validation. Protect live-enable and budget controls with an owner identity separate from role identities. Bind to loopback by default; remote deployment requires authenticated TLS/private access and appropriate browser-session protections.

## Configuration hierarchy

Owner policy is stored separately from agent-editable artifacts with a hash/revision and restricted writer. It establishes capital allocation, spending periods, provider/venue allowlists, capability classes, exposure limits, permitted price-card updates and live eligibility. Leader mandate/configuration must validate as a subset. A graph artifact cannot choose its own owner-policy file, database or secret path.

Public config/defaults.example.json is an illustrative setup profile. It contains no authority to use real money or paid APIs. The actual initialization flow asks the owner to set a budget and explicitly enable paid calls. Mode defaults to paper. Store secrets outside the repository, redact logs, and scope exchange keys to the kernel. .env.example documents variable names only.

## Target startup interface

Implement an installed `trade-graph` CLI with commands equivalent to the following, then replace this target list with tested instructions in IMPLEMENTATION-STATUS.md:

```text
trade-graph init --mode paper --capital-eur 100
trade-graph doctor                     # configuration, DB, optional credentials
trade-graph demo --offline              # complete scripted loop, no paid calls
trade-graph run --mode paper            # requires explicit paid-call config
trade-graph pause --profile manage-only
trade-graph backup --destination <private-path>
trade-graph reconcile                  # never blindly resubmits orders
trade-graph report --format json
```

Default startup must not download paid services, silently use a developer's key or switch to live based on an environment accident. `doctor` is read-only except an explicitly requested credential probe, whose cost is reserved and reported. A startup preflight validates SQLite schema/lock, prices and permissions, provider capabilities, feed rules, current budget and last recovery state.

The currently shipped scripts/check_plan.py, scripts/next_task.py and scripts/cost_model.py only validate/select planning work and calculate estimates. They do not implement this CLI or start a trading process.

## Runbooks

**Budget exhausted:** stop new paid inference/research/engineering, retain deterministic management and reconciliation, display used/reserved/uncertain totals, and let the Leader act only if an already-authorized priority allocation remains. The owner may choose a larger budget; the Leader may not. Do not liquidate automatically unless the owner's predeclared policy requests it.

**Unknown order:** keep possible exposure reserved, inspect submission request/client ID, venue history/fills and balance differences, continue bounded queries, and block conflicting increases. Resolve with explicit evidence. A manual resolution writes an auditable correction; it never deletes the original attempt.

**Feed/provider outage:** show data-age and provider state, reconnect/backfill through software, avoid new exposure on stale context, use only an approved bounded model fallback, and keep protections/reconciliation active. No synthetic current market data is presented as a live feed.

**Bad improvement/deployment:** stop new mutable-graph decisions, keep kernel execution/reconciliation, restore known-good artifact/image pointer, confirm version and state compatibility, reconcile account, then resume according to the strongest pause. Preserve all failed-candidate evidence and costs.

**Host restart:** restore consistent database backup only when needed, retain append-only evidence, acquire one worker lease, recover pending attempts, reconcile external account state and refresh feeds before allowing increased exposure. Never restore a database snapshot as a way of undoing a real trade.

**Stop/shutdown:** prefer manage-only or verified flatten. A hard host stop while inventory remains has residual risk; show which native protections will survive and what cannot be managed without connectivity. Do not label an unreachable exchange account flat.

## Maintenance and evidence

Schedule software checks for data age, account/ledger discrepancy, unresolved intents, task lease health, spend forecast/unknown receipts, disk headroom, backups and active version consistency. A health alarm does not automatically invoke multiple AI roles; route one bounded high-priority task only when reasoning is useful.

Use local private alerts and dashboard status in R1; an optional email/webhook integration is an owner-configured later capability with its own credentials, privacy rules and any costs. Do not assume the user's ChatGPT Gmail/calendar connections exist in the deployed app.

Back up SQLite through a consistent snapshot/backup method, not by copying only the main database while WAL writes are in flight. Keep encrypted off-host copies where appropriate and test restoration. Keep audit/evidence exports private. Document retention, maximum disk use and what compaction may remove; never silently discard the evidence underpinning active lessons or financial results.
