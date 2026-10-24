# W5-C T42: Scorecard: result after fees vs. cash and Bitcoin

For the owner: The software can now make a scorecard of the paper account for any period, for example the four
weeks of the scored test: what the Trader made or lost after every fee, compared with simply keeping the dollars and
with buying Bitcoin at the start and selling it at the end, plus the number of trades, how many won, the average win
and loss, the worst fall and the longest run of losses, in dollars and in euros. Beside it, it shows what the AI
really cost in those same days, from receipts, with costs not known yet shown as unknown rather than zero, and it
says plainly that practice money never pays for the AI and that a good scorecard is neither proof of profit nor
permission to trade real money. Nothing real has been scored yet because the new engine is not installed (step L3);
once it runs you can read a scorecard at any time with `trade-graph scorecard --format text`, and nothing here needs
a decision from you.

Branch: `claude/t42-scorecard`, head: the commit that adds this handoff's pull request link (code at
`cb114a5`; the handoff itself first committed in `086fcaf`). Pull request:
https://github.com/krybaby7/trade-graph/pull/13 (not merged).

## What was built

- `src/trade_graph/kernel/trade_metrics.py` (W2-A's sums, extended, not forked): `TradeFill.from_fill_record(fill,
  fill_ref=, base_asset=, quote_asset=)` reads a ledger `FillRecord` with every fee leg (quote fee; a fee in the coin
  changes what is held and is valued at the fill price; a third-asset fee at its identified rate, refused without
  one; a net rebate moves the principal). No other sum changed.
- `src/trade_graph/application/scorecard.py`: the read model. `compute_scorecard(database, portfolio_id=, end=,
  start=None, cost_profile="kraken-spot-starting-tier")` → `Scorecard` (`metrics` = the kernel's `TradeMetrics`,
  `ending_equity_marked`, `exit_fees`, `carried_in`, `provisional` reasons, `btc` (`BtcComparison`), `eur`
  (`EurReporting`), `ai` (`PeriodSpending`), `owner_test_reading` (D138 item 7)), `Scorecard.document()` (JSON with
  `scorecard_sha256`), `format_scorecard_text()`, `ScorecardUnavailable` (plain reasons), `GATES_STATEMENT`. It
  reads a `Database` in one snapshot or any read-only sqlite3 connection, and writes nothing.
- `src/trade_graph/application/ai_spending.py` (W2-C's module, extended): `period_spending(database, start, end)` →
  `PeriodSpending` (Perplexity, Jev and every other paid card; actual / estimated / unresolved per department; test
  calls apart; Codex turns counted). The HQ report is unchanged: `_calls` now shares `_settled` (one row per
  reservation) and `_card_index` with it, `_call` takes an optional set of Perplexity cards, `_codex` calls
  `_codex_between`.
- `src/trade_graph/application/ledger.py`: `Ledger.fx_rate(base, quote, at)`, the reference rate the books use (read
  only).
- `src/trade_graph/market_data_cli/scorecard.py` (discovered; `cli.py` unchanged): `trade-graph scorecard
  [--database] [--portfolio-id] [--start] [--end] [--cost-profile] [--format json|text]`, read-only, never creates a
  database; an unscorable period prints `{"available": false, "reason": ...}` with exit code 1.
- `src/trade_graph/kernel/authority.py`: `application/scorecard.py` added to `PROTECTED_PATH_PREFIXES` (a scoring
  definition, like the kernel sums).
- `docs/SCORECARD.md` (owner summary, rules, command, limits); one link line in `docs/PRACTICE-RANGE.md`.
- Tests: `tests/integration/test_scorecard.py` (hand-checked golden day and 15 more cases),
  `tests/integration/test_scorecard_cli.py`, `tests/integration/test_ai_spending_period.py`,
  `tests/property/test_scorecard_properties.py`, `tests/unit/test_scorecard_stays_offline.py`, five new cases in
  `tests/unit/test_trade_metrics.py`; helper `tests/scorecard_support.py` (a synthetic paper ledger through the real
  `Ledger` and `BudgetGateway`); golden document `tests/fixtures/scorecard/golden_scorecard.json` with a README
  labelling it synthetic.

## Decisions (reserved block D200–D204)

- **D200 (2026-10-17, implementation default for T42): the period and the valuation.** A scorecard covers
  `(start, end]` of one paper portfolio, read from records stamped up to `end`. The default start is the opening
  deposit, or the first BTC price in the account currency recorded after it (so holding BTC is valued from the first
  moment); the default end is now (the command refuses a future end). A deposit or withdrawal inside the period, or
  an operating cost paid from the paper cash (an embedded expense), makes it unscorable with the reason (a paper
  reset opens a new account, 10-PAPER-CAPITAL.md). Only cash in the account currency (the opening deposit's, USD)
  and coins whose marks are in it are counted; anything else is refused rather than converted at a guessed rate.
  The account is valued from the ledger's own journal postings (`cash`, `cash:*`, `inventory`) and its marks (the
  ledger's choice: the newest observed, the first recorded of equal times): at the start as the ledger values it;
  coins already held then are carried in as bought at the start price with no fee (`carried-in:<coin>`); the equity
  curve has a point after every posting and at every new price of a coin held; at the end each coin still held is
  valued as if sold at its mark with the taker fee of the fee profile, the same terms holding BTC gets, and that is
  the curve's last point (as D169 does for the practice range). The running books must equal the ledger's own replay
  (`Ledger.books` valued at the same prices) at both ends, or nothing is scored. A price older than 15 minutes (as
  D182 reads marks) or marked stale at the start or end makes the scorecard `provisional` with each reason; a held
  coin with no price at all makes it unscorable; moments when a held coin had no price yet are left out of the curve
  and named. Reason: T42 "from the paper ledger ... over the same period", AGENTS.md (unknown is not zero; the ledger
  is the books) and a fair comparison with holding BTC.
- **D201 (2026-10-17, implementation default for T42): ledger fills in the shared sums.** The scorecard uses W2-A's
  `summarize`, `round_trips`, `worst_drawdown`, `worst_losing_streak` and `hold_benchmark` unchanged (D169). The only
  addition is `TradeFill.from_fill_record`: a fee in the quote currency is the fee; a fee in the coin changes what is
  held exactly as the books post it (a buy holds `quantity - fee`, a sell gives up `quantity + fee`), is valued at
  the fill price and moves the principal with it, so `sold - bought - fees` is the cash the trade made; a fee in a
  third asset is valued at its identified rate and refused without one; a net rebate lowers a buy's principal or
  raises a sell's, because fees paid are never negative. Fills are ordered by fill time and ledger sequence; a late
  fill recorded inside the period counts from the start. Reason: W2-A's handoff ("convert fills with fees in another
  asset to quote-currency TradeFills before summing") and the instruction to extend the sums, not fork them.
- **D202 (2026-10-17, implementation default for T42): the comparisons and EUR reporting.** Holding cash earns
  nothing (D127), so beating cash means a result above zero after fees. Holding BTC spends the starting equity on BTC
  at the ledger's newest BTC price in the account currency at or before the start, with the taker fee, floored to the
  paper instrument's lot (`instruments`, venue `paper`; 0.00000001 without one, as D167), and sells at the newest
  price at or before the end with the fee. The fee profile is `kraken-spot-starting-tier` (the paper broker's
  default, D146), the revision in force on the start day, or the one named with `--cost-profile`; its reference and
  hash are in the document. Without a BTC price at either end the comparison is unavailable with the reason, never
  invented; an old or stale BTC price makes the scorecard provisional. EUR reporting uses the reference rate the
  books use (`Ledger.fx_rate`): the starting equity at the start rate, everything else at the end rate; holding the
  starting dollars as cash is the EUR baseline, so the dollar's own move is shown as the currency effect and the
  result against holding dollars is the USD result at the end rate (A43). Without a rate the EUR figures are
  unavailable; a stale rate is labelled. Reason: T42 "versus holding cash and holding BTC over the same period ...
  USD native units with EUR reporting", D138 item 7 and A43.
- **D203 (2026-10-17, implementation default for T42): AI spending beside the result.** The AI spending of a period
  is every budget reservation created inside `(start, end]`, with receipts recorded up to `end`, counted once with
  W2-C's rules (D175): actual, estimated (conservative charge) or unresolved (no receipt by the end, counted at its
  held maximum, never zero, or "can't be valued"), in USD, per department, test calls counted apart and never added.
  Unlike HQ's testing view it covers every paid price card (Perplexity, Jev and any other, as `other`), so nothing
  paid is left out, and the whole installation, because AI budgets belong to the installation, not to one paper
  account (10-PAPER-CAPITAL.md). Codex subscription turns are counted per department, not priced. The figure is shown
  beside the result in USD and at the end rate in EUR, and is never subtracted from or paid by the simulated paper
  result (`paper_result_pays_for_ai: false`). Reason: T42 "AI spend from receipts, including unresolved usage, shown
  beside trading results" and T40's "paper gains never pay for AI".
- **D204 (2026-10-17, implementation default for T42): gates, the owner's test rule and the record.** Every
  scorecard, in JSON and text, carries `GATES_STATEMENT` ("Working software, economic evidence and permission to trade
  real money are three separate gates ...") and a `gates` block that judges none of them (`judged_here: false`,
  `authorized: false`). It reads D138 item 7 from its figures: `long_enough` (at least 28 days and at least 30
  completed trades), `beats_cash`, `beats_btc`, and `keep_going` only when long enough and BTC is available,
  labelled as information, not a pass mark or permission. The scorecard is computed on demand and hashed
  (`scorecard_sha256` over its canonical document; reruns reproduce it), so no state migration was needed and the
  reserved 0037 stays unused. It is a protected path. It is not shown in HQ in this package: W5-B changes the same HQ
  files at the same time, and T43's final report is the first consumer; showing it in HQ is later work (mind the
  80 KB payload budget). Reason: the T42 acceptance, AGENTS.md (acceptance gates not agent-editable) and keeping the
  wave's packages apart.

## Evidence

All runs on Python 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, at `cb114a5` (the
handoff commits after it change only this file). No network call, key, paid call, model call or exchange call in any
test.

- Tests written first. Each new test file was run before its module existed and failed:
  `test_trade_metrics.py`'s five new cases (`AttributeError: type object 'TradeFill' has no attribute
  'from_fill_record'`), `test_ai_spending_period.py` (`ImportError: cannot import name 'period_spending'`),
  `test_scorecard.py` (`ModuleNotFoundError: trade_graph.application.scorecard`), `test_scorecard_cli.py` (`cannot
  import name 'scorecard' from trade_graph.market_data_cli`), and `test_scorecard_stays_offline.py`'s protected-path
  case (failed until the path was added). Rechecked against the untouched base `b09cae4` (an exported copy with the
  new tests): four new files fail at collection, `test_scorecard_stays_offline.py` fails 3 of 5 and
  `test_trade_metrics.py` fails its 5 new cases (its 9 old ones pass). The first golden run caught a real bug
  (coins carried in at the end price instead of the start price), and the first property run a test-model bug (a
  fill at the start instant counted after the start).
- Hand-checked golden day (`tests/integration/test_scorecard.py`, arithmetic in the comments): USD10,000; ETH in two
  partial maker buys and two partial taker sells, +220.64 after 74.36 of fees; BTC bought and stopped out, -66.6;
  0.5 ETH still held, valued as sold at 3200 less the 12.8 taker fee; fees paid 102.06; ending equity 10210.14,
  result +210.14 (0.021014); worst drawdown 165.26 = 0.0161415092 from the 03:05 peak to the 12:00 trough; holding BTC
  +169.31216384 (0.16534391 BTC), so +40.82783616 against BTC; EUR 9000 → 9393.3288 with a +200 currency effect and
  +193.3288 against holding dollars; AI spending 0.009 + 0.000042 known, one estimate and one unknown at their
  maxima, one test call apart. The whole document equals `tests/fixtures/scorecard/golden_scorecard.json`.
- Property test (`tests/property/test_scorecard_properties.py`, Hypothesis, derandomized, 30 examples; also run once
  with 400 random examples, all passed): over random buys and sells of two coins in partial amounts, fees in USD or
  in the coin, moving prices and a start while coins are held, the books agree with the ledger at both ends, the
  result is exactly the trades plus the open positions as if sold, fees paid are exactly the fills' fees, the worst
  fall lies in [0, 1) and reruns give the same hash.
- `uv run ruff check src tests`: All checks passed!
- New tests: `uv run pytest -p no:cacheprovider -rfs tests/integration/test_scorecard.py
  tests/integration/test_scorecard_cli.py tests/integration/test_ai_spending_period.py
  tests/property/test_scorecard_properties.py tests/unit/test_scorecard_stays_offline.py
  tests/unit/test_trade_metrics.py`: **47 passed**, 0 failed, 0 skipped (10.7 s).
- Related tests: every test module that imports or exercises a changed module (W2-C's spending read model and HQ's
  spending, projection, payload, routes and cost views; W2-A's practice range and the day plans that read its
  reports; the ledger, its fee and FX paths and the financial dashboard; the protected-path users (Engineer and
  application engineering) and authority binding; every candle, coin, news, money-limit and history command that
  builds the CLI parser), 38 modules plus the 4 new integration and property ones, one run of
  `uv run pytest -p no:cacheprovider -rfs --durations=8 <files>` at `cb114a5`: **725 passed, 0 failed, 1 skipped**
  (608.85 s while the two other wave 5 sessions ran their tests in the same container; the skip is the existing
  `tests/archive_support.py:104`, the container runs as root). The 38: `tests/integration/test_ai_spending.py`
  `tests/integration/test_ai_spending_cli.py` `tests/integration/test_hq_spending.py`
  `tests/integration/test_hq_projection.py` `tests/integration/test_hq_payload.py`
  `tests/integration/test_hq_routes.py` `tests/integration/test_hq_cost.py`
  `tests/integration/test_migration_0032.py` `tests/property/test_ai_spending_properties.py`
  `tests/test_hq_static.py` `tests/integration/test_practice_range_replay.py`
  `tests/integration/test_practice_range_service.py` `tests/integration/test_practice_range_cli.py`
  `tests/integration/test_migration_0030.py` `tests/property/test_practice_range_properties.py`
  `tests/integration/test_okx_history.py` `tests/integration/test_day_plans.py`
  `tests/integration/test_day_plan_recovery.py` `tests/property/test_day_plan_protection_properties.py`
  `tests/integration/test_ledger_store.py` `tests/integration/test_dashboard_financial.py`
  `tests/integration/test_native_fee_components.py` `tests/integration/test_native_quote_cost.py`
  `tests/integration/test_fx_availability.py` `tests/integration/test_native_exposure_stale_reporting_fx.py`
  `tests/integration/test_append_only_fill_replay.py` `tests/integration/test_engineer.py`
  `tests/integration/test_application_engineering.py` `tests/integration/test_authority_binding.py`
  `tests/integration/test_mandate_legacy_ids.py` `tests/integration/test_money_limits_cli.py`
  `tests/integration/test_coin_scanner_cli.py` `tests/integration/test_news_alarm_cli.py`
  `tests/integration/test_history_cli.py` `tests/integration/test_archive_cli.py`
  `tests/integration/test_backfill_cli.py` `tests/integration/test_collect_candles_cli.py`
  `tests/security/test_news_alarm_boundary.py`.
- Unit safety net: `uv run pytest -p no:cacheprovider tests/unit -rfs` (the configured `-q`; an extra `-q` only hides
  the count): **2148 passed, 0 failed, 7 skipped** (64.3 s; the skips are the existing
  `test_indicator_cross_check.py:117` (4, talipp lacks two indicators) and `tests/archive_support.py:104` (3, root)).
- `python3 scripts/check_plan.py`: "Plan valid: 46 tasks; acyclic dependencies; references/configuration checked".
  `python3 scripts/test_planning.py`: 11 tests OK. `python3 scripts/check_repository_hygiene.py`: "Repository hygiene
  passed: no private runtime files or recognized credentials".
- Full suite: not run (night shift: the wave integration runs it).
- Speed: one scorecard over four weeks of 10-second prices for four coins (967,684 marks) and 60 trades took 3.47 s
  in this container (15.7 s before the price pass was reworked; most of the rest is reading the marks).
- Real or synthetic: everything is synthetic (`tests/scorecard_support.py` writes made-up fills, prices, rates and AI
  calls through the real `Ledger` and `BudgetGateway`). No real paper run exists to score yet.

## Schema and dependencies

- State migration: none. The reserved 0037 is unused (the scorecard is computed on demand from existing tables).
  Candle database: none.
- Dependencies added: none. `pyproject.toml` and `uv.lock` unchanged.

## Not done, open or for someone else

- **Owner / step L3–L4:** nothing to do for the scorecard itself. After the new engine is installed, `trade-graph
  scorecard --format text` shows it; T43's report should pass the test's start and end (`--start`, `--end`).
- **T43 (scored paper test):** call `compute_scorecard(database, portfolio_id=..., start=<test start>, end=<test
  end>)` and include `document()`; `owner_test_reading` is D138 item 7. Keep BTC/USD in the paper feed's
  `paper_symbols` (it is in the default), or holding BTC is reported unavailable.
- **Later HQ package:** the scorecard is not in HQ yet (D204). A block would need caching like HQ's money facts,
  since one scorecard over four weeks reads every price of the coins held (about 3.5 s at the size above), and must
  stay within the 80 KB payload budget.
- **Integrator:** copy D200–D204 into `docs/DECISIONS.md`; no migration to re-link (0037 unused, so W5-A's 0035 and
  W5-B's 0036 are the wave's only new versions). Shared files touched: `kernel/authority.py` (one line appended to
  `PROTECTED_PATH_PREFIXES`; W5-A may append its own lines there too, keep both), `application/ai_spending.py`
  (additive; HQ's report unchanged, W2-C's tests pass), `application/ledger.py` (one read-only method),
  `kernel/trade_metrics.py` (one classmethod), `docs/PRACTICE-RANGE.md` (one line). No contract
  (`src/trade_graph/contracts/`), provider schema, HQ file, migration or dependency changed.
- **Found outside the package (not changed):** `valuation_marks` has no index on `(portfolio_id, asset,
  observed_at)`, so every "newest price of a coin" query (execution, the daily loss limit, HQ, this scorecard)
  scans the whole table, which grows by one row per coin about every 10 seconds; an index in a later migration
  would keep those reads fast.
