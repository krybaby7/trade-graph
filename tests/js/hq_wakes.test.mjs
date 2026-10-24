// HQ wake-ups (T39): hq-wakes.js on the harness page with a scripted GET /api/v1/hq/wakes. The documents below are
// synthetic and follow trade-graph.hq.wakes.v1 (api/hq_wakes_contract.py); the server writes every sentence, so the
// tests check where each one lands, that model text stays text, and when the page reads.
import test from "node:test";
import assert from "node:assert/strict";
import {SCRIPTS, clone, fixture, loadHQ, response} from "./hq_harness.mjs";

const WAKE_SCRIPTS = [...SCRIPTS, "hq-wakes.js"];
const AT = "2026-10-09T13:58:00.000Z";
const words = text => [{t: "text", v: text}];
const DEPARTMENTS = ["trader", "research", "leader", "learning", "optimisation"];

function wake(cursor, changes = {}) {
  return {
    id: `wake-${cursor}`, cursor, dept: "trader", origin: "engine", purpose: "event", status: "completed",
    tone: "neutral", at: new Date(Date.parse(AT) - (100 - cursor) * 60000).toISOString(),
    closed_at: new Date(Date.parse(AT) - (100 - cursor) * 60000 + 120000).toISOString(),
    headline: words(`A waiting buy filled (${cursor})`), reasons: [{kind: "entry_filled", count: 1}],
    events: [words("Waiting buy filled: 5 SOL at 99.5 USD each (SOL/USD).")], more_events: 0,
    allowance: {status: "available", remaining_percent: "62", observed_at: AT,
      text: [{t: "term", key: "allowance", v: "AI allowance"}, {t: "text", v: " when woken: 62% left."}]},
    result: words("Done after 2 min. 1 day plan put in place."),
    quote: {label: "In their words", text: "Synthetic: rest a bid under the market."}, notes: [],
    link: "/organization", technical: "event wake (completed): entry_filled", ...changes,
  };
}

function day() {
  return {
    start: "2026-10-09T00:00:00.000Z", end: "2026-10-10T00:00:00.000Z",
    steps: ["day_start", "scan", "focus", "day_plan", "day_end_review"].map((step, index) => ({
      step, status: index < 3 ? "done" : "not_yet", at: index < 3 ? AT : null, text: words(`Step text ${step}.`)})),
    check_ins: DEPARTMENTS.map(dept => ({
      dept, at: dept === "research" ? "2026-10-09T16:30:00.000Z" : "2026-10-10T00:00:00.000Z",
      basis: dept === "research" ? "named" : "day_start", status: dept === "trader" ? "awake" : "planned",
      text: words(dept === "trader" ? "Awake now: woken for a price alarm." : `Check-in text ${dept}.`),
      quote: dept === "research" ? {label: "In their words", text: "Synthetic: after the US open."} : null,
      set_at: dept === "research" ? AT : null, waiting: dept === "trader" ? 2 : 0})),
    focus: {status: "named", text: words("Research named 2 focus coins for today."), revision: 1, recorded_at: AT,
      quote: {label: "In their words", text: "Synthetic focus summary."},
      coins: [{symbol: "SOL/USD", quote: {label: "In their words", text: "Liquid and rising."}, evidence: 2},
        {symbol: "ADA/USD", quote: null, evidence: 0}],
      shortlist: ["SOL/USD", "ADA/USD", "LTC/USD"]},
    alarms: {total: 3, text: words("3 price alarms are set. 1 went off and waits for the Trader's next wake-up."),
      waiting: [{dept: "trader", count: 1}],
      items: [{id: "alarm-1", dept: "trader", symbol: "SOL/USD", text: words("mid price crosses below 95"), armed_at: AT,
        quote: {label: "In their words", text: "Synthetic: deeper than planned."}, in_day_plan: true},
      {id: "alarm-2", dept: "leader", symbol: "BTC/USD", text: words("mid price above 70000"), armed_at: AT, quote: null,
        in_day_plan: false}]},
    counts: {today: 3, week: 9, text: words("Today: 3 wake-ups. Last 7 days: 9."), reasons_today: [],
      departments: DEPARTMENTS.map(dept => ({dept, today: dept === "trader" ? 2 : dept === "research" ? 1 : 0,
        week: dept === "trader" ? 6 : dept === "research" ? 3 : 0}))},
  };
}

function wakeLog(changes = {}) {
  return {schema: "trade-graph.hq.wakes.v1", as_of: AT, available: true, status_text: null, day: day(),
    items: [wake(99), wake(98, {dept: "research", headline: words("Its own check-in time")}),
      wake(97, {status: "failed", tone: "attention", closed_at: AT, result: words("Its turn didn't finish.")})],
    total: 5, next_before: 97, ...changes};
}

function panels(page) {
  const {doc} = page;
  const make = (section, name) => {
    const panel = doc.createElement("article");
    panel.setAttribute("class", "hq-panel");
    const box = doc.createElement("div");
    box.setAttribute("data-hq", name);
    panel.append(box);
    doc.querySelector(`#${section}`).append(panel);
  };
  make("calendar", "wakeplan");
  make("calendar", "watchlist");
  make("story", "wakes");
}

async function start({answer = () => response(200, wakeLog()), active = ["calendar", "story"]} = {}) {
  const page = loadHQ({scripts: WAKE_SCRIPTS, fetch: async call => {
    if (call.url.startsWith("/api/v1/hq/wakes")) return answer(call);
    return response(200, fixture("busy"));
  }});
  panels(page);
  for (const id of active) page.doc.querySelector(`#${id}`).classList.add("is-active");
  page.show(fixture("busy"));
  page.use("wakes").init();
  await page.flush();
  await page.flush();
  return page;
}

const wakeCalls = page => page.calls.filter(call => call.url.startsWith("/api/v1/hq/wakes"));

test("each department's next check-in and today's routine land in the Calendar tab", async () => {
  const page = await start();
  assert.equal(wakeCalls(page).length, 1);
  assert.equal(wakeCalls(page)[0].url, "/api/v1/hq/wakes");
  const plan = page.view("wakeplan");
  const rows = plan.querySelectorAll(".hq-wake-checkin");
  assert.equal(rows.length, 5);
  assert.deepEqual(rows.map(row => row.getAttribute("data-row")), DEPARTMENTS.map(dept => `checkin:${dept}`));
  assert.match(page.text(rows[0]), /Trader · next day start Awake 2 waiting/);
  assert.match(page.text(rows[1]), /Research · its own time/);
  assert.match(page.text(rows[1]), /Synthetic: after the US open\./, "its own note, quoted");
  assert.ok(rows[1].querySelector("time"), "the time is a <time> the viewer can tap");
  const steps = plan.querySelectorAll(".hq-wake-step");
  assert.equal(steps.length, 5);
  assert.match(page.text(steps[0]), /✓Day start \(done\): Step text day_start\./);
  assert.match(page.text(steps[3]), /○Day plans \(not yet\)/);
  assert.match(page.text(plan), /Wake-ups happen only while the team is running/);
});

test("the day's focus coins and the alarms that are set land beside them", async () => {
  const page = await start();
  const watch = page.view("watchlist");
  assert.match(page.text(watch), /Research named 2 focus coins for today\./);
  const coins = watch.querySelectorAll(".hq-wake-coin");
  assert.equal(coins.length, 2);
  assert.match(page.text(coins[0]), /SOL\/USD 2 sources\s*In their words\s*Liquid and rising\./);
  assert.match(page.text(watch), /The scanner's shortlist today: SOL\/USD, ADA\/USD, LTC\/USD\./);
  assert.match(page.text(watch), /Research's summary, in their words\s*Synthetic focus summary\./);
  const alarms = watch.querySelectorAll(".hq-wake-alarm");
  assert.equal(alarms.length, 2);
  assert.match(page.text(alarms[0]), /SOL\/USD mid price crosses below 95/);
  assert.match(page.text(alarms[0]), /Trader's alarm, set .* day plan/);
  assert.match(page.text(watch), /1 more alarm set, not listed here\./);
  assert.match(page.text(watch), /1 went off and waits for the Trader's next wake-up/);
});

test("the wake log shows every wake with its department, reasons, allowance and result, newest first", async () => {
  const page = await start();
  const log = page.view("wakes");
  assert.match(page.text(log), /Today: 3 wake-ups\. Last 7 days: 9\./);
  assert.match(page.text(log), /Today by department: Trader 2 · Research 1\./);
  const rows = log.querySelectorAll(".hq-wake-item");
  assert.deepEqual(rows.map(row => row.getAttribute("data-row")), ["wake:wake-99", "wake:wake-98", "wake:wake-97"]);
  assert.match(page.text(rows[0]), /Trader Done\s*A waiting buy filled \(99\)/);
  assert.match(page.text(rows[0]), /AI allowance when woken: 62% left\./);
  assert.match(page.text(rows[0]), /Done after 2 min\. 1 day plan put in place\./);
  assert.ok(rows[0].querySelector("button.hq-term[data-term='allowance']"), "the allowance word explains itself");
  assert.equal(rows[2].getAttribute("data-tone"), "attention");
  assert.match(page.text(rows[2]), /Didn't finish/);
  const more = rows[0].querySelector("details.hq-story-more");
  assert.match(page.text(more), /Waiting buy filled: 5 SOL at 99\.5 USD each/);
  assert.match(page.text(more), /Synthetic: rest a bid under the market\./);
  assert.equal(more.querySelector("a").getAttribute("href"), "/organization");
});

test("load earlier continues below the cursor, and the end of the saved wake-ups says so", async () => {
  const older = wakeLog({day: null, items: [wake(96), wake(95)], next_before: null});
  const page = await start({answer: call => response(200, call.url.includes("before=") ? older : wakeLog())});
  const log = page.view("wakes");
  assert.match(page.text(page.view("wakes")), /3 wake-ups shown/, "a day may go on in a page not loaded yet");
  const earlier = page.byText('[data-hq="wakes"] button', "Load earlier");
  assert.ok(earlier);
  earlier.dispatchEvent(new page.dom.Event("click"));
  await page.flush();
  assert.equal(wakeCalls(page).at(-1).url, "/api/v1/hq/wakes?before=97");
  assert.equal(page.view("wakes").querySelectorAll(".hq-wake-item").length, 5);
  assert.match(page.text(page.view("wakes")), /That's every saved wake-up\./);
  assert.doesNotMatch(page.text(page.view("wakes")), /wake-ups shown/);
  assert.equal(page.byText('[data-hq="wakes"] button', "Load earlier"), null);
  assert.ok(log);
});

test("the department filter narrows the log to one department", async () => {
  const page = await start();
  page.byText("button.hq-filter", "Research").dispatchEvent(new page.dom.Event("click"));
  const rows = page.view("wakes").querySelectorAll(".hq-wake-item");
  assert.deepEqual(rows.map(row => row.getAttribute("data-row")), ["wake:wake-98"]);
});

test("the page reads only while a wake panel is shown and refreshes with HQ's poll", async () => {
  const page = await start({active: ["money"]});
  assert.equal(wakeCalls(page).length, 0, "on a phone with the Money tab open nothing is read");
  assert.match(page.text(page.view("wakeplan")), /Loading the wake-ups/);
  page.doc.querySelector("#money").classList.remove("is-active");
  page.doc.querySelector("#story").classList.add("is-active");
  page.use("wakes").wake();
  await page.flush();
  assert.equal(wakeCalls(page).length, 1);
  page.show(fixture("busy"));
  await page.flush();
  assert.equal(wakeCalls(page).length, 1, "not again within the refresh interval");
  page.clock.now += 26000;
  page.show(fixture("busy"));
  await page.flush();
  assert.equal(wakeCalls(page).length, 2, "the next poll refreshes it");
});

test("a failed refresh keeps what HQ saw last and says so; another schema is never shown", async () => {
  let fail = false;
  const page = await start({answer: () => fail ? response(500, {detail: "synthetic failure"}) : response(200, wakeLog())});
  fail = true;
  page.clock.now += 26000;
  page.show(fixture("busy"));
  await page.flush();
  assert.match(page.text(page.view("wakes")), /couldn't be refreshed just now; this is what HQ saw last/);
  assert.equal(page.view("wakes").querySelectorAll(".hq-wake-item").length, 3);
  const other = await start({answer: () => response(200, {...wakeLog(), schema: "trade-graph.hq.v1"})});
  assert.match(other.text(other.view("wakes")), /couldn't be loaded just now/);
  assert.equal(other.view("wakes").querySelectorAll(".hq-wake-item").length, 0);
});

test("model text stays text: a quoted note can't add markup", async () => {
  const sneaky = wake(99, {notes: [{label: "In their words", text: "<img src=x onerror=alert(1)> buy everything now"}],
    quote: {label: "In their words", text: "<script>alert(1)</script>"}});
  const page = await start({answer: () => response(200, wakeLog({items: [sneaky], next_before: null}))});
  const row = page.view("wakes").querySelector(".hq-wake-item");
  assert.equal(row.querySelectorAll("img").length, 0);
  assert.equal(row.querySelectorAll("script").length, 0);
  assert.match(page.text(row), /<img src=x onerror=alert\(1\)> buy everything now/);
});

test("a database without wake records says so in every panel and lists nothing", async () => {
  const unavailable = {schema: "trade-graph.hq.wakes.v1", as_of: AT, available: false, day: null, items: [], total: 0,
    next_before: null, status_text: words("This database has no wake-up records: it is older than the event engine.")};
  const page = await start({answer: () => response(200, clone(unavailable))});
  for (const name of ["wakeplan", "watchlist", "wakes"]) {
    assert.match(page.text(page.view(name)), /older than the event engine/);
  }
  assert.equal(page.view("wakes").querySelectorAll(".hq-wake-item").length, 0);
});

test("older pages are dropped when the newest page no longer meets them, so no wake-up is skipped", async () => {
  let first = wakeLog();
  const older = wakeLog({day: null, items: [wake(96), wake(95)], next_before: 95});
  const page = await start({answer: call => response(200, call.url.includes("before=") ? older : first)});
  page.byText('[data-hq="wakes"] button', "Load earlier").dispatchEvent(new page.dom.Event("click"));
  await page.flush();
  assert.equal(page.view("wakes").querySelectorAll(".hq-wake-item").length, 5);
  first = wakeLog({items: [wake(140), wake(139)], next_before: 139, total: 45});
  page.clock.now += 26000;
  page.show(fixture("busy"));
  await page.flush();
  const rows = page.view("wakes").querySelectorAll(".hq-wake-item");
  assert.deepEqual(rows.map(row => row.getAttribute("data-row")), ["wake:wake-140", "wake:wake-139"]);
  page.byText('[data-hq="wakes"] button', "Load earlier").dispatchEvent(new page.dom.Event("click"));
  await page.flush();
  assert.equal(wakeCalls(page).at(-1).url, "/api/v1/hq/wakes?before=139");
});
