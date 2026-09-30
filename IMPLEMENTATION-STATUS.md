# Implementation status — recovery and authorization review

Updated: 2026-09-30. **Partial offline prototype, not complete autonomous R1.**
Branch: `cursor/trade-graph-r1-548a`; PR #1 stays draft and unmerged.
Paid model calls, live trading and executable Engineer extensions stay disabled.
Default USD10,000 virtual capital, EUR reporting; real budgets remain separate.

## Recovery verified

Remote head at entry: `0b5ba2b18384b717b9a9e99388622b4ced2a910d` (parent `18ab1b25773a7be67b16638ddc40c0a2c275a4af`).
Previous-session local implementation and recovery archive were unavailable in this fresh runtime.
CI source artifact 11096454025 from run 36718371771 was extracted and its complete Git tree
verified as `223c22d74106014389d994916162a517bcdd90ea`, identical to the remote head.
Only that historical remote source was recovered, not the previously reported new implementation.
The previous session's 45/23/185 local-test claims are not verification of this tree.

## Current work

T12 is reopened and in progress: replace helper-only Leader/Secretary with persisted reports,
bounded digests, scheduled gateway-backed decisions, scoped consultation and authorized commissions.
The six reproduced defects require regression-backed activation, engineering and CSRF repairs.
T13–T15 are reopened; small passing test subsets do not establish task acceptance.
T16 is not dependency-ready. Existing foundations and all historical evidence are preserved.

## Acceptance still open

- T13: full Engineer worker crash/retry/failure lifecycle and authorization. The restricted
  artifact subprocess is not an OS sandbox; arbitrary executable work remains disabled.
- T14: artifact-consumer reload, controlled restart, observation and automatic rollback.
  Pointer switching alone is not delivery of these requirements.
- T15: audit the full dashboard/API/owner-control specification, not just CSRF.
- T16 onward: complete offline fault catalogue, packaged continuous runtime and real diagnostics,
  separately funded paper soak, economic evaluation and separately authorized live progression.

## Verification

Local `uv sync --frozen --group dev --python 3.12` could not fetch the interpreter because
this runtime has no network DNS. Python 3.13 available-dependency tests are supplemental only.
Full locked Python 3.12 checks must run in credential-free GitHub CI without exclusions.
No paid provider calls or real orders have been authorized or performed.

## Historical record

`docs/history/STATUS-0b5ba2b.md` preserves the previous status verbatim for audit only.
Its T12–T15 completion labels and T16-next statement are superseded, not current acceptance.
`planning/progress.json` retains prior evidence under reopened tasks.
