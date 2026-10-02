# Protected process boundary and financial RPC

`trade_graph.kernel.process_boundary.ProtectedBoundaryHarness` now runs mutable
Python in a fresh Linux x86-64 process confined before its first untrusted byte
is parsed or compiled. Actual-host adversarial tests exercise this boundary with
synthetic protected state. `application.protected_runtime.ProtectedPaperRuntime`
now additionally composes the real durable paper ledger, authority, execution and
budget services in a trusted parent with a separate confined decision process.
This is a functioning opt-in T21 slice; the complete model gateway and departmental
graph have not been extracted, the intended deployment image/host has not been
verified, and the deployed Engineer retains its R1 artifact permissions.
`isolation.run_plugin` and staged application-code promotion remain disabled.

The trusted parent keeps its credential and owner-pinned policy in its own
address space. The child starts via a fresh interpreter exec with `-I -S -B`, a
scrubbed environment, closed inherited descriptors and an absolute trusted
worker path. It loads only trusted standard-library and pinned sandbox code
before confinement. Candidate source and public numeric observations enter by
stdin after the boundary is installed. No production keys, owner budgets,
ledger objects, authenticated transports or deployment capabilities enter it.

The reused independent-checker sandbox installs an irreversible seccomp filter
with `no_new_privs`, clears capabilities, ensures a non-root identity and closes
descriptors above 2. It permits reading fd 0, writing fds 1/2, bounded private
memory operations, self identity/time/signal handling and exit. Filesystem
open/stat/mutation, sockets, fork/clone/exec, process signalling, ptrace,
process-vm access, mount/namespaces, privilege/limit changes, new seccomp/BPF
programs, io_uring and access to other descriptors are denied. Linux x86-64 is
required; unsupported confinement fails closed. There is no AST security claim.
Arbitrary Python and ctypes/syscall probes execute behind this OS boundary.

Kernel limits remain 128 MiB address space, three CPU seconds, zero filesystem
output, no process creation, 524,288 input bytes and 16,384 output bytes. The
protected parent additionally bounds candidate source to 65,536 bytes and
enforces a policy deadline of at most ten seconds. Closing stdout/stderr does
not bypass the deadline: the parent kills and reaps a process which outlives its
pipes. The controller handles malformed/deep JSON, invalid numeric strings,
scientific-exponent expansion, excessive features and output flooding without
using candidate code to recover.

The owner fixes a `BoundaryPolicy` and the fingerprints of policy,
kernel/controller/worker/sandbox/runner sources and interpreter. Fingerprints are
checked before and after source evaluation. These pin values must come from a
trusted owner manifest outside the mutable process; computing a new pin from a
candidate-controlled checkout is not owner approval. The scaffold detects file
changes, but it does not establish a complete immutable interpreter image,
dependency supply chain, race-free release distribution or production host
mount ownership. Those remain release-controller work.

Only two scoped JSON RPC operations exist: `read_snapshot` for the current
public numeric snapshot and `propose_features` for the exact owner-allowlisted
feature names. The snapshot content digest prevents proposals against different
observations. Snapshot/feature values are bounded fixed-point Decimal strings;
binary floats, nonfinite numbers, excessive precision and huge scientific
exponents are rejected before expansion. Duplicate JSON fields and stale
snapshot digests fail. The parent validates every response independently. A
child-supplied `passed`, `approved`, `set_budget`, `activate`, `withdraw`,
`enable_live` or similar field/operation has no route to protected state.
Features are proposals, and no operation executes or authorizes an order.

`deterministic_fallback` returns an explicit `mutable_unavailable` diagnostic
with protected zero-valued features after candidate failure. It uses neither the
candidate nor a model. This demonstrates surviving controller behavior without
claiming that an actual application/version rollback or portfolio restoration
has been implemented. The scaffold holds no production ledger and restores no
financial database.

The actual-host tests verify 30 blocked file/descriptor/network/process/syscall
attempts, parent environment/memory and world-readable/writable sibling-account
files, inherited handles, worker import shadowing, privileged RPC attempts,
invalid/deep output, independent process identity, CPU/memory/wall/output limits
and controller survival. Protected fixtures remain unchanged. These are host
tests against synthetic state, not real credentialed execution or evidence that
the production deployment host/image has passed A39/A40.

## Durable paper financial service

`ProtectedFinancialService` holds the actual `Ledger`, `AuthorityRecord`,
`BudgetGateway` and paper `Execution`. The confined candidate receives public
fixed-point quotes, cash and position quantity, an opaque snapshot digest, release
identity, expiry and a one-use capability. It receives no private journal, account
identifier, owner budget document, database handle, exchange/model transport or
signing secret. The only business operation is `submit_decision`: a bounded
`enter`, `exit` or `hold` proposal. There is no route for owner writes, arbitrary
tools, withdrawal, direct ledger edits, paid invocation or live switching.

The parent generates all `Decision` identity, timestamp, portfolio, mode, policy,
mandate, snapshot and release fields. The existing real execution validator checks
funds, native precision, minima, fees, exposure, pause and quote age. The RPC result,
decision, intent, position reservation and outbox commit in one database transaction.
A failed RPC journal write rolls back every new financial effect. The external
submission runs afterward from its durable protected outbox. An exact authenticated
replay returns the previous result; a different proposal cannot reuse that capability.
This is local idempotency, not an exactly-once exchange guarantee.

The private parent key authenticates capabilities with HMAC-SHA256. Each scope
binds operation, instance, portfolio, source/build digest, protected manifest,
active release/generation, actual policy and mandate content, expiry and a financial
snapshot digest. The digest covers portfolio events, reservations, order state,
pause, execution venue/account/mode/fee configuration, instruments, point-in-time
quotes across the owner universe, valuation marks and FX. Any change invalidates
unapplied work. The worker's response must match its newly issued capability;
prior applied replies cannot masquerade as current-cycle success. Scope issuance
is durable before launching the worker, and activation/recovery revokes stale scopes.

Financial history hashing streams rows with a 10,000-row/8 MiB bound. Exceeding it
refuses new mutable decisions while protected management remains available. A
production long-history deployment needs a protected incremental checkpoint design;
this bounded slice does not silently omit older financial events.

## Pinned controller and independent management

`ProtectedRuntimeManifest` comes from an owner-approved protected deployment.
It pins all installed application Python sources, packaged default artifacts and
the interpreter, operation/resource bounds, deployment identity and exact approved
mutable source hashes. A source hash calculated in a candidate-controlled checkout
does not constitute approval. Admission rejects any unpinned source and copies the
approved source into the private protected database. Release IDs are immutable;
the parent derives the decision-contract build digest itself. This digest identifies
source/contract content, not a complete dependency/OS build attestation. Class grants
and T22 staged plugin receipts remain separate gates.

The fresh `-I -S -B` decision worker uses the same irreversible Linux x86-64 seccomp
confinement as the numeric harness, before parsing source or context. It cannot
read files, open sockets or recover through a candidate hook. The parent checks
its package/interpreter pin before and after execution. Source pins do not establish
race-free immutable distribution, dependency image pinning, host mount ownership
or an approved production platform.

`ProtectedPaperRuntime.cycle` holds an exclusive OS lock on the financial database
inode, preventing another controller or normal `PaperService` from operating it.
It reconciles, advances the persisted owner pause and drains existing protected
outbox work before new reasoning. Mutable evaluation runs on a bounded worker
thread with a separate database connection. Trusted management repeats every
250 ms while that process runs. `ingest_observation`, reconciliation, pause management
and external dispatch share one execution lock; callers must use these guarded
methods rather than concurrent direct `Execution` calls. Cancellation suppresses
the pending child reply and drains the bounded process before releasing controller
ownership, including repeated cancellation.

After a candidate fails, the independent controller atomically switches to the
previous owner-approved release with a successful protected business receipt, or
records `MANAGE_ONLY`. The next cycle uses that release; the failed cycle does not
invent a deliberate hold. Failure recovery compares the original release/generation
and preserves newer concurrent owner activation. Restart revokes interrupted scopes,
reconciles uncertain orders and resumes already-journaled outbox work. None of these
operations restores, deletes or rewrites ledger events, fills, receipts or positions.
Owner cancel/flatten remains available without candidate/model execution.

The integration tests exercise actual durable paper decisions, intent/reservation
atomicity, real simulated fills, restart with an undispatched intent, lost
acknowledgement without resubmission, financial-history-preserving rollback, owner
cancel/flatten, stale authority/account/FX/valuation/version scopes, host file/network
attacks, management during timeout, newer activation and cancellation. Credentials
and capital in these tests are synthetic; they do not certify a production host,
funded observation, private venue access or broader deployed Engineer authority.

To finish T21, integrate the complete protected model/budget gateway and departmental
mutable graph into this topology, pin immutable deployment/dependency images and
owner mounts outside candidate mutation, and verify independent recovery/rollback
on the intended deployment host.
Repeat capability, resource, egress and host-mount attacks on the intended
deployment platform. T22 additionally requires T18 economic evidence, an explicit
owner class grant, pure-plugin determinism/replay tests, immutable staging and
compatible migration/deployment/rollback evidence before production code
authority expands.
