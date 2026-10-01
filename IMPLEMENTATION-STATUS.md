# Implementation status — T15 complete

Updated: 2026-10-01. **T00–T15 complete at their recorded scopes; autonomous R1 is not complete.**
Branch: `cursor/trade-graph-r1-548a`; verified implementation: `8760fb2667d4e677cac67646a01e86bdb4d563eb`.
Default USD10,000 virtual capital with EUR reporting. Paid calls and live trading remain disabled.

## Completed T15

The responsive dashboard now serves overview, trading, organisation, costs, improvements and owner controls,
with paginated decision, research, lesson, activity and candidate evidence. Financial projections share one
SQLite read snapshot and derive values from authoritative ledger, receipt, allocation and reservation records.
They distinguish simulated trading performance, simulated economic performance after attributable actual
expenses, and actual deployment spend. Native amounts, reporting FX, benchmark, fees, lots, uncertain orders,
stale marks and missing provenance remain visible. Observed trading drawdown uses retained valuation samples;
missing historical samples make it provisional rather than inventing a continuous curve.

Owner controls persist protected policy and shared-pool budgets. Leader tasks remain bounded by actual mandates,
role/root commitments and authority. Writes enforce roles, cookie CSRF, durable request identity and optimistic
revisions. Activation uses the trusted controller's attestation. Resume enters manage-only before reconciliation
and cannot clear unknown execution, unresolved billing, system halts or incomplete recovery. An emergency
manage-only control remains available after an interrupted command. The browser preserves unknown request IDs,
reloads stale revisions and requires an authoritative acknowledgement before reporting success.

The standalone dashboard opens a private paper database, creates a private owner-session file and binds one
loopback web worker. It starts no scheduler or paid provider. Public health exposes no portfolio evidence;
authenticated pages/APIs redact credentials, private paths and raw conversations. The repository hygiene scan
checks staged bytes, and CI scans before uploading test artifacts. Read [the T15 review](docs/reviews/2026-10-01-dashboard.md)
and D36–D40 for scope and limitations. T14 artifact consumption and deterministic rollback remain verified in
[the activation review](docs/reviews/2026-10-01-activation.md).

## Verified commands and evidence

```bash
uv sync --frozen --group dev --python 3.12
uv run ruff check src tests scripts/check_repository_hygiene.py
uv run pytest --junitxml=/tmp/t15-final-results.xml
python3 scripts/check_repository_hygiene.py --staged
python3 scripts/check_plan.py
python3 scripts/test_planning.py
python3 scripts/next_task.py --prompt
uv build --wheel --out-dir /tmp/t15-final-wheel
```

- Python 3.12.14, unchanged `uv.lock`; **534 passed, zero failures, errors or skips**, independently counted
  in JUnit. The recovered T14 baseline was 391 tests.
- Ruff, planning references/DAG, all ten planning tests, cost illustration and staged repository hygiene passed.
- The offline-loop dashboard test reopens the real credential-free demo, reconciles equity/spend to durable
  records, navigates retained activation/rollback evidence and confirms four fills without extra effects.
- Chromium verifies login, eight actual pages, desktop/mobile layout and dark theme without page/CSP errors.
  Scripted browser responses exercise stale revisions, exact-ID retries after unknown outcomes, terminal
  failures, emergency manage-only and malformed successful responses without false success.
- The distributable wheel contains the templates, CSS, JavaScript and favicon. Runtime databases, session
  credentials, browser screenshots and JUnit output remain outside tracked source.

Run the local dashboard:

```bash
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph dashboard --database runtime/trade_graph.sqlite
```

Open `http://127.0.0.1:8000/login` and use `session_token` from the private `runtime/owner-session.json`.
New runtime directories use mode 0700 and session files use 0600; an existing database directory must be private.
Use a fresh demo directory because previous databases and staging evidence are intentionally retained.

This is local credential-free evidence. No new CI conclusion, paid-provider probe, authenticated exchange test,
real order or profitability claim is made.

## Remaining boundaries and next task

**T16 is next:** complete the offline acceptance fault catalogue and production-equivalent full-loop paths.
Its earlier partial evidence is retained; the new dashboard/hygiene cases do not close the whole task.

**T17:** assemble continuous paper scheduling, persisted model-routing consumers, real diagnostics/reporting,
deployment/backup procedures and interrupted dashboard-command recovery. A hard-crashed control remains
visibly `PROCESSING`; ordinary writes stay blocked while emergency manage-only remains available.
`run --mode paper` still performs one recovery pass. Credentialed paper verification needs separate owner funding.

Legacy cross-currency receipts retain their original stored conversion but lack linked FX source IDs; the
dashboard discloses incomplete provenance. Drawdown is sampled trading drawdown; historical economic drawdown
cannot be reconstructed without historical cost-allocation timestamps. Neither label claims continuous observation.

**T18–T22:** forward economic evaluation, live adapter/pilot and protected-host isolation before broader code.
T20 remains blocked. Functional health, a dashboard and paper capital do not authorize paid operation or live trading.
Inspect current Git state before continuing; preserve newer work and historical evidence in progress.json/reviews.
