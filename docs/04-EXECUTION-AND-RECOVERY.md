# 04 — Broker contract, virtual execution and operational recovery

## Shared broker interface

Implement an async protocol with normalized request/result DTOs. Paper, replay and live adapters must satisfy the same conformance suite:

```python
class Broker(Protocol):
    async def capabilities(self) -> BrokerCapabilities: ...
    async def instruments(self) -> list[InstrumentRules]: ...
    async def balances(self) -> BalanceSnapshot: ...
    async def open_orders(self) -> list[OrderSnapshot]: ...
    async def fills_since(self, cursor: str | None) -> FillPage: ...
    async def submit(self, intent: AuthorizedOrderIntent) -> SubmitResult: ...
    async def cancel(self, request: CancelRequest) -> CancelResult: ...
    async def order_status(self, key: OrderLookup) -> OrderLookupResult: ...
```

This is a target interface sketch, not existing source. Include typed lookup-not-supported, timeout/uncertain, rejected, unavailable and rate-limited results. Broker capabilities declare client-ID lookup, native amendment, stop/conditional orders, time-in-force, reduce-only semantics, fills pagination and cancellation behaviour. An unsupported capability cannot be silently simulated as equivalent live protection. Spot reduction may need kernel-enforced owned-quantity reservations rather than a venue reduce-only flag.

Market data is a separate interface providing observations and clocks; replay controls time explicitly. Broker instances are bound by software to mode/account/venue. No role can choose a raw endpoint or change paper to live inside an order. There is no withdrawal/transfer-out tool in the runtime interface.

## Intent and order state machine

Separate a model decision, an authorized intent, a submission attempt and a venue order. Validate the current owner envelope, mandate revision, pause state, market freshness, precision, minimum notional/quantity, available funds, anticipated fees and worst permitted exposure before reserving funds and writing an outbox entry atomically.

```text
PROPOSED -> VALIDATED -> RESERVED -> SUBMISSION_PENDING
                                     |
                                  SUBMITTING
                               /      |       \
                       REJECTED     ACKNOWLEDGED   UNKNOWN
                                      |             |
                                    OPEN <--- reconciliation
                                      |
                                 PARTIALLY_FILLED -> FILLED
                                      |
                                 CANCEL_PENDING -> CANCELLED / FILLED / UNKNOWN
```

Expiry is also a terminal order state. Cancellation of a partially filled order does not erase fills. Model order amendments are new authorized intent revisions with optimistic concurrency, not edits to a previously submitted record. Order state may need to catch up directly to filled when the exchange acknowledgement was lost.

Persist a unique stable client ID before submission. Serialize order creation for the relevant account reservation and attach that ID to all reconciliation work. A client ID reduces ambiguity but is not a universal exactly-once guarantee: Kraken documents `cl_ord_id` as unique for an open order [S07]. A timed-out order may already have filled and no longer be open. Therefore never blindly resubmit on timeout, even with the same client ID, without a venue-specific proven idempotency guarantee.

On ambiguous submission/cancellation, retain reserved potential exposure, mark `UNKNOWN`, query order history/client ID where supported, fetch fills and reconcile balances. Block conflicting exposure increases for the affected account/instrument while uncertainty remains. Do not conclude rejection from one empty query with eventual consistency. Apply bounded backoff, record each query and escalate an unresolved incident. Reconciliation may continue on a slower schedule after the task's active retry budget is spent.

At-least-once network delivery plus idempotent local recording and explicit uncertainty is the target; do not claim distributed exactly-once execution. Deduplicate fills by venue/account/fill ID. A second report with the same fill ID and inconsistent values is a discrepancy, not a second fill.

## Precision, adjustments and position management

Normalize asset names without losing native venue symbols. Refresh market rules and fee tiers at startup and on relevant errors. Round quantities/prices using venue-specific rules, check the result against the original sizing intent, and reject a rounded order that would exceed authorization. A minimum order is not permission to increase risk. Persist the exact submitted payload and rounding explanation.

Prefer native amendment only when the capability and identity semantics are tested. For cancel-and-replace: request cancellation, reconcile any intervening fills, verify the remaining quantity, then submit the authorized remainder. Never assume cancellation has won a race with a fill. Multiple exits reserve quantities so they cannot sell more than the owned position. OCO/bracket behaviour must be native or explicitly coordinated; emulation carries an outage/gap limitation.

Pre-agreed deterministic invalidation/protection rules operate without model calls. Native protective orders reduce dependence on the host, but still have venue/market risks. A stop price does not guarantee the execution price. The Trader can revise a management plan within its mandate while the kernel continues managing the currently active one.

## Pause is a profile, not a boolean

Persist originator, reason, scope, desired profile, requested time and achieved state. An owner-originated pause cannot be lifted by the Leader. Allow independent controls for new reasoning, exposure increases, entry orders, protective orders and deliberate liquidation.

| Profile/action | New discretionary work | Orders and existing inventory |
|---|---|---|
| `RUNNING` | Within mandate/budget | Normal execution and reconciliation |
| `PAUSE_DECISIONS` | Stop new Trader decisions; maintenance continues | Existing orders/protection remain active under their recorded policies |
| `NO_NEW_EXPOSURE` | Only exposure-reducing management is allowed | Cancel increasing orders and verify cancellation; retain/manage protective exits |
| `MANAGE_ONLY` | No new entries/research experiments; optional bounded reduction-only reasoning | Reconcile and follow existing deterministic protection; valid reductions remain possible |
| `CANCEL_ALL` | Block new entries | Explicit workflow cancels all open orders, including protective orders; dashboard warns and continues position management |
| `FLATTEN` | No new exposure | Cancel conflicting orders, reconcile fills, then close the verified remaining inventory with bounded execution; remain flattening until confirmed |
| `STOPPED` | No strategies | Achieved automatically only after no positions, outstanding orders or unresolved execution remain; reconciliation can still catch late events |

A UI request to stop a nonflat portfolio must choose manage-only or flatten rather than kill the process and abandon it. An emergency host shutdown cannot guarantee position management; warn about venue-side protections and the recovery runbook. A paused strategy may still accrue market P&L and fees from previously submitted orders.

Do not enable a cancel-all dead-man switch blindly: Kraken's endpoint cancels all orders on expiry [S11], which can also remove protective exits. Use it only with a specifically tested order/position policy. Selective cancellation of exposure-increasing orders is the normal no-new-exposure behaviour.

## Startup and degraded modes

On startup obtain the process lease, validate schema/configuration/version, restore the strongest persisted pause, replay unprocessed local events, fetch account balances/open orders/recent fills, reconcile all uncertain intents, and refresh metadata/market health. Only then permit exposure increases. A valid local checkpoint does not prove the broker state matches it.

| Failure | Required behaviour |
|---|---|
| Stale quotes, gaps or lost feed heartbeat | Mark degraded, block increases, refresh/reconnect; retain known protective orders, do not invent current prices |
| Model unavailable/refuses/malformed | Record a paid failure when applicable, use bounded repair or approved fallback, otherwise manage-only; not a deliberate Trader hold |
| Budget exhausted | Stop new paid work; deterministic reconciliation/protection/cancellation remain active; no forced liquidation unless owner previously chose that policy |
| Exchange unavailable | No claims of completed actions; preserve uncertain states, retry queries with limits, alert owner; outage protection cannot be guaranteed |
| SQLite/disk failure | Stop new intents, avoid unjournaled submissions, preserve data/backups; an operational outage is visible |
| Restart mid-call | Recover persisted attempts/reservations, reconcile before retrying external side effects |
| Mandate or owner limits changed | Revalidate unsent intents and cancel prohibited increases; existing positions get an explicit management transition |
| Deployment failed | Keep/restart known-good graph version; ledger, positions, orders and policy are never rolled back to an earlier financial snapshot |

Critical software maintenance is not a paid agent loop, but infrastructure/API charges, if any, still count. Live operation needs owner-funded continuous hosting/connectivity; an AI reserve does not keep a powered-off computer running.

## Virtual broker and replay fidelity

R1 paper trading uses real public market observations and an internal virtual account. It is not an exchange testnet balance. Implement order acceptance, funds reservation, partial fills, cancellation races, minimums, precision, maker/taker fees and the same journal/outbox path as live.

For market orders, use the next available observation after configured submission latency, cross bid/ask and apply a configurable depth/impact model. Do not fill at a stale last-trade price. For limits, use a conservative queue/volume participation assumption; a touched price does not guarantee a fill. If only bid/ask is available, label fills heuristic and vary pessimism in validation. Native stop/trailing order simulation must use future event arrival and handle gaps, not fill every stop exactly at its trigger.

For historical replay, expose only information available at the decision time. Research publication alone is insufficient if ingestion was later. Orders cannot execute on a bar's earlier high/low after using its close. When intrabar order is unknown, use conservative ordering or report both optimistic and pessimistic bounds. Seed any simulated randomness and store the seed/configuration. Do not silently pick the path that makes the strategy win.

Fees are per fill and asset; spreads/slippage are embedded in simulated prices. Define a volume participation ceiling and partial-fill policy rather than assuming unlimited depth. Deposit events are explicit. Test very small orders against the live venue's published minimums; a synthetic test instrument is clearly distinguished.

Paper limitations: no true queue position, matching-engine impact, private liquidity, full venue latency distribution, real account permissions, production rejection patterns or assurance that a live stop works. Coinbase's Advanced Trade sandbox returns static mocked responses [S13]; it can help request-shape testing, not establish performance. A venue test environment complements, rather than replaces, our simulator and eventual tiny live conformance tests.
