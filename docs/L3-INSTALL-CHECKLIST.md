# Step L3: installing the new engine — the checklist

Status: written 2026-10-17 by cloud package W6-D (C2, decisions D220–D224) for local step L3 of the
[cloud build plan](CLOUD-BUILD-PLAN.md#local-steps--on-the-owners-pc). Nothing in it has been run: the cloud has no
Docker, no installation, no key and no private data. Paper only. **Every step runs only with your yes.**

In plain words: on your PC a Claude session builds the new protected image from the merged code, puts your paper
account on hold, copies your price history into the installation, moves the books to the new release, records your
money limits and switches Research and the Leader to Perplexity, starts the service with the new price collector,
checks that prices arrive, removes the two old Windows price tasks, and then lifts the hold. The event engine (wakes
instead of fixed timers) is one setting in the protected configuration; this checklist turns it on together with
the release. Your virtual USD10,000 account, its history and every receipt stay as they are.

Placeholders in angle brackets are the owner's private paths (never written in Git). `<state>` is the private state
folder mounted at `/var/lib/trade-graph` in the image, `<owner>` the root-owned owner folder mounted read-only at
`/run/trade-graph-owner`, `<commit>` the integration commit being installed. "Inside the image" means the way the
owner launcher already runs commands in the installed image (`python -I -B -m trade_graph.kernel.deployment_image
...` for the fixed entry points, `python -I -B -m trade_graph.cli ...` for owner commands); the checklist never
bypasses the launcher.

## Before you start

- Waves 1 to 6 are merged into `claude/day-trader-engine-20261012` and GitHub's `test` job passed on `<commit>`.
- The Perplexity test key is installed through the private-file route (D125, D154) and auto top-up is off.
- For step 12 only: the Perplexity wiring listed under "Perplexity in the installed service" is merged; otherwise
  step 12 is skipped and Research and the Leader stay on the subscription.
- You have about an hour; the account is on hold (MANAGE_ONLY) from step 4 to step 13. Management, reconciliation and
  protective exits keep running the whole time (D117).

## The steps, in order

| # | Step | Command (where) | What you should see |
|---|---|---|---|
| 1 | Get the code and run the full suite | `git fetch origin && git checkout <commit>`, then `uv sync --frozen --group dev --python 3.12` and `uv run pytest -p no:cacheprovider -rf` (trusted checkout on the PC; or read GitHub's `test` job on `<commit>`) | No failure. Compare with "Known failures" in the cloud build plan. |
| 2 | Build the protected image | `uv run python scripts/build_protected_image.py --output <new-build-dir> --subscription-tools <reviewed-native-inputs>` then `uv run python scripts/verify_protected_image.py --build-directory <new-build-dir> --output <new-proof-dir>` (trusted checkout, [PROTECTED-DEPLOYMENT.md](PROTECTED-DEPLOYMENT.md)) | An image pin. The builder first checks that NumPy and TA-Lib are pinned with the hash of a Python 3.12 Linux wheel (it stops before any download if not), and the build imports both before it seals the image (D224). |
| 3 | Prepare the new protected owner files | In `<owner>` (root-owned, as today): `runtime-manifest.json` with the new image's `protected_package_sha256` and approved source; `paper-config.json` with your existing settings plus `"event_engine": true` (the price collector is on by default with `public_data_enabled: true`; see "The configuration" below); `subscription-profile.json` with `"wake"` and `"practice_range"` added to `allowed_context_keys.trader`; `subscription-network-profile.json` re-pinned to the new image id, manifest, package hash and the new `paper-config.json` hash; check `graph.py` (below) | The new files validate: `python -I -B -m trade_graph.kernel.deployment_image check-subscription` (inside the new image) prints `subscription_paper_profile_prepared`. |
| 4 | Hold the account and stop the service | HQ: **Pause AI** / the authenticated owner pause to MANAGE_ONLY, then **Stop safely** (the dashboard, old image) | HQ shows the account on hold and the service stopped; `paper-service` and `role-worker` leases cleared. |
| 5 | Stop the old Windows price tasks | `Get-ScheduledTask -TaskName 'TradeGraph-Candles-*' \| Disable-ScheduledTask` (PowerShell on the PC) | Both tasks `Disabled`. They are removed in step 12, once the new collector is seen working. Two collectors at once are not coordinated (D133). |
| 6 | Back up | `trade-graph backup --database /var/lib/trade-graph/trade_graph.sqlite --destination <private-backup>` (inside the old image, service stopped); keep a copy of the WSL candle file `~/.local/share/trade-graph-market/market_data.sqlite` | A backup file; the candle file copy. The candle file is public data and not part of the financial recovery unit (D132). |
| 7 | Put your price history into the installation | `sqlite3 ~/.local/share/trade-graph-market/market_data.sqlite ".backup '<state>/market_data.sqlite'"`, then `chown 10001:10001 <state>/market_data.sqlite && chmod 600 <state>/market_data.sqlite` (WSL, collector stopped) | `<state>/market_data.sqlite` beside `<state>/trade_graph.sqlite`, owned by the container user, mode 600. Skipping it is safe: the collector then starts a new file with the newest 720 candles of each series (30 days of hours, 12 hours of minutes) and the archive history is missing. |
| 8 | Move the books to the new release | `python -m trade_graph.kernel.financial_transition inspect --database /var/lib/trade-graph/trade_graph.sqlite --protected-owner /run/trade-graph-owner --source-manifest <previous-owner>/runtime-manifest.json --operation-id <unique-id> --expires-at <in-the-next-hour>`, install the reviewed approval as `financial-continuity-approval.json` in `<owner>`, then `python -m trade_graph.kernel.financial_transition apply --database /var/lib/trade-graph/trade_graph.sqlite --protected-owner /run/trade-graph-owner` (inside the new image, [FINANCIAL-TRANSITION.md](FINANCIAL-TRANSITION.md)) | The transition committed; every portfolio still MANAGE_ONLY. |
| 9 | Start the new dashboard | `python -I -B -m trade_graph.kernel.deployment_image boot-subscription-dashboard` (the launcher, new image) | HQ opens; it reads the candle file read-only (`--market-database /var/lib/trade-graph/market_data.sqlite`, set by the image). |
| 10 | Clean up the mandate | `python -I -B -m trade_graph.cli mandate-cleanup --database /var/lib/trade-graph/trade_graph.sqlite`, then the same with `--confirm` (inside the image, service not running) | A new mandate revision without the leftover test strategy ids (D129). |
| 11 | Record your money limits | `python -I -B -m trade_graph.cli money-limits --database /var/lib/trade-graph/trade_graph.sqlite` (preview), then the same with `--confirm` (W3-A, D138 items 1, 2, 3 and 6) | A new policy revision: 3% daily loss limit, 20% per coin, 80% in total, the coin rule, day start 00:00 UTC. Until this exists day plans are refused. |
| 12 | Switch Research and the Leader to Perplexity (only once the wiring below is merged) | `python -I -B -m trade_graph.cli perplexity-departments --database /var/lib/trade-graph/trade_graph.sqlite` (preview), then the same with `--confirm` (W6-A's command for both departments; options `--expect-revision`, `--research on\|off`, `--leader on\|off`, `--monthly-limit-usd`; it refuses while the service runs; the Leader's news rounds, W6-B, read the same switch) | A switch revision naming Research and the Leader. Until "Perplexity in the installed service" below is merged, skip this step: Research and the Leader stay on the subscription. |
| 13 | Start the service and watch the prices | HQ **Start Trading** (the account stays on hold); after 10 to 70 minutes: `python -I -B -m trade_graph.cli candle-coverage --market-database /var/lib/trade-graph/market_data.sqlite --symbols BTC/USD,ETH/USD` and `python -I -B -m trade_graph.cli coin-shortlist --database /var/lib/trade-graph/trade_graph.sqlite` (inside the image) | The collector's first `price_collection` activity (the pair list), then BTC/USD and ETH/USD at 1, 5, 15 and 60 minutes with a last close within the hour; after the next hour's pass, a new live shortlist. HQ's charts draw current candles. |
| 14 | Remove the old Windows price tasks | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_candle_schedule.ps1 -Remove` (PowerShell on the PC) | `removed TradeGraph-Candles-Short`, `removed TradeGraph-Candles-Full`. If step 13 showed no fresh candles, re-enable them instead (`Enable-ScheduledTask`) and stop here. |
| 15 | Lift the hold | HQ **Resume** (authenticated owner resume with the current revision, [NORMAL-PAPER-OPERATION.md](NORMAL-PAPER-OPERATION.md)) | The service runs. With `event_engine` on, the next 00:00 UTC day start runs the scan step, Research's focus step and the Trader's day plans; HQ's wake log (W6-C) shows the wakes. |
| 16 | About two weeks later | Set the Perplexity monthly limit at about 1.5 times what HQ shows (D138 item 4) | — |

If any step fails: keep the hold, do not resume, and leave the old image's files in place. Restoring the candle file
is never needed for the books (it is rebuilt from Kraken); the financial rules for an interrupted transition are in
[FINANCIAL-TRANSITION.md](FINANCIAL-TRANSITION.md#interruption).

## Perplexity in the installed service: prerequisites of step 12

W6-A and W6-B built Research's focus coins and the Leader's news rounds on Perplexity, and W6-A's switch. Running
them **in the installed subscription service** still needs wiring that no wave 6 package built (W6-A's and W6-B's
handoffs; W6-D could not build it on code that was not merged yet). Each item is an explicit prerequisite of step 12,
for a follow-up package before L3 or the integrator; until they exist, leave the switch off:

1. A Research handler (W6-A's focus research) and a Leader news-round handler (W6-B) assembled inside the protected
   process boundary with a paid-enabled gateway. Today `run_subscription_operation` refuses separately billed API
   configuration (`models` or `price_cards` in `paper-config.json`) and builds every department from the subscription
   profile.
2. A pinned transport that reaches both Perplexity endpoints. The subscription market proxy admits only
   `api.kraken.com` and `api.frankfurter.dev` (`kernel/subscription_proxy.py`, `MARKET_HOSTS`), so the owner's proxy
   review and network profile need the Perplexity hosts as a separately reviewed route.
3. The Perplexity key read from the owner's private file inside the image (D154), never from chat, Git or the
   environment.
4. Seeded, re-verified price cards and their route: the 2026-10-12 Agent card goes stale on 2026-11-11 (W6-B).
5. A money allocation for Research and Leader wakes on Perplexity: in a subscription installation every wake task
   gets EUR0 (`PaperService._wake_task`), so a Perplexity wake stops at its allocation before any request (safe, but
   no round runs); W6-A gives a switched-on department's wake the owner's paid root allocation (the integrator merges
   it with this package's `paper_service.py` changes).
6. `event_engine: true` in `paper-config.json` (this package, step 3): without it nobody is woken for a focus step or a
   news round.

Without step 12 the engine still runs: Research's subscription answer names no focus coins, so each day's focus step
is recorded as failed and the Trader's day-plan wake follows with the scanner's shortlist as its coins (D223).

The hourly scans of the price collector (D222) keep the newest live scan at most about an hour old, which the coin rule
of your money limits needs (W2-B's `coin_rule` treats a scan older than two hours as unknown). The day's focus coins
still come from the day start's shortlist; the hourly scans do not widen it during the day (W6-A's engine check is
unchanged), they only keep the coin rule and the collected coins current.

## The configuration (`<owner>/paper-config.json`)

Keep every existing setting; these are the new ones (W6-D). All of them are public-data settings or a switch; none
adds a paid call, an order permission or a cap on anyone's work (D116).

```json
{
  "public_data_enabled": true,
  "event_engine": true,
  "price_collector": {"enabled": true}
}
```

- `event_engine` (D223, default `false`): the event engine replaces the fixed four-hour Trader timer and the other role
  timers; departments wake on alarms, fills, exits, Leader requests, their own check-ins and the day start. It needs
  `public_data_enabled` and the price collector (the price watcher's candle and indicator alarms read its candle file)
  and is refused without them.
- `price_collector` (D222, on by default): `one_minute_symbols` (default BTC/USD and ETH/USD; focus coins and coins with
  a 1-minute alarm are collected at 1 minute too), `one_minute_every_seconds` (300), `ranking_every_hours` (1: every
  eligible pair's hourly candles for the hourly scan), `wide_every_hours` (24: every eligible pair at 5 and 15 minutes),
  `hourly_scan` (true) and `chunk_requests` (30). `{"enabled": false}` keeps the older behaviour: no candle file in the
  service and the hourly BTC/ETH refresh into `hourly_candles`.
- With the collector on, the hourly refresh into `hourly_candles` stops (the collector supersedes it); the Trader's and
  Research's hourly features read the candle file (D220). Old snapshots stay readable.

The subscription profile (`<owner>/subscription-profile.json`) must allow the Trader's two new context keys,
`"wake"` and `"practice_range"`, in `allowed_context_keys.trader`. Without them the Trader still answers a wake, but
without knowing why it was woken or which strategy versions have a practice-range report.

The owner graph (`<owner>/graph.py`) sees every department answer at `apply_role_result`. A wake answer has
`day_plans`, `next_check_in` and `summary` and no `rationale`: a graph that edits `payload["rationale"]` for the
Trader fails every wake turn (the turn is set aside, no order is placed, the wake is recorded failed). The reference
graph in `scripts/verify_protected_image.py` passes answers through unchanged.

## What is new in the running system

- **The price collector** (D222, [KRAKEN-CANDLE-COLLECTION.md](KRAKEN-CANDLE-COLLECTION.md#in-the-service)) runs in
  the service's own thread and replaces the two Windows tasks: the coins the team works with at 1, 5, 15 and 60
  minutes as their candles close, every eligible pair's hourly candles and then the coin scan every hour, every
  eligible pair at 5 and 15 minutes once a day, the pair list once a day. All through one request limiter with the
  Trades backfill (D221). Each chunk is one candle run in the candle file; HQ's activity log shows the pair list, each
  hourly scan, gaps and failures as `price_collection`.
- **The candle file** is `<state>/market_data.sqlite` (D224), private like the database, written only by the
  collector and opened read-only by the dashboard, the hourly features and the price watcher. It grows by about 30 MB
  a day for the full scope (KRAKEN-CANDLE-COLLECTION.md, "Storage"); the store never deletes.
- **The Trader on a wake** (D223) answers with day plans for its coins and its next check-in; software installs each
  plan only within your limits and coin rule. The fixed four-hour timer is gone while `event_engine` is on.

## Afterwards (L4)

The scored practice test (T43) starts after this step. A good result does not authorize real money (D138 item 7).
