# Official source register

Review date: **2026-09-29**. These are official documentation/publication references, not successful runtime integration tests. Refresh volatile pricing, model settings, eligibility and exchange capabilities at implementation and before live use. Bracketed source IDs in the plan refer to this register. Design defaults and mathematical examples are our proposals, not vendor recommendations.

| ID | Official source | What was checked / limitation |
|---|---|---|
| S01 | [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) | Standard short-context token rates, processing/cache distinctions and tool charges; public price page, not account entitlement |
| S02 | [Anthropic API pricing](https://platform.claude.com/docs/en/about-claude/pricing) | Base token/cache rates and search billing; example uses direct API, not a managed-agent subscription |
| S03 | [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) | Structured-output contract and exceptional responses; implementation must validate current model/schema support |
| S04 | [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Checkpoints versus cross-thread stores and local/development SQLite guidance |
| S05 | [SQLite write-ahead logging](https://www.sqlite.org/wal.html) | WAL operating/concurrency considerations; not evidence that any deployment configuration is safe |
| S06 | [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) | Provider-specific structured formatting and tool schema handling |
| S07 | [Kraken Add Order](https://docs.kraken.com/api-reference/trading/add-order) | Required trading permission, order fields/types, client ID scope and reference to instrument metadata |
| S08 | [Kraken fee schedule](https://www.kraken.com/features/fee-schedule) | Public lowest-tier spot maker/taker schedule; actual account/pair/region may differ |
| S09 | [Kraken WebSocket v2 ticker](https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/ticker) | Public market-data reference; adapter must separately prove reconnect/gap/rule handling |
| S10 | [Where Kraken is licensed or regulated](https://support.kraken.com/articles/where-is-kraken-licensed-or-regulated) | Availability/regulatory reference only, not a determination of owner eligibility or a complete legal review |
| S11 | [Kraken Cancel All Orders After X](https://docs.kraken.com/api-reference/trading/cancel-all-orders-after-x) | Dead-man cancellation scope; position-management consequences must be designed explicitly |
| S12 | [Kraken Get Trade Volume](https://docs.kraken.com/api-reference/account-data/get-trade-volume) | Account/pair fee information for a credentialed adapter |
| S13 | [Coinbase Advanced Trade sandbox](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/sandbox) | Static mocked sandbox limitations; not a realistic strategy simulator |
| S14 | [CCXT official documentation](https://docs.ccxt.com/) | Project integration reference; exchange-specific conformance remains an implementation requirement |
| S15 | [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) | Public standard-runner/private-plan distinctions; no claim of free production hosting |
| S16 | [GitHub App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation) | Separate app/installation credentials and scoped integration tokens |
| S17 | [Frankfurter official API documentation](https://frankfurter.dev/) | Public reference-currency API and historical/daily use; not an intraday executable FX rate |
| S18 | [Central Bank of Egypt warning, 2022-09-12](https://www.cbe.org.eg/en/news-publications/news/2022/09/12/warning-statement) and [warning, 2023-03-08](https://www.cbe.org.eg/en/news-publications/news/2023/03/08/warning-statement) | Official indexed warning results were found, but full pages were blocked/inaccessible. They were not fully reviewed. Current legal applicability requires independent verification |
| S19 | [Claude Sonnet 5.5 model documentation](https://platform.claude.com/docs/en/models/sonnet-5-5/overview) | Published API model identifier and model-specific tool/sampling constraints |
| S20 | [OpenAI SDK strict-schema transformer](https://github.com/openai/openai-python/blob/e5de2e5656fb3d4fa70f050195382e6a4d59f806/src/openai/lib/_pydantic.py) | Focused source review 2026-10-02: recursive object closure and required fields for provider wire schemas; no credentialed API acceptance |
| S21 | [Anthropic SDK schema transformer](https://github.com/anthropics/anthropic-sdk-python/blob/18f25547f20cf5f01da69ac611e700e3bc9ebf21/src/anthropic/lib/_parse/_transform.py) | Focused source review 2026-10-02: provider keyword subset and constraint descriptions; original software validation remains authoritative |
| S22 | [Kraken-owned SDK source review](reviews/2026-10-02-kraken-foundation.md) | Focused source review 2026-10-02: pinned official asset/order/trade/ledger/signing references. Direct REST documentation was proxy-blocked; authenticated and unresolved wire conformance stay pending |
| S23 | [Frankfurter official v2 OpenAPI source](https://github.com/lineofflight/frankfurter/blob/915fabfef4f4074437e32e25aac71f437ecfa76e/lib/public/v2/openapi.json) | Focused source review 2026-10-02: provider-scoped rate endpoint and required actual pair/date. [Continuation evidence](reviews/2026-10-02-t17-operations.md) records failed public probes; source review is not a working current FX response |

| S24 | [Kraken Get OHLC Data](https://docs.kraken.com/api-reference/market-data/get-ohlc-data) | Reviewed 2026-10-06: public pair-scoped GET, interval 60, incremental `since`, 720-entry recent-history ceiling and mandatory final uncommitted candle. See [history definitions and bounds](KRAKEN-PRICE-HISTORY.md); documentation verification is separate from actual collection or account acceptance. |

| S25 | [Codex/Claude official subscription preflight sources](reviews/2026-10-06-research-subscription-preflight.md#verified-official-subscription-route-and-exact-blockers) | Reviewed 2026-10-06: real installed CLI login/metadata checks, official subscription/structured interfaces and installed-version retry restrictions; no inference was started. |

## Verification boundaries

No paid OpenAI/Anthropic request, exchange authentication, real order, GitHub Actions billing test or deployment benchmark was performed while preparing the plan. Model selection is an evaluation proposal. The supplied price data is a dated snapshot for a calculator; the runtime must refresh and validate it. EUR/USD conversion and hosting in the cost example are assumptions, not retrieved market/vendor quotes.

The repository itself was inspected through the connected GitHub tools before editing; it contained only a placeholder README at the initial commit. The handoff does not assume an existing implementation or inherited runtime credentials. Implementation verification must be recorded separately with commands, versions and actual results.

The 2026-10-02 SDK reviews do not refresh the 2026-09-29 pricing snapshot or establish owner eligibility.
Direct provider documentation reads were blocked by the workspace proxy. Current private price cards,
credentialed paper observations and separately authorized venue conformance remain runtime prerequisites.
