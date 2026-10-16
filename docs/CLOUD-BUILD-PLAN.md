# Cloud build plan for the day-trader engine

Status: plan, 2026-10-16 (D139). Phases D0 and D1 are done (T23–T27), and
cloud wave 1 is merged (T28, T33, T36, T44 and C1; D140–D164). Every
owner decision in the [build plan](DAY-TRADER-BUILD-PLAN.md) is decided (D138).
This file splits the remaining work into packages that cloud coding sessions
can build in parallel, and lists the steps that must stay on the owner's PC.

Integration branch: `claude/day-trader-engine-20261012`. All cloud work starts
from it and returns to it through pull requests.

## Part 1 — For the owner

### In one minute

- The work is split into **18 cloud packages in 7 waves**, plus **4 local
  steps** on your PC and a short list of things only you can do.
- Packages in the same wave don't depend on each other, so you start them all
  at once, one cloud session each.
- When a wave's sessions have opened their pull requests, you start one more
  session, the **integrator**. It checks and merges that wave's work, then
  updates the status board below.
- Then you start the next wave. Repeat until wave 7.
- After that, a session on your PC installs the new engine (with your
  approval) and starts the scored practice test.

Nothing in this plan trades real money, spends on paid AI services, or changes
the running system without your approval.

### How to start a cloud session

1. Open Claude Code on the web (claude.ai/code) and start a new session on the
   repository **krybaby7/trade-graph**.
2. The first time, create a cloud environment with the default network setting
   and **no secrets or environment variables**. No session in this plan needs a
   key. Claude's GitHub app must have access to this repository, or sessions
   can read it but not push: on GitHub, open Settings → Applications →
   Installed GitHub Apps → Claude → Configure, and add `trade-graph` under
   Repository access.
3. Paste the package's prompt from Part 2 and send it. Each prompt is a short
   block that starts with "You are a cloud build session".
4. Start one session per package. Sessions in the same wave can all run at
   once.

You can also ask the Claude session on your PC to "start wave 1 in the cloud",
and it will launch the sessions for you.

**Overnight, or while you are away:** one session, the night lead, can run
several waves one after another by itself, building, merging and testing
each wave before it starts the next. Paste the night-shift prompt from Part 2
and name the waves. In the morning, read `planning/handoffs/NIGHT-<date>.md`
on GitHub, or open the session. Your PC does not need to stay on.

**This repository is public.** Never paste a key, password or account detail
into a cloud session.

### The waves

| Wave | Packages (run side by side) | Starts when |
|---|---|---|
| 1 | W1-A news alarm log, W1-B fill engine, W1-C web research connection, W1-D price alarms, W1-E green checks | Now |
| 2 | W2-A practice range, W2-B coin scanner, W2-C HQ spending view | Wave 1 merged |
| 3 | W3-A money limits and coin list | Wave 2 merged |
| 4 | W4-A day plans | Wave 3 merged |
| 5 | W5-A event engine, W5-B HQ charts, W5-C scorecard | Wave 4 merged |
| 6 | W6-A focus coins, W6-B Leader news, W6-C HQ wake log, W6-D install readiness | Wave 5 merged |
| 7 | W7-A real news wake-ups | Wave 6 merged |

The slowest chain is W1-B → W2-A and W2-B → W3-A → W4-A → W5-A → wave 6 →
local install. If you can only start a few sessions at a time, start W1-B
first.

### Status board

The integrator updates this table each time it merges a wave.

| Package | Task | What you get | Status |
|---|---|---|---|
| W1-A | T44 | Jev reads headlines and logs whom it would have woken | Merged 2026-10-16 (#3); real feeds and Jev checked at L2 |
| W1-B | T28 | Cautious fill and safety-exit engine with Kraken and OKX fee profiles | Merged 2026-10-16 (#2); fee pages re-read at L2 |
| W1-C | T36 | Perplexity web research connection with receipts | Merged 2026-10-16 (#4); first real call at L2 |
| W1-D | T33 | Price alarms that fire once | Merged 2026-10-16 (#5) |
| W1-E | C1 | The automatic checks pass cleanly (no known failures) | Merged 2026-10-16 (#6) |
| W2-A | T29 | Practice range: replay strategies on history with real fees | Merged 2026-10-16 (#9); first real reports and an OKX recording at L2 |
| W2-B | T31 | Hourly coin scanner and shortlist of about 10 | Merged 2026-10-16 (#8); first real scan and a Ticker recording at L2 |
| W2-C | T40 | HQ shows AI spending per department, $20 alert | Merged 2026-10-16 (#7); first real receipts and a look on phone and iPad at L2 |
| W3-A | T32 | Your money limits and coin rule enforced by software | Not started |
| W4-A | T34 | Trader day plans with resting entries and safety exits | Not started |
| W5-A | T35 | Event engine: wake on alarms, fills, exits and check-ins | Not started |
| W5-B | T41 | Price charts in HQ | Not started |
| W5-C | T42 | Scorecard: result after fees vs. cash and Bitcoin | Not started |
| W6-A | T37 | Research picks daily focus coins on Perplexity | Not started |
| W6-B | T38 | Leader news rounds and wake notes | Not started |
| W6-C | T39 | HQ wake log and next check-ins | Not started |
| W6-D | C2 | Code ready to install: one price collector, new data store wired in | Not started |
| W7-A | T45 | News alarms really wake the Leader and Trader | Not started |
| L1 | — | Price data kept current on your PC | Not started |
| L2 | — | First real practice-range reports and paid-service smoke tests | Not started |
| L3 | — | New engine installed on your PC (your approval) | Not started |
| L4 | T43 | Scored practice test, at least 4 weeks and 30 trades | Not started |

### Only you can do these

| When | What | Why |
|---|---|---|
| Now | Switch on the price schedule on your PC, or tell the local Claude session it may | Windows blocked the agent. Without it, gaps appear in recent prices. |
| Now | Check that Perplexity auto top-up is off | The prepaid credit is the outer limit while testing has no budget (D125). |
| Optional | Get a Jev key at console.typesafe.ai only, then ask the local session to store it | The news alarm makes real checks only with a key. It is built and tested without one. |
| Each wave | Start the wave's sessions, then the integrator, or one night lead for several waves | Cloud sessions don't start themselves. |
| Step L3 | Approve installing the new engine and the policy update with your D138 settings | Agents cannot change protected owner settings. |
| About 2 weeks after L3 | Set the Perplexity monthly limit at about 1.5 × what HQ shows (D138) | Testing runs without a limit until then. |
| End of testing | Revoke the Perplexity test key | D125. |

## Part 2 — For the build sessions

### Rules for every cloud session

These rules add to [AGENTS.md](../AGENTS.md), which always applies.

**Start.**

1. Base: `git fetch origin claude/day-trader-engine-20261012`, then
   `git checkout -B <your branch> origin/claude/day-trader-engine-20261012`.
   Your branch is `claude/<task>-<slug>` as named in your package, or the
   `claude/` branch your session already has.
2. Install: `pip install uv` if it is missing, then `uv python install 3.12`
   and `uv sync --frozen --group dev --python 3.12`. The cloud container's
   default Python is 3.13, and plain `uv sync` would pick it; the system
   `/usr/bin/python3.12` is not a substitute (see "Cloud container failures").
3. Read AGENTS.md, this section, your package below, your task in
   `planning/tasks.json` with its `specs`, and the decisions it names in
   `docs/DECISIONS.md`. D114–D139 explain the day-trader design. D116 (no caps
   on agent activity), D127 (no riba: spot, long only, no interest), D118 (no
   in-house indicator formulas) and D138 (the owner's settings) apply to every
   package.

**Build.**

4. Work test-first. A new test must fail before your change and pass after.
   Use small typed modules, Pydantic contracts, Decimal for money and
   quantities, deterministic clocks and scripted providers.
5. Default tests make no network call and need no key. A test that uses a
   recorded public fixture says so. A mock is never reported as a real paid or
   exchange test.
6. Paid calls stay off. No real orders. Do not deploy or touch any
   installation. Keys never appear in code, logs, fixtures or model context.
7. Existing databases must still open and old records stay readable. Anything
   the plan says starts switched off (for example order installation before
   owner values exist) stays off by default.
8. Keep to your package's scope. If you find a problem outside it, describe it
   in your handoff instead of fixing it.

**Shared files (to avoid merge conflicts).**

9. Do not edit `planning/progress.json`, `IMPLEMENTATION-STATUS.md` or
   `docs/DECISIONS.md`. Write your status and decisions in your handoff file;
   the integrator moves them.
10. Decision numbers: use only your package's reserved block. Code comments and
    docs may cite them before the integrator copies them into the log.
11. Database migrations: if you need one in the state database, use your
    reserved version in `src/trade_graph/adapters/persistence/migrate.py`. If
    you change the candle database schema (`market_data_schema.py`), take the
    next free version and say so in the handoff. The integrator keeps versions
    in order and renumbers if needed.
12. Dependencies: add a package to `pyproject.toml` and `uv.lock` only if your
    package needs it, with an exact reason in the handoff. Price records go in
    `planning/model-prices.json` as new dated entries; never edit an existing
    entry.

**Finish.**

13. Run, and report the real results:
    - `uv run ruff check src tests`
    - your new and related tests
    - `python3 scripts/check_plan.py` and `python3 scripts/test_planning.py`
    - `python3 scripts/check_repository_hygiene.py`
    - the full suite once, `uv run pytest -p no:cacheprovider -rf` (about 1 to
      2 hours in a cloud container). Compare with the known failures below.
      Report any new failure, even if you think it is unrelated.
14. Write `planning/handoffs/<package>.md` from the template in
    `planning/handoffs/README.md`. Start it with three plain-English sentences
    for the owner, who is not a programmer or trader.
15. Commit in small steps with plain sentences as messages, like the existing
    history. Push your branch (`git push -u origin <your branch>`) and open a
    pull request into `claude/day-trader-engine-20261012`, titled
    `<package> <task>: <title>`, with the handoff's owner summary as the
    description. Do not merge it. In a cloud session `gh pr create` is refused
    because it uses GitHub's GraphQL API; use the REST API instead:
    `gh api repos/krybaby7/trade-graph/pulls -f title="..." -f head="<your branch>" -f base="claude/day-trader-engine-20261012" -f body="..."`.
    If that is refused too, use the session's Create PR button and set the
    base branch to `claude/day-trader-engine-20261012` on GitHub's page.
    If a push is refused with an authorization error, keep your commits, say
    so plainly, and retry when the owner says access is fixed: Claude's GitHub
    app needs access to this repository.
16. If something only the owner can provide is missing (a key, a decision,
    private data), build and test everything you can without it, and list the
    rest in the handoff. Do not stop early and do not ask for a key.

**Known failures.** None since W1-E (C1) was merged on 2026-10-16. The wave 1
integration ran the full suite at `c4bde76` on Python 3.12.13 (from
`uv python install 3.12`) in a cloud container: 6,664 passed, 0 failed, 23
skipped. Any failure now counts and must be reported. That run took 105
minutes in the container (W1-E measured 54); GitHub's test job may run 120.

**Cloud container failures.** In a cloud session some isolation tests can also
fail because of the container itself. Bubblewrap (`bwrap`) is not installed:
the tests that really run it skip, as they do on GitHub. With the system
`/usr/bin/python3.12`, the plugin runtime refuses Debian's symlinked
`_sysconfigdata` file (found by W1-B, 2026-10-16); the Python from `uv python
install 3.12` has no such link, and with it the wave 1 integration run had no
container failure. These are security checks: never skip, loosen or rewrite
them to pass in the cloud. A failure is "new" only if the untouched base
branch passes that test in the same container; check by running the failing
tests on the base commit before reporting.

### Prompt for a package

Replace `<ID>` with the package id (for example `W1-B`). Every package uses
the same prompt; the package section holds the details.

```text
You are a cloud build session for Trade Graph (github.com/krybaby7/trade-graph).
Your work package is <ID> in docs/CLOUD-BUILD-PLAN.md on branch
claude/day-trader-engine-20261012.

1. git fetch origin claude/day-trader-engine-20261012
2. git checkout -B <the branch named in your package> origin/claude/day-trader-engine-20261012
3. Read docs/CLOUD-BUILD-PLAN.md: "Rules for every cloud session" and
   package <ID>. Follow them exactly, then build the package.
4. Finish with planning/handoffs/<ID>.md and a pull request into
   claude/day-trader-engine-20261012. Do not merge it.
```

### Prompt for the integrator

Start this once every package of a wave has opened its pull request. Replace
`<N>` with the wave number.

```text
You are the integration session for Trade Graph (github.com/krybaby7/trade-graph).
Integrate wave <N> following "Integration (INT)" in docs/CLOUD-BUILD-PLAN.md
on branch claude/day-trader-engine-20261012.

1. git fetch origin
2. git checkout -B claude/day-trader-engine-20261012 origin/claude/day-trader-engine-20261012
3. Read docs/CLOUD-BUILD-PLAN.md: "Rules for every cloud session" and
   "Integration (INT)". Follow them for wave <N>.
```

### Integration (INT)

1. List the wave's open pull requests into the integration branch with the
   REST API (`gh api "repos/krybaby7/trade-graph/pulls?base=claude/day-trader-engine-20261012&state=open"`;
   `gh pr` commands are refused in cloud sessions). If a package has none,
   look for its pushed branch (`git ls-remote --heads origin "claude/*"`) and
   merge that instead; if there is neither, stop and report which one is
   missing.
2. For each pull request, in the order the wave lists them:
   - Read the handoff and the diff. Check it against the rules above and the
     task's acceptance criteria in `planning/tasks.json`. Reject (comment and
     leave unmerged) a change that adds a key or private data, enables paid
     calls or real orders, caps agent activity, or edits another package's
     reserved numbers.
   - Merge it with `git merge --no-ff` into the integration branch. Resolve
     conflicts: keep migration versions in numeric order (renumber an unused
     reservation only if the migration code requires contiguous versions),
     keep every dated price record, and keep both sides of document changes.
   - Run the package's own tests after each merge.
3. Fold the handoffs into the shared files:
   - Copy each package's decisions into `docs/DECISIONS.md` under its reserved
     numbers, in number order.
   - Add one new top entry to `IMPLEMENTATION-STATUS.md` for the wave: commit,
     what changed for the owner, commands and real results, failures, missing
     credentials, next wave.
   - Mark a task `done` in `planning/progress.json` only when its acceptance is
     met with evidence (commit, tests). Otherwise `in_progress`, with what is
     missing.
   - Update the status board in this file.
4. Run `uv run ruff check src tests`, `python3 scripts/check_plan.py`,
   `python3 scripts/test_planning.py`, `python3 scripts/check_repository_hygiene.py`
   and the full suite once. A new failure (one the base branch does not have
   in the same container; see "Cloud container failures") means the wave is
   not merged: fix it if it is an integration mistake, otherwise revert that
   merge and report.
5. Push the integration branch. Merged pull requests close themselves. Comment
   on any pull request you did not merge, saying why
   (`gh api repos/krybaby7/trade-graph/issues/<number>/comments -f body="..."`).
6. End with three plain-English sentences for the owner: what merged, what
   didn't, and whether the next wave can start.

### Night shift: several waves in one unattended session

Use this when the owner wants waves to run one after another while away, for
example overnight. One cloud session, the **night lead**, runs the listed waves
in order. Each wave starts only after the previous one is merged and pushed.
The owner is not there: never stop to ask anything. Every owner decision the
packages need is already made (D138); record anything else as an open
question in the morning report.

For each wave, in order:

1. Update: `git fetch origin`, then
   `git checkout -B claude/day-trader-engine-20261012 origin/claude/day-trader-engine-20261012`.
   Check in `planning/progress.json` that every dependency of the wave's tasks
   is `done`. If one is not, stop (see "Stopping").
2. Build each package of the wave in its own git worktree on its own branch
   (`git worktree add ../<branch> -b <branch> origin/claude/day-trader-engine-20261012`,
   with the branch name from "Reserved numbers"). Give each package to its
   own subagent, which starts with a fresh context window: pass the package
   id, its worktree path and the instruction to follow "Rules for every cloud
   session" and its package section. Packages of the same wave may run at the
   same time.
3. Night-shift difference from rule 13: a package runs its new and related
   tests, ruff, and the planning and hygiene checks, but **not** the full
   suite. The wave's integration runs the full suite once, for all of them.
4. Each package writes its handoff, pushes its branch and opens its pull
   request through the REST API (rule 15).
5. Give the wave's integration to one more fresh subagent: the wave number,
   the list of package branches and pull requests, and the instruction to
   follow "Integration (INT)", including the status board, the handoffs
   folded into the shared files and the full suite once (see "Long
   commands"). It pushes the integration branch and returns its plain-English
   summary, the merge commits and the full-suite result.
6. Check the result yourself before the next wave: `git fetch origin`, the
   integration branch head contains every merge commit, and
   `planning/progress.json` there marks the wave's tasks `done`. Only then
   start the next wave.

Keep your own context small: you plan, start subagents, check their results
and write the morning report; the subagents do the building and merging. If
subagents are not available, do the work yourself in order and say so in the
morning report.

**Long commands.** The full suite takes 1 to 2 hours. Use either way, and
report which:
- Push the merged, not yet pushed integration result to a candidate branch,
  `claude/night-candidate-wave<N>`, and wait for GitHub's "Test runtime" check
  on that commit (about 65 minutes). Read it with
  `gh api repos/krybaby7/trade-graph/commits/<sha>/check-runs`, checking about
  every 10 minutes. A failed, cancelled or timed-out check counts as failed;
  read its log (`gh api repos/krybaby7/trade-graph/actions/jobs/<job id>/logs`)
  before deciding. On success, push the same commit to the integration branch.
- Or run it in the container in the background, with output to a log file,
  and wait for it to finish. If one run is cut off by a time limit, run it in
  slices (by test folder) and add the results up.
Do not end your turn while a long command runs; nobody will start you again.

**Stopping.** Stop the night shift, and start no further wave, when:
- the wave's full suite has a new failure that is not an integration mistake
  you can fix;
- a package cannot meet its acceptance without something only the owner can
  provide, and the plan does not say to build around it;
- a push is refused.
Leave the integration branch at the last good, fully tested wave. Push the
finished package branches and keep their pull requests open. Then write the
morning report.

**Morning report.** Keep `planning/handoffs/NIGHT-<date>.md` (the date the
shift started) on the integration branch. Create it after the first wave and
update it after every wave, committing and pushing each time, so the owner
can read progress while the shift is still running. It starts with three to
six plain-English sentences for the owner: which waves merged, what the owner
can now do or see, what failed or stopped, what is running now, and anything
that needs a decision. Then, per package: branch, pull request, merge commit
and test results, and the full suite result per wave with how it was run.
Then what comes next. End the session with the same plain-English summary.

Prompt (replace the wave numbers):

```text
You are the night lead for Trade Graph (github.com/krybaby7/trade-graph).
Run the night shift for waves 2, 3 and 4, following "Night shift" in
docs/CLOUD-BUILD-PLAN.md on branch claude/day-trader-engine-20261012.
The owner is asleep: never stop to ask anything.

1. git fetch origin
2. git checkout -B claude/day-trader-engine-20261012 origin/claude/day-trader-engine-20261012
3. Read AGENTS.md and docs/CLOUD-BUILD-PLAN.md: "Rules for every cloud
   session", "Integration (INT)", "Night shift" and the sections of your
   waves' packages. Then run the waves in order and finish with the morning
   report.
```

### Reserved numbers

New owner decisions after this plan start at D240.

| Package | Branch | Decisions | State migration |
|---|---|---|---|
| W1-A | `claude/t44-jev-shadow` | D140–D144 | 0026 |
| W1-B | `claude/t28-fill-engine` | D145–D149 | 0027 |
| W1-C | `claude/t36-perplexity` | D150–D154 | 0028 |
| W1-D | `claude/t33-price-alarms` | D155–D159 | 0029 |
| W1-E | `claude/c1-green-checks` | D160–D164 | none |
| W2-A | `claude/t29-practice-range` | D165–D169 | 0030 |
| W2-B | `claude/t31-coin-scanner` | D170–D174 | 0031 |
| W2-C | `claude/t40-hq-spending` | D175–D179 | 0032 |
| W3-A | `claude/t32-money-limits` | D180–D184 | 0033 |
| W4-A | `claude/t34-day-plans` | D185–D189 | 0034 |
| W5-A | `claude/t35-event-engine` | D190–D194 | 0035 |
| W5-B | `claude/t41-hq-charts` | D195–D199 | 0036 |
| W5-C | `claude/t42-scorecard` | D200–D204 | 0037 |
| W6-A | `claude/t37-focus-coins` | D205–D209 | 0038 |
| W6-B | `claude/t38-leader-news` | D210–D214 | 0039 |
| W6-C | `claude/t39-hq-wake-log` | D215–D219 | 0040 |
| W6-D | `claude/c2-install-ready` | D220–D224 | 0041 |
| W7-A | `claude/t45-news-wakes` | D225–D229 | 0042 |
| L1–L4 | local, see below | D230–D239 | none |

### Wave 1 — start now

Order for the integrator: W1-E, W1-B, W1-D, W1-C, W1-A.

**W1-A · T44 · News alarm in log-only mode.** Jev, a cheap decision model,
reads headlines every 5 minutes and the system logs whom it would have woken.
Nobody is woken yet.
- Spec: build plan section "DN — News alarm with Jev", D137, D138 item 10,
  source S40. Facts checked on TypeSafe's official docs on 2026-10-15:
  `POST /v1/systemone` takes a state and typed questions (`noul`, `choice`,
  `score`) and returns probabilities, no text; USD0.042 per million input
  tokens, output free; pin `jev-1.13.0`; responses report `model` and `usage`;
  no seed.
- Jev is not a model-provider adapter: give it its own small decision contract.
  The existing `PriceCard` accepts an output price of 0.
- Choose the free, keyless feeds at build time and record them (Kraken status
  and announcements plus a few major crypto outlets). Record a small public
  fixture of each feed if the cloud network allows; otherwise write a synthetic
  fixture in the same format, labelled synthetic, and list the real recording
  for step L2.
- Include an injection test: a headline that tells the system to do something
  changes nothing.
- Done when every T44 acceptance item passes with scripted Jev answers and
  the CLI report works on fixtures.

**W1-B · T28 · Fill and safety-exit engine.** One cautious fill module shared
by the paper broker and the practice range, plus dated fee profiles.
- Spec: build plan section D2, D119, D126. Golden cases are in T28's
  acceptance. Protective exits fill as taker orders with slippage (build plan
  "Notes for implementers").
- Fee profiles are data records with source links and dates: Kraken spot
  starting tier (0.40% maker / 0.80% taker per side), Kraken after USD10k
  30-day volume (0.22% / 0.38% per side), OKX Europe spot (0.10% / 0.20% per
  side). Check each against the venue's official fee page and record the date
  you read it; if a page can't be reached from the cloud, keep the build-plan
  figure and say so.
- The existing T05 broker guarantees and broker conformance must still pass.

**W1-C · T36 · Perplexity web research connection.** The adapter that lets
Research and the Leader think with Claude Sonnet 5.5 and search the web through
Perplexity, with a receipt for every call.
- Spec: build plan section D5, D121, D122, D124, D125. Prices from the dated
  card: USD2 / USD10 per million input / output tokens, USD0.0025 per search,
  USD0.0005 per page fetch (published 2026-10-12). Re-check Perplexity's
  official pricing page and record the date; D122's search price came from
  third-party pages.
- Search runs in software as a registered tool so every result is stored
  before a finding can cite it.
- The key is read only from the owner's private file path. No key exists in
  the cloud; the credentialed smoke test is step L2.
- Research and the Leader do not switch to Perplexity in this package (that is
  W6-A and W6-B).

**W1-D · T33 · Price alarms.** Saved alarms on price levels and indicator
thresholds that fire once and wait to be re-armed.
- Spec: build plan section D4, D116, D117, D123. Indicators come from the T27
  menu (`docs/INDICATOR-MENU.md`); read candles through the candle store
  (`CandleStore.window`), never from the financial database.
- No limit on the number of alarms or how often they fire, beyond the
  one-shot rule.
- The wake dispatcher is W5-A. Here, deliver fired alarms to a persisted queue
  with a clear contract that W5-A will consume; describe the contract in the
  handoff.

**W1-E · C1 · Green checks.** Make the automatic test suite pass cleanly, so
every later pull request gives a clear pass or fail.
- Fix the root cause of each known failure listed above. Mission Control's
  progress page (`src/trade_graph/api/progress.py`) must accept the `todo`
  status and the full `updated_at` timestamp, and the packaged
  `progress_catalog.json` must include T23–T45.
- For timing cases that fail only under load, use deterministic clocks or
  justify a changed limit in the handoff. Never delete a test or weaken what it
  proves.
- Also run the GitHub Actions workflow's steps (`.github/workflows/test.yml`)
  and fix anything that fails there but not locally.
- Touch only test files, the progress page and its catalog, and the minimum
  source each fix needs.

### Wave 2 — after wave 1 is merged

Order for the integrator: W2-A, W2-B, W2-C.

**W2-A · T29 · Practice range.** Replays a strategy version on history under
each fee profile and writes a report.
- Spec: build plan section D2, D119, D138 item 5 (report required, no pass
  mark). Tune on an earlier window, confirm on a later untouched one, with
  dates.
- Read history through the candle store. The cloud has no real archive: build
  and test on synthetic and recorded fixtures. The first real reports are
  step L2.
- OKX public candles: record a small fixture if okx.com is reachable;
  otherwise use a synthetic OKX-format fixture, labelled, and list the
  recording for L2.
- Put the result sums in a metrics module that W5-C's scorecard reuses.

**W2-B · T31 · Coin scanner.** Hourly ranking of all Kraken USD coins and a
shortlist of about 10.
- Spec: build plan section D3, D120, D127, D131. Swing and trend come from
  menu indicators. Round-trip cost comes from the W1-B fee profiles.
- Add the price-peg check for unreviewed stablecoins. 608 of 677 USD coins
  are not individually reviewed (`docs/PAIR-CATALOG.md`); the 2026-10-15
  status entry names U, STBL, UNITAS, CAP, DEBIT, RE, ST and USDE as needing
  a look.
- Replays on archive history are labelled optimistic (delisted coins are
  missing).

**W2-C · T40 · HQ spending view.** HQ shows AI spending per department from
receipts, the testing total next to the prepaid credit, and the USD20 alert.
- Spec: build plan section D6, D131, D138 item 4. HQ design rules are in
  `docs/HQ-DASHBOARD.md`: plain English, phone and iPad first, light and dark.
- Unknown usage shows as unknown, never as zero. Paper gains never appear to
  pay for AI.

### Wave 3 — after wave 2 is merged

**W3-A · T32 · Money limits and coin list.** The owner's limits become
software rules.
- Spec: build plan section D4, D138 items 1, 2, 3 and 6.
- Add an owner command that previews and, with `--confirm`, records a policy
  revision with the D138 values, in the style of `trade-graph mandate-cleanup`.
  The owner runs it at step L3; tests use synthetic values.
- Property tests over random fill sequences: no sequence breaches the per-coin
  or total limit.

### Wave 4 — after wave 3 is merged

**W4-A · T34 · Day plans.** The Trader writes a plan per focus coin; software
installs resting entries, each with a safety exit.
- Spec: build plan section D4, D115, D117, D119, D138 item 5. Uses W1-B fills,
  W2-A reports, W3-A limits and W1-D alarms.
- Persist intents before effects. Unknown order status is reconciled before
  anything is replaced. A pause cancels resting entries and keeps exits.
- No confidence threshold, committee or Leader approval of a plan.

### Wave 5 — after wave 4 is merged

Order for the integrator: W5-A, W5-C, W5-B.

**W5-A · T35 · Event engine.** Departments wake on alarms, fills, safety exits
and Leader requests, and each picks its own next check-in. Replaces the fixed
4-hour timer.
- Spec: build plan section D4, D116, D123, D138 items 6 and 8. This package
  owns the wake, check-in and focus-step contracts that W6-A, W6-B, W6-C and
  W7-A build on; document them in the handoff.
- Coalescing may batch wakes, never drop them. No cap on wakes.
- The scripted end-to-end day in T35's acceptance is required.

**W5-B · T41 · HQ charts.** Candles, chosen indicators, alarm lines and orders
drawn with TradingView Lightweight Charts.
- Vendor one pinned release (Apache-2.0) into HQ's static files, with its
  license and the required TradingView attribution link. No outside script or
  data request at runtime.
- Phone and iPad widths, light and dark.

**W5-C · T42 · Scorecard.** Profit after fees compared with holding cash and
holding BTC, with AI spending beside it.
- Reuse W2-A's metrics module. Exact Decimal golden fixtures, including partial
  fills and fees.
- The scorecard states that working software, economic evidence and permission
  for real money are separate gates.

### Wave 6 — after wave 5 is merged

Order for the integrator: W6-D, W6-A, W6-B, W6-C.

**W6-A · T37 · Focus coins.** Research reviews the scanner's shortlist with
news and names the day's focus coins on Perplexity.
- Spec: build plan section D5, D121, D124. Uses W1-C and W5-A. Research notes
  last for the day plan, not 47 minutes.
- Switching Research to Perplexity in the installation is a protected setting:
  ship it switched off with an owner command to turn it on at step L3.

**W6-B · T38 · Leader news rounds.** The Leader skims crypto and AI-trading
news, asks Research for deep dives and can wake the Trader with a note.
- Spec: build plan section D5, D122, D124. The Leader never places, approves
  or blocks a trade. Same protected switch as W6-A.

**W6-C · T39 · HQ wake log.** Every wake-up with its reason, each department's
next check-in, active alarms and the day's focus coins.
- Built only from saved records; reading HQ starts nothing. Payload budget and
  phone layout as in `docs/HQ-DASHBOARD.md`.

**W6-D · C2 · Install readiness.** The code changes needed before the new
engine can be installed.
- The hourly features read the candle store instead of `hourly_candles`
  (D136 "later work").
- One price collector inside the service: the service keeps the candle store
  current for the shortlist and focus coins and replaces the two Windows
  scheduled tasks once installed. Use one shared rate limiter for the
  collector and the Trades backfill (status of 2026-10-15).
- The runtime layout includes the candle file; `scripts/build_protected_image.py`
  includes the NumPy and TA-Lib wheels. If the cloud cannot build images, test
  the script's inputs and leave the real build to step L3.
- A written install checklist for L3 in `docs/` that names each command in
  order.

### Wave 7 — after wave 6 is merged

**W7-A · T45 · News alarms wake people.** A news alarm wakes the Leader, and
the Trader when the coin is held.
- Spec: build plan section DN, D137, D138 item 10. Uses W5-A's dispatcher and
  W6-C's wake log. Ships in shadow mode; going live is an owner switch decided
  from shadow evidence after L3.

### Local steps — on the owner's PC

These need private data, keys, WSL or the installation, so they run in a
Claude session on the owner's PC. They follow the same branch, handoff and
pull-request flow, with the handoff at `planning/handoffs/L<n>.md`.

**L1 · Keep price data current (now).** With the owner's yes, register the
two scheduled collection tasks (`scripts/register_candle_schedule.ps1`), or
guide the owner to run it. Run one full collection pass now. Report coverage
with `trade-graph candle-coverage`. Confirm the archive's open-time
convention, which the import assumed: backfill one September BTC/USD
15-minute slot from public Trades and reconcile it against the archive
(`reconcile-candles`), because the archive (to 30 September) and the collector
(from 13 October) don't overlap yet. Ask the owner before downloading the
2026Q2 archive (538 MB), which would give the practice range six months of
history instead of three.

**L2 · Real-data checks (after W2-A and W1-C are merged).** Practice-range
reports for the two seed strategies on the real imported history, under all
three fee profiles. Record the real OKX and news-feed fixtures if the cloud
used synthetic ones. One opt-in Perplexity smoke call with the test key
(D125), with its receipt. A Jev smoke call only if the owner has installed a
key. Each result is labelled real or synthetic.

**L3 · Install the new engine (after waves 1–6 are merged).** Each step only on
the owner's yes: full suite; build the protected image and re-pin the kernel
manifest; back up; install through the financial-manifest transition with the
paper account in MANAGE_ONLY (docs/FINANCIAL-TRANSITION.md); run
`trade-graph mandate-cleanup`; record the D138 policy revision (W3-A's
command); switch Research and the Leader to Perplexity (W6-A, W6-B); confirm
the new collector runs, then remove the Windows scheduled tasks; resume.

**L4 · T43 · Scored practice test.** Run for at least four weeks and at least
30 completed trades (D138 item 7). Weekly owner check-ins in HQ. The final
report: scorecard, wakes by reason, AI spending per department, incidents and
what to tune next. A good result does not authorize real money.
