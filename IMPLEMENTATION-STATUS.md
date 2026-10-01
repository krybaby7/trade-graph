# Implementation status — T14 complete

Updated: 2026-10-01. **T00–T14 complete at their recorded scopes; autonomous R1 is not complete.**
Branch: `cursor/trade-graph-r1-548a`; verified implementation: `8447f4c068a62433a2975bccb2292696640baca4`.
Default USD10,000 virtual capital with EUR reporting. Paid calls and live trading remain disabled.

## Completed T14

Consumers now load the actual independently tested artifact bytes and manifest from immutable persisted bundles.
Activation validates the real predecessor, attestation, commission, checker confinement and baseline CAS,
then increments a generation at a quiet Trader boundary. Reconciliation precedes reload acknowledgement.
Requests, snapshots, receipts and Decisions retain loaded provenance; stale/ABA generations cannot dispatch
or apply new effects. Follow-on Engineer work uses the active artifact source.

The protected controller pins functional observation gates before activation. Persisted decisions/requests
and failed tasks supply evidence; byte counts and usage/receipt facts are derived from stored records.
Completion and observation commit together. Unknown billing waits without creating a decision or order.
Failure or timeout restores and reloads the verified predecessor without a model. Busy decisions defer rollback;
corrupt recovery blocks fresh mutable work while reconciliation/protection and owner controls remain intact.
All orders, fills, expenses, reservations and opening/closing version references survive.

Registered context policy, prompts, strategy guidance, report sections and artifact-owned schedules are consumed.
Schedule artifacts cannot allocate funds or change protection cadence; trusted routing supplies bounded allocation.
Read [the T14 review](docs/reviews/2026-10-01-activation.md) and D33–D35 for policy and boundaries.

## Verified commands and evidence

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests
uv run pytest --junitxml=test-results.xml
python3 scripts/check_plan.py
python3 scripts/test_planning.py
python3 scripts/next_task.py --prompt
uv run trade-graph demo --offline --work <fresh-private-directory>
```

- Python 3.12.14, unchanged `uv.lock`; locked dependencies installed from the existing local cache.
- **391 passed; zero failures, errors or skips**, independently verified in JUnit. Baseline was 349 tests.
- **42 new cases:** 24 lifecycle, 15 actual inference/consumer recovery, three schedule/report cases.
- Ruff, planning references/DAG, all ten planning tests and illustrative cost calculation passed.
- Offline demo: actual Trader context **8 → 5**, database reopen still **5**, healthy observation, then a charged
  invalid reply triggers automatic reload to **8** and a subsequent baseline decision. Four fills survive restart,
  no duplicate submission, rejected Engineer evidence and all receipts retained.
- Staged source/document/test hygiene checked; runtime data, environments and credentials are not tracked.

All model/HTTP inputs were scripted or mocked. This is local credential-free evidence; no new CI conclusion,
paid provider probe, authenticated exchange test, real order or profitability claim is made.
Use a fresh demo directory because previous databases and staging evidence are intentionally retained.

## Next dependency-ready task: T15

Complete the financial/organisation/cost/change dashboard, missing positions/decision/research/lesson/event APIs,
owner configuration/task controls, evidence navigation and authoritative reconciliation/provisional-state coverage.

**T16:** complete the offline acceptance fault catalogue and production-equivalent full-loop paths.
**T17:** replace one-pass `run` and placeholder `doctor`/`report` with a continuous paper service, real diagnostics,
deployment/backup procedures, then separately owner-funded credentialed paper verification.
**T18–T22:** forward economic evaluation, live adapter/pilot and actual protected-host isolation before broader code.
T20 remains blocked; no paid soak or live pilot is authorized by implementation completion.

Functional health checks do not prove strategy quality, token savings or economic improvement.
Inspect current Git state before continuing; preserve newer work and historical evidence in progress.json/reviews.
