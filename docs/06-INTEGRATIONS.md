# 06 — Integrations, permissions and deployment credentials

Verified against the official sources listed in 90-SOURCES.md on 2026-09-29. API access, account eligibility and current pricing must be checked again when implementing/deploying. Documentation review is not an executed integration test. The default release can run offline with fixtures, then paper with one configured model provider and public market data.

## Integration decision matrix

| Integration | Recommended approach / R1 relevance | Deployed credentials and permissions | Cost and limits |
|---|---|---|---|
| OpenAI inference | Direct API adapter using Responses and our structured request/output contracts; required adapter in R1 | Dedicated API project/service credential, approved model access; no owner/admin token in graph | Token/tool charges in S01; quota/rate limits, supported schemas/model settings and incomplete/refusal outcomes need testing [S03] |
| Anthropic inference | Direct Messages adapter; required adapter in R1, optional credential when only OpenAI is configured | Dedicated API key/workspace and enabled model; no console/admin key to agents | Token/tool rates S02; model-specific tool/thinking constraints, schema subset and output failures [S06, S19] |
| Public market data | Kraken public REST metadata plus WebSocket ticker/book, with reconnect/backfill; suggested paper reference feed | No private trading key for public feed | No paid subscription budgeted; rate limits, gaps, retention/redistribution terms and available pairs must be respected [S07, S09] |
| Virtual broker | Own deterministic paper/replay adapter sharing the live broker contract | None; synthetic virtual allocation in local state | Local compute/storage plus any real model/research charges; fill assumptions are not live guarantees |
| Live exchange | Conditional first candidate: Kraken exchange-specific adapter; implement only after eligibility and capability gate | Dedicated read/trade/cancel key; balance, open/closed orders, fills and required account reads; no funding/withdrawal permission; IP restrictions where supported | Actual account/pair fee tier, spread, slippage, conversion/funding costs and venue minimums; query account fees [S07, S08, S10, S12] |
| CCXT | Optional adapter implementation aid; do not make its unified surface the domain contract | Same venue credentials if used | Public project docs S14; exchange capabilities differ. Prove order IDs, cancellation and precision behaviour per adapter; no assumption of universal sandbox/stop/amend support |
| External research | Approved project/exchange/official feeds and bounded HTTPS fetch first; optional provider-native paid web search | Public sources often none; paid search uses the selected provider key and explicit tool budget | Both listed provider search rates are USD10/1,000 searches plus applicable input/output tokens [S01, S02]. Cache by question/source/version/expiry, honor access terms |
| EUR reference FX | Frankfurter v2 daily reference rates, cached and attributed; actual invoice settlement separately | Public service requires no API key | Free public reference source, not an execution FX quote; no SLA assumed [S17] |
| Storage/observability | Local SQLite + artifact directory, append-only journal, usage receipts and simple dashboards | Local OS permissions; no paid tracing account | No database/tracing subscription in R1; disk, backup, electricity and hosting still attributable. WAL and checkpoint constraints in S04/S05 |
| Engineer development | Local Git worktree, isolated process/container, trusted test runner and activation controller | No production secrets in sandbox; optional narrow GitHub service identity outside model context | Model inference plus runner compute and optional CI storage/minutes; reject unbounded builds |
| GitHub/CI | Repository source, change history and bounded tests; optional GitHub App for controlled PR publication | App ID/private key/installation ID outside sandbox; repository-scoped Contents and PR permissions only as needed | Standard public-repo GitHub-hosted runner use is free under documented rules; private quotas/storage/other runners differ [S15, S16]. Not 24/7 trading hosting |
| Hosting | Existing owner machine for paper first; optional always-on Linux host with persistent disk | OS/service credentials, private dashboard access; secrets mounted at runtime | Use actual selected vendor invoice. EUR6/month in the calculator is a budget allowance, not a verified vendor quote |

No paid aggregator, on-chain RPC, wallet key, market-news terminal, vector database, proprietary hosted agent runtime or hosted code interpreter is needed for R1. If added later, its credentials, permission boundary and receipts must be implemented before agents can use it.

## Provider-neutral inference contract

The domain/app layers call `ModelGateway.invoke(ModelRequest) -> ModelResult`. ModelRequest carries role/task/version, model-routing entry, system instructions, bounded typed context/messages, output JSON Schema, allowed tool definitions, maximum billable output, maximum tool/continuation steps, timeout, approved processing tier and a pre-reserved resource envelope.

ModelResult returns validated payload or typed failure, tool requests with call IDs, finish status, provider request/model IDs, raw usage receipt, elapsed time and redacted response reference. Errors distinguish credentials, unsupported capabilities/schema, rate limit, temporary failure, timeout/uncertain billing, refusal, truncation and validation failure. Never map all of them to an empty successful result.

Native Codex failures retain an optional bounded diagnostic envelope in durable
attempt and invocation results: process exit code, a fixed failure category,
allowlisted native error code, and a schema path verified against the supplied
public output schema. Raw stdout/stderr, provider messages, URLs and account
identifiers are not retained as diagnostic causes. Quota and model-availability
control messages remain exact; a generic native failure records its exit code
without asserting an unobserved cause. Earlier Leader failures whose original
cause was not retained remain unexplained by this instrumentation.

Provider adapters own SDK/wire differences: message roles, schema dialect subset, tool blocks/results, continuation tokens, finish reasons, usage/cache categories and supported reasoning controls. OpenAI structured responses and Anthropic structured outputs do not have identical request fields [S03, S06]. Local Pydantic validation and business-authority checks remain mandatory even with strict output formatting.

Maintain an explicit capability table per approved model. For example, current Sonnet 5.5 documentation lists `claude-sonnet-5-5` and warns that forced tool use and non-default sampling settings can fail [S19]. Do not emit one universal `temperature=0` or forced-tool configuration to every model. During T06 verify the selected model's API access and settings; record the resolved model identifier in receipts and decisions. Do not claim cross-provider outputs are behaviorally identical.

All calls pass through the cost gateway, including prompt repairs and provider fallbacks. A fallback is a configured choice with its own capability check, price reservation and version metadata, not a silent provider substitution. Model quality/routing is evaluated on schema validity, relevant reasoning, latency, net utility and errors, not price alone. The lean cost example is a hypothesis about affordable routing, not evidence that its cheapest model trades well.

Use ordinary bounded HTTP/API calls; do not require OpenAI Agents API, Claude Managed Agents or proprietary persistence. A standard SDK is an adapter dependency only. Avoid provider-managed conversation state as the sole record; retain our own versioned inputs/results. Default fixtures emulate both provider success and failure shapes without credentials.

## Suggested initial routing

For the cost illustration only: `gpt-6-luna` for Trader and Research; `gpt-6.1-sol` for Leader, Learning and Optimisation; `claude-sonnet-5-5` for Engineer. All are configurable and subject to credential/capability/quality checks. A one-provider installation can map Engineer to an approved OpenAI model or the other roles to approved Anthropic models after recalculating its budget. No paid model is activated simply because its identifier appears in an example file.

Public rates used for the illustration are standard short-context uncached inference: Luna USD0.10 input / USD0.50 output per million tokens; Sol USD2 / USD10; Sonnet 5.5 USD2 / USD10 [S01, S02]. Current price-card categories, cached tokens, context bands and processing-tier changes must come from verified configuration, not this paragraph as permanent business logic.

## Exchange and jurisdiction gate

Kraken is a suggested **technical paper-data reference**, not a determination that the owner is eligible to trade there. Before live selection document the account's residence/legal entity, applicable local law, platform eligibility, KYC status, supported spot pairs, fiat funding/conversion route, minimum orders, maker/taker tier, API-key controls, protection/amend capabilities and available testing route. Country lists and product availability can differ from the brand's general availability [S10]. Do not route around a restriction with a VPN or another person's account.

For jurisdictions with specific crypto restrictions, obtain current qualified advice before enabling live execution. As one relevant example, official Central Bank of Egypt warning statements are indexed regarding cryptocurrency restrictions; full pages were inaccessible in this review [S18]. That source is a warning to verify eligibility, not a complete current legal opinion. The repository does not assume the owner's jurisdiction or publish personal account information.

Do not pick an exchange solely for low advertised fees or a convenient sandbox. Use account-specific fees; the public Kraken schedule observed for its lowest spot tier was 0.40% maker / 0.80% taker [S08], which makes small frequent round trips expensive. Other venues are future candidates only after the same checklist. Coinbase's static Advanced Trade sandbox is useful for mocked contract tests, not our paper strategy-performance engine [S13].

## Coding environment versus deployed application

The ChatGPT/GitHub connection used to publish this plan does not provide the deployed service with a GitHub token, model API budget, exchange account or market-data subscription. The coding orchestrator may have shell/Git/network tools and its own billing, but the runtime must be configured independently. The owner supplies runtime secrets through environment/secret files outside Git; the application exposes only role-scoped capabilities, never raw secrets.

Offline CI requires none of these credentials. Credentialed paper tests require a funded model API project and access to public feeds. Optional paid search needs approved tool spend. Live tests additionally require verified legal/account eligibility and a dedicated trading key. A coding-agent subscription is not assumed to subsidize autonomous runtime API usage; reconcile any explicitly supported deployment arrangement separately rather than treating it as free.
