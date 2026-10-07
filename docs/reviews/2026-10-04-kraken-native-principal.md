# Kraken native quote principal — 2026-10-04

This checkpoint implements the protected owner's agreed additive financial contract
for rounded native quote principal. All transport/account/order facts used here are
synthetic. No private venue request, real order, cancellation, paid call or deployment
was performed. T19 and actual pilot readiness remain incomplete.

## Native facts and accounting

`FillRecord.quote_cost` is optional positive finite Decimal quote principal excluding
fees. It is omitted when absent, preserving legacy durable JSON and legacy
`quantity * price` behavior. A rounded fill retains its original reported quantity
and price; `quote_principal` supplies the exact native principal for cash consideration,
FIFO acquisition cost/disposal proceeds and buy reservation consumption. Trading
fees remain explicit and are charged once. Native principal is not an operating
expense; retained runtime expense projections continue to derive fees and operating
receipts independently of trading cash consideration.

The Kraken adapter accepts a rounded principal only with selected native metadata
that explicitly declares `cost_decimals`, cost aligned to that quantum, discrepancy
strictly less than one quantum from exact quantity times price, and independently
matching native base/quote ledger legs. Wrong ledger legs, undeclared precision,
off-quantum or oversized discrepancies and protected 28-digit precision overflow
remain refused. Quantity and price are never rewritten to manufacture agreement.
Shared books preflight exact native cash, quantity, fee and identified third-asset
fee valuation plus resulting basis/proceeds before mutation. Existing proportional
FIFO allocation keeps its existing reporting policy.

Executed facts that exceed an intent limit or original reservation still post their
exact ledger legs. A durable `native_fill_execution_limit_discrepancy` incident
identifies the intent/trade, native principal and reasons, and blocks new increases
across restart and later clean history scans. Duplicate retained fills repair a
missing incident projection without duplicating financial facts. Incident resolution
requires a separate protected owner contract; this checkpoint provides no automatic
clearance. Reconciliation and order/position management continue.

Guarded submission requires explicit quote cost precision and rejects declared grids
that permit native rounding. Compatible declared order grids are only a necessary
arithmetic check: `lot_decimals` does not independently prove actual native
partial-fill quantity precision or execution count. There is no protected factual
bound on partial-fill rounding/reserve today. Concrete production conformance and
pilot gates therefore remain refused; the low-level synthetic compatible-grid
request tests are not private acceptance or permission evidence. Native observed
fills can retain finer exact quantities independently of submitted order grids.
Source identity pins now include books, ledger and execution in addition to the
adapter, transport and DTO contract; prior observations do not silently approve a
changed financial implementation.

## Remaining shape contracts

Multiple native fee assets still cannot be compressed into the existing single
fee amount/asset without losing their independent ledger identities or allocating
their cost twice. Support needs an agreed ordered fee vector with signed native
amounts and individual source/rate provenance, asset-aware reservations and matching
FIFO cash/inventory postings. This checkpoint does not fabricate that vector or
silently net unrelated fee legs.

Rebates remain refused. A signed negative number alone cannot prove whether a native
rebate credits quote cash, base inventory or a third asset, its effective time,
source ledger identity or allocation to a trade. Native signed rebate evidence and
the matching cash/inventory/basis contract are needed before replacing the current
nonnegative fee DTO; no actual venue rebate capability is inferred here.

Late earlier fills require append-only corrections and a versioned chronological
financial replay/projection contract. Posting a new earlier fill after already
realized FIFO disposals can change affected lots and proceeds; reordering or editing
immutable historical journal entries would destroy their provenance. Current
execution retains its explicit chronological-replay refusal and blocks readiness;
captured read-only sources remain available for protected review.

Permanent no-withdraw permission inventory, eligibility, native account identity,
authenticated account reconciliation, native order/cancel uncertainty, stops and
host/dependency acceptance remain separate external gates. Adapter method absence,
owner declarations and these synthetic tests do not prove them.

## Verification

The isolated-worktree suite uses the shared frozen environment with explicit source
imports and no credentials. It covers buy/sell and quote/base/identified-third-asset
fees, exact per-asset journal conservation, legacy/new JSON replay, partial
reservation and cold terminal recovery, sticky limit/reserve incidents, wrong native
legs, undeclared/off-quantum/oversized costs, no-attempt submission refusals and
zero-mutation precision failures. **387 tests** passed with zero failures, errors or
skips, including 29 dedicated native-principal regressions. Targeted Ruff, diff checks
and repository hygiene passed. JUnit evidence is private-free synthetic test output.

```sh
PYTHONPATH=/workspace/trade-graph-t19-takeover/src \
  /workspace/trade-graph/.venv/bin/python -m pytest -q \
  --basetemp=/tmp/t19-native-cost-final \
  --junitxml=/tmp/t19-native-cost-final.xml \
  tests/integration/test_native_quote_cost.py \
  tests/integration/test_kraken_live_adapter.py \
  tests/integration/test_kraken_identity_recovery.py \
  tests/integration/test_broker_identity.py \
  tests/integration/test_venue_conformance.py \
  tests/integration/test_execution.py \
  tests/integration/test_execution_acceptance.py \
  tests/integration/test_execution_reconciliation_ordering.py \
  tests/integration/test_submission_completion.py \
  tests/integration/test_ledger_store.py \
  tests/integration/test_runtime_evidence.py \
  tests/unit/test_books.py tests/unit/test_contracts.py tests/unit/test_protocols.py \
  tests/property/test_accounting_invariants.py
```
