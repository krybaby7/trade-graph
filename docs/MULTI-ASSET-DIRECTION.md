# Deferred direction: multi-market opportunity discovery

**Status: deferred by the owner on 2026-10-05.** The current work is the [existing crypto paper experiment](CRYPTO-NEXT-STEPS.md). Revisit this direction on owner request or after reviewing the initial crypto evidence; it is not an active implementation assignment.

Recorded 2026-10-05 after the owner's clarification. Goal: a personal trading organization that searches across crypto, stocks, ETFs and other useful market exposures, compares opportunities and chooses trades aimed at positive results after trading and operating costs. API comparison supports that goal; finding the nicest API is not the investment objective.

This document records the new direction and proposed extension. It does not claim these capabilities have been implemented. Existing checkpoint `5f7cbeb` and T00–T16 completion evidence remain intact within their original release scopes. No runtime policy, paid allowance, account credentials or live trading settings were changed.

## Target workflow

1. Maintain a catalog of instruments and source coverage, separating instruments observable for research from instruments actually available for execution through the owner's accounts.
2. Gather broad, normalized price/history and relevant research inputs through selected sources. A provider may cover many instruments; adding every provider is not required to expand the universe.
3. Use ordinary software to screen the universe cheaply for strategy-relevant conditions, liquidity, trading costs, freshness and material events.
4. Research examines a bounded changing shortlist, attaches source/time evidence, proposes theses and identifies counterevidence.
5. Trader compares the candidate opportunities against current holdings, available cash, costs, loss potential, uncertainty and holding horizon. It may trade, adjust, hold or do nothing without individual Leader approval.
6. Execution routes permitted orders to the appropriate paper account or eventual selected broker, maintaining exact account/venue/currency identity.
7. Accounting and Learning measure realized results, missed opportunities, failed hypotheses and the incremental contribution of additional sources. Optimisation measures useful work relative to expense.
8. Leader changes priorities, schedules and bounded allocations or commissions tested improvements within the owner's chosen universe and limits.

Do not add a mandatory model-confidence threshold, committee approval or a rule that every valid uncertain trade must avoid loss. Opportunity ranking is a supported estimate, never knowledge of the future most-profitable trade.

## Department responsibilities

- **Research:** broad cross-market coverage and deeper analysis of shortlisted candidates; direct findings to Trader.
- **Trader:** cross-market opportunity comparison, position management and order choices within available account funding and owner limits.
- **Leader:** research priorities, mandate/resources/schedules and experimentation; no per-order approval committee.
- **Learning:** evidence about theses and decision quality across assets, including counterexamples and forward outcomes.
- **Optimisation:** useful source/model/workflow contributions and attributable costs.
- **Engineer:** implement and test commissioned extensions within existing allowed classes; broader code authority remains a separate decision.
- **Secretary and screening services:** ordinary software organizes work and reduces the large universe into bounded decision context.

The six AI roles can be retained. Screening need not become another permanently running paid AI role.

## Asset coverage stages

Initial expanded coverage: selected spot crypto, stocks and ETFs. ETFs can offer exposure to regions, sectors, bonds and commodities such as gold where those instruments are available. Each exact listing and currency remains distinct.

Other products can be added when an experiment and account capability justify them. Direct futures, options, margin, leveraged FX, shorting and on-chain execution are distinct execution/accounting projects; the request to analyze broadly is not an instruction to silently enable them.

Research coverage may include information about those markets before the system can trade those products. Signals from a commodity or index do not require buying the exact source instrument.

## Reusable implementation

The current six-role workflows, scheduler, model/budget gateway, dashboard, journals, version checks, much of the ledger and provider-neutral MarketData/Broker contracts remain foundations. Venue-tagged observations and instrument rules support new adapters.

The present release deliberately targets one venue configuration, spot long-only crypto, BTC/USD and ETH/USD examples, and two unproven strategy templates. The public runtime selects Kraken and the existing paper matcher is not a multi-venue allocator. Stocks/ETFs and broader selection are therefore real scope extensions.

## Minimum extensions

- An instrument catalog with stable identity, asset class, listing/venue, quote/settlement currency, availability and data-source mapping; avoid treating a bare ticker as a universal instrument ID.
- Provider routing and collection for the selected broad sources, retaining raw/source/time provenance and declared coverage gaps.
- Market sessions, calendars, holidays and stale-data interpretation for non-24-hour markets.
- Stock splits, dividend investment income, adjusted-history policies and applicable cash settlement for credible equity performance.
- Opportunity records with bounded evidence, as-of time, thesis, holding horizon, invalidation, costs, feasibility, uncertainty and source/version attribution.
- Trader context selection that can compare candidates across the expanded universe instead of only a small static symbol list.
- A view of total portfolio exposure and capital allocated/available across accounts. Cash held at one broker is not instantly spendable at another, and copied paper portfolios are not additional real money.
- Separate venue-specific paper execution paths and assumptions; do not mix observations from different venues into an undifferentiated broker merely because symbols match.
- Appropriate cross-market baselines, total-return handling and explicit all-in expense allocation.

The existing record/accounting/controller safeguards should be retained rather than rewritten during expansion. Current host/network and provider configuration still needs completion for funded operation.

## How the earlier API shortlist fits

Kraken, Binance and Coinbase are crypto candidates; Alpaca and Interactive Brokers are stock/ETF data/broker candidates, with Tradier a reserve. Current account availability, subscriptions and request shapes require verification. Neither a payment card nor a marketing feature list settles account access.

Select coverage and execution interfaces separately. Aim for a broad useful universe with a manageable number of reliable connections. Measure additional sources' cost and incremental usefulness; retain a source if it improves evidence, operations or outcomes enough to justify it.

## Evaluation

Compare feeds/providers on quality within the same instrument/currency and comparable periods. Separately compare portfolio strategies over predeclared future windows, including cash/no-trade, buy-and-hold/appropriate total-return benchmarks and simple rule-based controls.

Record all attempted variants and costs. Scanning more instruments creates more opportunities to find an accidental historical winner; forward evaluation must not turn that selection into a profitability claim. More API coverage does not establish an edge by itself.

The meaningful milestone is a working cross-market paper opportunity loop with trustworthy after-cost records, followed by enough future evidence to assess whether it improves on simple alternatives. Live allocation remains a separate owner decision.

## When this direction is resumed

When resumed, update the implementation roadmap around this expanded coverage and opportunity-selection layer, while preserving completed release evidence and unfinished external acceptance. Retain the API comparison plan as one workstream, rather than the whole product objective. No new task completion or supported economic result is asserted by this memo.
