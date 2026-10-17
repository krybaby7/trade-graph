"""HQ price charts (T41): the vendored library, its licence and attribution, and the chart script's rules.

Static checks, no browser: the pinned Lightweight Charts copy is byte-identical to its recorded release, it loads only
from HQ's own origin with a pinned integrity, the attribution link and the licence notice sit wherever a chart is
drawn, and the library's own attribution logo (which would inject a <style> element and send HQ's address to
TradingView) stays switched off (D195, D196).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "trade_graph" / "web"
STATIC = WEB / "static"
VENDOR = STATIC / "vendor" / "lightweight-charts"
TEMPLATE = WEB / "templates" / "hq.html"
SCRIPT = "lightweight-charts.standalone.production.js"
ATTRIBUTION = "https://www.tradingview.com/"


def _code(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def _record() -> dict:
    return json.loads((VENDOR / "VENDOR.json").read_text(encoding="utf-8"))


def test_the_vendored_copy_matches_the_pinned_release() -> None:
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "vendor_lightweight_charts.py"), "--check"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    record = _record()
    assert record["version"] == "5.2.1" and record["license"] == "Apache-2.0"
    assert record["sources"]["tarball"]["url"].startswith("https://registry.npmjs.org/lightweight-charts/-/")
    for name, facts in record["files"].items():
        data = (VENDOR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == facts["sha256"] and len(data) == facts["bytes"]
    header = (VENDOR / SCRIPT).read_text(encoding="utf-8")[:300]
    assert "TradingView Lightweight Charts™ v5.2.1" in header and "Apache License 2.0" in header


def test_the_licence_and_notice_are_the_upstream_texts() -> None:
    licence = (VENDOR / "LICENSE.txt").read_text(encoding="utf-8")
    assert "Apache License" in licence and "Version 2.0, January 2004" in licence
    notice = (VENDOR / "NOTICE.txt").read_text(encoding="utf-8")
    assert notice.startswith("TradingView Lightweight Charts™\nCopyright")
    assert "TradingView, Inc. https://www.tradingview.com/" in notice


def test_the_library_is_loaded_lazily_from_hq_itself_with_a_pinned_integrity() -> None:
    code = _code((STATIC / "hq-charts.js").read_text(encoding="utf-8"))
    assert re.findall(r'"(/static/vendor/[^"]+)"', code) == [f"/static/vendor/lightweight-charts/{SCRIPT}"]
    assert f'"{_record()["script_integrity"]}"' in code
    assert "attributionLogo: false" in code
    assert "createElement(\"script\")" in code and ".integrity = " in code
    assert not re.search(r"https?://", code)
    page = TEMPLATE.read_text(encoding="utf-8")
    assert SCRIPT not in page  # never fetched on page load, only when a chart is first shown


def test_every_chart_box_has_the_attribution_link_and_licence_notice_beside_it() -> None:
    page = TEMPLATE.read_text(encoding="utf-8")
    panel = re.search(r'<article class="hq-panel hq-charts".*?</article>', page, flags=re.S)
    assert panel, "the Money tab has a Price charts panel"
    panel = panel.group(0)
    assert page.count("data-hq-chart-box") == 1 and "data-hq-chart-box" in panel
    attribution = re.search(r'<p class="hq-chart-attribution" data-hq-chart-attribution>(.*?)</p>', panel, re.S)
    assert attribution
    text = attribution.group(1)
    assert f'<a href="{ATTRIBUTION}" rel="noopener noreferrer"' in text
    assert 'href="/static/vendor/lightweight-charts/LICENSE.txt"' in text
    assert 'href="/static/vendor/lightweight-charts/NOTICE.txt"' in text
    words = re.sub(r"<[^>]+>", "", text)
    notice = (VENDOR / "NOTICE.txt").read_text(encoding="utf-8").replace("с", "c")  # upstream types a Cyrillic с
    for line in notice.replace(" https://www.tradingview.com/", "").strip().splitlines():
        assert line in words, line
    assert "Apache License 2.0" in words
    # The attribution link is the only address outside HQ on the page, and it is a plain link, never a script.
    assert re.findall(r"https?://[^\s\"'<>]+", page) == [ATTRIBUTION]


def test_the_chart_script_keeps_the_hq_page_rules() -> None:
    code = _code((STATIC / "hq-charts.js").read_text(encoding="utf-8"))
    for forbidden in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                      "cssText", ".style.", "localStorage", "sessionStorage"]:
        assert forbidden not in code, forbidden
    assert code.lstrip().startswith('"use strict";')
    assert re.findall(r"""\bfetch\(""", code) == []  # reads go through HQ.api (hq-core.js) like every HQ read
    assert 'HQ.api.get("chart"' in code


def test_the_vendored_files_ship_with_the_package_and_the_protected_image() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data = project["tool"]["setuptools"]["package-data"]["trade_graph"]
    assert "web/static/vendor/lightweight-charts/*" in data
    builder = (ROOT / "scripts" / "build_protected_image.py").read_text(encoding="utf-8")
    suffixes = re.search(r'path\.suffix not in (\{[^}]+\})', builder).group(1)
    for path in VENDOR.iterdir():
        assert f'"{path.suffix}"' in suffixes, path.name
