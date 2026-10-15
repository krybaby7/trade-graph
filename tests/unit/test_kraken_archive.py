"""The local Kraken archive reader: strict names, streaming limits, loud row rejection. No network, no extraction."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import zipfile
import zlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.archive_support import (
    FIRST_OPEN,
    csv_line,
    csv_text,
    synthetic_rows,
    unreadable,
    write_directory,
    write_zip,
)

from trade_graph.adapters.market.kraken_archive import (
    CHUNK_BYTES,
    REJECTION_REASONS,
    ArchiveLimits,
    ArchiveMember,
    ArchiveRejected,
    MemberReader,
    ReadBudget,
    fingerprint_archive,
    is_fiat_symbol,
    open_archive,
    parse_member_name,
    parse_row,
    symbol_for_pair_code,
)
from trade_graph.contracts.candles import Candle, from_unix

GOOD = f"{FIRST_OPEN},100.10,101,99.5,100.75,12.50000000,7"


# --- pair codes and member names ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("code", "symbol"), [
    ("XBTUSD", "BTC/USD"), ("XDGUSD", "DOGE/USD"), ("ETHUSD", "ETH/USD"), ("1INCHUSD", "1INCH/USD"),
    ("SUSD", "S/USD"), ("XXBTZUSD", "BTC/USD"), ("XETHZUSD", "ETH/USD"), ("USDTUSD", "USDT/USD"),
])
def test_usd_pair_codes_map_to_symbols(code: str, symbol: str) -> None:
    assert symbol_for_pair_code(code) == symbol


@pytest.mark.parametrize("code", ["ADAEUR", "XBTUSDT", "ETHXBT", "XBTEUR", "xbtusd", "", "USD", "XBTUSD ", "XBT/USD"])
def test_other_codes_are_not_usd_pairs(code: str) -> None:
    assert symbol_for_pair_code(code) is None


@pytest.mark.parametrize("code", ["EURUSD", "GBPUSD", "AUDUSD", "CADUSD", "CHFUSD", "JPYUSD", "AEDUSD", "NZDUSD"])
def test_currency_pairs_map_to_symbols_but_are_marked_as_not_coins(code: str) -> None:
    symbol = symbol_for_pair_code(code)
    assert symbol == f"{code[:3]}/USD" and is_fiat_symbol(symbol)


@pytest.mark.parametrize("symbol", ["BTC/USD", "DOGE/USD", "ETH/USD", "USDT/USD", "USDC/USD", "EURT/USD", "SEUR/USD"])
def test_coins_and_stablecoins_are_not_marked_as_currencies(symbol: str) -> None:
    assert not is_fiat_symbol(symbol)  # stablecoins are a scanner decision (D131), not a currency filter here


def test_member_names_split_into_pair_and_interval() -> None:
    assert parse_member_name("XBTUSD_15.csv") == ("XBTUSD", 15)
    assert parse_member_name("1INCHUSD_1440.csv") == ("1INCHUSD", 1440)
    for name in ("MANIFEST.json", "xbtusd_15.csv", "XBTUSD_15.CSV", "XBTUSD_x.csv", "XBTUSD_15.csv.bak", "XBTUSD.csv",
                 "XBTUSD_99999.csv", "_15.csv"):
        assert parse_member_name(name) is None


# --- single rows --------------------------------------------------------------------------------------------------


def test_a_good_row_keeps_exact_decimals() -> None:
    row = parse_row(GOOD.encode(), 15 * 60)
    assert not isinstance(row, str)
    assert (row.open_unix, row.open, row.high, row.low, row.close) == (
        FIRST_OPEN, Decimal("100.10"), Decimal("101"), Decimal("99.5"), Decimal("100.75"))
    assert row.volume == Decimal("12.50000000") and row.trades == 7
    assert all(isinstance(value, Decimal) for value in (row.open, row.high, row.low, row.close, row.volume))


def test_windows_line_endings_are_accepted() -> None:
    assert not isinstance(parse_row(GOOD.encode() + b"\r", 900), str)


@pytest.mark.parametrize(("line", "reason"), [
    (b"", "malformed_row"),
    (b"1,2,3", "malformed_row"),
    (GOOD.encode() + b",1", "malformed_row"),
    (f"{FIRST_OPEN},100.10,101,99.5,100.75,12.5,".encode(), "malformed_row"),
    (f"{FIRST_OPEN},abc,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN},NaN,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN},Infinity,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN},1_0,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN}, 100.10,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN},\"100.10\",101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN},1.0.0,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN},100.10,101,99.5,100.75,12.5,7.0".encode(), "malformed_row"),
    (f"{FIRST_OPEN},1e400,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    (f"{FIRST_OPEN}.5,100.10,101,99.5,100.75,12.5,7".encode(), "malformed_row"),
    ("1781200800,100.10,101,99.5,100.75,12.5,7é".encode(), "malformed_row"),
    (f"{FIRST_OPEN * 1000},100.10,101,99.5,100.75,12.5,7".encode(), "timestamp_out_of_range"),  # milliseconds
    (b"-900,100.10,101,99.5,100.75,12.5,7", "timestamp_out_of_range"),
    (b"0,100.10,101,99.5,100.75,12.5,7", "timestamp_out_of_range"),
    (f"{FIRST_OPEN + 1},100.10,101,99.5,100.75,12.5,7".encode(), "off_grid"),
    (f"{FIRST_OPEN + 60},100.10,101,99.5,100.75,12.5,7".encode(), "off_grid"),
    (f"{FIRST_OPEN},0,101,99.5,100.75,12.5,7".encode(), "non_positive_price"),
    (f"{FIRST_OPEN},100.10,101,-1,100.75,12.5,7".encode(), "non_positive_price"),
    (f"{FIRST_OPEN},100.10,101,99.5,100.75,-0.1,7".encode(), "negative_volume"),
    (f"{FIRST_OPEN},100.10,101,99.5,100.75,12.5,-1".encode(), "negative_trades"),
    (f"{FIRST_OPEN},100.10,100.5,99.5,100.75,12.5,7".encode(), "ohlc_out_of_bounds"),  # close above high
    (f"{FIRST_OPEN},99,101,99.5,100.75,12.5,7".encode(), "ohlc_out_of_bounds"),  # open below low
    (f"{FIRST_OPEN},100,99,101,100,12.5,7".encode(), "ohlc_out_of_bounds"),  # high below low
])
def test_bad_rows_name_their_reason(line: bytes, reason: str) -> None:
    assert parse_row(line, 15 * 60) == reason


@pytest.mark.parametrize("line", [
    f"{FIRST_OPEN},100,100,100,100,0,0",
    f"{FIRST_OPEN},0.00000001,0.00000002,0.00000001,0.00000002,1e-8,1",
    f"{FIRST_OPEN},99999999999999999999.99999999999999999999,99999999999999999999.99999999999999999999,"
    "99999999999999999999.99999999999999999999,99999999999999999999.99999999999999999999,1.5e99,999999",
    f"{FIRST_OPEN},0.00000000000000000001e-99,0.00000000000000000001e-99,0.00000000000000000001e-99,"
    "0.00000000000000000001e-99,0.00000000000000000001e-99,1",
])
def test_every_accepted_row_builds_a_candle(line: str) -> None:
    """The reader's own checks must be at least as strict as the Candle contract, or an import dies late."""
    row = parse_row(line.encode(), 900)
    assert not isinstance(row, str)
    opened = from_unix(row.open_unix)
    Candle(symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=15, open_time_utc=opened,
           close_time_utc=from_unix(row.open_unix + 900), available_at_utc=datetime(2026, 12, 1, tzinfo=UTC),
           open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume, trade_count=row.trades,
           source="kraken_ohlcvt_archive")


# --- reading one member -------------------------------------------------------------------------------------------


def _reader(text: str | bytes, *, limits: ArchiveLimits | None = None, interval: int = 15) -> MemberReader:
    data = text.encode("ascii") if isinstance(text, str) else text
    limits = limits or ArchiveLimits()

    class _Source:
        def __init__(self) -> None:
            self.limits = limits

        def open(self, member):
            return io.BytesIO(data)

    member = ArchiveMember(name=f"XBTUSD_{interval}.csv", size=len(data), compressed_size=len(data),
                           pair_code="XBTUSD", interval_minutes=interval)
    return MemberReader(_Source(), member, ReadBudget(limits))


def test_a_member_is_read_in_order_with_counts() -> None:
    rows = synthetic_rows(FIRST_OPEN, 5, 15)
    reader = _reader(csv_text(rows))
    parsed = list(reader)
    assert len(parsed) == 5 and reader.rows_read == 5 and reader.accepted == 5 and not reader.rejected
    assert reader.first_open == FIRST_OPEN and reader.last_open == FIRST_OPEN + 4 * 900


def test_a_final_line_without_a_newline_is_read() -> None:
    reader = _reader("\n".join(synthetic_rows(FIRST_OPEN, 3, 15)))
    assert len(list(reader)) == 3 and reader.rows_read == 3


def test_a_second_row_for_the_same_time_is_rejected_and_the_first_is_kept() -> None:
    first = csv_line(FIRST_OPEN, "100", "101", "99", "100", "1", 1)
    clash = csv_line(FIRST_OPEN, "100", "105", "99", "104", "9", 9)
    reader = _reader(csv_text([first, clash]))
    rows = list(reader)
    assert [row.high for row in rows] == [Decimal("101")]
    assert dict(reader.rejected) == {"duplicate_timestamp": 1} and reader.rows_read == 2


def test_a_row_that_goes_back_in_time_is_rejected() -> None:
    later = csv_line(FIRST_OPEN + 1800, "100", "101", "99", "100", "1", 1)
    earlier = csv_line(FIRST_OPEN + 900, "100", "101", "99", "100", "1", 1)
    after = csv_line(FIRST_OPEN + 2700, "100", "101", "99", "100", "1", 1)
    reader = _reader(csv_text([later, earlier, after]))
    assert [row.open_unix for row in reader] == [FIRST_OPEN + 1800, FIRST_OPEN + 2700]
    assert dict(reader.rejected) == {"out_of_order": 1}


def test_a_header_row_and_blank_lines_are_counted_not_swallowed() -> None:
    lines = ["timestamp,open,high,low,close,volume,trades", *synthetic_rows(FIRST_OPEN, 2, 15), ""]
    reader = _reader("\n".join(lines) + "\n")
    assert len(list(reader)) == 2
    assert dict(reader.rejected) == {"malformed_row": 2}
    assert reader.rows_read == 4


def test_a_bad_row_does_not_advance_the_order_check() -> None:
    bad = csv_line(FIRST_OPEN + 900, "0", "101", "99", "100", "1", 1)
    ok = csv_line(FIRST_OPEN, "100", "101", "99", "100", "1", 1)
    same_slot_as_bad = csv_line(FIRST_OPEN + 900, "100", "101", "99", "100", "1", 1)
    reader = _reader(csv_text([ok, bad, same_slot_as_bad]))
    assert len(list(reader)) == 2 and dict(reader.rejected) == {"non_positive_price": 1}


def test_an_overlong_line_refuses_the_file() -> None:
    reader = _reader(b"1" * 1000 + b"\n", limits=ArchiveLimits(max_line_bytes=128))
    with pytest.raises(ArchiveRejected) as refused:
        list(reader)
    assert refused.value.reason == "line_too_long"


def test_a_member_with_no_newline_is_refused_before_it_is_buffered() -> None:
    """A hostile member without line breaks must not be collected in memory up to the per-member limit."""
    consumed: list[int] = []

    class _Counting(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            chunk = super().read(size)
            consumed.append(len(chunk))
            return chunk

    class _Source:
        limits = ArchiveLimits()

        def open(self, member):
            return _Counting(b"1" * (4 * CHUNK_BYTES))

    member = ArchiveMember(name="XBTUSD_15.csv", size=4 * CHUNK_BYTES, compressed_size=4 * CHUNK_BYTES,
                           pair_code="XBTUSD", interval_minutes=15)
    reader = MemberReader(_Source(), member, ReadBudget(_Source.limits))
    with pytest.raises(ArchiveRejected) as refused:
        list(reader)
    assert refused.value.reason == "line_too_long"
    assert sum(consumed) <= CHUNK_BYTES  # stopped after the first chunk, not after the whole member


def _declaring(text: str, declared: int, limits: ArchiveLimits) -> tuple[MemberReader, list[str]]:
    """A member whose listed size is ``declared`` whatever it really holds; the list records every open."""
    data, opened = text.encode("ascii"), []

    class _Source:
        def __init__(self) -> None:
            self.limits = limits

        def open(self, member):
            opened.append(member.name)
            return io.BytesIO(data)

    member = ArchiveMember(name="XBTUSD_15.csv", size=declared, compressed_size=len(data), pair_code="XBTUSD",
                           interval_minutes=15)
    return MemberReader(_Source(), member, ReadBudget(limits)), opened


def test_a_member_that_lists_a_small_size_but_streams_a_large_one_is_stopped_while_streaming() -> None:
    reader, opened = _declaring(csv_text(synthetic_rows(FIRST_OPEN, 50, 15)), 10, ArchiveLimits(max_member_bytes=1000))
    with pytest.raises(ArchiveRejected) as refused:
        list(reader)
    assert refused.value.reason == "member_size_limit" and "inflates" in str(refused.value)
    assert opened == ["XBTUSD_15.csv"] and reader.rows_read == 0


def test_a_member_that_lists_a_size_over_the_limit_is_refused_without_being_opened() -> None:
    small = csv_text(synthetic_rows(FIRST_OPEN, 3, 15))
    reader, opened = _declaring(small, 10_000, ArchiveLimits(max_member_bytes=1000))
    with pytest.raises(ArchiveRejected) as refused:
        list(reader)
    assert refused.value.reason == "member_size_limit" and "declares" in str(refused.value)
    assert opened == [] and reader.rows_read == 0


def test_the_row_limit_refuses_the_file() -> None:
    reader = _reader(csv_text(synthetic_rows(FIRST_OPEN, 10, 15)), limits=ArchiveLimits(max_rows=9))
    with pytest.raises(ArchiveRejected) as refused:
        list(reader)
    assert refused.value.reason == "row_limit"


def test_limits_must_be_positive() -> None:
    for field in ("max_members", "max_member_bytes", "max_total_bytes", "max_rows", "max_compression_ratio",
                  "max_line_bytes"):
        with pytest.raises(ValueError):
            ArchiveLimits(**{field: 0})
    with pytest.raises(ValueError):
        ArchiveLimits(max_rejected_share=-1)  # type: ignore[arg-type]


# --- opening archives ---------------------------------------------------------------------------------------------


def _members(count: int = 3) -> dict[str, str]:
    return {"MANIFEST.json": "{}\n", "XBTUSD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, count, 15)),
            "ETHUSD_60.csv": csv_text(synthetic_rows(FIRST_OPEN, count, 60))}


def test_a_zip_lists_members_and_streams_their_rows(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "Kraken_OHLCVT_2026Q3.zip", _members(4))
    with open_archive(path) as archive:
        assert archive.format == "zip"
        by_name = {member.name: member for member in archive.members}
        assert set(by_name) == {"MANIFEST.json", "XBTUSD_15.csv", "ETHUSD_60.csv"}
        assert by_name["XBTUSD_15.csv"].pair_code == "XBTUSD" and by_name["XBTUSD_15.csv"].interval_minutes == 15
        assert by_name["MANIFEST.json"].pair_code is None
        reader = MemberReader(archive, by_name["XBTUSD_15.csv"], ReadBudget(archive.limits))
        assert len(list(reader)) == 4


def test_a_directory_of_csv_files_reads_the_same_way(tmp_path: Path) -> None:
    path = write_directory(tmp_path / "csv", _members(4))
    with open_archive(path) as archive:
        assert archive.format == "directory"
        member = next(m for m in archive.members if m.name == "ETHUSD_60.csv")
        assert len(list(MemberReader(archive, member, ReadBudget(archive.limits)))) == 4


@pytest.mark.parametrize("name", [
    "../XBTUSD_15.csv", "/abs/XBTUSD_15.csv", "dir/XBTUSD_15.csv", "dir\\XBTUSD_15.csv", "C:XBTUSD_15.csv",
    "XBTUSD_15.csv/", ".hidden_15.csv", "a..b_15.csv", "..", "folder/", "XBTUSD 15.csv", "XBTUSD_15.csv\n",
    "X" * 200 + "_15.csv", "éTH_15.csv",
])
def test_unsafe_member_names_refuse_the_whole_zip_and_write_nothing(tmp_path: Path, name: str) -> None:
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("XBTUSD_15.csv", csv_text(synthetic_rows(FIRST_OPEN, 2, 15)))
        archive.writestr(zipfile.ZipInfo(name), "x")
    before = sorted(os.listdir(tmp_path))
    with pytest.raises(ArchiveRejected) as refused, open_archive(path):
        pass
    assert refused.value.reason == "unsafe_member_name"
    assert sorted(os.listdir(tmp_path)) == before


def test_a_symlink_member_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("XBTUSD_15.csv")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ArchiveRejected) as refused, open_archive(path):
        pass
    assert refused.value.reason == "unsafe_member_type"


def test_duplicate_member_names_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "dup.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("XBTUSD_15.csv", "1")
        with pytest.warns(UserWarning):
            archive.writestr("XBTUSD_15.csv", "2")
    with pytest.raises(ArchiveRejected) as refused, open_archive(path):
        pass
    assert refused.value.reason == "duplicate_member_name"


def test_too_many_members_are_refused(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "many.zip", {f"A{i}USD_15.csv": "" for i in range(6)})
    with pytest.raises(ArchiveRejected) as refused, open_archive(path, ArchiveLimits(max_members=5)):
        pass
    assert refused.value.reason == "member_limit"


def test_too_many_files_in_a_directory_are_refused_and_exactly_the_limit_is_accepted(tmp_path: Path) -> None:
    path = write_directory(tmp_path / "many", {f"A{i}USD_15.csv": "" for i in range(6)})
    with pytest.raises(ArchiveRejected) as refused, open_archive(path, ArchiveLimits(max_members=5)):
        pass
    assert refused.value.reason == "member_limit"
    with open_archive(path, ArchiveLimits(max_members=6)) as archive:
        assert len(archive.members) == 6


def test_a_member_declared_larger_than_the_limit_is_refused_before_reading(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "big.zip", {"XBTUSD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, 200, 15))})
    limits = ArchiveLimits(max_member_bytes=1000)
    with open_archive(path, limits) as archive:
        reader = MemberReader(archive, archive.members[0], ReadBudget(limits))
        with pytest.raises(ArchiveRejected) as refused:
            list(reader)
    assert refused.value.reason == "member_size_limit" and reader.rows_read == 0


def test_the_total_uncompressed_limit_counts_what_was_actually_read(tmp_path: Path) -> None:
    text = csv_text(synthetic_rows(FIRST_OPEN, 100, 15))
    path = write_zip(tmp_path / "total.zip", {"XBTUSD_15.csv": text, "ETHUSD_15.csv": text, "ADAUSD_15.csv": text})
    limits = ArchiveLimits(max_member_bytes=len(text) + 10, max_total_bytes=len(text) * 2 + 10)
    budget = ReadBudget(limits)
    with open_archive(path, limits) as archive, pytest.raises(ArchiveRejected) as refused:
        for member in archive.members:
            list(MemberReader(archive, member, budget))
    assert refused.value.reason == "total_size_limit"


def test_a_zip_bomb_is_stopped_by_the_compression_ratio(tmp_path: Path) -> None:
    bomb = b"1,1,1,1,1,1,1\n" * 400_000  # about 5.6 MB that deflates to a few KB
    path = write_zip(tmp_path / "bomb.zip", {"XBTUSD_15.csv": bomb})
    assert path.stat().st_size < 50_000
    limits = ArchiveLimits(max_compression_ratio=50)
    budget = ReadBudget(limits)
    with open_archive(path, limits) as archive:
        reader = MemberReader(archive, archive.members[0], budget)
        with pytest.raises(ArchiveRejected) as refused:
            list(reader)
    assert refused.value.reason == "compression_ratio_limit"
    assert budget.bytes_read < 3 * 1024 * 1024  # stopped near the 1 MiB grace, not after inflating everything


def test_an_encrypted_member_is_refused_when_read(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "secret.zip", {"XBTUSD_15.csv": "x"})
    raw = bytearray(path.read_bytes())
    for header, flags_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):  # local and central headers
        raw[raw.index(header) + flags_at] |= 0x1
    path.write_bytes(bytes(raw))
    with open_archive(path) as archive:
        reader = MemberReader(archive, archive.members[0], ReadBudget(archive.limits))
        with pytest.raises(ArchiveRejected) as refused:
            list(reader)
    assert refused.value.reason == "unsupported_member"


def test_an_unusual_compression_method_is_refused_when_read(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "bz.zip", {"XBTUSD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, 3, 15))},
                     compression=zipfile.ZIP_BZIP2)
    with open_archive(path) as archive:
        reader = MemberReader(archive, archive.members[0], ReadBudget(archive.limits))
        with pytest.raises(ArchiveRejected) as refused:
            list(reader)
    assert refused.value.reason == "unsupported_member"


def test_a_corrupt_member_is_refused(tmp_path: Path) -> None:
    text = csv_text(synthetic_rows(FIRST_OPEN, 20, 15))
    path = write_zip(tmp_path / "corrupt.zip", {"XBTUSD_15.csv": text}, compression=zipfile.ZIP_STORED)
    raw = bytearray(path.read_bytes())
    at = raw.find(b",", raw.find(str(FIRST_OPEN).encode()) + 12)
    raw[at + 1] = ord("7") if raw[at + 1] != ord("7") else ord("8")
    path.write_bytes(bytes(raw))
    with open_archive(path) as archive:
        reader = MemberReader(archive, archive.members[0], ReadBudget(archive.limits))
        with pytest.raises(ArchiveRejected) as refused:
            list(reader)
    assert refused.value.reason == "corrupt_member"


def _three_deflated_members() -> dict[str, str]:
    return {f"{code}USD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, 2500, 15, seed=seed))
            for seed, code in enumerate(("XBT", "ETH", "ADA"), start=1)}


def test_a_zip_with_bytes_missing_from_the_middle_is_a_corrupt_member(tmp_path: Path) -> None:
    """A mis-joined download: the end of the file is intact so the ZIP opens, but the stored offsets are now wrong.

    Cutting inside the first member moves its start before the beginning of the file, which zipfile reports as a
    bare OSError; the later members still read, because their data is intact.
    """
    path = write_zip(tmp_path / "joined.zip", _three_deflated_members())
    raw = path.read_bytes()
    assert len(raw) > 30_000
    path.write_bytes(raw[:1000] + raw[1000 + 4096:])
    assert zipfile.is_zipfile(path)
    with open_archive(path) as archive:
        damaged = next(member for member in archive.members if member.name == "XBTUSD_15.csv")
        with pytest.raises(ArchiveRejected) as refused:
            list(MemberReader(archive, damaged, ReadBudget(archive.limits)))
        assert refused.value.reason == "corrupt_member"
        intact = [member for member in archive.members if member is not damaged]
        assert [len(list(MemberReader(archive, member, ReadBudget(archive.limits)))) for member in intact] == [2500] * 2


@pytest.mark.parametrize(("at", "fill"), [(200, 0x00), (200, 0xFF), (5000, 0x00), (5000, 0xFF)])
def test_damaged_deflate_data_is_a_corrupt_member(tmp_path: Path, at: int, fill: int) -> None:
    """A real format error from the decompressor, not the checksum mismatch that stored data would give."""
    path = write_zip(tmp_path / "damaged.zip", {"XBTUSD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, 2500, 15))})
    raw = bytearray(path.read_bytes())
    start = 30 + len("XBTUSD_15.csv")  # the local header, then the compressed bytes
    raw[start + at:start + at + 8] = bytes([fill]) * 8
    path.write_bytes(bytes(raw))
    with open_archive(path) as archive:
        with pytest.raises(ArchiveRejected) as refused:
            list(MemberReader(archive, archive.members[0], ReadBudget(archive.limits)))
    assert refused.value.reason == "corrupt_member" and isinstance(refused.value.__cause__, zlib.error)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_a_file_cut_short_after_it_was_opened_is_a_corrupt_member(tmp_path: Path, compression: int) -> None:
    """The listing was read while the file was whole; the member's bytes then run out before its stored size."""
    path = write_zip(tmp_path / "cut.zip", {"XBTUSD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, 2500, 15))},
                     compression=compression)
    whole = path.read_bytes()
    with open_archive(path) as archive:
        path.write_bytes(whole[:1000])
        with pytest.raises(ArchiveRejected) as refused:
            list(MemberReader(archive, archive.members[0], ReadBudget(archive.limits)))
    assert refused.value.reason == "corrupt_member" and isinstance(refused.value.__cause__, EOFError)


class _FailingStream:
    def __init__(self, error: OSError) -> None:
        self._error = error

    def read(self, count: int) -> bytes:
        raise self._error

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class _ScriptedSource:
    limits = ArchiveLimits()

    def __init__(self, stream: _FailingStream | None, error: OSError | None = None) -> None:
        self._stream, self._error = stream, error

    def open(self, member: ArchiveMember):
        if self._error is not None:
            raise self._error
        return self._stream


@pytest.mark.parametrize("fails_on", ["open", "read"])
def test_an_input_output_error_while_reading_a_member_is_a_corrupt_member(fails_on: str) -> None:
    error = OSError(5, "Input/output error")
    source = _ScriptedSource(_FailingStream(error)) if fails_on == "read" else _ScriptedSource(None, error)
    member = ArchiveMember("XBTUSD_15.csv", 100, 100, "XBTUSD", 15)
    with pytest.raises(ArchiveRejected) as refused:
        list(MemberReader(source, member, ReadBudget(source.limits)))
    assert refused.value.reason == "corrupt_member" and refused.value.__cause__ is error


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "plain.zip"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(ArchiveRejected) as refused, open_archive(path):
        pass
    assert refused.value.reason == "not_a_zip"


def test_a_missing_path_and_a_multipart_piece_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ArchiveRejected) as missing, open_archive(tmp_path / "nothing.zip"):
        pass
    assert missing.value.reason == "not_a_file"
    piece = tmp_path / "Kraken_OHLCVT_Full.zip.part00"
    piece.write_bytes(b"PK")
    with pytest.raises(ArchiveRejected) as part, open_archive(piece):
        pass
    assert part.value.reason == "multipart_archive" and "join" in str(part.value)


def test_a_directory_with_a_subfolder_a_symlink_or_a_bad_name_is_refused(tmp_path: Path) -> None:
    folder = write_directory(tmp_path / "with_dir", _members())
    (folder / "nested").mkdir()
    with pytest.raises(ArchiveRejected) as nested, open_archive(folder):
        pass
    assert nested.value.reason == "unsafe_member_type"

    linked = write_directory(tmp_path / "with_link", _members())
    outside = tmp_path / "outside.csv"
    outside.write_text("x")
    (linked / "SOLUSD_15.csv").symlink_to(outside)
    with pytest.raises(ArchiveRejected) as symlink, open_archive(linked):
        pass
    assert symlink.value.reason == "unsafe_member_type"

    hidden = write_directory(tmp_path / "with_hidden", _members())
    (hidden / ".DS_Store").write_bytes(b"x")
    with pytest.raises(ArchiveRejected) as bad_name, open_archive(hidden):
        pass
    assert bad_name.value.reason == "unsafe_member_name"


def test_opening_an_archive_never_extracts_or_uses_temporary_files(tmp_path: Path, monkeypatch) -> None:
    path = write_zip(tmp_path / "ok.zip", _members())

    def refuse(*args, **kwargs):
        raise AssertionError("the importer must not extract or create files")

    monkeypatch.setattr(zipfile.ZipFile, "extract", refuse)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", refuse)
    before = sorted(os.listdir(tmp_path))
    with open_archive(path) as archive:
        for member in archive.members:
            if member.interval_minutes:
                list(MemberReader(archive, member, ReadBudget(archive.limits)))
    assert sorted(os.listdir(tmp_path)) == before


# --- fingerprints -------------------------------------------------------------------------------------------------


def test_an_unreadable_zip_is_refused_as_not_a_file(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "Kraken_OHLCVT_2026Q3.zip", _members())
    with unreadable(path):
        with pytest.raises(ArchiveRejected) as hashing:
            fingerprint_archive(path)
        with pytest.raises(ArchiveRejected) as opening, open_archive(path):
            pass
    assert hashing.value.reason == opening.value.reason == "not_a_file"
    assert isinstance(hashing.value.__cause__, PermissionError)


def test_an_unreadable_csv_in_a_directory_is_refused_as_not_a_file(tmp_path: Path) -> None:
    folder = write_directory(tmp_path / "csv", _members())
    with unreadable(folder / "XBTUSD_15.csv"), pytest.raises(ArchiveRejected) as refused:
        fingerprint_archive(folder)
    assert refused.value.reason == "not_a_file"


def test_an_unreadable_directory_is_refused_as_not_a_file(tmp_path: Path) -> None:
    folder = write_directory(tmp_path / "csv", _members())
    with unreadable(folder), pytest.raises(ArchiveRejected) as refused:
        fingerprint_archive(folder)
    assert refused.value.reason == "not_a_file"


def test_the_fingerprint_of_a_zip_is_its_streamed_sha256(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "Kraken_OHLCVT_2026Q3.zip", _members())
    fingerprint = fingerprint_archive(path)
    assert fingerprint.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (fingerprint.file_name, fingerprint.size_bytes, fingerprint.format) == (
        "Kraken_OHLCVT_2026Q3.zip", path.stat().st_size, "zip")


def test_the_signature_changes_when_the_file_is_rewritten(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "a.zip", _members(3))
    first = fingerprint_archive(path).signature
    assert fingerprint_archive(path).signature == first
    write_zip(path, _members(5))
    assert fingerprint_archive(path).signature != first


def test_a_directory_fingerprint_follows_its_content(tmp_path: Path) -> None:
    folder = write_directory(tmp_path / "csv", _members(3))
    first = fingerprint_archive(folder)
    assert first.format == "directory" and first.size_bytes == sum(p.stat().st_size for p in folder.iterdir())
    assert fingerprint_archive(folder).sha256 == first.sha256
    (folder / "XBTUSD_15.csv").write_text(csv_text(synthetic_rows(FIRST_OPEN, 4, 15)))
    assert fingerprint_archive(folder).sha256 != first.sha256


def test_a_directory_fingerprint_changes_when_a_member_is_only_renamed(tmp_path: Path) -> None:
    """Same bytes under another pair name are another archive: the importer would store them as another series."""
    folder = write_directory(tmp_path / "csv", {"XBTUSD_15.csv": csv_text(synthetic_rows(FIRST_OPEN, 3, 15))})
    first = fingerprint_archive(folder)
    (folder / "XBTUSD_15.csv").rename(folder / "ETHUSD_15.csv")
    renamed = fingerprint_archive(folder)
    assert renamed.size_bytes == first.size_bytes and renamed.sha256 != first.sha256


# --- properties -----------------------------------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(data=st.binary(max_size=90))
def test_arbitrary_bytes_never_raise_they_are_accepted_or_named(data: bytes) -> None:
    row = parse_row(data, 900)
    assert not isinstance(row, str) or row in REJECTION_REASONS


_MANTISSA = st.integers(min_value=1, max_value=10**20 - 1)
_EXPONENT = st.integers(min_value=-60, max_value=60)


@settings(max_examples=300, deadline=None)
@given(values=st.lists(st.tuples(_MANTISSA, _EXPONENT), min_size=4, max_size=4), volume=st.tuples(_MANTISSA, _EXPONENT),
       trades=st.integers(min_value=0, max_value=10**12), slot=st.integers(min_value=0, max_value=10**6),
       flip=st.booleans())
def test_every_row_the_reader_accepts_builds_a_candle_exactly(values, volume, trades, slot, flip) -> None:
    ordered = sorted(values, key=lambda pair: Decimal(pair[0]).scaleb(pair[1]))
    low, middle_a, middle_b, high = ordered
    open_, close = (middle_b, middle_a) if flip else (middle_a, middle_b)
    text = lambda pair: f"{pair[0]}e{pair[1]}"  # noqa: E731
    fields = [str(FIRST_OPEN + slot * 900), text(open_), text(high), text(low), text(close), text(volume), str(trades)]
    row = parse_row(",".join(fields).encode(), 900)
    assert not isinstance(row, str), row
    assert row.open == Decimal(fields[1]) and row.volume == Decimal(fields[5]) and row.trades == trades
    Candle(symbol="BTC/USD", source_pair="XBTUSD", interval_minutes=15, open_time_utc=from_unix(row.open_unix),
           close_time_utc=from_unix(row.open_unix + 900), available_at_utc=datetime(2100, 1, 1, tzinfo=UTC),
           open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume, trade_count=row.trades,
           source="kraken_ohlcvt_archive")
