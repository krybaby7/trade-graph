// HQ Trading desk room (W13-A): hq-desk.js on the harness page with a scripted GET /api/v1/hq/desk. The document is
// tests/fixtures/hq_desk/desk_week.json, made by the real builder (api/hq_desk.py) from the scripted week of
// tests/hq_desk_support.py (all synthetic, labelled synthetic_fixture); the server writes every sentence, so the
// tests check where each one lands, that the desk's words stay text, and when the page reads.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {SCRIPTS, clone, fixture, loadHQ, response} from "./hq_harness.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const WEEK = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "fixtures", "hq_desk", "desk_week.json"), "utf8"));
const DESK_SCRIPTS = [...SCRIPTS, "hq-desk.js"];

function panel(page) {
  const {doc} = page;
  const article = doc.createElement("article");
  article.setAttribute("class", "hq-panel hq-desk");
  const box = doc.createElement("div");
  box.setAttribute("data-hq", "desk");
  article.append(box);
  doc.querySelector("#hq").append(article);
}

async function start({answer = () => response(200, WEEK), active = ["hq"]} = {}) {
  const page = loadHQ({scripts: DESK_SCRIPTS, fetch: async call => {
    if (call.url.startsWith("/api/v1/hq/desk")) return answer(call);
    return response(200, fixture("busy"));
  }});
  panel(page);
  for (const id of active) page.doc.querySelector(`#${id}`).classList.add("is-active");
  page.show(fixture("busy"));
  page.use("desk").init();
  await page.flush();
  await page.flush();
  return page;
}
const deskCalls = page => page.calls.filter(call => call.url.startsWith("/api/v1/hq/desk"));
const part = (page, name) => page.view("desk").querySelector(`[data-part="${name}"]`);

test("the room reads its own endpoint once and lays out its five parts in order (W13-A)", async () => {
  const page = await start();
  assert.deepEqual(deskCalls(page).map(call => [call.url, call.method]), [["/api/v1/hq/desk", "GET"]]);
  const view = page.view("desk");
  assert.match(page.text(view), /^Playbook revision 2\. 2 coins chosen today\. Lab: 0 queued, 0 running\./);
  assert.deepEqual(view.querySelectorAll(".hq-desk-part").map(node => node.getAttribute("data-part")),
    ["playbook", "stances", "lab", "news", "split"]);
  assert.ok(view.querySelectorAll(".hq-desk-part").every(node => node.getAttribute("data-state") === "shown"));
});

test("the playbook says per market type the strategy or wait, and why, in the desk's words (W13-A)", async () => {
  const page = await start();
  const playbook = part(page, "playbook");
  const rows = playbook.querySelectorAll(".hq-desk-choices > li");
  assert.deepEqual(rows.map(row => row.getAttribute("data-market-type")), ["rising", "sideways", "falling", "unclear"]);
  assert.match(page.text(rows[1]), /^↘\(buy dips\) Sideways: buy dips with dip-rebound@1\.\s?Rest maker bids after dips inside the range\./);
  assert.match(page.text(rows[2]), /^○\(wait\) Falling: wait in cash\./);
  assert.equal(rows[1].querySelector(".hq-desk-quote").getAttribute("aria-label"), "In their words");
  assert.match(page.text(playbook), /It replaced revision 1 from/);
  const why = playbook.querySelector("details.hq-quote");
  assert.equal(page.text(why.querySelector("summary")), "Why the desk changed it, in their words");
});

test("today's choice per coin shows the market type the desk saw and the label now, never colour alone (W13-A)", async () => {
  const page = await start();
  const coins = part(page, "stances").querySelectorAll(".hq-desk-coin");
  assert.deepEqual(coins.map(row => row.getAttribute("data-row")), ["stance:BTC/USD", "stance:SOL/USD"]);
  assert.match(page.text(coins[0]), /^○\(wait\) BTC\/USD Wait in cash\.\s?\(label current\) Falling when it chose/);
  assert.match(page.text(coins[1]), /SOL\/USD Buy dips with dip-rebound@1\./);
  assert.match(page.text(coins[1]), /\(label current\) Sideways now/);
  assert.match(page.text(part(page, "stances")), /Today's stance for 2 coins: BTC\/USD waits \(falling\), SOL\/USD buys dips \(sideways\)\./);
});

test("the lab shows its newest results against holding and the tries per family (W13-A)", async () => {
  const page = await start();
  const lab = part(page, "lab");
  const results = lab.querySelectorAll(".hq-desk-results > li");
  assert.match(page.text(results[0]), /^✓\(done\) Confirmed dip-rebound@1 on SOL\/USD over 8 Sep to 15 Sep: 2 reports\./);
  const reports = results[0].querySelectorAll(".hq-desk-report");
  assert.equal(reports.length, 2);
  assert.match(page.text(reports[0]), /result after fees \$[\d,.]+; holding SOL\/USD over the same days made −?\$[\d,.]+, so the strategy did \$[\d,.]+ (better|worse)\./);
  assert.match(page.text(reports[0]), /^▲\(earned\)/);
  const families = lab.querySelector("details.hq-desk-families");
  assert.match(page.text(families.querySelector("summary")), /^Tries per strategy family \(2\)$/);
  assert.match(page.text(families), /dip-rebound: 1 version, 1 study, 2 tuning runs and 2 confirmation runs; confirmed: dip-rebound@1\./);
});

test("the news part quotes the Leader's round and the lab inbox as text (W13-A)", async () => {
  const data = clone(WEEK);
  data.news.round.summary.text = "Synthetic: <img src=x onerror=alert(1)> round.";
  const page = await start({answer: () => response(200, data)});
  const news = part(page, "news");
  assert.match(page.text(news), /The news the desk read at its last turn/);
  assert.match(page.text(news), /Synthetic: <img src=x onerror=alert\(1\)> round\./);
  assert.equal(news.querySelector("img"), null, "model text is never markup");
  const inbox = news.querySelector("details.hq-desk-inbox");
  assert.match(page.text(inbox), /Ideas in the lab inbox \(1\).*the desk cited it in 4 lab requests\./);
  assert.match(page.text(news), /shadow mode/);
});

test("the split says what each strategy earned in each market type, with a word beside every mark (W13-A)", async () => {
  const page = await start();
  const split = part(page, "split");
  const rows = split.querySelectorAll(".hq-desk-choices-earned > li");
  assert.equal(rows.length, 1);
  assert.equal(rows[0].getAttribute("data-row"), "split:dip-rebound@1|sideways|buy_dips");
  assert.match(page.text(rows[0]), /^▼\(lost\) dip-rebound@1 when sideways, buying dips: 2 trades \(1 won, 1 lost\) and 1 still held, result after fees −\$7\.05\./);
  assert.deepEqual(split.querySelectorAll("details.hq-desk-split > summary").map(node => page.text(node)),
    ["By strategy version", "By market type at entry", "By the desk's stance"]);
  assert.match(page.text(split.querySelector(".hq-desk-note")), /never proof of profit or permission for real money/);
});

test("a part the database doesn't have yet says so, and an unreadable part is marked (W13-A)", async () => {
  const data = clone(WEEK);
  data.playbook = {state: "unreadable", headline: [{t: "text", v: "The playbook couldn't be read (StaleState)."}],
    revision: null, set_at: null, revisions_total: 0, why: null, choices: [], previous: null};
  data.lab = {state: "not_available", headline: [{t: "text", v: "The strategy lab isn't in this database yet."}],
    queued: 0, running: 0, next: [], results: [], more_results: 0, families: [], more_families: 0};
  const page = await start({answer: () => response(200, data)});
  const playbook = part(page, "playbook");
  assert.equal(playbook.getAttribute("data-state"), "unreadable");
  assert.ok(playbook.querySelector(".hq-warn-text"));
  assert.match(page.text(part(page, "lab")), /isn't in this database yet/);
  assert.equal(part(page, "lab").querySelector(".hq-desk-results"), null);
});

test("the room waits until the HQ tab is open, and a failed read keeps what it saw (W13-A)", async () => {
  const idle = await start({active: ["money"]});
  assert.equal(deskCalls(idle).length, 0, "nothing is read while another tab is open on a phone");
  let fail = false;
  const page = await start({answer: () => (fail ? response(500, null) : response(200, WEEK))});
  fail = true;
  page.clock.now += 26000;
  page.use("desk").wake();
  await page.flush();
  assert.equal(deskCalls(page).length, 2);
  assert.match(page.text(page.view("desk")), /couldn't be refreshed just now; this is what HQ saw last\..*Playbook revision 2/);
});

test("the Trader's room sheet opens the Trading desk room (W13-A)", async () => {
  const page = await start();
  page.HQ.bindGlobal?.();
  const tile = page.view("map")?.querySelector('[data-room="trader"]');
  assert.ok(tile, "the map draws the Trader's room");
  tile.dispatchEvent(new page.dom.Event("click"));
  await page.flush();
  const button = page.byText("button", "Open the Trading desk room");
  assert.ok(button, "the Trader's room sheet offers the Trading desk room");
  button.dispatchEvent(new page.dom.Event("click"));
  await page.flush();
  assert.ok(deskCalls(page).length >= 1);
});
