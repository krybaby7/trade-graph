# Protected process boundary and business RPC

`trade_graph.kernel.process_boundary.ProtectedBoundaryHarness` now runs mutable
Python in a fresh Linux x86-64 process confined before its first untrusted byte
is parsed or compiled. Actual-host adversarial tests exercise this boundary with
synthetic protected state. `application.protected_runtime.ProtectedPaperRuntime`
now additionally composes the real durable paper ledger, authority, execution and
budget services in a trusted parent with separate confined decision and departmental
processes. All six durable department handlers can use the protected model gateway
and confined reasoning/result graph stages. This is a functioning opt-in T21 slice;
the intended deployment image/host has not been verified, and the deployed Engineer
retains its R1 artifact permissions.
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

The numeric harness has two scoped JSON RPC operations: `read_snapshot` for the current
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
candidate nor a model. This demonstrates surviving harness behavior. The harness
holds no production ledger and restores no financial database; the separate real
controller's durable release recovery is described below.

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
signing secret. The financial service's only business operation is `submit_decision`: a bounded
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

## Protected model gateway and departmental graph

`application.protected_departments.assemble_protected_handlers` composes the
existing research, trader, learning, optimisation, leader and Engineer handlers
with `ProtectedDepartmentGateway`. The immutable parent still selects task leases,
point-in-time context, model routes, original role schemas, token/tool limits,
price cards, FX provenance and priorities. It holds provider credentials, the real
`RuntimeGateway`, `InvocationJournal`, budget services, effect validators and
independent artifact checker. No model SDK or authenticated transport enters the
mutable process. Paid operation remains subject to the existing owner authority;
this composition grants none.

The owner manifest defaults to the financial-only operation tuple
`("submit_decision",)`. Departmental assembly requires the exact additional tuple
`("submit_decision", "invoke_model", "apply_role_result")`; other operation lists
are rejected. The admitted source must provide `graph(context)`. Each fresh
confined invocation receives a role-scoped context and can return only one of:

* `{"node": "invoke_model", "guidance": "bounded reasoning guidance"}`;
* `{"node": "apply_role_result", "payload": {"...": "original typed role result"}}`.

The owner-pinned operation tuple also selects the build-contract identity:
financial-only releases retain `decision-proposal-v1`, while the opt-in composition
uses `decision-and-departmental-graph-v1` and binds its exact operations. Source used
for both paths must implement both `propose(context)` and `graph(context)`; invoking
the financial path on graph-only source fails closed. The financial endpoint accepts
only a `submit_decision` capability, even when the owner allows departmental stages.

Guidance is limited to 2,000 UTF-8 bytes and conservatively increases the protected
input-token reservation estimate. The child cannot choose or override a provider,
card, schema, tool, token limit, task/invocation identifier, priority or budget.
The parent validates the transformed result against the original protected role
schema using a bounded local-only schema registry, then bounds native money and
quantity precision before typed parsing. Deterministic parent validators decide
whether a strategy, lesson, proposal, ChangeTask or artifact effect is admissible;
candidate assertions such as `passed` or `approved` confer no authority.

The child snapshot starts from an explicit public root-field allowlist, with
owner/billing/provider metadata removed recursively. JSON depth, node count,
collection lengths, total bytes and process output are bounded. Source and context
are parsed only after the same irreversible seccomp confinement used by the
financial worker. The child cannot open the database, use a network socket, inherit
the parent's environment keys or invoke a recovery hook.

Both graph stages use durable one-use capabilities in `protected_rpc_requests`.
Each capability binds the role/task/root/portfolio, persisted lease owner/token,
original request and snapshot, exact immutable model configuration and price card,
policy, mandate, active artifacts, pause, budget allocations, release/generation,
protected manifest, result digest where applicable and expiry. The private scope
retains the exact original request/billing identity for recovery; the child receives
neither that protected scope nor the signing secret. Exact replay returns the saved
stage result, while identifier reuse with changed request/billing/result is refused.
The child envelope must match the capability freshly issued to that stage.

Before provider dispatch the parent rechecks current authority, task lease, release,
budget guard and capability expiry. The real gateway journals the invocation and
reservation before external effects. A previously dispatched or uncertain invocation
is recovered through `InvocationJournal`, never sent to the provider again. Unknown
usage or a cancelled/interrupted invocation keeps its unresolved billing hold.
Known usage and cost receipts commit before checking whether a delayed result still
has application authority, so lease loss, cancellation or release change cannot
erase incurred cost. Known-result recovery permits a new valid task lease, checks
the original remaining bindings and reruns only an interrupted local result stage.
Engineer recovery binds the exact invocation in its current durable job, rather
than choosing an arbitrary latest task invocation.

A graph plan or schema-valid completion is insufficient to make a release a
rollback baseline. The parent adds a private successful-effect acknowledgment to
the completion receipt in the same transaction as the actual `SUCCEEDED` role
result and its effects. This includes Engineer terminal success after independent
artifact checks. The controller requires that acknowledgment to match the durable
role result, or a real financial `submit_decision` receipt, before choosing a prior
release for rollback. Child self-attestation cannot create the acknowledgment.

An actual confined process, framing, protocol or original-schema failure invokes
the independent release controller using the original release/generation. Recovery
also rechecks the original lease and authority/budget scope: a newer activation,
owner policy change or cancellation prevents a delayed failure from undoing it.
The controller chooses a previously successful effect-backed release or enters
`MANAGE_ONLY`, retaining task, receipt and financial history. The batch stops claiming
new tasks in `MANAGE_ONLY` while protected management proceeds. Model uncertainty,
semantic parent refusal and protected storage failure do not blame mutable source.

`ProtectedPaperRuntime.run_departments(worker, assembly, portfolio_id,
maximum_tasks=1)` runs a bounded batch of 1–32 durable role tasks through that
assembly, claiming only tasks in the portfolio whose pause and management it handles.
It holds controller ownership and uses a separate thread-local database
connection for the worker. Trusted reconciliation, pause management and protected
outbox dispatch repeat every 250 ms while a confined graph or model transport is
pending. Cancellation prevents further stages and drains pending side effects
before releasing ownership. Existing scheduler/context assembly, authority checks,
receipt recovery, typed effect application and independent Engineer checking remain
protected parent code; only mutable reasoning and result transformation run in the
confined graph. The legacy paper runtime remains the default.

Scripted integration tests run all six actual durable handlers through both confined
stages, with real synthetic invocation/usage receipts and zero paid transport calls.
They cover provider-control and result-self-attestation attempts, OS access denial,
native Decimal expansion/precision attacks, expired dispatch capabilities, uncertain
billing without redispatch, crash before effect application, new-lease restart,
release revocation after a model reply and cancellation during slow transport while
independent management continues. These are implementation and recovery tests, not
funded model evidence or deployment-host certification.

## Pinned controller and independent management

`ProtectedRuntimeManifest` comes from an owner-approved protected deployment.
It pins all installed application Python sources, packaged default artifacts and
the interpreter, operation/resource bounds, deployment identity and exact approved
mutable source hashes. A source hash calculated in a candidate-controlled checkout
does not constitute approval. Admission rejects any unpinned source and copies the
approved source into the private protected database. Release IDs are immutable;
the parent derives the operation/contract build digest itself. This digest identifies
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

The [immutable offline image workflow](PROTECTED-DEPLOYMENT.md) now pins a complete
OS/dependency image and archive, verifies exact owner mounts and Docker configuration,
and exercises independent recovery/rollback in actual local Docker containers.
Its fixed entrypoint boots the protected paper service. The image and root-owned
distribution still require owner approval and independent verification on the
intended deployment host. Installed-source/interpreter fingerprints and local
synthetic image tests do not satisfy that intended-host gate.
Repeat capability, resource, egress and host-mount attacks on the intended
deployment platform. T22 additionally requires T18 economic evidence, an explicit
owner class grant, pure-plugin determinism/replay tests, immutable staging and
compatible migration/deployment/rollback evidence before production code
authority expands.
