# Signed native fee components — 2026-10-04

This local continuation implements an additive financial contract, using synthetic
records only. It performs no network, private venue, paid-provider or order effect;
T19, native partial-fill reserve proof and concrete pilot readiness remain open.

`FillRecord.fee_components` is an optional ordered tuple of frozen `FillFeeRecord`
values. Each nonzero amount has its own native asset, signed debit/credit, distinct
source reference, timezone-aware effective time and optional positive identified
rate together with its rate-source reference. Positive amounts charge fees;
negative amounts credit rebates. Components currently must share the original
fill's effective time. Later fee corrections remain refused. When a vector is
present the legacy fee amount must be zero and its identified rate absent. When
absent the new field is omitted, preserving old single-fee durable JSON exactly.
`fee_legs()` adapts legacy fees with `provenance_kind=legacy_compatibility`; these
compatibility source/rate labels do not prove a native publisher or authenticated
valuation source.

Books validate exact native arithmetic at protected precision 28 independently of
ambient context and commit a copied book only after all cash/inventory/FIFO work
succeeds. Every fee leg has independent balanced native postings. Quote charges
and credits alter cash and acquisition basis/disposal proceeds. Base charges alter
received inventory or disposed quantity. A base rebate on a sale creates a new
source-attributed lot at the fill price and credits that identified value to sale
proceeds. Individually valued third-asset charges dispose existing FIFO inventory;
rebates acquire a new attributed lot. Buy basis includes signed third-asset value;
sell proceeds subtract that signed fee value. Those basis allocations do not debit
quote cash again. Missing rates/inventory, overflowing native amounts and negative
acquisition basis leave the original book unchanged.

Execution consumes positive per-asset fee debits and the original principal or sale
quantity from each retained reservation. Rebates do not replenish exposure holds.
Original principal/reservation discrepancies retain the executed financial facts
and a sticky incident. Unexpected third-asset fee charges without an original
reservation also remain incidents. This does not yet create a venue-authorized
multi-asset reservation or future native partial-fill count/granularity proof.

Kraken normalization retains every distinct linked base/quote fee source, including
signed credits. Such vectors require exact native ledger effective times matching
the trade and an independently consistent aggregate quote fee amount. Existing
positive single-fee records retain their prior representation. Multiple native
third-asset fees remain refused because an aggregate quoted fee cannot establish
an individual valuation rate for each asset. The signed ledger semantics are tested
synthetically; no actual Kraken rebate support or permission is asserted.

Read-only fee projections iterate each signed component independently, including
its source and valuation reference, rather than charging the legacy zero again.
Historical original records remain readable. Source-pinned observations taken
against an earlier implementation become stale under the existing source checks.

Verification: **378 tests passed**, zero failures/errors/skips, including 31 dedicated
component cases and legacy native principal, adapter/identity, execution/replay,
ledger, dashboard, retained runtime evidence, contract and generated conservation
checks. Evidence: `/tmp/t19-r4-fees-reviewed.xml`. Targeted Ruff passes. The first
broader run found one older refusal-message expectation; explicit missing native
fee effective-time refusal now retains the expected rebate diagnostic.
