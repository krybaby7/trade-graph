# Protected native execution incident review

Native execution-limit discrepancies preserve the actual fill and its financial
effects. An acknowledgement reviews that retained discrepancy; it does not erase
the fill, change reservations, release an unknown pilot effect, restore RUNNING,
replace a pilot grant or enable live trading.

The protected service can configure `ProtectedNativeIncidentResolver` with its
exact `LivePilotScope`, `PinnedVenueObservation` and a separate protected receipt
key. The default application does not configure it. Models, department RPCs and
Engineer artifacts have no resolution capability.

The existing owner HTTP session, role and CSRF checks protect three routes:

- `GET /api/v1/owner/native-incidents` provides incident and financial CAS pins.
- `POST /api/v1/owner/resolve-native-incident` requires a request ID, current owner
  revision, exact incident and financial digests, incident generation and the
  typed `accept_preserved_native_effects_after_review` acknowledgement.
- `POST /api/v1/owner/revoke-native-incident-resolution` permanently revokes one
  receipt, with the same owner revision and idempotency contract.

All review work is retained-source verification and SQLite work. The owner
command, acknowledgement, signature and completed response share one writer
commit. A process death before commit rolls back everything; a death after
commit leaves a replayable exact receipt. A request ID cannot be reused for
different prose, pins or acknowledgement. A newer generation fences a stale
review. Revocation never deletes the original record.

Before retaining an acknowledgement, the controller replays the exact pinned
native captures without network calls. They must contain the complete supported
observation stage sequence, match the entire protected live scope, use current
adapter/controller sources and postdate every local native financial change and
the incident. Native instrument, balances, absence of open/held orders and the
complete supported trade history must agree with current protected facts.

The local check reconstructs Books, compares every journal transaction group to
the reconstructed postings, compares all recorded fills to financial fill
events, checks the event sequence and exact account/portfolio/instrument
identities, and refuses unresolved order, reservation or pilot-effect holds.
Owner policy and selected system version must match their protected pins. A
balanced but differently posted journal cannot pass merely because its asset
sum is zero.

An actual owner may retain a synthetic or injected-transport preparation review
as `PENDING_NATIVE_PROOF`. Its `incident_cleared` and `execution_authority` are
false, and the native incident latch stays closed. Only the exact collector's
internally derived authenticated owned-HTTPS facts can produce
`VERIFIED_NATIVE_EFFECTS`. There is no caller success flag or injectable verifier.
Current checks have no authenticated private account evidence, so no actual live
incident clearance is claimed.

On restart, a clearance requires the original immutable receipt signature,
exact incident bytes, owner request/response and revision evidence, current full
financial digest, whole scope and current protected implementation fingerprints.
New fills, late replay corrections, altered journals, unknown orders or holds,
policy/version changes, source drift, receipt-key changes or explicit revocation
close it again. Resuming management and authorizing a live increase remain
separate protected operations.

Account balance agreement and a complete supported trade scan do not establish
all native deposits, withdrawals, transfers, adjustments, eligibility or account
ownership. Every review reports `complete_account_verified=false`.
`ProtectedPilotLifecycle.complete_stop` continues to require independently
authenticated complete account reconciliation; an incident review cannot satisfy
that gate.

The 2026-10-04 implementation ran 347 related cases, including 29 new incident
cases and 26 owner-mapping cases, with zero failures, errors or skips. The
incident cases execute real SQLite fill/accounting work, real session/CSRF
middleware, actual retained synthetic native normalization, and subprocess
death before/after commit. Historical authenticated clearance rows are explicitly
seeded mechanical test fixtures, not successful private venue observations.
Evidence is `/tmp/trade-graph-t20-next-controls.xml` on the implementation host.

The follow-up also binds every `native_fee_reservations` row, including original
allocation and plan identity, current amount and released history. A nonzero
held secondary fee amount, invalid bounds or scope/plan mismatch prevents review;
an added released row invalidates an earlier clearance digest. These checks do
not release any fee reservation.

After integrating the signed fee contract, append-only chronological fill replay
and schema 0015, 405 related cases passed with zero failures, errors or skips
(`/tmp/trade-graph-t20-next-fees-replay.xml`). The 35 incident cases include a
complete actual synthetic broker-history reconciliation that discovers an older
fill, appends its deferred source and replay receipt, preserves original journal
groups, records the balanced delta and verifies a fresh retained native capture.
The actual owner HTTP review remains `PENDING_NATIVE_PROOF` with the incident
blocked. The exact journal comparison was retained without relaxing it to a
simple zero-sum check.
