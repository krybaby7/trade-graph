"""A stored practice-range report in plain sentences, for the owner and the Trader (T29).

Display only: amounts are rounded to cents and fractions to hundredths of a percent here, never in the stored report,
whose numbers stay exact decimal text.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from trade_graph.application.practice_range.store import RunRecord

_CENT = Decimal("0.01")


def _amount(text: str | None, currency: str) -> str:
    if text is None:
        return "none"
    return f"{Decimal(text).quantize(_CENT, rounding=ROUND_HALF_EVEN):,} {currency}"


def _percent(text: str | None) -> str:
    if text is None:
        return "none"
    return f"{(Decimal(text) * 100).quantize(_CENT, rounding=ROUND_HALF_EVEN)}%"


def _benchmarks(metrics: dict[str, Any], currency: str) -> list[str]:
    lines = []
    names = {"hold_btc": "holding BTC", "hold_coin": "holding the coin itself"}
    for key, label in names.items():
        benchmark = metrics["benchmarks"].get(key)
        if benchmark is None:
            continue
        lines.append(f"Against {label} ({benchmark['symbol']}): {_amount(benchmark['versus'], currency)} "
                     f"(holding made {_amount(benchmark['result'], currency)}).")
    return lines


def report_lines(record: RunRecord) -> list[str]:
    report = record.report
    result = report["result"]
    request, metrics, data = result["request"], result["metrics"], result["data"]
    currency = request["currency"]
    window = request["window"]
    profile = request["cost_profile"]
    lines = [
        f"Practice-range report {record.run_id} ({record.phase}, study {record.study_id}).",
        f"Strategy {request['strategy']['ref']} on {request['symbol']}, {request['interval_minutes']}-minute candles "
        f"from {request['source']}, {window['start']} to {window['end']} ({window['days']} days, end excluded).",
        f"Fees: {profile['ref']} (maker {_percent(profile['maker_rate'])}, taker {_percent(profile['taker_rate'])}); "
        f"fills: {request['fill_policy']['ref']}. {report['venue']['note']}",
        f"Trades: {metrics['trades']} ({metrics['wins']} won, {metrics['losses']} lost; win rate "
        f"{_percent(metrics['win_rate'])}). Average win {_amount(metrics['average_win'], currency)}, average loss "
        f"{_amount(metrics['average_loss'], currency)}.",
        f"Result after fees: {_amount(metrics['result'], currency)} ({_percent(metrics['result_fraction'])}) from "
        f"{_amount(metrics['starting_equity'], currency)}. Fees paid: {_amount(metrics['fees_paid'], currency)}.",
        f"Against cash: {_amount(metrics['versus_cash']['versus'], currency)} (cash earns no interest).",
        *_benchmarks(metrics, currency),
        f"Worst drawdown: {_percent(metrics['worst_drawdown']['fraction'])} "
        f"({_amount(metrics['worst_drawdown']['amount'], currency)}). Worst losing streak: "
        f"{metrics['worst_losing_streak']} trades.",
    ]
    ending = result["ending"]
    if Decimal(ending["held"]) > 0:
        lines.append(f"Still held at the end: {ending['held']} (valued at {_amount(ending['value'], currency)} after "
                     "the selling fee); not counted as a trade.")
    lines.append(f"Data: {data['window_present']} of {data['window_bars']} candles present, cut-off "
                 f"{data['data_as_of_utc']}. {report['evidence']['statement']}")
    if not report["study"]["meets_three_month_minimum"]:
        lines.append(f"This study replays {report['study']['replayed_days']} days, less than the three months D119 "
                     "asks for.")
    if report.get("currency_note"):
        lines.append(report["currency_note"])
    lines.append(report["label"]["statement"])
    return lines


def report_text(record: RunRecord) -> str:
    return "\n".join(report_lines(record))


def report_overview(record: RunRecord) -> dict[str, Any]:
    """The few numbers a list shows, with the report's identity."""
    metrics = record.report["result"]["metrics"]
    return {"run_id": record.run_id, "study_id": record.study_id, "phase": record.phase,
            "strategy_ref": record.strategy_ref, "symbol": record.symbol, "source": record.source,
            "cost_profile_ref": record.cost_profile_ref, "window": {"start": record.window.start.isoformat(),
                                                                   "end": record.window.end.isoformat()},
            "evidence": list(record.evidence), "trades": metrics["trades"], "win_rate": metrics["win_rate"],
            "result": metrics["result"], "currency": metrics["currency"],
            "worst_drawdown": metrics["worst_drawdown"]["fraction"], "result_sha256": record.result_sha256,
            "label": record.report["label"]["kind"]}


__all__ = ["report_lines", "report_overview", "report_text"]
