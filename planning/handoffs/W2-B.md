# W2-B T31: Hourly coin scanner and a shortlist of about 10

For the owner: The coin scanner is built: once an hour it can rank every Kraken coin traded in US dollars by how
much it trades, how far its price usually moves compared with the trading fee, how small the gap between buying
and selling prices is, and whether it rose over the last day, and it keeps the top ten as a shortlist for Research,
with every number behind it and no AI involved. It leaves out stablecoins, coins that pay a yield, coins Kraken is
not trading normally, coins that trade too little, unreviewed coins whose price sits still like a stablecoin's,
and seven coins (U, STBL, UNITAS, CAP, DEBIT, RE and ST) that the coin list had marked for your look; a replay on
old price history is always labelled optimistic, because coins Kraken later delisted are missing from it. The
cloud could not reach Kraken, so it is tested only on recorded and made-up data and the first real scan is part of
step L2 on your PC; nothing needs a decision from you, unless you want one of those seven coins reviewed and
allowed.

Branch: `claude/t31-coin-scanner`, head: the commit that adds this line; code at `0fbe7a5`. Pull request:
PR_LINK_PENDING.

## What was built

- `src/trade_graph/contracts/coin_scanner.py`: `ScannerRules`, the dated, versioned rules (thresholds, indicator
  choices from the menu, cost profile, hold list, method) whose canonical SHA-256 is the ranking version
  (`coin-scanner-r1-2026-10-16/<12 hex>`); `IndicatorChoice` (validated against the T27 menu, defaults filled);
  `AssetHold` and `DEFAULT_HOLDS`; the fixed exclusion reasons and flags. No library, SDK or network import.
- `src/trade_graph/adapters/market/kraken_ticker.py`: one bounded, keyless `GET /0/public/Ticker` for every pair
  (4 MiB limit, no redirects, strict JSON, rate-limit answers as their own failure, fixed failure categories,
  receipt time taken after the answer); `quotes_by_symbol` matches Kraken's result keys to catalog pairs the way
  the OHLC adapter does and gives no quote when a match is ambiguous; `load_ticker_fixture` reads a labelled
  fixture and refuses a live label.
- `src/trade_graph/application/coin_scanner/inputs.py`: one pair's inputs and checks, read point-in-time (or by
  event time in a replay) through `CandleStore.window` and the T27 indicator engine: anchor hour, 24-hour USD
  volume (exact), NATR swing and swing relative to the round-trip fee, ROCP trend, the price-peg check for
  unreviewed coins, the ticker spread, each with its source time and provenance.
- `src/trade_graph/application/coin_scanner/ranking.py`: the pure rank-sum ranking and the shortlist.
- `src/trade_graph/application/coin_scanner/scanner.py`: `CoinScanner.scan` (live or replay), the evidence
  label, the optimism reasons and the scan document; `ScanResult.entries()` holds the shortlist with every input.
- `src/trade_graph/application/coin_scanner/store.py`: `CoinScanStore` (`record`, `get`, `latest`,
  `coin_rule`) and `read_latest_scan` for read-only connections.
- `src/trade_graph/adapters/persistence/coin_scan_schema.py`, `migrations/versions/0031_coin_scans.py`, and two
  lines in `adapters/persistence/migrate.py`: state migration 0031 (three append-only tables).
- `src/trade_graph/market_data_cli/coin_scanner.py`: `scan-coins` (one scan, `--scans N --at-minute 5` for hourly
  repeats, `--replay --as-of T --scans N` for past hours, `--fixture-ticker`) and `coin-shortlist` (read-only,
  plain text or JSON). Discovered like the other candle commands; `cli.py` is unchanged.
- `docs/COIN-SCANNER.md`: the rules, inputs, exclusions, peg check, ranking, record, coin rule, replays,
  commands and measurements. `docs/PAIR-CATALOG.md`: one sentence now points to the peg check.
- Tests: `tests/unit/test_coin_scanner_rules.py`, `tests/unit/test_coin_ranking.py`,
  `tests/unit/test_kraken_ticker.py`, `tests/property/test_coin_ranking_properties.py`,
  `tests/integration/test_coin_scanner.py`, `tests/integration/test_coin_scanner_recorded.py`,
  `tests/integration/test_coin_scan_store.py`, `tests/integration/test_coin_scanner_cli.py`,
  `tests/integration/test_migration_0031.py`; helpers in `tests/coin_scanner_support.py`; the synthetic ticker
  fixture and its README in `tests/fixtures/coin_scanner/`.

## Contracts other packages use

- **W3-A (T32), the coin rule (D138 item 3).** `CoinScanStore(database, clock).coin_rule(symbol, as_of=...,
  max_age=timedelta(hours=2))` returns a `CoinRuleAnswer`: `passed_checks` (`passed` True, with its place),
  `excluded` (False, with the reasons), `not_in_catalog` (False), or unknown (`passed` None): `no_recent_scan`
  when the newest live scan is older than `max_age`, `scan_failed` when the newest live scan failed. It reads only
  live scans. `ScanResult.passed_symbols()` gives the same set for a scan in memory. W3-A decides what unknown
  means for new entries.
- **W6-A (T37), Research.** `CoinScanStore.latest()` returns the newest live scan (`StoredScan.entries`: the
  shortlist, each entry with every input, its source time, flags and the ranking version). Replays are returned
  only with `mode="replay"`. A finding that cites the shortlist can cite the `scan_id`.
- **W5-A (T35) / W6-D (C2), scheduling.** Run one scan per hour after the hour's candle is collected: call
  `CoinScanner(...).scan(...)` and `CoinScanStore.record(...)`, or run `trade-graph scan-coins`. The scanner
  makes no request of its own; the command makes one Ticker request per scan.

## Decisions (reserved block D170–D174)

- **D170 (2026-10-16, implementation default for T31): the inputs.** Candles are the collector's 60-minute series
  (`kraken_public_ohlc`), read point-in-time through `CandleStore.window` and the T27 engine. Each pair's window
  ends at its newest stored completed hour, at most 2 hours behind the scan hour (flag `candles_lagging`; further
  behind is `missing_candles`), like D129's 2-hour rule. Volume is the exact sum of volume × vwap over the last
  24 hourly candles (volume × close when a traded candle has no vwap, as in Kraken's archive; the basis is
  recorded); any missing hour withholds it. Swing is TA-Lib `NATR` 14 on hourly candles, and swing relative to
  cost is NATR ÷ (round-trip rate × 100) with the maker round trip of the cost profile in force on the scan's day
  (default `kraken-spot-starting-tier`, 0.80%, W1-B). NATR is the menu's only swing already measured as a share
  of the price, so the rules refuse another swing indicator rather than divide a price-unit swing in-house. It is
  the typical hourly move, smaller than D126's daily figure (BTC 2.7%); the ranking only compares coins with each
  other. Trend is `ROCP` 24 (the 24-hour change). Spread is (ask − bid) ÷ mid from one public, keyless Kraken
  Ticker request for every pair per live scan; the answer may be at most 300 seconds old and not newer than the
  scan moment, and a key that could name two pairs gives no quote. Library floats are rounded half-even to 12
  significant digits before any comparison (D136). Reading of D118: the scanner's own code sums money, compares
  library outputs with levels, writes the fee in NATR's unit and ranks; these are the D120 ranking rules, not
  indicator formulas. A pair the store or engine refuses is `evaluation_failed`, and the other pairs carry on.
  Reason: D120's four measures from menu indicators and the W1-B fees, point-in-time, with no guessed input.
- **D171 (2026-10-16, implementation default for T31): exclusions and the thin-pair thresholds.** Excluded
  without reading candles: a catalog status other than `online` (D133), the catalog's stablecoin and yield-token
  marks (D131, D127), fiat currencies (D133), and a hold list of U, STBL, UNITAS, CAP, DEBIT, RE and ST, the names
  D133 left for the owner's eye (USDE is already marked a stablecoin), held as `held_for_review` until someone
  reviews them; removing one is a rules revision. Riba (D127): the catalog holds only spot currency pairs
  (futures, perpetuals and margin are not in it and leverage fields are ignored), and staking, lending and
  rebasing tokens are the yield marks; no other riba product type exists in Kraken's spot list as parsed. Thin:
  under USD500,000 traded in 24 hours (D138's USD2,000 per coin needs USD20,000 an hour at the practice range's 10%
  share of a bar, D148: USD480,000 a day, rounded up) or a spread above 0.50% of the mid (a protective exit then
  pays 0.25% to cross, below one side's 0.40% maker fee). Any missing input excludes the pair with its reason;
  nothing is guessed. Reason: D120's "exclude thin pairs", D131 and D127, conservative where a coin is unknown.
- **D172 (2026-10-16, implementation default for T31): the price-peg check.** For every pair whose base asset the
  pair catalog never reviewed (608 of 677 USD coins on 2026-10-13), the scanner reads TA-Lib `HIGHEST_HIGH` 72 and
  `LOWEST_LOW` 72 on hourly candles. `price_peg` when the low is at least 0.98 and the high at most 1.02 USD;
  `price_pinned` when the high is at most 2% above the low, which also catches a peg to another currency (no
  exchange rate needed) and a market with no trades; either excludes. Without 72 complete hours the check is
  `peg_check_unavailable`, which excludes too. A coin that passes keeps the flag `not_individually_reviewed` on
  the shortlist, because a price check is not a review. Reviewed coins, including the gold tokens PAXG and XAUT,
  are not checked. Reason: a list cannot recognise a stablecoin it has never seen (PAIR-CATALOG.md), and D131
  excludes stablecoins whatever their name.
- **D173 (2026-10-16, implementation default for T31): ranking and its version.** Method `rank-sum-v1`: each
  passing pair gets a place per factor (higher is better for volume, swing relative to cost and trend, lower for
  spread; equal values share a place and the next is skipped), the score is the sum of the four places so the
  factors count equally, a lower score ranks first, and ties go to more traded volume and then the symbol. The
  shortlist is the first 10 places and is never padded. The ranking version is
  `coin-scanner-r<revision>-<date>/<first 12 hex of the SHA-256 of the canonical rules>`, so any change to a
  threshold, setting, profile, hold or method is a new version. Property tests show the order never depends on
  the input order and a better value never moves a pair down. Reason: D120 names four equal criteria and no
  weights; a rank sum needs no scale for volumes against percentages and is easy to explain.
- **D174 (2026-10-16, implementation default for T31): the record, the coin rule, replays and commands.** State
  migration 0031 adds `coin_scans` (one row per scan, live or replay, with its document and SHA-256),
  `coin_scan_pairs` (every pair that passed, with its place and values) and `coin_shortlist_entries` (every input
  and source time). Triggers make all three append-only; a replay row must be optimistic and a live row never is;
  a failed scan names its reason; places exist only for a scan that ranked something and inside its counts; an
  entry needs a shortlisted place; a read whose document no longer matches its hash is refused. Failed scans are
  recorded too. The tables are not added to agent context, the financial checkpoint, the runtime evidence or the
  review export. `latest()` returns live scans only unless a replay is asked for by name. `coin_rule` (above) is
  the D138 item 3 contract for W3-A, with a 2-hour freshness default. A replay reads a named source by event time,
  takes no ticker, ranks without spread and is always labelled optimistic, with the reasons
  `delisted_pairs_missing` and `no_historical_spread`, plus `catalog_observed_after_the_replayed_moment` and
  `cost_profile_after_the_replayed_moment` when those apply. A scan's evidence label is the weakest of its candle
  source, its ticker answer and every collection run behind its numbers. `scan-coins` needs an initialised paper
  database in a 0700 folder and an existing candle file, refuses the financial database as a candle file (the
  D133 guard) and creates neither; `coin-shortlist` opens the database read-only. Reason: the T31 deliverable
  "persisted shortlist with every input value, source time and ranking version", and the D138 coin rule.

## Evidence

All runs on CPython 3.12.13 (`uv sync --frozen --group dev --python 3.12`) in this cloud container. No network,
key, paid, model or exchange call in any test; tests that could reach the network block it.

- Tests written first: each new test module was written before its code and failed at collection
  (`ModuleNotFoundError: No module named 'trade_graph.application.coin_scanner'`, `... 'trade_graph.adapters.
  market.kraken_ticker'`, `... 'trade_graph.adapters.persistence.coin_scan_schema'`); the ticker test was
  re-checked by moving the adapter away. On their first runs against the new code, two failures were real and
  fixed: the scan document of a failed scan carried a `Decimal` that JSON could not store, and one synthetic pair
  meant to be the weakest was below the volume threshold (test data). Mutation checks: reading live candles in
  replay mode makes `test_a_candle_received_after_the_scan_moment_is_not_seen` fail; dropping the peg check makes
  `test_each_excluded_pair_names_exactly_why` fail.
- `uv run ruff check src tests`: all checks passed.
- New tests: `uv run pytest -p no:cacheprovider -rf tests/unit/test_coin_scanner_rules.py
  tests/unit/test_coin_ranking.py tests/unit/test_kraken_ticker.py tests/property/test_coin_ranking_properties.py
  tests/integration/test_coin_scanner.py tests/integration/test_coin_scanner_recorded.py
  tests/integration/test_coin_scan_store.py tests/integration/test_coin_scanner_cli.py
  tests/integration/test_migration_0031.py`: **75 passed, 0 failed, 0 skipped**.
- Related existing tests (migrations and their newest-version checks, state-database users, candle store,
  catalog, indicator engine, cost profiles, public market, the market-data commands and registry, import
  boundary, offline-module checks, database locks and privacy, financial checkpoint and recovery, review export,
  runtime evidence, paper service): `uv run pytest -p no:cacheprovider -rf
  tests/integration/test_migration_0025.py test_migration_0026.py test_migration_0028.py test_migration_0029.py
  test_owner_checkins.py test_owner_expenses.py test_owner_expense_upgrade.py test_database_threads.py
  test_scheduler_recovery.py test_native_incident_resolution.py test_progress_projection.py
  test_candle_collection_isolation.py test_collect_candles_cli.py test_archive_cli.py test_backfill_cli.py
  test_news_alarm_cli.py test_kraken_catalog.py test_candle_store.py test_indicator_engine.py
  test_public_market.py test_database_locks.py test_database_privacy.py test_financial_checkpoint.py
  test_financial_recovery.py test_review_export_safety.py test_runtime_evidence.py test_paper_service.py
  tests/unit/test_market_data_cli_registry.py test_import_boundary.py test_trades_modules_stay_offline.py
  test_archive_modules_stay_offline.py test_cost_profiles.py test_pair_classification.py
  test_pair_catalog_contract.py test_indicator_contract.py` (35 modules, paths shortened here): **1070 passed,
  0 failed, 1 skipped** (`tests/archive_support.py:104`, "this user can read a file whatever its mode": the
  container runs as root).
- `python3 scripts/check_plan.py`: plan valid, 46 tasks, acyclic. `python3 scripts/test_planning.py`: 11 tests
  OK. `python3 scripts/check_repository_hygiene.py` (and `--staged` before every commit): passed.
- Full suite: not run (night shift: the wave integration runs it).
- Real or synthetic: `tests/integration/test_coin_scanner_recorded.py` uses the **recorded public fixtures** of
  2026-10-13 (the 53-pair Kraken pair list and the 120-candle hourly series of BTC/USD, SOL/USD, LINK/USD and
  USDUC/USD): the same inputs give the same scan, the catalog exclusions match the recorded marks, reviewed coins
  without candles are `missing_candles`, the recorded volume is the exact sum of Kraken's volume × vwap, the trend
  is the 24-hour change, and five days of candles leave NATR `warming_up`, so no pair is ranked. Everything else
  is **synthetic** and labelled: the ticker fixture (Kraken's format, made-up prices) and the generated hourly
  candles. kraken.com and api.kraken.com were refused by the cloud network policy on 2026-10-16 (`CONNECT tunnel
  failed, response 403`), so no Ticker answer could be recorded. No paid, model, exchange or private call was made.
- Measured (synthetic, this container): one scan of 100 pairs with 760 hourly candles each took about 4.5 s, so
  about 590 eligible pairs is about 25–30 s; almost all of it is the candle store hydrating the 710 candles NATR
  reads per pair. Stored for 25 pairs (12 passing): scan document 4 KB, places 2.3 KB, ten shortlist entries 31 KB.

## Schema and dependencies

- State migration: **0031** (W2-B's reservation), registered in `migrate.py` right after 0029 with a comment that
  0030 and 0032 belong to W2-A and W2-C. The Alembic revision `0031` has `down_revision = "0029"`; **the
  integrator re-links it to 0030 after merging W2-A** (W2-C's 0032 then follows 0031).
  `tests/integration/test_migration_0031.py` checks `down_revision` against the version registered just before
  0031 in `migrate.STATEMENTS`, so it passes once re-linked. Candle database: no change.
- Dependencies added: none (`pyproject.toml` and `uv.lock` unchanged).

## Not done, open or for someone else

- First real scan and one recorded Ticker answer: local step L2. Run `trade-graph scan-coins` once on the owner's
  PC after a candle collection, and record one `GET /0/public/Ticker` answer as a fixture. The swing needs about
  30 days of hourly collector candles per pair (NATR 14 reads 710), so the collector must have kept its 60-minute
  series (L1).
- Running the scan every hour inside the service, after each candle collection: W5-A (T35) or W6-D (C2). Until
  then `scan-coins --scans N` or a scheduled task.
- Research reading the shortlist: W6-A (T37). The money limits using `coin_rule`: W3-A (T32). HQ showing the
  shortlist: a later HQ package.
- Owner, optional: review any of U, STBL, UNITAS, CAP, DEBIT, RE and ST; a reviewed coin goes into the pair rules
  (T25's table) and leaves the hold list in a new rules revision.
- Retention: rows are append-only; at about 60–80 KB per scan for the real list (estimate), hourly scans add about
  1.5–2 MB a day to the state database. A retention or compaction rule needs an owner decision later.
- Suggested source-register entry for `docs/90-SOURCES.md` (not edited here to avoid number clashes): "Kraken
  REST Get Ticker Information, https://docs.kraken.com/api-reference/market-data/get-ticker-information (the
  `pair` parameter is optional; without it every tradeable pair is returned); not reachable from the cloud on
  2026-10-16; recorded at L2".
- For the integrator, shared lines touched: `src/trade_graph/adapters/persistence/migrate.py` (one import, two
  lines after 0029, next to where W2-A adds 0030 and W2-C 0032; keep them in numeric order),
  `migrations/versions/0031_coin_scans.py` (re-link as above) and one sentence in `docs/PAIR-CATALOG.md`.
- Observed outside scope: `application/pair_classification.py`'s `VERIFICATION_NOTE`, written into every catalog
  report, still says "The scanner (T31) will add a price-peg check". Left unchanged because catalog reports and
  their tests use the text; whoever next revises the pair rules can update it.
