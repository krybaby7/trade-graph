# Paper repair implementation plan

Goal: repair the current subscription paper installation without replacing financial history or evidence.
Architecture: retain the protected financial controller and current database/witness. Independent worktrees repair public history, typed model context/diagnostics and the authenticated manifest transition. One integration owner controls schemas, deployment and continuity verification.
Tech stack: Python 3.12, SQLite, pytest, protected Docker runtime.

- [x] Inspect registered worktrees; use first-paper-cycle rather than main checkout.
- [x] Apply authenticated owner MANAGE_ONLY; confirm achieved managing and retain consistent private database/witness/owner backup and attempt evidence.
- [x] History worker: reproduce startup-relative hourly drift and per-symbol retry delay in subscription_operation.py; add deterministic-clock failing tests, align completed-hour input barrier and retry failures; retain bounded feed diagnostics. Run focused history/service tests and commit.
- [x] Context worker: expose findings.source_ref eligibility from snapshot.sources separately from evidence_refs; retain atomic validation. Add field-scoped native account/order versus EUR valuation status and bounded native failure diagnostics, with focused regressions and commit.
- [x] Continuity worker: implement protected owner-authenticated manifest transition over current witness and complete commitments. Reject invalid/stale authorization and active workers; test crash recovery, historic identities and tampering before commit.
- [x] Integrate commits and independently review continuity; bounded reads and expiry-boundary findings resolved. Independent final selection: 108 passed. Final cross-component rerun recorded in repair evidence.
- [ ] Build exact protected image; verify actual isolation, single-model/no-fallback/>40% guard and unchanged owner settings. Stop old worker only for exclusive supported transition; preserve current database inode and witness, recover through supported route on interruption.
- [ ] Obtain fresh native quota admission, resume through owner controls, verify actual Research publication and feature-ready Trader snapshot. Preserve ordinary schedules and no-action discretion.
- [x] Scan staged changes and prepare sanitized checkpoint; 363 integrated regressions and 108 independent continuity checks passed. Remote commit verification is reported in the handoff; no private journals or quota readings are published.


Storage recovery gate discovered during the hold:

- [x] Preserve the damaged current database, original witness/owner files,
  post-stop evidence and verified consistent hold-time backup privately. Leave
  the authoritative inode/content unchanged. Stop the failed dashboard after
  worker exit; do not claim collection/management/dashboard still running.
- [x] Reproduce and repair raw descriptor closure releasing SQLite transaction
  locks; retain descriptors across Database owners/connections/leases and test
  controller, service, status, transition and path-race behavior.
- [ ] Obtain a separate storage recovery decision, validate recovered state and
  reviewed SQLite runtime, then perform protected transition/deploy/resume. The
  verified backup is not silently restored; the invalid forensic header-only
  candidate is never authoritative. No real repaired Research/Trader result yet.
