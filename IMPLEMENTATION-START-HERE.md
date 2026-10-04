# Implementation orchestrator entrypoint

## Assignment

Implement Trade Graph in this repository: an autonomous cost-aware crypto-trading organisation with Leadership (Leader, software-first Secretary, Improvement Engineer), Research, Trading (one AI Trader plus deterministic execution), Learning and Graph Optimisation (software accounting/journal plus periodic analyst).

The default virtual account is **USD10,000** following the owner's updated instruction; initial reporting remains EUR. EUR100 is not an operating restriction. Keep native USD and actual FX provenance, with USD spot pairs initially. Paper capital is not an API allowance or live authorization. See docs/10-PAPER-CAPITAL.md.

The objective is growth after trading friction, AI and attributable operating expenses. Profitability must be evaluated, not assumed. Deliver the smallest coherent release that completes the whole loop, including an Engineer that writes/tests an actual authorised change and a Leader that activates it within software-enforced boundaries. Advice-only improvements are incomplete. Individual trades need no Leader approval.

## Read completely

AGENTS.md; docs/00-PRODUCT.md through docs/10-PAPER-CAPITAL.md; planning/tasks.json; planning/progress.json; IMPLEMENTATION-STATUS.md. Consult docs/90-SOURCES.md for external API details and docs/DECISIONS.md for defaults. config/defaults.example.json and planning/cost-assumptions.json are concrete examples, not credentials or paid/live authority.

The specifications define required behaviour; current official documentation controls external wire formats/capabilities. Preserve requirements while adapting integration details and recording deviations. Do not silently remove a role or the automatic implementation loop.

## Begin

Inspect current Git state and preserve newer implementation. Run:

```bash
python3 scripts/check_plan.py
python3 scripts/next_task.py
python3 scripts/next_task.py --prompt
python3 scripts/cost_model.py
```

These standard-library utilities validate/select planning work and calculate estimates. They neither start trading nor invoke a coding provider. T00–T16 have evidence-backed completion; preserve their implementation and continue the remaining T17–T22 work from the current verified checkpoint. Read the status and task evidence before selecting a slice. Continue concrete source, command and test implementation while preserving the separate funded, external-evidence and owner-authority gates.

After contracts are agreed, independent broker/data, provider/budget and presentation tasks may run in separate worktrees. One integration owner coordinates contracts, migrations and dependency files. Parallel implementation is not an instruction to run permanent runtime agents for every package.

## Delivery protocol

Claim work in progress.json; implement concrete deliverables; run acceptance tests; commit checkpoints; record actual commands/results/commits. Finish a synthetic offline loop before paid integrations, then substitute public data and budgeted provider adapters. R1 includes both OpenAI and Anthropic adapters, but one credentialed provider is sufficient to operate. Missing credentials do not block fixtures or justify a fabricated integration result.

The offline scenario starts a paper account, obtains research, decides/trades, records fills/fees and synthetic usage, creates a lesson with counterevidence, commissions an improvement, writes/tests a real artifact in isolation, activates it and attributes a later decision to the new version. Show restart recovery without duplicate execution and a rejected change whose expense/evidence persists. Test USD10,000/EUR conversion and separation of real expenses from paper resets (A43/A44).

## Handoff and live boundaries

Keep IMPLEMENTATION-STATUS.md current with branch/commit, verified startup/test commands, known failures, missing credentials and next eligible task. Preserve unfinished work. Do not mark a feature done merely because its spec/mock exists.

Credentialed paper operation requires separate owner-funded API configuration. Live enablement requires separately established eligibility, venue capabilities, permissions and owner capital authorization. A completed task or a profitable paper account cannot bypass those gates. Broader code-engineering authority follows isolation phases; useful automatic artifact implementation already belongs in R1.
