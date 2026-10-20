/* Trade Graph HQ Trading desk room (W13-A): lays out GET /api/v1/hq/desk in the "Trading desk room" panel of the HQ
   tab: the desk's playbook per market type, today's stance per coin with its market type, the strategy lab, the
   Leader news the desk read and what each strategy earned in each market type. The server reads every record and
   writes every sentence; this file only places them. It reads the room only while the panel is on screen, and
   reading starts nothing. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const doc = document;
  const {el, list, obj, str, textEl, hasText} = HQ;

  const SCHEMA = "trade-graph.hq.desk.v1";
  // The page asks again on HQ's own 30-second poll while the panel is on screen, at most this often.
  const REFRESH_MS = 25000;
  const PANEL = "desk";
  // A glyph and a word for every stance and result, so nothing rests on colour alone.
  const STANCE_MARKS = Object.freeze({
    wait: ["○", "wait"], buy_dips: ["↘", "buy dips"], follow_trend: ["↗", "follow the trend"], other: ["◇", "other"],
  });
  const TONE_MARKS = Object.freeze({good: ["▲", "earned"], neutral: ["■", "even"], attention: ["▼", "lost"]});
  const STATUS_MARKS = Object.freeze({done: ["✓", "done"], refused: ["⊘", "refused"], failed: ["✕", "failed"]});
  const LABEL_WORDS = Object.freeze({current: "label current", stale: "label too old", missing: "no label"});

  const state = {document: null, note: "", busy: false, started: false, loadedAt: 0, version: 0, initialised: false,
    seen: typeof globalThis.IntersectionObserver !== "function"};

  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const chip = (text, className = "") => el("span", `hq-chip ${className}`.trim(), text);
  function container() { return doc.querySelector(`[data-hq="${PANEL}"]`); }
  function quote(item, label) {
    if (!item || typeof item.text !== "string" || !item.text) return null;
    const box = el("details", "hq-quote");
    box.append(el("summary", "", label), el("blockquote", "", item.text));
    return box;
  }
  function inline(item) {
    if (!item || typeof item.text !== "string" || !item.text) return null;
    const box = el("blockquote", "hq-desk-quote", item.text);
    box.setAttribute("aria-label", "In their words");
    return box;
  }
  function mark(pair, className) {
    const [glyph, word] = pair;
    const node = el("span", className);
    const shape = el("span", "hq-desk-glyph", glyph);
    shape.setAttribute("aria-hidden", "true");
    node.append(shape, el("span", "hq-sr", `(${word}) `));
    return node;
  }
  function more(count, words) {
    const n = HQ.count(count) || 0;
    return n > 0 ? el("p", "hq-muted hq-desk-more", `And ${plural(n, words[0], words[1])} not shown here.`) : null;
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
      const result = await HQ.api.get("desk", {timeout: 15000});
      state.loadedAt = Date.now();
      const data = result.body;
      if (!result.ok || !data || data.schema !== SCHEMA) {
        state.note = state.document ? "The Trading desk room couldn't be refreshed just now; this is what HQ saw last."
          : "The Trading desk room couldn't be loaded just now. HQ will try again shortly.";
        return;
      }
      state.document = data;
      state.note = "";
    } catch (error) {
      console.error(error);
      state.note = "The Trading desk room couldn't be shown just now. HQ will try again shortly.";
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

  function part(id, title, section) {
    const block = el("section", "hq-desk-part");
    block.dataset.part = id;
    block.dataset.state = str(section?.state);
    const heading = el("h3", "hq-subhead", title);
    block.append(heading);
    const lead = textEl("p", section?.state === "shown" ? "hq-desk-lead" : "hq-empty", section?.headline);
    if (section?.state === "unreadable") lead.classList.add("hq-warn-text");
    block.append(lead);
    return block;
  }
  function playbookPart(section) {
    const block = part("playbook", "Playbook", section);
    if (section.state !== "shown") return block;
    const rows = el("ul", "hq-desk-list hq-desk-choices");
    for (const item of list(section.choices)) {
      const choice = obj(item);
      const row = el("li", "hq-desk-row");
      row.dataset.marketType = str(choice.market_type);
      row.dataset.stance = str(choice.stance) || "none";
      row.append(mark(STANCE_MARKS[choice.stance] || ["–", "no choice"], "hq-desk-mark"));
      const body = el("div", "hq-desk-body");
      body.append(textEl("p", "hq-desk-text", choice.text));
      const why = inline(choice.reason);
      if (why) body.append(why);
      if ((HQ.count(choice.reports) || 0) > 0) body.append(el("p", "hq-muted hq-desk-small", `Rests on ${plural(choice.reports, "report")}.`));
      row.append(body);
      rows.append(row);
    }
    block.append(rows);
    const why = quote(section.why, "Why the desk changed it, in their words");
    if (why) block.append(why);
    if (hasText(section.previous)) block.append(textEl("p", "hq-muted hq-desk-small", section.previous));
    return block;
  }
  function stancesPart(section) {
    const block = part("stances", "Today's choice per coin", section);
    const items = list(section.items);
    if (!items.length) return block;
    const rows = el("ul", "hq-desk-list hq-desk-coins");
    for (const value of items) {
      const item = obj(value);
      const row = el("li", "hq-desk-coin");
      row.dataset.row = `stance:${str(item.symbol)}`;
      row.dataset.stance = str(item.stance);
      const head = el("p", "hq-desk-coin-head");
      head.append(mark(STANCE_MARKS[item.stance] || ["◇", "other"], "hq-desk-mark"), el("strong", "", str(item.symbol)), " ");
      HQ.renderText(head, item.text);
      row.append(head);
      const types = el("ul", "hq-plain-list hq-desk-types");
      for (const reading of [obj(item.shown), obj(item.now)]) {
        const line = textEl("li", "", reading.text);
        line.dataset.label = str(reading.status);
        line.prepend(el("span", "hq-sr", `(${LABEL_WORDS[reading.status] || "label"}) `));
        types.append(line);
      }
      row.append(types);
      const why = inline(item.reason);
      if (why) row.append(why);
      if ((HQ.count(item.news) || 0) > 0) row.append(el("p", "hq-muted hq-desk-small", `It weighed ${plural(item.news, "news item")}.`));
      rows.append(row);
    }
    block.append(rows);
    const tail = more(section.more_stances, ["more coin", "more coins"]);
    if (tail) block.append(tail);
    return block;
  }
  function reportRow(value) {
    const report = obj(value);
    const row = el("li", "hq-desk-report");
    row.dataset.tone = str(report.tone);
    row.append(mark(TONE_MARKS[report.tone] || TONE_MARKS.neutral, "hq-desk-mark"));
    row.append(textEl("span", "hq-desk-text", report.text));
    if (HQ.technical()) row.append(el("span", "hq-code", ` [${str(report.run_id)}]`));
    return row;
  }
  function labPart(section) {
    const block = part("lab", "Strategy lab", section);
    if (section.state !== "shown") return block;
    const upcoming = list(section.next);
    if (upcoming.length) {
      const rows = el("ul", "hq-plain-list hq-desk-next");
      for (const line of upcoming) rows.append(textEl("li", "", line));
      block.append(rows);
    }
    const results = list(section.results);
    if (results.length) {
      const rows = el("ul", "hq-desk-list hq-desk-results");
      for (const value of results) {
        const item = obj(value);
        const row = el("li", "hq-desk-row");
        row.dataset.row = `lab:${str(item.request_id)}`;
        row.dataset.status = str(item.status);
        row.append(mark(STATUS_MARKS[item.status] || STATUS_MARKS.failed, "hq-desk-mark"));
        const body = el("div", "hq-desk-body");
        const text = textEl("p", "hq-desk-text", item.text);
        if (item.at) text.append(" ", textEl("span", "hq-time-inline", [{t: "time", at: item.at, style: "relative_clock"}], {inert: true}));
        body.append(text);
        if (hasText(item.reason)) body.append(textEl("p", "hq-muted hq-desk-small", item.reason));
        const reports = list(item.reports);
        if (reports.length) {
          const lines = el("ul", "hq-desk-reports");
          for (const report of reports) lines.append(reportRow(report));
          body.append(lines);
        }
        const tail = more(item.more_reports, ["more report", "more reports"]);
        if (tail) body.append(tail);
        row.append(body);
        rows.append(row);
      }
      block.append(rows);
    }
    const tail = more(section.more_results, ["earlier result", "earlier results"]);
    if (tail) block.append(tail);
    const families = list(section.families);
    if (families.length) {
      const box = HQ.keepOpen(el("details", "hq-desk-families"), "desk-families");
      box.append(el("summary", "", `Tries per strategy family (${families.length + (HQ.count(section.more_families) || 0)})`));
      const rows = el("ul", "hq-plain-list");
      for (const value of families) rows.append(textEl("li", "", obj(value).text));
      box.append(rows);
      const rest = more(section.more_families, ["more family", "more families"]);
      if (rest) box.append(rest);
      block.append(box);
    }
    return block;
  }
  function newsPart(section) {
    const block = part("news", "News the desk read", section);
    const round = section.round ? obj(section.round) : null;
    if (round) {
      const summary = inline(round.summary);
      if (summary) block.append(summary);
      const headlines = list(round.headlines);
      if (headlines.length) {
        const rows = el("ul", "hq-plain-list hq-desk-headlines");
        for (const value of headlines) {
          const item = obj(value);
          const row = el("li", "");
          row.append(el("span", "", str(item.text?.text)));
          if (item.source) row.append(el("span", "hq-muted", ` · ${str(item.source)}`));
          rows.append(row);
        }
        block.append(rows);
      }
      const rest = more(round.more_headlines, ["more headline", "more headlines"]);
      if (rest) block.append(rest);
      const note = quote(round.desk_note, "The Leader's note to the desk, in their words");
      if (note) block.append(note);
      for (const idea of list(round.ideas)) {
        const box = quote(idea, "A strategy idea for the desk, in their words");
        if (box) block.append(box);
      }
    }
    const inbox = list(section.inbox);
    if (inbox.length) {
      const box = HQ.keepOpen(el("details", "hq-desk-inbox"), "desk-inbox");
      box.append(el("summary", "", `Ideas in the lab inbox (${inbox.length + (HQ.count(section.more_inbox) || 0)})`));
      const rows = el("ul", "hq-plain-list");
      for (const value of inbox) {
        const item = obj(value);
        const row = el("li", "");
        const idea = inline(item.idea);
        if (idea) row.append(idea);
        row.append(textEl("p", "hq-muted hq-desk-small", item.text));
        rows.append(row);
      }
      box.append(rows);
      block.append(box);
    }
    if (hasText(section.jev)) block.append(textEl("p", "hq-muted hq-desk-jev", section.jev));
    const verdicts = list(section.verdicts);
    if (verdicts.length) {
      const rows = el("ul", "hq-plain-list hq-desk-verdicts");
      for (const value of verdicts) {
        const item = obj(value);
        const row = el("li", "");
        row.append(el("span", "", str(item.title?.text)), " ");
        HQ.renderText(row, item.text);
        rows.append(row);
      }
      block.append(rows);
    }
    const rest = more(section.more_verdicts, ["more verdict", "more verdicts"]);
    if (rest) block.append(rest);
    return block;
  }
  function splitRows(rows, className) {
    const box = el("ul", `hq-desk-list ${className}`);
    for (const value of list(rows)) {
      const row = obj(value);
      const line = el("li", "hq-desk-row");
      line.dataset.row = `split:${str(row.key)}`;
      line.dataset.tone = str(row.tone);
      line.append(mark(TONE_MARKS[row.tone] || TONE_MARKS.neutral, "hq-desk-mark"));
      line.append(textEl("p", "hq-desk-text", row.text));
      box.append(line);
    }
    return box;
  }
  function splitPart(section) {
    const block = part("split", "What each strategy earned", section);
    if (section.state !== "shown") {
      if (hasText(section.note) && section.state === "empty") block.append(textEl("p", "hq-muted hq-desk-small", section.note));
      return block;
    }
    block.append(splitRows(section.by_choice, "hq-desk-choices-earned"));
    const rest = more(section.more_choices, ["more combination", "more combinations"]);
    if (rest) block.append(rest);
    for (const [key, title, rows, extra] of [
      ["strategy", "By strategy version", section.by_strategy, section.more_strategies],
      ["market", "By market type at entry", section.by_market_type, 0],
      ["stance", "By the desk's stance", section.by_stance, 0],
    ]) {
      if (!list(rows).length) continue;
      const box = HQ.keepOpen(el("details", "hq-desk-split"), `desk-split-${key}`);
      box.append(el("summary", "", title), splitRows(rows, ""));
      const tail = more(extra, ["more version", "more versions"]);
      if (tail) box.append(tail);
      block.append(box);
    }
    block.append(textEl("p", "hq-desk-note", section.note));
    return block;
  }
  HQ.view(PANEL, {
    deps: () => [state.version],
    render(root) {
      const data = state.document;
      if (!data) {
        root.append(el("p", "hq-empty", state.note || "Loading the Trading desk room…"));
        return;
      }
      if (state.note) root.append(el("p", "hq-warn-text", state.note));
      root.append(textEl("p", "hq-plan-lead hq-desk-headline", data.headline));
      const grid = el("div", "hq-desk-grid");
      grid.append(playbookPart(obj(data.playbook)), stancesPart(obj(data.stances)), labPart(obj(data.lab)),
        newsPart(obj(data.news)), splitPart(obj(data.split)));
      root.append(grid);
      root.append(HQ.link("/trading", "Every order in the Trading records ›", "hq-more-link"));
    },
  });

  // --- Start ------------------------------------------------------------------------------------------------------

  function open() {
    const node = container();
    const panel = node?.closest(".hq-panel") || node;
    if (!panel) return;
    state.seen = true;
    panel.scrollIntoView?.({block: "start"});
    wake();
  }
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
      if (event.target instanceof Element && event.target.closest("a[href='#hq']")) later();
    });
    globalThis.matchMedia?.("(max-width: 767px)")?.addEventListener?.("change", later);
    wake();
  }

  HQ.define("desk", {init, wake, load, open, state: () => state});
})();
