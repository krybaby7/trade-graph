# Trade Graph

An economical autonomous crypto-trading organisation that trades, records outcomes, learns from evidence and implements improvements under a Leader's direction.

**The offline demo and continuous paper service are available. Defaults enable no paid calls or live trading.**

The installed runtime includes paper execution, durable scheduling, authenticated owner controls and
artifact improvement workflows. Default operation performs maintenance and reconciliation. Model-driven
paper operation requires approved private routing, provider credentials, explicit owner and runtime paid
permissions, and a separate real operating allowance. Read [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md)
for recorded evidence and [the operations runbook](docs/operations.md) for setup, funded observation and recovery.

Local implementation includes exact service binding and actual paper-process restart
proof, preregistered four-arm paper evidence collection with historical verification,
signed native fee components/rebates, append-only earlier-fill correction and protected
paper fee holds. Authenticated owner incident review and paper-to-live scope mapping
prepare the closed pilot lifecycle. The protected six-role service retains financial
and provider-budget continuity with a separate restart witness, and has an immutable
offline image plus a separately prepared provider proxy profile. Commissioned
offline plugin and Secretary application workflows exercise confined generation,
compatible sidecar migration, fenced health observation, restart and source rollback.
Actual current-public-data/funded observation, forward economics, authenticated venue
conformance and owner-approved intended-host deployment remain open. Live trading
and broader deployed Engineer authority remain closed.
See [protected deployment](docs/PROTECTED-DEPLOYMENT.md),
[plugin commissions](docs/PLUGIN-COMMISSIONS.md),
[offline plugin rollout](docs/PLUGIN-ROLLOUT.md),
[application commissioning](docs/APPLICATION-COMMISSIONS.md),
[application preparation](docs/APPLICATION-PREPARATION.md),
[financial continuity](docs/FINANCIAL-CHECKPOINTS.md) and the current implementation status.

**Default paper account: USD10,000. Reporting: EUR. Real AI/operating budget: separate, explicitly configured.** Paper gains are not real earnings or funding for API bills. Source/pricing review: 2026-09-29.

## Continue implementation

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

## Paper runtime

Python, FastAPI, a small dashboard, one durable scheduler, SQLite, a thin LangGraph layer, provider-neutral model adapters, and deterministic broker/execution/accounting services. Departments are responsibilities, not permanent processes.

The required R1 release must complete Research -> Trader -> paper execution -> evidence -> Learning/Optimisation -> Leader -> Improvement Engineer -> tested version -> further operation. No per-trade approval committee. Owner capital/spending/permissions remain software-enforced and outside agent control.

Read [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md) for commands, synthetic results, and checks that are still pending credentials or an owner decision.

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests
uv run pytest
uv run python scripts/verify_offline_acceptance.py --report /tmp/trade-graph-offline-evidence.json
uv run trade-graph demo --offline --work /tmp/trade-graph-demo-new
```

Use a fresh demo work directory. Its private `evidence.json` records checked outcomes and
request/receipt/version provenance. Scripted receipts are synthetic. Offline functional checks,
actual public/provider observation, economic evidence and live authority have separate gates.

Initialize a private paper account and inspect it before starting the service:

```bash
umask 077
install -d -m 0700 runtime
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph doctor --database runtime/trade_graph.sqlite
uv run trade-graph report --database runtime/trade_graph.sqlite --format json
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite --once
uv run trade-graph run --mode paper --database runtime/trade_graph.sqlite
```

Initialization creates USD10,000 virtual capital, a bounded paper mandate and the installed baseline
artifacts. Runtime staging uses private packaged artifacts rather than an operator's source checkout.
`run` continues until stopped; `--once` performs one tick and `--max-ticks 3` bounds a local exercise.
One service owns the database lock and lease. `doctor` and `report` read existing records without
network calls, credential probes or spending; unavailable/stale EUR valuations remain provisional.
The public Kraken/Frankfurter feed requires `run --public-data` or an explicit private configuration
setting. It uses no exchange trading key and preserves paper fill assumptions.

Run the authenticated dashboard in a separate terminal:

```bash
uv run trade-graph dashboard --database runtime/trade_graph.sqlite
```

Open `http://127.0.0.1:8000/login` and paste `session_token` from the private
`runtime/owner-session.json` file. Keep that file outside Git. New runtime directories
use mode 0700 and session files use 0600; existing database directories must be private.
The dashboard starts one loopback web worker. Its owner controls configure the real allowance and
paid permission; private runtime configuration must independently enable approved model routes.
Before stopping a nonflat account, choose its management policy as described in the runbook.

After explicit owner funding, paid permission, private credentials and public-data configuration,
stop the other paper service before starting the opt-in observation command:

```bash
uv run trade-graph soak --mode paper --database runtime/trade_graph.sqlite \
  --config runtime/paper-runtime.private.json --duration-seconds 60 \
  --report runtime/soak-evidence-new.json
```

Use a new private report file. The report distinguishes actual accrued expenses, forecast bounds,
synthetic receipts and unresolved billing, and records whether the requested duration completed.
Its economic result remains `insufficient_evidence`. A successful command does not establish
profitability, complete provider-invoice reconciliation or authorize live trading.
See [forward evaluation](docs/FORWARD-EVALUATION.md), [protected paper process boundary](docs/PROCESS-BOUNDARY.md),
[live readiness](docs/LIVE-PILOT-READINESS.md) and [offline plugin staging](docs/PLUGIN-STAGING.md).
