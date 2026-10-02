# Protected process boundary scaffold

`trade_graph.kernel.process_boundary.ProtectedBoundaryHarness` now runs mutable
Python in a fresh Linux x86-64 process confined before its first untrusted byte
is parsed or compiled. Actual-host adversarial tests exercise this boundary with
synthetic protected state. This is preparatory T21 work. The production financial
kernel, gateway, controller and graph have not been extracted into this topology,
and the deployed Engineer still has its existing R1 artifact permissions.
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

To finish T21, extract the real accounting/execution/authority/budget gateway
into the protected service, bind authenticated scoped RPC to actual durable
business contracts, pin owner policy/controller/harness and deployment images
outside candidate mutation, define authenticated freshness/version envelopes
and test deterministic recovery/rollback of the production mutable process.
Repeat capability, resource, egress and host-mount attacks on the intended
deployment platform. T22 additionally requires T18 economic evidence, an explicit
owner class grant, pure-plugin determinism/replay tests, immutable staging and
compatible migration/deployment/rollback evidence before production code
authority expands.
