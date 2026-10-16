"""The coin scanner on a synthetic candle store (T31): exclusions, inputs, peg check, ranking, replays.

Every candle, catalog row and ticker answer here is synthetic and labelled so. No network, model or key is used.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal

import pytest
from tests.coin_scanner_support import (
    AS_OF,
    LAST_CLOSE,
    Path_,
    hourly,
    new_store,
    record,
    save_candles,
    save_catalog,
    ticker,
    tight_quote,
)
from tests.network_guard import block_network

from trade_graph.application.coin_scanner.ranking import Candidate, rank
from trade_graph.application.coin_scanner.scanner import CoinScanner
from trade_graph.contracts.coin_scanner import FACTORS, ScannerRules
from trade_graph.domain.clock import FrozenClock
from trade_graph.domain.errors import ValidationFailure

RULES = ScannerRules(live_source="synthetic")
# base: (path, spread, candle options). The first twelve pass every check; the rest each fail one on purpose.
ELIGIBLE = {
    "SOL": (Path_("200", "0.03", "0.0005", "5000"), "0.0002", {}),
    "BTC": (Path_("80000", "0.008", "0.0001", "10"), "0.0003", {}),
    "ETH": (Path_("3000", "0.012", "0.0002", "200"), "0.0004", {}),
    "ADA": (Path_("0.7", "0.02", "-0.0001", "600000"), "0.0006", {}),
    "DOT": (Path_("5", "0.018", "0.0003", "60000"), "0.0008", {}),
    "AVAX": (Path_("30", "0.022", "0", "8000"), "0.001", {}),
    "XRP": (Path_("0.6", "0.015", "0.0001", "300000"), "0.0005", {}),
    "DOGE": (Path_("0.2", "0.025", "-0.0002", "600000"), "0.0012", {}),
    "LINK": (Path_("20", "0.016", "0.0002", "4000"), "0.0009", {}),
    "TRX": (Path_("0.3", "0.01", "0.0001", "200000"), "0.0007", {"last_close": LAST_CLOSE - timedelta(hours=1)}),
    "WILDX": (Path_("2", "0.028", "0.0004", "20000"), "0.0015", {}),   # never reviewed; passes the peg check
    "ATOM": (Path_("8", "0.005", "-0.0005", "5000"), "0.004", {}),     # the weakest on every factor
}
FAILING = {
    "PEGX": (Path_("1.0", "0.002", "0", "2000000"), "0.0001", {}),    # never reviewed, sits at one dollar
    "EURX": (Path_("1.08", "0.004", "0", "2000000"), "0.0001", {}),   # never reviewed, pinned to another level
    "LTC": (Path_("50", "0.02", "0", "100"), "0.0005", {}),           # thin
    "XLM": (Path_("0.3", "0.02", "0", "500000"), "0.01", {}),         # wide spread
    "ZEC": (Path_("40", "0.02", "0", "5000"), "0.0005", {"skip": (5,)}),  # a hole five hours back
    "XMR": (Path_("150", "0.02", "0", "1000"), "0.0005", {"hours": 200}),  # too young for the swing indicator
    "ETC": (Path_("20", "0.02", "0", "5000"), None, {}),              # no bid and ask in the ticker
    "BCH": (Path_("400", "0.02", "0", "100"), "0.0005", {"last_close": LAST_CLOSE - timedelta(hours=3)}),
}
CATALOG_ONLY = {"USDT": "online", "LSETH": "online", "EUR": "online", "KIN": "cancel_only", "U": "online"}
EXPECTED_REASONS = {
    "USDT/USD": {"stablecoin"}, "LSETH/USD": {"yield_token"}, "EUR/USD": {"fiat_currency"},
    "KIN/USD": {"not_normal_status"}, "U/USD": {"held_for_review"},
    "PEGX/USD": {"price_peg", "price_pinned"}, "EURX/USD": {"price_pinned"}, "LTC/USD": {"thin_volume"},
    "XLM/USD": {"wide_spread"}, "ZEC/USD": {"missing_volume", "missing_swing", "missing_trend"},
    "XMR/USD": {"missing_swing"}, "ETC/USD": {"missing_spread"}, "BCH/USD": {"missing_candles"},
}


def _quotes() -> dict[str, tuple[str, str]]:
    return {f"{base}/USD": tight_quote(path.level, spread)
            for base, (path, spread, _) in {**ELIGIBLE, **FAILING}.items() if spread is not None}


@pytest.fixture(scope="module")
def field(tmp_path_factory):
    store = new_store(tmp_path_factory.mktemp("scanner") / "private")
    records = [record(base) for base in (*ELIGIBLE, *FAILING)]
    records += [record(base, status=status) for base, status in CATALOG_ONLY.items()]
    save_catalog(store, records, observed_at=AS_OF - timedelta(hours=5))
    for base, (path, _spread, options) in {**ELIGIBLE, **FAILING}.items():
        save_candles(store, hourly(f"{base}/USD", path, **options))
    return store


def _scan(store, *, quotes=None, rules=RULES, as_of=AS_OF, **kwargs):
    snapshot = ticker(_quotes() if quotes is None else quotes) if quotes is not False else None
    return CoinScanner(store, clock=FrozenClock(as_of + timedelta(seconds=30)), rules=rules).scan(
        as_of=as_of, ticker=snapshot, **kwargs)


def test_the_eligible_pairs_are_ranked_and_the_top_ten_shortlisted(field) -> None:
    result = _scan(field)
    assert result.status == "ok" and result.mode == "live" and result.optimistic is False
    ranked = [item.symbol for item in result.ranked]
    assert sorted(ranked) == sorted(f"{base}/USD" for base in ELIGIBLE)
    assert ranked[0] == "SOL/USD" and ranked[-1] == "ATOM/USD"
    assert [item.symbol for item in result.shortlist] == ranked[:10]
    assert result.document()["counts"]["shortlisted"] == 10


def test_each_excluded_pair_names_exactly_why(field) -> None:
    result = _scan(field)
    reasons = {item.symbol: set(item.reasons) for item in result.evaluations if item.reasons}
    assert reasons == EXPECTED_REASONS
    excluded = result.document()["excluded"]
    assert excluded["missing_swing"] == ["XMR/USD", "ZEC/USD"]
    assert not {item.symbol for item in result.ranked} & set(EXPECTED_REASONS)


def test_flags_say_what_was_unusual_without_dropping_the_pair(field) -> None:
    result = _scan(field)
    flags = {item.symbol: set(item.flags) for item in result.evaluations if item.flags}
    assert flags["TRX/USD"] == {"candles_lagging"}
    assert flags["WILDX/USD"] == {"not_individually_reviewed"}
    trx = next(item for item in result.evaluations if item.symbol == "TRX/USD")
    assert trx.inputs["candles"]["end_close_time_utc"] == "2026-10-16T11:00:00.000000Z"
    assert trx.inputs["candles"]["lag_hours"] == 1


def test_catalog_exclusions_read_no_candles(field) -> None:
    result = _scan(field)
    usdt = next(item for item in result.evaluations if item.symbol == "USDT/USD")
    assert set(usdt.inputs) == {"catalog"}


def test_every_shortlist_entry_carries_every_input_value_and_its_source_time(field) -> None:
    result = _scan(field)
    entry = result.entries()[0]
    assert entry["symbol"] == "SOL/USD" and entry["position"] == 1
    assert entry["ranking_version"] == RULES.ranking_version
    inputs = entry["inputs"]
    assert inputs["catalog"]["observed_at_utc"] == "2026-10-16T07:05:00.000000Z"
    assert inputs["catalog"]["status"] == "online" and inputs["catalog"]["class"] == "reviewed_not_marked"
    assert inputs["candles"]["end_close_time_utc"] == "2026-10-16T12:00:00.000000Z"
    assert inputs["candles"]["newest_received_at_utc"] == "2026-10-16T12:03:00.000000Z"
    assert inputs["candles"]["mode"] == "point_in_time" and inputs["candles"]["source"] == "synthetic"
    volume = inputs["volume"]
    assert volume["price_basis"] == "vwap" and volume["hours"] == 24
    assert volume["first_close_utc"] == "2026-10-15T13:00:00.000000Z"
    swing = inputs["swing"]
    assert swing["indicator"] == "NATR" and swing["status"] == "ready" and swing["parameters"] == {"timeperiod": 14}
    assert Decimal(swing["swing_to_cost"]) == Context(prec=12).divide(Decimal(swing["value"]), Decimal("0.8"))
    assert swing["round_trip_rate"] == "0.008" and swing["candles"]["input_sha256"]
    assert inputs["trend"]["indicator"] == "ROCP" and Decimal(inputs["trend"]["value"]) > 0
    spread = inputs["spread"]
    assert spread["received_at_utc"] == "2026-10-16T12:04:40.000000Z" and spread["key"] == "SOLUSD"
    assert Decimal(spread["ask"]) > Decimal(spread["bid"])
    assert inputs["peg_check"] == {"applied": False, "reason": "base asset reviewed in the pair catalog"}
    cost = inputs["cost"]
    assert cost["ref"] == "kraken-spot-starting-tier@1" and cost["liquidity"] == "maker"
    assert len(cost["content_sha256"]) == 64
    assert set(entry["ranks"]) == set(FACTORS)


def test_traded_volume_is_the_exact_sum_of_volume_times_vwap_over_the_last_24_hours(field) -> None:
    result = _scan(field)
    sol = next(item for item in result.evaluations if item.symbol == "SOL/USD")
    candles = [item for item in hourly("SOL/USD", ELIGIBLE["SOL"][0]) if item.close_time_utc > LAST_CLOSE -
               timedelta(hours=24)]
    assert len(candles) == 24
    expected = sum((item.volume * item.vwap for item in candles), Decimal(0))
    assert Decimal(sol.inputs["volume"]["value_usd"]) == expected
    assert Decimal(sol.inputs["volume"]["base_volume"]) == Decimal("5000") * 24


def test_the_same_inputs_give_the_same_document(field) -> None:
    first, second = _scan(field).document(), _scan(field).document()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_stored_ranking_values_reproduce_the_order(field) -> None:
    document = _scan(field).document()
    candidates = [Candidate(symbol=row["symbol"], volume_usd_24h=Decimal(row["values"]["volume_usd_24h"]),
                            swing_to_cost=Decimal(row["values"]["swing_to_cost"]),
                            spread=Decimal(row["values"]["spread"]), trend=Decimal(row["values"]["trend"]))
                  for row in document["ranking"]]
    assert [item.symbol for item in rank(candidates)] == [row["symbol"] for row in document["ranking"]]


def test_without_a_usable_ticker_no_pair_is_ranked_and_the_scan_says_why(field) -> None:
    missing = _scan(field, quotes=False)
    assert missing.status == "no_candidates" and not missing.ranked
    assert missing.document()["ticker"] == {"usable": False, "reason": "no_ticker_answer"}
    stale = CoinScanner(field, clock=FrozenClock(AS_OF), rules=RULES).scan(
        as_of=AS_OF, ticker=ticker(_quotes(), received_at=AS_OF - timedelta(minutes=6)))
    assert stale.status == "no_candidates"
    assert stale.document()["ticker"]["reason"] == "ticker_older_than_allowed"
    assert all("missing_spread" in item.reasons for item in stale.evaluations if item.symbol == "SOL/USD")
    crossed = _scan(field, quotes={**_quotes(), "SOL/USD": ("201", "200")})
    sol = next(item for item in crossed.evaluations if item.symbol == "SOL/USD")
    assert sol.reasons == ("missing_spread",) and sol.inputs["spread"]["reason"] == "ask_not_above_bid"


def test_a_candle_received_after_the_scan_moment_is_not_seen(tmp_path) -> None:
    store = new_store(tmp_path / "private")
    save_catalog(store, [record("SOL")], observed_at=AS_OF - timedelta(hours=1))
    candles = hourly("SOL/USD", ELIGIBLE["SOL"][0])
    late = candles[-1].model_copy(update={"available_at_utc": AS_OF + timedelta(minutes=1)})
    save_candles(store, [*candles[:-1], late])
    result = _scan(store)
    sol = result.evaluations[0]
    assert sol.inputs["candles"]["end_close_time_utc"] == "2026-10-16T11:00:00.000000Z"
    assert sol.flags == ("candles_lagging",)


def test_without_a_pair_catalog_the_scan_fails_plainly(tmp_path) -> None:
    result = _scan(new_store(tmp_path / "private"))
    assert result.status == "failed" and result.error == "no_pair_catalog"
    assert result.document()["counts"]["usd_pairs"] == 0 and not result.shortlist


def test_a_catalog_observed_after_the_scan_moment_is_not_used(tmp_path) -> None:
    store = new_store(tmp_path / "private")
    save_catalog(store, [record("SOL")], observed_at=AS_OF + timedelta(minutes=1))
    assert _scan(store).error == "no_pair_catalog"


def test_a_replay_on_archive_history_is_labelled_optimistic_and_does_not_rank_spread(tmp_path) -> None:
    store = new_store(tmp_path / "private")
    past = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)
    save_catalog(store, [record(base) for base in ("SOL", "ETH", "ADA")],
                 observed_at=datetime(2026, 10, 13, tzinfo=UTC))
    imported = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)
    for base in ("SOL", "ETH", "ADA"):
        path = ELIGIBLE[base][0]
        candles = hourly(f"{base}/USD", Path_(path.level, path.swing, path.drift, path.volume, vwap=False),
                         last_close=past, source="kraken_ohlcvt_archive", received_at=imported)
        save_candles(store, candles, label="local_archive_file", kind="archive_import")
    scanner = CoinScanner(store, clock=FrozenClock(datetime(2026, 10, 16, 21, tzinfo=UTC)), rules=ScannerRules())
    result = scanner.scan(as_of=past + timedelta(minutes=5), ticker=None, mode="replay",
                          source="kraken_ohlcvt_archive")
    document = result.document()
    assert result.optimistic is True and result.mode == "replay"
    assert {reason["code"] for reason in document["scan"]["optimism_reasons"]} == {
        "delisted_pairs_missing", "catalog_observed_after_the_replayed_moment", "no_historical_spread"}
    assert document["factors"] == ["volume_usd_24h", "swing_to_cost", "trend"]
    assert [item.symbol for item in result.ranked][0] == "SOL/USD" and len(result.ranked) == 3
    sol = result.entries()[0]["inputs"]
    assert sol["volume"]["price_basis"] == "close"
    assert sol["candles"]["mode"] == "replay" and sol["candles"]["availability_basis"] == "event_time_only"
    assert sol["spread"] == {"ranked": False, "reason": "no historical bid and ask in a replay"}


def test_a_live_scan_cannot_read_another_source_or_skip_point_in_time(field) -> None:
    with pytest.raises(ValidationFailure, match="source"):
        CoinScanner(field, clock=FrozenClock(AS_OF), rules=RULES).scan(
            as_of=AS_OF, ticker=None, mode="live", source="kraken_ohlcvt_archive")


def test_the_scan_makes_no_network_call(field, monkeypatch) -> None:
    block_network(monkeypatch)
    assert _scan(field).status == "ok"


def test_making_a_shortlist_loads_no_model_or_budget_code() -> None:
    probe = (
        "import sys\n"
        "import trade_graph.application.coin_scanner.scanner\n"
        "watched = ('trade_graph.adapters.models', 'trade_graph.application.budget', "
        "'trade_graph.application.gateway', 'trade_graph.application.model_invocations', "
        "'trade_graph.adapters.decisions', 'anthropic', 'openai', 'httpx')\n"
        "print(','.join(sorted(name for name in sys.modules if name.startswith(watched))))\n")
    completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120,
                               check=False)
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip() == ""
