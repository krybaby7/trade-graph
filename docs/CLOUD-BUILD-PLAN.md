# Cloud build plan for the day-trader engine

Status: plan, 2026-10-16 (D139). Phases D0 and D1 are done (T23–T27), and
all ten cloud waves are merged (wave 1 on 2026-10-16; waves 2 to 7 by the
night shift of 2026-10-16/17; waves 8 and 9 by the night shift of
2026-10-17/18; wave 10 on 2026-10-18; D140–D278). Waves 8 and 9, added on
2026-10-17, closed the gaps between "built" and "installed" that the night
shift found; wave 10, the owner's tidy-up before the install (D263), locked
the AI-spending records, kept the money checks fast and added HQ's card for a
coin without a safety exit. L1 is done (2026-10-19). L2's first practice-range
test found that both starting strategies lose after fees and that the graph
cannot create or test strategies itself, so the owner added waves 11 to 13 on
2026-10-20 (D280): one Trading desk with its own strategy lab, market types and
news in its strategy choice. L2's other checks, L3 and L4 remain. Every
owner decision in the [build plan](DAY-TRADER-BUILD-PLAN.md) is decided (D138).
This file splits the remaining work into packages that cloud coding sessions
can build in parallel, and lists the steps that must stay on the owner's PC.

Integration branch: `claude/day-trader-engine-20261012`. All cloud work starts
from it and returns to it through pull requests.

## Part 1 — For the owner

### In one minute

- The work is split into **29 cloud packages in 13 waves**, plus **4 local
  steps** on your PC and a short list of things only you can do. Waves 1 to
  10 are done. Waves 11 to 13, added on 2026-10-20 (D280), turn the Trader into
  one Trading desk that finds, tests and chooses its own strategies. L1 is
  done; the install (L3) waits for waves 11 to 13 and starts with your yes.
- Packages in the same wave don't depend on each other, so you start them all
  at once, one cloud session each.
- When a wave's sessions have opened their pull requests, you start one more
  session, the **integrator**. It checks and merges that wave's work, then
  updates the status board below.
- Then you start the next wave. Repeat until wave 13.
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
| 8 | W8-A Perplexity inside the installed system, W8-B Trader exit tools, W8-C HQ loss, plans and scorecard | Wave 7 merged |
| 9 | W9-A news check next to the installed system | Wave 8 merged |
| 10 | W10-A lock the AI-spending records, W10-B fast money checks and scorecard, W10-C HQ card for a coin without a safety exit | Wave 9 merged (added 2026-10-18, D263) |
| 11 | W11-A market types, W11-B the strategy lab inside the service | Wave 10 merged (added 2026-10-20, D280) |
| 12 | W12-A one Trading desk: strategy choice, playbook, news | Wave 11 merged |
| 13 | W13-A the Trading desk in HQ, scorecard by strategy and market type | Wave 12 merged |

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
| W3-A | T32 | Your money limits and coin rule enforced by software | Merged 2026-10-17 (#10); you record the limits at L3 (`trade-graph money-limits --confirm`) |
| W4-A | T34 | Trader day plans with resting entries and safety exits | Merged 2026-10-17 (#11); plans stay off until you record the limits at L3 (`trade-graph money-limits --confirm`) |
| W5-A | T35 | Event engine: wake on alarms, fills, exits and check-ins | Merged 2026-10-17 (#14); off in your installed service until the AI Trader answers wakes (W6-D) and you install at L3 |
| W5-B | T41 | Price charts in HQ | Merged 2026-10-17 (#12); you look at the charts on your phone and iPad at L2 or L3 |
| W5-C | T42 | Scorecard: result after fees vs. cash and Bitcoin | Merged 2026-10-17 (#13); first real scorecard after L3 (`trade-graph scorecard --format text`) |
| W6-A | T37 | Research picks daily focus coins on Perplexity | Merged 2026-10-17 (#16); ships switched off: you switch Research and the Leader on at L3 (`trade-graph perplexity-departments --confirm`) once the installed-service wiring exists ([L3 checklist](L3-INSTALL-CHECKLIST.md), step 12) |
| W6-B | T38 | Leader news rounds and wake notes | Merged 2026-10-17 (#18); the same switch and wiring as W6-A at L3 |
| W6-C | T39 | HQ wake log and next check-ins | Merged 2026-10-17 (#15); you look at the Calendar and Story tabs on your phone and iPad after L3 |
| W6-D | C2 | Code ready to install: one price collector, new data store wired in | Merged 2026-10-17 (#17); the real image build and the first collector pass are L3 steps ([L3 checklist](L3-INSTALL-CHECKLIST.md)) |
| W7-A | T45 | News alarms really wake the Leader and Trader | Merged 2026-10-17 (#19); ships in shadow mode (it records whom an alarm would wake and wakes nobody): after L3 you decide from the shadow log and HQ's wake counts (`trade-graph news-wakes`, [L3 checklist](L3-INSTALL-CHECKLIST.md) step 17), once the news poll runs next to the installed service with your Jev key |
| W8-A | C3 | Research and the Leader can run on Perplexity inside the installed system | Merged 2026-10-18 (#21); ships switched off: at L3 you put the Perplexity key and the price file in your owner folder and add the Perplexity route to your network profile and proxy (step 3), then switch Research and the Leader on (step 12, `trade-graph perplexity-departments --confirm`); the prices are re-checked at L2, because today's price card goes stale on 2026-11-11 ([L3 checklist](L3-INSTALL-CHECKLIST.md)) |
| W8-B | C4 | The Trader can close a position early or move its safety exit | Merged 2026-10-18 (#22); paper only, like the day plans it works on, which stay off until you record the limits at L3 (`trade-graph money-limits --confirm`); one question for the live pilot (T19) is in the status entry |
| W8-C | C5 | HQ shows today's loss, the day plans and the scorecard | Merged 2026-10-18 (#20); you look at the Money tab on your phone and iPad after L3 |
| W9-A | C6 | The news check runs next to the installed system, so real news evidence builds up | Merged 2026-10-18 (#23); ships with Jev off: at L3 you add the news route to your network profile and proxy (step 3), and for Jev your `news-alarm.json` with `calls_enabled` and your `typesafe.key`; the Jev price is re-checked at L2, because today's price card goes stale on 2026-11-15; after L3 the shadow log fills and you decide on news wake-ups at step 17 ([L3 checklist](L3-INSTALL-CHECKLIST.md)) |
| W10-A | C7 | The files that record AI spending are locked, and Perplexity's spending gets the same protected checks as Jev's | Merged 2026-10-18 (#25); nothing new for you: the protected package fingerprint changes, which the normal re-pin at L3 steps 2 and 3 covers; Perplexity rounds (step 12) now also check the protected runtime the installed service prepares at start, and a missing or altered spending record refuses the next round with the reason in HQ's activity log |
| W10-B | C8 | Money checks, HQ and the scorecard stay fast as weeks of prices pile up | Merged 2026-10-18 (#26); nothing new for you: it is part of the normal install at L3, with no new database step; the first real scorecard after L3 (`trade-graph scorecard --format text`) uses it |
| W10-C | C9 | HQ shows a "Needs you" card when a bought coin is left without a safety exit | Merged 2026-10-18 (#24); after L3 you look at the HQ tab once on your phone and iPad (it was checked only in a test browser with made-up numbers); the card appears only if a bought coin has had no safety exit for 10 minutes |
| W11-A | C10 | Software labels each coin's market type (rising, sideways, falling), and practice reports show where a strategy earns and loses | Planned 2026-10-20 (D280) |
| W11-B | C11 | The Trading desk writes, replays and confirms its own strategies inside the installed system | Planned 2026-10-20 (D280) |
| W12-A | C12 | One Trading desk: chooses per coin to wait, buy dips or follow the trend, keeps a playbook, reads the Leader's news and Jev | Planned 2026-10-20 (D280); after wave 11 |
| W13-A | C13 | HQ's Trading desk room shows the playbook, today's choices and the lab; the scorecard splits results by strategy and market type | Planned 2026-10-20 (D280); after wave 12 |
| L1 | — | Price data kept current on your PC | Done 2026-10-19 ([L1.md](../planning/handoffs/L1.md)): April–September for every coin, Bitcoin and Ether without a break to 19 October, Windows price tasks registered; a later local session added two years for the five practice coins (2026-10-20) |
| L2 | — | First real practice-range reports and paid-service smoke tests | Part 1 done 2026-10-19 ([L2.md](../planning/handoffs/L2.md)): both starting strategies lose after fees; the coin scan, recordings, fee pages and the paid smoke calls remain |
| L3 | — | New engine installed on your PC (your approval) | Not started; waits for waves 11 to 13 (D280), then starts with your yes; every command in order is in [L3-INSTALL-CHECKLIST.md](L3-INSTALL-CHECKLIST.md) |
| L4 | T43 | Scored practice test, at least 4 weeks and 30 trades | Not started |

### Only you can do these

| When | What | Why |
|---|---|---|
| Now | Switch on the price schedule on your PC: run `scripts/register_candle_schedule.ps1` once (the local session gives you the command) | Windows blocked the agent. Without it, gaps appear in recent prices. Since 15 October 23:00 UTC none were collected. |
| Done 2026-10-18 | You accepted the three recommendations of the 2026-10-18 briefing: start L1 now, run wave 10 before the install, and import the April–June archive (D263). You also asked that nothing runs Trade Graph without telling you first | Recorded in the decision log. |
| Done 2026-10-20 | You chose one Trading desk with its own strategy lab, market types and news in its strategy choice, built before the install (D280) | Recorded in the decision log; waves 11 to 13. |
| Now | Check that Perplexity auto top-up is off | The prepaid credit is the outer limit while testing has no budget (D125). |
| Optional | Get a Jev key at console.typesafe.ai only, then ask the local session to store it | The news alarm makes real checks only with a key. It is built and tested without one. |
| Done 2026-10-18 | You answered the three open questions with the recommended answers: the older Leader keeps its power to pause the account or narrow the allowed coins and strategies (D262); the files that record AI spending become protected in a later package (D261); the fix for an order whose confirmation got lost will also apply to real-money orders, only through the live pilot (D260) | Recorded in the decision log; nothing changed in code. |
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
skipped. The night integrations ran it as GitHub's test job: wave 2 at
`4c134c0`, 6,929 passed, 0 failed, 18 skipped; wave 3 at `ce17fcb`, 7,084
passed, 0 failed, 18 skipped; wave 4 at `b09cae4`, 7,189 passed, 0 failed, 18
skipped; wave 5 at `e1008e5`, 7,347 passed, 0 failed, 18 skipped; wave 6 at
`0595d98`, 7,600 passed, 0 failed, 18 skipped; wave 7 at `3008446`, 7,665
passed, 0 failed, 18 skipped; wave 8 at `f90bda1`, 7,915 passed, 0 failed, 18
skipped; wave 9 at `3a6b298`, 8,016 passed, 0 failed, 18 skipped; wave 10 at
`b24422e`, 8,112 passed, 0 failed, 18 skipped. Any failure now counts and must
be reported. The container run took 105 minutes (W1-E measured 54); GitHub's
test job takes 50 to 80 minutes and may run 120.

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

The owner's decisions of 2026-10-18 are D260–D263 and of 2026-10-20 D280. New
owner decisions after this plan start at D310. Alembic has one head, 0044;
reserved migration numbers that were never used (0043, 0045–0049) stay unused,
and the integrator renumbers a new migration to follow the head if the
migration code requires it (rule 11).

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
| W8-A | `claude/c3-perplexity-in-service` | D240–D244 | 0043 |
| W8-B | `claude/c4-trader-exit-tools` | D245–D249 | 0044 |
| W8-C | `claude/c5-hq-loss-plans-scorecard` | D250–D254 | 0045 |
| W9-A | `claude/c6-news-poll-in-service` | D255–D259 | 0046 |
| W10-A | `claude/c7-lock-ai-spending` | D264–D268 | 0047 |
| W10-B | `claude/c8-fast-money-checks` | D269–D273 | 0048 |
| W10-C | `claude/c9-hq-unprotected-card` | D274–D278 | 0049 |
| W11-A | `claude/c10-market-types` | D285–D289 | 0050 |
| W11-B | `claude/c11-strategy-lab` | D290–D294 | 0051 |
| W12-A | `claude/c12-trading-desk` | D295–D299 | 0052 |
| W13-A | `claude/c13-desk-hq-scorecard` | D300–D304 | 0053 |

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

### Wave 8 — follow-up before the install (added 2026-10-17)

The night shift found gaps between "built" and "installed"
(`planning/handoffs/NIGHT-2026-10-16.md`, "What comes next";
[L3-INSTALL-CHECKLIST.md](L3-INSTALL-CHECKLIST.md), "Perplexity in the
installed service"). Waves 8 and 9 close them. Each package reads the
handoffs it names before it starts.

Order for the integrator: W8-B, W8-C, W8-A.

**W8-A · C3 · Perplexity inside the installed service.** Research's focus
step and the Leader's news rounds can run on Perplexity in the installed
subscription service, once the owner switches them on at L3 step 12.
- Spec: L3-INSTALL-CHECKLIST.md "Perplexity in the installed service:
  prerequisites of step 12", items 1 to 5; the W6-A, W6-B and W6-D handoffs;
  D121, D124, D125, D154, D205–D214, D220–D224.
- Assemble W6-A's `FocusResearch` and W6-B's `LeaderNewsRounds` inside the
  protected process boundary with a paid-enabled `ModelGateway`, controlled by
  the owner switch (`perplexity_enabled_for`, `perplexity_setting`). With the
  switch off (the default), nothing changes: both departments stay on the
  subscription, and `run_subscription_operation` keeps refusing any other
  separately billed configuration.
- A separately reviewed network route: the owner's protected network profile
  gains an explicit, pinned list of reviewed hosts for paid research, kept
  apart from the market proxy's `MARKET_HOSTS` (Kraken and Frankfurter).
  Nothing reaches a host the owner has not listed. Design it so W9-A adds the
  news-feed and TypeSafe hosts as a second reviewed list the same way, and
  document it.
- A protected route for the registered `web_search` tool (W1-C: the kernel
  department gateway refuses `tools` in a department's context), so every
  search result is still stored before a finding may cite it.
- The Perplexity key is read only from the owner's private file inside the
  image (D154), never from Git, chat, the environment or a model's context.
- Seeded, dated price cards with their source. Re-check Perplexity's official
  pricing if the cloud can reach it; otherwise keep the 2026-10-12 card, which
  goes stale on 2026-11-11, and list the re-check for step L2. A stale card
  refuses paid calls; it never prices them as free.
- The deployment's EUR allowance must not block D125's "no money limit while
  testing". The owner's monthly limit (D138 item 4), once set, pauses only
  these rounds; the Trader, protection and records continue.
- Fix, test first: a repeated provider request id raises `IntegrityError` out
  of `BudgetGateway.commit` and leaves the reservation `RESERVED` (found by
  W1-C). Every call, retry and failure still ends with a receipt or an
  unresolved record.
- Recommended default, record it: add `application/price_collector.py` and
  `application/service_market_data.py` to `PROTECTED_PATH_PREFIXES`. They
  feed the coin rule and the price alarms, so the deployed Engineer should not
  edit them (W6-D left this open).
- Update the L3 checklist: step 12's prerequisites become concrete owner
  actions (which hosts to review, where the key file goes) or are marked done.
- Done when a scripted day in the subscription operation, with the switch on,
  runs Research's focus step and one Leader news round through a scripted
  Perplexity transport, with receipts and stored sources; with the switch off
  the same day runs as before. No real call.

**W8-B · C4 · Trader exit tools and order reconcile.** The Trader can close a
day-plan position early or move its safety exit.
- Spec: the W4-A and W5-A handoffs ("Not done"), D115–D117, D185–D194.
- A Trader action in its wake reply: close a day-plan position, or move its
  safety exit. Software cancels the exit, reconciles, then sells or attaches
  the new exit, as one recorded sequence. An open position is never left
  without a safety exit, except during the sell itself; a refused or unknown
  step leaves the old exit in place. Property tests over fills, partial
  fills, cancels and restarts.
- Within the owner's limits and the spot-only rules. No confidence threshold,
  committee or approval.
- Reconcile fix (W4-A's finding, W5-A's proposed fix), test first: when the
  broker answers `open` or `partially_filled` and the recorded fills equal its
  filled quantity, move an `UNKNOWN` intent to `OPEN` or `PARTIALLY_FILLED`
  with its venue order id, in paper. Live behaviour does not change; record
  the live question as an owner question in the handoff.
- An index on `valuation_marks (portfolio_id, asset, observed_at)` (W5-C's
  finding), in this package's migration; existing databases upgrade.
- After any contract change, run `tests/integration/test_provider_wire_schemas.py`:
  a new field in the Trader's reply can break the strict reply schema (wave
  3's lesson).
- Tell the Trader about the new action in its wake brief and in
  `docs/EVENT-ENGINE.md`.

**W8-C · C5 · HQ: today's loss, the day plans and the scorecard.**
- Spec: the W3-A, W4-A, W5-B, W5-C, W6-D and W2-C handoffs;
  `docs/HQ-DASHBOARD.md` (plain English, phone and iPad first, light and dark,
  the 80 KB payload budget). Reading HQ starts nothing.
- Today's loss against the 3% limit (W3-A's `LossStatus.document()`), and a
  money-limit failure in plain words (today it shows as "a routine step is
  failing").
- Today's day plans per coin: entries, safety exits, fills, refusals and plan
  events (W4-A).
- The scorecard (W5-C's `compute_scorecard`) with caching like HQ's money
  facts.
- A small price-collector panel from the `price_collection` activity (W6-D).
- Long-lived caching for the vendored chart script under `/static/vendor/`
  (W5-B: it is fetched again on every page load).
- Recommended default for W2-C's question, record it: reword the older "Pays
  for itself?" box so it never suggests practice gains pay real bills
  (AGENTS.md: paper equity cannot replenish the real budget).
- A new HQ key goes into `api/hq_contract.py` and all nine
  `tests/fixtures/hq/snapshot_*.json`, keeping the keys other packages added.

### Wave 9 — after wave 8 is merged

**W9-A · C6 · News check next to the installed service.** The 5-minute news
check runs with the installed system, so the shadow log fills with real
evidence after L3. Step 17 needs that evidence.
- Spec: L3-INSTALL-CHECKLIST.md "News wake-ups: what step 17 needs";
  `docs/NEWS-ALARM.md`; the W1-A and W7-A handoffs; D137, D138 item 10,
  D140–D144, D225–D229; W8-A's reviewed-route design.
- Run T44's poll every 5 minutes inside the service (a thread like the price
  collector) or as a companion the owner launcher starts, writing into the
  installation's state database.
- Add the five feed hosts and TypeSafe as a second reviewed host list, using
  W8-A's route mechanism.
- The Jev key is read from the owner's private file. Jev calls stay off until
  the owner sets `jev.calls_enabled`. Bind Jev checks to protected budget
  origins, and decide whether the runtime evidence collector reads
  `news_jev_attempts` (open since the wave 1 integration).
- A lost feed is reported, never treated as "no news". Headlines stay data.
- Update the L3 checklist's step 17 and "News wake-ups".
- Done when a scripted service day polls scripted feeds every 5 minutes,
  records headlines and Jev answers with receipts, and the engine records
  shadow routes; with Jev off, headlines are stored and nothing is called.

### Wave 10 — tidy-up before the install (added 2026-10-18)

The owner chose (D263) to close three items from the night report for waves
8 and 9 (`planning/handoffs/NIGHT-2026-10-17.md`, "Left for later") before
L3, so the engine is installed once: D261's protected spending files, the
growing cost of reading every price mark, and a warning when a bought coin
has no safety exit. The three packages touch different files and run side by
side. Wave 10 has no task in `planning/progress.json` (C ids are labels); its
only dependency is wave 9, which is merged. Each package reads the handoffs it
names before it starts.

Order for the integrator: W10-B, W10-C, W10-A.

**W10-A · C7 · Lock the AI-spending records.** The files that record every
paid AI call become protected, and Perplexity's reservations get the same
protected checks as Jev's.
- Spec: D261, D243, D244, D258, D259; the W8-A and W9-A handoffs (W9-A's
  "W8-A's Perplexity route could adopt the same protected budget origins");
  `kernel/authority.py`, `kernel/protected_budget.py`,
  `application/news_installation.py` (`NewsRouteBudget`),
  `application/perplexity_installation.py` (`PerplexityRouteBudget`).
- Add `application/budget.py` and `application/gateway.py` to
  `PROTECTED_PATH_PREFIXES` with a comment, and a parametrized test like
  `tests/unit/test_news_installation_paths.py` (D261). Afterwards
  `kernel/protected_budget.py` imports only protected code.
- Give `PerplexityRouteBudget` the protected budget origins that W9-A gave
  Jev: the origin recorded in the reserve transaction, validated at dispatch,
  settlement, commit and allocate, a bounded audit each round, scoped by
  `ProtectedBudgetOrigins(protected.financial, deployment_id=...)`. A
  reservation whose origin fails validation is refused or left unresolved,
  never treated as free.
- Do not change D125 (no money limit while testing) or the owner's monthly
  limit (D243): it still pauses only the Perplexity rounds.
- Fix, test first: a missing or forged origin on the Perplexity route is
  refused and recorded, mirroring the news route's origin tests.
- The protected package hash changes; the normal L3 re-pin (steps 2 and 3)
  covers it. Update the L3 checklist only if an owner step changes.
- Done when both files are protected with a test, and a scripted Perplexity
  round in the installed service reserves with a protected origin, passes
  the audit and ends receipted, while a tampered origin is refused. No real
  call.

**W10-B · C8 · Money checks and the scorecard stay fast.** As weeks of price
marks pile up during the scored test, the money-limit step, HQ and the
scorecard keep their speed and give the same results.
- Spec: the night report's "Left for later" (`Ledger._marks`, a scorecard
  refresh of about 3.5 s on four weeks of prices); the W5-C, W8-B and W8-C
  handoffs; `docs/SCORECARD.md`; D200–D204, D245–D254.
- `Ledger._marks` (`application/ledger.py`) reads every valuation mark of the
  portfolio, with no asset filter and no limit, and `kernel/books.py`
  `_latest_mark` searches that list for each open lot. Read only the newest
  mark per held asset as of the time (`... WHERE portfolio_id=? AND asset=?
  AND observed_at<=? ORDER BY observed_at DESC, rowid ASC LIMIT 1`, as
  `application/scorecard.py` already does), so migration 0044's
  `valuation_marks_newest` index is used. Do the same for `_rates` if it reads
  every FX rate. Callers include the money-limit step (every tick), HQ's
  today's loss, the Trader workflow and the forward producer.
- Results do not change: a Hypothesis property test that the old and new
  reads agree, tied timestamps included, and an EXPLAIN QUERY PLAN test that
  the index is used. `application/money_limits.py` breaks ties with
  `rowid DESC`; align it with the ledger and record the rule as a decision.
- Scorecard: `_curve` (`application/scorecard.py`) loads every mark of every
  coin held in the period. Read only what it needs, or update HQ's cache
  incrementally; `tests/property/test_scorecard_properties.py` must still
  pass unchanged.
- Measure before and after on a synthetic four-week portfolio and record it
  in the handoff. Assert on rows read or queries made, never on seconds
  (waves 8 and 9 lost time to tests that depended on machine speed).
- Use migration 0048 only if a new index is really needed.
- Done when the money-limit step, HQ's today's loss and the scorecard give
  identical results on old and new code with a bounded number of rows read.

**W10-C · C9 · HQ asks you when a coin has no safety exit.** A "Needs you"
card appears when a filled day-plan buy has stayed without an active safety
exit.
- Spec: `docs/HQ-DASHBOARD.md` (plain English, phone and iPad first, light
  and dark, the 80 KB payload budget); the W4-A, W8-B and W8-C handoffs;
  D115–D117, D185–D189, D245–D254. HQ's attention cards:
  `api/hq_state.py` `attention`, with `api/hq_spending.py` as the pattern
  for an extra card.
- The card shows which coin, how much, since when and what the system is
  doing about it. Measure the time from the fill or the first
  `protection_deferred` event. Recommended default, record it: show the card
  after 10 minutes without full cover. Use one shared coverage function with
  `application/day_plan_protection.py` and `api/hq_plans.py`, not a third
  copy of the arithmetic. Reading HQ starts nothing, and the card has no
  button that trades.
- A new `AttentionCode` with its severity and copy. If the snapshot gains a
  key, update `api/hq_contract.py` and all nine
  `tests/fixtures/hq/snapshot_*.json`, keeping the keys other packages added.
- Small fix, test first: `news-alarm-poll` and `news-alarm-report` print a
  traceback on a malformed number in `news-alarm.json`
  (`decimal.InvalidOperation` escapes `_settings` in
  `market_data_cli/news_alarm.py`). They must refuse with a clear message,
  like the in-service reader.
- Done when a scripted day whose exit placement is refused shows the card
  after the delay and clears it once the exit is in place or the position is
  closed; JS tests, and Chromium (Playwright) at phone, iPad and desktop sizes
  in light and dark.

### Wave 11 — the strategy lab and market types (added 2026-10-20)

The owner chose (D280) one Trading desk that finds, tests and chooses its own
strategies. L2 part 1 (`planning/handoffs/L2.md`) found four gaps: both
starting strategies lose after fees on six months of real prices (before fees
they roughly break even); no department can create or replay a strategy, so
only the owner's `practice-range-*` commands make the confirmation reports a
day plan needs (`application/day_plans.py`); no software labels market types,
although D137's text says it does (the scanner only ranks); and the L3
checklist has no step that gives any strategy a confirmation report in the
installed database, so as written the scored test would make no trade. Waves
11 to 13 close these gaps; L3 waits for them. The cloud has no market data and
cannot reach kraken.com or okx.com: tests use synthetic candle series labelled
`synthetic_fixture`, and the local session reruns the lab on real data after
the merge. Waves 11 to 13 have no task in `planning/progress.json` (C ids are
labels). Each package reads the handoffs it names before it starts.

Order for the integrator: W11-A, W11-B. Both touch the practice range: W11-A
the grammar, the replay and the report (`contracts/practice_range.py`,
`replay.py`, `summary.py`); W11-B the store, the paths and new modules. Keep
W11-B's contracts in their own file (`contracts/strategy_lab.py`).

**W11-A · C10 · Market types.** Software labels each coin's market type, the
same way live and in replays, and practice reports show where a strategy earns
and where it loses.
- Spec: D280, D118 (TA-Lib menu only, no in-house formulas), D119, D120, D132,
  D136, D165–D169, D170–D174; `planning/handoffs/L2.md`; `docs/PRACTICE-RANGE.md`,
  `docs/COIN-SCANNER.md`; `contracts/indicators.py` (ADX, PLUS_DI and MINUS_DI
  are on the menu), `application/indicators/rolling.py`,
  `application/coin_scanner/` (`inputs.py` `evaluate_pair`, `scanner.py`,
  `store.py`).
- One deterministic function, in a new protected kernel module, labels a coin
  at a moment as `rising`, `sideways`, `falling` or `unclear` (too few candles,
  a gap) from completed candles only, for example from a long moving average's
  slope, the price's side of it and ADX for trend strength. The rules are a
  versioned document (`market-type-rules@1`) with bounded parameters and the
  candle size they read; record the defaults as decisions, with the reason.
  Live labelling and replays call the same function with the same rules, never
  a second copy (like `kernel/day_plan_coverage.py`, D277). Point in time: a
  label never uses a candle that closed after its moment. Comparisons follow
  D136.
- Live: each hourly scan labels every shortlist coin, every coin held and the
  day's focus coins, and stores the label with its inputs and rules version
  (migration 0050 only if a new table is needed). A read-only command,
  `trade-graph market-types`, prints the newest labels. The desk does not see
  them yet (W12-A).
- Practice range: (1) an optional grammar operand, so a strategy can require a
  market type at entry (for example `{"market_type": "rising"}` with `is` or
  `is_not`), computed in the replay by the same function; (2) every report
  splits its trades by the market type at entry: count, wins, losses and
  result after fees, summing to the report's totals. Existing strategy
  documents, reports and their hashes stay valid.
- Done when golden tests on synthetic series (a steady rise, a steady fall, a
  flat range, a gap) give the expected labels from the expected first candle;
  a property test shows a label at a moment never changes when later candles
  are added; a replay using the operand sees the same label sequence as the
  live function on the same candles; and a report's split sums to its totals.

**W11-B · C11 · The strategy lab inside the service.** The Trading desk writes
strategy versions, replays and confirms them inside the installed system, and
is woken with the results; protected software keeps the test fair.
- Spec: D280, D116, D117, D138 item 5, D165–D169, D185–D194, D220–D224 (the
  in-service collector's thread); `planning/handoffs/L2.md` ("Finding for
  L3"); `docs/PRACTICE-RANGE.md`, `docs/EVENT-ENGINE.md`, `docs/DAY-PLANS.md`;
  `application/practice_range/` (`store.py` `register`, `create_study`,
  `record_run`; `service.py` `PracticeRange.run`), `application/day_plans.py`,
  `application/trader_workflow.py`, `application/events/` (`engine.py`,
  `sources.py`), `contracts/wakes.py` (`TraderWakeReply`, `TRIGGER_KINDS`,
  `_TRIGGER_NEEDS`, `purpose_of`), `adapters/persistence/wake_schema.py`,
  `application/subscription_runtime.py` (`ROLE_CONTEXT_KEYS`).
- Lab requests (`contracts/strategy_lab.py`): register a strategy version (a
  `PracticeStrategy` document: data, never code), create a study, run tuning
  replays (coin, source, window, cost profiles) and ask for a confirmation.
  `TraderWakeReply` gains an optional `lab_requests` list. No cap on how many
  (D116). Each request is checked by the existing grammar and study rules; a
  refused one is reported back with its reason, never dropped silently.
- A lab worker in the installed service, in its own thread like the price
  collector: it runs queued requests in order against the installation's
  candle file (read-only) and writes practice-range records into the state
  database. It never places, sends or cancels an order and never delays
  trading, protection, reconciliation or the money-limit step. The queue
  survives a restart: an interrupted run is run again, never half recorded.
  HQ's activity log shows `strategy_lab` with what is queued, running, done or
  refused, and the seconds spent.
- When the desk's requests are done, the desk is woken by a new trigger kind,
  `lab_results` (`TRIGGER_KINDS`, `_TRIGGER_NEEDS`, `purpose_of`, a source in
  `application/events/sources.py`, and migration 0051 for the CHECK
  constraint in `wake_schema.py`); like a news alarm it keeps the desk's
  check-in. A new `lab` context key, allowed for the Trader in
  `ROLE_CONTEXT_KEYS`, gives the newest results in plain sentences
  (`summary.report_lines`, the result per cost profile, W11-A's split by market
  type), what is queued, and per strategy family how many versions and runs
  were tried, all within the 128 KiB request limit.
- A fair test, kept by protected software: add `application/practice_range/`,
  `contracts/practice_range.py` and the lab modules to
  `PROTECTED_PATH_PREFIXES` with a test, so the deployed Engineer cannot change
  the rules. Keep the store's rules (tune on earlier days, confirm once on
  untouched later days, every try listed in the report), and add a helper that
  offers the desk the newest untouched confirmation window per family, so new
  days from the collector become new confirmation days (walk-forward). The
  account's own cost profile is always among the profiles replayed.
- First start: an installation with no registered strategy registers the two
  examples in `strategies/practice/` (shipped in the image), so the lab has a
  start; the desk asks for their confirmations itself. Update the L3
  checklist: step 3 adds `lab` to `allowed_context_keys.trader`, and the
  "missing confirmation step" of `planning/handoffs/L2.md` becomes this
  first-start behaviour, with what the owner should see.
- Done when, on a scripted installation with synthetic candles: the desk's
  reply registers a version and asks for tuning and a confirmation; the worker
  runs them; the desk is woken with `lab_results`; its next day plan citing the
  confirmed version is accepted; a confirmation on spent days, a tuning run on
  confirmation days and a malformed strategy are refused with their reasons in
  the next wake; the queue survives a restart; and a safety exit fills while a
  long queue is running.

### Wave 12 — one Trading desk (added 2026-10-20)

**W12-A · C12 · One Trading desk.** The Trader becomes the Trading desk: it
owns strategy choice and strategy research, and decides per coin whether to
wait, buy dips or follow the trend, using market types, the lab and the news.
- Spec: D280, D115–D117, D123, D127, D137, D138, D185–D189, D205–D214,
  D225–D229, D255–D259; the W11-A and W11-B handoffs; `docs/LEADER-NEWS-ROUNDS.md`,
  `docs/NEWS-ALARM.md`, `docs/FOCUS-RESEARCH.md`; `contracts/wakes.py`
  (`TraderWakeReply`, `ShortlistCoin`), `application/events/scan.py`,
  `application/trader_workflow.py` (`WAKE_INSTRUCTION`, the context),
  `prompts/trader.md`, `application/subscription_runtime.py`,
  `contracts/leader_news.py` (`StrategyIdea`), `application/leader_news/rounds.py`
  (`_route`), `adapters/persistence/leader_news_schema.py`.
- The desk's job, in its instruction (`WAKE_INSTRUCTION`, `prompts/trader.md`),
  in plain terms: read each coin's market type, the lab results and the news;
  decide per coin to wait in cash, buy dips, follow the trend or something
  else; use the lab to find and confirm better strategies; fees decide most
  results (L2), so weigh how often to trade. Discretion stays real: no
  confidence threshold, no committee, no pass mark (D116, D138 item 5).
- Playbook: a reply may revise the desk's playbook: per market type, the
  strategy versions it means to use or "wait", each with a short reason and
  the report ids it rests on. Store revisions with who, when, why and the
  previous revision, readable at any past moment like lessons. Software never
  refuses a day plan for not following the playbook or a market type.
- Stances: a reply may give, per coin it considered, a stance (wait, buy dips,
  follow the trend or other), the market type it saw, the strategy version, the
  news it weighed and a one-sentence reason. A day plan records the coin's
  market type and the playbook revision when it is installed, for Learning
  and the scorecard (W13-A).
- News: the desk's context gains `news`: the newest Leader news round (summary,
  headlines, the desk's note and the strategy ideas addressed to it) and Jev's
  recent verdicts and would-be alarms for the coins it watches, shadow mode
  included, within the size limit. `StrategyIdea.department` gains the desk
  (contract and the CHECK in `leader_news_schema.py`, migration 0052); an idea
  for the desk appears in its next wake as a lab inbox item, and the desk
  decides what to test. Optimisation keeps cost and quality.
- Market types: each shortlist, focus and held coin in the desk's context
  carries W11-A's label (`ShortlistCoin`, `events/scan.py`).
- The new context keys are allowed for the Trader in `ROLE_CONTEXT_KEYS`; L3
  checklist step 3 lists them for `subscription-profile.json`. A busy-day test
  keeps every request under 128 KiB.
- Done when a scripted week (synthetic candles, scripted Leader rounds and Jev
  verdicts, scripted desk replies, no paid call) shows: a coin labelled
  `falling` gets a "wait" stance; a Leader idea addressed to the desk becomes a
  lab run; a confirmed dip-buying version is used on a `sideways` coin; the day
  plan records its market type and playbook revision; and every request stays
  under the size limit.

### Wave 13 — the Trading desk in HQ (added 2026-10-20)

**W13-A · C13 · The Trading desk room and the scorecard by strategy.** You see
what the desk believes, what it is testing and what each strategy earns in
each market type.
- Spec: D280, `docs/HQ-DASHBOARD.md` (plain English, phone and iPad first,
  light and dark, the 80 KB payload budget), `docs/SCORECARD.md`; the W11-A,
  W11-B and W12-A handoffs; `api/hq_copy.py`, `api/hq_state.py`,
  `api/hq_contract.py`, `web/static/hq-render.js`, `kernel/trade_metrics.py`,
  `application/scorecard.py`.
- HQ's Trading desk room shows, in plain words: the playbook (per market type,
  the strategy or "wait", and why), today's stance per coin with its market
  type, the lab (queued, the newest results with the result after fees against
  holding, tries per family) and the Leader news the desk read. Reading HQ
  starts nothing. Update the snapshot contract and the fixtures, keeping other
  packages' keys.
- The scorecard splits its trades and result after fees by strategy version,
  market type at entry and stance, with the same sums (`kernel/trade_metrics.py`),
  and Learning can read the split, so lessons can say which choice earned or
  lost in which market.
- Done when JS tests and Chromium (Playwright) at phone, iPad and desktop
  sizes in light and dark show the room on a scripted week, within the payload
  budget, and a property test shows the scorecard's split sums to its totals.

### Later, not packaged

Open items the night shift found that wait for an owner decision or a later
package: the suite's speed (about half of it is `runtime_environment_sha256()`
re-hashing the standard library, which is protected engineering code); whole
lots in practice-range replays (changes replay results); hourly scans widening
the day's shortlist; Perplexity departments outside the AI allowance floor;
how long hourly scans are kept (about 2 MB a day); money limits on the live
path (live trading is out of scope); a partial close; HQ's wake log naming
the Trader's position actions; the runtime evidence collector's 10,000-row
bound filling with Jev records in about 50 days. Decided by the owner and
waiting for a package: the live pilot (T19) applies D248's settlement of a
lost acknowledgement to live orders through its own lifecycle (D260). D261
(protected spending files) was built by W10-A (merged 2026-10-18).

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
(from 13 October) don't overlap yet. The owner approved downloading and
importing the 2026Q2 archive (538 MB) on 2026-10-18 (D263), which gives the
practice range six months of history instead of three. Tell the owner before
running any `trade-graph` command (D263).

**L2 · Real-data checks (after W2-A and W1-C are merged).** Practice-range
reports for the two seed strategies on the real imported history, under all
three fee profiles. Record the real OKX and news-feed fixtures if the cloud
used synthetic ones. One opt-in Perplexity smoke call with the test key
(D125), with its receipt. A Jev smoke call only if the owner has installed a
key. Each result is labelled real or synthetic.

**L3 · Install the new engine (after waves 1–10 are merged; waves 8 and 9
close the gaps of its steps 12 and 17, and the owner chose to wait for wave
10, D263).** Each step only on
the owner's yes: full suite; build the protected image and re-pin the kernel
manifest; back up; install through the financial-manifest transition with the
paper account in MANAGE_ONLY (docs/FINANCIAL-TRANSITION.md); run
`trade-graph mandate-cleanup`; record the D138 policy revision (W3-A's
command); switch Research and the Leader to Perplexity (W6-A, W6-B); confirm
the new collector runs, then remove the Windows scheduled tasks; resume.
The commands, in order, with what each should show, are in
[L3-INSTALL-CHECKLIST.md](L3-INSTALL-CHECKLIST.md) (W6-D). W8-A and W9-A are
merged, so its step 12 (Research and the Leader on Perplexity) and its step 17
(news wake-ups, once the shadow log shows real alarms) no longer wait for a
cloud package; wave 10 is merged too (2026-10-18). The owner then chose
(D280) to build the Trading desk first, so L3 waits for waves 11 to 13; they
also replace the confirmation step that L2 part 1 found missing.

**L4 · T43 · Scored practice test.** Run for at least four weeks and at least
30 completed trades (D138 item 7). Weekly owner check-ins in HQ. The final
report: scorecard, wakes by reason, AI spending per department, incidents and
what to tune next. A good result does not authorize real money.
