# Owner service controls

Mission Control at `/progress` now shows durable worker status and has **Start
Trading**, **Start Optimisation**, **Pause AI · keep protection** and **Stop safely**
controls. Dashboard serving starts no service or model call. Writes require an
owner session; cookie writes also require the session's CSRF token.

Start the dashboard from the existing private paper installation:

```bash
uv run --frozen trade-graph dashboard --database runtime/trade_graph.sqlite
```

A separately provisioned owner configuration can be supplied with `--config`.
The default starts a management-only paper service when Start Trading is pressed:
reconciliation and protection are available, while subscription model routing
remains unavailable until its independent official-provider and isolation gates
are satisfied. The controls refuse direct separately billed model API routes.
Live activation stays separately protected and disabled by default; pressing a
paper control does not authorize live trading or purchases.

Start Trading starts one child service or attaches to the current worker. A
private OS control lock serializes launches; the service holds the actual
SQLite inode lock for its whole lifetime. The durable run includes the Linux
PID's birth identity, heartbeat, terminal state and sanitized failure type.
Interruption is visible. A new owner request can start a successor after the
previous worker loses its process ownership. Requests retain their identity
through uncertain browser responses; repeated requests attach or replay rather
than launch another worker. Private logs and credentials are not returned.

Optimisation is owner-requested only. Periodic Optimisation schedules, including
artifact-managed schedules, are suppressed. Previously queued automatic
Optimisation is retired with a retained cancellation outcome. Leader commissions
cannot reopen this scheduling route in the running service. Start Optimisation
requires an available isolated subscription runtime and an unpaused portfolio.
It persists exactly one task with one attempt and a ten-minute deadline. The
existing protected departmental consultation can return one Leader review and
bounded descendants within the same root allocation and deadline. Engineer
acceptance, artifact activation, delegation limits and accounting continue to
apply. No change is automatically accepted merely because a cycle was requested.

A repeated cycle request returns the same saved task and progress. A different
request while an active or unresolved cycle remains receives an explicit
conflict. The dashboard shows each task's result/failure and the overall cycle
state. A deadline prevents fresh inference; it does not erase existing costs,
financial effects, applied artifacts or unresolved attempts.

Pause AI keeps the service and deterministic management active. Stop safely
stops only a flat account with no outstanding orders. Otherwise it retains
management-only protection, or an already stronger system/flatten/cancel pause,
and leaves the worker active. Use Owner controls for verified flatten or resume.
A stopped process provides no continuing monitoring. Quota exhaustion blocks new
AI scheduling and claims while reconciliation and protection continue.

The authenticated API exposes `GET /api/v1/service` and owner writes
`POST /api/v1/owner/start-trading`, `start-optimisation` and `stop-service`.
Each lifecycle write requires a bounded `request_id`. Start accepts `mode`;
stop accepts `position_policy` of `manage-only` or `flatten`. Public projections
show subscription quota as authenticated provider metadata when available and
unknown otherwise. Unknown usage/cost remains unresolved. Subscription usage,
real operating expenses and USD10,000 virtual paper capital are separate.

Default tests use no credentials. Lifecycle tests include actual CLI child
startup, duplicate attachment, abrupt process loss, restart and flat shutdown in
a disposable synthetic database. These tests establish implementation behavior;
they do not establish successful Research inference, continuous paper AI
operation, Kraken conformance or authorized live commissioning.

Dashboard page labels use the persisted scoped portfolio mode. A live account journal is labelled as live records, while operating expenses stay separate from trading allocation. The live scope label does not enable execution. Mission Control and Owner controls show static protected startup admission refusals, and the service lifecycle shows whether any worker is observed. Rendering these pages causes no broker effects or worker launch.
