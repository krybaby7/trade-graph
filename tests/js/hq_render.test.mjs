// hq-render.js behaviour: Up next and tiles (U1), the calendar (C4, U14, T2), the reserve rule (U5),
// "Since you last looked" (C5, U3), quest rows (U7) and live-account copy (C1). Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import {loadHQ, fixture, clone, shiftIso} from "./hq_harness.mjs";

const HOUR = 3600000;
const open = (name, options = {}) => {
  const data = fixture(name);
  const page = loadHQ({now: Date.parse(data.as_of), ...options});
  page.show(data);
  return {page, data};
};

test("Up next shows a turn's condition, or at least 'only if running' (U1)", () => {
  const {page, data} = open("safety_stop");
  assert.match(page.text(page.view("upnext")), /Trader's turn.*Only if Resume is possible\./);
  const next = clone(data);
  const item = next.calendar.items.find(entry => entry.id === next.calendar.up_next[0]);
  item.detail = null;
  item.conditional = true;
  page.show(next);
  assert.match(page.text(page.view("upnext")), /only if running/);
});

test("an overdue tile says Waiting, Due now or Overdue as the server says; never 'Due now' by default (U1)", () => {
  const {page, data} = open("allowance_paused");
  const line = () => page.text(page.view("map").querySelector('[data-room="trader"] .hq-room-line'));
  const next = clone(data);
  delete next.departments.find(dept => dept.id === "trader").next.status;   // an older server
  page.show(next);
  assert.equal(line(), "Overdue");
  for (const [status, words] of [["waiting", "Waiting"], ["due_now", "Due now"], ["overdue", "Overdue"],
    ["planned", "Overdue"], ["toString", "Overdue"]]) {
    next.departments.find(dept => dept.id === "trader").next.status = status;
    page.show(clone(next));
    assert.equal(line(), words);
  }
});

test("overdue, due and waiting items are listed under Now, even after their time (C4)", () => {
  const {page, data} = open("allowance_paused");
  const overdue = data.calendar.items.find(item => item.status === "overdue");
  const calendar = page.view("calendar");
  assert.match(page.text(calendar.querySelector("#hq-cal-now")), /Trader's turn · overdue/);
  const next = clone(data);
  const moved = next.calendar.items.find(item => item.id === overdue.id);
  moved.at = shiftIso(next.as_of, -26 * HOUR);   // yesterday, and past
  moved.past = true;
  next.calendar.items.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  page.show(next);
  assert.match(page.text(calendar.querySelector("#hq-cal-now")), /overdue/);
  const earlier = calendar.querySelector(".hq-cal-earlier");
  assert.ok(!earlier || !/overdue/.test(page.text(earlier)), "not hidden in Earlier");
});

test("every week-strip chip has a status glyph and words, and Today's list shows where now is (U14)", () => {
  const {page} = open("quiet");
  const chips = page.view("calendar").querySelectorAll(".hq-week-chip");
  assert.ok(chips.length > 3);
  for (const chip of chips) {
    const glyph = chip.querySelector(".hq-cal-mark");
    assert.ok(glyph && glyph.getAttribute("aria-hidden") === "true" && page.text(glyph), "glyph");
    assert.match(page.text(chip.querySelector(".hq-sr")), /^, (?:planned|estimate|due now|done|had a problem|overdue|waiting|skipped|stuck|missed|cancelled)$/);
  }
  const key = page.HQ.localDayKey(page.HQ.serverNow());
  const today = page.view("calendar").querySelector(`#hq-cal-${key}`);
  const rows = today.querySelector(".hq-cal-list").children;
  const line = rows.findIndex(row => row.classList.contains("hq-cal-now-line"));
  assert.ok(line >= 0, "a Now line");
  const times = rows.filter(row => row.classList.contains("hq-cal-item")).map(row => Date.parse(row.querySelector("time").dateTime));
  const now = page.HQ.serverNow();
  assert.ok(rows.slice(0, line).every(row => Date.parse(row.querySelector("time").dateTime) <= now));
  assert.ok(rows.slice(line + 1).every(row => Date.parse(row.querySelector("time").dateTime) > now));
  assert.ok(times.length >= 2);
});

test("summarised estimates show one box per schedule and fill the week strip (T2)", () => {
  const {page, data} = open("busy");
  const at = Date.parse(data.as_of);
  const next = clone(data);
  next.calendar.items = next.calendar.items.filter(item => !(item.status === "estimated" && Date.parse(item.at) > at + 48 * HOUR));
  next.calendar.estimates = [{schedule_id: "s-trader", dept: "trader", title: [{t: "text", v: "Trader's turn"}],
    first_at: shiftIso(data.as_of, 50 * HOUR), every_seconds: 14400, count: 30, conditional: true, detail: null}];
  page.show(next);
  const later = page.view("calendar").querySelector("#hq-cal-later");
  assert.ok(later, "a Later (estimates) group");
  assert.match(page.text(later.querySelector(".hq-cal-day")), /Later \(estimates\)/);
  const box = later.querySelector('details[data-keep="series:s-trader"]');
  const listed = next.calendar.items.filter(item => item.series?.schedule_id === "s-trader" && item.status === "estimated" &&
    page.HQ.dayDiff(Date.parse(item.at)) > 1).length;
  assert.equal(page.text(box.querySelector("summary")), `Trader: about every 4 hours (${30 + listed} turns)`);
  assert.equal(box.querySelectorAll(".hq-cal-item").length, 30 + listed);
  assert.match(page.text(box), /only if running/);
  const firstRow = box.querySelectorAll(".hq-cal-item")[listed];
  assert.equal(firstRow.querySelector("time").dateTime, shiftIso(data.as_of, 50 * HOUR));
  assert.equal(page.text(firstRow.querySelector(".hq-cal-mark")), "≈");
  const stripEstimates = [...page.view("calendar").querySelectorAll(".hq-week-chip")]
    .filter(chip => chip.dataset.status === "estimated" && page.text(chip).includes("Trader"));
  assert.ok(stripEstimates.length >= 10, `projections within the week appear in the strip (${stripEstimates.length})`);
  assert.match(page.text(page.view("calendar").querySelector(".hq-notes")), /one catch-up turn/);
  // Without the field (an older server) the listed estimates are grouped as before.
  const older = clone(data);
  delete older.calendar.estimates;
  page.show(older);
  assert.equal(page.view("calendar").querySelector('details[data-keep="series:s-trader"]') === null,
    !older.calendar.items.some(item => item.status === "estimated" && item.series?.schedule_id === "s-trader" &&
      page.HQ.dayDiff(Date.parse(item.at)) > 1));
});

test("close deadlines are one counted item with a time range; an older server's single items still render (D111)", () => {
  const {page, data} = open("busy");
  const group = data.calendar.items.find(item => item.count);
  assert.equal(group.kind, "research_expiry");
  const calendar = page.view("calendar");
  const row = calendar.querySelector(`[data-row="cal:${group.id}"]`);
  assert.match(page.text(row.querySelector(".hq-cal-title")), /^2 research notes stop being used · planned/);
  assert.equal(row.querySelector(".hq-cal-until time").dateTime, group.until);
  const chip = () => calendar.querySelectorAll(".hq-week-chip").find(entry => entry.dataset.kind === "research_expiry");
  assert.equal(page.text(chip().querySelector(".hq-week-chip-title")), "2 notes expire");
  assert.deepEqual(chip().querySelectorAll(".hq-week-when time").map(node => node.dateTime), [group.at, group.until]);
  const next = () => page.view("upnext").querySelector(`[data-row="next:${group.id}"]`);
  assert.match(page.text(next()), /2 research notes stop being used.*Between .+ and .+\./);
  const older = clone(data);
  const single = older.calendar.items.find(item => item.id === group.id);
  delete single.count;
  delete single.until;
  single.title = [{t: "text", v: "Research notes stop being used"}];
  page.show(older);
  assert.equal(calendar.querySelector(`[data-row="cal:${group.id}"] .hq-cal-until`), null);
  assert.equal(page.text(chip().querySelector(".hq-week-chip-title")), "Note expires");
  assert.equal(chip().querySelectorAll(".hq-week-when time").length, 1);
  assert.doesNotMatch(page.text(next()), /Between/);
});

test("a condition is said once: one caveat for estimates, and an equal condition is not repeated row after row", () => {
  const {page, data} = open("quiet");
  const caveat = "Only if the team is running and not paused. Missed turns are merged into one.";
  const wait = "Only once AI allowance is available.";
  const next = clone(data);
  for (const item of next.calendar.items.filter(entry => entry.kind === "dept_turn" && !entry.past)) {
    item.conditional = true;
    item.detail = [{t: "text", v: item.status === "estimated" ? caveat : wait}];
  }
  page.show(next);
  const calendar = page.view("calendar");
  const times = (node, words) => page.text(node).split(words).length - 1;
  assert.equal(times(calendar, caveat), 1, "the estimates caveat once, above the agenda");
  assert.match(page.text(calendar.querySelector(".hq-cal-caveat")), /^≈ ?Estimated turns: Only if the team is running/);
  const lists = calendar.querySelectorAll(".hq-cal-group").filter(group => times(group, wait));
  assert.ok(lists.length >= 2, "each day still says it");
  for (const group of lists) assert.equal(times(group, wait), 1, page.text(group.querySelector(".hq-cal-day")));
  assert.doesNotMatch(page.text(calendar), /only if running/);
  // Up next keeps each turn's own condition on its row (U1).
  const turns = page.view("upnext").querySelectorAll(".hq-next-item").filter(row => row.dataset.kind === "dept_turn");
  assert.ok(turns.length >= 3 && turns.every(row => page.text(row).includes(wait)));
});

test("Up next says 'in …' today, then just 'tomorrow' or the date under the clock time (desktop check)", () => {
  const {page} = open("quiet");
  const seen = new Set();
  for (const row of page.view("upnext").querySelectorAll(".hq-next-item")) {
    const at = Date.parse(row.querySelector(".hq-next-when time").dateTime);
    const words = page.text(row.querySelector(".hq-next-rel"));
    const days = page.HQ.dayDiff(at);
    seen.add(Math.min(days, 2));
    if (days === 0) assert.match(words, /^in \d/);
    else if (days === 1) assert.equal(words, "tomorrow");
    else assert.equal(words, page.HQ.fmt.dateWords(at));
  }
  assert.deepEqual([...seen].sort(), [0, 1, 2]);
});

test("the reserve rule is on, off or couldn't be read; unknown is never 'no rule' (U5)", () => {
  const {page, data} = open("quiet");
  const words = () => page.text(page.view("allowance"));
  // The review prompt says the same: the line when on, "really off" when off, "couldn't read it" otherwise.
  const prompt = () => page.use("prompts").reviewText({checkin: {title: "Check", prompt_kind: "interval", planned_at: data.as_of}});
  const cases = [
    [{stop_line_state: "on", stop_line_percent: 40}, /New AI work stops at 40%/, /above-40% admission rule is met/],
    [{stop_line_percent: 40}, /New AI work stops at 40%/, /above-40% admission rule is met/],
    [{stop_line_state: "off", stop_line_percent: null}, /No reserve rule is set\./, /reserve admission rule is really off/],
    [{stop_line_state: "unknown", stop_line_percent: null}, /The reserve rule couldn't be read\./, /HQ couldn't read it/],
    [{stop_line_percent: null}, /The reserve rule couldn't be read\./, /HQ couldn't read it/],
  ];
  for (const [fields, expected, prompted] of cases) {
    const next = clone(data);
    delete next.team.allowance.stop_line_state;
    Object.assign(next.team.allowance, fields);
    page.show(next);
    assert.match(words(), expected, JSON.stringify(fields));
    assert.match(prompt(), prompted, JSON.stringify(fields));
    if (fields.stop_line_state !== "off") assert.doesNotMatch(words(), /No reserve rule is set/);
  }
});

test("'Since you last looked' never counts beyond what the story covers (C5)", () => {
  const data = fixture("quiet");
  const lastSeen = at => ({"tg-hq-last-seen": at});
  const since = page => page.text(page.view("now").querySelector(".hq-now-since"));
  // Older than the story window: no counts at all.
  let page = loadHQ({now: Date.parse(data.as_of), storage: lastSeen(shiftIso(data.story.window_start, -HOUR))});
  page.show(data);
  assert.match(since(page), /more happened than HQ can count here; see the Story tab\./);
  // A capped page (next_before ≠ window_start) and a last look older than its oldest item: a floor.
  const capped = clone(data);
  capped.story.next_before = capped.story.items.at(-1).at;
  page = loadHQ({now: Date.parse(data.as_of), storage: lastSeen(shiftIso(data.story.window_start, HOUR))});
  page.show(capped);
  assert.match(since(page), /: at least \d+ decisions?, \d+ trades?, \d+ problems?/);
  // The same last look with nothing cut: exact counts.
  page = loadHQ({now: Date.parse(data.as_of), storage: lastSeen(shiftIso(data.story.window_start, HOUR))});
  page.show(data);
  assert.doesNotMatch(since(page), /at least/);
  assert.match(since(page), /: \d+ decisions?, \d+ trades?, \d+ problems?\.$/);
});

test("a 'since' time in the future is never printed (U3)", () => {
  const {page, data} = open("quiet");
  const next = clone(data);
  next.attention[0].since = shiftIso(next.as_of, -2 * HOUR);
  next.attention[1].since = shiftIso(next.as_of, 2 * HOUR);
  page.show(next);
  const heads = page.view("attention").querySelectorAll(".hq-attn-head").map(head => page.text(head));
  assert.doesNotMatch(heads[1], /since/);
  assert.match(heads[0], /since 2 h ago/);
});

test("a stuck job says 'stuck', and 'try 0 of 3' is never shown (U7)", () => {
  const {page, data} = open("quiet");
  const next = clone(data);
  const quest = {...next.quests[0], id: "q-stuck", dept: "trader", needs_you: true, terminal: false,
    attempts: {used: 0, max: 3}, state: "BLOCKED_BUDGET"};
  next.quests.unshift(quest);
  page.show(next);
  page.use("render").openRoom("trader", null);
  const row = page.$("[data-hq-sheet] .hq-quest.is-needs-you");
  assert.match(page.text(row), /stuck/);
  assert.doesNotMatch(page.text(row), /needs you|try 0/);
  assert.match(page.text(page.$("[data-hq-sheet]")), /try 1 of 3/, "a real attempt count still shows");
});

test("a live account never reads 'practice' anywhere HQ's scripts write (C1)", () => {
  const {page} = open("live");
  page.HQ.store.remove(page.HQ.KEYS.welcome);
  page.HQ.renderAll(true);
  const all = page.text(page.doc.body);
  assert.doesNotMatch(all, /practice/i);
  assert.match(page.text(page.view("bills")), /^Bills are real costs you paid, such as your AI subscription\.This page doesn't control a live account\. Use Owner controls\.$/);
  page.use("render").openRoom("trader", null);
  assert.match(page.text(page.$("[data-hq-sheet]")), /The only department that can move the account's money\./);
  assert.doesNotMatch(page.text(page.$("[data-hq-sheet]")), /practice/i);
  const glossary = page.use("glossary");
  for (const text of [...Object.values(glossary.ROOM_WHY_LIVE), ...Object.values(glossary.SECTION_HELP_LIVE).map(entry => entry.text)]) {
    assert.doesNotMatch(text, /practice/i);
  }
  const prompts = page.use("prompts");
  for (const kind of ["baseline", "interval", "bedtime", "next_day", "custom"]) {
    assert.doesNotMatch(prompts.reviewText({checkin: {title: "Check", prompt_kind: kind, planned_at: page.HQ.snapshot().as_of}}), /practice/i, kind);
  }
  const blocked = page.use("decide").classify("resume", {status: 409, ok: false,
    body: {detail: {reason: "resume blocked", barriers: ["unresolved orders"]}}});
  assert.match(blocked.message, /An order's status is unclear/);
});

test("the paper run keeps its practice wording (C1 counterpart)", () => {
  const {page} = open("quiet");
  const prompts = page.use("prompts");
  assert.match(prompts.reviewText({checkin: {title: "Bedtime", prompt_kind: "bedtime"}}), /open practice positions/);
  assert.match(prompts.reviewText({checkin: {title: "Next day", prompt_kind: "next_day"}}), /separate practice results/);
  page.use("render").openRoom("trader", null);
  assert.match(page.text(page.$("[data-hq-sheet]")), /move \(practice\) money/);
  assert.match(page.text(page.view("bills")), /never touch the practice account/);
});
