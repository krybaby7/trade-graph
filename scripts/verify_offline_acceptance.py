#!/usr/bin/env python3
"""Run the catalogue once and report actual JUnit evidence, never release authorization.

From a Python 3.12 checkout: uv sync --frozen; then
uv run --frozen python scripts/verify_offline_acceptance.py --report /tmp/acceptance.json
Reports, JUnit, logs, pytest temporary files and XDG configuration stay outside the checkout.
Exit zero requires both passing tests and complete applicable T16 coverage. Partial
or pending applicable cases fail the gate. Paid/live/T17/P6 boundaries remain separate.
Missing evidence, skips, collection errors and pytest failures cannot pass.
No real credentials are inherited. Provider fixtures remain synthetic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "planning/offline-acceptance.json"
CASE_IDS = [*(f"A{i:02}" for i in range(1, 39)), "A43", "A44"]
OUTCOMES = ("passed", "failed", "error", "skipped")


def load_catalogue(path: Path) -> dict:
    catalogue = json.loads(path.read_text())
    if catalogue.get("schema_version") != 1:
        raise ValueError("unsupported catalogue schema")
    cases = catalogue["cases"]
    if [case["id"] for case in cases] != CASE_IDS:
        raise ValueError("catalogue must contain A01-A38,A43-A44 exactly once, in order")
    for case in cases:
        coverage = case["coverage"]
        nodes = case["test_node_ids"]
        if coverage not in {"full", "partial", "pending"}:
            raise ValueError(f"{case['id']}: invalid coverage")
        if not isinstance(nodes, list) or len(set(nodes)) != len(nodes):
            raise ValueError(f"{case['id']}: duplicate/invalid test identifiers")
        if coverage != "pending" and not nodes:
            raise ValueError(f"{case['id']}: missing evidence selectors")
        if coverage == "pending" and nodes:
            raise ValueError(f"{case['id']}: pending identifiers belong in coordination, not runnable evidence")
        if (coverage == "full") != (not case["uncovered_subrequirements"]):
            raise ValueError(f"{case['id']}: coverage contradicts uncovered subrequirements")
        for node in nodes:
            file, separator, name = node.partition("::")
            if (
                not separator
                or not name
                or not file.startswith("tests/")
                or not file.endswith(".py")
                or ".." in Path(file).parts
                or "\\" in file
            ):
                raise ValueError(f"{case['id']}: expected a repository pytest node ID")
    return catalogue


def selected_nodes(catalogue: dict) -> list[str]:
    return sorted({node for case in catalogue["cases"] for node in case["test_node_ids"]})


def matches(selector: str, node: str) -> bool:
    # A function node ID selects every parameter variant, with each expanded ID
    # recorded from pytest collection and JUnit, not guessed from its display name.
    return node == selector or ("[" not in selector and node.startswith(selector + "["))


def counts(records: list[dict]) -> dict:
    counter = Counter(record["outcome"] for record in records)
    return {"total": len(records), **{outcome: counter[outcome] for outcome in OUTCOMES}}


def parse_junit(path: Path) -> tuple[list[dict], list[str], list[dict]]:
    issues: list[str] = []
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return [], ["JUnit missing or malformed"], []
    if root.tag not in {"testsuites", "testsuite"}:
        return [], ["unexpected JUnit root"], []
    records = []
    for element in root.iter("testcase"):
        identifiers = [
            prop.get("value")
            for prop in element.findall("./properties/property")
            if prop.get("name") == "offline_node_id"
        ]
        node_id = identifiers[0] if len(identifiers) == 1 and identifiers[0] else None
        problems = [child for child in element if child.tag in {"failure", "error", "skipped"}]
        tags = {child.tag for child in problems}
        outcome = (
            "error"
            if "error" in tags
            else "failed"
            if "failure" in tags
            else ("skipped" if "skipped" in tags else "passed")
        )
        if node_id is None:
            issues.append("JUnit testcase lacks an unambiguous pytest node ID: " + element.get("name", ""))
        records.append(
            {
                "node_id": node_id,
                "junit_name": element.get("name"),
                "outcome": outcome,
                "duration_seconds": element.get("time"),
                "details": [
                    {
                        "kind": child.tag,
                        "type": child.get("type"),
                        "message": child.get("message", ""),
                        "text": child.text or "",
                    }
                    for child in problems
                ],
            }
        )
    if not records:
        issues.append("JUnit contains no test outcomes")
    declared = [
        {key: suite.get(key) for key in ("name", "tests", "failures", "errors", "skipped")}
        for suite in root.iter("testsuite")
    ]
    return records, issues, declared


def build_results(
    catalogue: dict, collected: list[str], records: list[dict], pytest_exit_code: int | None, issues: list[str]
) -> dict:
    selectors = selected_nodes(catalogue)
    issues = list(issues)
    if not collected:
        issues.append("no recorded pytest collection")
    if len(set(collected)) != len(collected):
        issues.append("duplicate collected pytest node IDs")
    for selector in selectors:
        if not any(matches(selector, node) for node in collected):
            issues.append(f"selected node was not collected: {selector}")
    for node in collected:
        if not any(matches(selector, node) for selector in selectors):
            issues.append(f"unexpected collected node: {node}")
    observed = {record["node_id"] for record in records if record["node_id"]}
    missing = sorted(set(collected) - observed)
    unexpected = sorted(observed - set(collected))
    if missing:
        issues.append("collected tests lack JUnit outcomes")
    if unexpected:
        issues.append("JUnit contains tests outside recorded collection")
    results = []
    for case in catalogue["cases"]:
        expected = sorted(node for node in collected if any(matches(s, node) for s in case["test_node_ids"]))
        evidence = [record for record in records if record["node_id"] in expected]
        missing_selectors = [s for s in case["test_node_ids"] if not any(matches(s, n) for n in collected)]
        missing_nodes = sorted(set(expected) - observed)
        tally = counts(evidence)
        outcome = "not_run"
        if case["coverage"] != "pending":
            if tally["error"]:
                outcome = "error"
            elif tally["failed"]:
                outcome = "failed"
            elif missing_selectors or missing_nodes or not evidence:
                outcome = "incomplete"
            elif tally["skipped"]:
                outcome = "skipped"
            else:
                outcome = "passed"
        # A completed failing session still provides independent passing cases;
        # interrupted/invalid sessions cannot certify earlier partial evidence.
        acceptance = (
            "pending"
            if case["coverage"] == "pending"
            else (
                "passed"
                if outcome == "passed" and case["coverage"] == "full" and pytest_exit_code in {0, 1} and not issues
                else "partial"
                if outcome == "passed" and case["coverage"] == "partial" and pytest_exit_code in {0, 1} and not issues
                else "unverified"
            )
        )
        results.append(
            {
                **case,
                "test_outcome": outcome,
                "acceptance_status": acceptance,
                "actual_test_node_ids": expected,
                "missing_selectors": missing_selectors,
                "missing_outcome_node_ids": missing_nodes,
                "counts": tally,
                "evidence": evidence,
            }
        )
    tally = counts(records)
    status = (
        "error"
        if issues or pytest_exit_code is None or pytest_exit_code not in {0, 1}
        else (
            "failed"
            if pytest_exit_code == 1 or tally["failed"] or tally["error"]
            else "incomplete"
            if tally["skipped"]
            else "passed"
        )
    )
    tests_passed = status == "passed"
    gate_complete = tests_passed and all(r["acceptance_status"] == "passed" for r in results)
    return {
        "suite_status": status,
        "tests_passed": tests_passed,
        "gate_complete": gate_complete,
        "gate_status": "complete" if gate_complete else "incomplete",
        "runner_exit_code": 0
        if gate_complete
        else (pytest_exit_code if pytest_exit_code and pytest_exit_code > 0 else 1),
        "pytest_exit_code": pytest_exit_code,
        "counts": tally,
        "collected_count": len(collected),
        "unique_observed_count": len(observed),
        "missing_outcome_node_ids": missing,
        "unexpected_outcome_node_ids": unexpected,
        "report_issues": issues,
        "case_status_counts": dict(Counter(r["acceptance_status"] for r in results)),
        "acceptance_complete": gate_complete,
        "cases": results,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(root: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    paths = sorted(
        subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
        )
        .decode()
        .split("\0")
    )
    manifest = [(path, sha256(root / path) if (root / path).is_file() else "missing") for path in paths if path]
    return {
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "working_tree_status": git("status", "--porcelain=v1", "--untracked-files=normal"),
        "working_files_sha256": hashlib.sha256(json.dumps(manifest).encode()).hexdigest(),
        "uv_lock_sha256": sha256(root / "uv.lock"),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
    }


def require_external(path: Path, root: Path) -> Path:
    path = path.resolve()
    if path == root.resolve() or root.resolve() in path.parents:
        raise ValueError("test artifacts must be outside the repository checkout")
    return path


def offline_env(runtime: Path) -> dict[str, str]:
    # Do not inherit credentials, pytest options, PYTHONPATH, tracing endpoints,
    # or proxies. XDG configuration is temporary; Git global/system config is disabled.
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "TMPDIR": str(runtime / "tmp"),
        "XDG_CONFIG_HOME": str(runtime / "config"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "HYPOTHESIS_STORAGE_DIRECTORY": str(runtime / "hypothesis"),
    }


# These hooks run only in the child pytest process when loaded with -p.
def pytest_addoption(parser):
    parser.addoption("--offline-collection-out", default=None)


def pytest_configure(config):
    if not config.getoption("--offline-collection-out"):
        return

    def deny_network(*args, **kwargs):
        raise OSError("offline acceptance harness forbids network connections")

    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.getaddrinfo = deny_network


def pytest_collection_modifyitems(config, items):
    destination = config.getoption("--offline-collection-out")
    if destination:
        for item in items:
            item.user_properties.append(("offline_node_id", item.nodeid))
        Path(destination).write_text(json.dumps([item.nodeid for item in items]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, help="JSON destination outside the checkout (default: new /tmp directory)"
    )
    parser.add_argument("--timeout", type=float, default=600, help="maximum suite duration in seconds")
    args = parser.parse_args(argv)
    try:
        if sys.version_info[:2] != (3, 12):
            raise ValueError("use the repository Python 3.12 environment after uv sync --frozen")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        catalogue = load_catalogue(CATALOGUE)
        parent = require_external(args.report.parent if args.report else Path(tempfile.gettempdir()), ROOT)
        if args.report:
            require_external(args.report, ROOT)
        parent.mkdir(parents=True, exist_ok=True)
        runtime = Path(tempfile.mkdtemp(prefix="trade-graph-acceptance-", dir=parent))
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    report_path = args.report.resolve() if args.report else runtime / "report.json"
    for directory in ("config", "tmp"):
        (runtime / directory).mkdir()
    junit, collection, log = (runtime / name for name in ("junit.xml", "collected.json", "pytest.log"))
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "scripts.verify_offline_acceptance",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit1",
        "--junitxml",
        str(junit),
        "--offline-collection-out",
        str(collection),
        "--basetemp",
        str(runtime / "pytest"),
        *selected_nodes(catalogue),
    ]
    started = datetime.now(UTC).isoformat()
    before = identity(ROOT)
    issues = []
    pytest_exit_code = None
    with log.open("w") as output:
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=offline_env(runtime),
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                check=False,
            )
            pytest_exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            issues.append("pytest timed out")
        except KeyboardInterrupt:
            issues.append("runner interrupted")
        except OSError:
            issues.append("pytest process could not start")
    records, junit_issues, declared = parse_junit(junit)
    issues.extend(junit_issues)
    try:
        collected = json.loads(collection.read_text())
        if not isinstance(collected, list) or not all(isinstance(node, str) for node in collected):
            raise ValueError("invalid collection")
    except (OSError, ValueError):
        collected = []
        issues.append("pytest collection manifest missing or malformed")
    after = identity(ROOT)
    if after != before:
        issues.append("checkout identity changed during the suite")
    results = build_results(catalogue, collected, records, pytest_exit_code, issues)
    report = {
        "schema_version": 1,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "identity": before,
        "identity_after": after,
        "catalogue_sha256": sha256(CATALOGUE),
        "scope": catalogue.get("scope"),
        "coverage_semantics": catalogue.get("coverage_semantics", {}),
        "criterion_sources": catalogue.get("criterion_sources", {}),
        "command": command,
        "offline_controls": {
            "inherited_credentials": False,
            "environment": "allowlist; no inherited proxies, tracing, pytest options or PYTHONPATH",
            "configuration": "temporary XDG_CONFIG_HOME; Git global/system configuration disabled",
            "pytest_plugin_autoload": False,
            "python_socket_connections": "denied in pytest process",
            "evidence_kind": "synthetic; no paid-provider or venue verification",
        },
        "artifacts": {
            "report": str(report_path),
            "junit": str(junit),
            "pytest_log": str(log),
            "collection": str(collection),
            "junit_sha256": sha256(junit) if junit.exists() else None,
        },
        "junit_declared_suites": declared,
        "pending_boundaries": catalogue["pending_boundaries"],
        **results,
    }
    report["coordination"] = catalogue.get("coordination", [])
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(report_path),
                "suite_status": results["suite_status"],
                "tests_passed": results["tests_passed"],
                "gate_complete": results["gate_complete"],
                "pytest_exit_code": results["pytest_exit_code"],
                "runner_exit_code": results["runner_exit_code"],
                "counts": results["counts"],
                "incomplete_case_ids": [
                    case["id"] for case in results["cases"] if case["acceptance_status"] != "passed"
                ],
            }
        )
    )
    return results["runner_exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
