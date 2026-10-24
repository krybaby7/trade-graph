"""OKX public candle history into the candle store (T29, D168): import a recorded folder, or page back live (L2).

Both write the ``okx_public_candles`` series of one instrument and interval, never mixed with Kraken's (D132), and
record a run with its evidence label. The receipt time is the moment this process obtained the data: the import
clock for a recorded folder (as for Kraken's archive, D134), the end of each request for a live page. Nothing is
backdated. A live fetch pauses between requests far below OKX's documented 20 requests per 2 seconds.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trade_graph.adapters.market.okx_candles import (
    OKX_SOURCE,
    OkxCandleFetcher,
    OkxFetch,
    OkxFetchError,
    okx_bar,
    parse_history_candles,
    read_recording,
    write_recording,
)
from trade_graph.application.candle_store import MAX_SAVE_CANDLES, CandleStore
from trade_graph.contracts.candles import Candle
from trade_graph.domain.clock import Clock, utc_iso
from trade_graph.domain.errors import ValidationFailure

REQUEST_PAUSE_SECONDS = 0.25
MAX_LIVE_PAGES = 2_500  # 5-minute candles need 2,103 pages for two years


def _save(store: CandleStore, candles: list[Candle], run_id: int) -> dict[str, int]:
    totals = {"inserted": 0, "revised": 0, "duplicates": 0}
    for start in range(0, len(candles), MAX_SAVE_CANDLES):
        result = store.save(candles[start:start + MAX_SAVE_CANDLES], run_id=run_id)
        totals["inserted"] += result.inserted
        totals["revised"] += result.revised
        totals["duplicates"] += result.duplicates
    return totals


def import_okx_recording(store: CandleStore, folder: Path | str, clock: Clock) -> dict[str, Any]:
    """Store a recorded (or labelled synthetic) folder of OKX pages; the label comes from its manifest only."""
    recording = read_recording(folder)
    received = clock.now()
    by_open: dict[int, Candle] = {}
    forming = rows = 0
    for _, text in recording.pages:
        page = parse_history_candles(text, inst_id=recording.inst_id, interval_minutes=recording.interval_minutes,
                                     received_at=received)
        forming += page.forming_dropped
        rows += page.rows_received
        for candle in page.candles:
            earlier = by_open.get(candle.open_unix)
            if earlier is not None and earlier.content_hash != candle.content_hash:
                raise ValidationFailure("two recorded pages disagree about one candle; nothing was stored")
            by_open[candle.open_unix] = candle
    candles = [by_open[key] for key in sorted(by_open)]
    parameters = {"recording": recording.folder.name, "inst_id": recording.inst_id,
                  "bar": okx_bar(recording.interval_minutes), "pages": len(recording.pages)}
    run_id = store.begin_run(kind="archive_import", source=OKX_SOURCE, evidence_label=recording.evidence_label,
                             started_at=received, parameters=parameters)
    try:
        totals = _save(store, candles, run_id)
    except BaseException:
        store.finish_run(run_id, finished_at=clock.now(), status="failed", report={"error": "save refused"})
        raise
    report = {**parameters, "evidence_label": recording.evidence_label, "rows": rows, "forming_dropped": forming,
              "candles": len(candles), **totals,
              "first_open_utc": utc_iso(candles[0].open_time_utc) if candles else None,
              "last_close_utc": utc_iso(candles[-1].close_time_utc) if candles else None,
              "received_at_utc": utc_iso(received)}
    store.finish_run(run_id, finished_at=clock.now(), status="ok", report=report)
    return {"run_id": run_id, **report}


def collect_okx_candles(store: CandleStore, fetcher: OkxCandleFetcher, clock: Clock, *, inst_id: str,
                        interval_minutes: int, pages: int, record_to: Path | str | None = None,
                        before_ms: int | None = None,
                        sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Page back from the newest candle (or from ``before_ms``, to resume a stopped run), saving each page as it
    arrives (step L2; tests use a scripted transport)."""
    if type(pages) is not int or not 1 <= pages <= MAX_LIVE_PAGES:
        raise ValidationFailure(f"collect between 1 and {MAX_LIVE_PAGES} pages")
    if before_ms is not None and (type(before_ms) is not int or before_ms <= 0):
        raise ValidationFailure("before is a positive open time in milliseconds")
    started = clock.now()
    # A scripted transport (tests) is synthetic and says so; only the real network transport is live public data.
    synthetic = bool(getattr(fetcher.transport, "synthetic", False))
    parameters: dict[str, Any] = {"inst_id": inst_id, "bar": okx_bar(interval_minutes), "pages": pages}
    if before_ms is not None:
        parameters["before_ms"] = before_ms
    run_id = store.begin_run(kind="public_collect", source=OKX_SOURCE,
                             evidence_label="synthetic_fixture" if synthetic else "live_public",
                             started_at=started, parameters=parameters)
    fetches: list[OkxFetch] = []
    totals = {"inserted": 0, "revised": 0, "duplicates": 0}
    after: int | None = before_ms
    status, stopped = "ok", None
    try:
        for number in range(pages):
            if number:
                sleep(REQUEST_PAUSE_SECONDS)
            try:
                fetch = fetcher.fetch(inst_id, interval_minutes, after_ms=after)
            except OkxFetchError as error:
                status, stopped = "partial" if fetches else "failed", error.category
                break
            fetches.append(fetch)
            for key, value in _save(store, list(fetch.page.candles), run_id).items():
                totals[key] += value
            if fetch.page.oldest_open_ms is None or fetch.page.oldest_open_ms == after:
                break  # nothing older
            after = fetch.page.oldest_open_ms
    finally:
        candles = [candle for fetch in fetches for candle in fetch.page.candles]
        report = {**parameters, "requests": len(fetches), "stopped_by": stopped, "candles": len(candles), **totals,
                  "first_open_utc": utc_iso(min(c.open_time_utc for c in candles)) if candles else None,
                  "last_close_utc": utc_iso(max(c.close_time_utc for c in candles)) if candles else None}
        store.finish_run(run_id, finished_at=clock.now(), status=status, report=report)
    if record_to is not None and fetches:
        write_recording(record_to, inst_id=inst_id, interval_minutes=interval_minutes, fetches=fetches,
                        synthetic=synthetic,
                        note=("Scripted test pages, not market data." if synthetic else
                              f"Recorded from OKX's public history-candles endpoint, run {run_id}."))
    return {"run_id": run_id, "status": status, **report}


__all__ = ["REQUEST_PAUSE_SECONDS", "collect_okx_candles", "import_okx_recording"]
