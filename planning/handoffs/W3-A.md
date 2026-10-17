# W3-A T32: Your money limits and coin rule enforced by software

For the owner: Your money limits are now software rules: once you record them, the paper account stops new
buying for the rest of the day after losing 3% of the day's starting value (fees included), never puts more than
20% of the account into one coin or 80% into coins altogether (counting orders still waiting as if they had
filled), and buys only coins that passed the coin scanner's latest checks, while safety exits and sales always
keep working. Nothing is switched on yet: the Trader's day plans stay off until you run one command at step L3,
`trade-graph money-limits --confirm`, which records your D138 settings (run it without `--confirm` first to see
exactly what it will record). Nothing needs a decision from you; the tests used made-up numbers, and no real
money, key, paid service or exchange was used.

Branch: `claude/t32-money-limits`, head: the commit that adds this line; code at `6e8914c`. Pull request:
https://github.com/krybaby7/trade-graph/pull/10.

## What was built

- `src/trade_graph/contracts/money_limits.py`: `OwnerMoneyLimits` (daily loss limit as a Decimal fraction, the
  trading-day start `HH:MM` UTC defaulting to `00:00`, the account currency) and `CoinScope` (the scanner rule,
  USD, a 2-hour scan age, unknown refuses new entries). `contracts/models.py`: `OwnerPolicy.money_limits`,
  `OwnerPolicy.coin_scope` and `Mandate.coin_scope` (`listed` or `owner_rule`), all left out of a stored
  document when absent, so every existing revision reads and hashes byte for byte as before.
- `src/trade_graph/kernel/money_limits.py` (protected): the trading day, the loss limit and `check_entry`, which
  values every entry and every resting entry as if filled (highest of its price, the buy quote and the mark)
  against the equity left after their worst fees and mark shortfall, for the coin bought and every other coin.
- `src/trade_graph/kernel/riba.py` (protected): `refuse_riba_symbol` (only plain spot pairs; futures,
  perpetuals, swaps, dated contracts and Kraken staking/earn variants refused) and `interest_posting_reasons`.
- `src/trade_graph/application/money_limits.py`: `MoneyLimitGuard` (day-start values, the loss status, the
  day's stop, interest-like posting flags, the day-plan gate) and `propose_money_limits` /
  `record_money_limits` (the owner's audited, revision-checked policy and mandate revision); `D138_VALUES`.
- `src/trade_graph/application/execution.py`: `authorize(..., day_plan=False)` applies D127, the coin rule, the
  loss limit and the as-if-filled shares to every entry, and refuses a day-plan entry until money limits are
  recorded; the balance check now runs before the exposure check; `_unsent_increase_block` re-checks them at
  dispatch; `enforce_money_limits(portfolio_id)` is the management step (records, stops, cancels resting buys);
  `_cancel_for_pause` takes a reason and returns what it cancelled.
- `src/trade_graph/application/authority.py`: the coin scope (`require_coin_rule`, `entry_scope`, an
  injectable `coin_rule` defaulting to `CoinScanStore.coin_rule`), and `install_policy` refuses
  `leverage_allowed` (D127). `kernel/authority.py`: `owner_scope_covers`; a mandate may follow the coin rule only
  under a policy that has one, and under the rule a listed mandate may name any pair the rule covers; the Leader
  is forbidden `edit_owner_policy` and `raise_money_limits`; the new contract and application modules are
  protected paths.
- `src/trade_graph/application/paper_service.py`: `_management` runs `enforce_money_limits` for every managed
  portfolio on every tick; its failure is reported (`money_limits:`) but never holds back dispatch, so
  protective exits keep going. `src/trade_graph/paper_runtime.py`: under a recorded coin rule the feed may
  watch any pair the rule can include.
- `src/trade_graph/adapters/persistence/money_limit_schema.py`, `migrations/versions/0033_money_limits.py`, two
  lines in `adapters/persistence/migrate.py`: state migration 0033 (four append-only tables).
- `src/trade_graph/market_data_cli/money_limits.py`: the owner command `trade-graph money-limits` (discovered like
  the other commands; `cli.py` is unchanged).
- `src/trade_graph/api/hq_copy.py`: plain words for the six new refusal reasons.
- `docs/MONEY-LIMITS.md`: the rules, the command and the contracts for W4-A and W5-A.
- Tests: `tests/unit/test_money_limit_rules.py`, `tests/property/test_money_limit_properties.py`,
  `tests/property/test_money_limit_execution_properties.py`, `tests/integration/test_money_limits.py`,
  `tests/integration/test_money_limits_cli.py`, `tests/integration/test_migration_0033.py`, six new rows in
  `tests/unit/test_hq_refusal_copy.py`; helpers in `tests/money_limit_support.py`.

## Contracts other packages use

- **W4-A (T34, day plans).** Install each day-plan entry with `Execution.authorize(portfolio_id, decision,
  day_plan=True)`. It raises `AuthorityDenied` (`application.money_limits.DAY_PLAN_ORDERS_OFF`) until the active
  owner policy holds `money_limits`, and otherwise applies every rule (coin rule, as-if-filled shares, loss
  limit, D127). `MoneyLimitGuard.require_day_plan_orders(policy)` is the same gate on its own. Protective exits
  stay `place_protection` and are never gated. The loss stop, like a pause, cancels resting buys and keeps exits.
- **W5-A (T35, event engine).** `await execution.enforce_money_limits(portfolio_id)` returns
  `{"portfolio_id", "money_limits": LossStatus.document() or None, "flagged": [...], "cancelled": [intent ids]}`;
  run it at the day start and after price updates (the paper service already does every tick). A new stop
  (`money_limits.stopped` turning true, activity event `daily_loss_limit_reached`) is a natural Trader wake.
  The day start comes from `policy.money_limits.trading_day_start_utc` (default `00:00`;
  `kernel.money_limits.trading_day`).
- **HQ (W5-B/W6-C or later).** `MoneyLimitGuard.status(portfolio_id, policy).document()`: day start, resumes at,
  day-start value and basis, current value, loss, limit amount, reached, stopped, and why it can't be checked.

## Decisions (reserved block D180–D184)

- **D180 (2026-10-17, implementation default for T32): the owner-policy fields and the day-plan gate.**
  `OwnerPolicy.money_limits` holds the daily loss limit (a Decimal fraction above 0 and below 1 of the day-start
  value, fees included), the trading-day start (`HH:MM` UTC, default `00:00`, D138 item 6) and the account
  currency the loss is measured in (the paper account's USD; EUR reporting labels never move it). The amount per
  coin and the total stay the existing `maximum_single_asset_exposure_fraction` and
  `maximum_gross_exposure_fraction` rather than a second copy; a revision holding `money_limits` is the one in
  which the owner recorded all four values. `OwnerPolicy.coin_scope` makes the coin list the scanner rule.
  Absent blocks are left out of stored documents, so older revisions and mandates hash exactly as before. Only
  the owner installs policy; a mandate cannot carry a loss limit or day start, may follow the coin rule
  (`coin_scope: owner_rule`) only under a policy that has one (the field is left out of the JSON schema, like
  the document of a listed mandate, so a model-made mandate is always listed), and may only narrow the
  fractions; the Leader is
  also forbidden `edit_owner_policy` and `raise_money_limits`, and the new contract and application modules are
  protected paths like the kernel. Day-plan entries (`authorize(..., day_plan=True)`) are refused until such a
  revision exists; the existing Trader path keeps working without one, and exits are never gated. Reason: D138
  ("until then T32 and T43 keep refusing to install orders without owner-recorded values") and AGENTS.md
  (protected owner policy, deterministic permission checks).
- **D181 (2026-10-17, implementation default for T32): entries counted as if filled.** Every entry and every
  resting entry already authorized is valued at the highest of its own price, the current buy quote and the
  coin's mark; the coin being bought is held at the highest of its mark, quote and order price, other coins at
  their marks; the caps are compared with the equity left after every counted entry paid the reserve fee rate
  and any loss it could take against the mark when it fills (quantity × (valuation − mark)); and a new entry may
  not push another coin that was within its share above it through its fees. This applies to every entry, with
  or without recorded limits: the earlier check valued a resting buy and the coin already held at the order's
  own price, so a resting buy below the market could fill past the per-coin share (the property test found it,
  and the old valuation fails both property tests). The balance check now runs before the exposure check so an
  order the account cannot pay for says so. A coin that grows past its share because its price rose is not a
  breach and is not sold. Reason: D138 item 2 "resting entries count as if filled" and the acceptance "no sequence
  of resting-entry fills can breach per-coin or total exposure".
- **D182 (2026-10-17, implementation default for T32): the daily loss limit.** The day-start value is the
  account's native equity read point-in-time at the day start (books as they stood, the newest marks at or
  before it, each at most 15 minutes old); when that is impossible (the portfolio opened later, or a held coin's
  mark was missing or older because the service was down) the first valuation the guard makes later that day is
  used and labelled `first_valuation_after_start`. The limit is reached when the day-start value less the
  current value (equity with fees paid and positions at marks no older than 15 minutes) is at least the fraction
  of the day-start value. `authorize` refuses new entries as soon as it is reached and only reads; the
  management step, every service tick, records the day's stop and cancels every resting or unsent buy; unsent
  buys are re-checked at dispatch; exits, protective exits and reconciliation are untouched and nothing is sold.
  The stop lasts until the next day start; cancelled entries are not restored. Without a day-start value or a
  current value with fresh marks, new entries are refused as stale, never allowed on a guess. Deposits and
  withdrawals during the day are not adjusted for (a paper account has none; a withdrawal would count as a
  loss, the cautious side), and a changed day start applies from the next valuation. Reason: D138 items 1 and
  6, D117, and "unknown is not a yes".
- **D183 (2026-10-17, implementation default for T32): the coin rule and what "unknown" means.** Under
  `coin_scope` an entry needs W2-B's `coin_rule(symbol, as_of=now, max_age=2 hours)` to answer `passed_checks`
  (any pair that passed every check of the newest live scan, not only the shortlist). `excluded` and
  `not_in_catalog` refuse it (an unsent entry is rejected at dispatch). Unknown, `no_recent_scan` or
  `scan_failed`, refuses a new entry as stale (retried, like a stale quote) and holds an unsent one until a fresh
  scan answers. Exits, protective exits and reconciliation never consult the scan, and an entry already resting
  at the venue is not cancelled by a later scan (it passed when it was authorized). Only pairs in the rule's
  quote currency (USD) are covered; a listed mandate still narrows the rule to its list, and may name any pair the rule covers; the paper feed may be
  configured with any pair the rule can include. Reason: D138 item 3 ("passes the scanner's checks at that
  moment") and D171's "nothing is guessed", while selling is never blocked.
- **D184 (2026-10-17, implementation default for T32): D127, the owner command and the records.** Entries on
  anything but a plain spot pair `BASE/QUOTE` are refused (futures, perpetuals, swaps, dated contracts,
  `BASE/QUOTE:SETTLE`, Kraken staking/earn variants such as `ETH.S`); `install_policy` refuses
  `leverage_allowed`; short sales stay impossible because an exit sells only what is held. Deposits, withdrawals
  and transfers whose reference or asset looks like interest, a staking or earn reward, a margin rollover or a
  funding payment are flagged once each for the owner (information only; balances are not changed).
  `trade-graph money-limits` previews by default and with `--confirm` records, in one transaction, a new
  owner-policy revision copied from the active one with the four values and the coin list, a mandate revision
  for the paper portfolio set to those values (the Leader may narrow it again), and an audit row naming the
  replaced revision and both document hashes; it is refused while the paper service runs and, with
  `--expect-revision`, if another revision became active since the preview; the same values again write
  nothing. Its defaults are the D138 values; tests record synthetic ones. Migration 0033 adds four append-only
  tables (`trading_day_values`, `daily_loss_stops`, `owner_policy_audit`, `riba_posting_flags`); they are
  controls and evidence, not added to agent context, the financial checkpoint or the runtime evidence, and the
  ledger stays the books. Reason: D127, D138's closing paragraph and the T32 deliverable "audited and
  revision-checked".

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container. No network,
key, paid, model or exchange call in any test.

- Tests written first: `tests/unit/test_money_limit_rules.py` failed at collection (`ModuleNotFoundError: No
  module named 'trade_graph.contracts.money_limits'`); `test_migration_0033.py` before the schema existed;
  `test_money_limits.py` failed on the missing HQ wording and on the paper service not running the step until
  both were added; `test_money_limits_cli.py` fails with the command module moved away (checked). The first pure
  property test found a real gap (a marketable entry's fees pushing another coin over its share), fixed in
  `check_entry`. Mutation checks: the old valuation (entry at its own price) and dropping the fee floor each
  make `tests/property/test_money_limit_properties.py` fail; the old valuation in `Execution` makes
  `tests/property/test_money_limit_execution_properties.py` fail.
- `uv run ruff check src tests`: all checks passed.
- New tests: `uv run pytest -p no:cacheprovider -rf tests/unit/test_money_limit_rules.py
  tests/property/test_money_limit_properties.py tests/property/test_money_limit_execution_properties.py
  tests/integration/test_money_limits.py tests/integration/test_money_limits_cli.py
  tests/integration/test_migration_0033.py`: **147 passed, 0 failed, 0 skipped** (19 s).
- Related existing tests (every module that imports or exercises the changed code), in four runs:
  - execution and pauses: `tests/integration/test_execution.py test_execution_acceptance.py
    test_native_exposure_stale_reporting_fx.py test_pause_profiles.py test_mandate_legacy_ids.py
    test_paper_fill_engine.py test_append_only_fill_replay.py test_native_fee_reserves.py
    test_submission_completion.py test_execution_reconciliation_ordering.py tests/unit/test_hq_refusal_copy.py`:
    **205 passed, 0 failed, 0 skipped**.
  - HQ, dashboard, paper service, scanner and contracts: `tests/test_hq_template.py test_hq_static.py
    test_dashboard_pages.py tests/unit/test_hq_contract.py test_hq_copy.py test_hq_refusal_copy.py
    test_hq_turn_failure_copy.py test_hq_spending_contract.py test_contracts.py test_protocols.py
    tests/integration/test_dashboard.py test_dashboard_controls.py test_dashboard_financial.py
    test_paper_service.py test_subscription_management.py test_coin_scan_store.py test_coin_scanner.py
    test_coin_scanner_cli.py test_paper_packaging.py test_order_refusal.py`: **446 passed, 0 failed, 0 skipped**.
  - migrations, privacy, checkpoint and boundaries: `tests/integration/test_review_export_safety.py
    test_database_privacy.py test_runtime_evidence.py test_financial_checkpoint.py test_database_locks.py
    test_database_threads.py test_migration_0025/0026/0028/0029/0030.py test_owner_checkins.py
    test_owner_expenses.py test_scheduler_recovery.py test_history_migration.py test_startup_migration.py
    test_protected_evidence_migration.py test_subscription_attempt_migration.py
    test_subscription_admission_migration.py test_native_fee_reservation_migration.py
    test_protected_budget_origin_migration.py test_operations.py tests/unit/test_import_boundary.py
    test_market_data_cli_registry.py` (and `test_migration_0031/0032.py`): **373 passed, 0 failed, 0 skipped**.
  - every other module that uses `Execution`, the authority or the paper runtime (43 modules, in
    `tests/integration/` unless named): test_api_security_live, test_application_engineering,
    test_artifact_consumers, test_authority_binding, test_dashboard, test_dashboard_controls,
    test_dashboard_financial, test_engineering_workflow, test_execution_native_incident_hook,
    test_financial_recovery, test_financial_recovery_history, test_financial_transition, test_history_contexts,
    test_kraken_identity_recovery, test_kraken_live_adapter, test_leader, test_leadership_followup,
    test_leadership_workflow, test_live_economics, test_live_pilot_dispatch, test_live_readiness,
    test_live_upstream, test_native_incident_resolution, test_native_quote_cost, test_operations_evidence,
    test_optimisation_readiness, test_owner_command_recovery, test_owner_expense_upgrade,
    test_paper_forward_producer, test_paper_service, test_plugin_engineering, test_protected_financial_runtime,
    test_public_market, test_research_trader, test_review_regressions, test_runtime_models, test_storage_faults,
    test_subscription_management, test_version_lifecycle, tests/security/test_news_alarm_boundary,
    tests/security/test_subscription_process_boundary, tests/unit/test_protocols, test_first_run_replay:
    **1123 passed, 2 failed, 14 skipped**. The two failures
    (`test_financial_recovery_history.py::test_receipt_incident_tamper_fails_authenticated_projection`,
    `test_financial_transition.py::test_wrong_stale_unapproved_transition_refuses_without_any_writes[target]`)
    were "protected installed package/interpreter pin mismatch": I was editing source files while that run was
    going, which changes the protected package hash mid-run. Both modules re-run on the finished tree: **56
    passed, 0 failed**. The 14 skips are `tests/security/test_subscription_process_boundary.py`, which needs
    bubblewrap (`bwrap`), not installed in the cloud container (as on GitHub). This run started before the last small changes
    (the paper service step, the HQ wording, the read-first locking and the feed-symbol check); the modules those
    touch were re-run on the final tree in the second run above.
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic. `python3 scripts/test_planning.py`: 11 tests OK.
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- **Fix after the wave 3 full suite.** GitHub's `test` job on the night lead's candidate `290713e` found two new
  failures caused by this package: `tests/integration/test_provider_wire_schemas.py::
  test_actual_six_role_wire_dialects_and_original_typed_replies[OpenAIAdapter-leader]` and
  `::test_real_six_role_schema_goes_through_gateway_http_fixture[leader-OpenAIAdapter]` (the Leader reply's
  mandate change "is not valid under any of the given schemas"). Root cause: `Mandate.coin_scope` was in the
  contract's JSON schema but left out of a listed mandate's document (so older mandates hash as before), and
  OpenAI's strict wire schema requires every listed property, so the serialised reply no longer matched its own
  schema. Fix: the field is now `SkipJsonSchema`, so schema and document agree; validation still accepts it,
  older mandates still read and hash byte for byte, and only software (the owner command) sets `owner_rule`, so
  a model-made mandate is always listed, which can only narrow. To keep the Leader able to narrow under the coin
  rule, a listed mandate may now name any pair the rule covers (`kernel.authority.owner_scope_covers`, moved
  from the application layer). New tests `test_the_mandate_schema_matches_what_a_listed_mandate_serialises` and
  `test_under_the_coin_rule_a_listed_mandate_may_name_any_pair_the_rule_covers` fail before the fix and pass
  after. `uv run pytest -p no:cacheprovider -rf tests/integration/test_provider_wire_schemas.py`: 2 failed, 63
  passed before; **65 passed** after. Then every test module that mentions `Mandate`, `OwnerPolicy`,
  `paper_mandate`, `paper_owner_policy`, `seed_paper_authority`, `LeaderReply`, `MandateChange`, the wire
  schemas (`_wire_schema`, `build_body`, `output_schema`), `model_json_schema`, `export_schemas`,
  `schema_version` or golden/snapshot JSON (110 modules found by `grep -rlE` over `tests`, including all of this
  package's), plus `tests/unit/test_hq_refusal_copy.py`, `tests/integration/test_paper_packaging.py` and
  `test_order_refusal.py`: `uv run pytest -p no:cacheprovider -rfs <those 113 modules>`, 66 min: **2832 passed,
  0 failed, 14 skipped** (all 14 in `tests/security/test_subscription_process_boundary.py`, "native Linux
  bubblewrap boundary required": `bwrap` is not in the cloud container). `uv run ruff check src tests`,
  `check_plan.py` (46 tasks, acyclic), `test_planning.py` (11 OK) and `check_repository_hygiene.py`: passed.
- Real or synthetic: everything is synthetic and labelled (a USD10,000 account, scripted quotes, a scripted coin
  rule, and one synthetic coin scan built with W2-B's helpers). No real order, key, paid call or exchange call.

## Schema and dependencies

- State migration: **0033** (W3-A's reservation), registered in `migrate.py` after 0032; the Alembic revision
  `0033` has `down_revision = "0032"`, and `test_migration_0033.py` checks it against the version registered
  just before. Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- Owner, step L3: run `trade-graph money-limits` (preview), then `trade-graph money-limits --confirm` with the
  paper service stopped. Until then day-plan entries are refused, as D138 requires.
- Integrator: copy D180–D184 into `docs/DECISIONS.md`; mark T32 in `planning/progress.json` with this evidence.
  Shared lines touched: `contracts/models.py` (`OwnerPolicy`, `Mandate`), `application/execution.py`
  (`authorize` gains `day_plan`; exposure valuation; `enforce_money_limits`), `application/authority.py`,
  `kernel/authority.py`, `application/paper_service.py` (`_management`), `paper_runtime.py` (feed symbol check),
  `api/hq_copy.py` (six reasons), `adapters/persistence/migrate.py` (one import, one line),
  `tests/unit/test_hq_refusal_copy.py` (six rows).
- Pre-existing, outside this package (W1-B's quote path): on a quote **without a volume figure** a crossed
  resting limit fills half of its remainder, never rounded to the instrument's quantity increment, so the
  decimals keep growing; after about 29 such quotes `Execution._consume_reservation` traps `decimal.Inexact`
  inside `record_fill`. Reproduced on the untouched base `868cda0` (a 1.3 BTC limit buy crossed by
  volume-less quotes). The live paper feed sets Kraken's 24-hour volume on its quotes, so it is mainly hit by
  synthetic or other feeds, but W4-A's resting entries make it likelier; rounding partial fills down to the
  quantity increment (and filling the rest when below the minimum) would fix it. The money-limit tests give
  their quotes a traded volume for this reason.
- Live: `LiveService._management` does not run `enforce_money_limits`, and the protected live financial service
  (`kernel/financial_service.py`) still reads `allowed_symbols`; live trading is off and out of this package's
  scope. A future live package should add both.
- HQ: a service failure of the new step shows as "a routine step is failing" (`money_limits:` is not one of
  HQ's known problem prefixes); the day's loss status is available (`LossStatus.document()`) but not shown in
  HQ yet.
- Retention: about one day-start row per portfolio and day, at most one stop per day; negligible.
