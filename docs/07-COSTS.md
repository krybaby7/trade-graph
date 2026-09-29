# 07 — Illustrative operating costs and EUR100 economics

This is a reproducible planning model, not a usage quote, expected return or promise of model quality. Prices were read from official documentation on 2026-09-29 [S01, S02, S08]. Invocation counts, token sizes, retries, FX, hosting and trading volume below are explicit assumptions. Run `python3 scripts/cost_model.py` and edit planning/cost-assumptions.json to test alternatives. The runtime must use actual receipts, not this spreadsheet-like illustration.

## Rates and assumptions

Use standard short-context uncached inference: GPT-6 Luna input/output USD0.10/0.50 per million tokens; GPT-6.1 Sol USD2/10; Claude Sonnet 5.5 USD2/10. Provider web search is USD10 per 1,000 calls plus applicable content/model tokens [S01, S02]. The estimate assumes no batch discount, cache saving, fast-mode premium, regional premium or hosted-code session. Total billed output includes reasoning where the provider bills it; it is not merely visible prose.

Assume a 30-day month. The input sizes include system instructions, tool schemas and expected retrieved context. Calls mean paid model requests; actual multi-call tool loops must be counted individually. Research's average includes expected search-content billing; a larger or multi-request research loop increases the estimate and must be recalibrated from real receipts. Software accounting, Secretary assembly, price monitoring and order tracking have zero model calls, not zero infrastructure cost.

| Role | Monthly model calls | Input / output tokens per call | Model | USD/month |
|---|---:|---:|---|---:|
| Trader | 180 (six/day) | 4,500 / 700 | Luna | 0.144 |
| Research | 30 | 6,000 / 1,000 | Luna | 0.033 |
| Learning | 8 | 8,000 / 1,500 | Sol | 0.248 |
| Optimisation | 4 | 8,000 / 1,000 | Sol | 0.104 |
| Leader | 5 (startup plus reviews) | 10,000 / 1,500 | Sol | 0.175 |
| Engineer | 20 (two tasks, ten calls each) | 20,000 / 4,000 | Sonnet 5.5 | 1.600 |
| **Model subtotal** | **247** | | | **2.304** |
| Paid search | 60 tool calls | Model content included above | USD0.01/search | 0.600 |
| **Subtotal** | | | | **2.904** |
| Retry/failure/extra-work allowance | 20% of subtotal | Planning buffer, not a new fee | | 0.5808 |
| **Estimated paid AI/search** | | | | **3.4848** |

Using **the hypothetical planning rate USD1 = EUR0.90**, not a current FX quote, this is **EUR3.13632/month, approximately EUR3.14**. Adding an optional **EUR6 hosting allowance** gives **EUR9.13632/month, approximately EUR9.14**. The hosting figure is not a verified retail price. Replace it with the actual hosting, backup, electricity, taxes and conversion costs for the chosen deployment. Existing hardware may have zero new cash rental expense but still has attributable operating costs.

The proposed EUR5/month owner limit can fit the lean inference illustration on existing hardware with some headroom; it cannot also fit an additional EUR6 host. The EUR1 priority reserve sits inside EUR5. Actual engineering or exception-heavy weeks may exhaust that budget sooner than a monthly average suggests; the software must obey the cap rather than pursue the assumed cadence at any cost.

## Cadence and model sensitivity

At five-minute Trader intervals there would be 8,640 routine Trader calls per 30 days. With all other assumptions unchanged, the estimate becomes EUR10.44576 before hosting/trading fees. This still assumes the same small contexts and no increase in research/events; real high-frequency operation can be more expensive and needs very different execution/data design.

Using Sol rather than Luna for the six daily Trader calls changes the model's monthly Trader charge from USD0.144 to USD2.88, and the all-role AI/search estimate to EUR6.09120 before hosting. The initial EUR5 cap would then require a lower cadence, a different allocation, or an owner-approved increase. A more capable model is justified only by measured utility that exceeds its extra expense, not by title or prestige.

These are sensitivity calculations, not endorsements of either routing choice. Benchmark cheap-model decision quality, malformed-output frequency and need for fallbacks. One failed large engineering task can dominate the monthly cost of ordinary trading decisions.

## Trading friction on EUR100

The public Kraken lowest-tier spot schedule observed lists 0.40% maker and 0.80% taker; account, region and pair-specific actual rates must be verified [S08, S12]. Illustrate 20 completed round trips, each with EUR20 entry notional and approximately EUR20 exit notional: EUR800 of two-sided turnover. At 0.80% taker fees on each side, estimated fees are EUR6.40. Add an assumed combined round-trip spread/slippage of 10 basis points on the EUR20 principal, giving EUR0.40 across the 20 trips.

Under those simplified fixed-notional assumptions, friction is EUR6.80. Add EUR3.13632 AI/search and gross trading gains need to exceed **EUR9.93632, about 9.94% of the initial EUR100**, merely to break even economically that month, before hosting/tax and other omitted actual costs. With EUR6 hosting, that rises to EUR15.93632. Changing prices, fill sizes and fee currencies make real results different; the actual ledger is authoritative.

The spread/slippage assumption is not measured market data, and placing limits does not guarantee maker fills or profitability. Fewer, more selective opportunities or another eligible venue may change economics; the system should learn and compare rather than assume frequent action is best. The target is net returns, not a steadily rising dashboard or an artificially active Trader.

## Initial build and later improvement spending

Do not hide initial development costs. A purely illustrative 200-request coding effort at 20,000 input and 4,000 output tokens/request using USD2/10 rates costs USD16 before extras, or EUR17.28 with the same 20% buffer and FX assumption. This is not an estimate of how many requests the actual implementation will take. Human time, compute, API experiments and failed work are additional where attributable.

Show separate views for recurring operation, engineering/setup and all-in inception economics, with fixed owner-defined classification. The primary all-in measure still subtracts attributable expenses; an agent cannot move its mistakes out of the score. A separately funded build budget is accounting visibility, not free capital.

## Runtime controls implied by the model

Reserve costs before every paid attempt using output/tool bounds. Refuse unpriced model/tier/tool combinations. Keep role, root-task, daily burst and monthly total limits; reserve capacity for exception handling inside the owner total. Batch nonurgent analysis only when delay is acceptable. Use cached research and bounded context; apply cache discounts only when actual provider receipts show them. Disable paid research/engineering before necessary management reasoning as funding runs low, while deterministic execution and reconciliation continue regardless of model budget.

Track forecasts versus actuals, costs of discarded/failed candidates, percentage of work producing reusable evidence, and net economic performance. Let the Leader propose a cheaper schedule or model allocation. It may not raise its own cap or change the price/accounting definitions used to judge it.
