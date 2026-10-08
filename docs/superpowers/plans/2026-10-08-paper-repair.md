# Paper repair implementation plan

Goal: repair the current subscription paper installation without replacing financial history or evidence.
Architecture: retain the protected financial controller and current database/witness. Independent worktrees repair public history, typed model context/diagnostics and the authenticated manifest transition. One integration owner controls schemas, deployment and continuity verification.
Tech stack: Python 3.12, SQLite, pytest, protected Docker runtime.

- [x] Inspect registered worktrees; use first-paper-cycle rather than main checkout.
- [x] Apply authenticated owner MANAGE_ONLY; confirm achieved managing and retain consistent private database/witness/owner backup and attempt evidence.
- [ ] History worker: reproduce startup-relative hourly drift and per-symbol retry delay in subscription_operation.py; add deterministic-clock failing tests, align completed-hour input barrier and retry failures; retain bounded feed diagnostics. Run focused history/service tests and commit.
- [ ] Context worker: expose findings.source_ref eligibility from snapshot.sources separately from evidence_refs; retain atomic validation. Add field-scoped native account/order versus EUR valuation status and bounded native failure diagnostics, with focused regressions and commit.
- [ ] Continuity worker: implement protected owner-authenticated manifest transition over current witness and complete commitments. Reject invalid/stale authorization and active workers; test crash recovery, historic identities and tampering before commit.
- [ ] Integrate commits, run focused cross-component regressions, independently review continuity and resolve findings.
- [ ] Build exact protected image; verify actual isolation, single-model/no-fallback/>40% guard and unchanged owner settings. Stop old worker only for exclusive supported transition; preserve current database inode and witness, recover through supported route on interruption.
- [ ] Obtain fresh native quota admission, resume through owner controls, verify actual Research publication and feature-ready Trader snapshot. Preserve ordinary schedules and no-action discretion.
- [ ] Scan staged changes and publish sanitized checkpoint; report actual results and remaining failures without private journals or quota readings.
