# Trade Graph

An economical autonomous crypto-trading organisation that trades, records outcomes, learns from evidence and implements improvements under a Leader's direction.

**Status: paper runtime implemented on `cursor/trade-graph-r1-548a`. Paid calls and live trading stay disabled. Credentialed provider, exchange, and forward-paper checks are pending.**

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

Read [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md) for commands, synthetic results, and checks that are still pending credentials or an owner decision.

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run pytest
uv run trade-graph doctor
uv run trade-graph demo --offline
```
