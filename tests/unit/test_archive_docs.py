"""The import document cannot drift from the code: constants, reasons and defaults it quotes must still be true."""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

from trade_graph.adapters.market.kraken_archive import GIB, REJECTION_REASONS, ArchiveLimits
from trade_graph.application.import_candle_archive import (
    MAX_GAPS_LISTED,
    SURVIVORSHIP_NOTICE,
    UNCONFIRMED_ASSUMPTIONS,
)

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "KRAKEN-ARCHIVE-IMPORT.md").read_text(encoding="utf-8")
SOURCES = [ROOT / "src" / "trade_graph" / part for part in (
    "adapters/market/kraken_archive.py", "application/import_candle_archive.py", "market_data_cli/archive.py")]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_survivorship_sentence_is_in_the_document_word_for_word() -> None:
    assert SURVIVORSHIP_NOTICE in _flat(DOC)


def test_every_refusal_reason_in_the_code_is_documented() -> None:
    reasons = set()
    for path in SOURCES:
        reasons |= set(re.findall(r'ArchiveRejected\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    reasons.add("store_refused")
    assert len(reasons) >= 18
    missing = sorted(reason for reason in reasons if f"`{reason}`" not in DOC)
    assert not missing, f"undocumented refusal reasons: {missing}"


def test_every_row_rejection_reason_is_documented() -> None:
    assert all(f"`{reason}`" in DOC for reason in REJECTION_REASONS)


def test_documented_defaults_match_the_limits() -> None:
    limits = ArchiveLimits()
    assert f"{limits.max_members:,}" in DOC and f"{limits.max_rows:,}" in DOC
    assert limits.max_member_bytes == 2 * GIB and "2 GiB" in DOC
    assert limits.max_total_bytes == 16 * GIB and "16 GiB" in DOC
    assert limits.max_compression_ratio == 100 and "100 times" in DOC
    assert limits.max_line_bytes == 256 and "256 bytes" in DOC
    assert str(limits.max_rejected_share) == "0.01" and "1%" in DOC
    assert f"At most {MAX_GAPS_LISTED} ranges" in _flat(DOC)
    assert set(asdict(limits)) == {"max_members", "max_member_bytes", "max_total_bytes", "max_rows",
                                   "max_compression_ratio", "max_line_bytes", "max_rejected_share"}


def test_the_research_facts_and_the_owner_download_section_are_present() -> None:
    for expected in (
        "https://support.kraken.com/hc/en-us/articles/360047124832", "Kraken_OHLCVT_Full.zip.part00",
        "Kraken_OHLCVT_Full.zip.part04", "OHLCVT_Full_PARTS_SHA256SUMS.txt", "Kraken_OHLCVT_2026Q1.zip",
        "Kraken_OHLCVT_2026Q3.zip", "assets.kraken.com", "about 2 GB", "No header row", "### Unconfirmed",
        "## Owner must download it; the importer reads only a local file", "never downloads",
    ):
        assert expected in DOC, expected


def test_the_stated_assumptions_are_the_ones_the_report_prints() -> None:
    flat = _flat(DOC)
    for key in ("Unix seconds", "candle open", "completed candle", "folders", "ascending"):
        assert key in flat
    assert len(UNCONFIRMED_ASSUMPTIONS) == 3
