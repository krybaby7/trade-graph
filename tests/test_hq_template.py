"""The HQ template renders a safe island and a readable first paint for every frozen fixture."""

import html
import json
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src" / "trade_graph" / "web" / "templates"
FIXTURES = ROOT / "tests" / "fixtures" / "hq"
CONTAINERS = ["now", "attention", "upnext", "map", "calendar", "checkins", "story", "money", "allowance", "market",
              "decide", "bills", "prompt", "help", "notices", "welcome"]


def render(data: dict | None, **context) -> str:
    environment = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    context = {"role": "owner", "csrf": "test-csrf", "auth_mechanism": "cookie", "operating_mode": "paper", **context}
    return environment.get_template("hq.html").render(data=data or {}, page="hq", **context)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def island(page: str) -> dict:
    match = re.search(r'<script type="application/json" id="hq-snapshot">(.*?)</script>', page, flags=re.S)
    assert match, "JSON island missing"
    return json.loads(match.group(1))


def text_of(page: str, marker: str) -> str:
    match = re.search(rf"<[a-z0-9]+[^>]*{marker}[^>]*>(.*?)</(?:p|div)>", page, flags=re.S)
    assert match, marker
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))


def test_quiet_snapshot_renders_island_headline_and_needs_you_titles():
    data = fixture("snapshot_quiet")
    page = render(data)
    assert island(page) == data
    assert text_of(page, "data-hq-now-headline") == "All quiet. Nobody is working this minute."
    assert 'aria-live="polite"' in page.split("data-hq-now-headline", 1)[1].split(">", 1)[0]
    needs = page.split('data-hq="attention"', 1)[1].split("</article>", 1)[0]
    for item in data["attention"]:
        title = "".join(segment.get("v", "") for segment in item["title"])
        assert html.escape(title, quote=True) in needs or title in html.unescape(needs)
    assert 'data-hq-attention-count>2</span>' in page
    assert "Paper results are simulated" in page
    assert "This is practice: no real trades happen. Real costs are only the bills you enter." in page
    assert "Practice money" in page and "Real trading: off" in page


def test_shell_has_sections_containers_tabs_sheet_and_toast():
    page = render(fixture("snapshot_quiet"))
    for section in ["hq", "calendar", "story", "money", "decide"]:
        assert re.search(rf'<section id="{section}"[^>]*data-hq-section', page), section
    for name in CONTAINERS:
        assert f'data-hq="{name}"' in page, name
    nav = re.search(r'<nav class="hq-tabs" aria-label="HQ sections">(.*?)</nav>', page, flags=re.S)
    assert nav
    assert re.findall(r'href="(#[a-z]+)"', nav.group(1)) == ["#hq", "#calendar", "#story", "#money", "#decide"]
    assert re.search(r'<dialog class="hq-sheet" data-hq-sheet aria-labelledby="hq-sheet-title">', page)
    assert re.search(r'<div class="hq-toast" role="status" aria-live="polite" data-hq-toast>', page)
    assert "data-hq-fresh" in page and "data-hq-root" in page
    assert '<h1 class="hq-title">Trade Graph HQ</h1>' in page
    assert page.count("aria-live=") == 2
    assert '<body class="hq-page">' in page


def test_first_paint_times_are_time_elements_with_a_utc_fallback():
    data = fixture("snapshot_offline")
    page = render(data)
    headline = page.split("data-hq-now-headline", 1)[1].split("</p>", 1)[0]
    times = re.findall(r'<time datetime="([^"]+)">([^<]+)</time>', headline)
    assert times
    for at, words in times:
        assert words == f"{at[11:16]} UTC"


def test_durations_render_in_plain_words_on_first_paint():
    page = render(fixture("snapshot_silent"))
    headline = text_of(page, "data-hq-now-headline")
    assert headline.startswith("No sign of life from the team for ")
    assert re.search(r"for \d+ (?:s|min|h|d)\b", headline)


def test_live_mode_shows_no_paper_copy():
    page = render(fixture("snapshot_live"), operating_mode="live")
    assert "Practice money" not in page
    assert "Paper results are simulated" not in page
    assert "This is practice" not in page
    assert "Live account" in page


def test_reader_render_has_no_csrf_and_still_renders():
    page = render(fixture("snapshot_reader"), role="reader", csrf="reader-csrf", auth_mechanism="bearer")
    assert "reader-csrf" not in page
    assert 'name="csrf-token"' not in page
    assert island(page)["viewer"]["can_act"] is False
    checkins_panel = page.split('aria-labelledby="hq-h-checkins"', 1)[1].split(">", 1)[0]
    assert "hidden" in checkins_panel


def test_owner_cookie_render_exposes_the_csrf_meta():
    page = render(fixture("snapshot_quiet"))
    assert '<meta name="csrf-token" content="test-csrf">' in page


@pytest.mark.parametrize("name", sorted(path.stem for path in FIXTURES.glob("snapshot_*.json")))
def test_every_fixture_renders_with_its_headline(name):
    data = fixture(name)
    page = render(data, operating_mode=data["mode"])
    assert island(page) == data
    expected = "".join(segment.get("v", "") for segment in data["now"]["headline"] if segment["t"] in ("text", "term"))
    assert expected.split(" ")[0] in text_of(page, "data-hq-now-headline")
    assert ("Paper results are simulated" in page) is (data["mode"] == "paper")


def test_empty_data_renders_the_shell_without_invented_values():
    page = render({})
    assert '<main id="main"' in page
    assert '<meta name="viewport"' in page
    assert 'href="/static/dashboard.css"' in page and 'src="/static/dashboard.js"' in page
    assert "Paper results are simulated" in page
    assert "10000" not in page
    assert island(page) == {}
    assert "Reading the latest records" in page
    assert "Nothing needs you" not in page


def test_island_cannot_break_out_of_its_script_element():
    data = fixture("snapshot_quiet")
    data["now"]["headline"] = [{"t": "text", "v": "</script><script>alert(1)</script>"}]
    page = render(data)
    assert "</script><script>alert(1)" not in page
    assert island(page)["now"]["headline"][0]["v"] == "</script><script>alert(1)</script>"
    assert "&lt;/script&gt;" in page
