# W9-A C6: The news check runs next to the installed service

For the owner: Once you add the news door to your network settings at L3, the installed service reads the five news
feeds every five minutes by itself and keeps the shadow news log filling, which is the real evidence step 17 needs
before you decide on news wake-ups. It was tested only with invented headlines and a pretend Jev with a made-up key, so
no real feed has been read and no real Jev call made; Jev stays off until you switch it on in your news alarm settings
and put your Jev key in your owner folder, and each Jev check then has a receipt and stays within your existing
paid-call limits. Nothing needs your decision now: at L2 the Jev price must be re-checked (today's price card is too
old for real calls from 15 November 2026), and at L3 step 3 of the checklist lists the lines to add.

Branch: `claude/c6-news-poll-in-service`, head: the commit that adds this handoff's pull request link (code, tests and
docs at `6d3ab17`). Pull request: https://github.com/krybaby7/trade-graph/pull/23 (not merged).

## What was built

- `src/trade_graph/kernel/subscription_proxy.py`: the second reviewed route of W8-A's catalogue,
  `NEWS_FEEDS = "news_feeds"`: `status.kraken.com`, `blog.kraken.com`, `www.coindesk.com`, `cointelegraph.com`,
  `decrypt.co` (T44's five feeds) and `api.typesafe.ai` (Jev's one endpoint), each host's source named in the comment.
  Nothing else in the proxy or the network profile changed (both read the catalogue); `MARKET_HOSTS` is unchanged.
- `src/trade_graph/application/news_installation.py` (new, protected path): the news check in the installed service.
  - `installed_news_poll(runtime, protected_owner)`: built once at start in `run_subscription_operation`'s `prepare`,
    in the protected parent process. It reads the owner's `subscription-network-profile.json`; without the reviewed
    `news_feeds` route it builds nothing and records a `news_route` activity `not_ready` with the reason. Otherwise it
    reads the optional `<owner>/news-alarm.json` (`NewsAlarmSettings`; absent means the recommended defaults, Jev off;
    a refused file is reported by field names only), builds `pinned_feed_fetcher(profile)` and, only when
    `jev.calls_enabled` is set and every prerequisite holds, the Jev route; it records `news_route` `ready` with the
    feeds, whether Jev is on and why not. An unexpected error leaves the news check off with its type recorded and never
    stops the service's start.
  - `pinned_feed_fetcher(profile)` / `pinned_jev_transport(profile)`: the feed fetcher and `PinnedJevHttp`, both only
    through `profile.route_url("news_feeds")`, no environment proxy; feeds refused unless `profile.route_admits` holds;
    Jev built only when the route admits `https://api.typesafe.ai/v1/systemone`.
  - `read_owner_jev_key`: `<owner>/typesafe.key` through the protected owner-file reader (one name, 16+ visible ASCII
    characters, held as `JevKey`, sent only as the `authorization` header). `card_problem`: a dated Jev card that names
    TypeSafe's endpoint and its source, effective, and verified at most 30 days ago (as the command and the router).
  - `NewsRouteBudget(BudgetGateway)`: Jev reservations on the route's own deployment `<deployment>:news_alarm`, role
    `news_alarm`, purpose `news_alarm:jev`, the route's card, no task, while the protected controller runs; the money
    rule is the owner policy's `daily_paid_limit` per UTC day and `monthly_operating` per UTC month over the route's
    real reservations (refusals are `NewsRouteRefused`, a `BudgetExhausted`, so T44 logs `budget_refused` and sends
    nothing). Each reservation gets an authenticated protected budget origin in the same transaction
    (`ProtectedBudgetOrigins` scoped to the route); `protect_dispatch`/`protect_settlement` validate it; `commit` seals a
    receipt commitment; `allocate` validates both sides. `audit`: two indexed completeness queries (every route
    reservation has its origin, every origin its reservation), the open and today's reservations, and a rolling sweep of
    500 of this month's per call, bounded at 5 s like the witness.
  - `ServiceNewsPoll`: `step()` polls when due (300 s after the previous poll's start, by the injected clock and the
    stored polls), recovers interrupted attempts (unresolved, never free), re-checks the card, the controller and the
    audit before passing Jev checks to T44's `NewsAlarmShadow`, and reports: a `news_poll` activity at the first poll,
    on every change (a feed lost or back, Jev on or off), with every would-be wake-up, failure or recovery. Lost feeds
    are named with the reason and when each was last read; never "no news". `request_stop` ends a poll between feeds
    and headlines.
- `src/trade_graph/application/paper_service.py`: an optional `news_poll` beside `price_collector`: `_poll_news` keeps
  one step running in its own thread (a tick never waits; a failed step is `news_poll:<error>`); stop and shutdown wait
  for a step in flight like the collector.
- `src/trade_graph/application/subscription_operation.py`: `prepare` sets `service.news_poll` (one import, one line);
  the result gains `news_polls` (polls made, or `None` when the news check is off).
- `src/trade_graph/kernel/protected_budget.py`: `ProtectedBudgetOrigins(financial, deployment_id=...)` (default: the
  manifest's deployment, unchanged behaviour) so a reviewed route binds its own reservations.
- `src/trade_graph/adapters/news/feeds.py`: `HttpxFeedFetcher(proxy=..., admits=...)`, the routed mode (no local DNS
  lookup, `trust_env` off, refused before any request when the route does not admit the address).
- `src/trade_graph/application/news_shadow.py`: `NewsAlarmShadow(should_stop=...)` (stop between feeds and headlines).
- `src/trade_graph/application/news_watchlist.py`: `service_watch_list`: held, today's focus coins (T37's
  `focus_selections`), the owner's extra and the mandate's coins, bounded at 253 with held, focus and extra first.
- `src/trade_graph/application/news_report.py`: a `feeds` section (polls, read, lost, whether the last poll was lost,
  since when, last read) and `summary.feeds_lost_now`; the text report lists each feed and the "not no news" note.
- `src/trade_graph/market_data_cli/news_alarm.py`: `news-alarm-poll` refuses Jev checks (scripted or real) on a
  database with a protected runtime instance (feeds-only polls still run): its reservations would carry the installed
  deployment's id without a protected origin, which the installation's financial witness refuses.
- `src/trade_graph/kernel/authority.py`: `application/news_installation.py`, `application/jev_checks.py` and
  `adapters/decisions/` appended to `PROTECTED_PATH_PREFIXES`.
- HQ: `api/hq_contract.py`, `api/hq_copy.py`, `api/hq_state.py`: the problem prefix `news_poll:` ("the news check's
  last step failed"; trading, protection and records are not affected).
- Docs: `docs/NEWS-ALARM.md` (new section "In the installed service (W9-A)"; the command's refusal; the Jev card's
  stale date), `docs/REVIEWED-NETWORK-ROUTES.md` (the second route and the owner's lines),
  `docs/L3-INSTALL-CHECKLIST.md` (step 3's news route and owner files, step 13's `news_route`/`news_poll`, step 17 and
  "News wake-ups: what step 17 needs" rewritten as done or owner actions), `docs/EVENT-ENGINE.md` (one bullet).
- Tests (new): `tests/security/test_news_feeds_route.py`, `tests/unit/test_news_feed_route_fetcher.py`,
  `tests/unit/test_news_installation_paths.py`, `tests/unit/test_hq_news_poll_problem.py`,
  `tests/integration/test_news_installation.py`, `test_news_poll_service.py`, `test_news_poll_in_service.py` (the
  acceptance day), `test_news_alarm_cli_installation.py`, `test_news_feed_health_report.py`,
  `test_news_service_watch_list.py`; helper `tests/news_service_support.py`. Changed:
  `tests/integration/test_perplexity_in_service.py` (one assertion, see "Shared files").

## Decisions (reserved block D255–D259)

- **D255 (2026-10-18, implementation default for C6): the news check is a thread of the installed service.** T44's
  poll runs inside the installed subscription service, in the protected parent process, as its own thread beside the
  price collector (D222), not as a companion process: one process owns the database lease and the launcher stays as it
  is, and the protected budget origins of Jev checks (D258) need the protected financial service of that process. The
  paper service keeps one news step running at a time; a tick never waits for it; a failed step is reported as
  `news_poll:<error>` (HQ: "the news check's last step failed"); a stop ends a poll between feeds and headlines and the
  service waits for a request in flight. A poll is due `poll_interval_seconds` (300) after the previous poll started,
  by the service's clock and the stored polls, so a restart never polls twice within five minutes (a clock that
  stepped back polls once). It writes T44's tables in the installation's state database, so the event engine's news
  router (T45) routes its verdicts. It runs under every pause (it places no order and wakes nobody itself) and with the
  event engine off (verdicts are then stored and routed once the engine runs). It is built only when the owner's
  network profile lists the reviewed `news_feeds` route; otherwise a `news_route` activity says why and nothing is
  fetched. Reason: the package's first bullet, D137 (a software check every 5 minutes), D222, the wave 8 lesson
  (tests drive it by an injected clock and explicit ticks).
- **D256 (2026-10-18, implementation default for C6): the second reviewed route, `news_feeds`.** One catalogue entry
  in `REVIEWED_ROUTE_HOSTS`: the five feed hosts of `RECOMMENDED_FEEDS` and `api.typesafe.ai`, exactly; the owner's
  profile lists its port (8083 suggested) and hosts (a subset) and the proxy gets
  `--route news_feeds=8083:api.typesafe.ai,blog.kraken.com,cointelegraph.com,decrypt.co,status.kraken.com,www.coindesk.com`.
  Feeds are fetched only through that port, only at addresses the profile admits, with no environment proxy, no
  redirects and no local DNS lookup (the reviewed proxy resolves, admits only public addresses and checks the TLS
  name); a feed whose host is not listed is refused before anything is sent and reported as lost
  (`route_not_listed`). Jev goes only to its one official endpoint through the same port. One route for feeds and Jev,
  as the package says ("a second reviewed host list"). A feed that moves to a host outside the catalogue needs a
  reviewed code change before the route admits it. Reason: the package's second bullet, D241, D142, D140.
- **D257 (2026-10-18, implementation default for C6): owner files and the Jev switch inside the image.** The owner's
  news alarm settings are `<owner>/news-alarm.json` (optional, a copy of `config/news-alarm.example.json`, `root:10001`
  mode `0440`; absent means the recommended defaults with Jev off); the Jev switch is its `jev.calls_enabled`. The key
  is `<owner>/typesafe.key` (`root:10001`, mode `0440`, like `perplexity.key`), read only when Jev is switched on,
  through the protected owner-file reader (D140's "owned by the reading user" rule cannot hold for a root-owned
  read-only mount; the protected reader's root-owned, not-other-readable, no-link rule replaces it, as D242 did), held
  as `JevKey`, sent only in the request header. Inside the image `jev.key_directory` and `jev.deployment_id` are not
  used. Jev checks run only when the switch is on, the key reads, the dated card is fresh (at most 30 days since it was
  verified; the 2026-10-15 card refuses real calls from 2026-11-15), the route admits the endpoint and the protected
  controller runs; otherwise headlines are collected, nothing is sent to TypeSafe and the reason is in the activity
  log. The watch list adds today's focus coins (T37) to held, mandate and extra coins; beyond the 253 a Jev question can
  name, held, focus and extra coins come first and the count left out is reported. Reason: the package's third bullet,
  D137 ("the owner installs the key through the private-file route"), D140, D143, D154, D242.
- **D258 (2026-10-18, implementation default for C6): Jev checks bound to protected budget origins; money.** Each Jev
  reservation of the in-service check carries the route's own deployment id, `<deployment>:news_alarm`, and in the same
  transaction as the reservation and the attempt row gets an authenticated protected budget origin
  (`protected_financial_budget_origins`, the protected financial service's MAC, the admitted manifest and controller
  instance, the paper portfolio, the exact reservation), before anything is sent; dispatch and settlement validate it,
  a receipt seals a commitment with its 100% allocation, and a changed or missing record stops Jev checks with the
  reason (headlines continue). Verification is per reservation (at reserve, dispatch and settlement) plus a bounded
  route audit each poll (completeness of all, the open and today's, and a rolling sweep of this month's), so it never
  re-verifies the whole history on each call: this answers W8-A's open note that binding a route to protected origins
  "would need a verifier that does not re-scan every reservation on each call" (D243). The route's reservations stay
  outside the subscription deployment's financial witness, so trading and protection never wait for, or fail on, Jev's
  records; the same scoped origins could bind W8-A's Perplexity route later (not changed here). The money rule is the
  owner policy's paid-call limits over the route's real reservations: `daily_paid_limit` per UTC day and
  `monthly_operating` per UTC month, held amounts counting until they settle, synthetic ones excluded; a check with no
  room is `budget_refused` and nothing is sent; the USD5 testing alert stays an alert (D138 item 10); the deployment's
  EUR allowance, which a subscription installation does not use, is not consulted (as D243). Unknown usage stays
  unresolved at the full hold; interrupted attempts are recovered as unresolved at each poll. `news-alarm-poll` refuses
  Jev checks on a protected installation's database (its reservations would carry the installed deployment's id
  without an origin, and the witness re-verifies every reservation of its deployment). Reason: the package's third
  bullet, W1-A's open item ("`ProtectedBudgetGateway.reserve` requires a task row, and Jev checks have none"),
  AGENTS.md (every paid call reserved and receipted; unknown usage unresolved; validate amounts deterministically).
- **D259 (2026-10-18, implementation default for C6): the runtime evidence collector, lost feeds and protected
  paths.** The runtime evidence collector does not read `news_jev_attempts`: it authenticates model-provider runs of the
  deployment it is bound to (OpenAI and Anthropic invocations and transport attempts with their request hashes); Jev is
  not a model provider (D140) and its in-service reservations carry the route's deployment id, so they are outside the
  collector's scope, as W8-A's Perplexity reservations are; Jev's evidence is its own append-only log
  (`news_jev_attempts`, `news_jev_outcomes`), the receipts and now protected origins; and a new table in the collector's
  inventory would invalidate every retained collection binding (the binding pins the exact table set). HQ's AI spending
  and the scorecard still count every Jev call (by role and purpose; any card). Lost feeds: every request's outcome is
  stored; the `news_poll` activity names lost feeds with the reason and when each was last read, at the start and end
  of an outage; `news-alarm-report` lists each feed's polls; a lost feed is never "no news". Protected paths:
  `application/news_installation.py` (key, route, money rule, origins), `application/jev_checks.py` (receipt
  collection) and `adapters/decisions/` (the key reader and the pinned transport). Reason: the package's third and
  fourth bullets, the wave 1 integration's open question, AGENTS.md (receipt collection is not agent-editable).

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/c6-news-poll-in-service` (4 CPUs, shared). No network, key, paid, model, feed, TypeSafe, Perplexity or
exchange call in any test; every feed is scripted and labelled `synthetic_fixture`, every Jev answer scripted with a
made-up key.

- Tests written first, or checked against the code without the change:
  - `tests/security/test_news_feeds_route.py`: failed at collection (`ImportError: cannot import name 'NEWS_FEEDS'`);
    52 passed after.
  - `tests/unit/test_news_feed_route_fetcher.py`: failed with the fetcher at base (no `proxy`/`admits`); 5 passed after.
  - `tests/integration/test_news_poll_service.py`: **4 failed** with `paper_service.py` at base; 4 passed after, three
    runs in a row.
  - `tests/integration/test_news_poll_in_service.py`: **4 failed** without the one-line wiring in
    `run_subscription_operation`'s `prepare`; 4 passed after, three runs in a row.
  - `tests/integration/test_news_alarm_cli_installation.py`: the refusal test failed before the guard (the two
    baseline tests passed); 3 passed after.
  - `tests/integration/test_news_feed_health_report.py`: 2 failed before (`KeyError: 'feeds'`); 2 passed after.
  - `tests/unit/test_news_installation_paths.py`: 3 failed before (the new paths not protected); 6 passed after.
  - `tests/unit/test_hq_news_poll_problem.py`: failed before; passes after.
  - `tests/integration/test_news_installation.py` (20 tests) and `test_news_service_watch_list.py` (2) were written
    alongside `news_installation.py` and `service_watch_list`; mutation checks, each restored: not recording the
    protected origin fails 5 tests, dropping the rolling sweep 1, skipping the money rule 2, skipping admission 5, not
    validating before dispatch 1, leaving out today's focus coins 2.
- `uv run ruff check src tests`: all checks passed (also before each commit).
- New tests together: `uv run pytest -p no:cacheprovider -q -rfs --junitxml=... tests/security/test_news_feeds_route.py
  tests/unit/test_news_feed_route_fetcher.py tests/unit/test_news_installation_paths.py
  tests/unit/test_hq_news_poll_problem.py tests/integration/test_news_installation.py
  tests/integration/test_news_poll_service.py tests/integration/test_news_poll_in_service.py
  tests/integration/test_news_alarm_cli_installation.py tests/integration/test_news_feed_health_report.py
  tests/integration/test_news_service_watch_list.py` at `6d3ab17`: **99 passed, 0 failed, 0 skipped** (88 s; 52 + 5 + 6
  + 1 + 20 + 4 + 4 + 3 + 2 + 2).
- Related tests: all of `tests/unit`, all of `tests/security`, and every other test module that mentions the news
  alarm, Jev, the subscription service or operation, the paper service, the price collector, protected budget origins,
  the budget gateway and receipts, the event engine, migrations, protected paths, the network profile or proxy and the
  reviewed routes, the Perplexity installation, HQ, wire schemas, the financial checkpoint or service, the deployment
  runtime or the market-data commands (`grep -lE` over `tests`: 223 of 391 modules plus the remaining security
  modules, including `tests/integration/test_provider_wire_schemas.py`, `test_paper_service.py`,
  `test_price_collector_service.py`, `test_owned_subscription_service.py`, `test_subscription_operation.py`,
  `test_perplexity_in_service.py`, the `test_migration_00xx.py` modules and the HQ static and JS behaviour tests), in
  three shards of whole modules run side by side without parallel plugins (`uv run pytest -p no:cacheprovider -q -rfs
  --junitxml=... <modules>`) at `7a80c43` (only `docs/EVENT-ENGINE.md` changed during the run): **5,849 tests: 5,827
  passed, 0 failed, 22 skipped** (shard 1, unit, security, property and HQ static: 2,927 tests, 21 skipped, 2 min;
  shard 2: 1,654 tests, 1 skipped, 22 min; shard 3: 1,268 tests, 0 skipped, 27 min). Skips: 14 in
  `tests/security/test_subscription_process_boundary.py` (bubblewrap is not installed in the cloud container, as on
  GitHub), the indicator cross-checks the second library cannot make, and the archive-permission cases (this user can
  read any file). An earlier run of the closest modules had found the one existing assertion this package changes
  (`test_perplexity_in_service.py`, see "Shared files").
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Measured here: a route Jev check's money path (reserve with origin, dispatch check, commit with receipt
  commitment, allocation) took about 70 ms in this container (four durable transactions); validating one route
  reservation in the audit about 0.45 ms (1,500 in 0.67 s). At the expected ~200 checks a day a month is ~6,000
  reservations, so the audit's per-poll cost stays near 0.1 s plus the 500-row sweep (~0.25 s).
- Real or synthetic: everything is synthetic and labelled: the RuntimeFlow account, a recorded live scan written as in
  W8-A's day, the owner's folder served from a dictionary (a synthetic network profile with the `news_feeds` route, a
  made-up key, the 2026-10-15 Jev card re-dated under a `synthetic-` id for the 2026-01-01 clock), scripted feeds, and
  `ScriptedJevHttp` answers (receipts `synthetic`). Two money tests mark a scripted Jev transport not synthetic to stand
  in for the pinned network transport on the money path; they say so and are not Jev or paid-provider tests. No feed,
  TypeSafe, Perplexity, model or exchange call and no order.

## Schema and dependencies

- State migration: **none** (0046 unused): the news check uses T44's, T45's and the protected origins' tables.
  `adapters/persistence/migrate.py` is not touched. Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Step L2 (owner, local session):** record one real sample of each feed (W1-A's open item) and, with a key, the
  opt-in Jev smoke; re-read TypeSafe's models page: the 2026-10-15 card refuses real calls from 2026-11-15, and a
  re-checked price is a new dated card (new id, new `verified_at`, its source) under `jev.price_card` in the owner's
  `news-alarm.json`. The cloud made no TypeSafe or feed request (the package forbids it).
- **Step L3 (owner):** step 3's `news_feeds` route in `subscription-network-profile.json` and on the reviewed proxy (with
  the profile's re-pin and the proxy's new review), and, for Jev, `news-alarm.json` with `calls_enabled` and
  `typesafe.key`; step 13 shows `news_route` `ready`. The real proxy container, profile and image were not run here
  (no Docker).
- **Step 17 (owner, after L3):** unchanged switch (`news-wakes`), now fed by the in-service poll.
- **Shared files for the integrator (keep both sides):** `application/paper_service.py` (a parameter, two attributes,
  `_poll_news`, two lines in `request_stop`/`_start`, one in `_tick`, one block in `_shutdown`),
  `application/subscription_operation.py` (an import, one line in `prepare`, one result key),
  `kernel/authority.py` (three lines appended to `PROTECTED_PATH_PREFIXES`), `kernel/protected_budget.py` (an optional
  scope argument), `kernel/subscription_proxy.py` (one catalogue entry), `adapters/news/feeds.py`,
  `application/news_shadow.py`, `application/news_watchlist.py`, `application/news_report.py`,
  `market_data_cli/news_alarm.py`, `api/hq_contract.py`, `api/hq_copy.py`, `api/hq_state.py` (one problem prefix each),
  `docs/NEWS-ALARM.md`, `docs/REVIEWED-NETWORK-ROUTES.md`, `docs/L3-INSTALL-CHECKLIST.md`, `docs/EVENT-ENGINE.md`
  (one bullet). One existing test changed:
  `tests/integration/test_perplexity_in_service.py::test_with_the_switch_off_the_same_day_runs_as_before` asserted that
  no owner file is read with the Perplexity switch off; the news check now reads the network profile at start to see
  whether its own route is listed, so the assertion now expects exactly that one read (its comment says why; nothing
  Perplexity-related is read). Copy D255–D259 into `docs/DECISIONS.md`; 0046 is unused.
- **Found, not changed (integrator or owner):**
  - The runtime evidence collector reads whole tables bounded at 10,000 rows (`MAX_ROWS`); route reservations (Jev's
    ~200 a day, Perplexity's) count toward that bound, which Jev alone reaches in about 50 days. It matters only for
    the economic and live evidence gates, which a paper subscription installation does not run.
  - The route's protected origins are authenticated but not part of the subscription deployment's file witness, which
    detects a rolled-back database for that deployment's own records; a restored older state file could drop recent
    Jev records (small amounts; the receipts were also in HQ). Covering route deployments in the witness would bring
    back the growth D243 and D258 avoid.
  - `news-alarm-poll`'s settings reader does not catch `decimal.InvalidOperation` (a settings file with a malformed
    decimal stops the command with a traceback instead of the usual refusal); the in-service reader catches it.
  - `application/budget.py` and `application/gateway.py` are still not protected paths (W8-A's finding stands).
  - W8-A's Perplexity route could adopt the scoped protected origins of D258 (`ProtectedBudgetOrigins(financial,
    deployment_id="<deployment>:perplexity")` with the same per-reservation checks); not done here (scope).
