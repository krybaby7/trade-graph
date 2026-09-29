# 00 — Product, scope and defaults

## Objective and organisation

Trade Graph is an autonomous organisation, not an ensemble that chats continuously. Its objective is net economic performance in EUR: trading results after friction minus all attributable AI and operating costs. Show gross portfolio movement, cash flows and expenses separately. A €100 virtual allocation is a useful economic stress test; it does not imply that frequent trading or paid agents will be worthwhile.

Responsibilities are departments; deployment topology need not mirror the organisation. There are six model-capable roles initially: Leader, Researcher, Trader, Learning Analyst, Optimisation Analyst and Improvement Engineer. Secretary, usage accounting, evidence journaling, scheduling, indicators, broker execution and reconciliation are ordinarily software. The Secretary may use a model only when a specific synthesis task warrants it, with a separate receipt.

The Leader chooses mandates, research priorities, resource allocation, schedules and tested improvements. The Trader makes individual decisions without a committee. Research can deliver findings directly to Trading under the mandate. Learning evaluates decision quality and strategies; Optimisation evaluates workflow utility and expense. Both use the same underlying evidence. The Engineer implements authorised changes rather than merely suggesting them.

## Owner authority versus Leader authority

Owner-only settings: allocated accounts/capital, live enablement, allowable venues and instruments, permission classes, maximum spend, maximum capital at risk, legally required eligibility confirmations, protected accounting semantics and the protected software baseline. The owner may make these permissive for a paper experiment. The Leader may allocate within them, choose less restrictive or more restrictive mandates within the envelope, pause/restart its own operations, and activate approved classes of tested changes. It cannot lift an owner halt, enlarge its allowance, create withdrawal capabilities or redefine success.

The software validates amounts, permissions and execution invariants; it does not add another approval agent. A valid order reaches the execution service even when the rationale explicitly acknowledges uncertainty. Do not hardcode a requirement for unanimous agents, a minimum model confidence score, positive results in every recent trade, or a minimum trade frequency. Confidence is metadata, not a calibrated probability.

## Smallest coherent release: R1

R1 comprises one portfolio, one virtual venue configuration, spot long-only instruments, one Trader, two simple strategy templates, durable tasks, six model roles, a real virtual broker, a deterministic financial ledger, provider adapters, a local web dashboard and automatic versioned configuration/prompt improvements. A reference dataset and scripted providers must exercise the entire loop without network access.

Use BTC/EUR and ETH/EUR as the initial paper universe when the selected public feed supports them. These are test instruments, not recommendations to buy. Normalize venue-specific symbols and obtain precisions/minimums from venue metadata. Paper tests may additionally use synthetic instruments. Prefer native EUR pairs to avoid adding stablecoin exposure and a currency conversion leg solely for the demonstration. Venue and account eligibility remain unresolved for live trading.

Seed two explicitly unproven hypotheses: a slow trend/pullback template and a range-reversion template. Each must define features, entry/exit conditions, sizing envelope, invalidation, costs, execution requirements and validation. The Trader can choose actions within the current hypothesis/mandate rather than merely echo a deterministic signal. Research can propose a replacement. The templates allow prompt/parameter improvement to be meaningful in R1 without granting arbitrary code execution.

Not in R1: leverage, perpetuals, options, margin borrowing, on-chain signing, DEX execution, withdrawals, market making, cross-exchange arbitrage, multi-user custody, foundation-model retraining, a managed proprietary agent runtime, a vector database, autonomous infrastructure purchases or unrestricted code deployment. These are scope choices, not assumptions that those activities are inherently unprofitable.

## Recommended initial paper settings

- Virtual capital €100; operating budget example €5/month, with a €1 reserve inside that total for priority reasoning/recovery. A separate owner-approved engineering/setup budget is optional, but costs remain in all-in performance.
- Maximum account gross exposure 80% and maximum one-asset exposure 50% as paper defaults; no leverage. The owner can change these. Leader sizing and experimentation allowances must fit inside the envelope. Losses beyond intended stop levels remain possible; a price stop is not a loss guarantee.
- One-hour features and four-hour routine Trader opportunities, targeting six model decisions/day on average. Material events can wake it earlier within its run and monetary budgets. Data monitoring and deterministic exits remain active between decisions.
- Daily market research, reusable strategy research, twice-weekly Learning when enough evidence exists, weekly Optimisation, weekly Leader review plus startup and material exceptions. Schedules are defaults and can be changed within the envelope.
- At most two small authorised engineering tasks/month in the lean cost illustration. The runtime uses monetary reservations and step limits, not this number as a universal restriction.
- Owner-reporting timezone may be configured; store all timestamps in UTC. Reporting currency EUR; store original native currencies and units.

All numbers above are proposed configuration, not verified safety limits or empirically successful settings. Before live use, select an envelope consciously and check minimum order sizes against the actual allocation. Do not distort sizes just to satisfy an exchange minimum; return an actionable reason if the allocation cannot support a strategy.

## Meaningful autonomy and experimentation

Support an explicit `experiment_id`, baseline, hypothesis, expiry/review horizon and limited capital/spend allocation. In paper mode the Leader can deliberately test uncertain strategies; in live mode experimentation must fit the owner's envelope. A failed experiment produces evidence, not an automatic permanent restriction. A period of inactivity is not success; unnecessary trading is not success either. Measure decision opportunities, reasons for abstention, net returns, counterfactuals with caveats, and information gained per expense.

Research should distinguish reproducible evidence from marketing claims of a successful trader or AI system. Do not infer a durable edge from screenshots or a provider benchmark. Testable proposals preserve the original source, point-in-time information and the expected mechanism.

## Release exit condition

The complete paper loop must run and recover reliably, financial results must reconcile, every paid call must be visible, at least one authorised artifact change must be implemented/tested/activated, and a failed change must not disturb the current version. R1 completion is a functional milestone, not a positive-return claim. See 08 for the separate economic and live gates.
