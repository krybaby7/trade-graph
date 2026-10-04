# Bounded persisted paper producer and historical economic verification

This checkpoint implements a protected four-arm collection interface and separates
immutable historical economics from current operational state. All network,
provider and exchange inputs in its tests are synthetic. No paid/private/live
operation, actual future trial or intended-host authorization was performed.

## Implemented contracts

- `PaperForwardProducer` binds a predeclared trial to four distinct actual paper
  portfolios, exact active version/artifacts, equal initial EUR capital, market
  stream, symbols, database inode, controller and bounded sampling policy. It
  reads persisted SQLite/ledger/Execution facts; callers cannot supply returns,
  fees, risk measures, observations or authentication booleans.
- Source/checkpoint/block records are append-only, private and authenticated.
  Complete source inventories are compressed losslessly and checked against all
  prior trials. Sources have count/stored/expanded-byte bounds before payload
  reads and bounded decoding. A failed whole-history admission rolls back new
  objects and records. Cash cannot trade and buy-and-hold permits one acquisition
  intent without sales; broader baseline policy execution review remains pending.
- Frozen source reconstruction independently derives native equity, flows,
  embedded expenses, charges/rebates, turnover, midpoint slippage, sampled
  exposure/drawdown and point-in-time registered decisions. Gross fee charges
  and rebates appear separately, already in equity. Fee sensitivity increases
  charges while retaining the original rebates. Ratios have explicit 18-place
  rounding; money is refused rather than rounded to fit an export.
- Publication retains the source receipt before registry observation import.
  Exact-equality restart recovery cannot rerun a candidate/provider/trade or
  replace an earlier block. This is a recoverable two-stage handoff, not a claim
  that transactions in independent SQLite databases commit atomically.
- `verify_historical_snapshot` reconstructs the original report from exact frozen
  deployment-wide registry records, including negative trials and all original
  expenses/allocations/resolutions. Later source records require a new current
  report. `verify_historical` also links frozen imported expenses to their native
  runtime receipts and audits actual current financial consistency; it retains
  original mutable observations without pretending they are current state.
- Schema 2 extends the complete current runtime footprint to migrations 0013–0016:
  pilot authority, incident review/revocation, financial checkpoints, original
  native fee holds and model reservation/settlement origins. Original identities
  and immutable financial commitments enter continuity. Schema 1 retains its
  original historical contract and requires new preregistration for schema-2
  collection. Inventory is separate from cryptographic authority validation.
- An optional exact producer plus owner-pinned paper/live mapping verifies every
  block, complete historical economics and current registry/cost inventory
  separately. Legitimate future ops writes cannot fabricate unchanged current
  captures; new omitted expenses still refuse eligibility. Ordinary upstream
  collection retains its strict current-source behavior.

## Evidence and limits

The earlier source slice passed **201 tests**, zero failures/errors/skips,
across producer/history and existing runtime/archive/forward-report/snapshot/live
upstream paths (`/tmp/trade-graph-r4-t18-final.xml`). That result precedes the final
native replay/fee cross-contract integration and independent expense-link review
fix. The final expanded run is recorded by the integration owner after it finishes.

These tests use real local `Execution`, `PaperBroker`, `Ledger`, `TrialRegistry`,
retained source objects and restart verification with synthetic market facts.
They test source corruption, exact scope/pins, native fees/rebates and late-fill
correction receipts, capacity rollback and mislinked expense imports. They do not
establish actual provider invoices, official/native data authentication, a future
untouched horizon, independence/regimes/useful-decision coverage, profitability,
actual intended-host key custody or an owner-selected live account.

`collector_origin_verified` describes authenticated derivation receipts under
independently protected key custody. Intended-host authorization and external
authentication remain false; supplying one's own key or controller hash is not
an owner grant. The default runtime does not automatically create a protocol,
schedule sampling or dispatch baseline trades.

The complete source remains capped at **8 MiB / 20,000 rows**, regardless of
compressed history. Long deployments still need an economic source retention
design; the financial gateway's incremental checkpoints do not automatically
constitute complete long-horizon four-arm economic exports. A real horizon must
be selected with these finite capacities in view, with no financial/input pruning
or truncated export treated as complete evidence.
