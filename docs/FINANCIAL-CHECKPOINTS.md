# Protected streaming financial continuity

Protected paper decisions retain complete financial commitments beyond the old
10,000-row / 8 MiB combined snapshot ceiling. The new collector streams every
retained source row; a checkpoint contains hashes, counts, current native cash
and inventory, owner policy and mandate, observations, instrument rules, account,
fee reserve, budget, allocations, usage receipts, costs and price-card identities.
It does not replace the ledger or send private history to mutable code.

Every scan verifies the entire previously authenticated ledger, journal,
transaction, fill, usage-receipt, cost-allocation, invoice and budget-origin
prefixes. Updated intents, reservations and observations are read afresh; immutable
reservation, invocation and transport identities remain committed. Known financial
facts with future record times refuse. Point-in-time market availability retains
its separate rules. Each native journal transaction must balance per
asset and link to the exact portfolio, ledger kind, external reference and event
time. Every normal financial source must produce exactly its persisted native
posting groups using the pinned Ledger posting helpers. Newly appended chronological
fill corrections must also pass complete prefix replay in a fixed confined trusted
worker, which independently validates both projection hashes and the native delta.
Previously authenticated corrections retain their complete source-manifest
commitment and exact adjustment postings. An unknown event kind, missing source,
dangling deferred fill or mismatched correction refuses new authority.

This is a bounded streaming scan and checkpoint generation. It does not claim
that verification takes only the time needed for newly appended rows. The scan
processes at most 1,000,000 rows, including repeated source/native verification passes,
and 512 MiB of exact encoded source data. A normal row is bounded to 64 KiB; the
known chronological correction record is bounded to 1 MiB. Current native assets
are bounded to 256 cash/inventory entries. Python checks and a SQLite progress
handler enforce a five-second scan deadline and cancellation. A complete scan
that exceeds any bound refuses; it never selects a recent tail or trusts a caller
cursor. No 512 MiB JSON document is assembled in parent memory.

The checkpoint proves native state and continuity, rather than providing another
FIFO cost-basis engine. The pinned `Ledger` remains the authority for accounting,
disposals, fee components, chronological corrections and exposure checks. The
first exact owner-source admission over a virgin controller audits the complete
existing native ledger against that authority before sealing it, with a 4,096-row
and 8 MiB financial-ledger bound. The same complete-prefix bound applies to new
correction verification; the trusted worker has a two-second parent deadline and
128 MiB memory limit, and receives no database handle, key or mutable source.
This bound applies to initial financial audit,
not accumulated marks or observations; a first deployment over financial history
larger than that requires separately reviewed preparation. The native late-fill
replay retains its own documented bounds. These limits are not solved by adding
credentials.

Exposure and owner-loss gates still use the pinned `Ledger.books` authority. This
checkpoint does not replace that full projection or make those existing gates
incremental; long normal-ledger projection feasibility remains separate from
the bounded streaming collector. New chronological corrections require the
complete bounded prefix described above.

Protected model dispatch seals the original reservation, controller, task and
portfolio scope before an outbound effect. Immutable parent-authenticated receipt
commitments retain the actual usage receipt, settled amount and exact expected
allocation. Original unresolved amounts cannot be reduced or released without a
matching retained settlement receipt. Deleting known costs, changing their task or
deployment, or dropping allocations refuses subsequent reservations and financial
authority. These checks cover all portfolios sharing the deployment's global
budget. First owner preparation refuses legacy uncertain/outbound holds that lack
pre-dispatch protected originals. Initial known settled history requires retained
matching receipts; dispatch never manufactures original authority from later state.

For every primary, repair and fallback provider attempt, the original reservation
and invocation writer commits first. A protected financial checkpoint then commits
and publishes its independent witness before any provider effect. Actual settlement
and exact allocations likewise commit and publish before a protected result is
acknowledged. Database rollback before the next financial decision therefore cannot
erase that dispatch hold or known cost. No writer transaction remains open during
the provider call. A publication failure retains the original hold or actual bill,
refuses acknowledgment/new dispatch, and requires protected recovery.

## Independent witness and restart

The private witness is a separate file beside the database, named
`<database-stem>.protected-financial-witness.json`. SQLite backup/restore operations
do not include it. A domain-separated parent HMAC binds each scope's latest
checkpoint generation and digest to the financial database device/inode identity.
The parent requires a private regular file, rejects symlinks, hardlinks, FIFOs,
public permissions and untrusted directory identities. Ancestors and leaves are
opened through no-follow directory/file handles. The parent uses a private
cross-process nonblocking lock. Publication uses a fresh exclusive file, fsync,
atomic rename and directory fsync. The in-process highwater refuses an observed
witness rollback, even when older bytes have a valid MAC.

The writer commits the checkpoint and one-use request before publishing the
witness, and returns a capability only after publication. A crash or filesystem
failure between those operations leaves no matching continuity proof and refuses
subsequent issuance. A stale decision computes current state without advancing
the witness. Initial preparation occurs only through exact owner-approved source
admission for a controller with generation zero and no active release; issue and
dispatch never recreate a missing witness. Later admission, restart or a restored
database cannot silently reset an existing witness. A missing or mismatched
witness makes runtime readiness false, while reconciliation and queued order
management continue.

Every recorded witness scope is checked against the database before any new scope
is considered. A new owner-approved manifest cannot reset finance. The explicit
[owner-approved offline transition](FINANCIAL-TRANSITION.md) binds exact old
approval documents, the original key, database identity, current witness and
scanned state before appending target checkpoints. Its schema-two witness retains
all old scopes and authenticated transition receipts. Old manifests remain
retired; ordinary admission, issue and dispatch never infer a transition or repair
a missing witness. Executable release changes within the same approved manifest
preserve financial history normally.

Restoring both the database and witness together, or rolling back the entire
host, is outside this local SQLite continuity proof. Intended-host admission
still requires an independently protected external rollback anchor and reviewed
backup/restore handling. Key rotation, deliberate database migration and recovery
from an interrupted checkpoint need protected owner procedures; mutable code has
no repair, reseal or reset route.

## Verification scope

Synthetic integration cases retain more than 12,000 marks and 8 MiB, verify exact
receipt reuse, change owner flows, preserve an actual fill across executable
rollback, and restore an older database while retaining its independent witness.
Negative cases cover committed prefix edits/deletes, new balanced but false native
postings/corrections, known paid-cost omissions, unresolved-budget resets,
cross-manifest rollback attempts, future financial facts, unknown kinds, orphaned and
unbalanced transactions, missing/tampered/private-file attacks, interruption,
deadline, row/byte bounds and cancellation. The installed image proof remains a
separate verification step on the final integrated source.
