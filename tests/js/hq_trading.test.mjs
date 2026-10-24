// hq-render.js today's trading (W8-C): today's loss against the daily limit, the scorecard and the price collector,
// all from the snapshot's trading block, and the paper result's comparison with real costs. The fixtures' blocks
// were made by the real builder from synthetic facts. Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import {loadHQ, fixture} from "./hq_harness.mjs";

const open = (name, change) => {
  const data = fixture(name);
  if (change) change(data);
  const page = loadHQ({now: Date.parse(data.as_of)});
  page.show(data);
  return {page, data};
};

test("today's loss shows its state in words, a meter with a label and the day's figures (W8-C)", () => {
  const {page} = open("quiet");
  const view = page.view("loss");
  assert.match(page.text(view), /^Within the limit Today's loss so far: \$18\.40 of the \$300\.38 limit \(3% of \$10,012\.50 at the day start\)\./);
  assert.ok(view.querySelector("button.hq-term[data-term='loss_limit']"), "the limit explains itself");
  const meter = view.querySelector("meter");
  assert.equal(meter.getAttribute("aria-label"), "6.1% of today's loss limit used");
  assert.match(page.text(view), /6\.1% of the limit used/);
  assert.match(page.text(view), /At the day start\$10,012\.50/);
  assert.match(page.text(view), /Now\$9,994\.10/);
  assert.match(page.text(view), /Safety exits and sales keep working/);
});

test("a loss that can't be checked has no meter and says why; a stop says when buying resumes (W8-C)", () => {
  const silent = open("silent").page;
  const view = silent.view("loss");
  assert.match(silent.text(view), /^Can't be checked now Today's loss can't be checked right now/);
  assert.match(silent.text(view), /too old/);
  assert.equal(view.querySelector("meter"), null);
  assert.match(silent.text(view), /Now\s*Not known yet/);
  const stopped = open("quiet", data => {
    Object.assign(data.trading.loss, {state: "stopped", headline: [{t: "text", v: "The daily loss limit was reached: no new buying until "},
      {t: "time", at: "2026-10-10T00:00:00.000Z", style: "day_clock"}, {t: "text", v: "."}]});
  }).page;
  const head = stopped.view("loss").querySelector(".hq-loss-head");
  assert.equal(head.getAttribute("data-state"), "stopped");
  assert.match(stopped.text(head), /^No new buying today The daily loss limit was reached: no new buying until /);
});

test("without recorded limits, or on a live account, the panel says so and shows no figures (W8-C)", () => {
  const empty = open("empty").page;
  assert.match(empty.text(empty.view("loss")), /^Not recorded yet Your money limits aren't recorded yet/);
  assert.match(empty.text(empty.view("loss")), /trade-graph money-limits --confirm/);
  assert.equal(empty.view("loss").querySelector(".hq-stat"), null);
  const live = open("live").page;
  assert.match(live.text(live.view("loss")), /^Not measured here/);
  assert.match(live.text(live.view("scorecard")), /The scorecard covers the paper account only\./);
  for (const name of ["loss", "scorecard", "collector"]) assert.doesNotMatch(live.text(live.view(name)), /practice/i);
});

test("the scorecard shows its result, the comparisons with words and shapes, the figures and the gates (W8-C)", () => {
  const {page} = open("busy");
  const view = page.view("scorecard");
  assert.match(page.text(view), /^Provisional Since .+: up \$210\.14 \(2\.1%\) after every fee\./);
  assert.match(page.text(view), /✓ Beats cash/);
  assert.match(page.text(view), /✓ Beats holding Bitcoin/);
  assert.match(page.text(view), /Trades2 \(1 won, 1 lost\)/);
  assert.match(page.text(view), /Worst fall1\.6% \(\$165\.26\)/);
  assert.match(page.text(view), /Against buying Bitcoin at the start and selling it at the end with the same fees: ahead by \$40\.83/);
  assert.match(page.text(view), /Real AI spending in these days: .+ It is real money; the paper result never pays for it\./);
  assert.match(page.text(view), /Information only, not a pass mark\./);
  const why = view.querySelector("details.hq-scorecard-why");
  assert.ok(why && /17 minutes old/.test(page.text(why)));
  assert.match(page.text(view.querySelector(".hq-scorecard-note")), /three separate gates/);
});

test("an unavailable scorecard says why and shows no figures (W8-C)", () => {
  const {page} = open("quiet", data => {
    Object.assign(data.trading.scorecard, {available: false, status: null, period_start: null, period_end: null,
      currency: null, result: null, versus_btc: null, beats_cash: null, beats_btc: null, facts: [], lines: [],
      provisional: [], more_provisional: 0, headline: [{t: "text", v: "The scorecard can't be worked out yet."}],
      unavailable_reason: [{t: "text", v: "The account has no opening deposit yet, so there is nothing to score."}]});
  });
  const view = page.view("scorecard");
  assert.match(page.text(view), /The scorecard can't be worked out yet\.The account has no opening deposit yet/);
  assert.equal(view.querySelector(".hq-stat"), null);
  assert.equal(view.querySelector(".hq-scorecard-beats"), null);
});

test("the price collector panel shows its state, newest scan and problems (W8-C)", () => {
  const busy = open("busy").page;
  const view = busy.view("collector");
  assert.match(busy.text(view), /^Working The price collector is working: the newest hourly coin scan was /);
  assert.match(busy.text(view), /Kraken asked it to slow down once in the last 24 hours/);
  assert.match(busy.text(view), /1 gap in saved candles was found/);
  const silent = open("silent").page;
  const attention = silent.view("collector").querySelector(".hq-collector-head");
  assert.equal(attention.getAttribute("data-state"), "attention");
  assert.match(silent.text(attention), /^Needs a look The price collector has a problem: there was no coin scan in the last 2 hours\./);
});

test("an older server without the block leaves the panels saying so, never zero (W8-C)", () => {
  const {page} = open("quiet", data => { delete data.trading; });
  assert.match(page.text(page.view("loss")), /Today's loss limit isn't available from the server right now\./);
  assert.match(page.text(page.view("scorecard")), /The scorecard isn't available from the server right now\./);
  assert.doesNotMatch(page.text(page.view("loss")), /\$0\.00/);
});

test("the paper result is compared with real costs and never said to pay for anything (W8-C, D254)", () => {
  const covered = open("quiet", data => {
    Object.assign(data.money.pays_for_itself, {status: "covered", net: {amount: "38.82", currency: "EUR", display: "€38.82"}});
  }).page;
  const box = covered.view("money").querySelector(".hq-pays");
  assert.match(covered.text(box), /Paper result against real costs/);
  assert.match(covered.text(box), /On paper, the result is €38\.82 above the real costs you entered\./);
  assert.match(covered.text(box), /A comparison only: paper money is simulated, never pays a real bill and never refills your budget\./);
  assert.doesNotMatch(covered.text(box), /pays for itself|left after costs/i);
  const short = open("quiet", data => {
    Object.assign(data.money.pays_for_itself, {status: "not_covered", net: {amount: "-12.3", currency: "EUR", display: "−€12.30"}});
  }).page;
  assert.match(short.text(short.view("money")), /On paper, the result is €12\.30 short of the real costs you entered\./);
  const live = open("live", data => {
    Object.assign(data.money.pays_for_itself, {status: "covered", net: {amount: "38.82", currency: "EUR", display: "€38.82"}});
  }).page;
  assert.match(live.text(live.view("money")), /Pays for itself\?Yes, for this period: €38\.82 left after costs\./);
  assert.doesNotMatch(live.text(live.view("money")), /paper money/i);
});

test("the glossary explains the new words, and the old comparison word without promising bills are paid (W8-C)", () => {
  const {page} = open("quiet");
  const glossary = page.use("glossary").GLOSSARY;
  for (const key of ["loss_limit", "day_plan", "safety_exit", "scorecard", "price_collector"]) {
    assert.ok(glossary[key]?.term && glossary[key]?.meaning, key);
  }
  assert.match(glossary.pays_for_itself.meaning, /never pays a real bill or refills your budget/);
  for (const name of ["loss", "plans", "scorecard", "collector"]) assert.ok(page.use("glossary").SECTION_HELP[name]?.text, name);
});
