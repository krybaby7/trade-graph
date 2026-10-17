"""The HQ chart document contract (T41): exact decimals for prices, labelled indicator numbers, fixed vocabularies."""

from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from trade_graph.api import hq_chart_contract as contract


def _document() -> dict:
    return {
        "schema": contract.SCHEMA, "as_of": "2026-10-16T08:00:01.000Z", "status": "ok", "status_text": None,
        "coins": [{"symbol": "SOL/USD", "reasons": ["day_plan", "held"]},
                  {"symbol": "BTC/USD", "reasons": ["reference"]}],
        "symbol": "SOL/USD", "interval_minutes": 15, "intervals": list(contract.INTERVALS),
        "source": "kraken_public_ohlc", "price_precision": 2,
        "window": {"first_open": 1760585400, "step_seconds": 900, "slots": 2, "now_open": 1760601600},
        "newest_close": "2026-10-16T07:45:00.000Z",
        "candles": [[1760585400, "100.1", "100.2", "99.9", "100", "12.5"]],
        "missing": [1760586300],
        "indicators": [{
            "key": "RSI|15|timeperiod=14", "indicator": "RSI", "title": "Relative Strength Index", "label": "RSI 14",
            "parameters": {"timeperiod": 14}, "interval_minutes": 15, "pane": "own", "status": "ready",
            "status_text": None, "chosen_by": ["day_plan"],
            "outputs": [{"name": "value", "label": "RSI 14", "style": "line", "values": [55.5, 56.25]}]}],
        "lines": [{"kind": "alarm", "price": "95", "title": "Alarm", "pane": "price", "state": "armed",
                   "text": "Trader's alarm: mid price crosses below 95"}],
        "markers": [{"kind": "buy", "t": 1760585400, "at": "2026-10-16T07:31:00.000Z", "price": "99.5",
                     "text": "Bought 5 SOL at 99.5"}],
        "elsewhere": [], "notes": [],
    }


def test_a_complete_document_validates() -> None:
    parsed = contract.validate_chart(_document())
    assert parsed.symbol == "SOL/USD" and parsed.candles[0][4] == "100"


@pytest.mark.parametrize("path, value", [
    (("candles", 0, 4), 100.0),              # a price is decimal text, never a binary float
    (("candles", 0, 4), "1e2"),              # nor an exponent
    (("candles", 0, 0), "1760585400"),
    (("lines", 0, "price"), 95),
    (("markers", 0, "price"), "-1"),
    (("indicators", 0, "outputs", 0, "values", 0), math.nan),
    (("indicators", 0, "outputs", 0, "values", 0), math.inf),
    (("lines", 0, "kind"), "approval"),
    (("markers", 0, "kind"), "confidence"),
    (("coins", 0, "reasons", 0), "leader_pick"),
    (("indicators", 0, "pane"), "left"),
    (("interval_minutes",), 30),
    (("schema",), "trade-graph.hq.v1"),
    (("window", "step_seconds"), 60),
])
def test_refused_values(path, value) -> None:
    document = _document()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises((ValidationError, ValueError)):
        contract.validate_chart(document)


def test_an_unknown_key_is_refused_and_ready_needs_values() -> None:
    document = _document()
    document["confidence"] = 0.9
    with pytest.raises(ValidationError):
        contract.validate_chart(document)
    document = _document()
    document["indicators"][0]["outputs"] = []
    with pytest.raises(ValidationError, match="ready"):
        contract.validate_chart(document)
    document = _document()
    document["indicators"][0]["status"] = "gapped"
    with pytest.raises(ValidationError, match="withheld"):
        contract.validate_chart(document)


def test_values_line_up_with_the_window_and_unready_text_is_required() -> None:
    document = _document()
    document["indicators"][0]["outputs"][0]["values"] = [1.0]
    with pytest.raises(ValidationError, match="slot"):
        contract.validate_chart(document)
    document = _document()
    document["indicators"][0].update(status="warming_up", outputs=[])
    with pytest.raises(ValidationError, match="says why"):
        contract.validate_chart(document)


def test_a_document_without_price_history_carries_no_drawing_data() -> None:
    document = copy.deepcopy(_document())
    document.update(status="no_price_history", status_text="Price history isn't connected to HQ yet.", candles=[],
                    missing=[], indicators=[], markers=[], window=None, newest_close=None, price_precision=None)
    contract.validate_chart(document)
    document["candles"] = _document()["candles"]
    with pytest.raises(ValidationError, match="no candles"):
        contract.validate_chart(document)
