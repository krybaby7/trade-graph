# Mission Control continuation review

Date: 2026-10-05. Base: `c61ef54`, branch `codex/orchestrator-takeover-2026-10-04`. This owner-requested T15 presentation continuation preserves all earlier work and the 17 completed / 4 in-progress / 2 blocked acceptance states.

## Delivered behavior

`/progress` adds a responsive interactive department graph, implementation progress ring, milestone path, filterable 23-task roadmap, recorded runtime activity, bounded Kraken test missions and persisted results. The authenticated projection refreshes every five seconds in a visible tab. A connection failure retains the last successful snapshot with a stale label; expired sessions require login. Task and result details remain expanded across refreshes, and department selection stays selected.

Project state comes from bounded current planning files in a checkout, or a labelled packaged catalog in an installed wheel. Missing/malformed data yields unavailable counts. Runtime role/task states and the paper-service lease are projected from scoped durable records; refreshing the browser is not agent activity. Task acceptance is independent of test-result badges.

Owner+CSRF actions start only two fixed checks: the packaged scripted offline loop in a fresh private workspace, and four fixed public Kraken GETs. Background workers have deadlines, persistent status, one active check per portfolio/deployment, bounded retention and interrupted-run recovery. No caller-selected command, endpoint or exchange credential is accepted. History is stored in an owner-private sidecar beside the runtime database; no business migration or financial-state write was added. Private-account, validation-only and real-order tests remain planned and unavailable.

An exported self-contained preview supports graph selection and task filters while disabling test actions and network refresh. The live server remains a local authenticated application; no hosting service was purchased or site published.

## Verification

The unchanged lock was provisioned with `uv sync --frozen --group dev --python 3.12`; the workspace had been missing the declared jsonschema dependency. No dependency or price change was made.

Actual command:

```bash
.venv/bin/pytest tests/integration/test_progress_dashboard.py \
  tests/integration/test_progress_runs.py tests/integration/test_progress_projection.py \
  tests/integration/test_dashboard.py tests/integration/test_dashboard_boundary.py \
  tests/integration/test_dashboard_session.py tests/integration/test_dashboard_controls.py \
  tests/integration/test_dashboard_evidence.py tests/integration/test_dashboard_financial.py \
  tests/test_dashboard_pages.py tests/e2e/test_dashboard_loop.py
```

**191 passed**, zero failures. New tests cover actual auth/CSRF boundaries, scope, changed recorded activity, unavailable catalogs/history, expired leases, no live promotion, durable/concurrent/stale test runs, retention, subprocess credential/config scrubbing and public API malformed/error/timeout/size cases. Public transport fixtures are mocks and are not external verification.

Full `ruff check src tests`, Node syntax, planning/DAG, ten planning tests and diff checks passed. Chromium verified desktop 1440x1100 and mobile 390x844, actual owner-started rehearsal with saved result updates, 15 drawn graph paths, department selection, task filters, expansion retention and offline/recovery behavior. No horizontal overflow or browser script error was observed. The private preview contains no session or CSRF token.

Actual packaged offline rehearsals passed all 14 checks with no external provider calls, paid calls or exchange orders. The runner's own exercise also verified unchanged financial database bytes. An actual dashboard public Kraken check failed through the workspace proxy at the first request; it stayed failed and did not complete any Kraken account milestone. Account verification, authenticated reads, validation-only orders and real-money trading were not performed.

An offline wheel was built and its new progress modules, packaged catalog, template, CSS and JavaScript were checked against source. This review is not a new installed full-runtime rehearsal or protected-image admission. The historical 2,544-test suite and image verification retain their original checkpoints. Git publication is separate from CI, whose status remains unavailable through the connector.

## Continuation

Use [the dashboard guide](../MISSION-CONTROL.md) on the chosen private installation. Preserve the test sidecar in operational backups where history is needed; the existing financial-only backup command does not include it. Once the Kraken account is ready, complete the separately configured authentic read-only path and connect its verified producer results to the tracker. The current dashboard has no key entry, live activation or trading-capital control added by this change.
