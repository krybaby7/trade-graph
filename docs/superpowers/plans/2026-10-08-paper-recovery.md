# Paper installation recovery implementation plan

> For agentic workers: execute the already owner-authorized recovery in isolated worktrees with one integration owner and independent review.

**Goal:** Recover the existing paper installation into a new private location with authenticated financial continuity, leaving AI paused.

**Architecture:** Preserve original damaged state and owner files; use the newest verified backup plus exact recoverable newer records. Authenticate an explicit identity recovery with the original key and witness commitments; then use the existing manifest transition for reviewed source deployment. Install a hash-pinned SQLite runtime and restart only management/public collection/dashboard.

**Tech stack:** Python 3.12, SQLite, root-distributed owner approval, offline Docker image.

- [x] Locate actual registered WSL operating worktree and containers.
- [x] Preserve original database, witness, owner files, keys, backup and incident evidence privately with matching hashes.
- [x] Independently audit backup integrity/FK/Decimal accounting, 15 attempts and 13 invocations, newer incident records.
- [x] Implement/test additive authenticated identity recovery without checkpoint reset or model RPC.
- [x] Pin/build/test repaired SQLite runtime and lock regressions in actual installed image.
- [x] Integrate independent reviews; reject recovery for unexplained missing financial/attempt history.
- [ ] Apply supported recovery and manifest transition in new private state/owner locations.
- [ ] Inspect and launch one MANAGE_ONLY management worker/dashboard; refresh public data; verify no new attempts.
- [ ] Record actual deployed identity, remaining limits and exact future authenticated resume step; publish sanitized source only.

The user explicitly authorizes preparing and using a verified separate recovery copy and implementing missing recovery support. No additional design approval is needed within that scope. Original corruption cause remains unknown. Live orders, separately billed APIs, paid extras and AI operation remain disabled.

Operational promotion/restart remains deliberately blocked: specifically missing newer financial payloads fail the owner's continuity gate. Exact IDs and uncertainty are retained privately. Original files and previous image remain unchanged.
