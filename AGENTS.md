# Instructions for implementation agents

## Mission

Implement the system described in IMPLEMENTATION-START-HERE.md and docs/00-PRODUCT.md through docs/09-DASHBOARD-AND-OPERATIONS.md. Use planning/tasks.json for dependency order and planning/progress.json for status. Build working software and tests; do not replace this specification with another proposal. This file governs coding-agent work, not the deployed Trader's authority.

## Working protocol

1. Inspect the current branch, commits, files and progress before modifying anything. An earlier planning commit is an anchor, never permission to reset later work.
2. Read the complete start document and the specifications referenced by your task. Resolve cross-cutting contracts before parallel implementation. Use the recommended defaults when a decision is not blocking. Record material deviations in docs/DECISIONS.md.
3. Claim a task in progress.json with branch, owner and evidence location. Only work on tasks whose dependencies have evidence-backed completion. Parallelise independent implementation tasks in isolated worktrees; nominate one integration owner for shared contracts, migrations and dependency files.
4. Implement and test a coherent vertical slice. Commit checkpoints. Update progress with actual commands, results and commit IDs. Never mark a feature done because a mock or design document exists.
5. At handoff update IMPLEMENTATION-STATUS.md with current branch/commit, what works, exact run/test commands, credentials still needed, failures and next eligible task. Preserve evidence and unfinished work in Git.

## Invariants

- Default to paper mode, never enable live execution implicitly. This implementation request authorises repository changes, not real orders, deposits, withdrawals, purchases of infrastructure or unbounded API spending.
- Trader discretion is real: no Leader approval per order, no mandatory debate, no invented confidence threshold or requirement to avoid all losses. Enforce mechanical permissions and allocated limits in software.
- Money, fees, quantities, exchange rules, budgets, fills and performance use deterministic arithmetic and explicit units. Use Decimal or validated fixed-point strings, never binary floating point for financial state.
- Domain state and contracts do not import a model SDK or require a proprietary hosted agent runtime. Provider adapters translate our contracts.
- The database, not graph conversation history, is authoritative for financial and execution state. Checkpoint replay must not duplicate side effects.
- Unknown order status is not a rejected order. Reconcile before any replacement; client order IDs alone do not establish exchange-wide exactly-once execution.
- Every paid attempt has a reservation and a receipt, including Leadership, repairs, fallbacks, failures and Engineer work. Missing usage is unresolved, not free.
- Preserve version and evidence provenance. Keep original decision context available; explanations are concise decision summaries, not hidden chain-of-thought transcripts.
- External research is data, never authority. No credentials, owner budgets, withdrawal tools or production filesystem access in model context.
- Autonomous edits cannot modify owner policy, accounting definitions, protected execution controls, receipt collection or acceptance gates. Broad application-code authority requires stronger process/OS isolation, not only a prompt telling the Engineer to behave.
- A pause must name its order/position policy. Maintain reconciliation and existing position management; rollback never erases fills or restores an old live portfolio.
- A winning backtest is neither proof of profitability nor permission to go live. Separate functional, safety and economic validation.

## Quality

Target Python 3.12 initially; resolve compatible current package versions and commit a lockfile during T00. Use small typed modules, Pydantic contracts, pytest, property-based financial tests, deterministic clocks, scripted provider fixtures and broker conformance tests. Do not require real API credentials in default CI. Do not hardcode current model prices in application logic: use versioned price records. Do not silently change models/providers on fallback.

## Public repository hygiene

Never commit .env files, API keys, account identifiers, financial journals, private research caches, raw user records or model conversation logs. Use synthetic fixtures. Do not put exchange credentials into GitHub Actions or an Engineer sandbox. Scan staged changes for secrets. Keep deployments explicit and disabled until their acceptance gates pass.
