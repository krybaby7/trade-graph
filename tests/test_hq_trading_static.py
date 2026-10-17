"""HQ today's-trading panels (W8-C): where they sit on the page, how the day plans script reads, and the help text
(static checks, no browser)."""

import re
from pathlib import Path

from trade_graph.api import hq_copy

WEB = Path(__file__).resolve().parents[1] / "src" / "trade_graph" / "web"
TEMPLATE = (WEB / "templates" / "hq.html").read_text(encoding="utf-8")
SCRIPT = (WEB / "static" / "hq-plans.js").read_text(encoding="utf-8")
PANELS = ("loss", "plans", "scorecard", "collector")


def _money() -> str:
    start = TEMPLATE.index('<section id="money"')
    return TEMPLATE[start:TEMPLATE.index("</section>", start)]


def test_each_panel_sits_in_the_money_tab_with_a_help_button_in_reading_order() -> None:
    part = _money()
    for panel in PANELS:
        assert f'data-hq="{panel}"' in part, panel
        assert f"help('{panel}'," in part, panel
        assert f'aria-labelledby="hq-h-{panel}"' in part and f'id="hq-h-{panel}"' in part
    order = [part.index(f'data-hq="{name}"') for name in ("money", "loss", "plans", "scorecard", "allowance",
                                                           "spending", "market", "collector")]
    assert order == sorted(order)
    assert part.index('data-hq="collector"') < part.index("data-hq-charts")
    # The snapshot island stays the only data on the page: the day plans read their own endpoint.
    assert TEMPLATE.count('type="application/json"') == 1


def test_the_plans_script_reads_only_its_own_endpoint_through_hq_core_and_starts_from_hq_js() -> None:
    code = re.sub(r"/\*.*?\*/", "", SCRIPT, flags=re.S)
    assert "fetch(" not in code and 'HQ.api.get("plans"' in code and "HQ.api.post" not in code
    core = (WEB / "static" / "hq-core.js").read_text(encoding="utf-8")
    assert 'plans: init => fetch("/api/v1/hq/plans", init)' in core
    assert re.search(r'const READS = new Set\(\[[^\]]*"plans"', core)
    assert 'HQ.use("plans")?.init();' in (WEB / "static" / "hq.js").read_text(encoding="utf-8")
    assert "IntersectionObserver" in code and 'classList.contains("is-active")' in code


def test_the_help_text_is_the_servers_copy() -> None:
    glossary = (WEB / "static" / "hq-glossary.js").read_text(encoding="utf-8")
    for panel in PANELS:
        text = hq_copy.SECTION_HELP[panel]
        assert re.search(rf'{panel}: \{{title: "[^"]+", text: "{re.escape(text)}"\}}', glossary), panel
        assert "practice" not in text.lower()


def test_the_comparison_box_never_says_paper_gains_pay_bills() -> None:
    render = (WEB / "static" / "hq-render.js").read_text(encoding="utf-8")
    view = render.split('HQ.view("money", {', 1)[1].split("\n  });", 1)[0]
    assert '"Paper result against real costs"' in view
    assert "never pays a real bill and never refills your budget" in view
    # The old words stay only for a live account, where trading results are real money.
    for line in view.splitlines():
        if "left after costs" in line or '"Pays for itself?"' in line:
            assert "live" in line, line
