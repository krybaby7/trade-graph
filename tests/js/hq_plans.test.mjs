// HQ day plans (W8-C): hq-plans.js on the harness page with a scripted GET /api/v1/hq/plans. The documents below are
// synthetic and follow trade-graph.hq.plans.v1 (api/hq_plans_contract.py); the server writes every sentence, so the
// tests check where each one lands, that model text stays text, and when the page reads.
import test from "node:test";
import assert from "node:assert/strict";
import {SCRIPTS, fixture, loadHQ, response} from "./hq_harness.mjs";

const PLAN_SCRIPTS = [...SCRIPTS, "hq-plans.js"];
const AT = "2026-10-09T13:58:00.000Z";
const words = text => [{t: "text", v: text}];

function entry(id, changes = {}) {
  return {id, status: "partly_filled", tone: "good", text: words(`Buy 5 SOL at 99.5 USD or lower (stop 97, target 104): partly filled: 2 of 5 (${id}).`),
    quantity: "5", filled: "2", unprotected: "0", limit_price: "99.5", stop_price: "97", take_profit_price: "104",
    expires_at: "2026-10-10T00:00:00.000Z",
    fills: [[{t: "text", v: "Bought 2 SOL at 99.5 USD at "}, {t: "time", at: AT, style: "clock"}, {t: "text", v: " (fee 0.796 USD)."}]],
    more_fills: 0,
    exits: [{id: `${id}-x`, status: "waiting", tone: "neutral", fills: [], more_fills: 0,
      text: words("Safety exit for 2 SOL: stop 97, or target 104, whichever comes first: in place, waiting for the price.")}],
    more_exits: 0, ...changes};
}

function plansDocument(changes = {}) {
  return {schema: "trade-graph.hq.plans.v1", as_of: AT, available: true, status_text: null,
    day: {start: "2026-10-09T00:00:00.000Z", end: "2026-10-10T00:00:00.000Z"}, limits_recorded: true,
    headline: words("2 day plans today (SOL/USD, ADA/USD): 1 waiting buy, 1 buy filled or partly filled. 1 plan refused today."),
    plans: [
      {id: "dp-sol", symbol: "SOL/USD", installed_at: AT, day_start: "2026-10-09T00:00:00.000Z",
        day_end: "2026-10-10T00:00:00.000Z", current: true, replaced_today: 1,
        headline: words("1 buy partly filled, 1 with its safety exit."),
        strategy: words("Strategy slow-trend-pullback, version 1, as tested on the paper range."),
        watching: words("Watching RSI (15-minute candles); 1 price alarm."),
        quote: {label: "In their words", text: "Synthetic: <b>rest</b> a bid under the market."},
        invalidation: {label: "In their words", text: "Synthetic: a quote below 95."},
        entries: [entry("e-1"), entry("e-2", {status: "filled", filled: "5", unprotected: "5", tone: "attention",
          text: words("Buy 5 SOL at 98 USD or lower (stop 96): filled. 5 SOL bought here has no safety exit right now."),
          exits: []})],
        events: [[{t: "text", v: "Put in place at "}, {t: "time", at: AT, style: "clock"}, {t: "text", v: "."}]], more_events: 2},
      {id: "dp-ada", symbol: "ADA/USD", installed_at: "2026-10-08T09:00:00.000Z", day_start: "2026-10-08T00:00:00.000Z",
        day_end: "2026-10-09T00:00:00.000Z", current: false, replaced_today: 0,
        headline: words("1 buy filled, 1 with its safety exit (a plan from an earlier day, still protecting coins)."),
        strategy: words("Strategy slow-trend-pullback, version 1."), watching: [], quote: null, invalidation: null,
        entries: [entry("e-3", {status: "filled", filled: "5"})], events: [], more_events: 0},
    ],
    more_plans: 3,
    refusals: [{at: AT, symbol: "SOL/USD", text: words("A plan for SOL/USD was refused: a waiting buy was at or above the current price."),
      technical: "entry 1: a day-plan entry rests below the market"}],
    refusals_total: 4, ...changes};
}

function panel(page) {
  const {doc} = page;
  const article = doc.createElement("article");
  article.setAttribute("class", "hq-panel hq-plans");
  const box = doc.createElement("div");
  box.setAttribute("data-hq", "plans");
  article.append(box);
  doc.querySelector("#money").append(article);
}

async function start({answer = () => response(200, plansDocument()), active = ["money"]} = {}) {
  const page = loadHQ({scripts: PLAN_SCRIPTS, fetch: async call => {
    if (call.url.startsWith("/api/v1/hq/plans")) return answer(call);
    return response(200, fixture("busy"));
  }});
  panel(page);
  for (const id of active) page.doc.querySelector(`#${id}`).classList.add("is-active");
  page.show(fixture("busy"));
  page.use("plans").init();
  await page.flush();
  await page.flush();
  return page;
}
const planCalls = page => page.calls.filter(call => call.url.startsWith("/api/v1/hq/plans"));

test("each plan lands with its buys, fills, safety exits and events, newest first (W8-C)", async () => {
  const page = await start();
  assert.deepEqual(planCalls(page).map(call => [call.url, call.method]), [["/api/v1/hq/plans", "GET"]]);
  const view = page.view("plans");
  assert.match(page.text(view), /^2 day plans today \(SOL\/USD, ADA\/USD\)/);
  const plans = view.querySelectorAll(".hq-plan");
  assert.deepEqual(plans.map(item => item.getAttribute("data-row")), ["plan:dp-sol", "plan:dp-ada"]);
  assert.match(page.text(plans[0].querySelector(".hq-plan-head")), /SOL\/USD Today replaced 1 time/);
  assert.match(page.text(plans[1].querySelector(".hq-plan-head")), /ADA\/USD Earlier day/);
  assert.ok(plans[1].classList.contains("is-earlier"));
  assert.match(page.text(plans[0]), /Watching RSI \(15-minute candles\); 1 price alarm\./);
  const entries = plans[0].querySelectorAll(".hq-plan-entry");
  assert.equal(entries.length, 2);
  assert.match(page.text(entries[0]), /^◐\(partly filled\) Buy 5 SOL at 99\.5 USD/);
  assert.match(page.text(entries[0]), /Bought 2 SOL at 99\.5 USD at .+ \(fee 0\.796 USD\)\./);
  assert.match(page.text(entries[0].querySelector(".hq-plan-exit")), /^○\(waiting\) Safety exit for 2 SOL/);
  assert.match(page.text(plans[0].querySelector("details.hq-plan-history")), /Put in place at .+And 2 more events\./);
});

test("a filled part without a safety exit is marked for attention with a word, not colour alone (W8-C)", async () => {
  const page = await start();
  const unprotected = page.view("plans").querySelectorAll(".hq-plan-entry")[1];
  assert.ok(unprotected.classList.contains("is-attention"));
  assert.equal(unprotected.getAttribute("data-tone"), "attention");
  assert.match(page.text(unprotected), /has no safety exit right now/);
});

test("the Trader's words stay labelled quotes and plain text (W8-C)", async () => {
  const page = await start();
  const plan = page.view("plans").querySelector(".hq-plan");
  const quotes = plan.querySelectorAll("details.hq-quote");
  assert.equal(page.text(quotes[0].querySelector("summary")), "The Trader's reasons, in their words");
  assert.equal(page.text(quotes[0].querySelector("blockquote")), "Synthetic: <b>rest</b> a bid under the market.");
  assert.equal(plan.querySelector("b"), null, "model text is never markup");
  assert.equal(page.text(quotes[1].querySelector("summary")), "What would prove it wrong, in their words");
});

test("today's refusals and the plans not shown are counted (W8-C)", async () => {
  const page = await start();
  const view = page.view("plans");
  assert.match(page.text(view), /3 more plans not shown here\./);
  const refusals = view.querySelector(".hq-plan-refusals");
  assert.match(page.text(refusals), /Refused today.+A plan for SOL\/USD was refused: a waiting buy was at or above the current price\./);
  assert.match(page.text(refusals), /And 3 earlier refusals today\./);
  assert.doesNotMatch(page.text(refusals), /rests below the market/, "the recorded reason shows only with technical names");
  assert.equal(view.querySelector("a.hq-more-link").getAttribute("href"), "/trading");
});

test("it reads only while the Money tab is on screen, and again after the refresh interval (W8-C)", async () => {
  const page = await start({active: ["hq"]});
  assert.equal(planCalls(page).length, 0);
  page.doc.querySelector("#hq").classList.remove("is-active");
  page.doc.querySelector("#money").classList.add("is-active");
  page.use("plans").wake();
  await page.flush();
  assert.equal(planCalls(page).length, 1);
  page.use("plans").wake();
  await page.flush();
  assert.equal(planCalls(page).length, 1, "not again within the refresh interval");
  await page.advance(26000);
  page.use("plans").wake();
  await page.flush();
  assert.equal(planCalls(page).length, 2);
});

test("a failed read keeps what HQ saw last and says so; a database without plans says so (W8-C)", async () => {
  let fail = false;
  const page = await start({answer: () => fail ? response(500, {detail: "x"}) : response(200, plansDocument())});
  fail = true;
  await page.advance(26000);
  page.use("plans").wake();
  await page.flush();
  const view = page.view("plans");
  assert.match(page.text(view), /^The day plans couldn't be refreshed just now; this is what HQ saw last\./);
  assert.equal(view.querySelectorAll(".hq-plan").length, 2);
  const empty = await start({answer: () => response(200, plansDocument({available: false, day: null, plans: [], refusals: [],
    refusals_total: 0, more_plans: 0, limits_recorded: null, status_text: words("This database has no day-plan records yet."),
    headline: words("This database has no day-plan records yet.")}))});
  assert.equal(empty.text(empty.view("plans")), "This database has no day-plan records yet.");
});
