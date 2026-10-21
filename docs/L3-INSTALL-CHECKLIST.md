# Step L3: installing the new engine — the checklist

Status: written 2026-10-17 by cloud package W6-D (C2, decisions D220–D224) for local step L3 of the
[cloud build plan](CLOUD-BUILD-PLAN.md#local-steps--on-the-owners-pc). Nothing in it has been run: the cloud has no
Docker, no installation, no key and no private data. Paper only. **Every step runs only with your yes.** Cloud package
W7-A (T45, D225–D229) added step 17, the after-L3 owner switch for news wake-ups, and the Leader's `wake` key in step 3.
Cloud package W8-A (C3, D240–D244) built Perplexity inside the installed service: step 3 now names the Perplexity owner
files and network route, and step 12 is no longer conditional. Cloud package W9-A (C6, D255–D259) put the 5-minute news
check inside the installed service: step 3 names its network route and owner files, step 13 what you should see, and
step 17 no longer needs a separate poll. Cloud package W11-B (C11, D290–D294) built the Trading desk's strategy lab
into the installed service: step 3 adds `"lab"` to the Trader's context keys, and the missing confirmation step that
step L2 found (`planning/handoffs/L2.md`, "Finding for L3") is now the lab's first-start behaviour, shown at steps 13
and 15 (see "The strategy lab" below). Cloud package W12-A (C12, D295–D299) made the Trader the Trading desk: step 3
adds `"playbook"` and `"news"` to the Trader's context keys (see "The Trading desk" below). Cloud package W13-A (C13,
D300–D304) added HQ's Trading desk room and the scorecard's split by strategy, market type and stance: step 3 adds
`"scorecard"` to Learning's context keys, and after the install you look at the room on the HQ tab on your phone and
iPad, in light and dark.

In plain words: on your PC a local build session builds the new protected image from the merged code, puts your paper
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
- The Perplexity test key is at hand for the owner folder (step 3, `perplexity.key`; D125, D154) and auto top-up is
  off. Step L2's smoke has re-checked Perplexity's pricing page; if a price changed, the new dated card is in
  `config/perplexity-price-cards.json` (see "Perplexity in the installed service" below).
- For step 12: `<commit>` includes wave 8, which has the Perplexity wiring (W8-A). Without step 12's `--confirm`,
  Research and the Leader stay on the subscription.
- You have about an hour; the account is on hold (MANAGE_ONLY) from step 4 to step 15. Management, reconciliation and
  protective exits keep running the whole time (D117).

## The steps, in order

| # | Step | Command (where) | What you should see |
|---|---|---|---|
| 1 | Get the code and run the full suite | `git fetch origin && git checkout <commit>`, then `uv sync --frozen --group dev --python 3.12` and `uv run pytest -p no:cacheprovider -rf` (trusted checkout on the PC; or read GitHub's `test` job on `<commit>`) | No failure. Compare with "Known failures" in the cloud build plan. |
| 2 | Build the protected image | `uv run python scripts/build_protected_image.py --output <new-build-dir> --subscription-tools <reviewed-native-inputs>` then `uv run python scripts/verify_protected_image.py --build-directory <new-build-dir> --output <new-proof-dir>` (trusted checkout, [PROTECTED-DEPLOYMENT.md](PROTECTED-DEPLOYMENT.md)) | An image pin. The builder first checks that NumPy and TA-Lib are pinned with the hash of a Python 3.12 Linux wheel (it stops before any download if not), and the build imports both before it seals the image (D224). |
| 3 | Prepare the new protected owner files | In `<owner>` (root-owned, as today): `runtime-manifest.json` with the new image's `protected_package_sha256` and approved source; `paper-config.json` with your existing settings plus `"event_engine": true` (the price collector is on by default with `public_data_enabled: true`; see "The configuration" below); `subscription-profile.json` with `"wake"`, `"practice_range"`, `"lab"` (W11-B: the Trading desk's strategy lab), `"playbook"` and `"news"` (W12-A: the desk's playbook with its newest stances, and the Leader's news round, the lab inbox and Jev's verdicts on its coins) added to `allowed_context_keys.trader`, `"scorecard"` (W13-A: the scorecard split by strategy version, market type at entry and stance) added to `allowed_context_keys.learning` and `"wake"` added to `allowed_context_keys.leader` (T45: the Leader reads why it was woken, news alarms included); `subscription-network-profile.json` re-pinned to the new image id, manifest, package hash and the new `paper-config.json` hash; check `graph.py` (below). For Perplexity (step 12): `perplexity.key` (your key, one line) and `perplexity-price-cards.json` (a copy of `config/perplexity-price-cards.json`), both `root:10001` mode `0440`; `"reviewed_routes": {"paid_research": {"port": 8082, "hosts": ["api.perplexity.ai"]}}` in `subscription-network-profile.json`; and your reviewed proxy recreated with `--route paid_research=8082:api.perplexity.ai` added to its command and its new review recorded ([REVIEWED-NETWORK-ROUTES.md](REVIEWED-NETWORK-ROUTES.md)). For the news check (step 17): `"news_feeds": {"port": 8083, "hosts": ["api.typesafe.ai", "blog.kraken.com", "cointelegraph.com", "decrypt.co", "status.kraken.com", "www.coindesk.com"]}` added to `reviewed_routes` and `--route news_feeds=8083:api.typesafe.ai,blog.kraken.com,cointelegraph.com,decrypt.co,status.kraken.com,www.coindesk.com` to the proxy command (the same re-pin and review); optionally `news-alarm.json` (a copy of `config/news-alarm.example.json`; `"calls_enabled": true` under `jev` switches Jev on) and, with Jev on, `typesafe.key` (your key, one line), both `root:10001` mode `0440` ([NEWS-ALARM.md](NEWS-ALARM.md#in-the-installed-service-w9-a)) | The new files validate: `python -I -B -m trade_graph.kernel.deployment_image check-subscription` (inside the new image) prints `subscription_paper_profile_prepared`. |
| 4 | Hold the account and stop the service | HQ: **Pause AI** / the authenticated owner pause to MANAGE_ONLY, then **Stop safely** (the dashboard, old image) | HQ shows the account on hold and the service stopped; `paper-service` and `role-worker` leases cleared. |
| 5 | Stop the old Windows price tasks | `Get-ScheduledTask -TaskName 'TradeGraph-Candles-*' \| Disable-ScheduledTask` (PowerShell on the PC) | Both tasks `Disabled`. They are removed in step 14, once the new collector is seen working. Two collectors at once are not coordinated (D133). |
| 6 | Back up | `trade-graph backup --database /var/lib/trade-graph/trade_graph.sqlite --destination <private-backup>` (inside the old image, service stopped); keep a copy of the WSL candle file `~/.local/share/trade-graph-market/market_data.sqlite` | A backup file; the candle file copy. The candle file is public data and not part of the financial recovery unit (D132). |
| 7 | Put your price history into the installation | `sqlite3 ~/.local/share/trade-graph-market/market_data.sqlite ".backup '<state>/market_data.sqlite'"`, then `chown 10001:10001 <state>/market_data.sqlite && chmod 600 <state>/market_data.sqlite` (WSL, collector stopped) | `<state>/market_data.sqlite` beside `<state>/trade_graph.sqlite`, owned by the container user, mode 600. Skipping it is safe: the collector then starts a new file with the newest 720 candles of each series (30 days of hours, 12 hours of minutes) and the archive history is missing. |
| 8 | Move the books to the new release | `python -m trade_graph.kernel.financial_transition inspect --database /var/lib/trade-graph/trade_graph.sqlite --protected-owner /run/trade-graph-owner --source-manifest <previous-owner>/runtime-manifest.json --operation-id <unique-id> --expires-at <in-the-next-hour>`, install the reviewed approval as `financial-continuity-approval.json` in `<owner>`, then `python -m trade_graph.kernel.financial_transition apply --database /var/lib/trade-graph/trade_graph.sqlite --protected-owner /run/trade-graph-owner` (inside the new image, [FINANCIAL-TRANSITION.md](FINANCIAL-TRANSITION.md)) | The transition committed; every portfolio still MANAGE_ONLY. |
| 9 | Start the new dashboard | `python -I -B -m trade_graph.kernel.deployment_image boot-subscription-dashboard` (the launcher, new image) | HQ opens; it reads the candle file read-only (`--market-database /var/lib/trade-graph/market_data.sqlite`, set by the image). |
| 10 | Clean up the mandate | `python -I -B -m trade_graph.cli mandate-cleanup --database /var/lib/trade-graph/trade_graph.sqlite`, then the same with `--confirm` (inside the image, service not running) | A new mandate revision without the leftover test strategy ids (D129). |
| 11 | Record your money limits | `python -I -B -m trade_graph.cli money-limits --database /var/lib/trade-graph/trade_graph.sqlite` (preview), then the same with `--confirm` (W3-A, D138 items 1, 2, 3 and 6) | A new policy revision: 3% daily loss limit, 20% per coin, 80% in total, the coin rule, day start 00:00 UTC. Until this exists day plans are refused. |
| 12 | Switch Research and the Leader to Perplexity | `python -I -B -m trade_graph.cli perplexity-departments --database /var/lib/trade-graph/trade_graph.sqlite` (preview), then the same with `--confirm` (W6-A's command for both departments; options `--expect-revision`, `--research on\|off`, `--leader on\|off`, `--monthly-limit-usd`; it refuses while the service runs; the Leader's news rounds, W6-B, read the same switch) | A switch revision naming Research and the Leader. After step 13's start, HQ's activity log shows `perplexity_route` with status `ready` (or `not_ready` and the reason: a missing key or price-card file, no `paid_research` route, a stale card). Skip this step to keep Research and the Leader on the subscription. |
| 13 | Start the service and watch the prices | HQ **Start Trading** (the account stays on hold); after 10 to 70 minutes: `python -I -B -m trade_graph.cli candle-coverage --market-database /var/lib/trade-graph/market_data.sqlite --symbols BTC/USD,ETH/USD` and `python -I -B -m trade_graph.cli coin-shortlist --database /var/lib/trade-graph/trade_graph.sqlite` (inside the image) | With step 12 done, a `perplexity_route` activity with status `ready`. With the news route listed, a `news_route` activity with status `ready` (and whether Jev is on, or why not) and a first `news_poll` activity at once; without it, `news_route` `not_ready` and the reason. The collector's first `price_collection` activity (the pair list), then BTC/USD and ETH/USD at 1, 5, 15 and 60 minutes with a last close within the hour; after the next hour's pass, a new live shortlist. HQ's charts draw current candles. No `price_collection` event with `stop_reason` `rate_limited` or `throttled` and no feed outage; if they appear, set `"ranking_every_hours": 2` (the quote feed's requests are outside the collector's limiter, KRAKEN-CANDLE-COLLECTION.md, "In the service"). A `strategy_lab` activity with status `started`: on this first start of the new engine it names `registered_starting` `slow-trend-pullback@1` and `range-reversion@1` (the installation had no registered strategy) and `candle_file` true (see "The strategy lab" below). |
| 14 | Remove the old Windows price tasks | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_candle_schedule.ps1 -Remove` (PowerShell on the PC) | `removed TradeGraph-Candles-Short`, `removed TradeGraph-Candles-Full`. If step 13 showed no fresh candles, re-enable them instead (`Enable-ScheduledTask`) and stop here. |
| 15 | Lift the hold | HQ **Resume** (authenticated owner resume with the current revision, [NORMAL-PAPER-OPERATION.md](NORMAL-PAPER-OPERATION.md)) | The service runs. With `event_engine` on, the next 00:00 UTC day start takes the collector's newest hourly scan as the day's shortlist and runs Research's focus step and the Trader's day plans; HQ's wake log (W6-C) shows the wakes. The first day plans may be refused with "has no practice-range confirmation report yet": the Trading desk first asks its strategy lab for confirmations (W11-B). HQ's activity log then shows `strategy_lab` entries (each request `done` or `refused` with the reason, and the seconds spent), the wake log a wake for "the strategy lab's results", and the desk's next plans can follow a confirmed version. No owner command is needed. |
| 16 | About two weeks later | Set the Perplexity monthly limit at about 1.5 times what HQ shows (D138 item 4) | — |
| 17 | After L3, once the shadow log shows real news alarms: decide on news wake-ups (T45, D227) | The service runs the 5-minute news check itself once step 3's news route is in place, with Jev when your `news-alarm.json` switches it on and `typesafe.key` is installed (W9-A, [NEWS-ALARM.md](NEWS-ALARM.md#in-the-installed-service-w9-a); see "News wake-ups" below). Do not run `trade-graph news-alarm-poll` against the installation's database: it refuses Jev checks there. Look at `python -I -B -m trade_graph.cli news-alarm-report --database /var/lib/trade-graph/trade_graph.sqlite --format text` (reads only; it also lists each feed's polls), HQ's Wake log top line ("would have happened") and `python -I -B -m trade_graph.cli news-wakes --database /var/lib/trade-graph/trade_graph.sqlite` (shows the evidence, writes nothing). To switch on: stop the service, run the same with `--live on` (preview), then `--live on --confirm --expect-revision <revision shown>`, then start the service again. `--live off --confirm` goes back to shadow at any time. | A new owner-policy revision with `news_wakes: "live"` and an audit row with the evidence you saw. Refused while the service runs, and refused while the shadow log holds no alarm from a live feed with a real Jev answer. From then on a news alarm wakes the Leader, and the Trader when the coin is held; HQ's Wake log shows "A news alarm about … from …". |

If any step fails: keep the hold, do not resume, and leave the old image's files in place. Restoring the candle file
is never needed for the books (it is rebuilt from Kraken); the financial rules for an interrupted transition are in
[FINANCIAL-TRANSITION.md](FINANCIAL-TRANSITION.md#interruption).

## Perplexity in the installed service: prerequisites of step 12

W6-A and W6-B built Research's focus coins and the Leader's news rounds on Perplexity, and W6-A's switch; W8-A (C3,
D240–D244) built them into the installed subscription service ([PERPLEXITY-IN-SERVICE.md](PERPLEXITY-IN-SERVICE.md)).
The six prerequisites, now done or concrete owner actions:

1. **Done (W8-A).** With `event_engine: true` and the switch on, `run_subscription_operation` hands Research's and the
   Leader's engine wakes to `FocusResearch` and `LeaderNewsRounds` in the protected process, on a paid-enabled gateway
   that carries only their rounds and the software `web_search` tool (every search result stored before the model
   reads it). Their other tasks, and everything while the switch is off, stay on the subscription; `paper-config.json`
   still refuses `models` and `price_cards`.
2. **Owner action (step 3).** The reviewed `paid_research` route: add it to `subscription-network-profile.json`
   (port 8082 suggested; hosts `["api.perplexity.ai"]`, the only host the reviewed catalogue allows) and to the proxy
   command (`--route paid_research=8082:api.perplexity.ai`), re-pin the profile and record the proxy's new review. The
   market listener still admits only Kraken and Frankfurter ([REVIEWED-NETWORK-ROUTES.md](REVIEWED-NETWORK-ROUTES.md)).
3. **Owner action (step 3).** The key: `<owner>/perplexity.key`, one line, `root:10001` mode `0440`, read only by the
   protected owner-file reader (D154), never from chat, Git, the environment or a model's context.
4. **Owner action (step 3), with step L2.** The prices: `<owner>/perplexity-price-cards.json`, a copy of
   `config/perplexity-price-cards.json`, seeded into the database as immutable dated cards. The cloud could not open
   Perplexity's pricing page on 2026-10-17, so the cards are unchanged: the 2026-10-12 Agent API card goes stale on
   **2026-11-11** and the Search API card on 2026-11-15. *Done at L2 on 2026-10-21 (D231): the official pages showed the
   same prices, renewed as cards verified 2026-10-21, fresh until 2026-11-20; renew again before then during L4.*
   **Re-check at step L2**: read the official pricing page; if a
   price differs, or to renew a card that would be stale on the day of L3, add a new dated card (new id, new
   `verified_at`) to `config/perplexity-price-cards.json` and copy the file again. A stale card refuses paid calls
   (the wake fails with the reason), never prices them as free.
5. **Done (W6-A, W8-A).** Money: each switched department's wake gets the owner's paid root allocation per task; the
   deployment's EUR allowance is not consulted for these rounds, so it cannot block D125's testing period; your
   spend rule (no limit while testing, `--monthly-limit-usd` later, step 16) pauses only these rounds.
6. **Owner action (step 3).** `event_engine: true` in `paper-config.json`: without it nobody is woken for a focus step
   or a news round.

If anything is missing at start, HQ's activity log shows `perplexity_route` `not_ready` with the reason, the switched
departments' wakes fail with it and nothing is sent; the Trader, protection and records continue.

Without step 12 the engine still runs: Research's subscription answer names no focus coins, so each day's focus step
is recorded as failed and the Trader's day-plan wake follows with the scanner's shortlist as its coins (D223).

The hourly scans of the price collector (D222) keep the newest live scan at most about an hour old, which the coin rule
of your money limits needs (W2-B's `coin_rule` treats a scan older than two hours as unknown). The day's focus coins
still come from the day start's shortlist; the hourly scans do not widen it during the day (W6-A's engine check is
unchanged), they only keep the coin rule and the collected coins current.

## News wake-ups: what step 17 needs

News alarms ship in shadow mode (T45): with the event engine on, the engine records, for every headline Jev rated
urgent, whom it would wake (the Leader, and the Trader when the coin is held) and wakes nobody. Nothing is recorded
without verdicts, and verdicts need the news check with Jev. W9-A (C6, D255–D259) built the news check into the
installed service; what remains are owner actions:

1. **Done (W9-A).** The service runs T44's poll every 5 minutes in its own thread (like the price collector), in the
   protected process, writing into the installation's state database, so the engine routes its verdicts. A failed step
   shows in HQ as "the news check's last step failed"; trading, protection and records never wait for it. It keeps
   running under every pause.
2. **Owner action (step 3).** The reviewed `news_feeds` route: in `subscription-network-profile.json` (port 8083
   suggested; the five feed hosts and `api.typesafe.ai`, the only hosts the reviewed catalogue allows) and on the proxy
   command, then re-pin the profile and record the proxy's new review ([REVIEWED-NETWORK-ROUTES.md](REVIEWED-NETWORK-ROUTES.md)).
   Without it nothing is fetched and HQ's activity log shows `news_route` `not_ready` with the reason.
3. **Owner action (step 3), optional until you want Jev.** `<owner>/news-alarm.json` with `"calls_enabled": true` under
   `jev`, and `<owner>/typesafe.key`, both `root:10001` mode `0440`, read only by the protected owner-file reader
   (sign up at console.typesafe.ai only, D137). Without them the service still collects headlines, and nothing is sent
   to TypeSafe.
4. **Owner action (step L2), before 2026-11-15.** The dated Jev card `typesafe-jev-1.13.0-2026-10-15` refuses real calls
   from 2026-11-15 (more than 30 days after it was read). Re-read TypeSafe's models page at L2; to renew it, put a new
   dated card (new id, new `verified_at`, its source) under `jev.price_card` in your `news-alarm.json`. A stale card
   keeps Jev off with the reason; it never prices a call as free.
5. **Done (W9-A).** Money: each Jev check reserves on the route's own deployment (`<deployment>:news_alarm`) within your
   owner policy's paid-call limits (`daily_paid_limit` per day, `monthly_operating` per month), with a protected budget
   origin before anything is sent and a receipt after; the USD5 testing alert stays an alert. Expected about USD0.05 a
   day (D137).

Then, once the shadow log holds real would-be alarms, decide at step 17. `news-alarm-report --database
/var/lib/trade-graph/trade_graph.sqlite --format text` (inside the image) lists the
headlines, Jev's answers, the would-be alarms and each feed's polls; a feed that could not be read is listed as lost,
never as "no news". Switching on is refused while the shadow log holds no alarm from a live feed with a real Jev answer.
The Leader's news-alarm wake works on the subscription Leader (it reads the alarm in its `wake` context key, step 3) and
on the Perplexity Leader of step 12 (in its brief).

## The strategy lab: what replaces the missing confirmation step (W11-B)

Step L2 found that a day plan needs a practice-range **confirmation** report in the installed database
(`application/day_plans.py`, D138 item 5) and that only the owner's `practice-range-*` commands made one. W11-B (C11,
D290–D294) put the strategy lab into the installed service instead; nothing is run by hand:

1. **First start (D293).** When the service starts and the installation has no registered strategy, the lab registers
   the two examples shipped in the image (`slow-trend-pullback@1`, `range-reversion@1`, the repository's
   `strategies/practice/*.v1.json`). It does not confirm them: confirmations are the desk's own requests, like any
   version. HQ's activity log shows a `strategy_lab` activity `started` with `registered_starting` naming both.
2. **The desk asks (D290).** On a wake the Trader (the Trading desk) sees its `lab` context key (step 3 adds it to the
   profile; without it the desk does not see the lab, though its requests still run) and may add `lab_requests` to its
   answer: register a version, create a study (tuning window, then a later confirmation window), tune, confirm. No
   limit on how many (D116).
3. **The lab runs them (D292).** In its own thread, like the price collector, against the candle file read only, under
   the cost profiles the desk names and always the account's own (Kraken's starting tier). It never places an order and
   no trading, protection or money-limit step waits for it. A stop or restart re-runs an interrupted request; nothing is
   half recorded. HQ's activity log shows each request `done`, `refused` (with the reason) or `failed`, the seconds
   spent and how many are queued and running.
4. **The desk is woken (D291).** When all requests of one answer have ended, the desk is woken with `lab_results`
   (HQ's wake log: "the strategy lab's results"); like a news alarm it keeps the desk's own check-in. A version with a
   confirmation report may then enter a day plan; there is no pass mark.
5. **Fair by protected software (D294).** Tuning never touches confirmation days, a confirmation runs once on untouched
   later days (the lab offers the newest, so new days from the collector become new confirmation days) and every try
   is listed in the confirmation report; these rules are protected paths the deployed Engineer cannot change.

To read the reports yourself: `python -I -B -m trade_graph.cli practice-range-report --database
/var/lib/trade-graph/trade_graph.sqlite --strategy <id@version> --format text` (inside the image; it opens the database
read-only). The practice database of step L2 is separate and unchanged; its confirmation windows (August and September
2026) do not count in the installation.

## The Trading desk (W12-A)

The Trader is now the Trading desk ([W12-A handoff](../planning/handoffs/W12-A.md)): on each wake it reads every coin's market type
(the hourly labels, inside `"wake"`), its lab, its playbook and the news, and says per coin whether it waits in cash,
buys dips, follows the trend or does something else. Nothing is needed from you beyond step 3's two keys. What you see
after step 15: HQ's wake log as before; the desk's playbook revisions and stances are stored and HQ's Trading desk
room (the HQ tab, below the map; wave 13) shows them with the lab, the news the desk read and what each strategy earned
in each market type; each installed day plan records the coin's market type and the playbook revision in force.
Software never refuses a day plan for not following the playbook or a market type (D280 item 5). State migration 0052
runs by itself when the new image opens the database. Learning's turns read the scorecard's split (W13-A) once
`"scorecard"` is in `allowed_context_keys.learning` (step 3); without it Learning answers as before.

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

The subscription profile (`<owner>/subscription-profile.json`) must allow the Trader's five new context keys,
`"wake"`, `"practice_range"`, `"lab"`, `"playbook"` and `"news"`, in `allowed_context_keys.trader` (and Learning's new
key, `"scorecard"`, in `allowed_context_keys.learning`). Without them the
Trader still answers a wake, but without knowing why it was woken, which strategy versions have a practice-range
report, what its strategy lab found, which playbook it keeps or what the news says (the coins' market types come with
`"wake"`).

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
  plan only within your limits and coin rule. The fixed four-hour timer is gone while `event_engine` is on. The quote
  feed then also quotes the day's coins (focus coins, coins with a day plan, an armed alarm or a position, within
  your coin rule) in one extra request every 10 seconds, so their plans can rest, fill and be protected; a failure of
  that request never stops the configured coins' quotes and is recorded as `public_feed_day_coins`. A wake for an
  alarm, a fill or an exit is answered at once; only the day's plans and the Trader's own check-ins wait, at most
  `decision_candle_wait_seconds`, for the hour that just closed.
- **News alarms, shadow mode** (T45): with the engine on, every would-wake news alarm is recorded with whom it would
  wake and why (`news_alarm_wake_routes`), and nobody is woken until step 17. HQ's Wake log counts them. HQ's room
  tiles and calendar show each department's next turn from its own check-in, not from the old fixed timers.

## Afterwards (L4)

The scored practice test (T43) starts after this step. A good result does not authorize real money (D138 item 7).
