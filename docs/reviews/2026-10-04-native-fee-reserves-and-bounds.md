# Paper auxiliary fee holds and conditional native bounds — 2026-10-04

This continuation is local software preparation. All observed fills, accounts,
funds, fee plans and execution tests are synthetic or virtual paper. It performs
no paid/private venue request or live order and establishes no future Kraken
execution granularity, count, fee-vector or reserve guarantee.

The root-owned additive migration 0015 retains `native_fee_reservations` separately
from the existing one-primary-hold-per-intent table. Asset, original amount, scope,
plan identity and creation time are immutable; current held amount and state may
settle. Deletion is refused. The existing primary reservation schema remains intact.

`PaperNativeFeeReserveController.reserve(PaperNativeFeeReservePlan(...))` is an
opt-in protected parent interface. It accepts an exact frozen bounded plan for an
existing pending, unattempted intent in an open virtual paper portfolio. Execution,
Ledger, database and clock must match the concrete protected objects. The broker
must be exactly `PaperBroker`, or exactly the fixed `DropAckBroker` wrapper around
that concrete broker on the same database/clock. Arbitrary broker subclasses,
injected implementations labeled paper, live scope, duplicate/replacement plans,
primary-asset duplication, invalid native amounts and insufficient unreserved
assets are refused before effect. Revalidation prevents `model_construct` from
bypassing monetary constraints. No default runtime assembly or model-facing tool
creates this authority.

Primary and auxiliary holds contend over all intents/symbols within one virtual
portfolio. Independent virtual trials/portfolios retain independent allocations;
this does not prove a shared real venue account pool or cross-portfolio live
custody mapping. Cash and FIFO inventory for the same fee asset cannot be silently
netted. Native width and exact 28-digit arithmetic are checked before reservation.
The retained plan event explicitly records `external_venue_bound_verified=false`.

Execution consumes positive native per-asset debits from each held row. A same-fill
sale principal can fund quote fees; an uncovered quote cash debit requires its own
original asset hold. Rebates never replenish a hold. Cumulative executed debits are
compared against the immutable original allocation even after a row is released.
Unreserved/over-original fee facts remain posted and create sticky discrepancies.
Global available asset calculations include auxiliary holds, and reject corrupt
negative, nonfinite or above-original current values. UNKNOWN outcomes retain both
primary and auxiliary holds through actual SQLite restart; confirmed complete owned
terminal reconciliation or cancellation releases them. Position projections show
those holds, and fee rebate lots preserve the original fill's decision/version refs.

`conservative_partial_fill_reserve(...)` supplies conditional quote-only arithmetic:
if independently established execution minimum quantum/count, quote rounding and
fee ceilings hold, it reserves a full rounding quantum per possible fill and fees
on the enlarged principal, plus one fee quantum per fill. It expects no rebate and
does not bound base/third-asset fees. Every operand has a protected native width;
large implied counts are compared to the bounded cap before conversion to an int.
Its result always reports `venue_evidence_verified=false` and the diagnostic
`conditional_arithmetic_only_native_execution_facts_missing`. No broker, pilot or
admission controller consumes this result as evidence. Public `lot_decimals` or a
historically observed minimum cannot establish the missing future guarantee. The
retained conformance verifier now independently lists
`native_partial_fill_reserve_bound_unverified` among its permanent pending gates.

Verification: **551 tests passed**, zero failures/errors/skips, including **32**
auxiliary paper hold cases and **22** conditional arithmetic cases. The related
suite includes signed fee/native principal/late replay, adapter identity and
read-only conformance, real paper lost acknowledgements/cancellation, cold SQLite
restart, immutable original allocation, exact quote/base/third-asset accounting,
public financial projections and generated per-fill rounding bounds. Evidence:
`/tmp/t19-r4-final-checkpoint.xml`. Scoped Ruff and staged hygiene pass. Independent
review of this final auxiliary slice is requested separately; prior signed-fee and
late-replay review passed 314 cases after its three findings were fixed.

Actual native fee permission/semantics and individual third-asset valuation sources,
future partial-fill granularity/count bounds, funded paper observations, eligibility,
no-withdraw permissions, host identity and pilot authorization remain open. Earlier
bounded replay also retains its explicit source-size/tie/late-fee limitations.
