# Trade Graph

An economical, autonomous crypto-trading organisation that trades, observes outcomes, learns from evidence, and implements improvements under a Leader's direction.

**Repository status: implementation plan and orchestrator handoff. The trading application is not implemented yet.** Public-source verification date: 2026-09-29. Nothing in this repository is a promise of profitability or permission to trade in a particular jurisdiction.

## Start here

Give your coding orchestrator [IMPLEMENTATION-START-HERE.md](IMPLEMENTATION-START-HERE.md). Repository-wide implementation instructions are in [AGENTS.md](AGENTS.md). The dependency-ordered backlog is [planning/tasks.json](planning/tasks.json); honest implementation status belongs in [planning/progress.json](planning/progress.json).

The planning utilities below are intended to run immediately with standard-library Python; they do not call models, install services, or place orders:

```bash
python3 scripts/check_plan.py
python3 scripts/next_task.py
python3 scripts/cost_model.py
```

## Design documents

| Document | Responsibility |
|---|---|
| [Product and first release](docs/00-PRODUCT.md) | Objectives, autonomy, defaults, complete-loop scope |
| [Architecture](docs/01-ARCHITECTURE.md) | Processes, boundaries, project layout, deployment |
| [Graph and contracts](docs/02-GRAPH-AND-CONTRACTS.md) | Roles, inputs/outputs, tools, triggers, task ownership |
| [Data and accounting](docs/03-DATA-AND-ACCOUNTING.md) | Persistent entities, portfolio ledger, EUR performance, usage receipts |
| [Execution and recovery](docs/04-EXECUTION-AND-RECOVERY.md) | Broker interface, virtual fills, order state, pause semantics |
| [Learning and engineering](docs/05-LEARNING-AND-ENGINEERING.md) | Evidence, experiments, isolated implementation, activation, rollback |
| [Integrations](docs/06-INTEGRATIONS.md) | Credentials, permissions, official sources, limitations |
| [Costs](docs/07-COSTS.md) | Reproducible invocation model and small-capital economics |
| [Delivery and validation](docs/08-DELIVERY-AND-VALIDATION.md) | Phases, dependencies, acceptance tests, live gates |
| [Dashboard and operations](docs/09-DASHBOARD-AND-OPERATIONS.md) | API, controls, setup, maintenance, runbooks |
| [Sources](docs/90-SOURCES.md) | Official documentation and verification limitations |

## Intended runtime

Python, FastAPI, a small server-rendered dashboard, one durable scheduler, SQLite, a thin LangGraph workflow layer, provider-neutral model adapters, and a deterministic broker/execution/accounting kernel. One role is not one permanent service. Initial mode is virtual spot trading with public market data; no exchange trading credential is needed for that mode.

The complete first release includes Research -> Trader -> virtual execution -> evidence -> Learning and Optimisation -> Leader -> Improvement Engineer -> tested version -> further operation. No per-trade approval committee. Owner-defined capital, expenditure and authority limits remain outside agent control.

Examples and future command names in the specifications are implementation targets, not claims that the application already runs. The planning scripts are the only runnable software promised by this planning handoff.
