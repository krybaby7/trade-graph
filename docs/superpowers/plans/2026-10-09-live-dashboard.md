# Live operating dashboard implementation plan

Owner request: implement and deploy a simple live home focused on operating
correctness and whether trading covers attributable costs. This continuation is
dependency-ready under T15/T17/T21; acceptance gates remain unchanged.
Integration owner: Codex, branch `codex/live-dashboard-20261009`, base `493c25c`.
The running image is the capacity/logging repair; preserve that source and all
operating records. Developer tests use fresh temporary databases only.

Architecture: extend the existing FastAPI/Jinja dashboard, with server Decimal
projections and one SQLite read snapshot per overview/export. Retain paper mode,
Sol-only, empty fallback, quota admission and existing schedules. Use persisted
quota observations; rendering and export never invoke inference or metadata CLI.

- [x] Reproduce HTML authentication failure; add redirect tests and fix HTML
  navigation while retaining API 401, cookie CSRF and owner authority.
- [x] Project department native/application outcomes, due times, retries, linked
  work and reported attempt usage exactly once. Keep coverage and shared quota
  separate; expose safe retained decision evidence with selected source links.
- [x] Add append-only owner expense evidence and an explicit bill/graph/department
  allocation policy. Preserve per-call usage uncertainty, shared-bill identity,
  historical FX and period completeness. Do not replenish operating budgets.
- [x] Render a modestly refreshing home with distinct operational/economic
  indicators, exposure, result, expenses, activity and recovery limitations.
- [x] Add authenticated read-only review export containing complete scoped safe
  records in one snapshot, independently timestamped external observations,
  resource warnings, coverage, schedules, failures and explicit limitations.
- [x] Run focused authentication/accounting/usage/snapshot regressions, lint,
  hygiene, and actual Windows browser desktop/mobile/offline checks on isolated
  synthetic storage. Build and independently verify an immutable image.
- [x] Pause new AI with management continuing, drain in-flight work, checkpoint
  original financial continuity, stop/release leases, transition the authenticated
  manifest, restart exactly one worker, verify and resume normal paper operation.
  Preserve database inode, witness, history, failures and accepted recovery gap.
- [x] Update status/progress/decisions with actual evidence, scan staged changes
  for secrets, commit and publish sanitized implementation only.
