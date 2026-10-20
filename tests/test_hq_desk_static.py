"""HQ's Trading desk room (W13-A, C13; D302): where the panel sits, how its script reads, and the help text (static
checks, no browser; ``tests/js/hq_desk.test.mjs`` checks the behaviour)."""

import re
from pathlib import Path

from trade_graph.api import hq_copy

WEB = Path(__file__).resolve().parents[1] / "src" / "trade_graph" / "web"
TEMPLATE = (WEB / "templates" / "hq.html").read_text(encoding="utf-8")
SCRIPT = (WEB / "static" / "hq-desk.js").read_text(encoding="utf-8")


def _home() -> str:
    start = TEMPLATE.index('<section id="hq"')
    return TEMPLATE[start:TEMPLATE.index("</section>", start)]


def test_the_room_sits_on_the_hq_tab_below_the_map_with_a_help_button() -> None:
    part = _home()
    assert 'data-hq="desk"' in part and "help('desk'," in part
    assert 'aria-labelledby="hq-h-desk"' in part and 'id="hq-h-desk"' in part
    assert part.index('data-hq="map"') < part.index('data-hq="desk"')
    board = part[part.index('<div class="hq-board">'):]
    assert board.index('data-hq="desk"') < board.rindex("</article>\n    </div>")  # inside the board, last
    assert TEMPLATE.index("/static/hq-plans.js") < TEMPLATE.index("/static/hq-desk.js") < TEMPLATE.index(
        "/static/hq.js")
    assert TEMPLATE.count('type="application/json"') == 1  # the snapshot island stays the only data on the page


def test_the_room_script_reads_only_its_own_endpoint_through_hq_core_and_starts_from_hq_js() -> None:
    code = re.sub(r"/\*.*?\*/", "", SCRIPT, flags=re.S)
    assert "fetch(" not in code and 'HQ.api.get("desk"' in code and "HQ.api.post" not in code
    core = (WEB / "static" / "hq-core.js").read_text(encoding="utf-8")
    assert 'desk: init => fetch("/api/v1/hq/desk", init)' in core
    assert re.search(r'const READS = new Set\(\[[^\]]*"desk"', core)
    assert 'HQ.use("desk")?.init();' in (WEB / "static" / "hq.js").read_text(encoding="utf-8")
    assert "IntersectionObserver" in code and 'classList.contains("is-active")' in code
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code  # model text is always text


def test_the_help_text_is_the_servers_copy_and_never_says_practice() -> None:
    glossary = (WEB / "static" / "hq-glossary.js").read_text(encoding="utf-8")
    text = hq_copy.SECTION_HELP["desk"]
    assert re.search(rf'desk: \{{title: "Trading desk room", text: "{re.escape(text)}"\}}', glossary)
    assert "practice" not in text.lower()


def test_the_desktop_layout_keeps_the_room_in_the_left_column() -> None:
    css = (WEB / "static" / "hq.css").read_text(encoding="utf-8")
    desktop = css[css.index("@media (min-width: 1024px)"):]
    assert "body.hq-page .hq-board > .hq-desk" in desktop.split("float: left", 1)[0]
