# 01 — Architecture and project layout

## Recommendation

Use a modular Python application rather than a microservice per department. Start with Python 3.12, FastAPI, Pydantic, SQLAlchemy/Alembic, SQLite, a thin LangGraph layer and a server-rendered dashboard using Jinja plus small browser scripts. Resolve compatible exact dependency versions in T00 and commit a lockfile. Do not add Redis, Celery, Kubernetes, a paid tracing service, a vector store or LangSmith as prerequisites.

One long-lived `core` process hosts the API, deterministic scheduling, market ingestion, graph task worker, accounting and execution. Run one server worker and enforce an OS/process lease; do not let web-worker replication create multiple schedulers. A separate ephemeral `engineer-sandbox` executes tests against copied artifacts and synthetic/read-only evidence. It has no production credentials, database, Docker socket or owner-policy mount.

SQLite is a deliberate single-host R1 trade-off: use WAL, foreign keys, busy timeouts, short transactions, a single logical financial writer and consistent online backups. Do not put the database on a network filesystem. WAL permits readers alongside a writer but does not create parallel writers [S05]. The LangGraph docs distinguish checkpoint state from cross-run stores and position SQLite checkpointers as local/development storage [S04]. Accordingly use SQLite for the small paper release, not as an assertion of distributed production readiness. Move to PostgreSQL before multiple hosts/writers or stronger live-service requirements warrant it.

## Layers

**Domain:** immutable typed entities, Decimal values, mandate validation, accounting, order transitions, evidence and version references. No network calls, model SDK imports or dependence on graph checkpoints.

**Application:** use cases (decide, enqueue intent, reconcile, review lessons, reserve spending, commission/activate change), transaction boundaries, task ownership, event/outbox semantics and authorisation.

**Adapters:** model providers, public market feed, broker, research search/fetch, local storage, sandbox runner, Git/GitHub and dashboard transport. Adapters translate a shared contract and never silently invent unsupported exchange semantics.

**Workflow:** small LangGraph graphs invoking application use cases. State contains task IDs, input record IDs, cursors, step outcomes and version fingerprints, not the entire organisation's transcripts. SQLite business tables are authoritative. Checkpoints help resume reasoning steps; application idempotency protects side effects even if a node re-executes.

**Presentation:** versioned API, server-rendered pages, server-sent events or polling for updates. A graph status panel is a projection of real tasks/events, not an independent invented state.

## Target directory structure

```text
src/trade_graph/
  domain/             # money, orders, mandates, research, lessons, versions
  application/        # transactional use cases and services
  kernel/             # protected authority, budget, ledger, execution invariants
  contracts/          # Pydantic DTOs and exported JSON schemas
  orchestration/      # graphs, durable scheduler, leases, context assembly
  roles/              # leader, research, trader, learning, optimisation, engineer
  adapters/
    models/           # openai.py, anthropic.py, scripted.py
    market/           # kraken_public.py, replay.py, normalized instruments
    brokers/          # paper.py, later kraken_live.py; capability registry
    research/         # feeds.py, fetch.py, optional provider_search.py
    persistence/      # repositories, SQLAlchemy models, outbox
    engineering/      # worktree.py, sandbox.py, validator.py, activation.py
  api/                # auth, owner controls, projections, SSE
  web/                # templates and static assets
  cli.py
migrations/
tests/{unit,property,contracts,integration,recovery,security,e2e}/
tests/fixtures/       # synthetic and documented point-in-time replay data
strategies/templates/ # explicit deterministic feature/strategy definitions
prompts/              # small per-role prompts, versioned assets
config/               # public examples; no active owner policy or secrets
scripts/              # planning tools, later maintenance commands
planning/             # tasks, status, price/cost assumptions
runtime/              # ignored local data: DB, journals, checkpoints, artifacts
```

This is the intended layout; do not populate fake implementations just to make directories appear complete.

## Trust boundary evolution

R1 automatic changes are data artifacts: strategy parameters, templates within a validated grammar, role prompts, schedules, reporting layouts and already-approved model assignments. Artifact activation is a protected application operation with a schema, path and capability allowlist. Models cannot supply Python expressions, shell fragments, SQL or arbitrary import paths in configuration.

Before automatically deploying new application code, extract a protected `control-kernel` process. It alone owns exchange credentials, accounting, budget reservations and authoritative policy. Run mutable graph/strategy code under a different OS identity/container with narrow authenticated RPC. Engineer changes cannot replace the kernel image, modify its mounts, alter CI gates or obtain the kernel credential. Test gates and deployment controller execute from an owner-pinned trusted revision, not from candidate code. A Python module boundary inside a process with exchange keys is not a security boundary.

The Leader can instruct the protected controller to pause, activate a permitted signed/hashed artifact and restart the mutable graph. It cannot restart the owner-halted kernel or install an unapproved permission class. Broader authority is an explicit owner capability grant; routine changes within that grant need no human review committee.

## Transactions and recovery

Use an append-only business event table plus normal relational projections, not full event sourcing of every market tick. Each critical transaction atomically writes state, its event and any outbox message. The scheduler claims leased tasks with a compare-and-set/transactional update. A crashed lease expires and a successor resumes from durable state.

External requests cannot share the database transaction. Persist a stable intent/request ID first, issue the call outside the transaction, then record its receipt/result. Order submission uncertainty enters reconciliation, never blind replay. Model-call uncertainty retains its cost reservation until reconciled or conservatively charged; a repeated provider request is a new paid attempt unless the provider explicitly guarantees reuse.

Store market observations separately from financial events. Retain decision snapshots and source evidence indefinitely for the configured evidence-retention period, while compacting unreferenced high-frequency observations. Logs are bounded and redacted. Free disk space, event backlog and checkpoint growth are health metrics.

## Runtime deployment

Local paper mode: one service with a persistent local volume; credentialed model usage is opt-in. The Engineer sandbox is launched by a narrow runner, not by giving the model an unrestricted host shell. Use an existing machine for the first experiment and account for attributable electricity/storage if material. A small VPS is optional, not included free infrastructure.

Remote dashboard access requires authentication and TLS, preferably behind an owner-controlled private tunnel. Bind loopback by default. All write endpoints require owner/session authorisation and CSRF protection where cookie sessions are used. Role calls use server-side scoped identities rather than an owner browser token.

Do not run the 24/7 trading service in GitHub Actions. Use CI for bounded build/test work. Public standard-runner minutes have specific free-use rules [S15]; repository visibility never justifies publishing private trading evidence.
