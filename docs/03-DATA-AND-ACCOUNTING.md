# 03 — Persistent state, financial accounting and resource costs

## Storage model

Use relational business tables plus an append-only activity/evidence journal. Decimal values are serialized as canonical strings or validated fixed-point integers; never use SQLite REAL for authoritative money/quantities. Store native assets/currencies, explicit scales and UTC event/ingestion times. Use immutable IDs and foreign keys; application-level migrations have tested forward compatibility and backups.

| Table family | Essential records and relationships |
|---|---|
| Accounts and permissions | Portfolios, broker accounts, asset metadata, owner-policy revisions, capital allocations, active pause profile with originator, mandate revisions |
| Market state | Instrument metadata/capabilities, quotes/order-book snapshots, bars/features, observed and available-at times, health, decision snapshot references |
| Durable work | Tasks, root-task budgets, leases, schedules, run/node records, role cursors, graph checkpoints, outbox entries |
| Decisions | Validated decisions, redacted raw responses, rationale/invalidation, snapshot and mandate references, associated intent IDs, no-action reasons |
| Execution | Order intents, submission/cancellation attempts, client IDs, venue order IDs, status transitions, fills, balances and reconciliation discrepancies |
| Accounting | Journal transactions and postings, inventory lots, cash-flow records, fees, cash/position projections, valuation marks, FX rate observations, performance snapshots |
| Paid resources | Provider requests/attempts, usage receipts, reservation states, versioned price cards, tool charges, expense accruals, invoices/settlements and correcting adjustments |
| Research and learning | Findings, source artifacts/hashes, strategy proposals, lesson revisions, evidence/counterexample links, experiment definitions and evaluations |
| Improvements | Authorised change tasks, worktree/artifact references, patch and test hashes, independent attestations, activation history, rollout observations and rollback records |
| Operations | Error incidents, audit events, health/backup records, deployment manifests, component fingerprints and configuration revisions |

Use unique constraints on `(venue, account, trade_id)` for fills, stable intent/client IDs, provider receipt IDs where available, task deduplication keys, and append-only revision identities. Out-of-order and duplicate feed messages must not produce duplicate financial postings. A reconciliation correction is a new journal entry linked to the original; it is not an edit that makes history disappear.

## Ledger design

Implement balanced native-asset postings, including a clearing/external account for each asset when a trade exchanges two different units. Balance each asset separately; do not add BTC and EUR quantities. A trade records the inventory transfer, cash consideration and explicit fee leg(s), all attached to one transaction group and its broker fill. EUR valuation and cost-lot projections sit above this native ledger.

Funds reserved for an open order are a classification of existing funds, not additional assets. Portfolio equity includes owned cash and marked inventory once, regardless of available/locked status. R1 forbids leverage/borrowing; nevertheless model liabilities explicitly so a future adapter cannot hide them. Asset/currency metadata and fee currency come from the adapter, not the model's arithmetic.

Use FIFO lots for initial internal realized/unrealized reporting, explicitly not a jurisdiction-specific tax report. Capitalize acquisition fees in lot cost and subtract disposal fees from sale proceeds, allocating partial fills proportionally. If a fee is paid in a third asset, reduce that asset's inventory and value the fee with an identified rate; account for its cost-basis disposal without charging the same fee twice. Fee rebates are signed amounts. Preserve original brokerage precision and rounding rules.

`FillRecord.quote_cost`, when present, is the positive finite native quote principal
excluding fees. Cash consideration, FIFO acquisition basis/disposal proceeds and
buy reservation consumption use `quote_principal`; quantity and reported price
remain unchanged. Legacy fills omit the new field and retain `quantity * price`
and identical durable JSON. Native principal, quantities, fees, identified
third-asset fee valuation and resulting cash/basis/proceeds must fit the protected
28-digit arithmetic context without rounding before mutation. Existing proportional
FIFO allocation remains a separate reporting policy. A genuine already executed
native principal that exceeds its authorized limit or original reservation is
conserved and linked to a durable execution incident; subsequent clean history
cannot clear that incident or authorize new increases.

Trading friction is already present in actual fill prices and fee debits. Spread/slippage are explanatory execution metrics against a contemporaneous reference; do not deduct them a second time from actual equity-based P&L. Estimated future liquidation fees may be shown as a separate conservative projection, not mixed with incurred costs.

## Primary performance equation

Use broker-asset equity, excluding externally accrued operating liabilities, as the primary portfolio boundary:

```text
E0, E1      = broker portfolio equity at interval start/end, marked in EUR
F           = deposits minus withdrawals, valued at each flow's event time
O_embedded  = operating-expense debits already reducing E during the interval
O_all       = all attributable operating expenses accrued for the interval

Trading result after trading costs = E1 - E0 - F + O_embedded
Net economic performance           = Trading result after trading costs - O_all
```

`O_embedded` excludes exchange trading fees: those remain in the trading result. It adds back only AI/hosting/other operating payments already reflected inside the equity boundary, to avoid subtracting them twice when all operating expenses are deducted. Invoice settlement of an already accrued expense is not a second expense. Externally paid AI costs still enter `O_all`, even though they do not reduce the exchange balance.

Record cash flows at their actual time and EUR valuation. A transfer between included accounts is internal, not a new owner deposit or performance. FX movement in owned non-EUR assets is part of EUR performance; conversion fees are recorded once. Keep setup/R&D expenditure visible in an all-in inception view as well as a clearly labeled recurring-operations view. Do not let the Leader relabel failed engineering work as excluded overhead to improve its score.

Show cash-flow-adjusted absolute P&L and, when meaningful, a time-weighted return calculated by breaking intervals at external flows. Define drawdown and benchmark equity series on the same cash-flow/expense basis. Do not divide by a changing deposited balance and call that a standard return. No annualized Sharpe claim from a handful of correlated trades; display sample length and uncertainty.

### Exact synthetic accounting fixture

Start with EUR100. Buy 1 TEST at EUR40 and pay a EUR0.40 acquisition fee. Cash becomes EUR59.60; lot cost is EUR40.40. A EUR44 mark gives equity EUR103.60 and unrealized gain EUR3.60. Sell at EUR44 with a EUR0.44 sale fee: ending cash EUR103.16 and realized trading gain EUR3.16.

An external AI expense of USD1 using an explicitly hypothetical rate of EUR0.90/USD gives net economic gain EUR2.26. A later EUR50 owner deposit changes equity, not this profit. If the same EUR0.90 operating expense instead leaves the broker account, equity becomes EUR102.26: add back EUR0.90 in the trading result and subtract it once in `O_all`, again obtaining EUR2.26. These 1% fixture fees and the FX rate are synthetic, not vendor quotes.

## Marks and foreign exchange

Store `base`, `quote`, decimal rate, source, observed time, valid-as-of time, retrieval time and rate kind. EUR invoices need no conversion. Convert USD provider accruals using an identified daily reference rate; reconcile actual invoice currency and conversion charges separately. Frankfurter's public API is a practical reference-FX source, not an executable intraday price [S17]. Carry the most recent available business-day rate with a stale/reference flag rather than fabricating weekend quotes.

Use current venue bid/ask or a declared mark convention for trading inventory. Do not assume that a stablecoin is always worth one USD. Missing/stale FX or market data makes economic totals provisional and blocks new exposure when the execution freshness rule is violated. Reserve foreign-currency spending with an owner-approved conservative conversion buffer; do not accept an unbounded EUR obligation because the latest reference rate is unavailable.

Never silently revalue historical native expenses with today's FX rate. Corrections and invoice adjustments append records. Views can offer a restated analytical series, but retain the original valuation basis and version.

## Paid-resource ledger and budgets

All paid access goes through a common gateway. Before a call, atomically reserve the worst authorized cost given input bound, maximum billable output, tool-call cap, price-card modifiers and FX reserve basis. Check owner total, period, role, task and root limits; these are nested allocations, not separate pools that can each spend the full allowance. Unused role allocations do not increase the total budget. The EUR1 priority reserve in the example sits inside EUR5, not on top.

Each attempt receipt captures provider/account project reference, model actually used, requested/resolved model version, request IDs, role/task/root/run/version, timestamps, latency, status, price-card ID, uncached input, cache read/write categories, billed output, any reasoning-token breakdown, tool units, native currency and actual/estimated monetary cost. Retain provider raw usage with secrets removed. Details that are included in a provider's output total must not be added again. Count tool-result input and all follow-up model calls, not merely the final answer.

Reservation states: `RESERVED -> COMMITTED` on known usage; unused balance released; `UNCERTAIN` on an ambiguous timeout; later `RECONCILED` by provider usage/invoice evidence or conservative charge with an explicit unresolved label. Failed work is not presumed free. A retry/fallback receives a new attempt ID and its own reservation. Cancelled streaming requests may still incur costs. Reconcile aggregate invoices to receipts and allocate unexplained differences transparently rather than suppressing them.

The price-card registry includes effective/verified dates, provider endpoint, billing currency, per-unit rates, processing tier, context band, cache category and tool charges. Reject a model/tier absent from the approved registry; refresh stale prices before new paid work. The model cannot supply the authoritative price. Owner-controlled rate-card updates require source evidence and independent validation; the Engineer cannot lower recorded prices to manufacture better performance.

The gateway must constrain potentially paid provider-side tools too. Prefer application-managed search where a hard call bound is clear; never expose open-ended paid agent/server-tool loops without a verifiable maximum. A provider-side budget setting is useful backup, not a substitute for application reservations. Errors or unknown bills can consume the reserve; surface a deficit immediately and stop new paid work rather than falsify a guarantee.

## Evidence retention and attribution

Retain every decision's point-in-time inputs, relevant original research, intent/fill links and component version. Fill events retain their original decision and execution-adapter versions even after a strategy update. A later close may reference a newer management decision; report both opening and closing provenance rather than claiming one release earned all returns from inherited positions.

Append-only is an application/permission guarantee, not magic immutability against a host administrator. Use restricted DB writes, backups and periodic journal hashes/export attestations to detect tampering. R1 evidence stays local/private; public Git stores synthetic fixtures and code only. Storage retention and compaction may discard unreferenced ticks, never the supporting evidence for retained decisions/lessons without an explicit retention policy and a visible tombstone.
