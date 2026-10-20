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
    pays_for_itself: {term: "Result against real costs", meaning: "Compares the trading result with the real costs you entered. On the paper account the result is simulated: paper money never pays a real bill or refills your budget.",
      why: "It shows whether results of this size would cover the costs; it is not proof of profit.", example: "", href: "/costs"},
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
    estimated_cost: {term: "Estimated (a cost)", meaning: "A call charged at the most it could cost, because its use was never reported.",
      why: "It may have cost less; it is never counted as free.", example: "", href: "/costs"},
    unresolved_cost: {term: "Not known yet (a cost)", meaning: "A call whose use hasn't been reported yet, counted at the most it could cost.",
      why: "Unknown is never shown as zero.", example: "Up to $0.02 (1 call).", href: "/costs"},
    testing_alert: {term: "Testing alert", meaning: "The amount at which HQ tells you how much AI testing has cost so far.",
      why: "It's an alert only: nothing pauses.", example: "Perplexity: $20.", href: "/costs"},
    prepaid_credit: {term: "Prepaid credit", meaning: "Money you loaded on an AI provider's account in advance.",
      why: "HQ shows spending since you last checked it; the provider's own page has the exact balance.", example: "",
      href: "/costs"},
    loss_limit: {term: "Daily loss limit", meaning: "The most the account may lose in one trading day, as a share of its value at the day start (fees and coins held count).",
      why: "Reaching it stops new buying until the next day start; safety exits and sales keep working.", example: "3% of $10,000 is $300.",
      href: "/trading"},
    day_plan: {term: "Day plan", meaning: "The Trader's plan for one coin today: waiting buys below the price, each with a safety exit.",
      why: "Software checks every plan against your limits before anything is sent.", example: "", href: "/trading"},
    safety_exit: {term: "Safety exit", meaning: "A sell order software puts on coins as soon as a planned buy fills: a stop below, and a target above if the plan set one.",
      why: "A position is never left unprotected while the Trader sleeps.", example: "Stop at 97, target at 104.",
      href: "/trading"},
    scorecard: {term: "Scorecard", meaning: "The result after every fee, compared with keeping the cash and with holding Bitcoin over the same days.",
      why: "It is evidence about one period, not proof of profit and never permission for real money.", example: "",
      href: "/journal"},
    price_collector: {term: "Price collector", meaning: "The part of the service that saves price candles from Kraken and runs the hourly coin scan.",
      why: "Charts, alarms and the coin scanner read what it saves.", example: "", href: "/progress"},
    playbook: {term: "Playbook", meaning: "The Trading desk's own record of which tested strategy it means to use in which kind of market, or to wait in cash, and why.",
      why: "It is the desk's record, not a rule: software never refuses a day plan for not following it.", example: "Sideways: buy dips with dip-rebound@1.",
      href: "/trading"},
    stance: {term: "Stance", meaning: "What the desk chose for one coin at a wake: wait in cash, buy dips, follow the trend or something else, with a short reason.",
      why: "The scorecard splits every trade by the stance behind it, so lessons can say which choice earned or lost.", example: "SOL/USD: buy dips.",
      href: "/trading"},
    market_type: {term: "Market type", meaning: "Software's label for a coin from its hourly prices: rising, sideways, falling or unclear. A label more than 2 hours old is not used.",
      why: "The desk chooses per market type, and the scorecard shows what each strategy earned in each one.", example: "BTC/USD: falling.",
      href: "/trading"},
    strategy_lab: {term: "Strategy lab", meaning: "The desk's own test bench: it replays strategy versions on stored prices with real fees, tuning on earlier days and confirming once on later, untouched days.",
      why: "A result is evidence the desk weighs, never a pass mark and never permission for real money.", example: "",
      href: "/organization"},
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

  // A live account never hears about "practice" money (review C1): these twins replace the paper lines.
  const ROOM_WHY_LIVE = Object.freeze({...ROOM_WHY, trader: "The only department that can move the account's money."});
  // Words that only exist in a practice run; a live account's glossary leaves them out.
  const PAPER_ONLY_TERMS = new Set(["practice_money", "real_trading_off"]);

  const SECTION_HELP = Object.freeze({
    now: {title: "Right now", text: "Built from the system's own records every 30 seconds. Quiet means nobody is working; it doesn't mean broken."},
    attention: {title: "Needs you", text: "Only things that need a person are listed. An empty list means you can relax."},
    upnext: {title: "Up next", text: "The moments something can change. Between them the team is idle."},
    map: {title: "HQ map", text: "A light changes only when a real record says so. Tap a room for details."},
    calendar: {title: "Calendar", text: "Solid items are planned; '≈' items are estimates, because each turn sets the next one when it actually runs."},
    story: {title: "Story", text: "Everything here comes from saved records. 'In their words' quotes are short summaries the departments wrote."},
    money: {title: "Money", text: "Practice money and real costs are kept apart. Unknown is never shown as zero."},
    decide: {title: "Decide", text: "Every button shows what will happen first and asks you to confirm. HQ never acts on its own."},
    spending: {title: "AI spending", text: "Real costs of paid AI from receipts, kept apart from practice money. Costs not known yet are never shown as zero. Alerts never pause anything."},
    charts: {title: "Price charts", text: "Saved candles with the indicators, alarms and orders the team chose. Missing candles stay as gaps. Looking at a chart never changes anything."},
    wakeplan: {title: "Next check-ins", text: "When each department looks next: the time it chose itself, or the next day start. Wake-ups happen only while the team is running."},
    watchlist: {title: "Today's focus coins and alarms", text: "Today's focus coins from Research and the price alarms the departments set. Looking never changes anything."},
    wakes: {title: "Wake log", text: "Every wake-up from the saved records, with its reasons and the AI allowance at that moment. Nothing limits how often a department is woken."},
    loss: {title: "Today's loss limit", text: "Today's loss against the daily loss limit you recorded, with fees and coins held at their latest prices. Reaching it stops new buying until the next day start; safety exits and sales keep working."},
    plans: {title: "Today's day plans", text: "The Trader's plans for today, coin by coin: waiting buys, the safety exit software puts on every filled part, fills and refusals. Looking never changes anything."},
    scorecard: {title: "Scorecard", text: "The result after every fee since the account opened, against keeping the cash and holding Bitcoin. It is evidence only: it never proves a profit or allows real money."},
    collector: {title: "Price collector", text: "The price collector saves candles and runs the hourly coin scan inside the paper service. This shows its newest records and any problems."},
    desk: {title: "Trading desk room", text: "What the Trading desk believes and tests: its playbook per market type, today's choice per coin, its strategy lab, the news it read and what each strategy earned in each market type. Looking never changes anything."},
  });
  const SECTION_HELP_LIVE = Object.freeze({
    ...SECTION_HELP,
    money: {title: "Money", text: "Account money and real costs are kept apart. Unknown is never shown as zero."},
    spending: {title: "AI spending", text: "Real costs of paid AI from receipts. Account gains are never counted against them. Costs not known yet are never shown as zero. Alerts never pause anything."},
  });

  const isLive = () => HQ.snapshot()?.mode === "live";
  const roomWhy = (id, live = isLive()) => (live ? ROOM_WHY_LIVE : ROOM_WHY)[id] || null;
  const sectionHelp = (live = isLive()) => live ? SECTION_HELP_LIVE : SECTION_HELP;

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
    const entry = sectionHelp()[section];
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

  function glossaryList(live = isLive()) {
    const box = el("dl", "hq-glossary");
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      if (live && PAPER_ONLY_TERMS.has(key)) continue;
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

  HQ.define("glossary", {GLOSSARY, ROOM_WHY, ROOM_WHY_LIVE, SECTION_HELP, SECTION_HELP_LIVE, roomWhy, sectionHelp, explain, help,
    legend, glossaryList});
})();
