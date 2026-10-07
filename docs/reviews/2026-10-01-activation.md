# T14 — verified artifact consumption and deterministic recovery

Starting checkpoint: `51e48b6`, on `cursor/trade-graph-r1-548a`.
Scope: registered R1 data artifacts and permitted prompts, using the protected application controller.
Paid calls, real orders and broader executable engineering remain disabled.

## Changed behavior

Activation now stores the actual independently tested files and their unambiguous manifest.
The loader reads these immutable database bundles, never the Engineer's mutable staging directory.
The baseline must also have real, grammar-valid registered bytes; a legacy version label alone
cannot support activation or claim a successful reload. The initial trusted bootstrap and Engineer
baseline staging register the original data. Follow-on Engineer commissions use the activated
bundle as their source, rather than reverting to the original repository artifacts.

The controller retains candidate/commission/attestation identity, baseline compare-and-set and
Trader quiescence checks. It also rechecks the exact tested bytes, manifest, installed harness,
checker-input digest and pinned Linux confinement facts. Activation increments a monotonic
generation and records a durable `RESTART_PENDING` rollout. Queued incompatible Trader work is
invalidated; reconciliation, protection and financial state are not reset.

The runtime rebuilds the context policy, registered role prompts and non-executable strategy
guidance from those bytes. Registered report sections and artifact-owned schedule intervals are
consumed by software. Schedule artifacts cannot change protection cadence or allocate funds;
trusted routing supplies the bounded monetary allocation. Unrelated owner/Leader schedules survive.
New requests include the encoded role prompt in their conservative input reservation bound.

Reload reconciles execution before acknowledging the exact hash, manifest and generation.
Provider dispatch and decision/order effects independently fence that identity and the worker lease.
The gateway-backed Trader's request, snapshot, receipt and persisted Decision share loaded-version
provenance. An old worker or an ABA return to the same bytes cannot pass a generation check.
Durable model responses and committed decisions recover without another paid attempt.
Unknown dispatch/billing remains unresolved and cannot create a fresh Trader decision.

## Observation and rollback policy

The protected controller pins typed policy at activation: by default three successful decisions,
zero failed observations, a one-hour observation deadline, a 60-second reload deadline and a
262,144-byte request-context ceiling. These are functional gates, not a profitability test.
Candidate task prose and model output cannot select weaker criteria.

Success requires an actual scoped persisted Decision, its loaded snapshot and corresponding
gateway invocation. Required context must contain the real pinned mandate and policy/pause objects.
Failure observations require retained scoped failed Trader work and the same loaded generation.
Context bytes come from the stored request; token counts and synthetic/uncertain receipt labels
come from durable provider usage. Missing usage is unknown. Anonymous samples, obsolete generations
and claims of success without evidence are rejected. Samples are idempotent and cannot cancel a
pending rollback. Task completion and observation commit in one writer transaction.

Failure or timeout requests rollback independently of a model. A busy Trader keeps rollback pending
and blocks fresh decisions until the boundary is quiet. The controller restores only the recorded
verified predecessor, increments generation and records `RESTORE_PENDING`. Runtime maintenance
reconciles and reloads it immediately, then acknowledges `RESTORED`. If predecessor bytes are corrupt
or reload cannot complete, mutable decisions remain blocked; reconciliation/protection and owner
pause authority are retained. Rollback never restores an old database or reissues an uncertain order.

## Verification evidence

The credential-free suite includes actual loaded-policy/prompt/strategy changes in gateway requests,
database reopen, generation fencing, stale/failed activation, report/schedule restoration, transactional
crash recovery, health/deadline rollback, corrupt bundles/predecessors, retained billing uncertainty,
and real paper unknown/partial-fill orders across rollback. Opening and closing decisions keep their
separate versions; intervening fills, fees, expenses, reservations and owner settings survive.

The offline CLI demonstrates commissioned Engineer generation and independent checks, Secretary/Leader
activation, real Trader context shrinking from eight lessons to five, a new process loading the same
five-lesson policy, healthy observation, then a charged invalid reply and automatic restoration of
the eight-lesson baseline. The later decision consumes those restored bytes. Four fills survive
restart with zero additional broker submissions. Exact final commands/counts are in IMPLEMENTATION-STATUS.md.

T14 does not complete T15 dashboard reconciliation, T16's full fault catalogue, T17's continuous
operating service or credentialed soak, forward economic evidence or T21 protected-host isolation.
Context/usage evidence does not establish realized economic improvement or strategy quality.
