// hq-core.js behaviour: polling (T6, T7, T9), saved-request expiry (S4), in-sheet messages (U4), panels and
// time details that survive a refresh (T3) and sheet focus. Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import {loadHQ, fixture, response, clone, shiftIso} from "./hq_harness.mjs";

const SNAP = fixture("quiet");
const AT = Date.parse(SNAP.as_of);
const fresh = clock => response(200, {...clone(SNAP), as_of: new Date(clock.now).toISOString()});

test("a decision's 3-second follow-up polling starts when its result sheet closes (T6)", async () => {
  const page = loadHQ({now: AT, bind: true, fetch: () => fresh(page.clock)});
  page.show(SNAP);
  page.HQ.poller.start();
  page.HQ.sheet.open({title: "Result", build: body => body.append("Paused. Protection continues.")});
  page.HQ.poller.fast();                       // what the confirm handler does while its sheet is open
  await page.advance(40000);
  assert.equal(page.calls.length, 0, "nothing polls while a sheet is open");
  page.HQ.sheet.close();
  await page.advance(10000);
  const offsets = page.calls.map(call => (call.at - page.calls[0].at) / 1000);
  assert.deepEqual(offsets.slice(0, 4), [0, 3, 6, 9]);
});

test("'Show 2 weeks' during a 7-day poll still asks for 14 days (T7)", async () => {
  const answers = [];
  const page = loadHQ({now: AT, fetch: () => new Promise(resolve => answers.push(() => resolve(fresh(page.clock))))});
  page.show(SNAP);
  const poll = page.HQ.refresh();
  const toggle = page.HQ.setCalendarDays(14);
  await page.flush();
  assert.equal(page.calls.length, 1);
  assert.match(page.calls[0].url, /calendar_days=7$/);
  answers.shift()();
  await page.flush();
  assert.equal(page.calls.length, 2, "a second request for the new range");
  assert.match(page.calls[1].url, /calendar_days=14$/);
  answers.shift()();
  assert.equal(await toggle, true);
  assert.equal(await poll, true);
  assert.equal(page.HQ.calendarDays(), 14);
});

test("a newer server stops polling and asks for a reload (T9)", async () => {
  const page = loadHQ({now: AT, fetch: () => response(200, {...clone(SNAP), schema: "trade-graph.hq.v2"})});
  page.show(SNAP);
  page.HQ.poller.start();
  await page.advance(30000);
  assert.equal(page.HQ.connection(), "outdated");
  await page.advance(600000);
  assert.equal(page.calls.length, 1, "polling stopped");
  const banner = page.$("[data-hq-banner]");
  assert.equal(banner.hidden, false);
  assert.equal(page.text(banner.querySelector(".hq-banner-text")), "HQ was updated. Reload the page.");
  page.byText("[data-hq-banner] button", "Reload").click();
  assert.equal(page.reloads.length, 1);
  assert.match(page.text(page.$("[data-hq-fresh]")), /HQ was updated/);
});

test("a server error says so and backs off; no answer at all says HQ can't be reached (T9)", async () => {
  let network = false;
  const page = loadHQ({now: AT, fetch: () => { if (network) throw new TypeError("offline"); return response(500, {detail: "boom"}); }});
  page.show(SNAP);
  page.HQ.poller.start();
  await page.advance(30000);
  assert.equal(page.HQ.connection(), "server_error");
  assert.match(page.text(page.$("[data-hq-fresh]")), /HQ had a problem; retrying/);
  assert.equal(page.HQ.online(), false, "decisions can't confirm while HQ has a problem");
  await page.advance(30000 + 60000 + 120000);
  const gaps = page.calls.slice(1).map((call, index) => (call.at - page.calls[index].at) / 1000);
  assert.deepEqual(gaps, [30, 60, 120]);
  network = true;
  await page.advance(120000);
  assert.equal(page.HQ.connection(), "failed");
  assert.match(page.text(page.$("[data-hq-fresh]")), /Can't reach HQ/);
});

test("a 403 stops polling with a sign-in banner (T9)", async () => {
  const page = loadHQ({now: AT, fetch: () => response(403, {detail: "forbidden"})});
  page.show(SNAP);
  page.HQ.poller.start();
  await page.advance(30000);
  assert.equal(page.HQ.connection(), "forbidden");
  await page.advance(600000);
  assert.equal(page.calls.length, 1);
  assert.match(page.text(page.$("[data-hq-banner]")), /This sign-in can't view HQ any more\. Sign in again ›/);
  assert.equal(page.$("[data-hq-banner] a").getAttribute("href"), "/login");
});

test("a saved request older than a day, or without a time, is dropped and never resent (S4)", () => {
  const key = "tg-hq-pending:start_trading";
  const entry = saved => ({request_id: "r-1", body: {request_id: "r-1", mode: "paper"}, ...(saved === undefined ? {} : {saved_at: saved})});
  for (const [saved, kept] of [[new Date(AT - 25 * 3600000).toISOString(), false], [undefined, false], ["yesterday", false],
    [new Date(AT - 3600000).toISOString(), true]]) {
    const page = loadHQ({now: AT, storage: {[key]: entry(saved)}});
    page.show(SNAP);
    const stored = page.HQ.readPending("start_trading");
    assert.equal(Boolean(stored), kept, String(saved));
    assert.equal(page.store.has(key), kept, `${saved} is removed from storage`);
  }
});

test("a message raised while a sheet is open shows inside the sheet, with its button (U4)", () => {
  const page = loadHQ({now: AT, bind: true});
  page.show(SNAP);
  let ran = 0;
  page.HQ.sheet.open({title: "Review prompt preview", build: body => body.append("text")});
  page.HQ.toast("Copied. Paste it into your review chat.", {label: "Mark done", run: () => { ran += 1; }});
  const inside = page.$("[data-hq-sheet] [data-hq-sheet-toast]");
  assert.match(page.text(inside), /Copied\. Paste it into your review chat\./);
  assert.equal(inside.getAttribute("role"), "status");
  assert.equal(page.text(page.$("[data-hq-toast]")), "", "nothing hides under the sheet");
  page.byText("[data-hq-sheet-toast] button", "Mark done").click();
  assert.equal(ran, 1);
  assert.equal(page.text(inside), "");
  page.HQ.sheet.close();
  page.HQ.toast("Calendar file ready.");
  assert.match(page.text(page.$("[data-hq-toast]")), /Calendar file ready\./);
});

test("a poll that only moves the clock window leaves the story and calendar untouched (T3)", () => {
  const page = loadHQ({now: AT});
  page.show(SNAP);
  const story = page.view("story");
  const calendar = page.view("calendar");
  const [storyFirst, calendarFirst] = [story.firstChild, calendar.firstChild];
  const more = story.querySelector("details.hq-story-more");
  page.toggle(more, true);
  const step = 30000;
  const next = clone(SNAP);
  next.as_of = shiftIso(SNAP.as_of, step);
  next.story.window_start = shiftIso(SNAP.story.window_start, step);
  next.story.next_before = next.story.window_start;
  next.calendar.from = shiftIso(SNAP.calendar.from, step);
  next.calendar.to = shiftIso(SNAP.calendar.to, step);
  page.clock.now += step;
  page.show(next);
  assert.ok(story.firstChild === storyFirst, "the story was not rebuilt");
  assert.ok(calendar.firstChild === calendarFirst, "the calendar was not rebuilt");
  assert.equal(more.open, true);
});

test("open panels and tapped times stay open when a view is rebuilt (T3)", () => {
  const page = loadHQ({now: AT, bind: true});
  page.show(SNAP);
  const more = page.view("story").querySelector("details.hq-story-more");
  const row = more.closest("[data-row]").dataset.row;
  page.toggle(more, true);
  const control = page.view("now").querySelector(".hq-time");
  control.click();
  assert.equal(control.getAttribute("aria-expanded"), "true");
  page.HQ.rerender("story");
  page.HQ.rerender("now");
  const again = page.view("now").querySelector(".hq-time");
  assert.ok(again !== control, "the view was rebuilt");
  assert.equal(again.getAttribute("aria-expanded"), "true");
  assert.equal(again.nextElementSibling.hidden, false);
  const reopened = [...page.view("story").querySelectorAll("details.hq-story-more")]
    .find(box => box.closest("[data-row]").dataset.row === row);
  assert.ok(reopened !== more, "the story was rebuilt");
  assert.equal(reopened.open, true);
  page.toggle(reopened, false);
  page.HQ.rerender("story");
  const closed = [...page.view("story").querySelectorAll("details.hq-story-more")]
    .find(box => box.closest("[data-row]").dataset.row === row);
  assert.equal(closed.open, false, "closing is remembered too");
});

test("a sheet whose first control is disabled puts focus on its title", () => {
  const page = loadHQ({now: AT, bind: true});
  page.show(SNAP);
  page.HQ.sheet.open({title: "Stop the team safely", build: body => {
    const confirm = page.HQ.button("button", "Stop");
    confirm.dataset.autofocus = "";
    confirm.disabled = true;
    body.append(confirm);
  }});
  assert.equal(page.doc.activeElement.id, "hq-sheet-title");
});
