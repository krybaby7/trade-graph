# Trusted synthetic application scheduling and recovery

`application.application_preparation.ApplicationPreparationLoop` is an explicit
opt-in trusted preparation loop for one commissioned
`secretary_digest_projection` change in one paper portfolio whose experiment is
`offline-application-preparation`. Default runtime assembly, CLI/model tools,
ordinary R1 activation and production permission do not acquire this route.
The fixed [commissioned Engineer/Gateway flow](APPLICATION-COMMISSIONS.md) supplies
all generated source and synthetic model cost facts.

Each awaited `advance()` reconciles existing protected paper financial state,
then checks independent policy/controller/environment pins and the exact
synthetic scope. The controller claims at most one existing Engineer task lease
in that portfolio, under a separately fenced process lease. Competing Engineer
work refuses the route before task dispatch. Calls do not schedule an unlimited
worker loop, accept model activation commands, install an owner policy, create
price cards or replenish budgets. The gateway remains scripted, without keys or
paid calls. Lost model dispatch persists an unresolved usage hold and produces
no activation or replacement request.

A successfully completed job must provide its exact authenticated READY
candidate and full retained usage/reservation/allocation facts. An actual
validated v1 projection effect must back the exact baseline. The trusted parent
performs the fixed compatible sidecar expansion and v2 pointer activation;
source never receives SQL, migrations or a database handle. An authenticated
pre-activation state binds the exact candidate/commission receipt/build and
expected generation. Restart recovery recognizes that exact persisted pointer,
or resumes the unapplied activation. It does not issue model requests or repeat
retained validation.

An independent parent health corpus selects at most eight finite inputs and
expected outputs. Inputs must belong to the already approved synthetic
projection corpus; baseline expectations must agree. Candidate output and
model feedback cannot replace this private corpus or its release policy pin.
Separate v2 health expectations can reject a candidate that passed development
validation. This is bounded functional preparation, not untouched economic
proof or general determinism.

Before each actual confined health effect, a separate private
`projection-workflow.sqlite` journal commits an authenticated STARTED intent.
At most one health case runs per call. Completed samples bind the actual durable
projection record and receipt, input, generation, exact candidate and full
commission/cost receipt. After a crash, an unfinished sample causes deterministic
code rollback; the candidate is not rerun and the sample cannot become success
because its result was lost. An effect completed before the crash remains in the
application sidecar. A fixed maximum deadline bounds the health sequence.
Completion receipts include the trusted journal checkpoint time. Restart
promotes an exactly complete verified set of on-time samples to ACTIVE even if
the final ACTIVE checkpoint was interrupted and recovery occurs after the
deadline. Late completions or samples without authenticated completion time
cause rollback; no restart timestamp is substituted for missing evidence.

Every invocation retains its exact process owner and lease expiry. Short parent
database writer transactions serialize lease admission with authenticated
workflow/sample receipt compare-and-swap updates and projection expansion,
activation or rollback. STARTED inserts never overwrite existing samples;
completion can update only its exact retained STARTED receipt. The parent writer
lock is released before any model or confined child executes. Trusted render
guards recheck the current lease, intent, source/cost admission and generation
before child execution and again before application record writes or failure
rollback. An expired invocation observes current authenticated progress without
rewriting samples or rolling back a replacement owner's release. A STARTED
intent interrupted by takeover still causes rollback and is never rerun.

ACTIVE derives from independently matched actual confined effects for the exact
finite case set. Restart maintenance reauthenticates samples, actual application
records, source and complete cost facts without repeating completed candidate
work. Source/cost drift, child failure or independent health rejection invokes
the existing effect-backed source rollback. Application records, financial
ledger, fills, intents, usage expenses and operating holds remain present. A
newer pointer is preserved; damaged baseline evidence leaves management-only
state. Financial reconciliation runs before application health admission and
continues on terminal application failure.

The workflow journal has two fixed tables and an 8 MiB bound, private 0600 mode,
no symlink/hard-link substitution, and parent-only authenticated records. It is
separate from the projection's fixed four-table sidecar and protected financial
schema. Its mutable parent storage assumes the already documented trusted
private release/store premise; it does not protect against the host owner or
establish production image/profile admission. Concrete paper labels and a
synthetic database are fixture requirements, not proof that arbitrary input has
synthetic provenance.

```python
# Trusted fixture/release preparation supplies concrete objects, independently
# selected frozen policy/pins, private keys, and a fresh process owner.
loop = ApplicationPreparationLoop(engineer, handler, policy=policy,
    expected_policy_sha256=policy_pin, expected_controller_sha256=controller_pin,
    receipt_key=private_workflow_key, owner=process_owner, execution=paper_execution)
result = await loop.advance()
```

Real model routes, intended-host isolation/admission, authoritative dependency
verification, funded economics and explicit owner class/production grants remain
separate prerequisites. The selected class exposes pure JSON presentation only.

The 2026-10-04 scoped run passed 14 actual tests in 131.40 seconds. It exercised
real commissioned Gateway generation and actual confined health effects,
independent health mismatch, a nonce-triggered confined failure after a real
synthetic paper fill, compatible sidecar activation, pointer-checkpoint recovery,
interrupted health before/after its effect, source/full-usage/allocation drift,
wrong scope/key/authentication, competing process/task leases and unknown or
failed generation costs retained without activation, refund or redispatch.

The final combined projection/commission/preparation run passed 55 tests in
313.61 seconds, including the existing 26 projection/migration tests, 13
commission tests and 16 preparation tests. The additional preparation cases ran
two independently selected finite inputs one per step, preserved completed health
without repetition, and refused a new health effect after its deadline while
retaining the generated source cost and restoring the effect-backed baseline.

The subsequent lease-takeover correction passed all 61 combined cases in
425.259 seconds with zero failures, errors or skips. Six added regressions pause
an expired owner before its activation checkpoint, health intent, attempted
rollback, child execution, application-record commit, or health completion.
They verify that a replacement's completed ACTIVE release is neither repeated
nor rolled back by the old owner, and that takeover of a retained STARTED intent
rolls back without rerunning the candidate or accepting a late result. Actual
effects finished before interruption remain retained. The independent review's
original stale-owner reproducer supplied the regression scenario.

After the final completion-checkpoint correction, the frozen source passed all
65 combined cases in 497.772 seconds, with zero failures, errors or skips.
Four added cases exercise on-time completion recovery after the deadline, a
late completion checkpoint interrupted before rollback, and changed cost
allocation exactly at the normal/recovered ACTIVE checkpoint. Current source,
full cost binding and generation are re-admitted inside the fenced ACTIVE
writer transaction. Independent review separately passed 13 targeted cases in
180.329 seconds, including both originally reproduced crash/takeover gaps.
