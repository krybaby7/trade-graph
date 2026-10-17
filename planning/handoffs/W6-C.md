# W6-C T39: HQ wake log and next check-ins

For the owner: HQ now has three new panels: in the Calendar tab, "Next check-ins" shows when each department will look
next (the time it chose itself, or the next day start) with today's routine step by step, and "Today's focus coins and
alarms" shows the coins Research picked and the price alarms that are set; in the Story tab, the "Wake log" lists every
wake-up with its reason, the department, how much AI allowance was left at that moment and what came of it. All of it
is read from the system's own saved records, looking at it never wakes anyone or places an order, and it was checked
with made-up records only, because wake-ups only start in your installation once the new engine is switched on at step
L3. Nothing needs a decision from you now; after L3, please look at the Calendar and Story tabs on your phone and iPad,
and after the first week use the wake counts at the top of the Wake log to decide on the 40% allowance floor (D138
item 8).

Branch: `claude/t39-hq-wake-log`, head: the commit that adds this line; code at `f1c80d0`. Pull request: (added after
it is opened).

## What was built

- `src/trade_graph/api/hq_wakes_contract.py`: the `trade-graph.hq.wakes.v1` document (strict Pydantic, the HQ
  snapshot's `Text`, `ISO`, `Quote` and `Dec` types, the event engine's vocabularies from `contracts/wakes.py`): a page
  of wakes newest first (`id`, `cursor`, department, origin, purpose, status, tone, times, headline, reasons with
  counts, events, `more_events`, the allowance reading, result, the department's summary and the notes it was handed
  as labelled quotes, link, technical names) and, on the first page, `day`: today's five daily-cycle steps, each
  department's next check-in, the focus coins, the alarms that are set and the wake counts. Validators: newest first,
  unique ids, a closed wake has its close time and an open one has none, the next page starts below this one,
  department counts add up, focus `named` iff coins, nothing listed without wake records.
- `src/trade_graph/api/hq_wakes.py`: the projection `wake_log(runtime, before=None, limit=20)` and the route
  `GET /api/v1/hq/wakes` (any signed-in role, reads only, `before` a positive wake cursor, `limit` 1–30, redacted,
  422 on a bad query). Plain SQL in one `Database.snapshot()`; no engine, scheduler, worker, provider or broker import.
  Next check-ins follow `EventEngine.next_check_ins` exactly; `alarm_words` words an alarm the way the price charts do.
- `src/trade_graph/api/hq_copy.py`: the `WAKES` copy block (every reason, event, allowance, result, check-in, step,
  focus, alarm and count sentence) and three `SECTION_HELP` entries (`wakeplan`, `watchlist`, `wakes`).
- `src/trade_graph/api/app.py`: one import and one `hq_wakes.register(...)` line after `hq_chart`.
- Page: `web/templates/hq.html` (the script tag before `hq.js`; "Next check-ins" and "Today's focus coins and alarms"
  panels after "Your check-ins" in the Calendar section, the "Wake log" panel after "Story so far" in the Story section,
  each with its help button), `web/static/hq-wakes.js` (new module `wakes`: three views, reads only while a panel is on
  screen, refreshes with HQ's 30-second poll at most every 25 s, department filter, "Load earlier", drops older pages
  when the newest page no longer meets them so nothing is skipped), `hq-core.js` (the `wakes` read endpoint and
  `wakesQuery`), `hq.js` (one `init` line), `hq-glossary.js` (three help entries), `hq.css` (a wake block before the
  phone rules; theme tokens only).
- Docs: `docs/HQ-DASHBOARD.md` (owner section "Wake-ups", data rule, route), `docs/EVENT-ENGINE.md` (one paragraph under
  W6-C).
- Tests: `tests/unit/test_hq_wakes_contract.py`, `tests/integration/test_hq_wakes.py`,
  `tests/integration/test_hq_wakes_routes.py`, `tests/test_hq_wakes_static.py`, `tests/js/hq_wakes.test.mjs` (run by
  `tests/test_hq_js_behaviour.py`), helper `tests/wake_log_support.py` (synthetic records through the event engine's
  own `WakeStore`). Changed: `tests/test_hq_static.py` (`SCRIPTS`, `FETCH_ALLOWLIST`, the jargon and practice-copy
  checks now include `hq-wakes.js`).

## Decisions (reserved block D215–D219)

- **D215 (2026-10-17, implementation default for T39): the wake log is its own read endpoint, paged.**
  `GET /api/v1/hq/wakes` returns `trade-graph.hq.wakes.v1`; the HQ snapshot, its contract and the nine snapshot
  fixtures are unchanged (W2-C's `ai_spending` and W5-B's chart keys stay as they are), so the snapshot's 80 KB budget
  is untouched. Pages hold 20 wakes (at most 30), newest first by the wake's sequence (`before=<cursor>` continues
  below it); the first page also carries today's block. Per wake at most 6 events are written out and the rest counted,
  at most 3 notes handed to it are quoted at 120 characters, its summary at 280; at most 20 alarms are listed and all
  counted. These are page bounds like a page size, never limits on wakes, triggers or alarms (D116); totals and counts
  are exact `COUNT(*)`. A busy synthetic day (600 wakes of 12 reasons each with long distinct notes and summaries, 45
  alarms, 16 focus coins) is 51.2 KB per default page and 70.7 KB at 30 wakes (budget 80 KB,
  `PAYLOAD_BUDGET_BYTES`), about 0.06 s per page. Reason: T39's acceptance ("a busy synthetic day stays within the HQ
  payload budget"), the night lead's note on the snapshot's budget, D116.
- **D216 (2026-10-17, implementation default for T39): where the owner sees it.** The deliverable's "story and calendar
  entries" are three panels in those tabs rather than new items in the snapshot's calendar and story lists: in the
  Calendar tab "Next check-ins" (with "Today's routine") and "Today's focus coins and alarms", in the Story tab the
  "Wake log" (grouped by local day like the Story, a department filter, "Load earlier"). The page reads them only while
  a panel is on screen (on a phone: the Calendar or Story tab is open) and again with HQ's own poll, at most every 25
  s. Older pages are kept only while the newest page still meets them, so a burst of new wakes can never leave a gap.
  Every time is a tappable time in the viewer's time zone; allowance and alarm times use the short day-and-clock form,
  because the longer relative form made a phone scroll sideways (found in the Chromium check). No glossary key was
  added (the snapshot contract stays untouched); "AI allowance" and "stop line" use the existing terms. Reason: T39's
  deliverables with the payload budget, phone layout without horizontal scroll (acceptance).
- **D217 (2026-10-17, implementation default for T39): what each wake says.** Its reasons are the trigger kinds in the
  contract's order with how many of each; the headline joins them in plain words ("40 price alarms went off and a
  waiting buy filled"). Each trigger becomes one event line from what the wake was handed: an alarm's coin and
  condition (from the alarm delivery in the wake's context, else the alarm record), a fill's exact saved quantity and
  price, a safety exit's leg (stop or target), the requester of a wake request, the time of a check-in, the day of a
  day-end review. The allowance line is the reading saved with the wake: how much was left and when it was read,
  "paused" (new AI work waited; prices and protection went on), or "not known; unknown is never counted as plenty".
  The result says what came of it: waiting to start (or "Awake and working on it" when its task is leased or running),
  being put in place, done after how long, the next time it chose, the focus coins it named, the day plans put in
  place, refused by the software's checks or too late, whom it asked to look; a failed or too-late wake says that what
  it was woken for waits for its next wake-up. Failed and obsolete wakes are marked for attention, a wake that put a
  plan in place as good. The department's summary and the notes it was handed (wake requests, news alarms, its own
  check-in note) appear only as labelled quotes, redacted and cut; raw codes only behind "Show technical names".
  Reason: D116 ("HQ shows per wake, reason and department allowance"), HQ's model-text rule (D105–D110).
- **D218 (2026-10-17, implementation default for T39): check-ins, today, focus coins, alarms and counts.** Each of the
  five departments' next check-in is read as `EventEngine.next_check_ins` reads it (D123): its newest check-in when it
  named a time whose trigger was not yet recorded, otherwise the next day start; HQ cannot know which departments are
  running, so all five are listed and the panel says wake-ups happen only while the team runs. Status: `awake` when it
  has an open wake, `due` when its chosen time has passed, else `planned`; `waiting` counts its pending triggers.
  Today is the trading day whose day start was recorded, else the owner policy's `trading_day_start_utc`, else 00:00
  UTC (D138 item 6). The focus coins are the newest revision of today in `focus_selections` (W5-A's record, which
  W6-A's Research answer is written into), checked against the record's saved SHA-256 like the store does; no coins is
  shown as a valid answer, a failed or skipped focus step says so. Alarms are the armed alarms of this portfolio or of
  none (the charts' rule), sorted by coin; alarms that went off and wait are the pending `alarm_fired` deliveries.
  Counts are the wakes of today and of the last 7 days per department and today's reasons, the figures the owner
  reviews before changing the allowance floor (D138 item 8). Reason: T39's second deliverable, D123, D138.
- **D219 (2026-10-17, implementation default for T39): reading starts nothing; no migration.** The projection runs
  plain SQL inside `Database.snapshot()` (a transaction that ends in a rollback) and imports nothing from
  `trade_graph.application`, the model or broker adapters, or the authority kernel (a test parses its imports); a test
  hashes the whole state database before and after repeated reads and through the route and finds it unchanged, also
  with a check-in that is due. A database from before migration 0035 answers `available: false` with a plain sentence;
  a wake record that can't be read is left out and counted, never invented. State migration 0040 is not used; no
  dependency is added. Reason: T39's acceptance ("built only from persisted records; reading HQ starts no inference or
  order"), D107, D110.

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container, in
`/home/user/claude/t39-hq-wake-log`. No network in any test, no key, paid, model or exchange call.

- Tests written first: `tests/unit/test_hq_wakes_contract.py` failed at collection (`cannot import name
  'hq_wakes_contract'`), `tests/integration/test_hq_wakes.py` (`cannot import name 'hq_wakes'`) and
  `tests/integration/test_hq_wakes_routes.py` (6 of 9 failed: no route, so validation and the read-nothing check
  failed) before the code; `tests/test_hq_wakes_static.py` failed against the base page files; `node --test
  tests/js/hq_wakes.test.mjs` with `HQ_STATIC_DIR` set to the base commit's scripts: 10 of 10 failed (no `hq-wakes.js`),
  and 10 of 10 with the new module but the base `hq-core.js` (no `wakes` endpoint). The first runs found: a page of 50
  busy wakes at 101 KB (bounds set to 20/30 wakes, 20 alarms, notes at 120 characters: 51/71 KB); test mistakes (a note
  over 500 characters, an evaluation-order slip, a selector that clicked the Story's own "Load earlier"). The Chromium
  check found a 10 px sideways scroll on a phone after "Load earlier" from the long relative time form in the allowance
  line (fixed: day-and-clock form).
- New tests: `uv run pytest -p no:cacheprovider -q tests/unit/test_hq_wakes_contract.py
  tests/integration/test_hq_wakes.py tests/integration/test_hq_wakes_routes.py tests/test_hq_wakes_static.py`:
  RESULTS_NEW. `node --test tests/js/hq_wakes.test.mjs`: 10 passed, 0 failed; `node --test tests/js/hq_*.test.mjs`:
  77 passed, 0 failed.
- `uv run ruff check --no-cache src tests scripts/check_repository_hygiene.py scripts/verify_offline_acceptance.py`:
  all checks passed.
- Related tests (every test module that imports or exercises changed code: `create_app`, the dashboard runtime,
  `hq_copy`, the HQ page, scripts and static files, the HQ snapshot, payload and routes, the image builder's package
  files, the event engine's store and migration 0035 (the wake log reads them), T35's scripted day (one new test runs
  it), the provider wire schemas, `model_json_schema` and `export_schemas` users, found with `grep -lE` over `tests`; 66
  modules) in three parallel shards of whole modules (`uv run pytest -p no:cacheprovider -q -rfs <modules>`):
  RESULTS_RELATED.
- `uv run pytest -p no:cacheprovider -q tests/unit`: RESULTS_UNIT.
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic; `python3 scripts/test_planning.py`: 11 tests OK;
  `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Browser check at `f1c80d0` (real page, real FastAPI app via `trade-graph dashboard`, a synthetic database from
  `trade-graph init` with 30 synthetic wakes, check-ins, a focus selection, day steps and 5 alarms written through the
  real stores): Chromium from Playwright 1.56.1 (`/opt/pw-browsers`) at phone 390×844 (Calendar and Story tabs),
  iPad portrait 820×1180, iPad landscape 1180×820 and desktop 1280×900, each in light and dark (system setting), before
  and after "Load earlier": every panel drew (5 check-ins, 5 steps, 3 focus coins, 4 alarms, 20 then 30 wakes), no
  horizontal page overflow and no element past the right edge, no console error or warning (so no Content Security
  Policy violation), no request outside HQ's origin. Screenshots stayed in the session's scratch folder. Not checked:
  Safari/WebKit (only Chromium is installed) and a real iPad.
- Real or synthetic: everything is synthetic and labelled (the T34 test account, wake records written through the
  event engine's `WakeStore`, alarms through the real `AlarmBook`, T35's scripted day with scripted departments). No
  paid, model or exchange call.

## Schema and dependencies

- State migration: none (0040 not used). Candle database: none.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- **Owner / after step L3:** look at the Calendar and Story tabs on your phone and iPad once the engine runs; after
  the first week, read the wake counts at the top of the Wake log before deciding on the allowance floor (D138 item 8).
- **When the event engine is switched on in the installed service (W6-D or step L3) — HQ's rooms and calendar:** the
  existing HQ snapshot still projects each room's "next turn" and the calendar's department turns from the fixed
  schedule rows (`role_schedules`). With `event_engine` the service no longer runs those schedules (W5-A), so those
  rows stop moving and the rooms and calendar would show old fixed-timer turns as overdue. Not changed here (it is
  the snapshot's own projection, `hq_state.py`/`hq_calendar.py`, and its payload budget). Proposed fix, test first, in
  its own small package: when the engine runs (a wake recorded in the current run), take a room's next turn and the
  calendar's department items from `department_check_ins` as this wake log reads them (`hq_wakes._check_ins`), or have
  the switch retire the schedule rows.
- **W6-A (T37, focus coins):** HQ reads the day's focus coins from `focus_selections` (W5-A's record that
  `ResearchFocusReply.outcome()` is written into): symbol, note, number of evidence references, summary, revision. No
  wiring is needed as long as W6-A records focus through the engine; W6-A's branch was not pushed when this was built.
- **W6-B (T38) and W7-A (T45):** a Leader wake request shows as "The Leader asked for it" with its note quoted; a news
  alarm as "A news alarm about <coin>" with its note quoted. A new trigger kind needs its words in `hq_copy.WAKES`
  (`test_every_trigger_kind_has_its_words` fails otherwise).
- **Integrator:** copy D215–D219 into `docs/DECISIONS.md`; mark T39 `done` if the evidence holds. Shared lines touched:
  `api/app.py` (one import, one `register` after `hq_chart`), `api/hq_copy.py` (the `WAKES` block before section help,
  three `SECTION_HELP` keys at its end), `web/templates/hq.html` (one script tag before `hq.js`, three panels),
  `web/static/hq-core.js` (`ENDPOINTS.wakes`, `READS`, `wakesQuery`), `hq.js` (one `init` line), `hq-glossary.js`
  (three `SECTION_HELP` entries), `hq.css` (a block before the phone rules), `tests/test_hq_static.py` (`SCRIPTS`,
  `FETCH_ALLOWLIST`, two parametrize lists), `docs/HQ-DASHBOARD.md` (a section before "Decisions", a data rule, a route
  row), `docs/EVENT-ENGINE.md` (one paragraph). `api/hq_contract.py`, the snapshot and the nine
  `tests/fixtures/hq/snapshot_*.json` are unchanged.
- **Optional, not done:** a scorecard line in HQ (W5-C's `compute_scorecard`); the wake log's page would have room,
  but it is not part of T39.
- Observed outside scope: the integration branch moved one status-only commit (`096f800`) past this branch's base
  (`e1008e5`); no file overlaps.
