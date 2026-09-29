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

## Verification boundaries

No paid OpenAI/Anthropic request, exchange authentication, real order, GitHub Actions billing test or deployment benchmark was performed while preparing the plan. Model selection is an evaluation proposal. The supplied price data is a dated snapshot for a calculator; the runtime must refresh and validate it. EUR/USD conversion and hosting in the cost example are assumptions, not retrieved market/vendor quotes.

The repository itself was inspected through the connected GitHub tools before editing; it contained only a placeholder README at the initial commit. The handoff does not assume an existing implementation or inherited runtime credentials. Implementation verification must be recorded separately with commands, versions and actual results.
