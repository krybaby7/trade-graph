/* Trade Graph HQ glossary, room explanations and "What am I looking at?" help (copy deck §D.12, §D.13). */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const {el, button, link} = HQ;

  // key → plain meaning, why it matters, example (optional) and the detailed page that shows the records.
  const GLOSSARY = Object.freeze({
    practice_money: {term: "Practice money", meaning: "Pretend money used to test the team.", why: "Nothing real is bought or sold.",
      example: "$10,000 practice money, worth about €8,900.", href: "/journal"},
    real_trading_off: {term: "Real trading: off", meaning: "No real exchange orders can be sent.", why: "Your real money is never touched.",
      example: "", href: "/owner"},
    protect_only: {term: "Protect-only", meaning: "No new AI work and no new buying; anything held stays protected.", why: "The safest pause.",
      example: "You paused the AI (protect-only).", href: "/owner"},
    safety_stop: {term: "Automatic safety stop", meaning: "The system switched to protect-only by itself after a problem.",
      why: "It can't be lifted from this page.", example: "After the Trader's turn failed.", href: "/owner"},
    allowance: {term: "AI allowance", meaning: "The share of your AI subscription the team may use.", why: "Below the line, departments wait.",
      example: "72% left this week.", href: "/costs"},
    stop_line: {term: "Stop line", meaning: "The level below which new AI work waits.", why: "Keeps some allowance for your own use.",
      example: "New work needs more than 40%.", href: "/costs"},
    turn: {term: "Turn", meaning: "One scheduled piece of work by a department.", why: "Between turns the team is idle.",
      example: "Trader every 4 hours.", href: "/organization"},
    timetable: {term: "Timetable", meaning: "When each department takes its turn.", why: "Missed turns are merged into one.",
      example: "", href: "/organization"},
    research_note: {term: "Research note", meaning: "A short dated note for the Trader.", why: "Notes expire; expired notes are ignored.",
      example: "In use until 12:00.", href: "/organization"},
    wait: {term: "Waiting (a decision)", meaning: "Deciding not to trade this turn.", why: "A normal, valid choice.", example: "",
      href: "/trading"},
    double_check: {term: "Double-check", meaning: "Comparing orders and balances against records.", why: "Unclear orders block Resume.",
      example: "", href: "/trading"},
    unclear_order: {term: "Unclear order", meaning: "An order whose outcome isn't confirmed yet.",
      why: "No new buying for that coin meanwhile.", example: "", href: "/trading"},
    estimate: {term: "Estimate", meaning: "A total using a price or rate that isn't final.", why: "It may change slightly.", example: "",
      href: "/journal"},
    euro_rate: {term: "Euro rate", meaning: "The daily dollar-to-euro rate.", why: "Your money is in dollars but shown in euros.",
      example: "", href: "/journal"},
    currency_effect: {term: "Exchange-rate effect", meaning: "Change caused only by the exchange rate.",
      why: "It moves even when nobody trades.", example: "", href: "/journal"},
    team_effect: {term: "The team's trading", meaning: "Change caused by the team's trades.", why: "This is what the Trader controls.",
      example: "", href: "/journal"},
    pays_for_itself: {term: "Pays for itself?", meaning: "Whether trading results cover the real costs you entered.",
      why: "The real test, later.", example: "", href: "/costs"},
    bill_share: {term: "Trade Graph's share", meaning: "The part of a bill that belongs to Trade Graph.", why: "Only that part counts.",
      example: "50% of a $20 bill.", href: "/costs"},
    bills_complete: {term: "I've entered every bill", meaning: "Your statement that every bill for a month is entered.",
      why: "Without it, costs stay \"not known\".", example: "", href: "/costs"},
    history_gap: {term: "History gap", meaning: "Some early price records were lost in a repair.",
      why: "Some since-start answers stay unknown.", example: "", href: "/journal"},
    avg_20h: {term: "20-hour average", meaning: "Average price over the last 20 hours.", why: "Shows the recent direction.", example: "",
      href: "/trading"},
    avg_50h: {term: "50-hour average", meaning: "Average price over the last 50 hours.", why: "Shows the recent direction.", example: "",
      href: "/trading"},
    trial: {term: "Trial", meaning: "A test period after switching an improvement on.", why: "If it fails, it's undone automatically.",
      example: "", href: "/changes"},
    rules: {term: "Rules", meaning: "The Trader's permission slip: which coins, how much.", why: "It can't go beyond these.",
      example: "At most 50% in coins.", href: "/trading"},
    sign_of_life: {term: "Sign of life", meaning: "The team's regular \"I'm alive\" signal.", why: "If it stops, nothing may be watched.",
      example: "8 s ago.", href: "/progress"},
    review_checkin: {term: "Review check-in", meaning: "A time you planned to review the run with your separate AI chat.",
      why: "Keeps you in the loop.", example: "", href: ""},
    review_prompt: {term: "Review prompt", meaning: "Ready-made text to paste into your review chat.",
      why: "Asks the right questions without changing anything.", example: "", href: ""},
  });

  // Room id → why the room matters (§D.12). Job lines come from the server.
  const ROOM_WHY = Object.freeze({
    trader: "The only department that can move (practice) money.",
    research: "The Trader only uses notes that are still in date.",
    learning: "Lessons feed later decisions.",
    leader: "Can change timetables and start improvement work.",
    optimisation: "Its suggestions can lead to improvements; nothing changes automatically.",
    engineer: "Changes go through automatic checks and a trial.",
    secretary: "If it flags a problem, the Leader gets an extra review.",
    market: "Without fresh prices the Trader can't buy.",
    accounts: "If an order's status is unclear, Resume waits until it's settled.",
  });

  const SECTION_HELP = Object.freeze({
    now: {title: "Right now", text: "Built from the system's own records every 30 seconds. Quiet means nobody is working; it doesn't mean broken."},
    attention: {title: "Needs you", text: "Only things that need a person are listed. An empty list means you can relax."},
    upnext: {title: "Up next", text: "The moments something can change. Between them the team is idle."},
    map: {title: "HQ map", text: "A light changes only when a real record says so. Tap a room for details."},
    calendar: {title: "Calendar", text: "Solid items are planned; '≈' items are estimates, because each turn sets the next one when it actually runs."},
    story: {title: "Story", text: "Everything here comes from saved records. 'In their words' quotes are short summaries the departments wrote."},
    money: {title: "Money", text: "Practice money and real costs are kept apart. Unknown is never shown as zero."},
    decide: {title: "Decide", text: "Every button shows what will happen first and asks you to confirm. HQ never acts on its own."},
  });

  const LIGHT_LEGEND = Object.freeze([
    ["working", "Working"], ["waiting", "Waiting"], ["resting", "Resting or OK"], ["paused", "Paused"],
    ["on_call", "On call"], ["stuck", "Stuck or needs a look"], ["off", "Off"],
  ]);

  function detailsHref(entry) {
    if (!entry.href) return null;
    if (entry.href === "/owner" && !HQ.snapshot()?.links?.owner) return null;
    return HQ.safePath(entry.href);
  }

  function explanation(key, compact) {
    const entry = GLOSSARY[key];
    const box = el("div", compact ? "hq-explain hq-explain-inline" : "hq-explain");
    if (!entry) {
      box.append(el("p", "", "No explanation is written for this word yet."));
      return box;
    }
    box.append(el("p", "hq-explain-meaning", entry.meaning));
    const why = el("p", "hq-explain-why");
    why.append(el("strong", "", "Why it matters: "), entry.why);
    box.append(why);
    if (entry.example) {
      const example = el("p", "hq-explain-example");
      example.append(el("strong", "", "Example: "), entry.example);
      box.append(example);
    }
    const href = detailsHref(entry);
    if (href && !compact) box.append(link(href, "See the detailed page ›", "hq-more-link"));
    if (HQ.technical()) box.append(el("p", "hq-code", `[${key}]`));
    return box;
  }

  function explain(key, opener) {
    const entry = GLOSSARY[key];
    HQ.sheet.open({
      title: entry ? entry.term : "Explanation",
      kind: "glossary",
      from: opener,
      build: body => body.append(explanation(key, false)),
    });
  }

  // Inside an open sheet a word expands in place, so the sheet underneath isn't replaced.
  function toggleInline(control) {
    const next = control.nextElementSibling;
    if (next?.classList.contains("hq-explain-inline")) {
      next.remove();
      control.setAttribute("aria-expanded", "false");
      return;
    }
    control.setAttribute("aria-expanded", "true");
    control.after(explanation(control.dataset.term, true));
  }

  function legend() {
    const row = el("ul", "hq-legend");
    for (const [light, label] of LIGHT_LEGEND) {
      const item = el("li", "hq-light");
      item.dataset.light = light;
      item.append(HQ.icon(`light-${light}`, "hq-icon hq-light-icon"), el("span", "", label));
      row.append(item);
    }
    return row;
  }

  function help(section, opener) {
    const entry = SECTION_HELP[section];
    if (!entry) return;
    HQ.sheet.open({
      title: `What am I looking at? ${entry.title}`,
      kind: "help",
      from: opener,
      build: body => {
        body.append(el("p", "hq-explain-meaning", entry.text));
        if (section === "map") {
          body.append(el("h3", "hq-sheet-label", "What the lights mean"), legend());
          body.append(el("p", "hq-muted", "An amber “!” means the room's last turn had a problem."));
        }
        if (section === "calendar") {
          const items = el("ul", "hq-plain-list");
          for (const line of ["● planned: the time is saved in the records.", "≈ estimate: worked out from the timetable.",
            "◆ yours: a check-in you planned.", "If several turns were missed, the team does one catch-up turn, not all of them."]) {
            items.append(el("li", "", line));
          }
          body.append(items);
        }
      },
    });
  }

  function glossaryList() {
    const box = el("dl", "hq-glossary");
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      const row = el("div", "hq-glossary-row");
      const term = el("dt", "");
      const control = button("hq-term", entry.term);
      control.dataset.term = key;
      term.append(control);
      row.append(term, el("dd", "", entry.meaning));
      box.append(row);
    }
    return box;
  }

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target : null;
    const term = target?.closest("[data-term]");
    if (term && term.matches("button")) {
      event.preventDefault();
      if (term.closest("[data-hq-sheet]") && HQ.sheet.isOpen()) toggleInline(term);
      else explain(term.dataset.term, term);
      return;
    }
    const helper = target?.closest("[data-hq-help]");
    if (helper) help(helper.dataset.hqHelp, helper);
  });

  HQ.define("glossary", {GLOSSARY, ROOM_WHY, SECTION_HELP, explain, help, legend, glossaryList});
})();
