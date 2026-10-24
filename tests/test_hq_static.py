"""HQ page assets keep the CSP, authority and storage rules of spec §C.2 (static checks, no browser)."""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from trade_graph.api import hq_contract

WEB = Path(__file__).resolve().parents[1] / "src" / "trade_graph" / "web"
STATIC = WEB / "static"
TEMPLATE = WEB / "templates" / "hq.html"
SCRIPTS = ["hq-core.js", "hq-glossary.js", "hq-render.js", "hq-prompts.js", "hq-decide.js", "hq-checkins.js",
           "hq-charts.js", "hq-wakes.js", "hq-plans.js", "hq-desk.js", "hq.js"]
FETCH_ALLOWLIST = {
    "/api/v1/hq", "/api/v1/hq/story", "/api/v1/hq/chart", "/api/v1/hq/wakes", "/api/v1/hq/plans", "/api/v1/hq/desk",
    "/api/v1/owner/config",
    "/api/v1/owner/pause",
    "/api/v1/owner/resume",
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
    # T41 (D196): the one exception is the plain TradingView attribution link that Lightweight Charts' licence asks
    # for beside every chart (tests/test_hq_charts_static.py checks where it sits).
    page = TEMPLATE.read_text(encoding="utf-8")
    assert re.findall(r"https?://[^\s\"'<>]*", page) == ["https://www.tradingview.com/"]
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
    for name in ["hq-glossary.js", "hq-render.js", "hq-checkins.js", "hq-wakes.js", "hq-plans.js", "hq-desk.js"]:
        strings = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', _code(_read(name)))
        for text in strings:
            if " " not in text:
                continue  # identifiers, keys and class names are not owner-facing copy
            for word in banned:
                assert word not in text, f"{name}: {word!r} in {text!r}"


# --- Review fixes (FIX-PLAN F1–F12): static guards; tests/js/*.test.mjs checks the behaviour -----------------


def _function(source: str, name: str) -> str:
    """The text of one `function name(` up to the next function (or view) at the same indent."""
    match = re.search(rf"(?m)^(\s*)(?:async )?function {name}\(", source)
    assert match, f"function {name} missing"
    indent = match.group(1)
    rest = source[match.start():]
    following = re.search(rf"(?m)^{indent}(?:async )?function \w+\(|^{indent}HQ\.(?:view|define)\(", rest[1:])
    return rest if not following else rest[: following.start() + 1]


def _view(source: str, name: str) -> str:
    start = source.index(f'HQ.view("{name}"')
    return source[start: source.index("\n  });\n", start)]


@pytest.mark.parametrize("name", ["hq-prompts.js", "hq-glossary.js", "hq-decide.js", "hq-render.js", "hq-checkins.js",
                                  "hq-wakes.js"])
def test_practice_copy_always_sits_behind_a_mode_branch(name):
    # Review C1: every owner-facing literal that says "practice" is on a line that branches on the mode, or
    # lives in a constant with a live twin (NAME_LIVE / LIVE_NAME), or is a paper-only glossary word.
    code = _code(_read(name))
    current = None
    for line in code.splitlines():
        declared = re.match(r"^  const ([A-Z_]+) = ", line)
        if declared:
            current = declared.group(1)
        elif re.match(r"^  (?:async function|function|HQ\.|const [a-z])", line):
            current = None
        for parts in re.findall(r'"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`', line):
            words = re.sub(r"\$\{[^}]*\}", "", "".join(parts))  # interpolations are code, not copy
            words = re.sub(r"\b[a-z]+_[a-z_]+\b", "", words)  # keys such as practice_money
            if "practice" not in words.lower() or re.search(r"\blive\b|isLive\(\)", line):
                continue
            if current == "GLOSSARY":
                assert re.match(r"^\s*(?:practice_money:|example:)", line), line
                continue
            assert current and (f"{current}_LIVE" in code or f"LIVE_{current}" in code), f"{name}: {line.strip()}"
    if name == "hq-glossary.js":
        assert 'PAPER_ONLY_TERMS = new Set(["practice_money", "real_trading_off"])' in code


def test_live_twins_exist_for_every_practice_line():
    assert "KIND_PARAGRAPH_LIVE" in _read("hq-prompts.js")
    glossary = _read("hq-glossary.js")
    assert "ROOM_WHY_LIVE" in glossary and "SECTION_HELP_LIVE" in glossary
    assert '"The only department that can move the account\'s money."' in glossary
    assert "Account money and real costs are kept apart. Unknown is never shown as zero." in glossary
    decide = _read("hq-decide.js")
    for name in ["BARRIERS_LIVE", "BILLS_INTRO_LIVE", "BILL_UNTOUCHED_LIVE"]:
        assert f"const {name} = " in decide
    bills = _view(_code(decide), "bills")
    assert "act_block_reason" in bills and "BILLS_INTRO_LIVE" in bills


def test_up_next_and_tiles_never_show_an_impossible_turn_as_normal():
    # Review U1: Up next shows the item's condition; an overdue tile says Waiting / Due now / Overdue.
    code = _code(_read("hq-render.js"))
    upnext = _view(code, "upnext")
    assert "item.detail" in upnext and "item.conditional" in upnext and "item.waiting_because" in upnext
    tile = _function(code, "roomLine")
    assert "OVERDUE_WORDS.get(next.status)" in tile and '|| "Overdue"' in tile and '"Due now"' not in tile
    assert 'OVERDUE_WORDS = new Map([["waiting", "Waiting"], ["due_now", "Due now"], ["overdue", "Overdue"]])' in code


def test_calendar_now_group_strip_glyphs_and_estimate_series():
    code = _code(_read("hq-render.js"))
    calendar = _view(code, "calendar")
    assert "!item.past" not in calendar and "CURRENT.has(item.status)" in calendar  # review C4
    strip = _function(code, "weekStrip")
    assert "statusMark(item)" in strip and '"hq-sr"' in strip  # review U14: glyph and words, not colour alone
    assert '"hq-cal-now-line", "Now"' in calendar
    assert "calendar.estimates" in code and "seriesTimes" in calendar  # review T2


def test_open_panels_survive_a_poll():
    # Review T3: the moving window fields are not render inputs, and open <details> are remembered.
    code = _code(_read("hq-render.js"))
    story_deps = _view(code, "story").split("render(root", 1)[0]
    calendar_deps = _view(code, "calendar").split("render(root", 1)[0]
    assert "snapshot.story," not in story_deps and "story.window_start ?" in story_deps
    assert "calendar.from" not in calendar_deps and "calendar.to" not in calendar_deps
    assert "snapshot.calendar," not in calendar_deps
    for key in ["story-more:", "story-day:", "cal:earlier", "series:", "help:sections", "help:glossary"]:
        assert key in code, key
    core = _code(_read("hq-core.js"))
    assert "function keepOpen(" in core and "reopenTimes(container, times)" in core
    assert "restoreScroll(container, anchor)" in core


def test_toasts_show_inside_an_open_sheet_and_done_offers_the_next_plan():
    toast = _function(_code(_read("hq-core.js")), "toast")
    assert "state.sheet" in toast and "[data-hq-sheet-toast]" in toast  # review U4
    checkins = _code(_read("hq-checkins.js"))
    assert "Plan the next one in 4 h" in checkins and "offerNext" not in checkins


def test_reserve_rule_unknown_is_never_none():
    allowance = _view(_code(_read("hq-render.js")), "allowance")
    assert "allowance.stop_line_state" in allowance  # review U5
    assert "The reserve rule couldn't be read." in allowance and "No reserve rule is set." in allowance


def test_bill_wizard_steps_move_focus_and_use_utc_months_and_percentages():
    decide = _code(_read("hq-decide.js"))
    for step in ["stepKind", "stepAmount", "stepShare", "stepPreview", "stepChoose"]:
        body = decide.split(f"const {step} = ", 1)[1].split("\n        };", 1)[0]
        assert "focusStep(body)" in body, step  # review U6
    assert "Date.UTC(year, month, 1)" in decide and 'timeZone: "UTC"' in decide  # review C14
    assert "new Date(year, month" not in decide
    assert '"Custom share (%)"' in decide and 'placeholder = "40"' in decide  # review U11
    assert "Enter a share above 0%, for example 50." in decide


def test_outcome_unknown_requests_expire_and_resends_cannot_drop_edits():
    core = _code(_read("hq-core.js"))
    assert "PENDING_MAX_AGE_MS = 86400000" in core and "function readPending(" in core  # review S4
    for name in ["hq-decide.js", "hq-checkins.js"]:
        code = _code(_read(name))
        assert "HQ.readPending(" in code and "HQ.store.getJson(HQ.KEYS.pending" not in code, name
        assert "replayed" in code and "Sending again uses what you sent before." in code, name  # review S5
    checkins = _code(_read("hq-checkins.js"))
    move = checkins.split("const moveForm = ", 1)[1].split("const skipForm = ", 1)[0]
    assert "outcome.sent?.planned_at" in move and "input.disabled = true" in move
    assert "Already done when you first pressed it" in _read("hq-decide.js")


def test_previews_say_whether_hq_recommends_and_quests_do_not_say_needs_you():
    decide = _code(_read("hq-decide.js"))
    says = _function(decide, "hqSays")
    assert '"Recommended: "' in says and '"Not available: "' in says  # review U9
    assert "hqSays(card)" in _function(decide, "preview")
    quest = _function(_code(_read("hq-render.js")), "questRow")
    assert 'chip("needs you"' not in quest and 'chip("stuck", "hq-chip-warn")' in quest  # review U7
    assert "if (used)" in quest


def test_polling_states_are_honest():
    core = _code(_read("hq-core.js"))
    for state in ['"server_error"', '"forbidden"', '"outdated"', "fastPending"]:
        assert state in core, state  # review T6, T9
    assert "HQ had a problem; retrying" in core and "HQ was updated. Reload the page." in core
    assert "inflightDays !== state.calendarDays" in core  # review T7


def test_since_counts_are_honest():
    code = _code(_read("hq-render.js"))
    facts = _function(code, "sinceFacts")
    assert "next_before" in facts and "window_start" in facts  # review C5
    assert "at least" in _function(code, "sinceLastLooked")
    assert "HQ.serverNow()" in _function(code, "attentionCard")  # review U3


def test_notice_link_is_a_full_size_tap_target_and_phones_lead_with_right_now():
    text = _read("hq.css")
    rule = re.search(r"body\.hq-page \.hq-notice \.hq-more-link \{([^}]*)\}", text)
    assert rule and "min-height: 2.75rem" in rule.group(1) and "inline-flex" in rule.group(1)  # review U15
    phone = text.split("@media (max-width: 767px) {", 1)[1].split("\n}\n", 1)[0]
    assert ".hq-title" in phone and "clip: rect(0, 0, 0, 0)" in phone  # review U10: the h1 stays for screen readers
    assert ".site-header .brand-copy small" in phone and ".header-actions .badge" in phone
