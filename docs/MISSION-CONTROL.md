# Mission Control dashboard

Mission Control is the visual progress view at `/progress`. It runs inside the existing authenticated dashboard and refreshes every five seconds while the page is visible. Opening the view starts no trading service, paid AI request or Kraken order.

## What you can see

- **Project progress:** the 23 implementation tasks, their recorded completion states, remaining dependencies and milestone path. Completed implementation is separate from actual Kraken acceptance and economic performance.
- **Department graph:** the six AI roles, Secretary, market data and execution. Select a department to see its purpose, connected APIs and actual recorded work. Quiet departments remain idle; the view does not invent agent activity.
- **Runtime activity:** persisted task/event records from the selected portfolio. A current service lease shows the paper service running; a browser connection alone does not mean the trading graph is running.
- **Kraken test missions:** available local/public checks, durable results and the account/order tests still pending. A passed local rehearsal is labelled synthetic; a public API check proves only the public requests it actually performed.

The milestones reward completed work and collected evidence. Profit, number of trades and winning streaks do not unlock implementation or live-trading milestones.

## Open it

Use the same database as the paper service you want to observe. For a fresh private installation:

```bash
umask 077
install -d -m 0700 runtime
uv run trade-graph init --database runtime/trade_graph.sqlite
uv run trade-graph dashboard --database runtime/trade_graph.sqlite
```

For an existing initialized account, run only the dashboard command. Open `http://127.0.0.1:8000/login`, use `session_token` from the private `runtime/owner-session.json` file, and select **Mission Control**. Login opens the progress view. Keep the session file and runtime databases outside Git.

The web server listens locally. Viewing it from another machine requires that installation's approved private access or port forwarding; the repository does not publish a hosted site. A cloud workspace and its running process may stop when the session ends. Use the same dashboard command on the eventual always-on host.

### Viewing from an iPad

Mission Control's mobile layout has been checked in Chromium; iPad Safari has
not yet been tested directly. Safari can be the dashboard viewer once an
authenticated private HTTPS address is configured for the running host. The
iPad does not need to run Python or hold Kraken API credentials. There is no
hosted live address yet, and opening `127.0.0.1` on the iPad refers to the iPad
itself rather than this cloud workspace.

A downloaded preview is a saved snapshot, without live refresh or runnable
checks. Files/Quick Look may display HTML without its interactive JavaScript.
For continuous updates, use the running dashboard through its configured
private access path. The [onboarding guide](KRAKEN-ONBOARDING.md) records the
recommended server arrangement and the next Kraken account check.

## Run a check

Owner access can start two bounded checks:

1. **Local rehearsal:** runs the packaged offline loop in a fresh isolated work directory. It uses scripted market/model responses, virtual capital and local orders. It verifies software behavior without using the runtime account, paid provider credentials or exchange credentials.
2. **Public Kraken API:** checks fixed public time, exchange-status, pair-metadata and price endpoints. It sends no account keys or orders. A network/proxy failure is recorded as a failure, with its stage, rather than a successful account test.

Results update as the check runs and survive dashboard restarts. One check can run at a time for the selected portfolio/deployment. An abandoned timed-out run becomes interrupted, rather than passed.

The tracker retains the newest 20 runs per portfolio/deployment, with a 200-run total bound; older completed entries and their generated rehearsal workspaces are pruned. Preserve important test evidence through private backups. Financial records and project completion evidence are unaffected by this test-history limit.

Kraken account reads, order validation and a small real-order pilot remain planned and unavailable here. The existing protected read-only collector is separate; this view does not accept trading keys, validate orders, enable live mode or grant capital. When those test producers are integrated, their verified evidence needs its own adapter into the tracker.

## Where the progress comes from

In a source checkout, the view reads the current `planning/tasks.json` and `planning/progress.json`; edits appear at the next refresh. An installed wheel uses the packaged `progress_catalog.json` snapshot and labels that source. A missing or malformed catalog is shown as unavailable.

Runtime tasks and events come from the financial runtime database using read snapshots. Test results live in a separate private `progress-runs.sqlite` beside it and do not change the financial journal or task acceptance. They are operational evidence, not a live-readiness certificate. Include the sidecar in private operational backups if you need test history; the existing financial database backup command does not automatically include it.

The projection is available to authenticated readers at `GET /api/v1/progress`. Starting an available test uses owner-authorized `POST /api/v1/progress/tests/{check_id}/run`, with the existing cookie CSRF protection. The page shows refresh failures and the time of its last successful update. Background tabs pause refreshes.
