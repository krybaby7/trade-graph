"""HQ page assets keep the CSP, authority and storage rules of spec §C.2 (static checks, no browser)."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from trade_graph.api import hq_contract

WEB = Path(__file__).resolve().parents[1] / "src" / "trade_graph" / "web"
STATIC = WEB / "static"
TEMPLATE = WEB / "templates" / "hq.html"
SCRIPTS = ["hq-core.js", "hq-glossary.js", "hq-render.js", "hq-prompts.js", "hq-decide.js", "hq-checkins.js", "hq.js"]
FETCH_ALLOWLIST = {
    "/api/v1/hq", "/api/v1/hq/story", "/api/v1/owner/config", "/api/v1/owner/pause", "/api/v1/owner/resume",
    "/api/v1/owner/start-trading", "/api/v1/owner/start-optimisation", "/api/v1/owner/stop-service",
    "/api/v1/owner/expenses", "/api/v1/owner/checkins",
}
STORAGE_KEYS = {
    "tg-hq-welcome-v1", "tg-hq-last-seen", "tg-hq-technical", "tg-hq-prompt-settings", "tg-hq-tab", "tg-hq-pending:",
}
SVG_NAMESPACE = 'xmlns="http://www.w3.org/2000/svg"'


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _code(source: str) -> str:
    """JavaScript without comments, so the checks look at executable code."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def test_template_scripts_are_external_or_a_json_island_in_the_specified_order():
    page = TEMPLATE.read_text(encoding="utf-8")
    tags = re.findall(r"<script\b([^>]*)>", page)
    assert tags
    for attributes in tags:
        assert re.search(r'src="/static/[a-z-]+\.js"', attributes) or 'type="application/json"' in attributes
        if "src=" in attributes:
            assert re.search(r"\bdefer\b", attributes)
    sources = re.findall(r'<script src="/static/([a-z-]+\.js)"', page)
    assert sources == SCRIPTS
    assert page.count('type="application/json"') == 1
    assert 'id="hq-snapshot"' in page
    assert re.search(r"<noscript>.*?/journal.*?</noscript>", page, flags=re.S)
    assert '<link rel="stylesheet" href="/static/hq.css">' in page


@pytest.mark.parametrize("path", [TEMPLATE, STATIC / "hq-icons.svg"])
def test_markup_has_no_inline_style_or_event_handlers(path):
    text = path.read_text(encoding="utf-8")
    assert "style=" not in text
    assert not re.search(r"\son[a-z]+\s*=", text)
    assert "<style" not in text
    assert "javascript:" not in text


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_never_build_markup_from_strings_or_evaluate_code(name):
    code = _code(_read(name))
    for forbidden in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                      "cssText", "setTimeout(\"", "setInterval(\"", ".style."]:
        assert forbidden not in code, f"{name} uses {forbidden}"
    assert not re.search(r"""setAttribute\(\s*["']style["']""", code)
    assert not re.search(r"""setAttribute\(\s*["']on""", code)
    assert code.lstrip().startswith('"use strict";')


@pytest.mark.parametrize("name", [*SCRIPTS, "hq.css"])
def test_no_external_urls_in_scripts_or_styles(name):
    assert not re.search(r"https?://", _read(name))


def test_markup_has_no_external_urls_except_the_svg_namespace():
    assert not re.search(r"https?://", TEMPLATE.read_text(encoding="utf-8"))
    icons = _read("hq-icons.svg")
    assert icons.count(SVG_NAMESPACE) == 1
    assert not re.search(r"https?://", icons.replace(SVG_NAMESPACE, ""))


def test_every_fetch_is_a_literal_allowlisted_endpoint_with_cookie_and_cache_rules():
    seen = set()
    for name in SCRIPTS:
        code = _code(_read(name))
        calls = re.findall(r"\bfetch\(\s*(.)", code)
        for opening in calls:
            assert opening in "\"'`", f"{name}: fetch target is not a literal"
        for path in re.findall(r"""\bfetch\(\s*["'`]([^"'`?$]*)""", code):
            assert path in FETCH_ALLOWLIST, f"{name}: {path} is not allowlisted"
            seen.add(path)
        if calls:
            assert 'credentials: "same-origin"' in code
            assert 'cache: "no-store"' in code
    assert seen == FETCH_ALLOWLIST


def test_owner_writes_map_fixed_decisions_to_fixed_endpoints():
    code = _code(_read("hq-decide.js"))
    assert "fetch(" not in code
    mapping = dict(re.findall(r'\b([a-z_]+): "([a-z-]+)"', code.split("const ENDPOINT", 1)[1].split("});", 1)[0]))
    assert mapping == {
        "start_trading": "start-trading", "pause_ai": "pause", "resume": "resume",
        "start_optimisation": "start-optimisation", "stop_safely": "stop-service",
        "record_bill": "expenses", "declare_bills_complete": "expenses",
    }
    assert 'profile: "MANAGE_ONLY", reason: "Owner HQ: pause AI, keep protection"' in code
    assert 'position_policy: "manage-only"' in code
    assert 'mode: "paper"' in code
    for name in SCRIPTS:
        # No script posts to a path that came from the snapshot: hrefs are only ever used as links.
        assert not re.search(r"api\.post\(\s*[a-z_.]*(?:href|link|url)", _code(_read(name)), flags=re.I)


def test_link_check_matches_the_contract_pattern():
    match = re.search(r"const ALLOWED_PATH = /(.+)/;", _read("hq-core.js"))
    assert match
    assert match.group(1).replace("\\/", "/") == hq_contract.ALLOWED_PATH_PATTERN


def test_storage_is_only_touched_inside_the_core_store_with_the_specified_keys():
    for name in SCRIPTS:
        code = _code(_read(name))
        if name == "hq-core.js":
            source = _read(name)
            before, rest = source.split("/* store:begin", 1)
            inside, after = rest.split("/* store:end */", 1)
            assert "Storage" in inside
            assert not re.search(r"localStorage|sessionStorage", before + after)
        else:
            assert not re.search(r"localStorage|sessionStorage", code), name
        for key in re.findall(r"""["'`](tg-hq-[a-z0-9:-]*)""", code):
            assert key in STORAGE_KEYS, f"{name} uses unlisted storage key {key}"


def test_glossary_keys_match_the_contract():
    source = _read("hq-glossary.js").split("const GLOSSARY = Object.freeze({", 1)[1].split("\n  });", 1)[0]
    keys = re.findall(r"^    ([a-z0-9_]+): \{", source, flags=re.M)
    assert keys == list(hq_contract.GLOSSARY_KEYS)


def test_icons_cover_every_room_and_light():
    root = ET.fromstring(_read("hq-icons.svg"))
    ids = {element.get("id") for element in root.iter() if element.get("id")}
    assert set(hq_contract.DEPT_IDS) <= ids
    assert {f"light-{light}" for light in hq_contract.LIGHTS} <= ids
    assert {"tab-hq", "tab-calendar", "tab-story", "tab-money", "tab-decide"} <= ids


def _css_rules(text: str):
    """Yield (selector, body) pairs for style rules outside @keyframes."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    position, depth, stack = 0, 0, []
    prelude_start = 0
    while position < len(text):
        character = text[position]
        if character == "{":
            prelude = text[prelude_start:position].strip()
            stack.append(prelude)
            depth += 1
            prelude_start = position + 1
            body_start = position + 1
        elif character == "}":
            prelude = stack.pop()
            depth -= 1
            inside_keyframes = any(item.startswith("@keyframes") for item in stack)
            if not prelude.startswith("@") and not inside_keyframes:
                yield prelude, text[body_start:position]
            prelude_start = position + 1
        position += 1
    assert depth == 0


def test_stylesheet_is_scoped_tokenised_and_self_contained():
    text = _read("hq.css")
    assert "@import" not in text
    assert not re.search(r"url\(\s*['\"]?https?:", text)
    assert "url(" not in text
    rules = list(_css_rules(text))
    assert len(rules) > 100
    for selector, body in rules:
        for part in selector.split(","):
            assert "body.hq-page" in part, f"unscoped selector: {part.strip()}"
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), f"colour literal in {selector}"
        if "rgb(" in body or "hsl(" in body:
            assert selector.endswith("::backdrop"), f"colour function outside the sheet backdrop: {selector}"
    assert "prefers-reduced-motion" in text
    assert "env(safe-area-inset-bottom" in text
    assert "@media (min-width: 768px)" in text and "@media (min-width: 1024px)" in text


def test_gauge_positions_have_a_class_for_every_five_percent():
    text = _read("hq.css")
    for value in range(0, 101, 5):
        assert f"body.hq-page .hq-at-{value} {{ left: {value}%; }}" in text


def test_copy_never_uses_banned_jargon_in_static_strings():
    banned = ["container", "worker process", "reconciliation", "inference", "quota admission", "MANAGE_ONLY", "SMA"]
    for name in ["hq-glossary.js", "hq-render.js", "hq-checkins.js"]:
        strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', _code(_read(name)))
        for text in strings:
            if " " not in text:
                continue  # identifiers, keys and class names are not owner-facing copy
            for word in banned:
                assert word not in text, f"{name}: {word!r} in {text!r}"
