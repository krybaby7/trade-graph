# Authenticated paper database recovery implementation plan

The owner explicitly authorized the missing recovery path and a separate private candidate. Root is integration/deployment owner; this branch handles source and synthetic tests only.

1. Add failing integration tests that copy a paused paper database while keeping the original authenticated witness, reject normal use of the changed inode, and require authenticated recovery to preserve facts and existing scopes.
2. Add migration 0023 for immutable authenticated recovery receipts. Add bounded witness schema 3 and recovery-chain validation; keep normal identity checks strict.
3. Implement an offline inspect/apply operator. Bind exact candidate inode, original witness payload and MAC, original inode, deployment, complete all-table logical snapshot, native accounting scans, operator-reviewed incident evidence digest and short expiry. Require owner MANAGE_ONLY and exclusive database/witness ownership.
4. Append one receipt and atomically publish a copied witness retaining original scopes and transition digests, with new identity and ordered recovery digest. Retry only identical committed approval and unchanged facts; reject missing/tampered/newer evidence.
5. Test incorrect approval/key/identity, lost checkpoints, state drift, interruption before/after commit, expiry, immutable rows, repeated recovery and manifest transition after recovery. Run focused existing checkpoint/transition/budget tests and Ruff. Commit sanitized source only.

Recovery cannot prove records that were never committed by the old witness. Root must independently compare incident evidence, exact attempt identities and backup cutoff before distributing approval. No AI, paid call, trade, fresh authority or mutation of original damaged files is part of this implementation.
