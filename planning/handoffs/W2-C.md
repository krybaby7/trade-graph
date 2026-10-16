# W2-C T40: HQ shows AI spending per department, with the USD20 testing alert

For the owner: The Money tab in HQ now has an "AI spending" panel that shows, in dollars and from the system's own
receipts, what Perplexity research and the Jev news alarm have cost per department, with costs not known yet shown
as "up to" an amount and never as zero, plus how each department uses your ChatGPT allowance. Once Perplexity
testing passes USD20 (or Jev passes USD5), "Needs you" shows a "Keep an eye on" card, and nothing is ever paused
by it; practice money never appears in this panel and never pays for AI. Nothing real has been spent or shown yet
(no paid call has run), and when you top up Perplexity you can ask the local session to record the balance with
`trade-graph ai-spending-record` so HQ shows the testing total next to it.

Branch: `claude/t40-hq-spending`, based on `origin/claude/day-trader-engine-20261012` at `fc0a4d9`; head: the
commit that adds this handoff's pull request link (code head `4623b32`; the handoff itself was first committed in `968c099`). Pull request:
https://github.com/krybaby7/trade-graph/pull/7 (not merged).

## What was built

- `src/trade_graph/application/ai_spending.py`: the read model. One pass over `budget_reservations` joined to
  `usage_receipts` counts every Perplexity call (price card provider `perplexity`) and Jev check (role
  `news_alarm`, purpose `news_alarm:jev`) once, as actual, estimated or unresolved (D175), in USD, per department,
  with test (synthetic) calls apart. It derives the Perplexity testing period and mode from the owner policy each
  round recorded (`web_research_runs.policy_json`), the testing alert state (D176), the prepaid credit and the
  spending since it (D177), this UTC month against a monthly limit (the same sum as `PerplexitySpendGuard`), and
  Codex turns and shares per department over 7 days (D178). It reads no practice-money table. Also
  `record_owner_amount` (append one owner record) and `format_report_text`.
- `src/trade_graph/adapters/persistence/ai_spending_schema.py`, `migrations/versions/0032_ai_spending_owner_records.py`
  and the registration in `migrate.py`: state migration 0032, one append-only table `ai_spending_owner_records`
  (service `perplexity`/`jev`, kind `prepaid_credit`/`testing_alert`, plain non-negative USD decimal, a positive
  alert, `set_by = 'owner'`).
- `src/trade_graph/api/hq_spending.py`: a 60-second cache of the report behind a fingerprint of the spending
  tables, the `ai_spending` block (copy from `hq_copy.SPENDING`) and the `spending_alert` cards.
- `src/trade_graph/api/hq_contract.py`: additive, omittable `HQSnapshot.ai_spending` (never null) with
  `AiSpending`, `ServiceSpending`, `SpendingLine`, `SpendAmounts`, `SpendingAlert`, `PrepaidCredit`,
  `MonthlyLimit`, `CodexUse`, `CodexLine`; attention code `spending_alert` (severity `watch`); glossary keys
  `estimated_cost`, `unresolved_cost`, `testing_alert`, `prepaid_credit`. Validators enforce USD, lines adding up
  to the total, "a part with no call is null", "no open call, nothing held" and "unknown hold, unknown total".
- `src/trade_graph/api/hq.py` (wires the block and the cards), `hq_state.attention(p, checkins, extra)` (extra
  cards), `hq_copy.py` (`SPENDING`, section help `spending`).
- Page: `web/templates/hq.html` (an "AI spending" panel in Money with a first paint of each headline),
  `web/static/hq-render.js` (`HQ.view("spending")`: wrapping lists, no tables, "under $0.01" for sub-cent costs,
  static notes composed from the figures), `hq-glossary.js` (four terms, section help in both twins), `hq.css`
  (theme tokens only, light and dark through the existing tokens).
- `src/trade_graph/market_data_cli/ai_spending.py` (discovered; `cli.py` unchanged): `trade-graph
  ai-spending-report [--format text]` opens the database read-only; `trade-graph ai-spending-record --service
  perplexity|jev --prepaid-credit USD [--checked-at UTC] | --testing-alert USD [--confirm]` previews by default and
  appends one row only with `--confirm` (private 0700 database folder required, like `mandate-cleanup`).
- `docs/HQ-DASHBOARD.md`: an "AI spending" section for the owner and a data rule for reviewers.
- Tests: `tests/integration/test_ai_spending.py`, `tests/property/test_ai_spending_properties.py`,
  `tests/integration/test_hq_spending.py`, `tests/unit/test_hq_spending_contract.py`,
  `tests/integration/test_ai_spending_cli.py`, `tests/integration/test_migration_0032.py`,
  `tests/js/hq_spending.test.mjs`, helper `tests/spending_support.py` (synthetic records through the real
  `BudgetGateway`). Changed: the nine `tests/fixtures/hq/snapshot_*.json` carry an `ai_spending` block made by the
  real projection; `tests/js/hq_harness.mjs` has the `spending` view; `test_hq_projection.snap` resets the
  spending cache like the money cache.

## Decisions (reserved block D175–D179)

- **D175 (2026-10-16, implementation default for T40): three kinds of AI cost, from receipts.** Every paid call is
  one budget reservation, counted once: *actual* is a `committed` or `reconciled` receipt (priced from the usage the
  provider reported); *estimated* is a `conservative_charge` (usage never reported, charged at the reserved
  maximum); *unresolved* is a call with no receipt yet (still open or usage unknown), counted at its held maximum
  and shown as "up to", never as zero, or as "can't be worked out yet" when even the hold can't be valued. Amounts
  are USD, the services' own currency: receipts at their USD price, EUR holds and conservative charges divided by
  the FX rate they were reserved with, so the month total equals what the owner's Perplexity limit counts (D152,
  D153). Synthetic calls are counted apart and never added. Perplexity calls are those priced on a Perplexity card;
  Jev checks are role `news_alarm`, purpose `news_alarm:jev` (W1-A). HQ sums every reservation in the state
  database, which belongs to one installation. In the HQ contract a part with no call of that kind is `null`, never
  `0`, so "nothing yet" can't read as a zero cost; a cost under half a cent reads "under $0.01". Reason: the
  acceptance ("totals reconcile to receipts; unknown usage is unresolved, never zero") with one rule shared by
  totals, lines, credit and month.
- **D176 (2026-10-16, implementation default for T40): the testing period and the alerts.** Perplexity testing runs
  from the first round recorded under the owner's `unlimited_testing` policy (D125) until the first round under a
  `monthly_limit` policy; the testing total and per-department lines cover that period; afterwards HQ shows this
  UTC month against the limit instead of the alert (reaching it pauses Research and Leader news rounds only, D138
  item 4). Jev testing is every real Jev check. The alert is at USD20 for Perplexity (D131) and USD5 for Jev (D138
  item 10) unless the owner recorded another amount. It has *passed* when known spending (actual plus estimated)
  reaches the amount, and *may have passed* when unresolved calls at their maximum could take it there; either
  adds one "Keep an eye on" (`watch`) card per service to Needs you, linked to Costs and included in the next review
  prompt. It stays while testing lasts, offers no button and pauses nothing (D116, D131). Deviation from W1-A's CLI
  report (D141), which counts committed spending only: HQ adds the "may have passed" state so unresolved calls are
  never treated as free; the CLI report is unchanged.
- **D177 (2026-10-16, implementation default for T40): the prepaid credit and the alert amounts are owner records.**
  Migration 0032 adds one append-only table. `trade-graph ai-spending-record` records the balance the owner read
  on the provider's own account page (with the time it was read) or another testing alert amount; it previews by
  default and writes only with `--confirm`; the newest record of each kind counts, older ones stay. HQ shows the
  spending since the reading and "about $X left" (unresolved calls at their maximum), as information only: nothing
  reads these rows to admit, refuse or pause a call, and a credit reading is not a bill (real costs stay in receipts
  and the owner's expense records). The Jev alert amount HQ uses is this record (default USD5), separate from
  `NewsAlarmSettings.testing_alert_usd` in the owner's private news alarm settings, which W1-A's CLI report reads
  (also USD5 by default); the owner changes both if they want another amount.
- **D178 (2026-10-16, implementation default for T40): Codex allowance use per department.** For the last 7 days,
  each department's non-synthetic ChatGPT subscription turns that were sent (not `BLOCKED`, not `not_incurred`) and
  its share of the team's reported use (input, cache and output counts of `subscription_invocations.usage_json`). A
  turn whose use wasn't reported counts as a turn with unknown use and is left out of the shares, which HQ says.
  The allowance gauge itself is never split by department, because the owner's own ChatGPT use shares it.
- **D179 (2026-10-16, implementation default for T40): HQ contract and reads.** `ai_spending` is an additive key of
  `trade-graph.hq.v1` that may be omitted but is never null; HQ leaves it out, rather than inventing figures, when
  the records can't be read. New attention code `spending_alert` (watch) and four glossary keys; no new route and no
  new authority. The report is cached for 60 seconds behind a fingerprint of reservations (count of rows and newest
  `updated_at`), receipts, Codex turns, Perplexity rounds and owner records, so a new receipt or state change shows
  on the next poll. Explanations that follow from the figures (open calls, estimates, test calls, the shared
  allowance) are static page copy; every figure and data-dependent sentence comes from the server.

## Evidence

- Tests written first, checked against the untouched base `fc0a4d9` (the new test files copied into a detached
  worktree of the base): `test_migration_0032.py` fails at collection (`ModuleNotFoundError:
  trade_graph.adapters.persistence.ai_spending_schema`), `test_ai_spending.py` and
  `test_ai_spending_properties.py` (`no trade_graph.application.ai_spending`), `test_hq_spending.py` (`cannot
  import name 'hq_spending'`), `test_ai_spending_cli.py` (`cannot import name 'ai_spending'` from
  `market_data_cli`), `test_hq_spending_contract.py` (`KeyError: 'ai_spending'` / `'spending_alert'`); with
  `HQ_STATIC_DIR` pointing at the base scripts, `tests/js/hq_spending.test.mjs` has 9 of 10 failing (the Needs you
  card test passes on the base too: the existing renderer already shows any card). All pass on this branch. The
  existing `test_unknown_money_is_null` (no zero money anywhere in an unknown snapshot) failed against this
  package's first version, which sent `$0.00` parts; that led to D175's "a part with no call is null", and it passes
  unchanged.
- New tests: `uv run pytest -p no:cacheprovider -o addopts="--import-mode=importlib" -q
  tests/integration/test_migration_0032.py tests/integration/test_ai_spending.py
  tests/property/test_ai_spending_properties.py tests/integration/test_hq_spending.py
  tests/unit/test_hq_spending_contract.py tests/integration/test_ai_spending_cli.py`: 71 passed, 0 failed,
  0 skipped. `node --test tests/js/hq_spending.test.mjs`: 10 passed, 0 failed.
- `uv run ruff check src tests`: All checks passed.
- Related tests (every test module that imports or exercises HQ, the CLI, the dashboard app or the migrations, plus
  the Perplexity, Jev, budget, cost, owner expense and migration modules; 89 files including the new ones and
  `tests/test_hq_js_behaviour.py`, which runs every `tests/js/hq_*.test.mjs`), one `uv run pytest -p no:cacheprovider
  -o addopts="--import-mode=importlib" -q -rf <files>` run in 202 s: 1519 passed, 0 failed, 1 skipped (the existing
  `tests/archive_support.py:104` skip: the container runs as root, which can read a file whatever its mode).
- `python3 scripts/check_plan.py`: "Plan valid: 46 tasks". `python3 scripts/test_planning.py`: 11 tests OK.
  `python3 scripts/check_repository_hygiene.py`: passed.
- Full suite: not run (night shift: the wave integration runs it).
- Size and speed: the HQ busy-week payload with an empty spending block is 65.9 KB compact (was 63.6 KB); with a
  busy block (600 synthetic calls, a credit and Codex turns) 69.8 KB compact and 76.1 KB readable, under the 80 KB
  budget; the busy fixture's block is 4.9 KB. One report over three months of synthetic records (27,000 Jev checks,
  3,000 Perplexity calls, 2,000 Codex turns) takes about 0.23–0.27 s in this container, at most once per minute or
  per new record (cached).
- Real or synthetic: everything is synthetic (records written through the real `BudgetGateway` with made-up
  usage; one end-to-end test runs W1-C's scripted Perplexity round). No paid, provider or exchange call was made;
  no network was used.

## Schema and dependencies

- State migration: `0032` (reserved), table `ai_spending_owner_records`. Its Alembic revision has
  `down_revision = "0029"` on this branch; the integrator re-links it after 0030/0031 if W2-A or W2-B add one (the
  SQL runner applies versions in list order and needs no change). Candle database: none.
- Dependencies added: none.

## Not done, open or for someone else

- **Owner / step L2–L3:** no real receipt exists yet, so HQ shows "No Perplexity research has run yet" and "No Jev
  checks yet" until real calls run. After topping up Perplexity (auto top-up off), record the balance:
  `trade-graph ai-spending-record --service perplexity --prepaid-credit <USD> --checked-at <UTC> --confirm`.
- **Owner / step L2:** look at the Money tab on a phone and an iPad, light and dark. The cloud has no browser; the
  layout uses wrapping lists and the existing theme tokens, and the JS tests drive the real script, but no one has
  looked at it on a screen.
- **Integrator:** copy D175–D179 into `docs/DECISIONS.md`; re-link Alembic 0032 if needed; mark T40 `done` if the
  evidence above holds. Shared files other packages also touch: `api/hq_contract.py` (W5-B T41 and W6-C T39 add
  their own keys next to `ai_spending`), the nine `tests/fixtures/hq/snapshot_*.json` (they now carry
  `ai_spending`; a fixture regenerated elsewhere must keep it, or `test_snapshot_validates_against_contract` fails),
  `tests/js/hq_harness.mjs` (`VIEWS` has `spending`), `web/templates/hq.html`, `hq-render.js`, `hq.css`,
  `hq-glossary.js` (GLOSSARY keys must stay in the contract's order), `hq_copy.py` and `hq_state.attention`.
- **W5-C (T42 scorecard) / T43:** "AI spending is shown next to it" can reuse
  `application.ai_spending.spending_report(...)` and its `document()` (the CLI's JSON).
- **W6-A / W6-B (T37, T38):** Perplexity rounds must keep recording their owner policy in `web_research_runs`
  (they do through `PerplexityWebResearch`); HQ derives the testing period from it.
- **Found outside the package (not changed):** the existing "Pays for itself?" box on the Money tab (T15) answers
  whether practice results cover the real costs you entered. The new AI spending panel never offsets AI costs
  against practice gains, but that older box is a comparison of the two; whether its wording should change is an
  owner or reviewer question, not part of T40.
