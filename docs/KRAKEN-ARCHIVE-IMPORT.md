# Kraken OHLCVT archive import (T26, slice A)

Status: importer and command built and tested on synthetic archives only (2026-10-13). **No real Kraken archive has
been downloaded or imported.** The importer reads a file that already sits on this computer. It never downloads.

Code: `adapters/market/kraken_archive.py` (reader), `application/import_candle_archive.py` (import and report),
`market_data_cli/archive.py` (command). Tests: `tests/unit/test_kraken_archive.py`,
`tests/integration/test_import_candle_archive.py`, `tests/integration/test_archive_cli.py`,
`tests/unit/test_archive_modules_stay_offline.py`, `tests/unit/test_archive_docs.py`. Candle rules it relies on:
[CANDLE-STORE.md](CANDLE-STORE.md). Slice B (public Trades backfill and source reconciliation) is separate.

## In plain words

Kraken publishes its price history as downloadable files. The practice range (D119) needs at least three months of
15-minute candles for many coins, and Kraken's live price request returns at most 720 recent candles. So someone
has to download Kraken's file once. That someone is the owner, or an agent the owner explicitly permits. This
program only reads the finished file from disk, checks it very carefully, and stores the candles. It does not
fetch anything from the internet, and a test enforces that.

**Delisted pairs are absent from Kraken's data, so any test of coin selection on this history is optimistic
(survivorship bias).** Every report repeats this sentence.

## What Kraken's official page says

Source: Kraken Support, [Downloadable historical OHLCVT (Open, High, Low, Close, Volume, Trades)
data](https://support.kraken.com/hc/en-us/articles/360047124832-Downloadable-historical-OHLCVT-Open-High-Low-Close-Volume-Trades-data)
(the same article is also served at
`https://support.kraken.com/articles/360047124832-downloadable-historical-ohlcvt-open-high-low-close-volume-trades-data`).
Read on 2026-10-13 through a web-fetch tool that summarises the page, twice (once per address), with identical
results; it was not opened in a browser and nothing was downloaded. The page showed "last updated October 12,
2026"; a search-engine snippet of an older copy said April 26, 2026, so re-read the page before relying on the file
list below.

| Fact | What the page says |
|---|---|
| Complete archive | `Kraken_OHLCVT_Full.zip`, offered as five parts: `Kraken_OHLCVT_Full.zip.part00` to `Kraken_OHLCVT_Full.zip.part04`. Join them into one ZIP before use. |
| Checksums | `OHLCVT_Full_PARTS_SHA256SUMS.txt` lists the parts' SHA-256. The page also gives the SHA-256 of the joined ZIP: `bc273e9027470b07d790a7fa4df9bb43f0dfc527c20c6ecae48402e123e7b1c7` (as read 2026-10-13; compare with the live page). |
| Quarterly updates | `Kraken_OHLCVT_2026Q1.zip`, `Kraken_OHLCVT_2026Q2.zip`, `Kraken_OHLCVT_2026Q3.zip`. Each is one ZIP; no joining. Published at the end of each quarter. |
| Host | Every link is on `assets.kraken.com`, under `https://assets.kraken.com/marketing/institutions/`. No other host (no Google Drive) is mentioned. |
| Sizes | Each of the five complete-archive parts is "about 2 GB", so roughly 10 GB in total. **The page gives no total and no size for the quarterly files.** |
| Coverage | The complete archive runs from each pair's first trade on Kraken through 30 June 2026. |
| Contents | One set of CSV files per currency pair, plus a `MANIFEST.json` describing the build (its fields are not described). |
| CSV columns | `timestamp, open, high, low, close, volume, trades`, in that order. **No header row.** |
| Intervals | 1, 5, 15, 60, 240, 720 and 1440 minutes. The page is inconsistent about 30 minutes: one section lists it, another does not. |
| Gaps | A row exists only for an interval in which trades occurred. Quiet intervals are not zero-filled. |

### Unconfirmed

The page does **not** say the following. The reader treats each as an assumption, is strict about it, and the
report lists these assumptions every time. Treat them as open until a real file has been inspected.

- The timestamp unit (seconds or milliseconds) and whether it marks the candle's open or close. Assumed: Unix seconds
  and the candle open, as in Kraken's REST OHLC. A millisecond value falls outside the accepted range
  (2010-01-01 to 2100-01-01 in seconds) and is rejected row by row, so a wrong unit refuses the file instead of
  loading shifted data. The open-versus-close question cannot be seen in the file; slice B's overlap check
  against the collector's candles is how it gets settled.
- Whether every row is a completed candle, including the newest row of a quarterly file. Rows closing after the
  import time are dropped and counted.
- File names inside the ZIP. A third-party guide (Concretum Group, undated, 2026, not official) shows
  `XBTUSD_1.csv`, `XBTUSD_5.csv`, `ETHUSD_1.csv` and `ADAEUR_1440.csv` and says the timestamps are Unix seconds.
  It also says some archives contain folders (or nested archives). The reader expects `PAIR_INTERVAL.csv` flat at
  the top level and **refuses a ZIP that has folders**, naming the member. If the real archive has folders the first
  run will refuse; the fix is a deliberate, reviewed change to the name policy, not a silent widening.
- Row order (assumed ascending), the presence of a 30-minute set, whether a quarterly file holds only that
  quarter's rows (the report counts rows outside the stated quarter, `rows_outside_quarter`), and whether delisted
  pairs appear at all (assumed absent, per D119).

## What the importer does

1. **Fingerprint.** The file name, size and SHA-256 are computed by streaming. A directory of CSV files (used by tests)
   is hashed from its files' names, sizes and SHA-256 values. The quarter comes from the name (`2026Q3`, `Q3_2026`,
   `2026-q3`) or from `--quarter`; a name like `Kraken_OHLCVT_Full.zip` is a complete archive with no quarter.
2. **Select.** Only USD-quoted pairs at the chosen intervals (default 15 and 60 minutes; 1 and 5 are opt-in
   because they are very large; the other intervals are skipped and counted). Other quote currencies, files
   not requested through `--pairs`, and unrecognised members are counted. Stablecoin pairs such as `USDT/USD` are
   imported if present; the exclusion in D131 belongs to the scanner (T31) using the collector's catalog marks.
   Pass `--pairs` to keep any pair out of storage.
3. **Scan (pass one).** Every in-scope row is parsed and checked; nothing is written. The file is refused whole if
   the rejected share is above 1% (or there are no rows, or nothing in scope). A refused file leaves the store
   untouched but leaves a failed run record.
4. **Stamp.** The injected clock is read once. That moment is `available_at` for every row of this import: the
   process obtained the data then, not at the historical close (CANDLE-STORE rule 2). Rows whose close is after
   it are dropped and counted (`dropped_unfinished`). `point_in_time` reads before the import time see nothing;
   `replay` reads (practice range) see the data and are labelled `event_time_only`.
5. **Write (pass two).** Rows are saved in batches of 10,000. Pass one keeps a SHA-256 of each file's accepted rows,
   in order, as it stood at the end of every batch. Before a batch is saved, the SHA-256 of every row read so far
   must equal pass one's for that point; if not, the import stops (`changed_during_import`) and that batch is not
   saved. The size and modification time are compared before and after the write pass, and each file's counts must
   repeat exactly. So anything already saved came from rows that read the same in both passes, even when a rewrite
   keeps the number of rows, but a failed `changed_during_import` run can leave earlier batches in the store (it is
   append-only); the run is recorded as failed and the report says so.
6. **Report.** One run (kind `archive_import`, evidence label `local_archive_file`; tests use `synthetic_fixture`)
   holds the parameters (file name, size, SHA-256, quarter, scope, limits) and the full report.

### Symbols

`symbol_for_pair_code` is the one place pair names are read: `XBTUSD` becomes `BTC/USD`, `XDGUSD` becomes `DOGE/USD`,
legacy `XXBTZUSD` becomes `BTC/USD`, everything else ending in `USD` keeps its base (`1INCHUSD` becomes
`1INCH/USD`). The venue pair code is kept as `source_pair` (a stored series can never change it). The collector
stream reads names from Kraken's AssetPairs catalog; this function is isolated so it can be replaced by a catalog
lookup. Two files mapping to one series (for example `XBTUSD_15.csv` and `XXBTZUSD_15.csv`) refuse the file
(`ambiguous_pair_codes`).

### Rows rejected, with counted reasons

A bad row is dropped and counted under one of these reasons (per pair and interval, and in total):

| Reason | Meaning |
|---|---|
| `malformed_row` | not exactly seven plain fields, non-ASCII, a header or blank line, non-numeric, `NaN`, underscores, quotes, fractions in the timestamp or trade count |
| `timestamp_out_of_range` | outside 2010-01-01 to 2100-01-01 in Unix seconds (a millisecond timestamp lands here) |
| `off_grid` | not a multiple of the interval, counted from the epoch |
| `duplicate_timestamp` | same time as the previous accepted row; the first row is kept |
| `out_of_order` | earlier than the previous accepted row |
| `non_positive_price` | open, high, low or close at or below zero |
| `negative_volume` | volume below zero |
| `negative_trades` | trade count below zero |
| `ohlc_out_of_bounds` | open or close outside the high-low range, or high below low |

The row checks are at least as strict as the candle contract, so an accepted row always builds a `Candle`. Prices and
volumes are exact `Decimal` values (plain or `e` notation, at most 20 digits per part); nothing passes through a
float. `vwap` is not in the archive and is stored empty. Rows are never filled, carried forward or interpolated.

### Why a whole file is refused

These are the stable `reason` codes in a failed report. Each leaves the run recorded as `failed`.

| Reason | Cause |
|---|---|
| `not_a_file` | the path is missing, unreadable, or neither a file nor a directory |
| `multipart_archive` | the file is one `.zip.partNN` piece; join the parts first |
| `not_a_zip` | the file is not a readable ZIP |
| `unsafe_member_name` | a member name is not a plain flat file name (separators, `..`, absolute path, drive letter, spaces, control or non-ASCII characters, leading dot, over 128 characters) |
| `unsafe_member_type` | a member is a symbolic link or other special entry (or a folder, for a directory source) |
| `duplicate_member_name` | two members share a name |
| `unsupported_member` | an in-scope member is encrypted or uses a compression other than stored or deflate |
| `corrupt_member` | a member is damaged or truncated (failed checksum or unreadable data) |
| `member_limit` | more members than the limit |
| `member_size_limit` | an in-scope member declares or inflates past the per-member limit |
| `total_size_limit` | in-scope data inflates past the total limit |
| `row_limit` | more rows than the limit |
| `line_too_long` | a line over 256 bytes (not a candle file) |
| `compression_ratio_limit` | a member inflates more than the ratio limit beyond a 1 MiB grace (zip bomb) |
| `ambiguous_pair_codes` | two files map to one pair and interval |
| `nothing_in_scope` | no USD-pair file at the chosen intervals and pairs |
| `no_rows` | the in-scope files hold no rows |
| `rejected_share_exceeded` | rejected rows are more than 1% of rows read |
| `changed_during_import` | the file changed while it was being imported |
| `store_refused` | the candle store refused a write, for example a changed row stamped earlier than its stored revision (the clock went backwards) |

### Safety limits (defaults; all configurable through `ArchiveLimits`)

| Limit | Default |
|---|---|
| members in the archive | 50,000 |
| uncompressed size of one member | 2 GiB |
| uncompressed size in total, actually read | 16 GiB (`--max-uncompressed-gb`, up to 1024) |
| rows in total | 400,000,000 |
| compression ratio per member | 100 times (checked after 1 MiB) |
| bytes in one line | 256 |
| rejected share of rows | 1% |

Nothing is ever extracted to disk and no temporary file is written: ZIP members are streamed through a bounded
read buffer, and names are checked against an allowlist and never used to build a path. Members that are not read
(other pairs, other intervals) are never decompressed. A test with sockets disabled, and a static test over the
modules and everything they import, fail if the importer touches `socket`, `http`, `urllib`, `httpx`, `requests`,
`ssl` or similar.

## Reports and exit codes

`trade-graph import-kraken-archive` prints one JSON document. Top level: `run_id`, `status`, `archive` (name, size,
SHA-256, format, kind, quarter, members, USD pairs found), `scope`, `imported_at_utc`, `totals`
(rows read, accepted, rejected, dropped, inserted, duplicates, revisions, rows outside the quarter), `rejected_by_reason`,
`rejected_share`, `skipped` (counts of non-USD files, files not requested, intervals not selected, unrecognised member
names), `same_file_seen_in_runs`, `expected_pairs`, `assumptions`, `survivorship_notice` and `series`.

Each entry in `series` (one per pair and interval) has rows read, accepted, rejected with reasons, dropped, `inserted`,
`duplicates`, `revisions`, first and last open time, and `coverage`: the candle store's own coverage over the imported
range (expected, present and missing slots, longest run, and the missing ranges). At most 25 ranges are listed (the
largest, in time order) with `missing_ranges_total` giving the real count. A missing slot is reported as "no candle";
the report cannot tell a quiet interval from a row absent from the file and does not guess.

`status`: `ok` when every in-scope row was stored or was an identical duplicate; `partial` when some rows were rejected
or dropped; `failed` for a refused file. Exit code 0 for `ok` and `partial`, 1 for a refusal (the JSON names the
reason and the recorded run), 2 for a usage error.

### Repeat and changed imports

Importing the same file again inserts nothing: every row is a duplicate, the original receipt times stay, and the
new run lists the earlier run in `same_file_seen_in_runs`. A different file (for example a corrected quarter)
saves a new revision only for rows whose content changed, stamped with the new import time; earlier revisions stay
readable, and `point_in_time` reads at an earlier time still see the earlier revision.

### Missing pairs and survivorship

`expected_pairs` names the pairs the caller expected but the archive does not contain at all (`missing`) and pairs
present only at some of the selected intervals (`missing_intervals`). The command takes the list from
`--expected-pairs`, or else from the newest stored collector catalog; with neither it says it cannot name missing
pairs. A pair that is absent because Kraken delisted it cannot be told apart from one missing for any other reason,
which is why the survivorship sentence above is always printed.

## Owner must download it; the importer reads only a local file

Nothing in this repository downloads the archive, and no agent has. Doing so is the owner's decision, or an agent's
only with the owner's explicit yes (a download of this size is a file transfer of about 10 GB for the complete archive).

1. **Choose.** If a quarterly file holds just its own quarter (unconfirmed, see above), `Kraken_OHLCVT_2026Q3.zip`
   alone gives three months of history to 30 September and `Kraken_OHLCVT_2026Q2.zip` with it gives six, which suits
   D119's tune-then-confirm split better, and both avoid the 10 GB download (their sizes are not stated on the page;
   check before downloading). Choose the complete archive only if years of history from each pair's first trade are
   wanted. Days after the newest file are slice B's job (public Trades) and the collector's.
2. **Download in a browser** from the links on the Kraken page above (host `assets.kraken.com`). For the complete
   archive, all five `.zip.part00` to `.zip.part04` files and `OHLCVT_Full_PARTS_SHA256SUMS.txt`.
3. **Check and join** (WSL), then compare with the SHA-256 printed on Kraken's page:

   ```bash
   sha256sum -c OHLCVT_Full_PARTS_SHA256SUMS.txt
   cat Kraken_OHLCVT_Full.zip.part0* > Kraken_OHLCVT_Full.zip
   sha256sum Kraken_OHLCVT_Full.zip
   ```

4. **Keep it private and out of Git**, for example in `runtime/archives/` (ignored by Git).
5. **Import** (a first look with one pair is wise):

   ```bash
   uv run --frozen trade-graph import-kraken-archive --archive runtime/archives/Kraken_OHLCVT_2026Q3.zip --pairs BTC/USD
   uv run --frozen trade-graph import-kraken-archive --archive runtime/archives/Kraken_OHLCVT_2026Q3.zip
   ```

   Add `--intervals 1,5,15,60` for finer history (large). Re-running is safe.
6. **Send back the first report** (or the refusal). A refusal in the first minutes usually means one of the
   unconfirmed layout assumptions above was wrong, and the report says which member and why.

The first import of a real file is the first real evidence for this stream and should be recorded as such. Everything
tested so far used synthetic data; a synthetic or mocked import is not evidence that Kraken's real file loads.

## Measured on synthetic data

On the development machine (WSL, 2026-10-13): a synthetic ZIP of 12 pairs at 15 and 60 minutes over 365 days
(525,600 candles, 9.8 MB zipped) imported in 24 s (about 22,000 candles per second including both passes and the
report); an identical re-import took 18.5 s; the candle database took 56.5 MB (about 107 bytes per candle). Real files
differ in size and row mix; treat these as order-of-magnitude rates, not a forecast.
