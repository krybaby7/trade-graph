# 07 — Operating costs and USD10,000 paper economics

Reproducible planning model, not a usage quote or return prediction. Official rates checked on 2026-09-29 [S01, S02, S08]. Token sizes, cadence, extra-work buffer, FX, hosting and turnover are assumptions. Run `python3 scripts/cost_model.py`; edit planning/cost-assumptions.json for sensitivity. Runtime receipts, not this example, are authoritative.

## Inference and research

Standard short-context uncached prices used: GPT-6 Luna USD0.10 input / USD0.50 output per million tokens; GPT-6.1 Sol USD2 / USD10; Claude Sonnet 5.5 USD2 / USD10. Web search is USD10/1,000 calls plus applicable content/model tokens [S01, S02]. No cache/batch discounts, fast-mode premiums or hosted-code sessions assumed. Billed output includes reasoning where applicable, not just visible prose.

A 30-day month and these paid request counts give:

| Role | Requests/month | Input/output tokens each | Model | USD/month |
|---|---:|---:|---|---:|
| Trader | 180 | 4,500 / 700 | Luna | 0.144 |
| Research | 30 | 6,000 / 1,000 | Luna | 0.033 |
| Learning | 8 | 8,000 / 1,500 | Sol | 0.248 |
| Optimisation | 4 | 8,000 / 1,000 | Sol | 0.104 |
| Leader | 5 | 10,000 / 1,500 | Sol | 0.175 |
| Engineer | 20 | 20,000 / 4,000 | Sonnet 5.5 | 1.600 |
| Model subtotal | 247 | | | 2.304 |
| Paid search | 60 tool calls | USD0.01 each | | 0.600 |
| Subtotal | | | | 2.904 |
| Failed/retried/extra-work allowance | 20% | Planning buffer, not a fee | | 0.5808 |
| **Paid AI/search estimate** | | | | **3.4848** |

Average input includes instructions, tool schemas and expected retrieved context. A provider-managed search request may invoke multiple searches inside one model response; the Research assumption is only valid if that workflow fits these request/content bounds. Application-managed multi-step search or larger results require more requests/tokens and new reservations. Every follow-up attempt is counted. Software monitoring, execution and Secretary work incur no model tokens but still use infrastructure.

At the **hypothetical planning rate USD1 = EUR0.90**, AI/search is **EUR3.13632/month**, approximately EUR3.14. A separately assumed EUR6 hosting allowance gives EUR9.13632; that allowance is not a verified vendor quote. Replace it with actual host, electricity, backup, taxes and conversion charges. Existing hardware does not make attributable costs disappear.

The optional lean EUR5/month cap with EUR1 priority reserve inside it can fit this illustration without new hosting. Actual usage or a larger Engineer task can exhaust it. Paid calls are disabled until the owner configures a real allowance. USD10,000 virtual funds do not authorize spending or pay invoices.

## Cadence and model sensitivity

Five-minute Trader intervals imply 8,640 Trader requests/month. Holding other assumptions fixed gives EUR10.44576 before hosting/trading fees. This is not a recommendation for high-frequency trading and excludes any extra data/research complexity that cadence would require.

Using Sol instead of Luna for the six daily Trader requests changes its monthly token bill to USD2.88 and total AI/search to EUR6.09120. This exceeds the optional EUR5 cap and requires a revised allocation/cadence or owner-approved budget. Choose models by tested utility, schema reliability and net economics, not price alone. Cheap-model trading quality has not been established.

Two Engineer tasks with ten requests each are an illustration, not a permanent authority restriction. More extensive development can dominate ordinary trading cost. The budget gateway must reserve each task/attempt at the real approved output/tool limits.

## Larger paper account: fixed costs and turnover

Default paper capital is **USD10,000**, or EUR9,000 only under the calculator's hypothetical FX assumption. USD3.4848 of AI/search is **0.034848%** of that virtual starting capital. With a EUR6 hosting allowance, total fixed expense is about **0.101515%** of the illustrative EUR9,000. These are break-even cost contributions, not expected returns.

For a deliberately simple trading-friction stress example, assume 20 round trips, each with USD2,000 entry and approximately USD2,000 exit notional. Two-sided turnover is USD80,000. Use a constant 0.80% taker fee reference from the public lowest spot tier [S08], giving USD640, plus an assumed combined round-trip spread/slippage of 10 basis points on principal, giving USD40. Total illustrative friction is USD680. With AI/search, gross gains must exceed USD683.4848 (6.834848% of the initial USD10,000) just to cover these example costs; with the EUR6 host allowance, about USD690.15147 (6.901515%).

**This fixed-tier example is intentionally conservative, not the expected Kraken fee bill at USD80,000 turnover.** Published fee tiers change with qualifying volume/assets and account/location [S08]. A proper simulator uses the applicable tier at each fill, with rolling-volume state or an explicit conservative fixed-tier assumption. Actual entry/exit values and liquidity differ. No claim is made that 20 such trips is an optimal strategy.

The example shows why larger starting capital reduces fixed-AI cost pressure but does not remove percentage trading friction. Limit orders do not guarantee maker fees/fills. Compare venues only after eligibility and execution-capability checks. Record actual simulated/real fills, deduct incurred fees once, and do not subtract measured slippage again from equity-based P&L.

## Setup and engineering visibility

A hypothetical 200-request build at 20,000 input and 4,000 output tokens/request using USD2/10 rates is USD16 before extras, or EUR17.28 with the same buffer/FX. This is arithmetic, not an estimate of the requests needed to build the application. Human time, compute, experiments and failed work are additional where attributable.

Separate recurring, setup/engineering and all-in inception views. Keep the owner-defined classification fixed; the Leader cannot relabel mistakes as excluded overhead. Across paper resets, real expenses remain in the deployment ledger. Shared expenses are allocated once with auditable weights, not duplicated or erased. Simulated profits never replenish actual API funding.

## Controls

Reserve each paid attempt using bounded input/output/tool counts; reject unpriced models/tiers/tools; enforce nested owner/role/root/period limits. Keep a priority reserve inside the total. Batch only nonurgent work; apply cache savings only when usage confirms them. Reduce low-value research or engineering before management reasoning when funds run low, while deterministic reconciliation/protection continues.

Track actual versus forecast cost, failed/discarded work, useful evidence per expense and net economic performance. The Leader may propose cheaper or better workflows and allocate within the limit. Only the owner can increase that limit or change protected accounting/price definitions.
