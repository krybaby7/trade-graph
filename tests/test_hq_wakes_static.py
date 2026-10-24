"""HQ wake-up panels (T39): where they sit on the page, how the script reads, and the help text (static checks)."""

import re
from pathlib import Path

from trade_graph.api import hq_copy

WEB = Path(__file__).resolve().parents[1] / "src" / "trade_graph" / "web"
TEMPLATE = (WEB / "templates" / "hq.html").read_text(encoding="utf-8")
SCRIPT = (WEB / "static" / "hq-wakes.js").read_text(encoding="utf-8")
PANELS = {"wakeplan": "calendar", "watchlist": "calendar", "wakes": "story"}


def _section(name: str) -> str:
    start = TEMPLATE.index(f'<section id="{name}"')
    return TEMPLATE[start:TEMPLATE.index("</section>", start)]


def test_each_panel_sits_in_its_tab_with_a_help_button() -> None:
    for panel, section in PANELS.items():
        part = _section(section)
        assert f'data-hq="{panel}"' in part, panel
        assert f"help('{panel}'," in part, panel
        assert f'aria-labelledby="hq-h-{panel}"' in part and f'id="hq-h-{panel}"' in part
    # The HQ snapshot island stays the only data on the page: the panels read their own endpoint.
    assert TEMPLATE.count('type="application/json"') == 1


def test_the_script_reads_only_its_own_endpoint_through_hq_core_and_starts_from_hq_js() -> None:
    code = re.sub(r"/\*.*?\*/", "", SCRIPT, flags=re.S)
    assert "fetch(" not in code and 'HQ.api.get("wakes"' in code and "HQ.api.post" not in code
    core = (WEB / "static" / "hq-core.js").read_text(encoding="utf-8")
    assert 'wakes: (init, options) => fetch(`/api/v1/hq/wakes${wakesQuery(options)}`, init)' in core
    assert re.search(r'const READS = new Set\(\[[^\]]*"wakes"', core)
    assert 'HQ.use("wakes")?.init();' in (WEB / "static" / "hq.js").read_text(encoding="utf-8")
    # It reads only while one of its panels is on screen (a phone shows one tab at a time).
    assert "IntersectionObserver" in code and 'classList.contains("is-active")' in code


def test_the_help_text_is_the_servers_copy() -> None:
    glossary = (WEB / "static" / "hq-glossary.js").read_text(encoding="utf-8")
    for panel in PANELS:
        text = hq_copy.SECTION_HELP[panel]
        assert re.search(rf'{panel}: \{{title: "[^"]+", text: "{re.escape(text)}"\}}', glossary), panel
        assert "practice" not in text.lower()
