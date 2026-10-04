# T20 durable lifecycle preparation — 2026-10-04

T20 remains blocked. `live_pilot.py` implements persisted lifecycle preparation
and deterministic conservative envelope enforcement, without a live dispatch,
signer, owner-policy writer, external request, or readiness bypass.

The root-owned migration 0013 stores immutable-ID scoped authorizations,
per-intent one-use effects, generations and append-only management events.
Preparing a pinned signed bundle produces PENDING. Activation calls the existing
closed current-source evaluator and records refusal. Reserving or beginning an
increase also calls that evaluator: even a synthetic historical ACTIVE record
cannot pass today's missing prerequisites.

The intended future dispatcher contract reads authoritative native intent and
reservation state before effects, binds the exact account/instrument/policy/
version/request/generation, and requires a bounded limit acquisition. Cumulative
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

Validation uses `/workspace/trade-graph/.venv/bin/python` with this worktree's
`src` first in PYTHONPATH. The combined lifecycle/readiness/upstream/API suite
passed 160 tests, including 71 lifecycle cases. These exercise real SQLite
migration/state/reopen, exact Decimal edges, scope/version/reservation drift,
source/expiry refusal, stop/revocation/recovery, terminal account-history release,
late-fill handling, flat-state verification, expense separation, retained quota
and append-only audit. ACTIVE test rows are explicitly synthetic retained states;
activation remains false. No venue/provider request or production grant occurred.

Root and T18 independent review found and corrected three management issues:
remember acknowledged monetary commitment through uncertain native order states;
preserve every current owner pause; and require post-effect scoped reconciliation
with concrete account evidence for full-account completion. The review added
regressions for these boundaries. T18 independently reran 70 lifecycle cases before the final observation timestamp binding test.
Own-effect history also requires observed_at to equal the durable event timestamp;
republishing an older observation under a later event cannot release an effect.

Outstanding prerequisites are T17's funded operation/host evidence, T18's future
actual authenticated complete economics and exact paper/live account/instrument/
policy mapping, T19 eligibility/permissions/protection/conformance, separate owner
allocation/loss/spending authority, actual intended-host protected isolation,
default-runtime dispatch adoption and a measured pilot with a continuation-or-stop
review. The controller cannot establish these facts from a declaration or test.
