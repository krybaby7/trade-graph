"""Bounded owner-requested checks, separate from authoritative financial state.

An offline check is scripted evidence. A public Kraken check is a connectivity
check, not account verification, execution evidence, or live authorization.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

_ACTIVE = ("queued", "running")
_KEEP_SCOPE = 20
_KEEP_TOTAL = 200
_MAX_ACTIVE = 8
_OFFLINE_TIMEOUT = 90.0
_PUBLIC_TIMEOUT = 30.0
_STALE_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 262_144
_PROCESS = uuid.uuid4().hex
_WORKERS: dict[str, threading.Thread] = {}
_WORKERS_LOCK = threading.Lock()

_CHECKS = (
    (
        "offline-loop",
        "Local graph rehearsal",
        "Scripted six-department loop with virtual orders, restart and accounting checks.",
        "offline",
        True,
        "",
    ),
    (
        "kraken-public",
        "Kraken public API",
        "Check Kraken's clock, status, BTC/ETH pair rules and public bid/ask prices.",
        "public",
        True,
        "",
    ),
    (
        "kraken-account",
        "Kraken read-only account check",
        "View verified historical read-only observations and pending readiness checks.",
        "account",
        False,
        "Run the protected owner CLI on the WSL host; browser execution is disabled.",
    ),
    (
        "kraken-order-validation",
        "Kraken order validation",
        "Validate an order against Kraken without submitting a real trade.",
        "validation",
        False,
        "The authenticated validation workflow is not connected to this dashboard yet.",
    ),
    (
        "kraken-live-pilot",
        "Small real-trade trial",
        "Observe actual orders, fills, fees and reconciliation.",
        "live",
        False,
        "Requires completed prerequisites and a separate owner-authorized live allocation.",
    ),
)
_STEP_LABELS = {
    "offline-loop": ["Run isolated scripted graph", "Verify retained rehearsal evidence"],
    "kraken-public": [
        "Kraken server clock",
        "Exchange operating status",
        "BTC/USD and ETH/USD pair rules",
        "BTC/USD and ETH/USD bid/ask",
    ],
}
_PUBLIC_ENDPOINTS = (
    ("Time", None),
    ("SystemStatus", None),
    ("AssetPairs", {"pair": "XBTUSD,ETHUSD"}),
    ("Ticker", {"pair": "XBTUSD,ETHUSD"}),
)


def checks() -> list[dict[str, Any]]:
    return [
        dict(zip(("id", "label", "description", "kind", "available", "reason"), item, strict=True)) for item in _CHECKS
    ]


def _current_time() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _current_time().isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> None:
    # Reject directory aliases rather than traversing into another owner's data.
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise RuntimeError("Check storage cannot contain symbolic links.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise RuntimeError("Check storage requires an owner-private directory (0700).")


def _path(runtime: Any) -> Path:
    path = Path(runtime.database.path).absolute().parent / "progress-runs.sqlite"
    _private_directory(path.parent)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_uid != os.geteuid():
        raise RuntimeError("Check registry requires a private regular file (0600).")
    return path


@contextmanager
def _connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, isolation_level=None, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("""CREATE TABLE IF NOT EXISTS progress_runs (
            run_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, deployment_id TEXT NOT NULL,
            check_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, summary TEXT NOT NULL, steps_json TEXT NOT NULL,
            heartbeat REAL NOT NULL, deadline REAL NOT NULL, process_id TEXT NOT NULL,
            owner_pid INTEGER NOT NULL)""")
        connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS progress_one_active
            ON progress_runs(portfolio_id, deployment_id) WHERE status IN ('queued', 'running')""")
        connection.execute("""CREATE TABLE IF NOT EXISTS progress_account_observations (
            run_id TEXT PRIMARY KEY REFERENCES progress_runs(run_id) ON DELETE CASCADE,
            metadata_json TEXT NOT NULL)""")
        yield connection
    finally:
        connection.close()


def _scope(runtime: Any) -> tuple[str, str]:
    return str(runtime.portfolio_id), str(getattr(runtime, "deployment_id", "deployment"))


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _recover(connection: sqlite3.Connection) -> None:
    now = time.time()
    rows = connection.execute("SELECT * FROM progress_runs WHERE status IN ('queued', 'running')").fetchall()
    for row in rows:
        if row["deadline"] < now or row["heartbeat"] < now - _STALE_SECONDS or not _is_alive(row["owner_pid"]):
            steps = json.loads(row["steps_json"])
            for step in steps:
                if step["status"] in _ACTIVE:
                    step.update(status="interrupted", detail="The worker stopped before recording a result.")
            connection.execute(
                """UPDATE progress_runs SET status='interrupted', finished_at=?, summary=?,
                steps_json=? WHERE run_id=? AND status IN ('queued', 'running')""",
                (
                    _now(),
                    "Interrupted or expired; this check has no successful result.",
                    json.dumps(steps),
                    row["run_id"],
                ),
            )


def _record(row: sqlite3.Row, *, now: datetime | None = None) -> dict[str, Any]:
    check = next(item for item in checks() if item["id"] == row["check_id"])
    record = {
        "run_id": row["run_id"],
        "check_id": row["check_id"],
        "label": check["label"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "summary": row["summary"],
        "kind": check["kind"],
        "steps": json.loads(row["steps_json"]),
        "synthetic": row["check_id"] == "offline-loop",
    }
    if row["check_id"] == "kraken-account":
        from trade_graph.api.account_checks import project_history

        try:
            return project_history(record, json.loads(row["account_json"]), now=now or _current_time())
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            raise RuntimeError("The private check registry is unavailable.") from None
    return record


def runs(runtime: Any) -> list[dict[str, Any]]:
    try:
        with _connection(_path(runtime)) as connection:
            _recover(connection)
            rows = connection.execute(
                """SELECT r.*, a.metadata_json AS account_json FROM progress_runs r
                LEFT JOIN progress_account_observations a ON a.run_id=r.run_id
                WHERE r.portfolio_id=? AND r.deployment_id=?
                ORDER BY r.started_at DESC, r.run_id DESC LIMIT ?""",
                (*_scope(runtime), _KEEP_SCOPE),
            ).fetchall()
            return [_record(row) for row in rows]
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError("The private check registry is unavailable.") from exc


def _prune(connection: sqlite3.Connection, path: Path, scope: tuple[str, str]) -> None:
    stale = connection.execute(
        """SELECT run_id FROM progress_runs WHERE portfolio_id=? AND deployment_id=?
        AND status NOT IN ('queued', 'running') ORDER BY started_at DESC, run_id DESC LIMIT -1 OFFSET ?""",
        (*scope, _KEEP_SCOPE - 1),
    ).fetchall()
    for row in stale:
        _remove_work(path, row["run_id"])
        connection.execute("DELETE FROM progress_runs WHERE run_id=?", (row["run_id"],))
    active = connection.execute("SELECT COUNT(*) FROM progress_runs WHERE status IN ('queued', 'running')").fetchone()[
        0
    ]
    stale = connection.execute(
        """SELECT run_id FROM progress_runs WHERE status NOT IN ('queued', 'running')
        ORDER BY started_at DESC, run_id DESC LIMIT -1 OFFSET ?""",
        (_KEEP_TOTAL - active - 1,),
    ).fetchall()
    for row in stale:
        _remove_work(path, row["run_id"])
        connection.execute("DELETE FROM progress_runs WHERE run_id=?", (row["run_id"],))


def _remove_work(path: Path, run_id: str) -> None:
    if len(run_id) != 32 or any(char not in "0123456789abcdef" for char in run_id):
        return
    root = path.parent / "progress-run-work"
    directory = root / run_id
    if root.is_symlink() or directory.is_symlink():
        return
    if directory.is_dir():
        # shutil.rmtree uses descriptor-relative operations against symlink attacks.
        shutil.rmtree(directory)


def start(runtime: Any, check_id: str) -> dict[str, Any]:
    check = next((item for item in checks() if item["id"] == check_id), None)
    if check is None:
        raise ValueError("Unknown check. Choose one of the dashboard's fixed checks.")
    if not check["available"]:
        raise ValueError(check["reason"])
    run_id = uuid.uuid4().hex
    steps = [{"label": label, "status": "queued", "detail": "Waiting to run."} for label in _STEP_LABELS[check_id]]
    timeout = _OFFLINE_TIMEOUT if check_id == "offline-loop" else _PUBLIC_TIMEOUT
    try:
        path = _path(runtime)
        with _connection(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _recover(connection)
            if connection.execute(
                """SELECT 1 FROM progress_runs WHERE portfolio_id=? AND deployment_id=?
                    AND status IN ('queued', 'running')""",
                _scope(runtime),
            ).fetchone():
                raise RuntimeError("A check is already running for this portfolio and deployment.")
            if (
                connection.execute(
                    "SELECT COUNT(*) FROM progress_runs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                >= _MAX_ACTIVE
            ):
                raise RuntimeError("The dashboard's bounded check workers are busy. Try again after a check finishes.")
            _prune(connection, path, _scope(runtime))
            now = time.time()
            connection.execute(
                "INSERT INTO progress_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    *_scope(runtime),
                    check_id,
                    "queued",
                    _now(),
                    None,
                    "Waiting for the check worker.",
                    json.dumps(steps),
                    now,
                    now + timeout + 5,
                    _PROCESS,
                    os.getpid(),
                ),
            )
            record = _record(connection.execute("SELECT * FROM progress_runs WHERE run_id=?", (run_id,)).fetchone())
            connection.execute("COMMIT")
        thread = threading.Thread(
            target=_worker, args=(path, run_id, check_id, steps), name=f"progress-check-{run_id[:8]}", daemon=True
        )
        with _WORKERS_LOCK:
            _WORKERS[run_id] = thread
        try:
            thread.start()
        except RuntimeError:
            with _WORKERS_LOCK:
                _WORKERS.pop(run_id, None)
            _update(path, run_id, "interrupted", "The check worker could not start.", steps, finished=True)
            raise
        return record
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError("The private check registry is unavailable.") from exc


def _update(path: Path, run_id: str, status: str, summary: str, steps: list[dict], *, finished: bool = False) -> None:
    with _connection(path) as connection:
        connection.execute(
            """UPDATE progress_runs SET status=?, summary=?, steps_json=?, heartbeat=?,
            finished_at=? WHERE run_id=? AND status IN ('queued', 'running') AND process_id=?""",
            (status, summary[:500], json.dumps(steps), time.time(), _now() if finished else None, run_id, _PROCESS),
        )


def _worker(path: Path, run_id: str, check_id: str, steps: list[dict]) -> None:
    def update(index: int, status: str, detail: str) -> None:
        steps[index].update(status=status, detail=detail[:500])
        _update(path, run_id, "running", "Check in progress.", steps)

    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(1):
            try:
                with _connection(path) as connection:
                    connection.execute(
                        """UPDATE progress_runs SET heartbeat=? WHERE run_id=?
                        AND status IN ('queued', 'running') AND process_id=?""",
                        (time.time(), run_id, _PROCESS),
                    )
            except (OSError, sqlite3.Error):
                return

    pulse = threading.Thread(target=heartbeat, name=f"progress-heartbeat-{run_id[:8]}", daemon=True)
    try:
        _update(path, run_id, "running", "Check in progress.", steps)
        pulse.start()
        if check_id == "offline-loop":
            summary = _offline(path, run_id, update)
        else:
            summary = asyncio.run(_public(update))
        _update(path, run_id, "passed", summary, steps, finished=True)
    except Exception as exc:
        message = _failure_message(exc)
        for step in steps:
            if step["status"] == "running":
                step.update(status="failed", detail=message)
            elif step["status"] == "queued":
                step.update(status="interrupted", detail="Not completed because an earlier step failed.")
        try:
            _update(path, run_id, "failed", message, steps, finished=True)
        except (OSError, sqlite3.Error):
            # The persisted heartbeat expires; loss of storage must never produce green evidence.
            pass
    finally:
        stop.set()
        if pulse.is_alive():
            pulse.join(timeout=2)
        with _WORKERS_LOCK:
            _WORKERS.pop(run_id, None)


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired, httpx.TimeoutException)):
        return "Check timed out. No successful result was recorded."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Kraken public request returned HTTP {exc.response.status_code}. No account or order was tested."
    if isinstance(exc, httpx.RequestError):
        return "Could not reach Kraken's public API. Check connectivity and try again."
    if isinstance(exc, _CheckFailure):
        return str(exc)[:500]
    return "Check failed before completion. No successful result was recorded."


class _CheckFailure(Exception):
    """Only fixed, non-sensitive messages from the allowlisted check routines."""


def _offline(path: Path, run_id: str, update: Callable[[int, str, str], None]) -> str:
    root = path.parent / "progress-run-work"
    _private_directory(root)
    directory = root / run_id
    directory.mkdir(mode=0o700)
    update(0, "running", "Using a fresh isolated workspace, scripted prices and scripted AI responses.")
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    process = subprocess.Popen(
        [sys.executable, "-I", "-m", "trade_graph.cli", "demo", "--offline", "--work", str(directory)],
        cwd=directory,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        umask=0o077,
    )
    try:
        code = process.wait(timeout=_OFFLINE_TIMEOUT)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        raise
    if code != 0:
        raise _CheckFailure("The isolated scripted graph failed. This is a local rehearsal, not a Kraken trade test.")
    update(0, "passed", "Scripted application loop completed; no external AI calls or exchange orders.")
    update(1, "running", "Reading the isolated rehearsal's check results.")
    evidence = directory / "evidence.json"
    descriptor = os.open(evidence, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 131_072:
            raise _CheckFailure("The local rehearsal evidence is invalid or too large.")
        report = json.loads(stream.read(131_073))
    assertions = report.get("checks", {})
    if (
        not isinstance(assertions, dict)
        or len(assertions) != 14
        or not all(value is True for value in assertions.values())
        or report.get("passed") is not True
        or report.get("paid_calls_enabled") is not False
        or report.get("live_enabled") is not False
        or report.get("external_provider_calls") != 0
    ):
        raise _CheckFailure("The scripted evidence did not pass all expected checks.")
    update(1, "passed", "14 scripted checks passed, including accounting, partial fills and restart recovery.")
    return "14 local scripted checks passed. No real market execution, paid AI calls or Kraken orders were tested."


def _public_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=min(5.0, _PUBLIC_TIMEOUT), follow_redirects=False, headers={"User-Agent": "TradeGraph-PublicCheck/1"}
    )


async def _public(update: Callable[[int, str, str], None]) -> str:
    async with asyncio.timeout(_PUBLIC_TIMEOUT), _public_client() as client:
        pair_keys: set[str] = set()
        for index, (endpoint, params) in enumerate(_PUBLIC_ENDPOINTS):
            update(index, "running", "Requesting a public endpoint; no exchange credentials are used.")
            async with client.stream("GET", f"https://api.kraken.com/0/public/{endpoint}", params=params) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise _CheckFailure("Kraken public response exceeded the check's size limit.")
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError) as exc:
                    raise _CheckFailure("Kraken public response was not valid JSON.") from exc
            if (
                not isinstance(payload, dict)
                or payload.get("error") != []
                or not isinstance(payload.get("result"), dict)
            ):
                raise _CheckFailure("Kraken public API reported an error or an unexpected response shape.")
            result = payload["result"]
            detail, pair_keys = _validate_public(endpoint, result, pair_keys)
            update(index, "passed", f"{detail} Received at {_now()}.")
    return "Four actual Kraken public API checks passed. No account access, private endpoint or order was tested."


def _positive(value: Any) -> bool:
    try:
        number = Decimal(str(value))
        return number.is_finite() and number > 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def _validate_public(endpoint: str, result: dict, pair_keys: set[str]) -> tuple[str, set[str]]:
    if endpoint == "Time":
        if (
            not isinstance(result.get("unixtime"), int)
            or isinstance(result["unixtime"], bool)
            or result["unixtime"] <= 0
            or not isinstance(result.get("rfc1123"), str)
        ):
            raise _CheckFailure("Kraken server-clock response was incomplete.")
        return "Server-clock fields verified", pair_keys
    if endpoint == "SystemStatus":
        if not isinstance(result.get("status"), str) or not isinstance(result.get("timestamp"), str):
            raise _CheckFailure("Kraken exchange-status response was incomplete.")
        if result["status"] != "online":
            raise _CheckFailure("Kraken reports a restricted or offline exchange status; the check did not pass.")
        return "Exchange reports online", pair_keys
    if endpoint == "AssetPairs":
        found = set()
        for key, pair in result.items():
            if not isinstance(pair, dict):
                raise _CheckFailure("Kraken pair-rule response was incomplete.")
            name = pair.get("altname")
            if name not in {"XBTUSD", "ETHUSD"}:
                continue
            if (
                not isinstance(pair.get("base"), str)
                or not isinstance(pair.get("quote"), str)
                or not isinstance(pair.get("pair_decimals"), int)
                or isinstance(pair["pair_decimals"], bool)
                or pair["pair_decimals"] < 0
                or not isinstance(pair.get("lot_decimals"), int)
                or isinstance(pair["lot_decimals"], bool)
                or pair["lot_decimals"] < 0
                or not _positive(pair.get("ordermin"))
            ):
                raise _CheckFailure("Kraken pair rules were missing precision or minimum-order fields.")
            found.add(name)
            pair_keys.add(key)
        if found != {"XBTUSD", "ETHUSD"}:
            raise _CheckFailure("Kraken did not return both requested BTC/USD and ETH/USD pair rules.")
        return "Both requested pairs expose precision and minimum-order fields", pair_keys
    if set(result) != pair_keys or len(pair_keys) != 2:
        raise _CheckFailure("Kraken ticker response did not match both requested pair rules.")
    for ticker in result.values():
        if (
            not isinstance(ticker, dict)
            or not isinstance(ticker.get("a"), list)
            or not isinstance(ticker.get("b"), list)
            or not ticker["a"]
            or not ticker["b"]
            or not _positive(ticker["a"][0])
            or not _positive(ticker["b"][0])
            or Decimal(str(ticker["a"][0])) < Decimal(str(ticker["b"][0]))
        ):
            raise _CheckFailure("Kraken ticker response was missing a usable bid/ask spread.")
    return "Both bid/ask spreads verified; REST receipt time is not an exchange event timestamp", pair_keys
