"""The scheduled collection runner: scope per kind, one lock, kept reports. A stub replaces the real command."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_candle_collection.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("flock") is None, reason="needs bash and flock")

STUB = """
import json, sys, time
from pathlib import Path
sleep = float(sys.argv[1]); argv = sys.argv[2:]
Path(sys.argv[0]).with_name("calls.txt").open("a").write(json.dumps(argv) + "\\n")
time.sleep(sleep)
print(json.dumps({"status": "ok", "requests_total": 3, "totals": {"inserted": 5}, "stop_reason": None}))
"""


def _run(tmp_path: Path, kind: str, *, sleep: float = 0, exit_stub: int | None = None, wait: int | None = None,
         block: bool = True):
    stub = tmp_path / "stub.py"
    stub.write_text(STUB if exit_stub is None else STUB + f"\nsys.exit({exit_stub})\n")
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TRADE_GRAPH_MARKET_DIR": str(tmp_path / "market"),
        "TRADE_GRAPH_CMD": f"{sys.executable} {stub} {sleep}",
    }
    if wait is not None:
        env["LOCK_WAIT_SECONDS"] = str(wait)
    process = subprocess.Popen(["bash", str(SCRIPT), kind], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    if not block:
        return process
    out, err = process.communicate(timeout=60)
    return process.returncode, out, err


def _calls(tmp_path: Path) -> list[list[str]]:
    return [json.loads(line) for line in (tmp_path / "calls.txt").read_text().splitlines()]


def test_short_runs_only_the_one_minute_scope(tmp_path: Path) -> None:
    code, _, _ = _run(tmp_path, "short")
    assert code == 0
    arguments = _calls(tmp_path)[0]
    assert arguments[0] == "collect-kraken-candles"
    assert arguments[arguments.index("--intervals") + 1] == "1"
    assert arguments[arguments.index("--market-database") + 1] == str(tmp_path / "market" / "market_data.sqlite")
    assert "--pairs" not in arguments and "--one-minute-pairs" not in arguments


def test_full_runs_five_fifteen_and_sixty_minutes_with_a_request_bound(tmp_path: Path) -> None:
    assert _run(tmp_path, "full")[0] == 0
    arguments = _calls(tmp_path)[0]
    assert arguments[arguments.index("--intervals") + 1] == "5,15,60"
    assert 1000 < int(arguments[arguments.index("--max-requests") + 1]) <= 10001


def test_an_unknown_kind_is_a_usage_error_and_runs_nothing(tmp_path: Path) -> None:
    code, _, err = _run(tmp_path, "weekly")
    assert code == 2 and "usage" in err and not (tmp_path / "calls.txt").exists()


def test_the_report_history_and_last_copy_are_kept_privately(tmp_path: Path) -> None:
    assert _run(tmp_path, "short")[0] == 0
    logs = tmp_path / "market" / "logs"
    reports = list(logs.glob("collect-short-*.json"))
    assert len(reports) == 1 and json.loads(reports[0].read_text())["status"] == "ok"
    assert json.loads((logs / "last-short.json").read_text())["totals"]["inserted"] == 5
    line = (logs / "history.log").read_text().strip()
    assert "short exit=0 status=ok requests=3 inserted=5" in line
    assert (tmp_path / "market").stat().st_mode & 0o077 == 0
    assert not list(logs.glob("*.err"))


def test_the_collector_exit_code_is_passed_on_and_recorded(tmp_path: Path) -> None:
    code, _, _ = _run(tmp_path, "short", exit_stub=1)
    assert code == 1
    assert "short exit=1" in (tmp_path / "market" / "logs" / "history.log").read_text()


def test_two_runs_never_overlap(tmp_path: Path) -> None:
    first = _run(tmp_path, "full", sleep=1.5, block=False)
    time.sleep(0.5)
    began = time.monotonic()
    second_code, _, _ = _run(tmp_path, "short")
    waited = time.monotonic() - began
    assert first.wait(timeout=30) == 0 and second_code == 0
    assert waited >= 0.8  # the second run waited for the first to release the lock
    assert len(_calls(tmp_path)) == 2


def test_a_run_gives_up_when_the_lock_is_held_too_long(tmp_path: Path) -> None:
    first = _run(tmp_path, "full", sleep=3, block=False)
    time.sleep(0.5)
    code, _, _ = _run(tmp_path, "short", wait=1)
    assert code == 75
    assert first.wait(timeout=30) == 0
    assert "short skipped" in (tmp_path / "market" / "logs" / "history.log").read_text()
    assert len(_calls(tmp_path)) == 1
