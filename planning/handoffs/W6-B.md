# W6-B T38: Leader news rounds, Research deep-dive requests and Trader wake notes

For the owner: When the Leader is woken it can now skim crypto news and news about AI trading agents on Perplexity,
with every search result saved before the AI reads it and a receipt for every call, and from that it can ask Research
to dig deeper, leave the Trader a short note and pass strategy ideas on to be tested on the practice range first,
always saying when it wants to look again. The Leader still cannot buy, sell, approve or block anything: a note to the
Trader is only information, and this was tested with a note that said "block every SOL entry" while the Trader's own
plan was installed anyway. It is built and tested with made-up news and a pretend Perplexity only, and it stays
switched off until you turn on the Perplexity setting at step L3, so there is nothing for you to decide or do now.

Branch: `claude/t38-leader-news`, head: the commit that adds this line; code at `02fd19d`. Pull request: https://github.com/krybaby7/trade-graph/pull/18.

## What was built

- `src/trade_graph/contracts/leader_news.py`: `LeaderNewsReply` (a `WebResearchReply`: `summary`, `headlines`,
  `deep_dives`, one optional `trader_note`, `strategy_ideas` for `research` or `optimisation`, and a required
  `next_check_in`; `findings` must stay empty; extra fields refused), `compose_note` (a wake note that always fits the
  wake contract's 500 characters and keeps its request id and sources), the brief (`LeaderBrief` and its parts) and
  the records (`RoutedRequest`, `LeaderRound`). No adapter, SDK or model import.
- `src/trade_graph/application/leader_news/`: `rounds.py` (`LeaderNewsRounds`, the Leader's wake handler with the
  worker's `recover`/`context`/call protocol; `LeaderRoundSettings`; `vet_reply`; `_LeaderResearch`, which runs W1-C's
  `PerplexityWebResearch` round with the Leader's guidance after the protected instructions and vets the reply before
  anything from it is kept), `brief.py` (`build_brief` from saved records through the engine's public reads,
  `EngineReads` protocol), `store.py` (`LeaderNewsStore`: start, finish once with the routed requests, reads with
  billing, receipt ids, unresolved reservations and stored sources live from T36's records).
- `src/trade_graph/adapters/persistence/leader_news_schema.py`, `migrations/versions/0039_leader_news_rounds.py`, one
  import and one line in `adapters/persistence/migrate.py`: state migration 0039 (`leader_news_rounds`,
  `leader_round_requests`; SQL triggers keep every record, freeze a round's identity and brief, let a round finish
  once, write requests only with their open round, and route each kind only where it may go).
- `src/trade_graph/kernel/authority.py`: the contract and `application/leader_news/` are protected paths.
- `docs/LEADER-NEWS-ROUNDS.md` (new): the round, the reply, how requests travel, the records, switch, money,
  service wiring and restart.
- Tests: `tests/unit/test_leader_news_contracts.py`, `tests/integration/test_migration_0039.py`,
  `tests/integration/test_leader_news_rounds.py`, `tests/integration/test_leader_news_service.py`; helpers in
  `tests/leader_news_support.py` (synthetic headlines; the T36 Perplexity fixtures; a scripted transport).

## The contract for the next packages

- **Wiring the handler.** `handlers["leader"] = LeaderNewsRounds(PerplexityWebResearch(gateway, route=PerplexityRoute(
  deployment_id=...), policy=None), enabled=lambda: perplexity_enabled_for(database, "leader"),
  spend_policy=lambda: perplexity_setting(database).spend_policy, fallback=<the existing Leader handler>)`, then
  `rounds.engine = service.event_engine` once the `PaperService` exists (it builds its own engine). The two reads are
  W6-A's (`trade_graph.application.perplexity_switch`); both are read fresh for every task and fail closed here.
  `fallback` keeps Leader tasks the engine did not make (Secretary routing, owner requests) and the Leader's wakes
  while the switch is off.
- **What the engine receives.** A completed round answers `{"leader_round": {...}, "wake_outcome": WakeOutcome}` with
  the Leader's `check_in` and one `wake_request` per routed request, in order: Trader note, deep dives, strategy
  ideas. Their engine triggers are `wake-request:<wake_id>:<position>`, stored as `trigger_source_ref` in
  `leader_round_requests`, so HQ (W6-C) can join a wake request to its round. Notes start `Leader note <id>`,
  `Leader deep dive <id>` or `Leader strategy idea <id>`.
- **Reads for HQ (W6-C).** `LeaderNewsStore(database, clock).rounds(portfolio_id, since=)` and `round(round_id)`:
  status, reason, brief, summary, check-in, billing, receipt ids, unresolved reservations, stored source refs and
  routed requests. Reading starts nothing.
- **W7-A (T45).** A news alarm for the Leader (`EventEngine.notify(..., "leader", kind="news_alarm", ...)`) wakes it
  for a round; the alarm's note is in the brief's `asked` list as data.

## Decisions (reserved block D210–D214)

- **D210 (2026-10-17, implementation default for T38): one wake, one round.** The Leader's news round is its
  event-engine wake handler: each engine wake of the Leader (the day start unless it named a time, its own check-ins,
  wake requests, news alarms) runs one Perplexity round through W1-C's `PerplexityWebResearch` with role `leader`. The
  question is fixed by software: what is new in crypto market news and news about AI trading agents since the last
  round; the Leader's guidance follows the protected T36 instructions. The brief is built from saved records only
  (the wake and its notes, the day's focus coins and plan coins, each department's next check-in, the latest 12 of
  today's wakes, which departments software wakes, the previous round) and persisted with the task's snapshot and the
  round. One round's envelope is 6 searches, 32,000 input and 3,000 output tokens and 120 seconds; it sizes the
  reservations of one round and never limits how often rounds run (D116: the Leader may name a check-in a minute
  away, tested). Leader tasks the engine did not make go to the configured Leader handler. Reason: T38's
  deliverables, D121, D124, D123, W5-A's wake contract.
- **D211 (2026-10-17, implementation default for T38): what the Leader may return, and where it goes.** A
  `LeaderNewsReply`: a summary, up to 12 headlines, up to 8 deep dives for Research, at most one Trader note, up to 4
  strategy ideas for Research or Optimisation, and a required next check-in (a time, or none for the next day start).
  `findings` stays empty: findings with counterevidence are Research's, the Leader asks for a deep dive, and the
  Leader's skim does not enter the findings the Trader reads. There is no field for orders, approvals, vetoes, plans,
  pauses, mandates, settings, prompts or code, and extra fields are refused, so a reply that tries is rejected whole.
  Every request becomes a wake request through the engine (applied after the task is saved), with a note of at most
  500 characters that starts with its request id and sources; the full request is stored. The Trader reads its note
  as data and decides; day plans come only from the Trader's answer and `DayPlans.install` reads no Leader record. A
  strategy idea goes to Optimisation when software wakes Optimisation, otherwise to Research (both recorded), marked
  for the practice range before any use; it changes no strategy, artifact, mandate or setting. Reason: T38's
  acceptance ("cannot place, approve or block individual trades"), D121, D192.
- **D212 (2026-10-17, implementation default for T38): every round has receipts and stored sources.** Every agent
  step and search reserves and receipts through T36 (an unknown bill stays unresolved, never free). A reply is
  accepted only if every source it cites (headlines, deep dives, the Trader note, ideas) was stored for this round
  and the round stored at least one; otherwise the round is `rejected` whole before anything is kept or routed, its
  receipts and stored results stay, and the wake fails as one turn: no system pause, the day step is recorded as
  failed, and event triggers (a news alarm, a wake request) are carried to the Leader's next wake. A completed round
  always carries the Leader's named next check-in. The round's billing, receipts and sources are read live from T36's
  records, so a later reconciliation shows. Reason: T38's acceptance, D124, D150, D152.
- **D213 (2026-10-17, implementation default for T38): the switch and money.** The protected switch that moves the
  Leader to Perplexity is W6-A's (one owner setting for both departments, off until step L3); here it is an injected
  function `enabled`, with the owner's spend rule that travels with it as `spend_policy`, both read fresh for every
  task; a switch that cannot be read counts as off and a spend rule that cannot be read as none. Switched off, the
  configured Leader handler keeps the Leader's wakes; without one the wake completes with a `switched_off` round and
  no call. Without an owner spending policy or with paid calls off the round is `disabled` (nothing reserved or
  sent). The owner's money limit, the deployment allowance or the wake task's allocation gives `paused_at_limit`:
  only news rounds pause; the wake completes and the Leader looks again at the next day start (D123); the Trader,
  protection and records continue (tested). Reason: D125, D138 item 4, D153, CLOUD-BUILD-PLAN W6-B.
- **D214 (2026-10-17, implementation default for T38): the records and a restart.** State migration 0039 holds
  `leader_news_rounds` (one per wake task, written `started` before any paid call, finished once, brief immutable)
  and `leader_round_requests` (written with their open round, append-only); they are orchestration records with no
  trade decision in them. After a restart in the middle of a round, the next claim of its task records the round
  `interrupted` and fails the turn: its calls keep their receipts, an unknown bill stays held at its maximum, and no
  second paid round runs for that wake; a finished round's saved answer is returned before any snapshot or paid call.
  The contract and the round's code are protected paths. Reason: AGENTS.md (persist before effects, unknown usage is
  unresolved, durable results recover before another paid call; protected acceptance gates), D193.

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/claude/t38-leader-news`. No network, key, paid, model or exchange call in any test.

- Tests written first: `tests/unit/test_leader_news_contracts.py`, `tests/integration/test_migration_0039.py` and
  `tests/integration/test_leader_news_rounds.py` failed at collection (`ModuleNotFoundError:
  trade_graph.adapters.persistence.leader_news_schema` and `trade_graph.application.leader_news`) and pass now. Their
  first runs found one test assumption (the deep-dive test needed the engine run to completion: Research was still
  in its focus wake when the Leader's requests arrived, so they waited for its next wake, as designed) and one test
  that checked broker orders before the management step sent the resting entry. Mutation checks: removing the reply
  vetting fails the outside-citation and read-nothing tests; not recording an interrupted round fails the restart
  test (`UNIQUE constraint failed: leader_news_rounds.task_id`); the unreadable-switch test failed with the original
  `OSError` before the fail-closed read, and the spend-rule test failed (the first round was `disabled`) before the
  injected `spend_policy` read.
- New tests: `uv run pytest -p no:cacheprovider -q tests/unit/test_leader_news_contracts.py
  tests/integration/test_migration_0039.py tests/integration/test_leader_news_rounds.py
  tests/integration/test_leader_news_service.py` at `02fd19d`: **65 passed, 0 failed, 0 skipped** (26 s: 33 contract,
  6 migration, 24 round and 2 service tests).
- `uv run ruff check --no-cache src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`:
  all checks passed.
- Related tests: every test module outside `tests/unit` that imports or exercises changed code (115
  modules: those that mention the migration list (`migrate`, `apply_migrations`, Alembic heads, `sqlite_master`,
  `schema_migrations`), path protection or `kernel.authority`, the Engineer, `web_research` or `perplexity`, the
  event engine (`wake_support`, `EventEngine`, `application.events`), `model_json_schema`, `export_schemas` or
  `_wire_schema`, the Leader (`leadership`, `LeaderHandler`), found with `grep -lE` over `tests`), including
  `tests/integration/test_provider_wire_schemas.py`, in three parallel shards of whole modules at `191e593`
  (`uv run pytest -p no:cacheprovider -q -rfs <modules>`): **2,069 passed, 1 failed, 15 skipped**. The one failure is
  `tests/integration/test_plugin_staging.py::test_parent_work_quota_retains_unrun_attempts_instead_of_starting_another_child`
  (`assert Decimal('1.596182196') < Decimal('1.5')`, a wall-clock bound on a sandboxed child process); it does not
  touch this package's code and passed 3 of 3 runs alone. Other wave 6 sessions were running their tests in the same
  container at the time (load average about 4 on 4 CPUs), so it is load-sensitive like the old timing cases. Skips:
  14 in `tests/security/test_subscription_process_boundary.py` (bubblewrap is not installed in the cloud container,
  as on GitHub) and 1 archive-permission case (this user can read any file). The later commits (`f4dcf39` to
  `02fd19d`) change only `application/leader_news/rounds.py`, the Leader tests and their helper, and the docs; at
  `02fd19d` these were re-run: the new tests above, and `tests/integration/test_provider_wire_schemas.py
  test_perplexity_research.py test_migration_0035.py test_migration_0034.py test_migration_0028.py test_wake_engine.py
  test_wake_cycle.py test_wake_service.py test_scripted_day.py tests/property/test_wake_properties.py
  tests/integration/test_leadership_workflow.py test_leader_execution_scope.py test_engineer.py`: **176 passed, 0
  failed, 0 skipped**.
- Trial merge with W6-A (read only): a scratch clone outside the repository, `02fd19d` merged with
  `origin/claude/t37-focus-coins` at `c3d0d1f`, conflicts in `migrate.py` and `kernel/authority.py` resolved by keeping
  both sides (0038 before 0039) and 0039 re-linked to `down_revision = "0038"`. There, this package's tests, W6-A's
  (`test_focus_research.py`, `test_focus_research_service.py`, `test_perplexity_switch.py`, `test_migration_0038.py`,
  `test_focus_research_contracts.py`) and the wake, migration 0035, Perplexity and wire-schema tests: **236 passed,
  0 failed**. Nothing from it is committed here.
- `uv run pytest -p no:cacheprovider -rfs tests/unit` at `02fd19d`: **2,213 passed, 0 failed, 7 skipped** (37 s; the
  indicator cross-checks the second library cannot make and the archive-permission cases).
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: everything is synthetic and labelled: the USD10,000 paper account with synthetic money limits,
  hand-written Perplexity Agent and Search API answers in the documented shapes (the T36 fixtures plus synthetic
  headlines in `tests/leader_news_support.py`) played by a scripted transport with a made-up key, and scripted
  Research and Trader departments. No Perplexity, model, exchange or other paid call was made; a scripted Perplexity
  is not a paid-provider test.

## Schema and dependencies

- State migration: **0039** (W6-B's reservation), registered in `migrate.py` after 0035; the Alembic revision `0039`
  has `down_revision = "0035"`, the head on the integration branch at `e1008e5`. **The integrator re-links it after
  W6-A's 0038** (its test checks `down_revision` against the previous registered version, not a fixed number).
  Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **The protected switch (W6-A, integrator).** W6-A's branch (`claude/t37-focus-coins`, read only at `c3d0d1f`) has
  the switch: `perplexity_enabled_for(source, "leader")` and `perplexity_setting(source).spend_policy` in
  `trade_graph.application.perplexity_switch`, set by the owner command `trade-graph perplexity-departments
  --confirm`. Nothing here imports it (it is not merged). The handler takes both as injected functions
  (`enabled`, `spend_policy`), so the wiring is the two lambdas above; there is no installation wiring point in this
  package to change (the installed service does not build a Leader news handler yet, see the next item). W6-A also
  fixes the EUR0 wake allocation in `PaperService._wake_task` for departments switched to Perplexity, which this
  package found too: without it a Leader round in a subscription installation stops at `paused_at_limit` ("room in
  shared task/root allocation exhausted") before any request (tested here as
  `test_a_wake_task_without_money_allocated_stops_at_the_allocation_never_spends`, which stays true for a task with
  EUR0). W6-A adds a `check` hook to `PerplexityWebResearch.run`; this package does not use it and does not change
  `web_research.py`: `_LeaderResearch` overrides `_request` and `_accept`, whose signatures W6-A keeps. After both
  merge, `vet_reply` could be passed as `check=` instead of the `_accept` override (optional, same effect).
  W6-A's new optional `WakeContext.focus_step` and its stricter focus check (no focus coins without a shortlist)
  need nothing here; the Leader tests give their day start a synthetic shortlist so they hold on both sides (the
  trial merge above).
- **The installation (W6-D or a follow-up before L3, step L3).** The installed subscription service builds its
  departments inside the protected process boundary (`subscription_runtime.py`) and does not run the event engine
  yet (W5-A). Running the Leader on Perplexity there needs: the handler wired as above with a pinned transport that
  has both Perplexity endpoints, the key from the owner's private file (D154), the owner's `PerplexitySpendPolicy`,
  a price card verified within 30 days (the 2026-10-12 Agent card goes stale on 2026-11-11), and a protected route
  for the registered `web_search` tool (W1-C: the kernel department gateway refuses `tools` in a department's
  context). The real opt-in Perplexity smoke stays step L2.
- **The older Leader actions (owner or a later package).** The existing Leader handler, kept as `fallback` for
  non-wake Leader tasks and switched-off wakes, still has its R1 portfolio actions: `pause` (for example
  NO_NEW_EXPOSURE) and `mandate` (a narrower mandate within the owner's envelope). Neither approves an individual
  trade, but a mandate without a coin stops new entries in it. T38's news rounds have neither action. Whether those
  stay is a question for the owner; nothing was changed.
- **HQ (W6-C).** Showing rounds, their spend and their requests in HQ uses the reads above; not built here.
- **Shared lines touched (integrator).** `adapters/persistence/migrate.py` (one import, one line after 0035; W6-A
  adds 0038's at the same place: keep both, 0038 before 0039) and `kernel/authority.py` (two protected paths appended
  after T42's line; W6-A appends eight there: keep all). Re-link `migrations/versions/0039_leader_news_rounds.py` to
  `down_revision = "0038"` after W6-A. No other shared file is changed. Copy D210–D214 into `docs/DECISIONS.md` and
  mark T38 with this evidence.
- **AI allowance floor.** In the paper service `_run_role` holds every department under the subscription allowance
  floor, so Perplexity Leader rounds wait under it too, although Perplexity spends no Codex allowance. Conservative
  and unchanged; the owner may want Perplexity departments outside the floor later.
