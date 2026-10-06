# Kraken hourly history implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for isolated implementation and review. The owner's explicit request and CRYPTO-NEXT-STEPS.md authorize this source slice.

**Goal:** Supply the installed Research and Trader workflows with bounded, attributable, point-in-time hourly BTC/USD and ETH/USD features.

**Architecture:** Retain immutable completed candle revisions in an additive SQLite table. Collect only public Kraken OHLC through the bounded public transport, exclude its final uncommitted candle, and compute Decimal features from the exact contiguous hourly slots ending at the context's as-of hour. Persist the resulting concise context through the existing worker snapshot/model invocation path.

**Tech stack:** Locked Python 3.12, Pydantic, SQLite, Decimal, pytest, existing bounded HTTP transport.

- [ ] Preserve the Windows checkout and owner-private runtime/evidence; record only the verified historical read-only observation's redacted projection in status. Keep T17–T22 acceptance pending.
- [ ] Claim the dependency-ready T04/T10 continuation under the root integration owner; use separate history and collector worktrees.
- [ ] Write failing synthetic tests for candle validation, exact features, missing/gapped/stale slots, source separation, immutable revisions, deduplication and restart. Implement contracts, storage and feature calculation in contracts/price_history.py and application/price_history.py.
- [ ] Root registers additive migration 0017 and Alembic revision, preserving earlier data. No dependencies or lockfile changes.
- [ ] Write failing scripted transport tests; implement adapters/market/kraken_history.py and application/collect_price_history.py. One public GET per symbol, 1–719 hours, 1 MiB response, ten-second timeout, no pagination/retries or private transport.
- [ ] Wire the fixed public collection command into cli.py. Require an existing private runtime DB, never initialize/reset an account.
- [ ] Write failing actual handler/worker tests; extend TraderHandler and ResearchHandler with the active templates' requested fields and provenance. Retain their snapshot/request values across later observations and restart. Research findings cite supplied feature snapshots without inventing publication times.
- [ ] Verify exact Arithmetic at Decimal precision 50, ROUND_HALF_EVEN: SMA20/50 close means; 20-hour pullback fraction from high; 24-hour high/low and range midpoint. Also define starter fallback returns (2/5 closes), pullback_depth alias and midpoint_distance. Unsupported requested fields remain explicit.
- [ ] Document formulas, units, lookbacks, receipt-time availability and Kraken's 720-entry limit with the current official reference. Keep synthetic observations separate from actual public collection.
- [ ] Run relevant Linux/WSL checks, independent review and staged secret/hygiene checks. Commit and fast-forward publish the sanitized checkpoint without resetting either owner's checkout.

Root owns migrations, contexts, CLI, progress and documentation. History and collector agents own disjoint source/tests; root integrates their commits and tests the whole slice. The original Windows status addition already exists unchanged in the latest published source. No private account requests, runtime resets, paid calls or live trading are part of this work.
