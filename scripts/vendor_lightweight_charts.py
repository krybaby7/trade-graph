#!/usr/bin/env python3
"""Vendor or check the pinned TradingView Lightweight Charts release that HQ's price charts load (T41, D195).

HQ serves the library from its own origin: the browser never fetches a script or data from anywhere else. This script
is the only way the vendored files change.

    python3 scripts/vendor_lightweight_charts.py --check   # offline: the committed files match VENDOR.json
    python3 scripts/vendor_lightweight_charts.py --fetch   # network: download the pinned release and verify it

``--fetch`` downloads the npm registry tarball of ``VERSION`` and refuses it unless its SHA-512 equals the registry
integrity pinned below; it takes the standalone production build and the LICENSE from that tarball, and the NOTICE
from the release tag on GitHub, refused unless its SHA-256 equals the pin. To move to a newer release, read the new
version's ``dist.integrity`` from https://registry.npmjs.org/lightweight-charts, update the pins, run ``--fetch``,
update the ``integrity`` attribute in ``hq-charts.js`` (``--check`` prints the expected value) and run the tests.
Only the Python standard library is used; nothing here runs at HQ runtime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "trade_graph" / "web" / "static" / "vendor" / "lightweight-charts"
LOADER = ROOT / "src" / "trade_graph" / "web" / "static" / "hq-charts.js"

VERSION = "5.2.1"
PUBLISHED = "2026-08-12T11:37:41.377Z"  # the registry's publication time of VERSION ("time" in its metadata)
TARBALL_URL = f"https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-{VERSION}.tgz"
TARBALL_INTEGRITY = ("sha512-IVwoK1RLFiLPubaKIjNbtjWLnpPMqiABSrTay6whmNa8L1+19292VtHJ+BWyPUuLCwF0tcQlhEWd1CLB2a1nsQ==")
NOTICE_URL = f"https://raw.githubusercontent.com/tradingview/lightweight-charts/v{VERSION}/NOTICE"
NOTICE_SHA256 = "f76c6afab94884448f0426e30d6e9d555ca7247894cd3484e477d2f87513036e"
# Tarball member -> vendored file name. The image build accepts only known suffixes, so the texts end in .txt.
MEMBERS = {
    "package/dist/lightweight-charts.standalone.production.js": "lightweight-charts.standalone.production.js",
    "package/LICENSE": "LICENSE.txt",
}
SCRIPT = "lightweight-charts.standalone.production.js"
MAX_DOWNLOAD = 8 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sri(data: bytes) -> str:
    """The Subresource Integrity value the page's loader pins for the script."""
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https pins above
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise SystemExit(f"refusing {url}: larger than {MAX_DOWNLOAD} bytes")
    return data


def fetch(fetched_on: str) -> dict:
    archive = _download(TARBALL_URL)
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii")
    if integrity != TARBALL_INTEGRITY:
        raise SystemExit(f"refusing the tarball: integrity {integrity} is not the pinned {TARBALL_INTEGRITY}")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member, name in MEMBERS.items():
            item = bundle.getmember(member)
            if not item.isfile():
                raise SystemExit(f"refusing the tarball: {member} is not a regular file")
            files[name] = bundle.extractfile(item).read()
    notice = _download(NOTICE_URL)
    if sha256(notice) != NOTICE_SHA256:
        raise SystemExit(f"refusing the NOTICE: SHA-256 {sha256(notice)} is not the pinned {NOTICE_SHA256}")
    files["NOTICE.txt"] = notice
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (TARGET / name).write_bytes(data)
    record = manifest(files, fetched_on)
    (TARGET / "VENDOR.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def manifest(files: dict[str, bytes], fetched_on: str) -> dict:
    return {
        "name": "lightweight-charts",
        "title": "TradingView Lightweight Charts",
        "version": VERSION,
        "published": PUBLISHED,
        "license": "Apache-2.0",
        "homepage": "https://www.tradingview.com/lightweight-charts/",
        "attribution_link": "https://www.tradingview.com/",
        "fetched_on": fetched_on,
        "sources": {
            "tarball": {"url": TARBALL_URL, "integrity": TARBALL_INTEGRITY,
                        "members": {name: member for member, name in MEMBERS.items()}},
            "notice": {"url": NOTICE_URL},
        },
        "files": {name: {"sha256": sha256(data), "bytes": len(data)} for name, data in sorted(files.items())},
        "script_integrity": sri(files[SCRIPT]),
    }


def check() -> list[str]:
    """Problems with the committed copy; an empty list means it is exactly the pinned release."""
    problems = []
    record = json.loads((TARGET / "VENDOR.json").read_text(encoding="utf-8"))
    if record.get("version") != VERSION or record["sources"]["tarball"].get("integrity") != TARBALL_INTEGRITY:
        problems.append("VENDOR.json does not name the pinned version and tarball integrity")
    expected = {*MEMBERS.values(), "NOTICE.txt"}
    if set(record.get("files", {})) != expected:
        problems.append(f"VENDOR.json lists {sorted(record.get('files', {}))}, expected {sorted(expected)}")
    present = {path.name for path in TARGET.iterdir()} - {"VENDOR.json"}
    if present != expected:
        problems.append(f"the vendor folder holds {sorted(present)}, expected {sorted(expected)}")
    for name in sorted(expected & present):
        data = (TARGET / name).read_bytes()
        if sha256(data) != record["files"].get(name, {}).get("sha256"):
            problems.append(f"{name} does not match its recorded SHA-256")
    if "NOTICE.txt" in present and sha256((TARGET / "NOTICE.txt").read_bytes()) != NOTICE_SHA256:
        problems.append("NOTICE.txt is not the pinned NOTICE")
    if SCRIPT in present:
        integrity = sri((TARGET / SCRIPT).read_bytes())
        if record.get("script_integrity") != integrity:
            problems.append("VENDOR.json script_integrity is not the script's")
        loader = LOADER.read_text(encoding="utf-8") if LOADER.is_file() else ""
        if f'"{integrity}"' not in loader:
            problems.append(f"hq-charts.js must pin integrity {integrity}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="verify the committed copy offline")
    action.add_argument("--fetch", action="store_true", help="download the pinned release and write the copy")
    args = parser.parse_args()
    if args.fetch:
        record = fetch(datetime.now(UTC).strftime("%Y-%m-%d"))
        print(json.dumps({"vendored": record["version"], "files": record["files"],
                          "script_integrity": record["script_integrity"]}, indent=2))
    problems = check()
    for problem in problems:
        print(f"vendor check: {problem}", file=sys.stderr)
    if not problems:
        print(f"Lightweight Charts {VERSION}: vendored copy matches its pins")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
