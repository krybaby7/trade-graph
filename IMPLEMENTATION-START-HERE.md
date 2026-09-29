# Implementation orchestrator entrypoint

## Assignment

Implement Trade Graph in this repository. It is an autonomous, cost-aware crypto-trading organisation with Leadership (Leader, software-first Secretary, Improvement Engineer), Research, Trading (one AI Trader plus deterministic execution), Learning, and Graph Optimisation (software accounting/journal plus periodic AI analyst).

The aim is growth of allocated capital **after trading friction, AI usage and attributable operating expenses**. Profitability is a hypothesis to evaluate, not an assumption. Begin with virtual funds and preserve the same broker contract for later live operation. The owner should see decisions, spending, positions, performance, lessons and improvements through a dashboard.

Deliver the smallest coherent release that actually completes the whole loop, including an Engineer that writes and tests an authorised change and a Leader that activates it automatically within software-enforced boundaries. A system that only recommends improvements is incomplete.

## Read order

Read AGENTS.md, all ten numbered specifications (docs/00-PRODUCT.md through docs/09-DASHBOARD-AND-OPERATIONS.md), planning/tasks.json, planning/progress.json and IMPLEMENTATION-STATUS.md. Read docs/90-SOURCES.md when implementing provider/exchange/hosting integrations. Read config/defaults.example.json and planning/cost-assumptions.json as concrete planning defaults, not production credentials or proof of API access.

The specifications are normative design requirements. Official external API documentation is authoritative for wire formats and current capabilities. If these conflict, preserve the product requirement, adapt the integration, and record a short decision with evidence. Do not silently remove a role or the automatic improvement loop.

## First actions

Inspect current Git state and preserve any newer implementation. Run:

```bash
python3 scripts/check_plan.py
python3 scripts/next_task.py
python3 scripts/cost_model.py
```

Select the next dependency-ready task, beginning with T00 in an untouched checkout. Read its referenced specifications, implement its deliverables and acceptance criteria, and record results in planning/progress.json. Implement through the R1 gate; the backlog distinguishes first-release tasks from later live/broader-engineering tasks.

These commands currently inspect the plan and calculate illustrative costs. They do not start a trading system. During implementation add the real CLI, server, migrations, tests and deployment files.

## Recommended implementation strategy

Build domain contracts and the financial kernel before paid agents. Produce a synthetic, offline complete-loop test before requiring API keys. Then substitute public market data and budgeted model adapters. Both OpenAI and Anthropic adapters belong in R1; an installation can run with only one provider configured. A missing second credential must not block offline tests or one-provider operation.

Use one coordinator for integration. Suggested parallel boundaries after T00/T01: broker/data work; provider/budget work; dashboard projections. Never concurrently edit the authoritative schema or migration ordering without coordination. This is not an instruction to create permanent model subagents for every package.

## Done means

The offline acceptance scenario creates a virtual account; obtains research; makes a decision; records an order and fill; accounts for fees and paid-usage fixtures; records a lesson with counterevidence; has the Leader commission a change; has the Engineer write and test an actual artifact in isolation; activates a version; processes another decision attributed to that version; and survives a restart without duplicate execution. A rejected change is also demonstrated, with retained evidence and charged costs.

A separate credentialed paper soak runs the same pipeline on current public data and actual model receipts under the owner-approved operating budget. Offline fixtures are sufficient for functional CI, not evidence of trading profitability or live integration success.

## Completion and interruption discipline

Commit changes and keep IMPLEMENTATION-STATUS.md current. Include exact startup commands that actually work, checked tests, unresolved integration gates and the next task. When blocked on credentials, implement the adapter and fixture tests and label credentialed verification pending. Do not claim a service was tested merely because its documentation was read.

The owner must separately establish legal eligibility, exchange account access and a live allocation before any live enablement. The implementation orchestrator does not have authority to bypass those gates.
