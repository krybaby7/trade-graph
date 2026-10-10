// hq-checkins.js behaviour: the follow-up offer inside the finished sheet (U4), replayed and expired requests
// (S4) and resends that can't silently drop edits (S5). Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import {loadHQ, fixture, response} from "./hq_harness.mjs";

const SNAP = fixture("quiet");
const AT = Date.parse(SNAP.as_of);
const HOUR = 3600000;
const DUE = SNAP.checkins.items.find(item => item.state === "due").checkin_id;
const page = (options = {}) => { const hq = loadHQ({now: AT, bind: true, ...options}); hq.show(SNAP); return hq; };
const button = (hq, words) => hq.byText("[data-hq-sheet] button", words);
const status = hq => hq.text(hq.$("[data-hq-sheet] .hq-form-status"));

test("Mark done offers the next check-in inside the finished sheet, not in a hidden toast (U4)", async () => {
  const hq = page({fetch: () => response(200, {checkin: null, replayed: false})});
  hq.use("checkins").done(DUE, null);
  button(hq, "Mark done").click();
  await hq.flush();
  assert.equal(status(hq), "Marked done.");
  assert.equal(hq.text(hq.$("[data-hq-toast]")), "", "no toast under the sheet");
  const next = button(hq, "Plan the next one in 4 h");
  assert.ok(next, "the follow-up is a button in the sheet");
  assert.ok(button(hq, "Close"));
  next.click();
  const sheet = hq.text(hq.$("[data-hq-sheet]"));
  assert.match(sheet, /Follow-up review \(regular review\)/);
  assert.match(sheet, /Yes, add 1 check-in/);
});

test("a resent check-in the server already saved says so (S4)", async () => {
  const hq = page({fetch: () => response(200, {checkin: null, replayed: true})});
  hq.use("checkins").done(DUE, null);
  button(hq, "Mark done").click();
  await hq.flush();
  assert.equal(status(hq), "Already saved earlier.");
});

test("after an unknown outcome a move is locked, and success names the time actually sent (S5)", async () => {
  let fail = true;
  const hq = page({fetch: () => fail ? response(503, null) : response(200, {checkin: null, replayed: false})});
  hq.use("checkins").more(DUE, null);
  button(hq, "Move it").click();
  const input = hq.$$("[data-hq-sheet] input").find(node => node.type === "datetime-local");
  button(hq, "Move it").click();
  await hq.flush();
  assert.match(status(hq), /We don't know if it went through\..*Sending again uses what you sent before\./);
  assert.equal(input.disabled, true, "the time can't be edited any more");
  const sent = hq.calls[0].body.planned_at;
  input.value = "2026-10-12T09:00";            // even if a value changed, the saved request is what goes
  fail = false;
  button(hq, "Send again").click();
  await hq.flush();
  assert.equal(hq.calls[1].body.planned_at, sent);
  assert.equal(hq.calls[1].body.request_id, hq.calls[0].body.request_id);
  assert.equal(status(hq), `Moved to ${hq.HQ.fmt.localDateTime(Date.parse(sent))}.`);
});

test("after an unknown outcome the Done note is read-only (S5)", async () => {
  const hq = page({fetch: () => response(502, null)});
  hq.use("checkins").done(DUE, null);
  const note = hq.$("[data-hq-sheet] textarea");
  note.value = "All fine";
  button(hq, "Mark done").click();
  await hq.flush();
  assert.equal(note.readOnly, true);
  assert.match(status(hq), /Sending again uses what you sent before\./);
  assert.equal(hq.calls[0].body.note, "All fine");
});

test("a saved check-in plan older than a day is dropped, not resent (S4)", () => {
  const stale = {saved_at: new Date(AT - 25 * HOUR).toISOString(), items: [{state: "pending",
    body: {request_id: "r-old", checkin_id: "c-old", action: "plan", planned_at: SNAP.as_of, prompt_kind: "interval", title: "Old"}}]};
  const hq = page({storage: {"tg-hq-pending:plan_checkin": stale}});
  hq.use("checkins").plan(null);
  const sheet = hq.text(hq.$("[data-hq-sheet]"));
  assert.doesNotMatch(sheet, /unknown outcome/);
  assert.match(sheet, /Today's usual set/);
  assert.equal(hq.store.has("tg-hq-pending:plan_checkin"), false);
});
