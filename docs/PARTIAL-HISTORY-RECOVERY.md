# Owner-accepted partial price/reporting history

An owner may explicitly accept a known gap in historical price/reporting
observations for a personal paper installation. This does not accept missing
money, orders, fills, accounting, model invocations/attempts, failed AI outcomes,
usage/cost uncertainty or decision inputs. Existing financial witness, prefix,
identity, native-accounting, SQLite, owner-pause and ownership checks remain
unchanged. Acceptance does not create observations, replay a task, reset an
account, replenish a real budget or authorize AI/live/paid operation.

Preserve all originals and incident evidence as described in
[authenticated recovery](FINANCIAL-RECOVERY.md). Independently establish that
financial/AI history and cost uncertainty are preserved. Classify each absent
record from retained evidence; a missing payload alone does not prove a row was
expendable. Unknown cause stays unknown. Do not infer an exact symbol or timestamp
from a damaged index. Unknown observation timestamps remain null.

State the audit's historical proof limits explicitly. Exact agreement of audited
surviving/backup tables and dispatch timing is evidence for retained state; it
cannot prove every historical row ever created was recovered. A legacy witness
that predates subscription records cannot authenticate them retroactively.
Recovery authenticates retention of the approved recovered state from that point
forward. An identified missing financial/AI record or unexplained mismatch remains
outside the accepted reporting gap and blocks recovery.

## Fixed owner incident document

The operator reads only `financial-history-incident.json` from the same protected
root-distributed owner directory as the manifest/key. It cannot accept an
arbitrary incident path or generate owner approval. The document has exactly
these fields; unknown fields refuse:

- `schema_version`: 1; `incident_id` and `portfolio_id`: bounded stable IDs.
- `classification`: `partial_price_reporting_history`; `cause`: `unknown`;
  `accepted_effect`: `historical_reporting_only`.
- `backup_cutoff_at`: known UTC backup cutoff; `affected_interval`: exact
  `start_at` and `end_at` UTC bounds, at/after the cutoff and no later than now.
- `missing_records`: nonempty bounded list of exact `table`, `category`,
  `record_id`, `rowid` and `observed_at`. Rowid/time may be null when unknown.
  Each claimed absent primary ID must actually be absent from the candidate.
  A recorded rowid describes the lost row in the original source inode. Fresh
  rows with different primary IDs may reuse unused rowids in the recovery copy;
  this does not recover a missing payload. Do not insert placeholders or
  manipulate SQLite sequences to reserve the old rowid positions.
- Allowed table/category pairs are `valuation_marks` / `valuation_mark`,
  `fx_rates` / `fx_reporting_observation`, and `observations` /
  `public_price_reporting_observation`. The last category requires independent
  evidence that these are public price/reporting rows and that no retained
  financial or AI decision depends on a lost input.
- `unknown_additional_loss`: explicit boolean for additional price/reporting
  uncertainty; `uncertainty_summary`: concise bounded statement. This field
  cannot expand accepted loss into financial/AI history.
- `financial_ai_history_verified`: true;
  `preserved_continuity_evidence_sha256`: SHA-256 of the retained independent
  financial/AI audit; `classification_evidence_sha256`: SHA-256 of retained
  evidence substantiating the observation classification. These are independent
  of the overall `--incident-evidence-sha256` forensic-inventory digest.
- `previous_evaluation_period`: exact `period_id` and `started_at`; unknown old
  start is null. Initially the period ID is the existing portfolio experiment
  identity; after a prior accepted incident it must match that authenticated new
  period exactly, including its known start.
- `new_evaluation_period`: a fresh distinct `period_id` and explicit UTC
  `started_at`, at/after the affected interval and no later than now. Historical
  period and incident identities cannot be reused.
- `old_run_retained`: true; `account_reset`: false.

The software validates this exact scope and binds the document. It cannot prove
an absent payload's original category or the completeness of an external audit.
Those are owner-controlled evidence obligations, not permission to guess.
If the financial/AI audit or observation classification fails, remain stopped.

## Inspect, approve and apply

Use the existing authenticated identity recovery with the original key/witness,
exact source manifests and a private verified candidate. Add only this flag to
inspection:

```bash
python -m trade_graph.kernel.financial_recovery inspect \
  --database /private/recovery/paper.sqlite \
  --protected-owner /root-distributed/reviewed-owner \
  --source-manifest /root-distributed/previous-owner/runtime-manifest.json \
  --source-device ORIGINAL_DEVICE --source-inode ORIGINAL_INODE \
  --operation-id unique-reviewed-recovery \
  --incident-evidence-sha256 PRIVATE_FORENSIC_INVENTORY_SHA256 \
  --expires-at CURRENT_UTC_EXPIRY_WITHIN_ONE_HOUR \
  --partial-price-history
```

Inspection emits approval schema 2 containing the exact incident, all original
witness commitments, complete candidate state and unchanged recovery safeguards.
Install only the reviewed exact bytes as `financial-recovery-approval.json` in
the protected owner directory. Apply uses the ordinary command from
FINANCIAL-RECOVERY.md. It independently rereads the fixed incident file and
requires equality with the approved document. Changing the incident requires a
fresh inspection/approval before commit; an already committed operation requires
its identical original approval/document. No flag bypasses validation.

The existing immutable authenticated recovery receipt carries the incident and
new period. Its digest remains in the independent witness recovery chain.
Schema-1 approvals/receipts remain valid and imply only `NO_RECORDED_INCIDENT`,
not a claim that all history is complete. Interrupted publication follows the
same commit, exact-state and idempotent retry rules as ordinary identity recovery.

## Honest reporting and restarting

`verified_recovery_history(financial, portfolio_id)` authenticates the current
witness and receipt/manifest chain before exposing the exact incident, recorded
receipt digest/time, old period and new period. It joins the caller's read
snapshot and performs no full financial rescan or authority change. A mismatched
witness/receipt/period chain refuses; the dashboard must show unavailable
verification rather than suppress the incident. A snapshot/publication race may
briefly make this projection unavailable.

The new period is an authenticated reporting/evaluation boundary on the same
paper account. It does not rewrite old run/task IDs, the portfolio experiment ID,
account inception, earlier P&L or expenses. Retain and label the old run's
incomplete historical price/reporting evidence; do not use it as complete
performance evidence. Any new-period performance analysis needs a fresh dated
opening valuation and complete subsequent observations. A period label alone is
not a preregistered qualified forward trial or proof of profitability.

After independent installed-image/SQLite/locking checks, required manifest
transition and owner-approved installation switch, start exactly one
MANAGE_ONLY worker/dashboard, refresh public data and verify fresh valuations.
Keep AI paused. Resuming ordinary paper AI remains a separate authenticated owner
resume through existing reconciliation and eligibility checks, using the
management-to-normal handover in [normal paper operation](NORMAL-PAPER-OPERATION.md). Real trading,
API billing and paid extras stay disabled.
