# Decision log

| ID | Accepted planning decision | Reason / revisit condition |
|---|---|---|
| D01 | Modular Python, one paper-runtime process, isolated Engineer runner | Small footprint; protected kernel process before broader mutable code |
| D02 | SQLite paper R1 and thin LangGraph | Local durable experiment; PostgreSQL when multi-host/writer requirements justify it |
| D03 | USD10,000 virtual spot account, BTC/USD and ETH/USD where supported, EUR reporting | Updated owner instruction; amounts/currencies remain configurable |
| D04 | Kraken public feed as technical reference; explicit broker capabilities | No live eligibility or fee-optimality conclusion |
| D05 | Both provider adapters; one-provider installation allowed | Independence without requiring two paid accounts |
| D06 | Six model roles with software administration/accounting/execution | Preserve responsibilities without permanent agent conversations |
| D07 | R1 implements real tested artifact/prompt/config changes | Complete improvement loop before arbitrary code authority |
| D08 | Real operating budget separate from virtual capital; paid calls disabled until configured | Optional lean EUR5/month example is not authorization or a permanent cap |
| D09 | Event-driven low-frequency reasoning with continuous software management | Avoid unnecessary token use without a per-trade committee |
| D10 | The 2026-09-29 handoff supplied a plan, task DAG and runnable planning utilities | Historical. Runtime code landed later at `a4bdce6`; this row does not describe the current tree |
| D11 | Paper resets cannot erase real expenses or refill the API allowance | Native accounting and cross-experiment cost provenance remain auditable |
| D12 | Provider-neutral adapters target REST bodies rather than vendor SDKs; current implementations are fixture parsers only | Real HTTP transport, capability handling and credentialed verification remain unfinished. Do not label fixture success as network success |
| D13 | The Engineer sandbox is a fresh temporary git repository of allowlisted files | A worktree of the product repo would mount deployment git credentials. Revisit only with a scoped remote that cannot push protected paths |
| D14 | Paper and reserve fees use an explicit fixed conservative tier, maker 0.004 and taker 0.008 | Rolling venue volume is not known offline. Replace with the owner's actual fee schedule when a venue account exists |
| D15 | Superseded security claim: the multiprocessing/RPC example is only a protocol fixture, not an OS sandbox | Executable plugins and trusted promotion are disabled. Build and adversarially test a real protected boundary before broader code authority |
| D16 | Fixed in `9e908d4`: compare aggregate exposure and equity in the same currency using identified FX provenance | Numeric USD-versus-EUR comparisons are invalid even in a demo. Preserve the currency regression tests |
| D17 | Durable scheduler claims carry unique fencing tokens; schema migration 0002 is additive and transactional | Reclaim stale work without stale-worker result writes. Reconcile prior external effects before retry; task recovery never authorizes duplicate orders or free retries |
| D18 | Authorization reads the active persisted owner policy and mandate. Exposure and quote-age limits are the tighter of those revisions. `Execution` is bound to a `Broker` whose capabilities must match venue/mode, support client-id lookup, and declare withdrawals disabled. OpenAI, Anthropic and scripted adapters implement `InferenceAdapter` | Caller-supplied exposure caps are not authority. Fixture `parse` is not a network call. A discretionary mandate may name a strategy that is not prelisted; a non-discretionary mandate may not |
| D19 | Public Kraken metadata/ticker/WebSocket and Frankfurter ECB reference FX go through injectable transports. Scripted tests prove reconnect, gap backfill, out-of-order rejection and point-in-time replay. A separate public smoke is not an order test | 2026-09-30 smoke: AssetPairs XBTUSD, REST ticker, one v2 ticker snapshot, ECB USD/EUR. Reconnect was not exercised on the live socket. Reference FX is not an executable quote |

The 2026-09-29 USD10,000 update supersedes EUR100 as the operating example. EUR100 golden accounting fixtures and small-live-allocation sensitivity language are tests/examples only. See docs/10-PAPER-CAPITAL.md; no live allocation has been set.

## Remaining decisions

Owner/runtime: legal residence/entity and exchange eligibility; actual venue/pairs/fee tier; model accounts and measured quality; actual expense budget including taxes/FX/hosting; deployment availability; exposure/loss/experiment envelope; live permission; broader engineering classes. Keep private answers out of public Git.

Implemented and tested slices: lockfile at `uv.lock`, fixed paper fee assumptions D14, currency correction D16, scheduler/migration recovery D17, persisted mandate authority and provider-neutral protocols D18, public market/FX transports D19. Provider model HTTP transport (D12) and D15 security isolation are not complete. Still open: credential store, real-host container/cgroup attestation, market-depth retention beyond the paper participation model, native stop/OCO/amend proof on a live venue, and an owner grant of broader code classes.

None prevents offline development. Mark credentialed verification pending when credentials are absent; do not invent successful tests or treat implementation completion as permission to trade or spend.
