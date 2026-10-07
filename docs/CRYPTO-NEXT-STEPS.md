# Crypto continuation: practical next steps

Owner priority, 2026-10-05: focus on the existing crypto implementation and obtain trustworthy operating and economic evidence. The broader [multi-asset direction](MULTI-ASSET-DIRECTION.md) is saved for later. This is a personal trading project; new infrastructure should serve useful operation, financial correctness or measurable results.

This plan is based on source checkpoint `5f7cbeb`. T00–T16 retain their recorded completion scopes; T17 and T18 remain in progress. Source integration findings below are follow-up work, not new passing acceptance evidence. No model/provider, operating allowance, deployment purchase, paid call or live order is authorized by this document.

## Near-term experiment

- Use the existing Kraken-oriented paper path and BTC/USD and ETH/USD examples where current public access supports them, while confirming the intended live crypto venue first as described below.
- Keep USD10,000 virtual capital with EUR reporting. Real AI/hosting spending stays separate.
- Retain all six role workflows, the software Secretary and existing financial/recovery protections.
- Choose one active model provider after checking current capabilities, account access and prices. Investigate [official subscription-backed CLI options](reviews/2026-10-05-local-subscription-options.md) before committing to separate API spending, reflecting the owner's existing Claude/Codex subscriptions. The current graph implements direct API adapters only. Retain both adapters without requiring two accounts or selecting a vendor from the research report's incomplete comparison.
- Retain the low-frequency, event-driven approach. The documented four-hour Trader/daily Research/evidence-gated review cadences are starting examples, not a requirement to trade or call a model without useful evidence.
- Start with existing unproven strategy hypotheses and simple controls. Defer additional exchanges, equities/ETFs, paid news and broader deployed Engineer classes while this experiment is established.

Kraken remains the current technical reference, not a conclusion about the best eventual execution venue or current account-specific fees. USD and stablecoins remain distinct.

## 0. Confirm the eventual live venue and test its own API

Owner update, 2026-10-05: Kraken verification and Kraken Pro access are reported
complete. Authenticated graph connectivity is still untested. The practical
next step is a dedicated read-only API connection on the selected host; see
[Kraken onboarding and iPad access](KRAKEN-ONBOARDING.md). The owner has since
chosen an existing Windows PC for the testing period, using
[WSL2 Ubuntu](WINDOWS-LOCAL-TESTING.md); cloud hosting is deferred.

Owner clarification, 2026-10-05: connected trading tests should target the platform intended for eventual real trades. Settle this before investing in a more elaborate custom fill simulator. Kraken spot is the conditional first candidate and the only implemented [live exchange adapter](../src/trade_graph/adapters/brokers/kraken_live.py); the runnable CLI still refuses live mode. Alpaca and Binance execution adapters are not implemented.

Verify actual account access, supported spot pairs/order types, costs and the available testing route for that exact product. Prefer the chosen venue's own hosted practice/test API for connected order-lifecycle tests where supported. A hosted virtual account still simulates fills; a different product's demo or another venue's fills do not establish the intended live execution behavior. Current official practice/test support has not been verified here because documentation retrieval was blocked by the environment's HTTP proxy.

Start with its public data and authenticated read-only account API, then its verified practice or validation-only route where available. The existing [Kraken read-only collector](KRAKEN-CONFORMANCE.md) does not submit or validate orders; a validation-only order path would need a separate implementation. Once software/account prerequisites are met and a real-capital envelope is explicitly authorized, very small actual orders on the selected production platform can establish real fills, fees, cancellation and reconciliation behavior before any increase in capital. This planning clarification authorizes no real orders or spending.

Keep local simulation for repeatable historical and failure tests. Usable price history remains necessary with either execution path, and local friction refinements should be scoped after the venue/testing choice. A suitable hosted paper API can be trialed without first completing an elaborate local fill model.

## What already works

The recorded checkpoint contains paper orders and partial fills, deterministic accounting, budgets/usage receipts, durable departmental workflows, authenticated owner controls, limited artifact improvement/activation, backups and restart/reconciliation behavior. Market buys already use the ask and sells the bid; the broker is not simply filling everything at a midpoint. Both model adapters already request structured output.

Prior verification records 2,544 full tests, 464 mapped tests and 40 applicable offline cases. Those historical results do not certify actual public connectivity, billed model calls or profitability. This planning change did not rerun that full suite or rebuild the installed wheel/protected image.

## 1. Supply usable history to Research and Trader

**Recommended first coding slice; needs no purchase or credentials.**

The current [Trader context](../src/trade_graph/application/trader_workflow.py) calls `quote_features([quote])`. Its features are bid, ask, midpoint and spread. The [starter templates](../src/trade_graph/roles/strategies.py) refer to hourly return, four-hour drift, pullback depth and range values. Those historical inputs are not currently supplied by the standard runtime context. Research likewise receives latest observations and cached findings.

Implement against the active [installed templates](../src/trade_graph/runtime_artifacts.json), whose requested fields include moving averages, pullback and range values; these differ partly from the starter declarations. Keep the installed inputs and their computed definitions aligned.

Add a bounded, point-in-time history/feature path through the real departmental context:

- Declare the source, interval and lookback for each feature; preserve event and availability times.
- Build from scripted/retained observations first, with compatible public historical collection prepared after current API verification.
- Produce the actual fields needed by the selected templates using Decimal values and attributable source/version identity.
- Surface insufficient history and gaps explicitly; do not invent trends, completed candles or publication times.
- Retain concise context and existing discretionary trading/mandate behavior. This is input preparation, not a new model-confidence threshold or per-trade approval committee.

Completion evidence should demonstrate the actual Research/Trader context changes when earlier available history changes, while later unavailable observations cannot change that snapshot. Cover restart, stale/gapped history and exact feature arithmetic. Keep synthetic and actual market observations distinct.

Implementation continuation, 2026-10-06: the bounded public hourly collector,
append-only retained candle revisions and active-template context features are
specified in [KRAKEN-PRICE-HISTORY.md](KRAKEN-PRICE-HISTORY.md). Use its one-shot
command in the existing private WSL2 paper checkout after updating source.
Actual source/test results and any public observation are recorded separately
in IMPLEMENTATION-STATUS.md; this slice does not close T17–T22 acceptance.

## 2. Make paper execution assumptions explicit

The installed runtime constructs [PaperBroker](../src/trade_graph/adapters/brokers/paper.py) with default fees and participation. [PaperRuntimeConfig](../src/trade_graph/paper_runtime.py) does not expose an experiment-specific friction profile. The broker stores `latency_seconds`, but submission/matching does not apply it; current intent eligibility uses the current clock. Same-observation exclusion exists, but it is not a configurable arrival-delay simulation.

Add an owner-configured, validated and versioned profile for maker/taker fees, participation, arrival delay and fill assumptions. Wire it through the actual broker and corresponding reserves, preserve exact profile identity in evidence, and make delay affect eligibility. Label current fee defaults as conservative assumptions rather than confirmed venue/account rates.

Use post-arrival trade/quote evidence appropriately. A rolling daily volume is not a quantity traded after our order arrived. A touched limit is not proof of queue priority; a marketable limit must not be assumed to receive a maker fee. Keep heuristic results explicit and evaluate sensitivity without double-counting spread/slippage already present in execution prices.

Verify meaningful scripted cases: no pre-arrival or same-observation fills, bounded partial fills, exact fees/reserves, unknown/restart behavior and changed-profile attribution. Preserve financial history across software/profile changes.

## 3. Finish the actual data/provider operating path

Prepare these alongside the first two software slices:

- Integrate timestamped Kraken quote/trade observations into the continuous paper service, with explicit gaps, reconnect and REST fallback provenance. The existing [WebSocket adapter](../src/trade_graph/adapters/market/public.py) is ticker-oriented and is not the standard runtime feed; trade/L2 support must not be assumed. Keep L2 depth a measured execution need rather than an unconditional large-data project.
- Verify current official pair metadata, public/API request shapes and Frankfurter ECB reference responses. Existing receipt-time snapshots are not exchange event timestamps.
- Check current provider/model IDs, capability registry and price cards. Existing strict schemas and receipt accounting should be reused. Resolve concrete request/retention configuration gaps from verified documentation; do not adopt all research recommendations uncritically.
- Complete reviewed public market/FX networking for the chosen protected deployment. The current [funded provider profile](FUNDED-PAPER-PROFILE.md) explicitly refuses `public_data_enabled`; adding keys alone cannot connect this configuration to actual market data.

Local scripted tests can proceed without keys. Actual public probes and credentialed calls need their own retained outcomes. Historical proxy HTTP 403 failures remain unresolved; do not label a synthetic transport or configuration check as an external success.

## 4. Prepare the selected PC and run a short operating check

Use the existing Windows PC with WSL2 Ubuntu for initial testing; no VPS purchase
is needed. Begin with the dashboard, offline rehearsal and public-data checks,
which need no paid inference. Before funded observation, verify the exact local
installation, choose an active model-provider route and set a separate real
operating allowance. Existing Claude/Codex subscriptions do not automatically
fund the graph's current direct API adapters. Keep the PC awake and connected
for the declared observation window; revisit always-on hosting later.

Use the [operations runbook](operations.md) and verify on that installation:

- Public prices/FX arrive and retain appropriate provenance and freshness.
- One bounded credentialed model request has an actual response and cost receipt.
- Scheduled roles operate through the real runtime, with holds/no-action distinguished from errors.
- Paper order/fill/accounting and EUR reporting reconcile.
- Restart, stale-data and provider failure behavior preserve records and order management.
- One actual alert reaches the selected destination, and an off-host backup can be restored.

Then perform a short funded paper observation and review operation, exceptions and expenses. One account can support all roles. Ordinary bill/statement comparison can be used initially instead of adding organization-admin billing credentials to the graph.

A successful operational check closes only the evidence it actually covers. It is not economic validation or live permission.

## 5. Collect future evidence and assess net value

T18 should compare the agent portfolio with cash/no-trade, buy-and-hold and a simple deterministic strategy over a predeclared future window. Use compatible source, initial allocation, external-flow path and execution assumptions; retain all attempted variants. Count trading and shared AI/hosting costs once.

The current four-arm producer/registry is implemented preparation; actual future blocks, baseline execution and external source/cost provenance remain pending. Determine the supported horizon before promising a long unattended evaluation: economic source retention is currently bounded at 8 MiB/20,000 rows and needs capacity work where the proposed window would exceed it.

Report after-cost results, drawdown, turnover, fee/fill sensitivity, expense uncertainty and value relative to controls. Review the usefulness and cost of departments and additional information. Increase cadence, depth or sources only when the evidence gives a reason.

No fixed short run can establish profitability. Supported, not-supported and insufficient-evidence findings are valid results. Keep the later live-account conformance and explicit real-capital decision separate.

## Task priorities and deferred scope

| Task / direction | Current priority |
| --- | --- |
| T17 | Finish the concrete software/host/provider/public-data path and obtain actual paper operating evidence. |
| T18 | Prepare a supported forward window and collect genuinely future after-cost comparisons. |
| T19 / T20 | Preserve the implemented preparation; authenticated venue acceptance and real-money pilot follow their prerequisites and separate owner decisions. |
| T21 | Complete only the host/network/recovery work needed for the chosen operating path now; retain existing isolation and continuity protections. |
| T22 | Preserve existing limited R1 improvements and synthetic preparation; broader production classes are deferred. |
| Multi-asset direction | Saved, deferred. Revisit on owner request or after reviewing the initial crypto evidence. |

The first planning step is to confirm the intended live venue and its own testing route. The history/feature-to-department slice can be built and meaningfully tested in parallel without waiting for a VPS purchase, private account access or paid API credentials. Scope the local friction/profile follow-up around the selected testing path; neither substitutes for actual external acceptance.
