# Bounded append-only native fill replay — 2026-10-04

This local continuation uses synthetic account, transport and financial facts. It
performs no paid/private venue request or order effect and grants no live authority.

A complete owned history scan may now retain a strictly earlier discovered fill by
appending its original `kind=fill` event with
`projection_deferred=chronological_replay_v1` and an immediate same-transaction
`kind=fill_chronological_replay` receipt. The receipt pins the exact complete original
source prefix, previous/current full financial projection hashes, the new fill event
ID and exact balanced native adjustment postings. Original events, journal groups,
fill documents, receipts, source/version identities and snapshots remain unchanged.
There is no caller-selected ordering list. Protected code computes the chronological
projection from every original cashflow/expense and fill, using native fill times
and trusted nonfill event times, with original sequence as a stable existing tie.

Cash, lots, FIFO disposals, basis, flows and expenses are reconstructed together.
The book preserves original journal groups and appends one balanced per-account,
per-asset delta group, enabling independent stored-journal comparison. Basis changes
are recorded by the linked previous/current complete projection hashes. A newly
visible fill can change the lots consumed by a later sale while retaining that
sale's source, price and proceeds. `Ledger.books(at=...)` uses record-time availability;
reads before the correction retain the original known-fact view. Current reads and
cold restart verify the complete receipt before deriving the restated book.

`Ledger._replay_row(books,row,prior_rows)` is the protected per-event audit interface.
It independently checks original-source hashes, contiguous scoped source sequence,
record-time availability, previous/current projections and exact journal delta.
`_replay_rows` additionally refuses dangling deferred facts. Calling `_mutate` alone
cannot interpret a correction. Retained economic audits must use the per-row prefix
interface; their integration belongs to the economic-producer workstream.

The correction requires a complete prefix of at most 4,096 events and 8 MiB of
source payloads. A new execution tie without finer retained ordering evidence,
future fill, regressed record clock, impossible inventory/cashflow chronology,
precision overflow or altered receipt is refused atomically. Long histories beyond
these bounds, late fee-only adjustments and finer native timestamp ties remain open
contracts. Imported opening funds recorded after actual historical trades cannot
invent earlier funding. Default direct `record_fill` still refuses late chronology;
the complete protected reconciliation scan invokes the bounded replay path.

A follow-up fee check records previously unreserved quote cash drawn when a sale's
quote charge exceeds its proceeds. Execution compares gross per-asset debits, so
rebates do not replenish holds. Extra asset declarations in mutable intent payloads
cannot fabricate a secondary reservation. Actual facts remain posted and the
sticky incident blocks new increases. Asset-aware secondary holds need the separately
planned additive reservation table and protected paper reserve helper.

Verification: **377 tests passed**, zero failures/errors/skips, including **18**
dedicated replay cases, **33** signed fee cases, actual Execution history/restart,
legacy ledger/adapter/conformance/dashboard and retained runtime evidence tests.
Evidence: `/tmp/t19-r4-replay-reviewed.xml`. Scoped Ruff and staged repository hygiene
pass. Failure injection aborts the real SQLite journal write and verifies that both
new original and correction roll back. No financial event is rewritten to pass it.
