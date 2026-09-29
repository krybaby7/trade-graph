# Decision log

## Accepted planning defaults

| ID | Decision | Reason / revisit condition |
|---|---|---|
| D01 | Modular Python, one paper-runtime process, isolated Engineer runner | Smallest operational footprint; separate protected kernel before broader mutable code |
| D02 | SQLite for single-host paper R1, thin LangGraph orchestration | Durable inexpensive local experiment; PostgreSQL when multi-host/writer or live-service needs justify it |
| D03 | Paper spot long-only with BTC/EUR and ETH/EUR where supported | Simple ledger/execution and EUR reporting; instrument choices are test fixtures, not investment advice |
| D04 | Kraken public feed as technical reference; exchange-specific broker contract | Inspectable documented order semantics; no live eligibility conclusion |
| D05 | Both provider adapters, per-role routing, one-provider installation allowed | Provider independence without requiring two paid accounts to start |
| D06 | Six model roles; software Secretary/accounting/monitoring/execution | Preserve responsibilities without permanently running departments |
| D07 | Automatic artifact/prompt/config changes in R1 | Full improvement loop with real implementation before granting arbitrary code authority |
| D08 | EUR100 virtual capital, illustrative EUR5 monthly expense ceiling | Expose small-capital economics; owner must explicitly authorize actual spend |
| D09 | Low-frequency event-driven Trader; deterministic protection between calls | Reduce token churn while retaining meaningful individual trade discretion |
| D10 | Repository is a planning handoff, not a fake runtime scaffold | The coding orchestrator receives specifications, task DAG, validation targets and runnable planning utilities |

## Decisions still required

Owner/runtime setup: legal residence/entity and exchange eligibility; actual venue/pairs/fee tier; available model accounts and model-quality results; approved real API budget including taxes/FX/hosting; deployment host and availability; experiment loss/exposure envelope; live permission; broader engineering classes. Never commit the owner's private answers to a public repository.

Implementation choices: exact compatible package versions and lockfile; operational credential store; sandbox mechanism on the actual host; market-depth retention; provider capability/snapshot pinning; venue-specific stop/OCO/amend/lookup support; independent test harness/attestation mechanism. Resolve these in their tasks and append concise evidence-backed entries here.

No remaining choice should prevent offline R1 development. When a choice genuinely needs credentials, finish synthetic/contract work and mark credentialed verification pending rather than inventing a successful test. Completing a code task is not authorization to enable paid resources or live trading.
