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
| D12 | Provider adapters speak documented REST with httpx-shaped bodies, not vendor SDKs | Domain and kernel stay free of provider SDKs. Revisit if an official SDK becomes the only supported wire |
| D13 | The Engineer sandbox is a fresh temporary git repository of allowlisted files | A worktree of the product repo would mount deployment git credentials. Revisit only with a scoped remote that cannot push protected paths |
| D14 | Paper and reserve fees use an explicit fixed conservative tier, maker 0.004 and taker 0.008 | Rolling venue volume is not known offline. Replace with the owner's actual fee schedule when a venue account exists |
| D15 | The P6 kernel boundary is a separate OS process with scoped RPC, a sanitized environment and `RLIMIT_CPU` | A container image and cgroup attestation need owner infrastructure. A Python import boundary is not treated as isolation |
| D16 | Exposure checks compare USD notional with reporting-currency equity numerically | Demo sizes stay inside the cap. Convert through an identified FX rate before any live or larger-notional use |

The 2026-09-29 USD10,000 update supersedes EUR100 as the operating example. EUR100 golden accounting fixtures and small-live-allocation sensitivity language are tests/examples only. See docs/10-PAPER-CAPITAL.md; no live allocation has been set.

## Remaining decisions

Owner/runtime: legal residence/entity and exchange eligibility; actual venue/pairs/fee tier; model accounts and measured quality; actual expense budget including taxes/FX/hosting; deployment availability; exposure/loss/experiment envelope; live permission; broader engineering classes. Keep private answers out of public Git.

Implementation resolved in code: lockfile at `uv.lock` (D12–D16 above); paper fee tier D14; process isolation D15. Still open: credential store, real-host container/cgroup attestation, market-depth retention beyond the paper participation model, native stop/OCO/amend proof on a live venue, and an owner grant of broader code classes.

None prevents offline development. Mark credentialed verification pending when credentials are absent; do not invent successful tests or treat implementation completion as permission to trade or spend.
