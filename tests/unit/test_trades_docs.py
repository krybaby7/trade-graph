"""The backfill document cannot drift from the code: constants, reasons, defaults and the measured numbers it quotes."""

from __future__ import annotations

import math
import re
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from trade_graph.adapters.market.kraken_trades import MAX_TRADES_PER_PAGE
from trade_graph.adapters.market.trades_rate_limit import LimiterSettings
from trade_graph.application.backfill_trades import (
    MAX_PAGE_RETRIES,
    MAX_RANGES_LISTED,
    MAX_REQUESTS_LIMIT,
    MAX_TRANSPORT_RETRIES,
    NOTES,
    STOP_REASONS,
)
from trade_graph.application.reconcile_candles import COMPARED_FIELDS, DEFAULT_SOURCE_PRECEDENCE
from trade_graph.market_data_cli.backfill import DEFAULT_MAX_REQUESTS, SMOKE_MAX_REQUESTS

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "KRAKEN-TRADES-BACKFILL.md").read_text(encoding="utf-8")
ADAPTER = (ROOT / "src" / "trade_graph" / "adapters" / "market" / "kraken_trades.py").read_text(encoding="utf-8")
FLAT = re.sub(r"\s+", " ", DOC)
BTC_PER_DAY, BAT_PER_DAY = 164_273, 13_680


def test_the_official_pages_and_the_review_date_are_cited() -> None:
    for expected in ("https://docs.kraken.com/api/docs/rest-api/get-recent-trades",
                     "https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits", "reviewed 2026-10-13",
                     "EAPI:Rate limit exceeded", "EService: Throttled", "tests/fixtures/kraken/README.md"):
        assert expected in DOC, expected


def test_every_stop_reason_is_documented() -> None:
    missing = [reason for reason in STOP_REASONS if f"`{reason}`" not in DOC]
    assert not missing, missing
    assert "`not_started`" in DOC


def test_every_page_refusal_reason_in_the_adapter_is_documented() -> None:
    reasons = set(re.findall(r'Trades(?:PageRefused|RateLimited|Throttled)\(\s*"([a-z_]+)"', ADAPTER))
    assert len(reasons) >= 14
    missing = sorted(reason for reason in reasons if f"`{reason}`" not in DOC)
    assert not missing, missing


def test_documented_defaults_match_the_code() -> None:
    settings = LimiterSettings()
    assert (settings.max_counter, settings.decay_per_second, settings.cost) == (Decimal(15), Decimal("0.33"), 1)
    assert "maximum 15, decay 0.33 per second, 1 per call, with 2 points of headroom (never above 13)" in FLAT
    assert settings.headroom == 2 and settings.max_counter - settings.headroom == 13
    assert settings.min_interval_seconds == 1 and "at least 1 second between calls" in FLAT
    assert settings.max_wait_seconds == 120 and "120 seconds" in DOC
    fall = (settings.max_counter - (settings.ceiling - settings.cost)) / settings.decay_per_second
    assert "9.091" in DOC and fall.quantize(Decimal("0.001"), ROUND_CEILING) == Decimal("9.091")
    assert MAX_PAGE_RETRIES == 2 and MAX_TRANSPORT_RETRIES == 2 and "three times (two back-off retries)" in DOC
    assert (DEFAULT_MAX_REQUESTS, SMOKE_MAX_REQUESTS, MAX_REQUESTS_LIMIT) == (100, 3, 100_000)
    assert "default 100, at most 100,000; `--smoke` 3" in DOC
    assert MAX_TRADES_PER_PAGE == 1000 and "1000 per request" in DOC
    assert MAX_RANGES_LISTED == 25 and "25 largest empty ranges" in DOC


def _duration(seconds: Decimal) -> str:
    if seconds < 120:
        return f"{round(seconds)} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min"


def test_the_cost_tables_follow_from_the_measured_trade_counts() -> None:
    seconds_per_request = Decimal(1) / Decimal("0.33")
    assert f"{int(Decimal(0.33) * 3600):,} requests per hour" in DOC
    assert f"| BTC/USD, a busy pair | {BTC_PER_DAY:,} | 165 | 8.3 minutes |" in DOC
    assert f"| BAT/USD, a thinner pair | {BAT_PER_DAY:,} | 14 | 42 seconds |" in DOC
    rows = re.findall(r"^\| (\d+) \| ([\d,]+) \| ([^|]+?) \| ([\d,]+) \| ([^|]+?) \|$", DOC, re.MULTILINE)
    assert [int(row[0]) for row in rows] == [1, 7, 14, 30, 90]
    for days, btc_requests, btc_time, bat_requests, bat_time in rows:
        for requests, shown, per_day in ((btc_requests, btc_time, BTC_PER_DAY), (bat_requests, bat_time, BAT_PER_DAY)):
            expected = math.ceil(int(days) * per_day / MAX_TRADES_PER_PAGE)
            assert int(requests.replace(",", "")) == expected, (days, per_day)
            assert shown == _duration(expected * seconds_per_request), (days, per_day, shown)
    assert "5 x 2,300 + 45 x 192 = 20,140 requests" in DOC
    assert 5 * math.ceil(14 * BTC_PER_DAY / 1000) + 45 * math.ceil(14 * BAT_PER_DAY / 1000) == 20_140
    assert round(20_140 * seconds_per_request / 3600) == 17


def test_the_default_precedence_and_its_reasons_are_stated() -> None:
    assert DEFAULT_SOURCE_PRECEDENCE == ("kraken_public_ohlc", "kraken_ohlcvt_archive", "kraken_public_trades")
    assert "Default order: `kraken_public_ohlc`, then `kraken_ohlcvt_archive`, then `kraken_public_trades`." in FLAT
    for field in COMPARED_FIELDS:
        assert field in DOC
    for phrase in ("never merged", "never averages", "first source in the order that has it"):
        assert phrase in FLAT, phrase


def test_the_assumptions_the_report_prints_are_the_ones_the_document_states() -> None:
    keys = {note.split(":")[0] for note in NOTES}
    assert keys == {"time_unit", "since_inclusive", "sequence_ids", "slot_rule", "trade_count", "missing_slots",
                    "survivorship"}
    for phrase in ("inclusive", "consecutive", "a trade exactly on the close belongs to the next candle",
                   "Delisted pairs have no history here"):
        assert phrase in FLAT, phrase


def test_the_id_gap_flag_and_the_first_trade_limit_are_documented() -> None:
    for phrase in ("open_candle_has_id_gap", "unknown withholds", "The trade before the first one a run receives is "
                   "unknown", "decided_this_run", "any flagged one counts"):
        assert phrase in FLAT, phrase
    assert any(note.startswith("sequence_ids:") and "first trade a run receives" in note for note in NOTES)


def test_the_note_about_the_one_shared_limiter_is_present() -> None:
    assert "One limiter (W6-D, D221)" in DOC and "rate_limit.KrakenRestLimiter" in FLAT
