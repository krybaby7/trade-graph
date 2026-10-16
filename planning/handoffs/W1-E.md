# W1-E C1: Green checks

For the owner: The automatic checks now pass cleanly on this branch: the 13
tests that always failed, the tests that failed only on a slow or busy
computer, and one test that failed only on GitHub are fixed, and Mission
Control's progress page shows all 46 tasks again. GitHub's own test run had
been cancelled every time since 9 October because the suite outgrew its
45-minute limit, so no change got a clear pass or fail; the limit is now 120
minutes. Nothing is needed from you for this package; the integrator has one
new routine step (refreshing the progress snapshot after updating the plan).

Branch: `claude/youthful-hawking-hbzl8v` (this session's own branch, used
instead of `claude/c1-green-checks` as the session prompt allows), based on
`435db71`. Pull request: see the session's final message.

## What was built

Each known failure, its root cause and the fix. Commits are on the branch in
this order.

- **Mission Control progress page** (3 `test_progress_dashboard`, 4
  `test_progress_projection`). `src/trade_graph/api/progress.py` refused
  `planning/progress.json` because it did not know the `todo` status and only
  accepted a bare date for `updated_at`, so the page showed no project data at
  all. It now accepts `todo` and either a date or a full UTC timestamp, and
  still refuses unknown statuses and impossible dates. The packaged
  `src/trade_graph/progress_catalog.json` (used by installed copies) stopped at
  T22; it is regenerated with T00–T45 by the new
  `scripts/update_progress_catalog.py`, which uses the page's own
  normalisation. The tests now count tasks from the planning files instead of
  a fixed "17 of 23", so they keep working as the integrator marks tasks done.
  The signed-out `/progress` check expected 401, but since 2026-10-09
  (`e4f7244`) browser pages deliberately redirect signed-out readers to
  `/login` (303), which `test_live_home` already covers; the test now checks
  the redirect and that no page content leaks. `docs/MISSION-CONTROL.md` says
  how to refresh the snapshot.
- **Overview degraded flag** (`e2e/test_dashboard_loop`, also offline
  acceptance case A34). The 2026-10-09 live overview (`e4f7244`) stopped
  copying the health check's `degraded` and `degraded_reasons` to the top
  level, so the journal page's "Degraded operations" notice could never
  appear. `src/trade_graph/api/live.py` copies both again. A new
  `test_live_home` test shows the notice with an unknown-status order and its
  absence when healthy.
- **Paper soak CLI tests** (4 `test_paper_soak`). Since 2026-10-08
  (`f2a6de6`) a closed `Database` refuses new connections. The soak CLI
  correctly closes the runtime database it was given; the tests then read
  receipts through that closed handle. They now open the same file again,
  the pattern other tests use, which also shows the records reached disk.
- **Subscription smoke cycle limits**
  (`test_subscription_smoke::test_cycle_runs_only_fresh_research_and_trader_through_protected_handlers`).
  On 2026-10-07 (`80cdad6`) the normal protected handlers started applying the
  subscription profile's own limits (600 s, 16,384 tokens by default) instead
  of a fixed 120 s and 4,096 tokens, and the matching department test was
  updated. The smoke test, written 30 minutes earlier, was not. The cycle runs
  through those normal handlers, so its calls must now equal the configured
  limits exactly; the diagnostic call keeps its own 120 s / 4,096 cap.
- **Timing cases under load.** Each one ran a real child process under a
  short wall clock that was not the subject of the test. Fixed by giving that
  child room, while the limits themselves stay tested elsewhere:
  - `test_plugin_staging` resource cases: after each hostile plugin the same
    validator must pass a good one, under 0.3 s. They now use the default
    policy, the 2 s production uses. Hostile plugins are still rejected.
  - `test_engineer_isolation::test_checker_wall_and_output_limits_are_enforced`:
    the output-flood case shared the sleep case's 0.3 s, so a slow child start
    hit the wall clock before the output cap. The sleep case keeps 0.3 s; the
    output case gets the default 10 s, so only the output cap can stop it.
  - `test_financial_recovery`, `test_financial_transition`,
    `test_financial_recovery_history`: the protected Ledger helper (about
    0.3 s alone) is allowed 2 s inside a 5 s scan, and under load it sometimes
    took longer ("owner-pinned complete native Ledger verification refused").
    A fixture gives the helper 30 s and the scan 60 s in these three files. The
    helper still runs and must still agree with the ledger;
    `test_financial_checkpoint` keeps testing the deadline itself.
- **Failures only on GitHub.**
  - `tests/security/test_subscription_egress_boundary.py::test_fixed_provider_proxy_is_the_only_child_egress_setting`
    failed with `ValueError: Linux bubblewrap is unavailable; isolation fails
    closed` on GitHub's hosted runner (run `37818575185`, 2026-10-08) and,
    identically, on the untouched base in this cloud container. It is not
    container-only: any Linux host without bubblewrap fails it. The test only
    inspects the command the boundary composes (the proxy setting) and never
    runs it. It now stands in a root-owned binary for `bwrap`; no isolation
    code changed and no skip was added. A new test shows that a host without
    bubblewrap still fails closed. The tests that really run bubblewrap keep
    the skip they already had. If the integrator prefers installing
    bubblewrap on the runner instead, drop commit `6ac70f4` (see the
    bubblewrap note at the end).
  - `.github/workflows/test.yml`: the job limit was 45 minutes. The suite now
    has about 6,300 tests and took 54 minutes here; every GitHub run from
    2026-10-09 on was cancelled mid-suite (it looked like a hang at 47–53%,
    but it is the slow plugin tests below). The limit is now 120 minutes.
    This is the only workflow change: one number, no new software and no
    change to what the steps run. GitHub accepted the push.
- **Cloud container only:** none found. With Python 3.12 the full suite
  passes in this container; the one host-dependent failure (bubblewrap) also
  failed on GitHub's runner.

## Decisions (reserved block D160–D164)

- **D160 (2026-10-16, implementation default for C1):** Mission Control
  accepts the planning statuses `todo`, `in_progress`, `blocked` and `done`
  (plus the older `pending` and `not_started`) and an `updated_at` that is a
  date or a full UTC timestamp. The packaged snapshot is generated, not
  hand-edited: run `uv run python scripts/update_progress_catalog.py` after
  every change to the planning files, and
  `test_packaged_catalog_matches_current_planning_without_private_metadata`
  fails until it is run. Tests derive task counts from the planning files.
  Three hand-written evidence notes in the old snapshot (T17, T19, T21) were
  never shown, because the page uses the notes in `progress.py`; the
  regenerated snapshot carries the shown ones.
- **D161 (2026-10-16, implementation default for C1):** The overview again
  carries `degraded` and `degraded_reasons` copied from the health check, as
  before 2026-10-09. Acceptance case A34 requires visible degraded states.
- **D162 (2026-10-16, test policy for C1):** A test whose subject is not a
  time limit gives real child processes at least the production wall clock
  (or more, for the Ledger helper), so a slow or busy machine cannot turn it
  red. Each limit keeps its own deterministic test: the 0.3 s checker sleep
  case, the plugin parent-deadline case and `MAXIMUM_SECONDS = 0` in
  `test_financial_checkpoint`. No production limit was changed.
- **D163 (2026-10-16, implementation default for C1):** The GitHub test job
  may run 120 minutes. Revisit if the suite is made faster (see below).
- **D164 (2026-10-16, implementation default for C1):** The subscription
  smoke cycle is checked against the profile's configured limits, following
  `80cdad6`; only the diagnostic call has the smaller fixed cap.

## Evidence

- Tests written or changed first, each seen failing on the old code:
  - progress tests: 10 failed with the old `progress.py`
    (`assert None == ...`, `todo` rejected), pass now.
  - `test_live_home::test_journal_and_overview_flag_degraded_operations`:
    failed without the `live.py` change (`KeyError: 'degraded'`), passes now.
  - `test_missing_bubblewrap_fails_closed_instead_of_running_unconfined`: new;
    the old egress test failed here with "Linux bubblewrap is unavailable".
  - Timing: a pytest plugin (not committed) delayed the relevant child's
    start to imitate a slow machine. With the Ledger helper 2.2 s late, all 89
    tests in the three financial files failed on the base and all pass now.
    With the plugin or checker child 0.5 s late, all 6 plugin resource cases
    and the checker output case failed on the base and pass now.
- `uv run ruff check src tests` (and the CI lint command with its scripts):
  all checks passed.
- Related files: progress, dashboard, live home, paper soak, subscription
  smoke and departments, plugin staging, engineer isolation, the three
  financial files and the egress test all pass.
- `python3 scripts/check_plan.py`: plan valid, 46 tasks.
  `python3 scripts/test_planning.py`: 11 tests OK.
  `python3 scripts/check_repository_hygiene.py`: passed.
- Full suite `uv run pytest -p no:cacheprovider -rf` (with CI's
  `TRADE_GRAPH_PAID_CALLS_ENABLED=false` and `TRADE_GRAPH_LIVE_ENABLED=false`):
  - at `683ae10` (first five fixes), 54 min: 6295 passed, 1 failed, 23
    skipped. The one failure was the bubblewrap egress test, fixed later in
    `6ac70f4`. None of the 20 known failures appeared.
  - at `f46ff23` (all code changes), 54 min, in a clean worktree: **6297
    passed, 0 failed, 23 skipped**. Compared with the known list: every known
    failure is gone and nothing new failed.
  - Both runs used CPython 3.12.13 installed with `uv python install 3.12`
    and `uv sync --frozen --group dev --python 3.12`, as CI uses 3.12. The
    container's default `python3` is 3.13; this session did not see the
    symlinked `_sysconfigdata` refusal that W1-B reported.
- CI's offline acceptance step (`scripts/verify_offline_acceptance.py`): on the
  base `435db71`, 463 of 464 passed and case A34 failed (the dashboard loop).
  At `f46ff23`: 464 of 464 passed, acceptance gate complete, and the CI
  artifact hygiene check on the JUnit file and the report passed.
- GitHub runs checked: `38896052018` (base `435db71`, today) and
  `37941520429` (2026-10-09) were cancelled at 45 minutes at 47% and 53%;
  `37818575185` (2026-10-08, 3,520 tests) finished in 34 minutes with 8
  failures, including the bubblewrap test.
- Real or synthetic: everything here is offline and synthetic. No paid call,
  exchange call or order was made; the only network use was installing the
  locked packages and reading this repository's public GitHub Actions logs.

## Schema and dependencies

- State migration: none. Candle database: none.
- Dependencies added: none.

## Not done, open or for someone else

- **Integrator:** after updating `planning/progress.json`, run
  `uv run python scripts/update_progress_catalog.py` and commit the snapshot,
  or the packaged-catalog test fails (by design). Update the "Known failures
  before W1-E" paragraph in `docs/CLOUD-BUILD-PLAN.md` once this is merged.
- **Suite speed (later package or owner decision):** about 45 tests take 25–80
  s each (plugin rollout and engineering, application preparation and
  engineering) and account for roughly half of the 54 minutes. Most of that
  is `runtime_environment_sha256()` re-hashing the whole Python standard
  library (689 files, 13 MB) on every pin check: one 40 s rollout test makes
  167 calls costing 23 s. Caching the result per file keyed on inode, size and
  modification and change times would keep detecting changes and cut roughly
  20 minutes, but it is protected engineering code, so it is not changed here.
- **Subscription smoke deadline (observation):** smoke tasks keep a 270 s
  deadline while each cycle call may now run up to the profile's 600 s. A slow
  call cannot start another one; at worst the next task is refused as expired.
  Whoever next touches the smoke check may want to align them.
- **Bubblewrap on CI:** the tests that really run bubblewrap are skipped on
  GitHub (not installed). Installing it in the workflow would run them, but
  Ubuntu 24.04's restriction on unprivileged user namespaces may block it, and
  that could not be checked from here.
- The CI step "Reject credentials and private runtime bytes in generated
  evidence" reports "unreadable artifact" whenever the test step is cancelled
  or fails before writing its files. That is a follow-on message, not a
  separate problem.
