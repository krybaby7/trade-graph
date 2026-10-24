// hq-decide.js behaviour: percentage shares (U11), UTC bill months (C14), wizard focus (U6), replayed and
// expired requests (S4), locked resends (S5) and the preview's "HQ says" line (U9). Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import {loadHQ, fixture, response, clone} from "./hq_harness.mjs";

const SNAP = fixture("quiet");
const AT = Date.parse(SNAP.as_of);
const HOUR = 3600000;
const page = (options = {}) => { const hq = loadHQ({now: AT, bind: true, ...options}); hq.show(options.snapshot || SNAP); return hq; };
const button = (hq, words) => hq.byText("[data-hq-sheet] button", words);
const say = (hq, node) => hq.text(node);

test("a custom share is typed as a percentage and converted without floats (U11)", () => {
  const {percentToShare} = page().use("decide");
  const cases = {"40": "0.4", "12.5": "0.125", "100": "1", "100.0": "1", "5": "0.05", "0.5": "0.005", "50.25": "0.5025", "007": "0.07"};
  for (const [typed, share] of Object.entries(cases)) assert.equal(percentToShare(typed), share, typed);
  for (const typed of ["0", "0.0", "101", "100.5", "250", "-5", "4e1", "", "abc", "12.34567", "1000"]) {
    assert.equal(percentToShare(typed), null, typed);
  }
});

for (const tz of ["Europe/Berlin", "America/New_York", "Pacific/Kiritimati"]) {
  test(`bill months are UTC calendar months in ${tz} (C14)`, () => {
    const hq = page({tz});
    const {monthPeriod, monthOptions} = hq.use("decide");
    const october = monthPeriod("2026-10");
    assert.equal(october.start, "2026-10-01T00:00:00.000Z");
    assert.equal(october.end, "2026-11-01T00:00:00.000Z");
    assert.equal(october.label, "October 2026");
    assert.equal(monthPeriod("2026-12").end, "2027-01-01T00:00:00.000Z");
    assert.equal(monthPeriod("2026-13"), null);
    const options = monthOptions(true);
    assert.equal(JSON.stringify(options.slice(0, 2).map(option => [option.value, option.label])),
      JSON.stringify([["2026-09", "September 2026"], ["2026-08", "August 2026"]]));
  });
}

test("the suggested finished month is pre-selected by its UTC month, west of UTC too (C14)", () => {
  const hq = page({tz: "America/New_York"});
  hq.use("decide").open("declare_bills_complete", null);
  const month = hq.$("[data-hq-sheet] select");
  assert.equal(month.value, "2026-09");
  assert.ok(hq.doc.activeElement === month, "focus starts on the month");
});

test("each bill-wizard step moves focus, and a 40% share is sent as 0.4 (U6, U11)", async () => {
  const hq = page({fetch: call => call.url.endsWith("/expenses") ? response(503, null) : response(200, null)});
  hq.use("decide").open("record_bill", null);
  button(hq, "Next").click();
  const amount = hq.doc.activeElement;
  assert.equal(amount.localName, "input");
  assert.equal(amount.inputMode, "decimal", "focus is on the amount field");
  amount.value = "20";
  button(hq, "Next").click();
  assert.equal(say(hq, hq.doc.activeElement), "Trade Graph's share", "focus is on the step heading");
  const custom = hq.$$("[data-hq-sheet] input").find(input => input.name === "bill-share" && input.value === "custom");
  custom.click();
  const box = hq.$$("[data-hq-sheet] label.hq-field").find(label => say(hq, label).startsWith("Custom share (%)"));
  assert.ok(box && !box.hidden);
  const field = box.querySelector("input");
  assert.equal(field.placeholder, "40");
  field.value = "0";
  button(hq, "Review").click();
  assert.match(say(hq, hq.$("[data-hq-sheet] .hq-form-status")), /Enter a share above 0%, for example 50\./);
  field.value = "40";
  button(hq, "Review").click();
  assert.equal(hq.doc.activeElement.dataset.stepFocus, "", "focus is at the top of the preview");
  assert.match(say(hq, hq.$("[data-hq-sheet] .hq-bill-summary")), /40% counts as Trade Graph's cost/);
  hq.$$("[data-hq-sheet] input").find(input => input.type === "checkbox").click();
  const confirm = button(hq, SNAP.decisions.find(card => card.id === "record_bill").confirm_label);
  assert.equal(confirm.disabled, false, "enabled once ticked");
  confirm.click();
  await hq.flush();
  const sent = hq.calls.find(call => call.url.endsWith("/expenses"));
  assert.equal(sent.body.graph_share, "0.4");
  assert.equal(sent.body.period_start, "2026-10-01T00:00:00.000Z");
  // Unknown outcome: the saved request is what gets resent, so the way back to edit it closes (S5).
  assert.match(say(hq, hq.$("[data-hq-sheet] .hq-form-status")), /Sending again uses what you sent before\./);
  assert.equal(button(hq, "Back"), null);
  assert.ok(button(hq, "Close"));
  assert.ok(button(hq, "Send again"));
});

test("a resent request the server had already handled says so instead of a fresh success (S4)", async () => {
  const saved = {request_id: "r-1", body: {request_id: "r-1", mode: "paper"}, saved_at: new Date(AT - 2 * HOUR).toISOString()};
  const hq = page({storage: {"tg-hq-pending:start_trading": saved}, fetch: () => response(200, {replayed: true, attached: false})});
  const outcome = await hq.use("decide").submit("start_trading");
  assert.equal(hq.calls[0].body.request_id, "r-1");
  assert.equal(outcome.kind, "done");
  assert.equal(outcome.message, "Already done when you first pressed it (sent 2 h ago). Check Right now for the current state.");
});

test("a saved request older than a day is never resent (S4)", async () => {
  const saved = {request_id: "r-old", body: {request_id: "r-old", mode: "paper"}, saved_at: new Date(AT - 25 * HOUR).toISOString()};
  const hq = page({storage: {"tg-hq-pending:start_trading": saved}, fetch: () => response(200, {attached: false})});
  const outcome = await hq.use("decide").submit("start_trading");
  assert.notEqual(hq.calls[0].body.request_id, "r-old");
  assert.equal(outcome.message, "Started.");
});

test("'HQ says' starts with Recommended or Not available, or gives the server's advice (U9)", () => {
  const hq = page();
  const {hqSays} = hq.use("decide");
  const text = value => [{t: "text", v: value}];
  assert.equal(say(hq, hqSays({recommended: true, available: true, recommended_because: text("Your AI subscription bills aren't confirmed yet.")})),
    "Recommended: Your AI subscription bills aren't confirmed yet.");
  assert.equal(say(hq, hqSays({recommended: false, available: false, unavailable_because: text("The team is off."),
    preview: {when: text("Only before PC maintenance.")}})), "Not available: The team is off.");
  assert.equal(say(hq, hqSays({recommended: false, available: true, preview: {when: text("Not recommended during an automatic safety stop.")}})),
    "Not recommended during an automatic safety stop.");
});

test("the preview of a recommended decision says so in 'HQ says' (U9)", () => {
  const snapshot = clone(SNAP);
  const card = snapshot.decisions.find(entry => entry.id === "pause_ai");
  card.recommended = true;
  card.recommended_because = [{t: "text", v: "The allowance is nearly used up."}];
  const hq = page({snapshot});
  hq.use("decide").open("pause_ai", null);
  const row = hq.$$("[data-hq-sheet] .hq-preview-row").find(entry => say(hq, entry.querySelector("dt")) === "HQ says");
  assert.equal(say(hq, row.querySelector("dd")), "Recommended: The allowance is nearly used up.");
});
