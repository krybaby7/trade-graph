# W1-A T44: Watch headlines with Jev in shadow mode and log would-be news alarms

For the owner: The system can now read five free news feeds (Kraken's status page and blog, CoinDesk,
Cointelegraph and Decrypt), save every new headline and, once you switch Jev on, ask Jev three fixed questions
about each one and log whom it would have woken, with a plain-English report of the headlines, Jev's answers,
the would-be alarms and Jev's cost per day; nobody is woken yet. It was built and tested only with invented
headlines and scripted Jev answers, because the cloud could not reach the news sites and there is no Jev key,
so the first real feed samples and any real Jev check still happen on your PC (step L2). Nothing needs a
decision now; if you want real Jev checks, get a key at console.typesafe.ai only, let the local session store
it, and switch Jev on in the news alarm settings file.

Branch: `claude/confident-curie-fkuvcj` (this session's own branch, reset onto the integration branch as the
plan allows; the plan's name for it is `claude/t44-jev-shadow`), head `fa882ef` for the code and docs (this handoff is the commit after it). Pull request: not opened yet.
GitHub refused this session's push with HTTP 403 ("Claude doesn't have GitHub access to krybaby7/trade-graph
for your organization"), and the GitHub connector was refused too (403 "Resource not accessible by
integration"). The commits are on the local branch; once access is fixed, push the branch and open the pull
request into `claude/day-trader-engine-20261012` titled `W1-A T44: Watch headlines with Jev in shadow mode and
log would-be news alarms`, with the owner summary above as its description.

## What was built

- `src/trade_graph/contracts/decisions.py`: provider-neutral decision contract. `DecisionRequest` (model,
  state, named `noul`/`choice`/`score` questions), `DecisionAnswer` (probabilities and optional confidence,
  as received), `DecisionUsage` (unreported is `None`, never 0), `DecisionResult` and `exact_json`, which
  stores numbers with their received digits.
- `src/trade_graph/adapters/decisions/jev.py`: the Jev adapter. Wire body for `POST /v1/systemone`, strict
  reply parsing, pinned `jev-1.13.0` (aliases refused before anything is sent), HTTP status mapping
  (400, 401, 413, 415, 422, 429, 529, other 5xx, timeouts), the dated price card
  `typesafe-jev-1.13.0-2026-10-15`, `PinnedJevHttp` (one official endpoint, bounded, no redirects),
  `ScriptedJevHttp` (always synthetic) and `load_jev_key` (private file only, never shown).
- `src/trade_graph/contracts/news.py`: feeds (`RECOMMENDED_FEEDS`), `Headline`, `WatchList`,
  `NewsAlarmRule`, `WouldWakeVerdict` and `NewsAlarmSettings` / `JevSettings` (Jev off by default).
- `src/trade_graph/adapters/news/feeds.py`: RSS/Atom reader (DTD and entities refused, 2 MB bound, canonical
  links for repeat detection), `HttpxFeedFetcher` (one keyless GET with ETag/Last-Modified, no redirects,
  public addresses only) and `FixtureFeedFetcher` (labelled fixture folders).
- `src/trade_graph/application/news_questions.py`: question set `news-alarm-questions-v1` and the
  would-wake rule `news-alarm-rule-v1`.
- `src/trade_graph/adapters/persistence/news_alarm_schema.py` and `migrations/versions/0026_news_alarm.py`:
  migration 0026, five append-only tables (`news_feed_polls`, `news_headlines`, `news_jev_attempts`,
  `news_jev_outcomes`, `news_alarm_verdicts`).
- `src/trade_graph/application/news_store.py`, `jev_checks.py`, `news_shadow.py`: storage, the receipted Jev
  check (reservation and attempt before the call; reply, receipt or unresolved hold and verdict after it),
  recovery of interrupted calls, and the poll pipeline. `wakes_sent` is always 0.
- `src/trade_graph/application/news_report.py`: read-only report (headlines, answers as stored, verdicts as
  stored, latency, daily spend real/synthetic/unresolved, USD5 alert) and its plain-English text form.
- `src/trade_graph/application/news_watchlist.py`: held coins and mandate coins from the paper account.
- `src/trade_graph/market_data_cli/news_alarm.py`: `trade-graph news-alarm-poll` and
  `trade-graph news-alarm-report`, discovered like the candle commands (no edit to `cli.py`).
- `config/news-alarm.example.json` (recommended defaults, Jev off), `docs/NEWS-ALARM.md` (how it works, the
  commands and how the owner switches Jev on), `tests/fixtures/news/` (labelled synthetic feeds, scripted
  Jev replies and the fixture settings).
- `planning/model-prices.json`: new dated entry `typesafe:jev-1.13.0` (no existing entry edited).
- `src/trade_graph/adapters/models/transport.py`: `ProviderHttpResponseError` now keeps `status_code`
  (one additive line; its `failure` mapping is unchanged).

## Decisions (reserved block D140–D144)

- **D140 (2026-10-16, implementation default for T44): Jev's own decision contract and wire reading.** Jev
  is not a model-provider adapter: it has its own contract (state and typed questions in, probabilities out)
  in `contracts/decisions.py`, with no SDK and no network import. The version is pinned to `jev-1.13.0`; any
  name that is not `jev-<major>.<minor>.<patch>` is refused before sending, so `jev-latest` and `jev-preview`
  can never be used. TypeSafe's documentation host did not resolve from the cloud on 2026-10-16, so the wire
  names beyond S40 (`question`, `options`, `levels`, `probabilities`, `confidence`, `id`) and the
  `Authorization: Bearer` header are this build's reading of the published API, kept in one adapter module;
  the first credentialed call (L2) confirms them. Replies are read strictly: the answers must name exactly the
  questions asked and exactly their options, every probability a number from 0 to 1, each answer's
  probabilities summing to between 0.99 and 1.01; `confidence` is optional and checked when present. Anything
  else is `malformed_answers`: kept as received, receipted, and given no verdict. The key is read only from
  `~/.local/share/trade-graph-owner/typesafe/typesafe.key` (folder private to its owner, file 0600, one
  link, no symlink, 16–4096 printable characters) and is sent only in the header.
- **D141 (2026-10-16, implementation default for T44): receipts.** Every call reserves through the existing
  `BudgetGateway` under role `news_alarm`, purpose `news_alarm:jev`, holding the largest request Jev accepts
  (64,000 input tokens, S40) plus 1,024 output tokens (free on the dated card), with the owner's FX rate and
  buffer (defaults 1 and 1.02, as `RuntimeModelConfig`). The reservation and the attempt row commit together
  before the call. A reply is receipted only when both token counts are reported and the answering model is
  the priced one; an HTTP error is receipted from the usage its body reports. Missing usage, a reply from
  another model (its price is unknown), a timeout, a refused reply or an interrupted call stays `UNCERTAIN`
  at the full hold. A retry is a new reservation (`attempt_kind` `retry`). A money limit with no room sends
  nothing and is logged as `budget_refused`, which is not an attempt. Scripted transports are always
  synthetic and the runner refuses a binding that labels them otherwise. A receipt is allocated once, 100%,
  to the latest paper portfolio. Real calls also refuse a price card verified more than 30 days ago, as the
  model router does. The USD5 testing alert is reported from real committed spending only and pauses
  nothing.
- **D142 (2026-10-16, implementation default for T44): feeds and polling.** The five feeds in
  `RECOMMENDED_FEEDS` (Kraken status page Atom, Kraken blog RSS, CoinDesk, Cointelegraph and Decrypt RSS),
  all free, keyless and https. One plain GET per feed per poll with the previous ETag and Last-Modified, no
  redirects, a 2 MB bound, a descriptive User-Agent and no cookie or credential; DTDs and entity
  declarations are refused outright instead of adding an XML dependency. Repeats are detected by canonical
  link (lower-case scheme and host, no fragment, no `utm_` parameters). Titles lose control and
  text-direction characters (so a printed headline reads as stored) and links holding them are refused; the
  item's hash is taken from the element as received. Recommended interval 5 minutes
  (`--polls N` repeats at that interval; scheduling it is an installation step). Only headlines published
  within the last hour (receipt time when the feed gives none or a future one) go to Jev; older ones are
  stored and reported as not checked. Retryable failures (rate limit, overloaded, server error, timeout,
  credentials) are asked again on later polls up to 3 attempts per headline; a credentials failure or a
  budget refusal ends that poll's Jev checks. The cloud network refused every feed host, so the fixtures are
  synthetic and labelled; one real recording per feed is step L2.
- **D143 (2026-10-16, implementation default for T44): question set and would-wake rule.** Three fixed
  questions: `concerns` (choice of watched coins, `kraken`, `none`), `event_type` (hack or exploit,
  delisting, listing, exchange outage or maintenance, regulatory action, other) and `holder_wants_to_know`
  (yes/no, within the hour). The headline is only in the `state`, as quoted data. Watched coins: held coins,
  the active mandate's coins, the owner's `extra_watch_coins` and focus coins once T37 exists, at most 253
  (a Jev choice holds 255 options). The rule: the subject is the most probable `concerns` option, ties all
  count and a coin tied with `none` counts; it would wake when the subject is not `none` and P(yes) is at
  least the alarm threshold (default 0.5, owner setting); it wakes the Leader, and the Trader too when a
  subject is a held coin or Kraken itself while anything is held. The event type is logged for T45's note and
  is not part of the rule. It is an alarm setting, not a confidence gate, and property tests show it can only
  add wake-ups.
- **D144 (2026-10-16, implementation default for T44): storage and commands.** Migration 0026 (W1-A's
  reserved version) adds five append-only tables to the state database; triggers refuse every update and
  delete, and money stays in the budget tables. The commands are discovered through `market_data_cli`, so
  `cli.py` is unchanged. `news-alarm-poll` needs an initialised paper database in a 0700 folder; with Jev
  switched on and no readable key it stops before reading anything; scripted replies and real calls cannot be
  mixed. `news-alarm-report` opens the database read-only, never creates or upgrades it, and never recomputes
  an answer or a verdict. News tables are not added to agent context, the financial checkpoint or the review
  export; how the Leader reads headlines is T38/T45.

## Evidence

- Tests written first: the first ten new test files failed before the code existed (collection stopped with
  `ModuleNotFoundError: No module named 'trade_graph.adapters.decisions'` and
  `... 'trade_graph.adapters.persistence.news_alarm_schema'`) and pass now. `test_news_alarm_settings.py`
  arrived with the example settings file it reads.
- `uv run ruff check src tests`: `All checks passed!` at `fa882ef`.
- New tests: 11 files, 155 tests (`tests/unit/test_jev_adapter.py`, `test_news_alarm_rule.py`,
  `test_news_feeds.py`, `test_news_alarm_settings.py`, `tests/property/test_news_alarm_rule_properties.py`,
  `tests/integration/test_jev_checks.py`, `test_news_alarm_shadow.py`, `test_news_alarm_report.py`,
  `test_news_alarm_cli.py`, `test_migration_0026.py`, `tests/security/test_news_alarm_boundary.py`):
  155 passed at `fa882ef`. The two follow-up tests (control characters in feed text, readable report times)
  failed with the fix stashed and pass with it.
- Related existing tests: `test_migration_0025.py`, `test_owner_checkins.py`, `test_import_boundary.py`,
  `test_market_data_cli_registry.py`, `test_provider_transport.py`, `test_owner_expense_upgrade.py`,
  `test_owner_expenses.py`, `test_scheduler_recovery.py`, `test_startup_migration.py`,
  `test_candle_collection_isolation.py`, `test_repository_hygiene.py`: all passed (278 tests together with the
  new ones at `b1dfccf`), and again inside both full-suite runs.
- `python3 scripts/check_plan.py`, `python3 scripts/test_planning.py`,
  `python3 scripts/check_repository_hygiene.py`: `Plan valid: 46 tasks; acyclic dependencies;
  references/configuration checked.`; `Ran 11 tests ... OK`; `Repository hygiene passed: no private runtime
  files or recognized credentials.` (also run with `--staged` before every commit). `scripts/cost_model.py`
  still runs with the new price record.
- Full suite: `uv run pytest -p no:cacheprovider -rf` at `fa882ef` on Python 3.12.3 (a separate locked
  environment from `uv sync --frozen --group dev --python 3.12`; this container's default Python is 3.13),
  15 min 21 s: **6,313 passed, 67 failed, 65 errors, 23 skipped**. No failure comes from this package:
  - Known before W1-E (13): `e2e/test_dashboard_loop` 1, `test_paper_soak` 4, `test_progress_dashboard` 3,
    `test_progress_projection` 4, `test_subscription_smoke::test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers`
    1. None of the load-only timing cases failed.
  - This container only, also failing on the untouched base `435db71` (run here from a separate worktree):
    `test_plugin_engineering` 19, `test_plugin_rollout` 14 and `test_plugin_runtime_shadow` 20 failures, and
    65 setup errors in `test_application_engineering` 13, `test_application_preparation` 26 and
    `test_application_projection` 26. All stop at `OSError: [Errno 40] Too many levels of symbolic links:
    '/usr/lib/python3.1x/_sysconfigdata__linux_x86_64-linux-gnu.py'`: the plugin sandbox correctly refuses the
    symlinked standard-library file of the system Python. CI's `setup-python` toolcache Python does not have
    that link. `tests/security/test_subscription_egress_boundary.py::test_fixed_provider_proxy_is_the_only_child_egress_setting`
    fails the same way on base: `Linux bubblewrap is unavailable; isolation fails closed`. W1-E may want
    these listed as environment-dependent.
  - An earlier run at `b1dfccf` on the container's Python 3.13.16 (14 min 14 s): 6,295 passed, 70 failed,
    65 errors, 23 skipped: the same set plus 3 `tests/unit/test_offline_acceptance.py` cases that require the
    repository Python 3.12 environment ("use the repository Python 3.12 environment after uv sync
    --frozen"); they pass on 3.12.
- CLI on fixtures: `tests/integration/test_news_alarm_cli.py` runs `init`, `news-alarm-poll` (fixture feeds,
  scripted Jev) and `news-alarm-report` (text and JSON) with the clock fixed at 2026-10-16 12:00 UTC: 8
  headlines stored, 7 checked, 2 would-be alarms (Leader only, as a fresh paper account holds nothing), 0 wakes,
  synthetic spending USD0.00012348. By hand in a scratch folder at 12:28 UTC: 8 stored, 6 checked (the Decrypt
  item was then older than the one-hour window), 2 would-be alarms, 0 wakes; at 12:56 UTC: 8 stored and none
  checked, because every fixture headline was older than the window. `docs/NEWS-ALARM.md` says so.
- Real or synthetic: every feed in the tests and the fixture run is a hand-written synthetic fixture,
  labelled `synthetic_fixture` in the files, the database and the report. Every Jev reply is scripted and
  receipted as synthetic. The two budget-path tests that mark a scripted transport non-synthetic (to exercise
  money limits and the real-spend columns) and the CLI wiring test that stands in for the HTTPS client say so
  in their docstrings; none is a Jev or paid-provider test. No network call to a feed, TypeSafe, an exchange
  or a paid provider was made, apart from the refused feed probes below.
- Network probes on 2026-10-16: `curl` to each feed address returned `CONNECT tunnel failed, response 403`
  from the cloud proxy; `docs.typesafe.ai` did not resolve.

## Schema and dependencies

- State migration: 0026 (reserved for W1-A), registered right after 0025, with Alembic revision `0026`
  (down revision `0025`, downgrade refused). Candle database: none.
- Dependencies added: none. XML is read with the standard library, with DTDs and entities refused.
- Tests changed outside the package: `tests/integration/test_migration_0025.py` and
  `tests/integration/test_owner_checkins.py` pinned 0025 as the newest migration; they now check that 0025 is
  registered once, right after 0024, and that Alembic's head is the newest registered version. Every other
  assertion is unchanged. W1-B, W1-C and W1-D add 0027–0029 and meet the same pins.

## Not done, open or for someone else

- Owner or integrator first: this session could not push (HTTP 403 for git and for the GitHub connector), so
  the branch is not on GitHub and there is no pull request yet. Reconnect GitHub at
  https://claude.ai/connect-github (and install the Claude GitHub App on the repository there if needed), then
  push `claude/confident-curie-fkuvcj` and open the pull request described at the top.
- Integrator: copy D140–D144 into `docs/DECISIONS.md`; add a sources entry for the five feed addresses
  (checked 2026-10-16, refused from the cloud) and note the S40 wire reading in `docs/90-SOURCES.md`; keep
  the `typesafe:jev-1.13.0` price record next to W1-C's Perplexity record; mark T44 `done` if the acceptance
  evidence above holds (the credentialed smoke is opt-in and separate by the task's own acceptance).
- Step L2 (owner's PC): record one small real sample of each feed into `tests/fixtures/news/` with
  `recorded_public_fixture` labels and check each address still serves a feed; if the owner has installed a
  key, one opt-in Jev smoke (`trade-graph news-alarm-poll --config <private settings with calls_enabled>`)
  to confirm the wire names, the header, `usage` and `id`, reported as real and separate from these tests.
- W2-C (T40 HQ spending): Jev spending is `budget_reservations.role = 'news_alarm'`,
  `purpose = 'news_alarm:jev'`, provider `typesafe`; show the USD5 testing alert (real committed spending,
  pauses nothing) and unresolved calls as unresolved.
- W2-B / W3-A / W6-A: the watch list holds at most 253 coins. When the paper coin list becomes the scanner's
  rule (D138 item 3, hundreds of pairs), pass held coins, focus coins and the shortlist rather than every
  eligible pair; T37 passes focus coins to `watch_list(..., focus=...)`.
- W6-D / L3 (installation): run the poll every 5 minutes (inside the service or as a scheduled task). Before
  real calls run under the protected deployment, bind Jev checks to the protected budget origins:
  `ProtectedBudgetGateway.reserve` requires a task row, and Jev checks have none. The runtime evidence
  collector (`runtime_evidence.py`) will flag real Jev receipts as `durable_provider_invocation_missing` and
  `provider_transport_attempt_missing`, because Jev keeps its own attempt log instead of `model_invocations`;
  decide there whether the collector reads `news_jev_attempts`.
- W7-A (T45): verdicts are in `news_alarm_verdicts` (`would_wake`, `wake_roles_json` ⊆ `["leader",
  "trader"]`, `subjects_json`, `held_subjects_json`, `event_type`, `holder_yes`, `rule_version`), joined to
  `news_headlines` (title, link, publisher, times, evidence) and `news_jev_outcomes` (answers as received).
  Only `status = 'answered'` outcomes have a verdict.
- Owner (optional): a Jev key from console.typesafe.ai only, stored in the private folder above; then
  `"calls_enabled": true` in a private copy of `config/news-alarm.example.json`.
