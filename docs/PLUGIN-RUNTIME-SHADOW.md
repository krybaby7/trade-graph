# Executable plugin bundles and independent functional shadow

This T22 preparation provides usable offline build, execute, compare and verify
APIs. It extends [plugin staging](PLUGIN-STAGING.md) without enabling production
promotion. The opt-in [offline commission handler](PLUGIN-COMMISSIONS.md) uses
these APIs for independently verified executable candidates with synthetic
gateway billing and recovery. The default paper runtime and deployed Engineer
do not call these APIs. `isolation.run_plugin` and `isolation.promote_staged`
remain disabled.
T18 economics, complete T21 extraction/images/host verification and an explicit
owner class grant remain prerequisites for broader deployed authority.

## A build contains its executable bytes

`PluginRuntimeBuilder` requires an independently pinned replay validator, runtime
builder fingerprint, current Python environment fingerprint, protected numeric
manifest and private receipt key. These pins must come from an owner-approved
release outside candidate-controlled input. Recomputing current hashes or having
an HMAC key does not establish a production grant or verified deployment image.

`build(stage_build_digest, replay_receipt_sha256=...)` verifies the exact sealed
stage and parent-authenticated finite replay receipt. A failed replay produces a
retained negative build receipt and no runtime bundle. A passing replay publishes
four actual files into the owner-private content-addressed runtime collection:

- Exact candidate `source.py`.
- Copied trusted `worker.py`, whose OS confinement precedes candidate parsing and compilation.
- Copied trusted `sandbox.py` implementing the existing irreversible seccomp boundary.
- A strict canonical `manifest.json`.

The runtime build digest binds source/stage/manifest/replay/corpus/baseline hashes,
the protected numeric manifest, exact worker/sandbox hashes, builder and Python
environment pins, `pure_feature_plugin`, `numeric_features/v1`, and
`stateless/v1`. Publication uses the same no-follow, private, sealed, locked,
fsynced object protocol as staging. Load verifies every exact byte, owner and
mode, expected file inventory, the original stage and independently passed
replay. A tampered source, worker, sandbox, manifest or original stage invalidates
the bundle and earlier build receipt.

`evaluate(runtime_build_sha256, observations)` actually launches the copied
worker from that sealed bundle using the resolved pinned base interpreter with
`-I -S -B`. The child receives exact copied source and numeric snapshot bytes
over stdin. This input contains no expectations, source provenance records,
keys, owner policy, financial state, model transports or store paths. Python
metadata contains the worker entry-point path; the OS denies access to that path
and other files once candidate execution begins. The trusted
parent validates the output independently and verifies the whole bundle again
after execution. Candidate failure returns explicit `mutable_unavailable`
zero-feature fallback from protected code; it never produces an order or a
fictional successful decision.

The Python fingerprint reads the current interpreter binary, standard-library
source and native extensions, Python shared library and standard-library ZIP
presence/content. File counts, file/total bytes and reads are bounded; linked
dependency directories or changing/nonregular files fail closed. The command
redirects bytecode-cache lookup to an absent path inside the sealed bundle and
disables cache writes, so unpinned ambient `.pyc` files cannot replace pinned
source imports. Unknown files appearing in the bundle invalidate it.

This seals an executable **application bundle**. It does not copy Python into a
container, pin every OS linker dependency, establish trusted immutable
distribution, or defend against the host administrator changing files between
checks. Python/stdlib remain on the owner-controlled host, whose exact sampled
bytes are checked. Complete dependency/OS images, trusted mount ownership and
race-free immutable deployment remain production gates. Tests run on the current
workspace host, not an owner-designated production platform.

Positive and negative build attempts have immutable parent-authenticated
receipts. `verify_build_receipt(receipt_digest, stage_build_digest=...)` requires
the exact expected stage, current independent pins and original replay; positive
receipts additionally require the identical loadable bundle. Caller-supplied
success flags, another key or another stage cannot transfer acceptance.

## Shadow uses the actual baseline

`ShadowCorpus` retains bounded immutable numeric evidence. Each field records
value, event time, availability time, source reference and source digest. Cases
record an explicit UTC decision time and independent expected features. Reject
future event/availability clocks, availability before the event, naive/non-UTC
timestamps, duplicate cases, nonchronological cases and invalid/nonfinite/
expanding/noncanonical Decimal strings. JSON loading rejects duplicate fields,
binary floats, excessive bytes, depth and node counts before typed validation.

`retain_corpus(store, corpus)` revalidates nested structures, seals canonical
bytes and returns their digest. `load_corpus` checks those exact bytes and modes.
Changing a caller's nested frozen-model mapping cannot change retained inputs.
Changing stored bytes, even by adding semantically irrelevant whitespace,
invalidates the evidence.

`ShadowPolicy` independently pins the corpus, baseline release/build/source,
protected numeric manifest, state compatibility, repetition/resource limits,
and functional error/regression tolerances. The runner holds a canonical byte
copy, not mutable nested caller data. Baseline identity comes from this protected
policy and the retained baseline bundle, never candidate prose or a supplied
baseline output. The candidate's declared predecessor must match the policy's
baseline release and actual baseline source bytes.

The runner executes both actual baseline and candidate bundles against the same
point-in-time numeric cases, in fresh confined processes, reversing arm order
on alternate repetitions. Expectations, comparison rules, clocks/provenance and
keys stay parent-only. It checks exact source/version/protected identities,
feature contract and stateless schema before children run. Incompatibility or an
excessive planned resource envelope produces a negative receipt without running
children. The parent also stops starting children when their full wall-time
envelope no longer fits the remaining independent quota.

`compare(candidate_runtime_build_sha256)` records every baseline/candidate
attempt, including failures and quota-limited unrun work. It compares outputs
with fixed expectations and earlier identical-case outputs. Functional metrics
are absolute feature error against the independent corpus, not trading returns:
total absolute error, sample count, maximum error and an upper-rounded display
mean. Acceptance uses exact Decimal sums and cross-multiplied sample counts;
rounding the displayed baseline mean cannot loosen the owner's regression limit.

Candidate resource/contract failure, changing output, controller error, quality
regression and changed artifacts produce retained negative receipts. Ordinary
controller diagnostics record the exception type without copying arbitrary
exception text. Trusted fallback and subsequent baseline execution do not
depend on the failed candidate or a model. Financial databases are absent from
this workflow and are never restored or changed.

All reports bind baseline/candidate runtime, source, stage, manifest and replay
receipt identities, exact retained corpus bytes, independent shadow policy and
runner, protected numeric manifest and state compatibility. They carry
`production_authorization=false`, `live_authorization=false`,
`economic_evidence=not_evaluated` and
`source_verification=retained_declared_point_in_time`. Source labels and digests
do not authenticate an upstream public feed, bill or real observation. Finite
shadow/replay also does not prove general determinism; repeated feedback may
turn held-out cases into development data. Retain all attempts and reserve final
evidence independently before granting a broader class.

`verify(receipt_digest, candidate_digest=...)` supports restart verification
using the same private key and independent pins. It rechecks current baseline,
candidate, corpus, compatibility and identity bindings; no candidate output or
caller boolean can replace its parent-authenticated receipt. A negative receipt
remains retained even if an externally damaged artifact temporarily prevents
verification. Repairing the original exact bytes permits verification of that
same negative outcome; it does not make it positive.

With already owner-pinned `builder` and `shadow_runner` objects:

```python
built = builder.build(stage_digest, replay_receipt_sha256=replay_receipt)
build_report = builder.verify_build_receipt(
    built["build_receipt_sha256"], stage_build_digest=stage_digest,
)
if build_report["status"] == "built":
    shadow_receipt = shadow_runner.compare(built["runtime_build_sha256"])
    shadow_report = shadow_runner.verify(
        shadow_receipt, candidate_digest=built["runtime_build_sha256"],
    )
```

These APIs stage and evaluate. No example admits or activates production code.
T21's `departmental_graph/v1` and decision proposal contracts are separate from
`numeric_features/v1`; there is no implicit bridge into financial/role execution.

Offline class-scoped commissioning, bounded synthetic repair/spend integration
and authenticated candidate recovery are now implemented. Remaining T22 work
includes funded broader commissioning after admission, immutable deployment
images and verified intended-host staging, compatible application migration
rehearsal, authoritative dependency
and owner-class admission, autonomous controller activation/observation, and
independent deterministic deployment rollback preserving financial history.
Stateless feature bundles require no database migrations; unsupported state
schema expectations fail compatibility rather than invoke candidate migration
hooks. Selected application-code authority remains closed.

```bash
UV_PROJECT_ENVIRONMENT=/workspace/trade-graph/.venv \
PYTHONPATH=/workspace/trade-graph-t22-gates-r2/src:/workspace/trade-graph-t22-gates-r2 \
uv run --no-sync pytest tests/integration/test_plugin_runtime_shadow.py \
  tests/integration/test_plugin_staging.py tests/integration/test_protected_process_boundary.py
```

The worktree-specific environment above is the local verification setup, not a
deployment command. All committed fixtures and keys are synthetic; no paid API,
private venue request, real order, production deployment or broader grant ran.
