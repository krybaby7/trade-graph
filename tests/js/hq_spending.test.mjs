// hq-render.js AI spending (T40): spending per department from receipts, the testing alert, the prepaid credit and
// Codex use per department, all from the snapshot's ai_spending block. Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import {loadHQ, fixture, clone} from "./hq_harness.mjs";

const open = (name, change) => {
  const data = fixture(name);
  if (change) change(data);
  const page = loadHQ({now: Date.parse(data.as_of)});
  page.show(data);
  return {page, data, view: page.view("spending")};
};
const service = (data, name) => data.ai_spending.services.find(item => item.service === name);
const line = (view, serviceName, id) => view.querySelector(`[data-service="${serviceName}"] [data-line="${id}"]`);
// "Label: value" for each kind of cost a line shows (the small test DOM joins text without spaces).
const parts = (page, row) => [...row.querySelectorAll(".hq-spend-amount")]
  .map(item => `${page.text(item.querySelector("dt"))}: ${page.text(item.querySelector("dd"))}`);

test("each department shows only the kinds of cost it has, with 'not known yet' as 'up to' (T40)", () => {
  const {page, view} = open("busy");
  const research = line(view, "perplexity", "research");
  assert.equal(page.text(research.querySelector(".hq-spend-name")), "Research");
  // No conservative charge in this line, so no "Estimated" part: left out, never "$0.00".
  assert.deepEqual(parts(page, research), ["From receipts: $0.15", "Not known yet: up to $0.02 (1 call)", "Calls: 12"]);
  assert.deepEqual(parts(page, line(view, "perplexity", "leader")), ["From receipts: $0.03", "Estimated: $0.02", "Calls: 2"]);
  assert.match(page.text(view), /Testing total so far: \$0\.21 from receipts, plus up to \$0\.02 not known yet\. No spending limit while testing\./);
  assert.match(page.text(view), /Prepaid credit: \$25\.00 when you checked it on .+ so about \$24\.93 is left\. Information only/);
  assert.ok(view.querySelector('[data-term="unresolved_cost"]') && view.querySelector('[data-term="estimated_cost"]'));
});

test("the notes follow from the figures: open calls, estimates and test calls (T40)", () => {
  const {page, view} = open("busy", data => { service(data, "perplexity").synthetic_calls = 2; });
  const perplexity = page.text(view.querySelector('[data-service="perplexity"]'));
  assert.match(perplexity, /Calls whose use isn't known yet count at the most they could cost, never as free\./);
  assert.match(perplexity, /Estimated costs are calls charged at the most they could cost/);
  assert.match(perplexity, /2 test calls with made-up answers aren't counted\./);
  const jev = page.text(view.querySelector('[data-service="jev"]'));
  assert.doesNotMatch(jev, /Estimated costs|test call/);
});

test("a cost under half a cent reads 'under $0.01', never $0.00 (T40)", () => {
  const {page, view} = open("busy");
  assert.deepEqual(parts(page, line(view, "jev", "news_alarm")),
    ["From receipts: $0.01", "Not known yet: under $0.01 (1 call)", "Calls: 41"]);
  assert.doesNotMatch(page.text(view), /\$0\.00/);
});

test("a hold that can't be valued says so instead of showing an amount (T40)", () => {
  const {page, view} = open("busy", data => {
    const research = service(data, "perplexity").lines.find(item => item.id === "research");
    research.amounts.unresolved = null;
    research.amounts.total = null;
  });
  assert.ok(parts(page, line(view, "perplexity", "research")).includes("Not known yet: 1 call, cost can't be worked out yet"));
});

test("the testing alert shows its state; passed is highlighted and still pauses nothing (T40)", () => {
  const {page, view} = open("busy");
  const below = view.querySelector('[data-service="perplexity"] .hq-spend-alert');
  assert.equal(below.dataset.state, "below");
  assert.match(page.text(below), /HQ alerts you here at \$20\.00\. It's an alert only: nothing pauses\./);
  const next = clone(fixture("busy"));
  const alert = service(next, "perplexity").alert;
  alert.state = "passed";
  alert.text = [{t: "text", v: "Testing spending passed your "}, {t: "money", amount: "20", currency: "USD", display: "$20.00"},
    {t: "text", v: " alert: "}, {t: "money", amount: "20.5", currency: "USD", display: "$20.50"}, {t: "text", v: " so far. Nothing was paused."}];
  page.show(next);
  const passed = page.view("spending").querySelector('[data-service="perplexity"] .hq-spend-alert');
  assert.equal(passed.dataset.state, "passed");
  assert.match(page.text(passed), /passed your \$20\.00 alert: \$20\.50 so far\. Nothing was paused\./);
});

test("Codex use per department is a share of the team's reported use (T40)", () => {
  const {page, view} = open("busy");
  const codex = view.querySelector(".hq-codex");
  assert.equal(page.text(codex.querySelector(".hq-subhead")), "AI allowance by department");
  assert.equal(page.text(codex.querySelector(".hq-spend-headline")), "Last 7 days: 9 turns on your ChatGPT subscription.");
  const trader = codex.querySelector('[data-line="trader"]');
  assert.equal(page.text(trader.querySelector(".hq-muted")), "7 turns · 65.6% of the team's reported use · 1 not reported");
  assert.match(page.text(codex), /shared with your own ChatGPT use/);
  assert.match(page.text(codex), /1 turn didn't report its use; shares count only what was reported\./);
});

test("practice money is never offered as paying for AI, and a live account never hears 'practice' (T40)", () => {
  const {page, view} = open("busy");
  assert.match(page.text(view), /Practice money never pays for AI\./);
  assert.doesNotMatch(page.text(view), /pays for itself|left after costs/i);
  const live = open("live");
  assert.doesNotMatch(live.page.text(live.view), /practice/i);
  assert.match(live.page.text(live.view), /Account gains are never counted against them here\./);
  assert.equal(live.view.querySelector("a.hq-more-link").getAttribute("href"), "/costs");
});

test("nothing recorded yet shows plain sentences and no amounts (T40)", () => {
  const {page, view} = open("empty");
  assert.match(page.text(view), /No Perplexity research has run yet\./);
  assert.match(page.text(view), /No Jev checks yet\. They start once you install a Jev key\./);
  assert.match(page.text(view), /No turns on your ChatGPT subscription in the last 7 days\./);
  assert.equal(view.querySelectorAll(".hq-spend-line").length, 0);
  assert.doesNotMatch(page.text(view), /\$0\.00/);
});

test("an older server without the block gets a sentence, not invented figures (T40)", () => {
  const {page, view} = open("quiet", data => { delete data.ai_spending; });
  assert.equal(page.text(view), "AI spending isn't available from the server right now. The Costs page has every recorded cost.");
});

test("the spending alert card shows in Needs you as 'keep an eye on' (T40)", () => {
  const {page} = open("busy", data => {
    data.attention.unshift({id: "spending_alert:perplexity", sev: "watch",
      title: [{t: "text", v: "Perplexity testing spending passed your "}, {t: "money", amount: "20", currency: "USD", display: "$20.00"},
        {t: "text", v: " alert"}],
      why: [{t: "text", v: "$20.50 so far during testing, from receipts."}],
      consequence: [{t: "text", v: "Nothing is paused: it's an alert only."}], can_fix_here: false, how: null, decision: null,
      checkin_id: null, link: {href: "/costs", label: "Costs"}, in_next_prompt: true, since: null});
  });
  const card = page.byText(".hq-attn", "Perplexity testing spending passed your $20.00 alert");
  assert.ok(card, "the card is listed");
  assert.match(page.text(card), /Keep an eye on/);
  assert.match(page.text(card), /Nothing is paused: it's an alert only\./);
});
