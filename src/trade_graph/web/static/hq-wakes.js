/* Trade Graph HQ wake-ups (T39): lays out GET /api/v1/hq/wakes in three panels: "Next check-ins" and "Today's focus
   coins and alarms" in the Calendar tab, and the "Wake log" in the Story tab. The server reads every wake-up, check-in,
   focus coin and alarm from saved records and writes every sentence; this file only places them and pages back
   through the log. It reads the log only while one of its panels is on screen, and reading starts nothing. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const doc = document;
  const {el, button, list, obj, str, textEl, hasText, parseIso} = HQ;

  const SCHEMA = "trade-graph.hq.wakes.v1";
  // The page asks again on HQ's own 30-second poll while a panel is on screen, at most this often.
  const REFRESH_MS = 25000;
  const PANELS = Object.freeze(["wakeplan", "watchlist", "wakes"]);
  const DEPARTMENTS = Object.freeze(["trader", "research", "leader", "learning", "optimisation"]);
  const DEPT_WORDS = Object.freeze({trader: "Trader", research: "Research", leader: "Leader", learning: "Learning",
    optimisation: "Optimisation"});
  const STATUS_WORDS = Object.freeze({queued: "Waiting", applying: "Finishing", completed: "Done",
    failed: "Didn't finish", obsolete: "Too late"});
  const STEP_WORDS = Object.freeze({day_start: "Day start", scan: "Coin scanner", focus: "Focus coins",
    day_plan: "Day plans", day_end_review: "Day end"});
  // A glyph and a word for every step status, so the state never rests on colour alone.
  const STEP_MARKS = Object.freeze({not_yet: ["○", "not yet"], started: ["◐", "in progress"], done: ["✓", "done"],
    failed: ["▲", "didn't finish"], skipped: ["↷", "skipped"]});
  const CHECK_IN_CHIPS = Object.freeze({due: ["Due", "hq-chip-warn"], awake: ["Awake", "hq-chip-new"]});

  const state = {
    document: null, note: "", busy: false, started: false, loadedAt: 0, version: 0, filter: "all",
    older: new Map(), olderBefore: null, olderDone: false, olderBusy: false, olderNote: "", initialised: false,
    seen: typeof globalThis.IntersectionObserver !== "function",
  };

  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const time = (at, style) => textEl("span", "hq-time-inline", [{t: "time", at, style}], {inert: true});
  const liveTime = (at, style) => textEl("span", "hq-time-inline", [{t: "time", at, style}]);
  const chip = (text, className = "") => el("span", `hq-chip ${className}`.trim(), text);
  const empty = text => el("p", "hq-empty", text);
  function deptName(id) {
    return HQ.use("render")?.deptName(id, HQ.snapshot()) || DEPT_WORDS[id] || str(id);
  }
  function deptIcon(id) { return HQ.icon(DEPARTMENTS.includes(id) ? id : "hq", "hq-icon hq-dept-icon"); }
  // Model text only ever appears as a labelled quote; a label says whose words they are when it isn't obvious.
  function quote(item, label = null) {
    if (!item || typeof item.text !== "string" || !item.text) return null;
    const box = el("details", "hq-quote");
    box.append(el("summary", "", label || str(item.label) || "In their words"), el("blockquote", "", item.text));
    return box;
  }
  function container(name) { return doc.querySelector(`[data-hq="${name}"]`); }
  function bump() {
    state.version += 1;
    for (const name of PANELS) HQ.rerender(name);
  }

  // --- When to read ---------------------------------------------------------------------------------------------

  const tabMode = () => Boolean(globalThis.matchMedia?.("(max-width: 767px)")?.matches);
  function shown() {
    if (doc.hidden || !state.seen) return false;
    return PANELS.some(name => {
      const node = container(name);
      if (!node) return false;
      const panel = node.closest(".hq-panel") || node;
      if (panel.hidden) return false;
      const section = node.closest("[data-hq-section]");
      return !(tabMode() && section && !section.classList.contains("is-active"));
    });
  }
  const top = document_ => Math.max(0, ...list(document_?.items).map(item => item.cursor).filter(Number.isSafeInteger));
  const bottom = items => Math.min(...items.map(item => item.cursor).filter(Number.isSafeInteger));
  async function load() {
    if (state.busy) return;
    state.busy = true;
    try {
      const result = await HQ.api.get("wakes", {timeout: 15000});
      state.loadedAt = Date.now();
      const data = result.body;
      if (!result.ok || !data || data.schema !== SCHEMA) {
        state.note = state.document ? "The wake-ups couldn't be refreshed just now; this is what HQ saw last."
          : "The wake-ups couldn't be loaded just now. HQ will try again shortly.";
        return;
      }
      // Older pages stay only while the new first page still meets them, so no wake-up is ever skipped.
      const known = [...list(state.document?.items), ...state.older.values()];
      if (state.older.size && known.length && bottom(list(data.items)) > top({items: known})) resetOlder();
      state.document = data;
      state.note = "";
    } catch (error) {
      console.error(error);
      state.note = "The wake-ups couldn't be shown just now. HQ will try again shortly.";
    } finally {
      state.busy = false;
      bump();
    }
  }
  function resetOlder() {
    state.older.clear();
    state.olderBefore = null;
    state.olderDone = false;
    state.olderNote = "";
  }
  async function loadEarlier() {
    const before = state.olderBefore || state.document?.next_before;
    if (state.olderBusy || state.olderDone || !Number.isSafeInteger(before)) return;
    state.olderBusy = true;
    state.olderNote = "";
    bump();
    const result = await HQ.api.get("wakes", {before, timeout: 15000});
    const page = result.ok ? result.body : null;
    if (!page || page.schema !== SCHEMA || !Array.isArray(page.items)) {
      state.olderNote = "Couldn't load earlier wake-ups. Try again in a moment.";
    } else {
      for (const item of page.items) if (item?.id) state.older.set(item.id, item);
      state.olderBefore = Number.isSafeInteger(page.next_before) ? page.next_before : null;
      state.olderDone = state.olderBefore === null;
    }
    state.olderBusy = false;
    bump();
  }
  function wake() {
    if (!shown() || HQ.connection() === "signed_out") return;
    if (!state.started || Date.now() - state.loadedAt >= REFRESH_MS) {
      state.started = true;
      load();
    }
  }

  // --- Shared pieces --------------------------------------------------------------------------------------------

  // What every panel says before the first read, when it failed, or when the database has no wake records.
  function preamble(root) {
    const data = state.document;
    if (!data) {
      root.append(empty(state.note || "Loading the wake-ups…"));
      return null;
    }
    if (state.note) root.append(el("p", "hq-warn-text", state.note));
    if (!data.available) {
      root.append(textEl("p", "hq-empty", data.status_text));
      return null;
    }
    return data;
  }

  // --- Next check-ins (Calendar tab) ------------------------------------------------------------------------------

  function checkInRow(item) {
    const row = el("li", "hq-next-item hq-wake-checkin");
    row.dataset.row = `checkin:${str(item.dept)}`;
    row.dataset.status = str(item.status);
    const when = el("span", "hq-next-when");
    const later = el("span", "hq-next-rel");
    const days = HQ.dayDiff(parseIso(item.at));
    if (days === 0) later.append(time(item.at, "relative"));
    else if (days === 1) later.append("tomorrow");
    else later.append(time(item.at, "date"));
    when.append(liveTime(item.at, "clock"), later);
    const what = el("span", "hq-next-what");
    const body = el("span", "hq-next-body");
    const head = el("span", "hq-next-text");
    head.append(el("strong", "", deptName(item.dept)), " · ", item.basis === "named" ? "its own time" : "next day start");
    const marked = CHECK_IN_CHIPS[item.status];
    if (marked) head.append(" ", chip(marked[0], marked[1]));
    if (HQ.count(item.waiting) > 0) head.append(" ", chip(`${item.waiting} waiting`, "hq-chip-soft"));
    body.append(head, textEl("span", "hq-next-detail", item.text));
    const words = quote(item.quote);
    if (words) body.append(words);
    what.append(deptIcon(item.dept), body);
    row.append(when, what);
    return row;
  }
  function stepRow(item) {
    const row = el("li", "hq-wake-step");
    row.dataset.status = str(item.status);
    const [glyph, word] = STEP_MARKS[item.status] || ["○", "not yet"];
    const mark = el("span", "hq-wake-step-mark", glyph);
    mark.setAttribute("aria-hidden", "true");
    const body = el("span", "hq-wake-step-body");
    body.append(el("strong", "", STEP_WORDS[item.step] || str(item.step)), el("span", "hq-sr", ` (${word})`), ": ");
    HQ.renderText(body, item.text);
    row.append(mark, body);
    return row;
  }
  HQ.view("wakeplan", {
    deps: () => [state.version],
    render(root) {
      const data = preamble(root);
      if (!data) return;
      const day = obj(data.day);
      const rows = el("ol", "hq-next-list hq-wake-checkins");
      for (const item of list(day.check_ins)) rows.append(checkInRow(item));
      root.append(rows);
      root.append(el("p", "hq-muted hq-wake-note", "Wake-ups happen only while the team is running. Nothing limits how often a department is woken."));
      const today = el("section", "hq-wake-block");
      const title = el("h3", "hq-subhead", "Today's routine");
      today.append(title);
      if (HQ.isIso(day.start) && HQ.isIso(day.end)) {
        const span = el("p", "hq-muted hq-wake-day");
        span.append("Trading day from ", liveTime(day.start, "day_clock"), " to ", liveTime(day.end, "day_clock"), ".");
        today.append(span);
      }
      const steps = el("ul", "hq-wake-steps");
      for (const item of list(day.steps)) steps.append(stepRow(item));
      today.append(steps);
      root.append(today);
    },
  });

  // --- Today's focus coins and alarms (Calendar tab) ----------------------------------------------------------------

  function focusBlock(focus) {
    const block = el("section", "hq-wake-block");
    block.append(el("h3", "hq-subhead", "Focus coins"), textEl("p", "hq-wake-lead", focus.text));
    const coins = list(focus.coins);
    if (coins.length) {
      const rows = el("ul", "hq-wake-coins");
      for (const coin of coins) {
        const row = el("li", "hq-wake-coin");
        const head = el("p", "hq-wake-coin-head");
        head.append(el("strong", "", str(coin.symbol)));
        if (HQ.count(coin.evidence) > 0) head.append(" ", chip(plural(coin.evidence, "source"), "hq-chip-soft"));
        row.append(head);
        const words = quote(coin.quote);
        if (words) row.append(words);
        rows.append(row);
      }
      block.append(rows);
    }
    const summary = quote(focus.quote, "Research's summary, in their words");
    if (summary) block.append(summary);
    const shortlist = list(focus.shortlist).filter(symbol => typeof symbol === "string");
    if (shortlist.length) block.append(el("p", "hq-muted", `The scanner's shortlist today: ${shortlist.join(", ")}.`));
    return block;
  }
  function alarmRow(item) {
    const row = el("li", "hq-wake-alarm");
    row.dataset.row = `alarm:${str(item.id)}`;
    const head = el("p", "hq-wake-alarm-head");
    head.append(el("strong", "", str(item.symbol)), " ");
    HQ.renderText(head, item.text);
    const who = el("p", "hq-muted hq-wake-alarm-who");
    who.append(deptIcon(item.dept), `${deptName(item.dept)}'s alarm`);
    if (HQ.isIso(item.armed_at)) who.append(", set ", time(item.armed_at, "day_clock"));
    if (item.in_day_plan) who.append(" ", chip("day plan", "hq-chip-soft"));
    row.append(head, who);
    const words = quote(item.quote, "Its note, in their words");
    if (words) row.append(words);
    return row;
  }
  HQ.view("watchlist", {
    deps: () => [state.version],
    render(root) {
      const data = preamble(root);
      if (!data) return;
      const day = obj(data.day);
      root.append(focusBlock(obj(day.focus)));
      const alarms = obj(day.alarms);
      const block = el("section", "hq-wake-block");
      block.append(el("h3", "hq-subhead", "Price alarms"), textEl("p", "hq-wake-lead", alarms.text));
      const items = list(alarms.items);
      if (items.length) {
        const rows = el("ul", "hq-wake-alarms");
        for (const item of items) rows.append(alarmRow(item));
        block.append(rows);
      }
      const hidden = (HQ.count(alarms.total) || 0) - items.length;
      if (hidden > 0) block.append(el("p", "hq-muted", `${plural(hidden, "more alarm")} set, not listed here.`));
      root.append(block);
    },
  });

  // --- Wake log (Story tab) -----------------------------------------------------------------------------------------

  const FILTERS = [["all", "All"], ...DEPARTMENTS.map(id => [id, DEPT_WORDS[id]])];
  function logItems() {
    const merged = new Map();
    for (const item of list(state.document?.items)) if (item?.id) merged.set(item.id, item);
    for (const [id, item] of state.older) if (!merged.has(id)) merged.set(id, item);
    return [...merged.values()].filter(item => parseIso(item.at) !== null && Number.isSafeInteger(item.cursor))
      .sort((a, b) => b.cursor - a.cursor);
  }
  function wakeRow(item) {
    const row = el("li", "hq-story-item hq-wake-item");
    row.dataset.tone = str(item.tone);
    row.dataset.row = `wake:${str(item.id)}`;
    if (item.tone === "attention") row.classList.add("is-problem");
    const when = el("span", "hq-story-when");
    when.append(liveTime(item.at, "clock"));
    const main = el("div", "hq-story-main");
    const who = el("p", "hq-story-who");
    who.append(deptIcon(item.dept), el("span", "", deptName(item.dept)), " ",
      chip(STATUS_WORDS[item.status] || str(item.status), item.tone === "attention" ? "hq-chip-warn" : "hq-chip-soft"));
    main.append(who, textEl("p", "hq-story-headline", item.headline));
    main.append(textEl("p", "hq-story-consequence", item.result));
    main.append(textEl("p", "hq-wake-allowance", item.allowance?.text));
    const more = HQ.keepOpen(el("details", "hq-story-more"), `wake-more:${str(item.id)}`);
    more.append(el("summary", "", "More"));
    const events = el("ul", "hq-plain-list hq-wake-events");
    for (const line of list(item.events)) events.append(textEl("li", "", line));
    if (HQ.count(item.more_events) > 0) events.append(el("li", "hq-muted", `And ${plural(item.more_events, "more event")}.`));
    if (events.childNodes.length) more.append(events);
    for (const note of list(item.notes)) {
      const words = quote(note, "A note it was handed, in their words");
      if (words) more.append(words);
    }
    const summary = quote(item.quote);
    if (summary) more.append(summary);
    const record = HQ.safePath(item.link);
    if (record) more.append(HQ.link(record, "Open the task records ›", "hq-more-link"));
    if (HQ.technical() && item.technical) more.append(el("p", "hq-code", `[${item.technical}]`));
    main.append(more);
    row.append(when, main);
    return row;
  }
  function dayLabel(ms) {
    const diff = HQ.dayDiff(ms);
    if (diff === 0) return "Today";
    if (diff === -1) return "Yesterday";
    return HQ.fmt.dateWords(ms);
  }
  function countsLine(counts) {
    const box = el("div", "hq-wake-counts");
    box.append(textEl("p", "hq-wake-lead", counts.text));
    const today = list(counts.departments).filter(item => HQ.count(item.today) > 0);
    if (today.length) {
      box.append(el("p", "hq-muted", `Today by department: ${today.map(item => `${deptName(item.dept)} ${item.today}`).join(" · ")}.`));
    }
    return box;
  }
  HQ.view("wakes", {
    deps: () => [state.version, state.filter],
    render(root) {
      const data = preamble(root);
      if (!data) return;
      if (data.day?.counts) root.append(countsLine(data.day.counts));
      if (hasText(data.status_text)) root.append(textEl("p", "hq-empty", data.status_text));
      const all = logItems();
      if (!all.length) return;
      const filters = el("div", "hq-filters");
      filters.setAttribute("role", "group");
      filters.setAttribute("aria-label", "Show the wake-ups of");
      for (const [key, label] of FILTERS) {
        const control = button("hq-filter", label);
        control.dataset.focusKey = `wake-filter:${key}`;
        control.setAttribute("aria-pressed", String(state.filter === key));
        control.addEventListener("click", () => { state.filter = key; HQ.rerender("wakes"); });
        filters.append(control);
      }
      root.append(filters);
      const shown = all.filter(item => state.filter === "all" || item.dept === state.filter);
      const byDay = new Map();
      for (const item of shown) {
        const key = HQ.localDayKey(parseIso(item.at));
        if (!byDay.has(key)) byDay.set(key, []);
        byDay.get(key).push(item);
      }
      const timeline = el("div", "hq-story-days hq-wake-days");
      // A day may continue on a page not loaded yet, so its count says what is shown.
      const partial = !state.olderDone && Number.isSafeInteger(state.olderBefore || data.next_before);
      let first = true;
      for (const [key, items] of byDay) {
        const label = dayLabel(parseIso(items[0].at));
        const rows = el("ul", "hq-story-list");
        for (const item of items) rows.append(wakeRow(item));
        const count = `${plural(items.length, "wake-up")}${partial ? " shown" : ""}`;
        if (first) {
          const block = el("section", "hq-story-day is-today");
          block.append(el("h3", "hq-story-day-title", label), el("p", "hq-story-summary", count), rows);
          timeline.append(block);
          first = false;
          continue;
        }
        const block = HQ.keepOpen(el("details", "hq-story-day"), `wake-day:${key}`);
        block.dataset.row = `wake-day:${key}`;
        const summary = el("summary", "");
        summary.append(el("span", "hq-story-day-title", label), el("span", "hq-story-summary", count));
        block.append(summary, rows);
        timeline.append(block);
      }
      if (!shown.length) timeline.append(empty("No wake-up of this department in what's loaded."));
      root.append(timeline);
      const footer = el("div", "hq-story-footer");
      if (state.olderNote) footer.append(el("p", "hq-warn-text", state.olderNote));
      const next = state.olderBefore || data.next_before;
      if (state.olderDone || !Number.isSafeInteger(next)) footer.append(el("p", "hq-muted", "That's every saved wake-up."));
      else {
        const more = button("button button-secondary", state.olderBusy ? "Loading…" : "Load earlier");
        more.dataset.focusKey = "wakes-earlier";
        more.disabled = state.olderBusy;
        more.addEventListener("click", loadEarlier);
        footer.append(more);
      }
      root.append(footer);
    },
  });

  // --- Start ------------------------------------------------------------------------------------------------------

  function init() {
    if (state.initialised) return;
    state.initialised = true;
    const later = () => setTimeout(wake, 0);
    if (typeof globalThis.IntersectionObserver === "function") {
      const observer = new globalThis.IntersectionObserver(seen => {
        if (list(seen).some(item => item.isIntersecting)) { state.seen = true; wake(); }
      }, {rootMargin: "200px"});
      for (const name of PANELS) {
        const node = container(name);
        if (node) observer.observe(node.closest(".hq-panel") || node);
      }
    }
    HQ.on("snapshot", wake);
    doc.addEventListener("visibilitychange", later);
    globalThis.addEventListener?.("hashchange", later);
    doc.addEventListener("click", event => {
      if (event.target instanceof Element && event.target.closest("a[href='#calendar'], a[href='#story']")) later();
    });
    globalThis.matchMedia?.("(max-width: 767px)")?.addEventListener?.("change", later);
    wake();
  }

  HQ.define("wakes", {init, wake, load, loadEarlier, state: () => state});
})();
