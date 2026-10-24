// hq-render.js "Needs you" (W10-C, C9): the card HQ shows when a bought coin has had no safety exit for 10 minutes.
// The cards come from tests/fixtures/hq_cards/no_safety_exit.json, built by the real server builder
// (api/hq_protection.py; tests/integration/test_hq_no_safety_exit.py keeps the file equal to it).
// Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {loadHQ, fixture} from "./hq_harness.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const CARDS = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "fixtures", "hq_cards", "no_safety_exit.json"), "utf8"));

// A snapshot fixture with the cards put where the server puts "act" cards: first in Needs you.
const open = (name, cards = CARDS.paper) => {
  const data = fixture(name);
  assert.equal(data.as_of, CARDS.as_of, "the cards are built at the snapshot fixture's time");
  data.attention = [...cards, ...data.attention];
  data.now.attention_count += cards.length;
  const page = loadHQ({now: Date.parse(data.as_of)});
  page.show(data);
  return {page, data, view: page.view("attention")};
};
const card = (page, id) => page.$(`[data-row="attn:${id}"]`);
// The only buttons a card may hold explain its words: a time (tap for the full date) or a glossary term.
const actions = row => row.querySelectorAll("button").filter(node => !/\bhq-(time|term)\b/.test(node.className));

test("the card says which coin, how much, since when and what the system is doing (W10-C)", () => {
  const {page} = open("quiet");
  const sol = card(page, "no_safety_exit:SOL/USD");
  assert.ok(sol, "the card is listed");
  assert.equal(sol.dataset.sev, "act");
  assert.equal(page.text(sol.querySelector(".hq-attn-title")), "SOL/USD: 5 SOL has no safety exit");
  assert.match(page.text(sol.querySelector(".hq-attn-head")), /^For you to do · since 25 min ago$/);
  assert.match(page.text(sol), /Software puts a safety exit on every buy as it fills; this one has had none since 1:34 PM/);
  assert.match(page.text(sol), /13:34 UTC\./, "tapping the time gives the full date and UTC");
  assert.match(page.text(sol), /Until it has one, nothing sells these coins by itself if the price falls\./);
  assert.match(page.text(sol.querySelector(".hq-attn-how")),
    /^Software tries again at every step of the team's service\. The last try failed: a problem HQ can't describe in plain words\. HQ has no button for this; Owner controls has the account-wide settings\.$/);
  assert.ok(sol.querySelector('[data-term="safety_exit"]'), "'safety exit' opens the glossary");
});

test("the card has no button that trades: only a link to the records, even for an owner who can act (W10-C)", () => {
  const {page, data} = open("quiet");
  assert.equal(data.viewer.can_act, true);
  page.byText("button", "Show").dispatchEvent(new page.dom.Event("click"));  // every card, not only the first three
  for (const item of CARDS.paper) {
    const row = card(page, item.id);
    assert.ok(row, item.id);
    assert.equal(actions(row).length, 0, `${item.id} has no button that does anything`);
    const links = row.querySelectorAll("a");
    assert.equal(links.length, 1);
    assert.equal(links[0].getAttribute("href"), "/trading");
    assert.equal(page.text(links[0]), "Trading records ›");
  }
});

test("Needs you lists the act cards first, three at a time, and counts them (W10-C)", () => {
  const {page, view} = open("quiet");
  const titles = view.querySelectorAll(".hq-attn-title").map(node => page.text(node));
  assert.deepEqual(titles, ["SOL/USD: 5 SOL has no safety exit", "ETH/USD: 0.5 ETH has no safety exit",
    "ADA/USD: 1500 ADA has no safety exit"]);
  assert.equal(page.text(page.$("[data-hq-attention-count]")), "6");
  page.byText("button", "Show").dispatchEvent(new page.dom.Event("click"));
  const more = card(page, "no_safety_exit:more");
  assert.ok(more, "the card naming the other coins appears after 'Show more'");
  assert.equal(page.text(more.querySelector(".hq-attn-title")), "2 more coins have no safety exit: XRP/USD, DOGE/USD");
  assert.match(page.text(more), /Each has had none for at least 10 minutes\. Trading records lists every order\./);
});

test("a viewer who can't act sees the same card, and a live account never reads 'practice' in it (W10-C)", () => {
  const reader = open("reader");
  const row = card(reader.page, "no_safety_exit:SOL/USD");
  assert.ok(row);
  assert.equal(actions(row).length, 0);
  const live = open("live", CARDS.live);
  const liveRow = card(live.page, "no_safety_exit:SOL/USD");
  assert.match(live.page.text(liveRow), /Software puts none on while the cancel-all setting is on/);
  assert.doesNotMatch(live.page.text(liveRow), /practice|paper/i);
});
