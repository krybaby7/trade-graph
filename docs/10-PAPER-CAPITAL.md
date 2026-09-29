# 10 — USD10,000 paper account and real-cost separation

The owner's updated instruction replaces the original EUR100 operating example with **USD10,000 virtual capital**. Amount and currency are independent typed fields. The initial reporting currency remains EUR. Default paper pairs are BTC/USD and ETH/USD, subject to public-feed metadata; do not substitute a USD stablecoin without an explicit instrument/account configuration.

## What changes

The paper account can test position sizing, multiple lots and order minimums without designing the entire system around a tiny bankroll. The core remains economical and event-driven, but avoiding every cent of inference at the expense of useful experiments is not the objective. Evaluate cheap and stronger model assignments against evidence; an operating-budget change still needs owner authorization.

The paper default is not a live capital commitment. Retain arbitrary starting-capital/currency support in CLI, schemas and tests. EUR100 remains useful as an exact arithmetic fixture and optional small-account sensitivity test, not a constraint on strategy design.

## Two ledgers and three visible results

1. The paper broker's financial ledger records simulated cash, orders, fills, fees and market P&L.
2. A real-resource expense ledger records actual API/search/engineering/hosting obligations, regardless of paper account size or resets.

Dashboard projections show simulated trading P&L; paper net-economic P&L after allocated actual operating expenses; and actual real-money operating spend. Clearly label the first two as simulated outcomes, not earned cash. An offline fixture uses synthetic receipts in a separate test namespace and must never consume or refill the real budget.

Budget reservations apply at deployment/owner scope across all paper portfolios and experiments, not separately as though each USD10,000 account were a fresh API allowance. Resetting a paper account creates a new experiment/account ID and cash-flow event. Real costs and old results persist in inception reporting. Never increase the real API budget because simulated equity rose.

When multiple experimental portfolios share research or a model request, charge the invoice/receipt once globally. Allocate its cost across experiment views with owner-defined weights summing to one, retaining the original receipt and allocation policy. Do not subtract the full shared expense from the global total once for each experiment.

## Cross-currency validation

Add acceptance case **A43**: initialize USD10,000 and, at a synthetic EUR0.90/USD mark, show EUR9,000; at EUR0.91/USD with no trade or deposit, show EUR9,100 and EUR100 FX P&L. USD cash is not treated as EUR and no stablecoin peg is assumed. Compare against a USD-cash baseline in EUR so the system does not claim currency movement as strategy alpha.

Add **A44**: resetting a paper portfolio preserves the original real receipts and remaining deployment budget; synthetic replay receipts do not alter them; shared-cost allocations reconcile to one total. Live-eligibility logic is unaffected by a paper-capital reset.

## Cost interpretation

The lean monthly paid-AI/search estimate remains USD3.4848 under the specified call/token assumptions, about 0.034848% of USD10,000 before hosting and trading friction. That percentage is arithmetic, not a forecast of returns. Fees and spreads scale with actual turnover, not simply with starting capital. The calculator uses an explicitly conservative constant reference fee for a turnover example; a real simulation should model the selected venue's account/rolling-volume tiers.

Do not inflate virtual starting capital merely to make fixed-cost ratios look good. Preserve performance at the intended eventual live allocation as a separate sensitivity run once that allocation is known. No actual future live allocation has been set by this update.
