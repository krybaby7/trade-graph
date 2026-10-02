# Recovery, T12 closure and remaining R1 work

Date: 2026-09-30. Scope: resume the crashed GitHub implementation, preserve published work,
complete the Leader/Secretary application workflow, and revalidate—not assume—T13–T15 acceptance.
Branch: `cursor/trade-graph-r1-548a`; PR #1 stays draft/unmerged.

## Recovery evidence

Entry head `0025141e7b094a5536deb6fbceb873574c4160a1` contains the prior session's CSRF
repair (`5a732f7`), bound engineering commissions/attestations and gateway leadership (`66d6f9a`),
and Secretary backlog/schedule/self-escalation fixes (`0025141`). No reset or replacement was performed.
The stale `IMPLEMENTATION-STATUS.md` and PR body did not describe that newer implementation accurately.

CI source artifact 11110365010 from PR run 36738990918 was extracted. Although its filename used the
PR merge commit, its complete Git tree `0a9317bf8ae85f035b3f69dc0801e842c44da457` equals the actual
branch head tree. The prior push runtime run 36738982665 and planning run 36738982770 had succeeded.
Unpublished edits from the crashed session are not verifiable; the published implementation is preserved.

## New code checkpoint

Commit `5d5ce35a7a0f955f14cbb4ef4c7ca920e4432c09`, parent `0025141`, tree
`308d552fc2bb424b1bcb660f9a3678c8627faa5a`. The remote Git tree equals the locally tested tree.
Only `src/trade_graph/application/secretary.py`, `src/trade_graph/application/leadership.py` and
`tests/integration/test_candidate_review_flow.py` changed in this code checkpoint.

### Reproduced gap and correction

The existing Engineer recorded `engineer_candidate` completion events, but the Secretary did not
route those events and the Leader context contained no completed candidate reports. A manually
supplied candidate ID could test the controller without proving an actual department-to-Leader review.

The Secretary now reads scoped persisted candidate facts from completion events, makes immutable
material reports and routes a digest. Bounded candidate cards contain the objective, success/rollback
criteria, changed paths, hashes, known limitations and selected independent-attestation facts.
They exclude raw check logs, executable content and production paths. The event payload is only a lookup hint.

The Leader receives those cards in its persisted gateway snapshot. Activate/reject actions must cite
the chosen candidate or its specific completion report, and the persisted candidate/attestation must
still match the reviewed snapshot. Existing activation authorization is not replaced by model evidence.
No new owner authority or per-trade approval step was added.

Ten new regression cases initially failed and now pass: READY activation and rejection, failed-artifact
visibility/rejection, report-based citation, post-snapshot state and attestation changes, guessed candidate,
unrelated evidence, cross-portfolio event injection, and crash after committed activation before worker finish.
The recovery case creates neither a second activation nor another model attempt/usage receipt.

## Verification performed

The container could not install packages over the network. The saved credential-free review environment
from artifact 11110085133 contains Python 3.12.14 and the locked dependencies; its `uv.lock` is byte-identical
to the repository. A local-only import path was configured so the trusted Engineer subprocess could import
the same installed package. No source or tests were changed to work around an import failure.

The unmodified recovered baseline passed **229 tests**. The changed source passed **239 tests**,
with **0 failures, 0 errors and 0 skips**, including every existing test and the ten new regressions.
Ruff passed. `scripts/check_plan.py`, all 10 `scripts/test_planning.py` tests and the cost-model utility passed.
The standalone `demo --offline` completed with four fills before and after restart, zero restart submits,
retained rejected-change evidence, budget-exhaustion handling and the A43/A44 currency/budget invariants.
Its evaluation verdict remains `insufficient_evidence`; its version label is not proof of consumer reload.

Both independent remote checks passed for the exact code commit. Downloaded JUnit artifact
11111353237 independently confirms 239 tests, including the ten new regressions, with no failures, errors or skips:
- Runtime: https://github.com/krybaby7/trade-graph/actions/runs/36744681382
- Planning: https://github.com/krybaby7/trade-graph/actions/runs/36744681372

Normal fresh-checkout commands (credential-free):

```sh
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests
uv run pytest --junitxml=test-results.xml
python3 scripts/check_plan.py
python3 scripts/test_planning.py
python3 scripts/next_task.py --prompt
uv run trade-graph demo --offline --work runtime/demo
```

No test exclusion, fixture-only shortcut for the full suite, paid provider request, authenticated exchange
request, live order or new public-data smoke was performed. Demo output and receipt fixtures are synthetic.

## T12 acceptance map

| Required behavior | Implemented path and evidence |
| --- | --- |
| Deterministic digests, routing, material events | `Secretary.report/collect/process/route/scheduled`; leadership workflow/followup tests plus new candidate-review tests |
| Persisted gateway-backed decisions and rationale | `LeaderHandler` through `RoleWorker` and the shared gateway; evidence, outcome, review criteria, resources and usage attribution persist |
| Bounded completed consultations | `GatewayRole` returns a department report and a follow-up Leader task under the same root; the Leader must cite the return; workflow tests verify dispatch, completion and incorporation |
| Mandates, allocations and schedules | Positive gateway action tests persist a tighter mandate, redistribute existing resources and retain schedule changes across restart; invalid scopes/limits roll back |
| Authorized commissions and reviewed results | A real persisted Leader decision authorizes the Engineer task; candidate-completion events now return to a gateway-backed Leader review |
| No owner escalation or trade committee | Owner allowance/halts are protected, root/attempt/delegation bounds are checked, own pause can be resumed, and trade approval is not an action |
| Failure and restart behavior | Malformed/refused/stale/over-budget decisions preserve receipts without effects; atomic rollback and recovery-before-recall tests pass |

T12 is therefore marked done at its application-workflow boundary. Continuous service assembly is still
T17; completing this task does not certify the Engineer worker (T13), changed-artifact consumption (T14)
or the whole-product offline catalogue (T16).

## T13–T15 revalidation and next implementation

**T13 — next dependency-ready task.** Preserve `ArtifactEngineer`, its allowlisted staged repository,
trusted installed checks, candidate-bound attestations and persisted attempt fences. Authorization tests
now reject nonrunnable/revoked/expired/uncommissioned work and retain failure evidence after revocation.
Still implement a commissioned Engineer `RoleWorker`/gateway handler: bounded patch generation and
repair attempts, real per-attempt accounting, lease-safe crash/retry/final-failure handling and meaningful
resource/path isolation tests. Calling `engineer.implement` directly from a fixture is not this lifecycle.
A scrubbed subprocess is not an OS sandbox; arbitrary mutable code remains disabled.

**T14 — not complete.** Persisted candidate, portfolio, commission, task hash and attestation binding,
quiescence, baseline CAS, stale rejection and ledger-preserving pointer rollback have passing regressions.
The new review path exercises the Leader gateway rather than manually supplied controller arguments.
Still implement the actual artifact-consumer reload/restart boundary, health observation and automatic
rollback. Demonstrate changed behavior in the next decision and restored behavior after rollback—not
merely a new `system_version_id` on an otherwise unchanged decision.

**T15 — not complete.** CSRF checks now cover every unsafe cookie write, including Leader activation.
An arbitrary Authorization header is not an exemption; genuine authenticated Bearer and cookie-plus-CSRF
writes have positive tests. Cross-portfolio change projection leakage is also covered. Still deliver the
full docs/09 views and APIs, including positions, decision detail, research, lessons and events; complete
owner configuration/task controls, evidence navigation and authoritative financial reconciliation with
native balances, external spend and provisional/degraded/uncertain distinctions. These are not all
implemented by the existing overview/tasks/orders/costs/changes/health routes.

## Remaining plan after those prerequisites

T16 must assemble the complete applicable offline A01–A38/A43–A44 fault catalogue; disk/DB failures,
repository/artifact secret scanning and production-equivalent integration paths remain open.
T17 must replace one-pass `run` and placeholder `doctor`/`report` with a packaged continuous service,
real diagnostics/reporting and verified operating procedures; the later credentialed paper soak needs
explicit owner funding. No credentials are required to continue the offline implementation.
T18 evaluates predeclared forward-paper results against baselines after all costs; profitability is not assumed.
T19 completes the live broker contract and separately authorized authenticated verification.
T20 remains blocked on prerequisites, eligibility and explicit real allocation/authorization.
T21–T22 add actual protected-kernel host isolation and separately granted broader code/plugin authority.

Use the current remote head, `planning/tasks.json` and `planning/progress.json` to resume at T13.
The code checkpoint above is a recovery anchor, not permission to overwrite later commits.
