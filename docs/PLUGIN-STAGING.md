# Pure-feature plugin staging and finite replay

This is offline T22 preparation. `PluginStageStore` and `PluginReplayValidator`
are independent trusted-parent tools; no CLI, Engineer tool, application worker,
activation or live adapter calls them. `isolation.run_plugin` and
`isolation.promote_staged` remain disabled. Passing replay checks does not grant
broader Engineer authority, satisfy T18 economics, prove deployment-host
isolation or complete A41/A42.

## Exact staged contract

A plugin implements `propose(snapshot) -> dict[str, str]`. Its input is the
existing protected boundary's content-hashed numeric snapshot, and its output
contains exactly the owner-pinned feature names using bounded Decimal strings.
This `pure_feature_plugin` / `numeric_features/v1` contract is deliberately
distinct from T21's strategy-decision `propose(context)` contract. There is no
automatic bridge from feature values to decisions or orders.

The trusted parent stages UTF-8 source, never a candidate archive, editable test
command, dependency declaration or caller-provided success manifest. A strict
manifest binds these fields:

| Field | Meaning |
|---|---|
| `schema_version`, `class_name`, `contract` | Fixed schema and numeric-feature capability class |
| `source_sha256`, `source_bytes` | Exact candidate bytes, at most 65,536 bytes |
| `build_digest` | SHA-256 of the canonical manifest identity excluding this field |
| `baseline_release_id`, `baseline_source_sha256` | Exact declared baseline; a future controller verifies commission and active baseline |
| `protected_manifest_sha256` | Independently owner-pinned validator, boundary and resource policy |
| `validation_corpus_sha256` | Exact independently selected finite replay corpus |

`PluginStage` exports those fields, exact `source_text`, and `manifest_sha256`.
It can supply source/build evidence to a future class-aware controller, but its
presence is never an admission capability. Changing source, baseline, corpus or
protected pins changes the build digest.

Private owner-owned staging/receipt roots use mode 0700. Objects publish through
temporary owner-private directories, sealed files (0400), sealed object
directories (0500), an exclusive parent-directory lock and an atomic rename.
Publication fsyncs files, object directories and the collection. Repeating the
same build is idempotent only when all existing bytes and modes match; a damaged
object is refused rather than overwritten. Loads reject unexpected files,
symlinks in every path component, hard links, nonregular files, writable sealed
files/directories and wrong ownership. Bounded reads verify exact canonical
manifest and source identity every time. Candidate processes never receive a
store path or filesystem capability.

This is a content-sealed local store, not an immutable container image or a
defense against the host administrator. Owner-private storage and independently
pinned software are premises. Root or the owner can change files; subsequent
loads detect inconsistent bytes. An interrupted publication can leave an
unreferenced temporary directory. Production release work must add retention,
disk quotas, host-mount ownership and complete immutable runtime-image pins.

## Independent replay evidence

The owner/controller selects corpus cases and expected features before candidate
validation. Corpus identity includes all case IDs, snapshots and expectations.
The validator deep-copies a single canonical byte serialization and checks its
pinned digest before work; frozen models alone do not freeze nested mappings.
Expected features require canonical Decimal strings. Expected values and the
private receipt-authentication key remain in the trusted parent and never enter
child input, environment, command line, inherited descriptors or reports.

The validator pins its own source, staging proxy, no-follow file helpers, Decimal
conversion, protected harness/worker/sandbox/process/provenance sources and the
interpreter identity represented by that harness. Pins must come from an
owner-controlled release. Computing new pins from a candidate-controlled
checkout is not owner approval. These selected source/interpreter fingerprints
do not yet pin every standard-library/dependency file or the intended production
host image.

Each case runs two to five times in a fresh `-I -S -B` interpreter through
`ProtectedBoundaryHarness`. Case order alternates. The existing irreversible
Linux x86-64 seccomp boundary is installed before candidate source is parsed or
compiled; it denies file access/mutation, networking, process creation and
parent-process access. Existing child CPU/memory/input/output limits apply.
The parent policy bounds cases to sixteen, candidate executions to eighty, and
total child work to sixty seconds. It does not start a child when that child's
full wall-time envelope no longer fits its remaining quota. Parent validation,
final verification and local persistence have some additional overhead.

The parent independently checks each numeric proposal against the pinned
expectation and earlier outputs for that same case. Repeated matching output
proves repeatability on this finite corpus only. A plugin can still be
nondeterministic on untested inputs or states; fresh-process tests do not prove
general purity or alpha. Production Class B needs its complete accepted
determinism/capability policy, reviewed held-out evidence and deployment-host
verification before an owner grant.

Repeated validation feedback can turn an originally held-out corpus into
development data. Retaining attempts exposes that reuse; it does not make the
corpus untouched. Future commissioning must bound attempts and reserve separate
final evidence outside Engineer-readable feedback.

Every actual replay attempt and unrun quota-limited attempt is retained. Reports
bind source/build/manifest/baseline/protected/corpus identities; record status,
failures, case/repetition, exit code, bounded diagnostics, numeric result,
timestamps, elapsed time and resource-policy identity. Raw streams stay out of
the receipt. Successful status is explicitly `finite_replay_passed`; failed
capability/resource/contract checks, mismatched expectations and nonmatching
repeats produce `rejected`.

The parent authenticates the exact canonical report with HMAC-SHA-256 under an
owner-provided key of at least 32 bytes. The canonical envelope is content
addressed, sealed and retained without overwriting earlier attempts. Its SHA-256
is `validation_receipt_sha256`. A restarted verifier requires the same
independent pins, exact stage and private key. Candidate-supplied `passed` fields,
modified reports, a different key or another source/baseline cannot substitute
for that receipt. The key must remain outside mutable code and public Git;
the synthetic test key is not a deployment key.

All reports assert `production_authorization=false`, `live_authorization=false`
and `economic_evidence=not_evaluated`. No API spends, private venue access,
financial-state mutation or production activation occur in this slice.

## Remaining T22 work

After actual T18/T21 evidence and an explicit owner code-class grant, bind
admission to verified receipts and compatible baselines using the protected
controller. Implement shadow evaluation, immutable runtime builds and staging,
class-scoped Engineer commissioning and repair budgets, compatible application
migration rehearsal, automatic health-based activation, and independent
deterministic rollback that retains all financial events. New budgets, vendors,
venues, controller/kernel releases and destructive migrations remain separate
owner-controlled decisions. Routine changes inside a demonstrated granted class
can then be autonomous.

## Credential-free verification

```bash
uv sync --frozen
uv run pytest tests/integration/test_plugin_staging.py tests/integration/test_protected_process_boundary.py
uv run ruff check src/trade_graph/adapters/engineering/plugin_artifacts.py \
  src/trade_graph/adapters/engineering/plugin_replay.py tests/integration/test_plugin_staging.py
```

Tests execute actual confined children on this workspace's Linux x86-64 host
using synthetic inputs and keys. They cover sealing, baseline identity,
symlink/hardlink/mode/extra-file refusal, source and receipt tampering, retained
failures, wrong-key and receipt substitution, restart verification, nested
corpus mutation, varying fresh-process IDs, denied file/network/process access,
CPU/wall/memory/output limits and parent recovery. They are not credentialed,
economic, deployed-host or live-rollout evidence.
