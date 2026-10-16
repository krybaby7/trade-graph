# W1-B T28: Shared cautious fill and protective-exit engine with dated cost profiles

For the owner: The paper account and the coming practice range now share one cautious "would this order
really have filled?" engine: a waiting order fills only when the price trades past it, a safety exit the
price jumps over fills at the worse price minus a little slippage, and a candle that hits both the safety
exit and the profit target counts as a loss. Fees come from three dated fee records (Kraken today, Kraken
after USD10,000 of monthly volume, and OKX Europe), and a record can never be quietly edited, so old results
keep the fees they were computed with. The cloud could not open Kraken's or OKX's fee pages, so the records
keep the figures from the build plan and say so; the local session should re-check those three pages at
step L2, and nothing here needs a decision from you.

Branch: `claude/nice-thompson-j88ec4` (this session's assigned branch, used instead of
`claude/t28-fill-engine`), head: the commit that adds this handoff; code at `f331ff0`. Pull request: not
opened yet, because this session's GitHub access was refused (see the last section).

## What was built
- `src/trade_graph/contracts/fills.py`: Pydantic contracts. `CostProfile` (a dated, sourced fee tier with
  `ref` = `id@revision` and a canonical content hash), `FeeSource` (URL, figure date, date checked, check
  result), `FillPolicy` (volume share and slippage assumptions, also hashed), `SimOrder` (market or limit,
  with `remaining`, `placement_price`, `eligible_after_utc`), `ProtectiveExit` (long-only stop-loss with an
  optional take-profit, one cancels the other), `SimFill` (fill with fee, reason, reference price and the
  exact profile and policy references and hashes). Decimal only; floats refused; no rebates.
- `src/trade_graph/kernel/fills.py`: the one fill engine, pure functions over exact decimals, in the protected
  kernel. `fill_order_on_candle`, `trigger_exit_on_candle` (practice range), `fill_order_on_observation`,
  `trigger_exit_on_observation` (paper broker), `check_order_rules` and `check_exit_rules` (exact increments
  and minimums, nothing resized). Policies `PAPER_QUOTE_POLICY` (`paper-quote@1`) and `BAR_REPLAY_POLICY`
  (`bar-replay@1`).
- `src/trade_graph/kernel/cost_profiles.json` and `cost_profiles.py`: the three dated profiles
  (`kraken-spot-starting-tier@1` 0.40% / 0.80%, `kraken-spot-after-10k-volume@1` 0.22% / 0.38%,
  `okx-eea-spot@1` 0.10% / 0.20% per side) and the `CostProfileBook`, which refuses a record that differs from
  its content-hash pin, an unpinned record, a missing pinned record, gaps in revision numbers and a revision
  that does not take effect later than the one before. `get`, `resolve("id@n")` and `effective(id, day)`.
- `src/trade_graph/adapters/brokers/paper.py`: the paper broker now fills only through the shared engine
  (market and limit orders as `SimOrder`, its sell stop as a stop-only `ProtectiveExit`). Default fees are
  `kraken-spot-starting-tier@1` (the D14 rates, now as a dated record); `cost_profile=` picks another;
  explicit `maker_rate`/`taker_rate` and fee changes at runtime become labelled `explicit_configuration`
  profiles. Each fill's terms are appended to the broker order document under `fill_terms` (no schema
  change; older documents stay readable). Stop exits now include 0.10% slippage, rounded down to the tick.
- `src/trade_graph/adapters/brokers/matching.py`: removed (the old private matcher).
- `docs/FILL-ENGINE.md`: the rules, the profile table, how to add a revision, and how T29 and T34 use it.
- `pyproject.toml`: `kernel/cost_profiles.json` added to package data (no dependency change).

## Decisions (reserved block D145–D149)
- **D145 (2026-10-16, implementation default for T28):** One fill engine in the protected kernel
  (`kernel/fills.py`) serves the paper broker and the practice range. Entries: no event that began before the
  order (`eligible_after_utc`, `excluded_event_ref`) can fill it, so the bar a decision used never does. On
  candles a resting limit fills only when trades print beyond it (`low < limit` for a buy), at its limit
  (never a better gap price), as a maker; a limit at or through its placement price is marketable and fills
  as a taker at the next open plus slippage, never beyond its limit; market orders fill at the next open
  plus slippage. On quotes a resting limit fills when the opposite best price reaches it (a crossed book,
  not a touch; the T05 behaviour is kept) and market orders take the displayed size at the touch. Fills
  inside a bar are stamped at its close, fills at the open at its open. Reason: D119's "a touch is not a
  fill" and the T28 golden cases, without changing the T05 quote guarantees.
- **D146 (2026-10-16, implementation default for T28):** Cost profiles are dated, sourced, append-only data
  records in `kernel/cost_profiles.json`, each revision pinned by the SHA-256 of its canonical content in
  protected kernel source (`PUBLISHED_PINS`), because the protected package hash covers only the Python
  files and `runtime_artifacts.json`.
  A fee change is a new revision with a later `effective_from`; every fill records the profile reference and
  hash, so a profile change never rewrites an earlier result. The three profiles keep the build-plan figures
  (D126) because kraken.com and okx.com were unreachable from the cloud on 2026-10-16 (the network policy
  refused the connections); each source records `check_result: unreachable_from_build_environment`. The paper
  broker's default profile is `kraken-spot-starting-tier@1`, the same rates as D14. Rates passed in
  configuration are `explicit_configuration` profiles with no source.
- **D147 (2026-10-16, implementation default for T28):** Protective exits are a stop-loss and an optional
  take-profit on one long spot position, one cancelling the other, both filled as triggered taker sells of the
  whole quantity with slippage (build plan "Notes for implementers"). On candles the stop triggers when the
  low reaches it and fills from `min(open, stop)` (a gap gives the worse open); the take-profit needs a trade
  beyond it and fills from its own level, never a better gap open; a bar reaching both takes the stop; in the
  bar the position was entered only the stop can trigger. On quotes the stop triggers at `bid <= stop` and
  fills from the bid, the take-profit needs `bid > target`; no bid, no fill. Default exit slippage is 0.10%
  on both paths; slipped prices round to the price increment against the trader. Consequence: a paper stop
  gapped to a bid of 90 on a 0.1 tick now fills at 89.9 instead of 90.
- **D148 (2026-10-16, implementation default for T28):** Fill policies are hashed records separate from fees.
  `bar-replay@1`: a fill takes at most 10% of the bar's volume (no fill in a bar without volume), 0.10% taker
  entry slippage and 0.10% exit slippage. `paper-quote@1`: the T05 shares (100% of displayed size or quote
  volume, 50% of the order when no volume is known), no entry slippage (real quotes already include the
  spread and buy reservations are sized at the quote, so the paper broker refuses entry slippage) and 0.10%
  exit slippage. The practice range may vary pessimism with a stricter, separately named policy.
- **D149 (2026-10-16, implementation default for T28):** The paper broker records each fill's terms in its
  own order document (`fill_terms`) rather than changing `FillRecord` or adding a migration, so ledger
  identities and old databases are untouched. Submitted take-profit and one-cancels-the-other orders in the
  paper broker are left to T34, which attaches exits to entries; the engine already supports them.

## Evidence
- Tests written first: `tests/unit/test_fill_engine.py`, `tests/unit/test_cost_profiles.py`,
  `tests/property/test_fill_engine_properties.py` and `tests/integration/test_paper_fill_engine.py` failed
  before the change (collection errors: `ModuleNotFoundError: No module named 'trade_graph.contracts.fills'`,
  `ImportError: cannot import name 'fills' from 'trade_graph.kernel'`) and pass now.
- Golden cases from T28's acceptance, each under all three profiles: a touch-only bar does not fill
  (`test_touch_only_bar_does_not_fill_a_resting_buy_limit`); a gap through a stop fills at the worse open
  (`test_gap_through_a_stop_fills_at_the_worse_open`: 90 exactly without slippage, 89.91 with 0.10%); a bar
  hitting both exits takes the stop (`test_bar_hitting_both_exits_takes_the_stop`).
- Changed existing test: `tests/integration/test_execution_acceptance.py::test_a13_...` asserted a gapped
  stop fill at exactly the gapped bid 90; it now asserts 89.9 (the bid less the D147 exit slippage on the
  0.1 tick). What it proves is unchanged and stricter: the stop fills from the gapped price, not its trigger.
- `uv run ruff check src tests`: All checks passed.
- Related tests: `uv run pytest -p no:cacheprovider tests/unit/test_fill_engine.py tests/unit/test_cost_profiles.py
  tests/property/test_fill_engine_properties.py tests/integration/test_paper_fill_engine.py`: all pass; every test
  file that uses `PaperBroker` or `DropAckBroker` (31 files, plus `test_public_market.py`, including
  `test_execution.py`, `test_execution_acceptance.py`, `test_protocols.py`, `test_pause_profiles.py`,
  `test_paper_service.py`): 568 passed, 0 failed.
- `python3 scripts/check_plan.py`, `python3 scripts/test_planning.py`,
  `python3 scripts/check_repository_hygiene.py`: plan valid (46 tasks, acyclic); 11 planning tests OK;
  hygiene passed.
- Full suite: `uv run pytest -p no:cacheprovider -rf` at `c5f784c` (same code as `f331ff0` plus this
  handoff) on Python 3.12.3 (`uv sync --frozen --group dev --python /usr/bin/python3.12`), 15 min: 6212
  passed, 67 failed, 23 skipped, 65 errors. The same files run on the untouched base `435db71` in a separate
  worktree with the same environment give exactly the same 67 failures and 65 errors, so this package adds
  no new failure. Of them:
  - 13 are on the known list: `e2e/test_dashboard_loop` (1), `test_paper_soak` (4), `test_progress_dashboard`
    (3), `test_progress_projection` (4), `test_subscription_smoke::test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers` (1).
  - 53 failures in `test_plugin_runtime_shadow` (20), `test_plugin_engineering` (19), `test_plugin_rollout`
    (14) and all 65 errors (setup of `test_application_preparation` 26, `test_application_projection` 26,
    `test_application_engineering` 13) are this cloud container, not code: the plugin runtime hashes the
    interpreter without following links (`adapters/engineering/plugin_runtime.py:72-89`), and this Ubuntu
    Python 3.12 ships `/usr/lib/python3.12/_sysconfigdata__linux_x86_64-linux-gnu.py` as a symlink
    (`OSError: [Errno 40] Too many levels of symbolic links`).
  - 1, `tests/security/test_subscription_egress_boundary.py::test_fixed_provider_proxy_is_the_only_child_egress_setting`,
    needs Linux bubblewrap, which this container lacks (`ValueError: Linux bubblewrap is unavailable;
    isolation fails closed`).
  An earlier run on the container's default Python 3.13.16 (at `f331ff0`, 13.5 min: 6209 passed, 70 failed,
  65 errors) failed the same tests plus 3 `test_offline_acceptance` cases that refuse anything but the
  repository's Python 3.12 environment; the 3.12 run above is the one that counts.
- Real or synthetic: every test uses synthetic candles and quotes. No recorded or live market data was needed.
  No paid call, exchange call, real order or deployment was made. The two fee-page checks were attempted
  (WebFetch and curl through the cloud proxy) and refused by the network policy (DNS failure / HTTP 403).

## Schema and dependencies
- State migration: none (reserved 0027 is unused). Candle database: none.
- Dependencies added: none. `pyproject.toml` package data gains `kernel/cost_profiles.json`; `uv.lock` is
  unchanged.

## Not done, open or for someone else
- Re-read the three official fee pages (Kraken fee schedule, OKX EEA fees) and, if a figure differs, publish a
  new revision with a new pin: local step L2. The owner's own Kraken tier (trade-volume endpoint, S12)
  outranks the public figure if a key is ever used.
- Suggested source-register entry for `docs/90-SOURCES.md` (not edited here to avoid number clashes with
  other wave 1 packages): "OKX fee schedule and the 2026-09-25 OKX Europe spot fee notice; figures recorded
  in D126; not reachable from the cloud on 2026-10-16". The notice's own URL was never recorded: L2.
- Paper broker take-profit and one-cancels-the-other submitted orders, and attaching exits on entry fills:
  T34 (W4-A). The engine functions are ready.
- Quote-path marketable limits keep the T05 behaviour (maker at the limit price). The paper broker does not
  store a placement price, so it cannot yet charge them as takers; day plans (T34) place resting entries
  below the price, where this does not arise. Worth revisiting in T34.
- Practice-range loop, reports and persistence of `cost_profile_ref`/`fill_policy_ref` with each report: T29
  (W2-A). See "For the practice range" in `docs/FILL-ENGINE.md`.
- For W1-E and the integrator: cloud sessions need `uv sync --frozen --group dev --python /usr/bin/python3.12`
  (plain `uv sync` picks the container's Python 3.13), and the plugin-runtime and bubblewrap failures above
  will appear in every cloud full-suite run until the plugin runtime accepts a distribution's symlinked
  `_sysconfigdata` file or the cloud image changes. Not fixed here (outside T28).
- Push and pull request: this session's GitHub access was refused (HTTP 403 on `git push` and on the GitHub
  connector, "Claude doesn't have GitHub access to krybaby7/trade-graph"). The owner reconnects GitHub at
  claude.ai/connect-github (and installs the Claude GitHub App on the repository if it is missing); then the
  branch is pushed and the pull request opened.
- Coin scanner round-trip cost: `CostProfile.round_trip_rate("maker"|"taker")` gives it (T31, W2-B).
