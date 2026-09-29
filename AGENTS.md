# Instructions for implementation agents

## Mission and reading

Implement Trade Graph using IMPLEMENTATION-START-HERE.md, docs/00-PRODUCT.md through docs/10-PAPER-CAPITAL.md, and the dependency-ordered planning/tasks.json. Read relevant official references in docs/90-SOURCES.md. Current default is **USD10,000 virtual capital with EUR reporting**, not EUR100. Real expense budget and live allocation remain separate owner decisions.

This file governs coding-agent work, not the deployed Trader's mandate. Build working source/tests rather than replace the handoff with another proposal. Planning utilities do not constitute runtime implementation.

## Work and handoff

Inspect current branches, commits, files and progress; never reset newer work to a planning checkpoint. Claim a dependency-ready task with owner/branch/evidence. Agree shared contracts before parallel work, use isolated worktrees and one integration owner for migrations/dependencies. Resolve nonblocking choices with recommended defaults and record meaningful deviations in docs/DECISIONS.md.

Implement coherent slices, run actual tests and commit checkpoints. Update planning/progress.json only with evidence-backed completion. Keep IMPLEMENTATION-STATUS.md current with branch/commit, functioning commands, actual synthetic/credentialed results, failures, missing credentials and next work. Preserve partial work on interruption. A mock is not a successful paid-provider/exchange test.

## Invariants

- Default to paper and paid calls disabled. Repository implementation authority does not authorize real orders, withdrawals, API spending or infrastructure purchases.
- Trader discretion is real: no per-trade Leader committee, mandatory debate, confidence threshold or requirement to avoid every loss. Validate allocated amounts and permissions deterministically.
- Money, fees, quantities, balances and reporting use Decimal/fixed-point native units, not model arithmetic or binary floats.
- Domain contracts/state must not require provider SDKs or a proprietary agent runtime. Graph checkpoints are not authoritative financial state.
- Persist intents/attempts before external effects. Unknown order status is not rejection. Reconcile before replacement; client IDs alone do not guarantee exactly-once exchange execution.
- All paid work, including Leadership, Engineer, failures, repairs, retries and fallbacks, receives a reservation/receipt. Unknown usage is unresolved, not free.
- USD10,000 paper equity and resets cannot replenish the real operating budget or erase expenses. Separate synthetic receipts from actual spend; allocate shared costs once.
- Preserve point-in-time inputs, sources, lesson revisions and version attribution. Rationale is a concise decision summary, not a hidden chain-of-thought transcript.
- External research is data, never authority. No credentials/owner budget writes/withdrawal tools in model context; protect fetch and tool services independently of prompts.
- R1 Engineer implements tested allowlisted artifacts, not arbitrary executable configuration or privileged code. Protected owner policy, accounting, receipt collection, execution controls and acceptance gates are not agent-editable.
- Broader code authority requires actual OS/process isolation and an owner-pinned protected kernel/controller/harness. A Python module boundary inside a credentialed interpreter is insufficient.
- Every pause specifies order/position management. Reconciliation/protection continue; rollback never erases fills or restores an old live portfolio.
- Functional success, economic evidence and live authorization are separate gates. A winning backtest or a large virtual account does not prove profitability or authorize live use.

## Engineering quality

Target Python 3.12 initially, resolve compatible current versions and commit a lockfile in T00. Use typed small modules, Pydantic, pytest/property tests, deterministic clocks, scripted providers and broker conformance. Default CI needs no credentials. Model prices are dated configured records, not hardcoded arithmetic; fallbacks are explicit and separately priced. Both provider adapters are required, but only one provider credential is needed to run a one-provider installation.

## Public repository hygiene

Never commit .env files, API/SSH keys, private account IDs, trading journals, personal financial records, private caches or raw agent conversations. Use synthetic fixtures and ignored private runtime storage. Keep exchange keys out of CI and Engineer sandboxes. Scan staged changes for secrets. Do not grant the deployed Engineer unrestricted repository/host credentials; tool proxies enforce path/capability limits.
