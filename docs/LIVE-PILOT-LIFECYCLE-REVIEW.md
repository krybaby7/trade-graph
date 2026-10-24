# T20 durable lifecycle and closed-gate dispatch — 2026-10-04

T20 remains blocked. `live_pilot.py` implements persisted lifecycle preparation
and deterministic conservative envelope enforcement. `Execution` adopts its
protocol for live increases, while the current gate remains closed. No protected
live service, signer, owner-policy writer, external request or readiness bypass
is added.

The root-owned migration 0013 stores immutable-ID scoped authorizations,
per-intent one-use effects, generations and append-only management events.
Preparing a pinned signed bundle produces PENDING. Activation calls the existing
closed current-source evaluator and records refusal. Reserving or beginning an
increase also calls that evaluator: even a synthetic historical ACTIVE record
cannot pass today's missing prerequisites.

Execution binds the exact lifecycle implementation, database, clock, account and
immutable authorization. It checks the concrete current grant before native
intent/reservation/outbox creation and atomically reserves the pilot effect with
those writes. Dispatch refuses before capabilities when no owned reduction is
eligible, then rechecks before submission. Beginning the pilot effect and linking
its real persisted native submit attempt share one writer transaction before the
external effect. The controller reads authoritative native intent and reservation
state, binds the exact account/instrument/policy/version/request/generation and
requires a bounded limit acquisition. Cumulative
worst-case acquisition costs, including quote fee reserves, never recycle through
sales and cannot exceed either separate owner allocation or full-loss envelope.
This is conservative pilot sizing, not a guaranteed price stop. Actual deployment
expenses from every role and unresolved usage remain charged; synthetic costs do
not consume the owner's operating allowance.

Stop/revocation persist owner management latches while preserving every effect
hold and every existing non-running owner pause's current semantics. Recovery
survives database reopen, closes increase authority and leaves
unknown outcomes reserved. Reconciliation uses durable native order/fill state,
not caller outcome flags. A rejected/cancelled unfilled order releases only with
an explicitly scoped owned-history observation strictly after the latest native
effect/order/attempt/fill/ledger change. Same-clock or bare complete labels are
refused. A committed acquisition retains its full-loss charge permanently, even
through later uncertainty and cancellation. A late fill cannot remain released.
Stop completion refuses nonflat books, unknown effects or outstanding orders, and
requires actual retained authenticated whole-account/protected-ledger evidence.
The current venue collector lacks that proof, so flat local books and a caller's
full-account label do not complete a stop. Replacement grants cannot
reclassify unresolved revoked-account exposure as a fresh allocation, including
exposure held in another portfolio on the same account.

Execution preserves unseen gate-blocked submissions as pending. Startup recovers
the grant before native reconciliation, and actual native outcomes synchronize
pilot holds conservatively. Missing/corrupt authority or out-of-scope pilot effects
record bounded fixed-code incidents while independent owned-intent reconciliation,
cancellation and reduction management continue. Database/ledger faults remain
failures and cannot become fabricated financial success.

Validation uses `/workspace/trade-graph/.venv/bin/python` with this worktree's
`src` first in PYTHONPATH. The combined lifecycle/readiness/upstream/API suite
passed 160 tests, including 71 lifecycle cases before dispatch adoption. These exercise real SQLite
migration/state/reopen, exact Decimal edges, scope/version/reservation drift,
source/expiry refusal, stop/revocation/recovery, terminal account-history release,
late-fill handling, flat-state verification, expense separation, retained quota
and append-only audit. ACTIVE test rows are explicitly synthetic retained states;
activation remains false. No venue/provider request or production grant occurred.

The final dispatch/lifecycle/execution/reconciliation/adapter/readiness/upstream/
protected-runtime/paper-service suite passed 383 tests, including 28 dispatch and
71 lifecycle cases. New regressions cover refusal before financial writes or
capability/transport calls, native attempt identity, exact execution scope,
unknown recovery, mixed buy/reduction management, malformed/missing authority,
source drift/expiry, out-of-scope effects, cancellation continuity and honest
database failure. Ruff and diff checks pass. Scripted brokers supply all order and
history responses; no readiness result is promoted to true in these tests.

Root and T18 independent review found and corrected three management issues:
remember acknowledged monetary commitment through uncertain native order states;
preserve every current owner pause; and require post-effect scoped reconciliation
with concrete account evidence for full-account completion. The review added
regressions for these boundaries. T18 independently reran 70 lifecycle cases before the final observation timestamp binding test.
Own-effect history also requires observed_at to equal the durable event timestamp;
republishing an older observation under a later event cannot release an effect.

T18 independently read the closed-gate execution adoption and found no concrete
dispatch flaw. The review identified a future source prerequisite for a collocated
paper/live database: exact current runtime captures become stale when required
live intent/reservation/attempt facts are written to the captured source. A
separate frozen paper database does not inherently stale on live-database writes.
A shared-database design must separate immutable historical forward economics
from independently verified current live operational/account scope; sealed forward
inventory cannot be rewritten per order. Genuine authenticated economics and live
scope mapping remain missing globally. Current literal external provenance
refusal continues to keep the route closed.

Outstanding prerequisites are T17's funded operation/host evidence, T18's future
actual authenticated complete economics and exact paper/live account/instrument/
policy mapping, T19 eligibility/permissions/protection/conformance, separate owner
allocation/loss/spending authority, actual intended-host protected isolation,
protected live service commissioning and a measured pilot with a
continuation-or-stop review. The controller cannot establish these facts from a
declaration or test.
