# Trade Graph

An economical autonomous crypto-trading organisation that trades, records outcomes, learns from evidence and implements improvements under a Leader's direction.

**Status: implementation plan and coding-orchestrator handoff. The trading application is not implemented yet.**

**Default paper account: USD10,000. Reporting: EUR. Real AI/operating budget: separate, explicitly configured.** Paper gains are not real earnings or funding for API bills. Source/pricing review: 2026-09-29.

## Start implementation

Give your coding orchestrator [IMPLEMENTATION-START-HERE.md](IMPLEMENTATION-START-HERE.md). Repository instructions are [AGENTS.md](AGENTS.md). [planning/tasks.json](planning/tasks.json) contains dependency-ordered deliverables and acceptance criteria; [planning/progress.json](planning/progress.json) records actual completion evidence.

Standard-library planning utilities (no paid calls, installs or orders):

```bash
python3 scripts/check_plan.py
python3 scripts/next_task.py --prompt
python3 scripts/cost_model.py
```

These prepare implementation work; they do not run a trading bot or launch a provider's coding agent.

## Specifications

| Document | Scope |
|---|---|
| [Product](docs/00-PRODUCT.md) | Objectives, autonomy and complete first release |
| [Architecture](docs/01-ARCHITECTURE.md) | Processes, security boundaries and target layout |
| [Graph and contracts](docs/02-GRAPH-AND-CONTRACTS.md) | Roles, tools, inputs/outputs, triggers and task lifecycle |
| [Data and accounting](docs/03-DATA-AND-ACCOUNTING.md) | Native ledger, EUR valuation, economic results and paid receipts |
| [Execution and recovery](docs/04-EXECUTION-AND-RECOVERY.md) | Paper/live contract, uncertain orders and pause profiles |
| [Learning and engineering](docs/05-LEARNING-AND-ENGINEERING.md) | Evidence, implemented changes, tests, activation and rollback |
| [Integrations](docs/06-INTEGRATIONS.md) | APIs, credentials, permissions, costs and limitations |
| [Costs](docs/07-COSTS.md) | Reproducible invocation and trading-friction estimates |
| [Delivery and validation](docs/08-DELIVERY-AND-VALIDATION.md) | Phases, acceptance catalogue and paper/live gates |
| [Dashboard and operations](docs/09-DASHBOARD-AND-OPERATIONS.md) | Views, controls, target API/CLI and runbooks |
| [Paper capital](docs/10-PAPER-CAPITAL.md) | USD10,000 update, native currencies and real-cost separation |
| [Sources](docs/90-SOURCES.md) | Official documentation and verification limitations |

## Intended runtime

Python, FastAPI, a small dashboard, one durable scheduler, SQLite, a thin LangGraph layer, provider-neutral model adapters, and deterministic broker/execution/accounting services. Departments are responsibilities, not permanent processes.

R1 completes Research -> Trader -> paper execution -> evidence -> Learning/Optimisation -> Leader -> Improvement Engineer -> tested version -> further operation. No per-trade approval committee. Owner capital/spending/permissions remain software-enforced and outside agent control.

Read [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md) for what was actually checked. Example future CLI commands in the specifications are implementation targets, not existing runtime features.
