# Owner-approved financial manifest transition

This offline operator changes the protected manifest over a healthy existing
paper database and its original independent witness. It preserves ledger facts,
fills, order attempts, budget receipts/origins, subscription invocation/attempt
identities, unknown subscription costs and owner policy/mandate. It cannot restore
a database, rotate a key, resume work, reconcile a venue or grant paid/live
authority. Mutable code has no transition RPC or owner-file writer.

## Preparation and approval

Use the reviewed target protected release and its current installed
package/interpreter pin. Preserve the exact complete old owner-approved manifest
documents, original parent capability key, original database inode and witness.
Every source manifest must retain the target deployment budget identity.

Every portfolio must already have an authenticated owner MANAGE_ONLY pause.
Stop/drain the service and workers before using this offline maintenance command.
Order/position management during this maintenance interval remains the owner's
responsibility. The command acquires the same nonblocking database ownership lock
as the service before opening/migrating SQLite and holds the private witness lock
through verification, commit and publication. Another worker causes refusal.

Inspection emits a proposal, without new authority. It binds a unique operation
ID, expiry within the next hour, exact old/target manifests, database device/inode,
full current witness digest/highwaters and complete current scanned state digests
for every portfolio. The existing complete-prefix/native-source checks and scan
bounds remain enforced. Old executable pins are historical data and are not run.

Older checkpoints did not retain immutable native order-attempt identity prefixes
or subscription row commitments. The transition binds their exact current rows
and captures identity prefixes for subsequent verification; it cannot prove
retroactively that these fields were unchanged before that approval. Existing
authenticated ledger and budget-origin commitments retain their original proof.

Run the reviewed installed interpreter with actual protected paths and a current
expiry (the paths and timestamp below are placeholders):

```bash
python -m trade_graph.kernel.financial_transition inspect \
  --database /private/state/paper.sqlite \
  --protected-owner /root-distributed/target-owner \
  --source-manifest /root-distributed/previous-owner/runtime-manifest.json \
  --operation-id reviewed-upgrade-unique-id \
  --expires-at 2026-10-08T18:10:00Z
```

Supply one --source-manifest for each distinct active source manifest. Review the
emitted JSON and install those approved bytes as financial-continuity-approval.json
in the target protected owner directory. The existing owner-file loader requires
root-controlled ancestors/directory and a bounded protected regular file. This
approval is supplied independently by the owner; inspection never installs it.
Apply accepts only that fixed root-distributed file, rather than an arbitrary
caller-provided JSON path or a role label.

```bash
python -m trade_graph.kernel.financial_transition apply \
  --database /private/state/paper.sqlite \
  --protected-owner /root-distributed/target-owner
```

Apply compares a fresh full scan with the exact approved proposal before writing.
It appends an immutable authenticated transition and target generation-one
checkpoints referencing the source checkpoints. It revokes old issued capabilities
and changes old controllers to MANAGE_ONLY without changing their historical
manifest/instance identity. Atomic witness publication retains every old scope,
every target scope and the authenticated transition chain. The destination
size/scope bounds are checked before commit: at most 128 retained scopes, 128
transitions and 64 KiB witness bytes. Old scopes consume this capacity; history
cannot be dropped to make room.

Old manifests remain retired. Normal target release admission can use the
retained finance, but every owner MANAGE_ONLY pause remains. A separate
authenticated resume must perform existing reconciliation/eligibility checks.

## Interruption

A precommit failure leaves no transition or target checkpoint. Reinspect when any
approved fact changed. A postcommit publication failure blocks authority for old
and target manifests. Retry apply with the **identical root-owned approval** and
original key. Recovery publishes only that operation's committed destination
after authenticating its exact source witness, chain, owner pause and a full scan
matching the committed target checkpoints. Changed facts/approval, missing
witness, tampered evidence, altered inode or newer target checkpoint refuse.
A committed operation can finish after approval expiry because its timely
authorization was already sealed; an unstarted expired operation refuses.

A postpublication crash is idempotent: the same operation verifies the retained
chain and returns its original result without another checkpoint or new grant.
If facts changed after an unpublished commit, preserve evidence and keep new
authority blocked for separate reviewed recovery. This command has no general
repair, reset, reseal or rollback route. Restoring a database alone cannot remove
a witnessed upgrade. Restoring both database and witness/host remains outside
this local proof and requires the existing independent external rollback anchor.

## Synthetic checks

```bash
PYTHONPATH=src python -m pytest \
  tests/integration/test_financial_transition.py \
  tests/integration/test_financial_checkpoint.py \
  tests/integration/test_protected_budget_continuity.py -q
```

Cases cover filled orders/attempts, original paid receipts/uncertain holds,
subscription identity/unknown costs, changed executable pins, multiple
transitions, old-manifest retirement, stale/wrong/expired approvals, wrong keys,
prefix/receipt tampering, rollback, exclusive ownership, immutable storage,
bounds, precommit failure, publication interruption/idempotency and postcommit
state drift. These synthetic checks do not establish installed-image, exchange,
paid-provider or production recovery evidence.
