# W6-A T37: Research picks the day's focus coins on Perplexity

For the owner: Every morning, after the coin scanner has made its shortlist, Research can now look up the news on
those coins through Perplexity and name the day's focus coins for the Trader, each with a short reason and the
saved web pages or scanner numbers behind it; it picks only coins from the shortlist that are inside your coin
list, its notes now last the whole trading day instead of 47 minutes, it looks again when the Leader asks or at a
time it chooses, and when anything goes wrong the Trader is still woken and told plainly why, rather than being
told "nothing today". It is built and tested only with made-up web answers, and it ships switched off: no real
Perplexity call has been made and none will be until you switch it on. At step L3, after the new engine is
installed, you run one command, `trade-graph perplexity-departments --confirm` (run it without `--confirm` first
to see what it records), which moves both Research and the Leader to Perplexity with no money limit while testing;
nothing needs a decision from you now.

Branch: `claude/t37-focus-coins`, head: the commit that adds this handoff's pull request link (code at `c5d1871`,
docs at `5a65509`). Pull request: PR_LINK (not merged).

## What was built

- `src/trade_graph/application/focus_research.py`: `FocusResearch`, Research's handler for the event engine's
  wakes (a plain `task -> output` handler with `context` and `recover`). The day's `focus` wake is the focus step;
  every other Research wake (the Leader's `wake_request`, its own `check_in`, a news alarm) is a refresh. A turn
  records its round before anything is sent, reads the owner's switch, takes the day's live shortlist (never a
  replay) and keeps as candidates only the coins inside the owner's coin list, then runs one Perplexity round
  (W1-C's `PerplexityWebResearch`, software-run `web_search`, every result stored before the model reads it, a
  receipt for every call). Before anything is kept it checks every pick (a candidate), every reference (the day's
  scan id or a result stored in this round) and that no pick rests only on instruction-like results. It answers
  under `wake_outcome` with the focus (or none, or "keep"), Research's next check-in and a summary; or fails the
  turn with a plain reason. An empty shortlist or none inside the coin list is an empty answer with no paid call.
  `recover` returns a finished turn's saved answer, and a turn that stopped mid-round is recorded `interrupted`
  and never re-run (nothing paid twice). `FocusResearchBounds` is the reservation envelope (10 searches for the
  focus step, 6 for a refresh, 60,000 input and 4,000 output tokens per step).
- `src/trade_graph/contracts/focus_research.py`: `FocusResearchReply` (extends T36's `WebResearchReply`):
  `summary`, `findings` (`FocusFinding`: one stored `source_ref`, the coins it concerns, no lifetime),
  `keep_current_focus`, `focus` (`FocusPick`: symbol, reason, 1–8 evidence refs) and `next_check_in`
  (`CheckInRequest`). No confidence, approval, plan, order or wake field. Passes the OpenAI and Anthropic strict
  wire schemas.
- `src/trade_graph/application/focus_scope.py`: `focus_scope_refusals(authority, portfolio_id, symbols)`, the
  owner's coin list for focus coins (a listed mandate's coins; spot pairs in the quote currency under the coin rule;
  riba products never).
- **The protected owner switch, for both departments:** `src/trade_graph/contracts/perplexity_switch.py`
  (`PerplexityDepartments`, `PERPLEXITY_DEPARTMENTS`), `src/trade_graph/application/perplexity_switch.py`
  (**`perplexity_enabled_for(database, department) -> bool`**, the typed read W6-B calls with `"leader"`;
  `perplexity_setting(database)` for the revision and its `spend_policy`; `propose_perplexity_departments`,
  `record_perplexity_departments`), and the owner command **`trade-graph perplexity-departments`**
  (`src/trade_graph/market_data_cli/perplexity_departments.py`, discovered; `cli.py` unchanged): preview by default,
  `--confirm` records, refused while the paper service runs, `--expect-revision`, `--research on|off`,
  `--leader on|off` (both default on), `--monthly-limit-usd` (default: the D125 testing mode). Shipped off.
- `src/trade_graph/adapters/persistence/focus_research_schema.py`, `migrations/versions/0038_focus_research.py`,
  one import and one line in `adapters/persistence/migrate.py`: state migration 0038 (three append-only tables:
  `perplexity_department_settings`, `focus_research_rounds`, `focus_research_outcomes`).
- `src/trade_graph/application/events/engine.py` (W5-A's engine): `_focus` now also refuses a focus coin outside
  the owner's coin list and any coin on a day without a shortlist; `_open_wake` hands Trader and Learning wakes (and
  reviews) `focus_step`. `src/trade_graph/contracts/wakes.py`: `FocusStepState` and `WakeContext.focus_step`
  (optional, default none, so stored wakes still read).
- `src/trade_graph/application/web_research.py` (W1-C's round): `WebResearchAsk.findings_expire_at` (software sets
  every accepted finding's lifetime) and `run(..., check=...)` (a check of the valid reply and the round's stored
  sources before anything is kept; a `ValidationFailure` rejects the whole reply). Without them nothing changes.
- `src/trade_graph/application/paper_service.py`: `_wake_task` gives a department switched to Perplexity the
  owner's paid root allocation even when the service runs on a subscription (Perplexity is billed per call).
- `src/trade_graph/kernel/authority.py`: the new modules plus `contracts/web_research.py` and
  `application/web_research.py` (the spend rule and receipt collection of research rounds) are protected paths;
  the Leader is forbidden `switch_perplexity_departments`.
- `docs/FOCUS-RESEARCH.md` (new): how a turn works, the outcomes, the switch and its command, the records, and what
  W6-B, W6-C, W6-D and step L3 need. `docs/EVENT-ENGINE.md` and `docs/PERPLEXITY-RESEARCH.md`: short updates.
- Tests: `tests/unit/test_focus_research_contracts.py`, `tests/unit/test_focus_scope.py`,
  `tests/integration/test_focus_research.py`, `test_focus_research_service.py`, `test_perplexity_switch.py`,
  `test_migration_0038.py`; helpers in `tests/focus_research_support.py`; the synthetic injection fixture
  `tests/fixtures/perplexity/search_injection.json` (listed in that folder's README). One W5-A test changed:
  `tests/integration/test_wake_cycle.py::test_a_failed_scan_is_recorded_and_the_day_goes_on_without_a_shortlist`
  now expects the stricter rule (D206) and also checks that the Trader is still woken and sees the failed step.

## Contracts for the next packages

- **W6-B (T38, the Leader).** `from trade_graph.application.perplexity_switch import perplexity_enabled_for,
  perplexity_setting`; `perplexity_enabled_for(database, "leader")` is False until the owner's `--confirm` and fails
  closed; `perplexity_setting(database).spend_policy` is the `PerplexitySpendPolicy` to pass to
  `PerplexityWebResearch(..., policy=...)`. The Leader's wake tasks already get the paid allocation when it is on.
  To ask Research for a deep dive, use `EventEngine.request_wake(portfolio, "research", note, requested_by="leader")`
  or a `wake_requests` answer: Research's refresh round reads the note as data (`context.wake.notes`).
- **W6-C (T39, HQ wake log).** Read the focus step from `day_cycle_steps` or a Trader wake's `focus_step`; Research's
  rounds from `focus_research_rounds` joined to `focus_research_outcomes` (status, `answered`, reason, coins,
  billing, Perplexity `web_run_id`); the switch from `perplexity_setting(connection)` (works on a read-only
  connection). Reading starts nothing.
- **W6-D or a follow-up before L3 (installing).** Wire `FocusResearch(execution, gateway=ModelGateway(...,
  paid_calls_enabled=True, transport=<pinned, both Perplexity endpoints>, api_keys={"perplexity": <read_private_key>}),
  route=PerplexityRoute(deployment_id=...))` as the `research` handler when the installation runs with
  `event_engine`, with the price cards seeded and the deployment's EUR allowance sized; see "Not done".

## Decisions (reserved block D205–D209)

- **D205 (2026-10-17, implementation default for T37 and T38): the owner's Perplexity switch.** Moving Research or
  the Leader to Perplexity is one protected owner setting for both departments, shipped off. It is stored as
  append-only revisions in the state database (migration 0038, `set_by` = owner in SQL, each naming the revision it
  replaced, hash-checked on read) and recorded only by `trade-graph perplexity-departments`, in the style of
  `mandate-cleanup` and `money-limits`: preview by default, `--confirm` records, refused while the paper service
  runs, `--expect-revision`. Its defaults are the owner's decisions so step L3 is one `--confirm`: both departments
  on, with the D125 testing spend rule (no money limit; every call reserved and receipted; HQ's USD20 alert, D131);
  `--monthly-limit-usd` records D138 item 4's later monthly limit, which pauses only Perplexity-backed rounds. The
  typed read `perplexity_enabled_for(database, department)` fails closed (no record, an older database, an edited
  record). The Leader is forbidden `switch_perplexity_departments`; the Trader cannot be switched (D124). A
  department switched on is billed per call, so its wake tasks get the owner's paid root allocation even under a
  subscription service. Reason: the plan's "protected setting, shipped off, with an owner command at L3", D124,
  D125, D138 item 4, AGENTS.md (protected owner policy; paid work has reservations and receipts).
- **D206 (2026-10-17, implementation default for T37): focus coins come only from the shortlist inside the owner's
  coin list, enforced twice.** The shortlist is the one the day start recorded: the newest live scan in
  `CoinScanStore` or the day start's own live scan, never a replay (a replay, a failed scan or none means no
  shortlist). Candidates are the shortlisted coins the owner's coin list covers (`focus_scope`: a listed mandate's
  coins inside the owner policy's list, or under the owner's coin rule any spot pair in its quote currency; riba
  products never, D127). Research's workflow offers only candidates, with the scanner's numbers, and rejects a reply
  naming anything else. The engine (`EventEngine._focus`) now also refuses a coin outside the owner's coin list and
  any coin on a day without a shortlist; this tightens D192, which accepted coins on a day with no scan, and W5-A's
  test was updated to the stricter rule. Refreshes use the same day's shortlist, the one the engine checks against.
  Reason: T37's acceptance "focus coins come only from the shortlist and the owner symbol scope", D120, D138 item 3.
- **D207 (2026-10-17, implementation default for T37): the focus step, refreshes and how long research lasts.**
  Research's `focus` wake names the day's focus coins; any other Research wake is a refresh that either keeps
  today's focus (no new revision, no Trader wake) or names a new selection (a new revision, which wakes the Trader,
  D192). Every turn names Research's next check-in (none means the next day start, D123); nothing limits how often
  it refreshes (D116). Findings of these rounds last until the trading day ends, the day plan's lifetime, set by
  software (`WebResearchAsk.findings_expire_at`); the reply has no lifetime field, so the first run's 47-minute
  expiry cannot recur. A pick's evidence is its cited references plus the ids of the findings saved for that coin.
  The round's size bounds (10 searches at the focus step, 6 at a refresh, 60,000 input and 4,000 output tokens) are
  a reservation envelope for one call, not a limit on activity. The old subscription `ResearchHandler` keeps its
  model-chosen lifetimes; it is not the Perplexity Research. Reason: the T37 deliverables, D121, D123, D116.
- **D208 (2026-10-17, implementation default for T37): empty is an answer, failure is visible, nothing is paid
  twice.** An empty selection is a valid answer, recorded as a focus with Research's summary: chosen by Research, or
  without a paid call when the shortlist is empty (`no_candidates`) or none of it is inside the coin list
  (`outside_owner_scope`). Every other outcome fails the turn with a plain reason (`no_shortlist`, `switched_off`,
  `disabled`, `paused_at_limit`, `rejected`, `failed`, `uncertain`, `interrupted`, `refused`): the engine records the
  focus step as failed with it and still wakes the Trader, whose wake carries `focus_step` (status and reason). No
  status exists for a hold, so an error can never be saved as "nothing today". A round is recorded before anything
  is sent and a task runs at most one round: a restarted turn returns its saved answer, or `interrupted` (its calls
  stay unresolved and held) instead of a second paid round. Reason: T37's acceptance "empty or failed research is
  visible and the Trader can still decide; errors are not disguised as holds", AGENTS.md (persist before effects;
  unknown usage is unresolved, not free).
- **D209 (2026-10-17, implementation default for T37): web content is data, never authority.** The reply contract
  forbids any field but the summary, findings, keep, focus and next check-in, so it cannot carry wake requests,
  plans, orders, approvals or confidence. Software checks every pick against the candidates, every reference
  against the day's scan id and the round's stored results (else D150's "outside the persisted snapshot"), and
  rejects the whole reply when a pick rests only on results flagged as instruction-like (T36's markers); such
  results stay stored and labelled, and a finding citing one is kept as `untrusted-page`, never offered as fresh
  research. Wake notes, including the Leader's, reach the model as data. Nothing in a reply can change the switch,
  a setting or an order. Reason: T37's acceptance "web content is data, never authority; injection fixtures are
  handled", D124, D150, AGENTS.md.

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/claude/t37-focus-coins`. No network, key, paid, model or exchange call in any test; every Perplexity
answer is synthetic and played by a scripted transport, with a made-up key.

- Tests written first: all six new test modules failed at collection before the code existed
  (`ModuleNotFoundError: trade_graph.contracts.focus_research`, `trade_graph.application.focus_scope`,
  `trade_graph.adapters.persistence.focus_research_schema`, `trade_graph.application.perplexity_switch`,
  `trade_graph.application.focus_research` twice), and the updated W5-A test failed against the old engine (it
  accepted a focus without a shortlist). On their first runs against the new code: the protected-path test failed
  until `kernel/authority.py` listed the modules; the paid-allocation test failed until `_wake_task` changed; one
  test assumption was wrong (recording an "off" switch over the default changes nothing, so the tamper test now
  edits an "on" revision). Mutation checks, each restored afterwards: turning off the flagged-only rule, the
  owner-scope filter, the visible failure (answering a failed round as an empty selection), the day-end lifetime
  (47 minutes instead) or the no-repeat rule on restart each fails at least one test; turning off the engine's two
  new refusals fails `test_the_engine_itself_refuses_...` and the updated W5-A test.
- `uv run ruff check --no-cache src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`:
  all checks passed (also before each commit).
- New tests: `uv run pytest -p no:cacheprovider -q -rfs tests/unit/test_focus_research_contracts.py
  tests/unit/test_focus_scope.py tests/integration/test_migration_0038.py tests/integration/test_perplexity_switch.py
  tests/integration/test_focus_research.py tests/integration/test_focus_research_service.py
  tests/integration/test_wake_cycle.py`: **54 passed, 0 failed, 0 skipped** (23 s).
- Related tests: every test module that imports or exercises changed code, 113 modules: those that mention
  `PaperService`/`paper_service`/the subscription operation, path protection or forbidden actions, the migration
  list (`apply_migrations`, Alembic heads, `sqlite_master`, `schema_migrations`), `model_json_schema`,
  `export_schemas` or `_wire_schema`, the wake contracts and engine, Perplexity research, CLI commands (`main([`,
  `market_data_cli`), `AuthorityRecord`, the day-plan and wake test helpers (found with `grep -lE` over `tests`),
  plus the Engineer path-protection and artifact tests, in three parallel shards of whole modules
  (`uv run pytest -p no:cacheprovider -q -rfs <modules>`): **1,970 tests: 1,955 passed, 0 failed, 15 skipped** (14 in
  `tests/security/test_subscription_process_boundary.py`: bubblewrap is not installed in the cloud container, as on
  GitHub; 1 archive-permission case: this user can read any file), every shard exit code 0. The container was
  shared with the other wave-6 sessions' test runs (load average about 7 on 4 cores); no timing case failed. The
  doubled `-q` (pyproject's `addopts` already has `-q`) hides pytest's summary line, so the counts are the
  progress marks, which equal `--collect-only` per shard (605, 557, 808). Also
  `uv run pytest -p no:cacheprovider tests/integration/test_provider_wire_schemas.py tests/unit/test_wake_contracts.py
  tests/unit/test_contracts.py tests/unit/test_import_boundary.py`: **85 passed**.
- `uv run pytest -p no:cacheprovider -q tests/unit` (and again without the extra `-q` for the summary):
  **2,189 passed, 0 failed, 7 skipped** (57 s; the indicator cross-checks the second library cannot make and the
  archive-permission cases).
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic and labelled: the USD10,000 day-plan account with synthetic money
  limits, a live coin scan written straight into migration 0031 with made-up inputs, hand-written Agent API and
  Search API answers in the documented shapes (T36's fixtures plus the new synthetic injection fixture), a made-up
  key and scripted Trader, Leader and Learning departments. No real Perplexity, model, exchange or paid call and no
  real order; a scripted transport is never a paid-provider test.

## Schema and dependencies

- State migration: **0038** (W6-A's reservation), registered in `migrate.py` after 0035; the Alembic revision
  `0038` has `down_revision = "0035"` and is the single head on this branch; `test_migration_0038.py` checks both
  against the registered list, so the integrator's re-linking (0038 after W6-D's 0041 or in number order) only needs
  the `down_revision` changed. Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Installing it (W6-D or a small follow-up before L3; then step L3).** The installed subscription service
  (`run_subscription_operation`) does not run the event engine yet and refuses separately billed API configuration,
  so switching Research on changes nothing in the running system until it is wired: the engine switched on; a
  `FocusResearch` with a `ModelGateway` that has `paid_calls_enabled`, a pinned transport with both Perplexity
  endpoints (the subscription network profile must allow them), the key from the owner's private file
  (`read_private_key`), the dated price cards seeded and a `PerplexityRoute` for the deployment; the deployment's
  EUR allowance (BudgetGateway) sized so D125's "no money limit" is not blocked by the EUR allowance; and the
  Perplexity price cards re-verified within 30 days (the 2026-10-12 agent card goes stale on 2026-11-11). Then the
  owner runs `trade-graph perplexity-departments --confirm` (step L3).
- **Research tasks made outside the engine** (Secretary routing): `FocusResearch` answers wakes; pass the old
  handler as `fallback` to keep answering tasks without a wake. Not wired here.
- **Refreshes widen the shortlist?** Refreshes use the day start's shortlist, which the engine checks against
  (D192, D206). If W6-D's hourly scans should let Research add a newly shortlisted coin during the day, the engine's
  `_focus` check and this workflow change together (one place each).
- **Integrator:** copy D205–D209 into `docs/DECISIONS.md`; mark T37 in `planning/progress.json` with this evidence.
  Shared lines touched: `application/events/engine.py` (`_focus`, `_open_wake`, a new `_focus_step`, one import),
  `contracts/wakes.py` (`FocusStepState`, `WakeContext.focus_step`, `__all__`), `application/paper_service.py`
  (`_wake_task`, two imports), `application/web_research.py` (`WebResearchAsk.findings_expire_at`, `run(check=)`,
  `_accept`), `kernel/authority.py` (`LEADER_FORBIDDEN`, `PROTECTED_PATH_PREFIXES`), `adapters/persistence/
  migrate.py` (one import, one line), `docs/EVENT-ENGINE.md`, `docs/PERPLEXITY-RESEARCH.md`,
  `tests/fixtures/perplexity/README.md`, and one W5-A test (`test_wake_cycle.py`). W6-B is likely to touch
  `web_research.py`, `kernel/authority.py` and the Perplexity docs as well; keep both sides.
