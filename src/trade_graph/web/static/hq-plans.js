/* Trade Graph HQ day plans (W8-C): lays out GET /api/v1/hq/plans in the "Today's day plans" panel of the Money tab:
   each coin's plan with its waiting buys, fills and safety exits, the plan's events, and today's refused plans. The
   server reads every record and writes every sentence; this file only places them. It reads the plans only while
   the panel is on screen, and reading starts nothing. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const doc = document;
  const {el, list, obj, str, textEl, hasText} = HQ;

  const SCHEMA = "trade-graph.hq.plans.v1";
  // The page asks again on HQ's own 30-second poll while the panel is on screen, at most this often.
  const REFRESH_MS = 25000;
  const PANEL = "plans";
  // A glyph and a word for every order status, so a state never rests on colour alone.
  const MARKS = Object.freeze({
    sending: ["→", "being sent"], waiting: ["○", "waiting"], partly_filled: ["◐", "partly filled"],
    filled: ["●", "filled"], cancelled: ["⊘", "cancelled"], rejected: ["✕", "refused"],
    being_checked: ["?", "being checked"],
  });

  const state = {document: null, note: "", busy: false, started: false, loadedAt: 0, version: 0, initialised: false,
    seen: typeof globalThis.IntersectionObserver !== "function"};

  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const time = (at, style) => textEl("span", "hq-time-inline", [{t: "time", at, style}], {inert: true});
  const chip = (text, className = "") => el("span", `hq-chip ${className}`.trim(), text);
  function container() { return doc.querySelector(`[data-hq="${PANEL}"]`); }
  function quote(item, label) {
    if (!item || typeof item.text !== "string" || !item.text) return null;
    const box = el("details", "hq-quote");
    box.append(el("summary", "", label), el("blockquote", "", item.text));
    return box;
  }
  function bump() {
    state.version += 1;
    HQ.rerender(PANEL);
  }

  // --- When to read -----------------------------------------------------------------------------------------------

  const tabMode = () => Boolean(globalThis.matchMedia?.("(max-width: 767px)")?.matches);
  function shown() {
    if (doc.hidden || !state.seen) return false;
    const node = container();
    if (!node) return false;
    const panel = node.closest(".hq-panel") || node;
    if (panel.hidden) return false;
    const section = node.closest("[data-hq-section]");
    return !(tabMode() && section && !section.classList.contains("is-active"));
  }
  async function load() {
    if (state.busy) return;
    state.busy = true;
    try {
      const result = await HQ.api.get("plans", {timeout: 15000});
      state.loadedAt = Date.now();
      const data = result.body;
      if (!result.ok || !data || data.schema !== SCHEMA) {
        state.note = state.document ? "The day plans couldn't be refreshed just now; this is what HQ saw last."
          : "The day plans couldn't be loaded just now. HQ will try again shortly.";
        return;
      }
      state.document = data;
      state.note = "";
    } catch (error) {
      console.error(error);
      state.note = "The day plans couldn't be shown just now. HQ will try again shortly.";
    } finally {
      state.busy = false;
      bump();
    }
  }
  function wake() {
    if (!shown() || HQ.connection() === "signed_out") return;
    if (!state.started || Date.now() - state.loadedAt >= REFRESH_MS) {
      state.started = true;
      load();
    }
  }

  // --- Layout -----------------------------------------------------------------------------------------------------

  function marked(item, className) {
    const row = el("li", className);
    row.dataset.status = str(item.status);
    row.dataset.tone = str(item.tone);
    if (item.tone === "attention") row.classList.add("is-attention");
    const [glyph, word] = MARKS[item.status] || ["?", "being checked"];
    const mark = el("span", "hq-plan-mark", glyph);
    mark.setAttribute("aria-hidden", "true");
    const body = el("div", "hq-plan-body");
    const text = textEl("p", "hq-plan-text", item.text);
    text.prepend(el("span", "hq-sr", `(${word}) `));
    body.append(text);
    row.append(mark, body);
    return {row, body};
  }
  function lines(items, more, what, className) {
    const box = el("ul", `hq-plain-list ${className}`);
    for (const line of list(items)) box.append(textEl("li", "", line));
    if ((HQ.count(more) || 0) > 0) box.append(el("li", "hq-muted", `And ${plural(more, what)}.`));
    return box.childNodes.length ? box : null;
  }
  function exitRow(item) {
    const {row, body} = marked(item, "hq-plan-exit");
    const fills = lines(item.fills, item.more_fills, "more sale", "hq-plan-fills");
    if (fills) body.append(fills);
    return row;
  }
  function entryRow(item) {
    const {row, body} = marked(item, "hq-plan-entry");
    row.dataset.row = `entry:${str(item.id)}`;
    const fills = lines(item.fills, item.more_fills, "more fill", "hq-plan-fills");
    if (fills) body.append(fills);
    const exits = list(item.exits);
    if (exits.length) {
      const rows = el("ul", "hq-plan-exits");
      for (const exit of exits) rows.append(exitRow(exit));
      body.append(rows);
    }
    if ((HQ.count(item.more_exits) || 0) > 0) body.append(el("p", "hq-muted", `And ${plural(item.more_exits, "more safety exit")}.`));
    return row;
  }
  function planBlock(item) {
    const block = el("section", "hq-plan");
    block.dataset.row = `plan:${str(item.id)}`;
    if (!item.current) block.classList.add("is-earlier");
    const head = el("h3", "hq-plan-head");
    head.append(el("strong", "", str(item.symbol)), " ", chip(item.current ? "Today" : "Earlier day", "hq-chip-soft"));
    if ((HQ.count(item.replaced_today) || 0) > 0) head.append(" ", chip(`replaced ${plural(item.replaced_today, "time")}`, "hq-chip-soft"));
    block.append(head, textEl("p", "hq-plan-headline", item.headline));
    const about = el("p", "hq-muted hq-plan-about");
    HQ.renderText(about, item.strategy);
    if (hasText(item.watching)) { about.append(" "); HQ.renderText(about, item.watching); }
    block.append(about);
    const entries = list(item.entries);
    if (entries.length) {
      const rows = el("ul", "hq-plan-entries");
      for (const entry of entries) rows.append(entryRow(entry));
      block.append(rows);
    }
    const why = quote(item.quote, "The Trader's reasons, in their words");
    if (why) block.append(why);
    const wrong = quote(item.invalidation, "What would prove it wrong, in their words");
    if (wrong) block.append(wrong);
    const events = lines(item.events, item.more_events, "more event", "hq-plan-events");
    if (events) {
      const more = HQ.keepOpen(el("details", "hq-plan-history"), `plan-events:${str(item.id)}`);
      more.append(el("summary", "", "What happened"), events);
      block.append(more);
    }
    if (HQ.technical()) block.append(el("p", "hq-code", `[${str(item.id)}]`));
    return block;
  }
  function refusalsBlock(data) {
    const items = list(data.refusals);
    if (!items.length) return null;
    const block = el("section", "hq-plan-refusals");
    block.append(el("h3", "hq-subhead", "Refused today"));
    const rows = el("ul", "hq-plain-list");
    for (const item of items) {
      const row = el("li", "hq-plan-refusal");
      row.append(time(item.at, "clock"), " ");
      HQ.renderText(row, item.text);
      if (HQ.technical() && item.technical) row.append(el("span", "hq-code", ` [${str(item.technical)}]`));
      rows.append(row);
    }
    const hidden = (HQ.count(data.refusals_total) || 0) - items.length;
    if (hidden > 0) rows.append(el("li", "hq-muted", `And ${plural(hidden, "earlier refusal")} today.`));
    block.append(rows);
    return block;
  }
  HQ.view(PANEL, {
    deps: () => [state.version],
    render(root) {
      const data = state.document;
      if (!data) {
        root.append(el("p", "hq-empty", state.note || "Loading the day plans…"));
        return;
      }
      if (state.note) root.append(el("p", "hq-warn-text", state.note));
      if (!data.available) {
        root.append(textEl("p", "hq-empty", data.status_text || data.headline));
        return;
      }
      root.append(textEl("p", "hq-plan-lead", data.headline));
      for (const item of list(data.plans)) if (item && typeof item === "object") root.append(planBlock(obj(item)));
      if ((HQ.count(data.more_plans) || 0) > 0) {
        root.append(el("p", "hq-muted", `${plural(data.more_plans, "more plan")} not shown here.`));
      }
      const refusals = refusalsBlock(data);
      if (refusals) root.append(refusals);
      root.append(HQ.link("/trading", "Every order in the Trading records ›", "hq-more-link"));
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
      const node = container();
      if (node) observer.observe(node.closest(".hq-panel") || node);
    }
    HQ.on("snapshot", wake);
    doc.addEventListener("visibilitychange", later);
    globalThis.addEventListener?.("hashchange", later);
    doc.addEventListener("click", event => {
      if (event.target instanceof Element && event.target.closest("a[href='#money']")) later();
    });
    globalThis.matchMedia?.("(max-width: 767px)")?.addEventListener?.("change", later);
    wake();
  }

  HQ.define("plans", {init, wake, load, state: () => state});
})();
