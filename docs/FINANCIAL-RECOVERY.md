# Authenticated recovery of a paper database into a separate location

`trade_graph.kernel.financial_recovery` is an offline owner operator for a healthy
verified recovery copy with a new device/inode. It preserves the original
independent witness commitments and records the identity change explicitly. It
provides no salvage, key rotation, account reset, task replay, resume, paid call or
live-trading authority. The manifest-transition operator still requires its
original inode and cannot restore a database.

## Preserve and substantiate continuity first

Keep the damaged database, surviving WAL/SHM/journal files, original witness,
protected owner settings/key, healthy backup/checksum and incident evidence
unchanged in private storage outside Git. Stop all original writers and keep AI
paused. Work only on a new private candidate created from a verified healthy
backup. Copy the original witness bytes alongside the candidate with mode 0600
and a mode-0700 parent. Never initialize a replacement witness.

Independently compare the backup cutoff, current witness and retained incident
records. Verify exact attempt/invocation identities, failed outcomes, uncertain
usage/costs and every newer financial record. The operator cannot infer history
that an old checkpoint never committed. Its inspection reports
`proof_limitations` for omitted financial tables and identity prefixes. A count
of surviving rows is not a completeness proof: damaged rows or missing newer
valuation/financial records block promotion even when older accounting balances.

The owner must retain a private audit establishing continuity and supply its
SHA-256 digest. Inspection binds that digest but cannot establish the truth of
its contents. Do not distribute an approval when records remain unexplained.
The October 8 damaged installation is not recovered merely because this source
path and its synthetic tests exist.

## Inspect and apply

Use a reviewed installed package/interpreter and the original capability key in
a root-distributed protected owner mount. Its manifest must pin this package and
retain the deployment identity. Supply every exact active source manifest;
all must have that same deployment identity, and their hashes must match all
active witnessed scopes. The manifest may differ from the historic
witness manifest; recovery preserves those scopes and requires a separate
manifest transition afterward when necessary.

Every portfolio must be paper and have owner MANAGE_ONLY. The operator obtains
the same nonblocking database-ownership lock as the service before SQLite opens,
then the private witness lock. It authenticates the original witness against its
explicit original device/inode, all retained checkpoint and manifest-transition
receipts, and any preceding recovery chain. Normal runtime witness reads still
require the actual current inode.

```bash
python -m trade_graph.kernel.financial_recovery inspect \
  --database /private/recovery/paper.sqlite \
  --protected-owner /root-distributed/reviewed-owner \
  --source-manifest /root-distributed/previous-owner/runtime-manifest.json \
  --source-device ORIGINAL_DEVICE --source-inode ORIGINAL_INODE \
  --operation-id unique-reviewed-recovery \
  --incident-evidence-sha256 PRIVATE_AUDIT_SHA256 \
  --expires-at CURRENT_UTC_EXPIRY_WITHIN_ONE_HOUR
```

Inspection runs full SQLite integrity and foreign-key checks, authenticates all
original scopes, verifies retained immutable financial and effect-identity
prefixes, native per-asset journal balancing and pinned ledger replay. It binds
every application table's complete rows and rowids plus the complete application
schema, including operational history and subscription failures/unknown costs.
Recovery receipts themselves are excluded from the state hash so interrupted
publication can be completed. Scans have row/byte/time bounds and refuse rather
than truncate. Changed mutable financial commitments and legacy proof limits
appear explicitly in the approval; they require the independent incident audit.

Review the exact proposal and install its bytes as the fixed root-distributed
`financial-recovery-approval.json`. The operator never writes an approval file.
Apply accepts only that protected file, not arbitrary caller JSON or a model role.

```bash
python -m trade_graph.kernel.financial_recovery apply \
  --database /private/recovery/paper.sqlite \
  --protected-owner /root-distributed/reviewed-owner
```

Apply rechecks the complete proposal before committing. It preserves historical
controller/request identities while setting every recovered controller to
MANAGE_ONLY, incrementing controller generations and revoking issued RPC
capabilities. The approval binds the exact fence targets; the authenticated
immutable receipt binds the resulting complete post-fence state. It then
atomically publishes a candidate witness that keeps every original scope and
manifest-transition digest, records the new inode and appends the recovery digest.
The original damaged database and witness are never opened by this operator.

The receipt also establishes forward retention commitments for the complete
recovered immutable/effect-identity prefixes, including native and subscription
attempts omitted by legacy checkpoints. Durable terminal subscription rows seal
their exact failure/outcome, usage and unknown-cost state. Recovery-only rowid
prefixes retain valuation marks, FX observations and dated price cards while
allowing new observations with random IDs to append. Normal admission/scans
verify these authenticated baselines before the first new checkpoint and after
subsequent manifest transitions/recoveries. This proves retention starting at
approved recovery time; it does not manufacture proof before that time.

Witness schema 3 retains an ordered recovery receipt chain. Runtime validates
its authenticated lineage and all ancestor receipts, alongside the original
financial scope/manifest chain. Subsequent checkpoints, manifest transitions and
further verified recoveries retain that chain. At most 128 recoveries and 64 KiB
of witness bytes are admitted; retained history cannot be dropped to make room.

## Interrupted publication and promotion

A precommit failure leaves no recovery receipt or controller fence. A postcommit
publication failure keeps authority blocked. Retry only the identical protected
approval and original key; publication succeeds only with the exact original
source witness, all original scopes, unchanged complete post-fence state and
matching current candidate inode. A timely sealed operation can finish after
expiry. An unstarted expired approval refuses. A published retry verifies the
chain and owner pause without issuing another grant or replaying a task.

After continuity, installed SQLite/locking behavior and any required manifest
transition pass independently, the installation owner may point the service and
dashboard at the recovered candidate. Retain the old image and damaged originals.
Start exactly one management worker, refresh public data and verify MANAGE_ONLY.
Normal paper AI operation still requires a separate authenticated owner resume
through existing reconciliation and eligibility checks. Missing records or
unexplained mismatches require remaining stopped.

## Synthetic regression checks

```bash
PYTHONPATH=src python -m pytest tests/integration/test_financial_recovery.py \
  tests/integration/test_financial_checkpoint.py \
  tests/integration/test_financial_transition.py \
  tests/integration/test_protected_budget_continuity.py
```

These tests establish source behavior only. Actual storage health, installation
identity, incident continuity and dashboard/management success need private
installation evidence; neither synthetic results nor a healthy backup prove the
cause of corruption.
