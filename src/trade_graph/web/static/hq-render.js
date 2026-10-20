/* Trade Graph HQ read-only sections: right now, needs you, up next, map and rooms, calendar, story, money,
   allowance, market weather, notices, welcome, help and the phone tabs. Every fact comes from the snapshot. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const {el, button, icon, link, list, obj, str, renderText, textEl, plain, hasText, fmt, parseIso} = HQ;
  const doc = document;
  const glossary = () => HQ.use("glossary");
  const decide = () => HQ.use("decide");
  const checkins = () => HQ.use("checkins");
  const prompts = () => HQ.use("prompts");

  const SECTIONS = ["hq", "calendar", "story", "money", "decide"];
  const ROOMS = new Set(["trader", "research", "learning", "leader", "optimisation", "engineer", "secretary", "market", "accounts"]);
  const LIGHT_LABEL = Object.freeze({
    working: "Working", waiting: "Waiting", resting: "Resting", ok: "OK", paused: "Paused", on_call: "On call",
    stuck: "Stuck", attention: "Needs a look", off: "Off",
  });
  const NOW_LABEL = Object.freeze({
    no_run: "Not started", crashed: "Stopped unexpectedly", stopped: "Switched off", attached_unknown: "Unclear",
    silent: "No sign of life", stopping: "Shutting down", safety_stop: "Safety stop", owner_stop_setting: "Your Stop is on",
    owner_pause: "Paused by you", leader_pause: "Paused by the Leader", model_unavailable: "AI switched off",
    quota_paused: "AI resting", timetable_failing: "Timetable problem", routine_failing: "Running with a problem",
    orders_unclear: "Checking an order", waiting_allowance: "Waiting for allowance", working: "Working",
    starting_turn: "Starting a turn", probably_low_allowance: "Probably waiting", calm: "All quiet",
  });
  const SEVERITY = Object.freeze({
    act: {label: "For you to do", icon: "sev-act"},
    watch: {label: "Keep an eye on", icon: "sev-watch"},
    info: {label: "Good to know", icon: "sev-info"},
  });
  const CALENDAR_STATUS = Object.freeze({
    planned: ["●", "planned"], estimated: ["≈", "estimate"], waiting: ["◐", "waiting"], due: ["◆", "due now"],
    overdue: ["▲", "overdue"], done: ["✓", "done"], problem: ["▲", "had a problem"], skipped: ["↷", "skipped"],
    stuck: ["▲", "stuck"], missed: ["✕", "missed"], cancelled: ["⊘", "cancelled"],
  });
  const DETAIL_LABEL = Object.freeze({
    "/trading": "Trading records", "/organization": "Organisation records", "/changes": "Improvements",
    "/progress": "Mission Control", "/costs": "Costs", "/journal": "Account journal", "/owner": "Owner controls",
  });
  const PURPOSE = "This is a practice run. The goal right now is to check the team works safely, not to make money.";
  const LIVE_PURPOSE = "This page shows what the team is doing and what is coming up. Live account controls stay in Owner controls.";
  const WELCOME = Object.freeze([
    "Right now tells you what is happening this minute.",
    "Needs you lists your to-dos, and only those.",
    "Tap any room, time or underlined word for an explanation.",
    "Buttons always show what will happen before anything happens.",
  ]);

  // Open panels (Story days, "More", calendar "Earlier" and estimate series, help blocks) are remembered by
  // HQ.keepOpen, so a 30-second refresh never snaps them shut (review T3).
  const ui = {
    attentionExpanded: false,
    calendarFilters: {depts: true, you: true, deadlines: true, machine: false},
    calendarBusy: false,
    storyFilter: "all",
    older: new Map(),
    olderBefore: null,
    olderWindow: null,
    olderDone: false,
    olderBusy: false,
    olderNote: "",
  };
  // Turn tiles: an overdue turn says why it hasn't happened (contract departments[].next.status). An older
  // server without the field gets "Overdue", never "Due now" (review U1). A Map, so an unexpected value can
  // only ever fall back to "Overdue" (never an inherited object property).
  const OVERDUE_WORDS = new Map([["waiting", "Waiting"], ["due_now", "Due now"], ["overdue", "Overdue"]]);
  // Calendar items that are happening or holding things up now, whether or not their time has passed (C4).
  const CURRENT = new Set(["due", "overdue", "waiting"]);

  // --- Shared pieces ----------------------------------------------------------------------------

  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const time = (at, style) => textEl("span", "hq-time-inline", [{t: "time", at, style}], {inert: true});
  const liveTime = (at, style) => textEl("span", "hq-time-inline", [{t: "time", at, style}]);
  const findDecision = (snapshot, id) => list(snapshot.decisions).find(card => card && card.id === id) || null;
  function percentText(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    return `${Number.isInteger(value) ? value : value.toFixed(1)}%`;
  }
  function cadence(seconds) {
    switch (seconds) {
      case 3600: return "every hour";
      case 14400: return "every 4 hours";
      case 86400: return "daily";
      case 302400: return "every 3½ days";
      case 604800: return "weekly";
      default: return Number.isFinite(seconds) && seconds > 0 ? `every ${fmt.duration(seconds)}` : "";
    }
  }
  /* Contract calendar items[].count and .until (optional; older servers and single items leave them out):
     a run of deadlines of one kind arrives as one item, from `at` to `until` (at most 12 hours later), whose
     title already says how many (D111, D112). Returns the `until` worth showing as a range, or null when there
     is none or it reads the same. */
  function groupUntil(item) {
    if (!(HQ.count(item?.count) > 1)) return null;
    const from = parseIso(item.at);
    const to = parseIso(item.until);
    if (from === null || to === null || to < from || fmt.clock(to) === fmt.clock(from)) return null;
    return item.until;
  }
  // The end of a range: its clock time, and the day too when it isn't the start's day ("01:50 tomorrow").
  function untilWords(item, until) {
    const from = parseIso(item.at);
    const to = parseIso(until);
    const words = [time(until, "clock")];
    if (HQ.localDayKey(from) === HQ.localDayKey(to)) return words;
    const days = HQ.dayDiff(to);
    words.push(days === 0 ? " today" : days === 1 ? " tomorrow" : days === -1 ? " yesterday" : ` on ${fmt.weekday(to)}`);
    return words;
  }
  function lightBadge(light, text) {
    const badge = el("span", "hq-light");
    badge.dataset.light = str(light);
    badge.append(icon(`light-${LIGHT_LABEL[light] ? light : "off"}`, "hq-icon hq-light-icon"));
    if (hasText(text)) renderText(badge, text, {inert: true});
    else badge.append(el("span", "", LIGHT_LABEL[light] || "Unknown"));
    return badge;
  }
  function deptName(id, snapshot) {
    if (id === "you") return "You";
    if (id === "hq") return "HQ";
    return list(snapshot?.departments).find(dept => dept.id === id)?.name || str(id);
  }
  function deptIcon(id) {
    if (ROOMS.has(id)) return icon(id, "hq-icon hq-dept-icon");
    return icon(id === "you" ? "you" : "hq", "hq-icon hq-dept-icon");
  }
  function chip(text, className = "") {
    return el("span", `hq-chip ${className}`.trim(), text);
  }
  function empty(text) { return el("p", "hq-empty", text); }
  function quote(item) {
    if (!item || typeof item.text !== "string" || !item.text) return null;
    const box = el("details", "hq-quote");
    box.append(el("summary", "", str(item.label) || "In their words"), el("blockquote", "", item.text));
    return box;
  }
  function detailLink(href, label) {
    const path = HQ.safePath(href);
    if (!path) return null;
    const anchor = link(path, `${label || DETAIL_LABEL[path] || "Open the record"} ›`, "hq-more-link");
    return anchor;
  }
  function askButton(focusText, at, key) {
    const control = button("button button-secondary hq-ask", "Ask about this");
    if (key) control.dataset.focusKey = key;
    control.addEventListener("click", () => prompts()?.copyReview({focus: {text: focusText, at}}, control));
    return control;
  }
  function storyItems(snapshot) {
    const merged = new Map();
    for (const item of list(snapshot.story?.items)) if (item?.id) merged.set(item.id, item);
    for (const [id, item] of ui.older) if (!merged.has(id)) merged.set(id, item);
    return [...merged.values()].filter(item => parseIso(item.at) !== null)
      .sort((a, b) => parseIso(b.at) - parseIso(a.at));
  }

  // --- Welcome and notices --------------------------------------------------------------------------

  HQ.view("welcome", {
    deps: snapshot => [snapshot.mode],
    render(root, snapshot) {
      if (HQ.store.get(HQ.KEYS.welcome) === "1") return;
      const card = el("section", "hq-welcome");
      card.setAttribute("aria-labelledby", "hq-welcome-title");
      const heading = el("h2", "hq-welcome-title", "Welcome to your HQ");
      heading.id = "hq-welcome-title";
      card.append(heading, el("p", "hq-welcome-purpose", snapshot.mode === "live" ? LIVE_PURPOSE : PURPOSE));
      const bullets = el("ul", "hq-welcome-list");
      for (const line of WELCOME) bullets.append(el("li", "", line));
      const done = button("button", "Got it");
      done.dataset.focusKey = "welcome-done";
      done.addEventListener("click", () => {
        HQ.store.set(HQ.KEYS.welcome, "1");
        HQ.rerender("welcome");
        const next = doc.getElementById("hq-h-now");
        if (next) { next.tabIndex = -1; next.focus(); }
      });
      card.append(bullets, done);
      root.append(card);
    },
  });

  HQ.view("notices", {
    deps: snapshot => snapshot.notices,
    render(root, snapshot) {
      // Only calm notes: on a phone they follow the map so Right now comes first (hq.css .is-calm).
      const calm = list(snapshot.notices).every(notice => notice?.tone !== "attention");
      doc.querySelector('[data-hq="notices"]')?.classList.toggle("is-calm", calm);
      for (const notice of list(snapshot.notices)) {
        const card = el("div", "hq-notice");
        card.dataset.tone = notice.tone === "attention" ? "attention" : "info";
        card.append(icon(notice.tone === "attention" ? "sev-watch" : "sev-info", "hq-icon hq-notice-icon"));
        const words = el("div", "hq-notice-words");
        words.append(textEl("p", "hq-notice-title", notice.title), textEl("p", "hq-notice-text", notice.text));
        const more = detailLink(notice.link);
        if (more) words.append(more);
        card.append(words);
        root.append(card);
      }
    },
  });

  // --- Right now ------------------------------------------------------------------------------------

  /* "Since you last looked" counts only what the snapshot's story really covers (review C5):
     - the last look is older than the story window (72 h, or later when a busy reader was capped): no counts;
     - the page was cut (next_before differs from window_start) and the last look is older than the oldest
       item shown: the counts are a floor, "at least". */
  function sinceFacts(snapshot) {
    const lastMs = parseIso(HQ.lastSeen());
    // Nothing to report when the last look was the same few minutes (or the very first visit).
    if (lastMs === null || (parseIso(snapshot.as_of) ?? 0) - lastMs < 5 * 60000) return null;
    const story = obj(snapshot.story);
    const windowStart = parseIso(story.window_start);
    if (windowStart !== null && lastMs < windowStart) return {beyond: true};
    const items = list(story.items).filter(item => parseIso(item?.at) !== null);
    const fresh = items.filter(item => parseIso(item.at) > lastMs);
    const truncated = HQ.isIso(story.next_before) && story.next_before !== story.window_start;
    const oldest = items.length ? Math.min(...items.map(item => parseIso(item.at))) : null;
    return {
      beyond: false,
      atLeast: truncated && (oldest === null || lastMs < oldest),
      decisions: fresh.filter(item => item.kind === "trader_decision").length,
      trades: fresh.filter(item => item.kind === "fill").length,
      problems: fresh.filter(item => item.problem === true).length,
    };
  }
  function sinceLastLooked(facts) {
    if (!facts) return null;
    const line = el("p", "hq-now-since");
    line.append(el("strong", "", "Since you last looked"), " (", time(HQ.lastSeen(), "relative"), ")");
    if (facts.beyond) {
      line.append(": more happened than HQ can count here; see the ");
      const anchor = el("a", "", "Story tab");
      anchor.href = "#story";
      line.append(anchor, ".");
      return line;
    }
    const counts = `${plural(facts.decisions, "decision")}, ${plural(facts.trades, "trade")}, ${plural(facts.problems, "problem")}`;
    line.append(facts.atLeast ? `: at least ${counts} (the Story tab has more).` : `: ${counts}.`);
    return line;
  }

  HQ.view("now", {
    deps: snapshot => [snapshot.now, snapshot.viewer, findDecision(snapshot, snapshot.now?.primary_decision), sinceFacts(snapshot)],
    render(root, snapshot) {
      const now = obj(snapshot.now);
      const card = doc.querySelector("[data-hq-now-card]");
      if (card) {
        card.dataset.tone = str(now.tone);
        card.dataset.code = str(now.code);
      }
      const label = doc.querySelector("[data-hq-now-chip]");
      if (label) label.replaceChildren(lightBadge(now.light, [{t: "text", v: NOW_LABEL[now.code] || "Status"}]));
      const headline = doc.querySelector("[data-hq-now-headline]");
      if (headline) {
        const signature = JSON.stringify([now.headline, HQ.technical()]);
        if (headline.dataset.signature !== signature) {
          headline.dataset.signature = signature;
          headline.replaceChildren();
          renderText(headline, now.headline);
        }
      }
      if (hasText(now.detail)) root.append(textEl("p", "hq-now-detail", now.detail));
      if (hasText(now.if_nothing)) root.append(textEl("p", "hq-now-if", now.if_nothing));
      const since = sinceLastLooked(sinceFacts(snapshot));
      if (since) root.append(since);
      const primary = findDecision(snapshot, now.primary_decision);
      if (primary?.shown && snapshot.viewer?.can_act && decide()) {
        const actions = el("div", "hq-actions");
        actions.append(decide().button(primary, {primary: true, focusKey: "now-primary"}));
        root.append(actions);
      }
    },
  });

  // --- Needs you ------------------------------------------------------------------------------------

  function promptButton(label, run, key) {
    const control = button("button button-secondary", label);
    control.dataset.focusKey = key;
    control.prepend(icon("copy", "hq-icon hq-icon-small"));
    control.addEventListener("click", () => run(control));
    return control;
  }
  function attentionCard(item, snapshot) {
    const severity = SEVERITY[item.sev] || SEVERITY.info;
    const code = str(item.id).split(":")[0];
    const card = el("li", "hq-attn");
    card.dataset.sev = str(item.sev);
    card.dataset.row = `attn:${str(item.id)}`;
    const head = el("p", "hq-attn-head");
    head.append(icon(severity.icon, "hq-icon hq-sev-icon"), el("span", "hq-sev-label", severity.label));
    // "since" is a moment that has passed; a future time ("since in 2 h") is never printed (review U3).
    const since = parseIso(item.since);
    if (since !== null && since <= HQ.serverNow()) head.append(el("span", "hq-attn-since", " · since "), time(item.since, "relative"));
    card.append(head, textEl("h3", "hq-attn-title", item.title));
    if (hasText(item.why) || hasText(item.consequence)) {
      const body = el("p", "hq-attn-body");
      renderText(body, item.why);
      if (hasText(item.why) && hasText(item.consequence)) body.append(" ");
      renderText(body, item.consequence);
      card.append(body);
    }
    if (hasText(item.how)) card.append(textEl("p", "hq-attn-how", item.how));
    if (item.in_next_prompt && !/review prompt/i.test(plain(item.how))) card.append(chip("In your next review prompt", "hq-chip-soft"));
    const actions = el("div", "hq-actions");
    const decision = findDecision(snapshot, item.decision);
    if (decision?.shown && snapshot.viewer?.can_act && decide()) {
      actions.append(decide().button(decision, {focusKey: `attn:${item.id}:decision`, primary: item.sev === "act"}));
    }
    if (item.checkin_id) checkins()?.actions(item.checkin_id, actions, {focusPrefix: `attn:${item.id}`});
    if (code === "team_silent") actions.append(promptButton("Copy check-up prompt", control => prompts()?.copyCheckup(control), `attn:${item.id}:prompt`));
    if (code === "safety_stop") actions.append(promptButton("Copy technical steps", control => prompts()?.copySafety(control), `attn:${item.id}:prompt`));
    if (item.link && HQ.safePath(item.link.href)) {
      const anchor = link(item.link.href, `${str(item.link.label) || DETAIL_LABEL[item.link.href] || "Details"} ›`, "button button-secondary");
      actions.append(anchor);
    }
    if (actions.childNodes.length) card.append(actions);
    return card;
  }

  HQ.view("attention", {
    deps: snapshot => [snapshot.attention, snapshot.viewer?.can_act, list(snapshot.decisions).map(card => [card.id, card.shown, card.available]),
      snapshot.checkins, ui.attentionExpanded],
    render(root, snapshot) {
      const items = list(snapshot.attention);
      const counter = doc.querySelector("[data-hq-attention-count]");
      if (counter) {
        const total = HQ.count(snapshot.now?.attention_count) ?? items.filter(item => item.sev !== "info").length;
        counter.textContent = String(total);
        counter.hidden = total === 0;
      }
      if (!items.length) {
        root.append(empty("Nothing needs you. You can relax."));
        return;
      }
      const shown = ui.attentionExpanded ? items : items.slice(0, 3);
      const cards = el("ul", "hq-attn-list");
      for (const item of shown) cards.append(attentionCard(item, snapshot));
      root.append(cards);
      if (items.length > 3) {
        const more = button("hq-more", ui.attentionExpanded ? "Show fewer" : `Show ${items.length - 3} more`);
        more.dataset.focusKey = "attention-more";
        more.setAttribute("aria-expanded", String(ui.attentionExpanded));
        more.addEventListener("click", () => { ui.attentionExpanded = !ui.attentionExpanded; HQ.rerender("attention"); });
        root.append(more);
      }
    },
  });

  // --- Up next --------------------------------------------------------------------------------------

  HQ.view("upnext", {
    deps: snapshot => {
      const ids = list(snapshot.calendar?.up_next);
      return [ids, list(snapshot.calendar?.items).filter(item => ids.includes(item.id))];
    },
    render(root, snapshot) {
      const byId = new Map(list(snapshot.calendar?.items).map(item => [item.id, item]));
      const items = list(snapshot.calendar?.up_next).map(id => byId.get(id)).filter(Boolean);
      if (!items.length) {
        root.append(empty("Nothing is planned yet."));
        return;
      }
      const rows = el("ol", "hq-next-list");
      items.forEach((item, index) => {
        const row = el("li", "hq-next-item");
        row.dataset.kind = str(item.kind);
        row.dataset.row = `next:${str(item.id)}`;
        if (index >= 3) row.classList.add("hq-wide-only");
        // One short line under the clock time: "in 2 h" today, then "tomorrow" or the date (desktop check: a
        // "tomorrow · in 16 h 41 min" line wrapped onto three lines). The clock time above keeps the exact moment.
        const days = HQ.dayDiff(parseIso(item.at));
        const when = el("span", "hq-next-when");
        const later = el("span", "hq-next-rel");
        if (days === 0) later.append(time(item.at, "relative"));
        else if (days === 1) later.append("tomorrow");
        else later.append(time(item.at, "date"));
        when.append(time(item.at, "clock"), later);
        // A turn that can only happen if something changes says so right here, not only in the Calendar
        // tab (review U1): the item's own condition, its waiting reason, or at least "only if running".
        const what = el("span", "hq-next-what");
        const body = el("span", "hq-next-body");
        const words = el("span", "hq-next-text");
        renderText(words, item.title);
        if (item.status === "due" || item.status === "overdue") words.append(" ", chip(item.status === "due" ? "due now" : "overdue", "hq-chip-warn"));
        if (item.conditional && !hasText(item.detail)) words.append(" ", chip("only if running", "hq-chip-soft"));
        body.append(words);
        const until = groupUntil(item);
        if (until) {
          const range = el("span", "hq-next-detail", "Between ");
          range.append(time(item.at, "clock"), " and ", ...untilWords(item, until), ".");
          body.append(range);
        }
        if (hasText(item.detail)) body.append(textEl("span", "hq-next-detail", item.detail));
        if (hasText(item.waiting_because)) body.append(textEl("span", "hq-next-detail hq-warn-text", item.waiting_because));
        what.append(deptIcon(item.dept === "you" ? "you" : item.dept), body);
        row.append(when, what);
        rows.append(row);
      });
      root.append(rows);
    },
  });

  // --- HQ map and room sheet -------------------------------------------------------------------------

  function roomLine(dept, line) {
    if (dept.kind === "software") {
      renderText(line, dept.now, {inert: true});
      return;
    }
    const next = dept.next;
    const ms = parseIso(next?.at);
    if (ms !== null) {
      if (next.overdue) { line.append(OVERDUE_WORDS.get(next.status) || "Overdue"); return; }
      // Tiles are about 100 px wide: today shows the clock, other days the short weekday (the sheet has the rest).
      const days = HQ.dayDiff(ms);
      const words = days === 0 ? fmt.clock(ms) : days > 0 && days <= 6 ? `${fmt.weekday(ms)} ${fmt.clock(ms)}` : fmt.dateWords(ms);
      const stamp = el("time", "hq-time-text", words);
      stamp.dateTime = next.at;
      line.append("Next ", stamp);
      return;
    }
    if (dept.light === "on_call") line.append("When asked");
  }
  function roomTile(dept) {
    const tile = button("hq-room");
    tile.dataset.light = str(dept.light);
    tile.dataset.kind = str(dept.kind);
    tile.dataset.room = str(dept.id);
    tile.dataset.focusKey = `room:${dept.id}`;
    const top = el("span", "hq-room-top");
    const badge = el("span", "hq-room-icon");
    badge.append(icon(ROOMS.has(dept.id) ? dept.id : "hq", "hq-icon"));
    top.append(badge, el("span", "hq-room-name", dept.name));
    const light = el("span", "hq-room-light hq-light");
    light.dataset.light = str(dept.light);
    light.append(icon(`light-${LIGHT_LABEL[dept.light] ? dept.light : "off"}`, "hq-icon hq-light-icon"),
      el("span", "", LIGHT_LABEL[dept.light] || "Unknown"));
    const line = el("span", "hq-room-line");
    roomLine(dept, line);
    tile.append(top, light, line);
    if (dept.had_problem) {
      const flag = el("span", "hq-room-flag");
      const mark = el("span", "hq-room-flag-mark", "!");
      mark.setAttribute("aria-hidden", "true");
      flag.append(mark, el("span", "", "had a problem"));
      tile.append(flag);
    }
    tile.addEventListener("click", () => openRoom(dept.id, tile));
    return tile;
  }
  function kv(container, label, ...values) {
    const row = el("div", "hq-kv-row");
    const term = el("dt", "", label);
    const value = el("dd", "");
    for (const item of values) if (item) value.append(item);
    row.append(term, value);
    container.append(row);
    return row;
  }
  // A stuck job is marked "stuck", not "needs you": the owner usually can't fix it here, and the Needs-you
  // card already says what to do (review U7). "try 0 of 3" (never tried) is left out.
  function questRow(quest) {
    const row = el("li", "hq-quest");
    if (quest.needs_you) row.classList.add("is-needs-you");
    const title = el("p", "hq-quest-title");
    renderText(title, quest.state_text);
    if (quest.needs_you) title.append(" ", chip("stuck", "hq-chip-warn"));
    row.append(title);
    const meta = el("p", "hq-quest-meta");
    renderText(meta, quest.origin_text);
    const used = HQ.count(quest.attempts?.used);
    if (used) meta.append(` · try ${used} of ${quest.attempts.max}`);
    if (quest.finished_at) meta.append(" · finished ", time(quest.finished_at, "day_clock"));
    else if (quest.due_at) meta.append(" · due ", time(quest.due_at, "day_clock"));
    if (quest.deadline_at) meta.append(" · time limit ", time(quest.deadline_at, "day_clock"));
    row.append(meta);
    if (hasText(quest.reason)) {
      const reason = el("p", "hq-quest-reason", "Why: ");
      renderText(reason, quest.reason);
      row.append(reason);
    }
    const more = detailLink(quest.link);
    if (more) row.append(more);
    return row;
  }
  function roomSheet(body, dept, snapshot) {
    const intro = el("div", "hq-room-intro");
    const badge = el("span", "hq-room-icon hq-room-icon-large");
    badge.dataset.light = str(dept.light);
    badge.append(icon(ROOMS.has(dept.id) ? dept.id : "hq", "hq-icon"));
    intro.append(badge, textEl("p", "hq-room-job", dept.job));
    body.append(intro);
    const why = glossary()?.roomWhy(dept.id, snapshot.mode === "live");
    if (why) body.append(el("p", "hq-muted", why));
    const facts = el("dl", "hq-kv");
    if (hasText(dept.rules)) {
      const label = button("hq-term", "Its rules");
      label.dataset.term = "rules";
      const row = kv(facts, "", textEl("p", "", dept.rules));
      row.firstChild.replaceChildren(label);
    }
    kv(facts, "Now", lightBadge(dept.light, dept.light_text), textEl("p", "", dept.now));
    if (dept.last) {
      const last = el("p", "");
      last.append(liveTime(dept.last.at, "relative_clock"), " · ");
      renderText(last, dept.last.text);
      kv(facts, "Last", last, quote(dept.last.quote), detailLink(dept.last.link));
    }
    if (dept.next) {
      const next = el("p", "");
      if (dept.next.at) {
        next.append(liveTime(dept.next.at, "relative_clock"));
        const every = cadence(dept.next.every_seconds);
        if (every) next.append(` · ${every}`);
        if (!dept.next.exact) next.append(" (estimate)");
      } else next.append("Not planned");
      const extra = [];
      if (dept.next.overdue && hasText(dept.next.waiting_because)) extra.push(textEl("p", "hq-warn-text", dept.next.waiting_because));
      if (hasText(dept.next.note)) extra.push(textEl("p", "hq-muted", dept.next.note));
      if (dept.next.extra_rows > 0) {
        extra.push(el("p", "hq-muted", `The timetable has ${plural(dept.next.extra_rows, "extra row")} for this room; HQ uses the newest.`));
      }
      kv(facts, "Next", next, ...extra);
    }
    if (hasText(dept.heads_up)) kv(facts, "Heads-up", textEl("p", "hq-warn-text", dept.heads_up));
    if (hasText(dept.if_nothing)) kv(facts, "If nothing changes", textEl("p", "", dept.if_nothing));
    if (dept.kind === "ai" && dept.record_7d) {
      const record = dept.record_7d;
      kv(facts, "Last 7 days", el("p", "", `${record.done} done · ${plural(record.problems, "problem")} · ${record.skipped} skipped`));
    }
    for (const fact of list(dept.facts)) kv(facts, str(fact.label), textEl("p", "", fact.value));
    const quests = list(snapshot.quests).filter(quest => quest.dept === dept.id);
    const open = quests.filter(quest => !quest.terminal);
    const done = quests.filter(quest => quest.terminal);
    const todo = el("div", "");
    if (open.length) {
      const rows = el("ul", "hq-quest-list");
      for (const quest of open) rows.append(questRow(quest));
      todo.append(rows);
    } else todo.append(el("p", "", "None open."));
    if (done.length) {
      const recent = el("details", "hq-recent");
      recent.append(el("summary", "", `Recent work (${done.length})`));
      const rows = el("ul", "hq-quest-list");
      for (const quest of done.slice(0, 10)) rows.append(questRow(quest));
      recent.append(rows);
      todo.append(recent);
    }
    kv(facts, "To-dos", todo);
    body.append(facts);
    if (dept.id === "trader" && HQ.use("desk") && doc.querySelector('[data-hq="desk"]')) {
      // W13-A: the desk's playbook, today's choices, its lab and the news it read are in the Trading desk room.
      const room = button("button button-secondary hq-desk-open", "Open the Trading desk room");
      room.addEventListener("click", () => { HQ.sheet.close(); HQ.use("desk").open(); });
      body.append(el("p", "hq-muted", "Its playbook, today's choice per coin, its strategy lab and the news it read are in the Trading desk room, below the map."), room);
    }
    const actions = el("div", "hq-actions hq-sheet-actions");
    const focus = `${dept.name}: ${plain(dept.light_text)}. ${plain(dept.now)}`;
    actions.append(askButton(focus, snapshot.as_of));
    const more = detailLink(dept.details_href);
    if (more) { more.className = "button button-secondary"; actions.append(more); }
    body.append(actions);
  }
  function openRoom(id, opener) {
    const snapshot = HQ.snapshot();
    const dept = list(snapshot?.departments).find(item => item.id === id);
    if (!dept) return;
    HQ.sheet.open({
      title: dept.name === dept.room ? dept.name : `${dept.name} · ${dept.room}`,
      kind: "room",
      from: opener,
      build: body => roomSheet(body, dept, snapshot),
    });
  }

  HQ.view("map", {
    deps: snapshot => [snapshot.departments, snapshot.now?.code],
    render(root, snapshot) {
      const grid = el("div", "hq-map-grid");
      for (const dept of list(snapshot.departments)) grid.append(roomTile(dept));
      root.append(grid);
      const legend = glossary()?.legend();
      if (legend) root.append(legend);
    },
  });

  // --- Calendar --------------------------------------------------------------------------------------

  const DEADLINES = new Set(["research_expiry", "allowance_refill", "optimisation_deadline", "trial_end", "proposal_expiry",
    "engineer_deadline", "mandate_expiry"]);
  function calendarVisible(item) {
    if (item.kind === "owner_checkin") return ui.calendarFilters.you;
    if (DEADLINES.has(item.kind)) return ui.calendarFilters.deadlines;
    return ui.calendarFilters.depts;
  }
  // Status is a glyph plus words everywhere, never colour alone (spec §C.11, review U14).
  function statusMark(item) {
    const [mark, words] = CALENDAR_STATUS[item.status] || ["•", str(item.status)];
    const glyph = el("span", "hq-cal-mark", item.kind === "owner_checkin" && !item.past ? "◆" : mark);
    glyph.setAttribute("aria-hidden", "true");
    return {glyph, words};
  }
  // `hideDetail`: the row's condition is already said just above it (or in the estimates caveat), so neither it
  // nor the "only if running" stand-in is repeated on this row.
  function calendarRow(item, snapshot, {hideDetail = false} = {}) {
    const row = el("li", "hq-cal-item");
    row.dataset.status = str(item.status);
    row.dataset.kind = str(item.kind);
    row.dataset.row = `cal:${str(item.id)}`;
    const {glyph, words} = statusMark(item);
    const when = el("span", "hq-cal-when");
    when.append(time(item.at, "clock"));
    const until = groupUntil(item);
    if (until) {
      const to = el("span", "hq-cal-until", "to ");
      to.append(...untilWords(item, until));
      when.append(to);
    }
    const main = el("div", "hq-cal-main");
    const title = el("p", "hq-cal-title");
    renderText(title, item.title);
    title.append(el("span", "hq-cal-status", ` · ${words}`));
    if (!item.exact && item.status !== "estimated") title.append(" ", chip("estimate", "hq-chip-soft"));
    if (item.conditional && !hasText(item.detail) && !hideDetail) title.append(" ", chip("only if running", "hq-chip-soft"));
    main.append(title);
    if (hasText(item.detail) && !hideDetail) main.append(textEl("p", "hq-cal-detail", item.detail));
    if (hasText(item.warning)) {
      // One text box beside the icon: the row is a flex row, and loose words, times and the full stop
      // would each become a flex item with gaps between them ("at 12:56 .").
      const warning = el("p", "hq-cal-warning");
      const words = el("span", "hq-cal-warning-text");
      renderText(words, item.warning);
      warning.append(icon("sev-watch", "hq-icon hq-icon-small"), words);
      main.append(warning);
    }
    if (hasText(item.waiting_because)) main.append(textEl("p", "hq-cal-detail", item.waiting_because));
    const more = detailLink(item.link);
    if (more) main.append(more);
    if (item.kind === "owner_checkin" && item.checkin_id && snapshot.checkins) {
      const actions = el("div", "hq-actions hq-actions-compact");
      checkins()?.actions(item.checkin_id, actions, {focusPrefix: `cal:${item.id}`, compact: true});
      if (actions.childNodes.length) main.append(actions);
    }
    row.append(when, glyph, main);
    return row;
  }
  /* Conditions are said once, not on every row (desktop check): an estimate's condition is the caveat above the
     agenda, and a future row whose condition matches the last one shown in the same list leaves it out. Returns
     the items whose condition is not repeated. */
  function repeatedConditions(entries, caveatKey) {
    const hidden = new Set();
    let last = null;
    for (const item of entries) {
      if (item.past || !hasText(item.detail)) continue;
      const key = plain(item.detail);
      if (item.status === "estimated" && key === caveatKey) { hidden.add(item); continue; }
      if (key === last) hidden.add(item);
      last = key;
    }
    return hidden;
  }
  function calendarRows(entries, snapshot, caveatKey) {
    const hidden = repeatedConditions(entries, caveatKey);
    return entries.map(item => calendarRow(item, snapshot, {hideDetail: hidden.has(item)}));
  }
  function dayLabel(ms) {
    const diff = HQ.dayDiff(ms);
    if (diff === 0) return "Today";
    if (diff === 1) return "Tomorrow";
    if (diff === -1) return "Yesterday";
    return fmt.dateWords(ms);
  }
  function filterChip(key, label) {
    const control = button("hq-filter", label);
    control.dataset.focusKey = `cal-filter:${key}`;
    control.setAttribute("aria-pressed", String(ui.calendarFilters[key]));
    control.addEventListener("click", () => { ui.calendarFilters[key] = !ui.calendarFilters[key]; HQ.rerender("calendar"); });
    return control;
  }

  /* Contract calendar.estimates (optional; older servers leave it out): estimated turns more than 48 h ahead
     arrive as one series per schedule, first_at + k·every_seconds for k = 0 … count−1, instead of one item each
     (review T2). Times are worked out in milliseconds from parseIso, so they never drift with the local zone. */
  const SERIES_SHOWN_MAX = 120;
  function seriesTimes(series) {
    const first = parseIso(series?.first_at);
    const every = Number.isSafeInteger(series?.every_seconds) && series.every_seconds > 0 ? series.every_seconds * 1000 : null;
    const count = HQ.count(series?.count);
    if (first === null || every === null || !count) return [];
    return Array.from({length: Math.min(count, SERIES_SHOWN_MAX)}, (_, index) => first + index * every);
  }
  function estimateSeries(calendar) {
    if (!Array.isArray(calendar.estimates)) return [];
    return calendar.estimates.filter(series => series && typeof series.schedule_id === "string" && seriesTimes(series).length);
  }
  // One summarised projection as a calendar item, for the week strip and the "Later" rows.
  function projectionItem(series, ms, index) {
    return {
      id: `estimate:${series.schedule_id}:${index}`, kind: "dept_turn", dept: series.dept, at: HQ.toIso(ms),
      title: series.title, status: "estimated", exact: false, conditional: series.conditional === true, past: false,
      detail: null, warning: null, waiting_because: null, link: null, checkin_id: null,
      series: {schedule_id: series.schedule_id, every_seconds: series.every_seconds},
    };
  }
  // A "Later" row: the day as well as the time, and no per-row condition (the series says it once).
  function laterRow(item, snapshot) {
    const row = calendarRow({...item, detail: null, conditional: false}, snapshot);
    row.querySelector(".hq-cal-when")?.replaceChildren(time(item.at, "day_clock"));
    return row;
  }
  function seriesBox(key, listed, series, snapshot, caveatKey) {
    const first = listed[0] || null;
    const dept = first ? first.dept : series?.dept;
    const every = cadence(first?.series?.every_seconds ?? series?.every_seconds);
    const projected = series ? seriesTimes(series).map((ms, index) => projectionItem(series, ms, index)) : [];
    const total = listed.length + (series ? HQ.count(series.count) || 0 : 0);
    const box = HQ.keepOpen(el("details", "hq-cal-series"), `series:${key}`);
    box.dataset.row = `series:${key}`;
    box.append(el("summary", "", `${deptName(dept, snapshot)}: about ${every || "regularly"} (${plural(total, "turn")})`));
    const detail = listed.find(item => hasText(item.detail))?.detail || (hasText(series?.detail) ? series.detail : null);
    if (detail && plain(detail) === caveatKey) { /* said once above the agenda */ }
    else if (detail) box.append(textEl("p", "hq-cal-detail", detail));
    else if (listed.some(item => item.conditional) || series?.conditional === true) {
      const note = el("p", "hq-cal-detail");
      note.append(chip("only if running", "hq-chip-soft"));
      box.append(note);
    }
    const rows = el("ul", "hq-cal-list");
    for (const item of [...listed, ...projected]) rows.append(laterRow(item, snapshot));
    if (total > listed.length + projected.length) rows.append(el("li", "hq-cal-detail", `+${total - listed.length - projected.length} more`));
    box.append(rows);
    return box;
  }

  // Week-strip chips are narrow: a deadline says what ends in two or three words, with its count when grouped
  // (singular for one, like the agenda's title). Words are never broken at an arbitrary letter (desktop check):
  // a hyphen is a non-breaking one, and a word too long for an iPad's column has a soft hyphen (U+00AD) where it
  // may break properly ("Allow-ance"), since browsers without a hyphenation dictionary would just cut it.
  const SHORT_DEADLINE = new Map([
    ["research_expiry", ["Note expires", "notes expire"]],
    ["proposal_expiry", ["Idea expires", "ideas expire"]],
    ["engineer_deadline", ["Engineer limit", "Engineer limits"]],
    ["allowance_refill", ["Allow­ance refill", "allow­ance refills"]],
    ["optimisation_deadline", ["Review limit", "review limits"]],
    ["mandate_expiry", ["Permis­sion ends", "permis­sions end"]],
  ]);
  function chipWords(item, snapshot) {
    if (item.kind === "dept_turn") return deptName(item.dept, snapshot);
    if (item.kind === "extra_review") return `${deptName(item.dept, snapshot)} review`;
    if (item.kind === "owner_checkin") return "Your check‑in";
    if (item.kind === "trial_end") return /reload/i.test(plain(item.title)) ? "Reload limit" : "Trial ends";
    const short = SHORT_DEADLINE.get(item.kind);
    if (!short) return plain(item.title).replace(/-/g, "‑");
    const count = HQ.count(item.count);
    return count > 1 ? `${count} ${short[1]}` : short[0];
  }

  function weekStrip(items, snapshot) {
    const strip = el("div", "hq-week");
    strip.setAttribute("role", "group");
    strip.setAttribute("aria-label", "The next 7 days at a glance");
    const nowMs = HQ.serverNow();
    const start = new Date(HQ.dayStart(nowMs));
    for (let offset = 0; offset < 7; offset += 1) {
      const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + offset).getTime();
      const key = HQ.localDayKey(day);
      const column = el("div", "hq-week-day");
      if (offset === 0) column.classList.add("is-today");
      const head = button("hq-week-head", offset === 0 ? "Today" : `${fmt.weekday(day)} ${new Date(day).getDate()}`);
      head.dataset.focusKey = `week:${key}`;
      head.addEventListener("click", () => {
        const target = doc.getElementById(`hq-cal-${key}`) || doc.getElementById("hq-cal-later");
        if (target instanceof HTMLDetailsElement) target.open = true;
        target?.scrollIntoView({behavior: "smooth", block: "start"});
      });
      column.append(head);
      const entries = items.filter(item => HQ.localDayKey(parseIso(item.at)) === key);
      const chips = el("ul", "hq-week-chips");
      let nowPlaced = offset !== 0;
      entries.slice(0, 8).forEach(item => {
        if (!nowPlaced && parseIso(item.at) > nowMs) {
          chips.append(el("li", "hq-week-now", "Now"));
          nowPlaced = true;
        }
        const entry = el("li", "hq-week-chip");
        entry.dataset.status = str(item.status);
        entry.dataset.kind = str(item.kind);
        const {glyph, words: status} = statusMark(item);
        // Only the start time fits a chip (desktop check: "12:30–" / "12:40" wrapped); a group's range is in the
        // agenda and Up next. A department's one-word name stays on one line (an ellipsis if it can't fit).
        const top = el("span", "hq-week-chip-top");
        const when = el("span", "hq-week-when");
        when.append(time(item.at, "clock"));
        top.append(glyph, when);
        const title = el("span", "hq-week-chip-title", chipWords(item, snapshot));
        if (item.kind === "dept_turn") title.classList.add("is-name");
        entry.append(top, title, el("span", "hq-sr", `, ${status}`));
        chips.append(entry);
      });
      if (!nowPlaced) chips.append(el("li", "hq-week-now", "Now"));
      if (entries.length > 8) chips.append(el("li", "hq-week-more", `+${entries.length - 8} more`));
      if (!entries.length) chips.append(el("li", "hq-week-empty", "Nothing planned"));
      column.append(chips);
      strip.append(column);
    }
    return strip;
  }

  HQ.view("calendar", {
    deps: snapshot => {
      const calendar = obj(snapshot.calendar);
      const now = HQ.serverNow();
      // The window's own from/to move with every request, so they are left out: only real changes rebuild
      // the calendar (review T3). The count of passed items moves the "Now" lines as the clock passes them.
      return [calendar.items, calendar.recurring, calendar.notes, calendar.truncated, calendar.estimates ?? null,
        snapshot.checkins, snapshot.viewer?.can_act, ui.calendarFilters, ui.calendarBusy, HQ.calendarDays(),
        list(snapshot.departments).map(dept => dept.name),
        list(calendar.items).filter(item => (parseIso(item?.at) ?? Infinity) <= now).length];
    },
    render(root, snapshot) {
      const calendar = obj(snapshot.calendar);
      const filters = el("div", "hq-filters");
      filters.setAttribute("role", "group");
      filters.setAttribute("aria-label", "Show in the calendar");
      filters.append(filterChip("depts", "Departments"), filterChip("you", "You"), filterChip("deadlines", "Deadlines"),
        filterChip("machine", "Machine routines"));
      root.append(filters);
      const items = list(calendar.items).filter(item => parseIso(item?.at) !== null && calendarVisible(item))
        .sort((a, b) => parseIso(a.at) - parseIso(b.at));
      const series = ui.calendarFilters.depts ? estimateSeries(calendar) : [];
      const projections = series.flatMap(entry => seriesTimes(entry).map((ms, index) => projectionItem(entry, ms, index)));
      const ahead = [...items, ...projections].filter(item => HQ.dayDiff(parseIso(item.at)) >= 0)
        .sort((a, b) => parseIso(a.at) - parseIso(b.at));
      root.append(weekStrip(ahead, snapshot));
      const earlier = [];
      const current = [];
      const days = new Map();
      const later = new Map();
      for (const item of items) {
        const ms = parseIso(item.at);
        const diff = HQ.dayDiff(ms);
        if (CURRENT.has(item.status)) current.push(item);
        else if (diff < 0) earlier.push(item);
        else if (diff > 1 && item.status === "estimated" && item.series?.schedule_id) {
          const key = item.series.schedule_id;
          if (!later.has(key)) later.set(key, []);
          later.get(key).push(item);
        } else {
          const key = HQ.localDayKey(ms);
          if (!days.has(key)) days.set(key, {ms, items: []});
          days.get(key).items.push(item);
        }
      }
      const agenda = el("div", "hq-agenda");
      // Every estimated turn carries the same condition: it is said once, above the agenda (desktop check).
      const caveat = [...items, ...series].find(entry => (entry.status === "estimated" || entry.schedule_id) &&
        entry.conditional === true && hasText(entry.detail))?.detail || null;
      const caveatKey = caveat ? plain(caveat) : null;
      if (caveat) {
        const note = el("p", "hq-cal-caveat");
        const mark = el("span", "hq-cal-mark", "≈");
        mark.setAttribute("aria-hidden", "true");
        note.append(mark, el("strong", "", "Estimated turns: "));
        renderText(note, caveat);
        agenda.append(note);
      }
      const group = (label, entries, id, nowLine) => {
        const block = el("section", "hq-cal-group");
        if (id) block.id = id;
        block.append(el("h3", "hq-cal-day", label));
        const rows = el("ul", "hq-cal-list");
        const nowMs = HQ.serverNow();
        let placed = !nowLine;
        calendarRows(entries, snapshot, caveatKey).forEach((row, index) => {
          // Today's list shows where "now" falls, like the week strip (review U14).
          if (!placed && parseIso(entries[index].at) > nowMs) { rows.append(el("li", "hq-cal-now-line", "Now")); placed = true; }
          rows.append(row);
        });
        if (!placed) rows.append(el("li", "hq-cal-now-line", "Now"));
        block.append(rows);
        return block;
      };
      if (earlier.length) {
        const box = HQ.keepOpen(el("details", "hq-cal-earlier"), "cal:earlier");
        box.dataset.row = "cal:earlier";
        box.append(el("summary", "", `Earlier (${earlier.length})`));
        const rows = el("ul", "hq-cal-list");
        rows.append(...calendarRows(earlier, snapshot, caveatKey));
        box.append(rows);
        agenda.append(box);
      }
      if (current.length) agenda.append(group("Now", current, "hq-cal-now", false));
      const todayKey = HQ.localDayKey(HQ.serverNow());
      for (const [key, day] of days) agenda.append(group(dayLabel(day.ms), day.items, `hq-cal-${key}`, key === todayKey));
      const byId = new Map(series.map(entry => [entry.schedule_id, entry]));
      const keys = [...new Set([...later.keys(), ...byId.keys()])];
      if (keys.length) {
        const firstAt = key => later.has(key) ? parseIso(later.get(key)[0].at) : parseIso(byId.get(key).first_at);
        keys.sort((a, b) => firstAt(a) - firstAt(b));
        const block = el("section", "hq-cal-group");
        block.id = "hq-cal-later";
        block.append(el("h3", "hq-cal-day", "Later (estimates)"));
        for (const key of keys) block.append(seriesBox(key, later.get(key) || [], byId.get(key), snapshot, caveatKey));
        agenda.append(block);
      }
      if (!agenda.childNodes.length) agenda.append(empty("Nothing to show for this period with these filters."));
      root.append(agenda);
      if (ui.calendarFilters.machine) {
        const routines = list(calendar.recurring);
        const block = el("section", "hq-cal-group");
        block.append(el("h3", "hq-cal-day", "Machine routines"));
        if (!routines.length) block.append(empty("No machine routines are recorded."));
        for (const routine of routines) {
          const line = el("p", "hq-cal-routine");
          renderText(line, routine.title);
          const every = cadence(routine.every_seconds);
          if (every) line.append(`: ${every}`);
          if (routine.next_at) line.append(", next ", time(routine.next_at, "relative_clock"));
          block.append(line);
        }
        agenda.append(block);
      }
      const footer = el("div", "hq-cal-footer");
      const range = button("button button-secondary", HQ.calendarDays() === 14 ? "Show 1 week" : "Show 2 weeks");
      range.dataset.focusKey = "cal-range";
      range.disabled = ui.calendarBusy;
      range.addEventListener("click", async () => {
        ui.calendarBusy = true;
        HQ.rerender("calendar");
        await HQ.setCalendarDays(HQ.calendarDays() === 14 ? 7 : 14);
        ui.calendarBusy = false;
        HQ.rerender("calendar");
      });
      footer.append(range);
      root.append(footer);
      const notes = el("ul", "hq-notes");
      for (const note of list(calendar.notes)) notes.append(textEl("li", "", note));
      // The estimates caveat above the agenda already says that missed turns are merged into one.
      if (!caveat && (items.some(item => item.conditional || item.status === "overdue") || series.some(entry => entry.conditional === true))) {
        notes.append(el("li", "", "If several turns were missed, the team does one catch-up turn, not all of them."));
      }
      if (calendar.truncated) notes.append(el("li", "", "Only the first 200 items are shown."));
      if (notes.childNodes.length) root.append(notes);
    },
  });

  // --- Story -----------------------------------------------------------------------------------------

  const STORY_FILTERS = [["all", "All"], ["team", "Team"], ["you", "You"], ["problems", "Problems"]];
  function storyMatches(item) {
    if (ui.storyFilter === "team") return item.dept !== "you";
    if (ui.storyFilter === "you") return item.dept === "you";
    if (ui.storyFilter === "problems") return item.problem === true;
    return true;
  }
  function daySummary(items) {
    const decisions = items.filter(item => item.kind === "trader_decision");
    const waits = decisions.length > 0 && decisions.every(item => item.technical === "no_action" || item.technical === "hold");
    const trades = items.filter(item => item.kind === "fill").length;
    const problems = items.filter(item => item.problem === true).length;
    return `${plural(decisions.length, "decision")}${waits ? " (all wait)" : ""} · ${plural(trades, "trade")} · ${plural(problems, "problem")}`;
  }
  function storyRow(item, snapshot) {
    const row = el("li", "hq-story-item");
    row.dataset.tone = str(item.tone);
    row.dataset.row = `story:${str(item.id)}`;
    if (item.problem) row.classList.add("is-problem");
    const when = el("span", "hq-story-when");
    when.append(time(item.at, "clock"));
    if (parseIso(item.until) !== null) when.append(el("span", "hq-story-until", "until "), time(item.until, "clock"));
    const main = el("div", "hq-story-main");
    const who = el("p", "hq-story-who");
    who.append(deptIcon(item.dept), el("span", "", deptName(item.dept, snapshot)));
    const lastSeen = parseIso(HQ.lastSeen());
    if (lastSeen !== null && parseIso(item.at) > lastSeen) who.append(chip("New", "hq-chip-new"));
    if (Number.isSafeInteger(item.count) && item.count > 1) who.append(chip(`×${item.count}`, "hq-chip-soft"));
    main.append(who, textEl("p", "hq-story-headline", item.headline));
    if (hasText(item.consequence)) main.append(textEl("p", "hq-story-consequence", item.consequence));
    const more = HQ.keepOpen(el("details", "hq-story-more"), `story-more:${str(item.id)}`);
    more.append(el("summary", "", item.quote?.text ? "In their words" : "More"));
    if (item.quote?.text) more.append(el("blockquote", "", item.quote.text));
    const record = detailLink(item.link, "Open the record");
    if (record) more.append(record);
    if (HQ.technical() && item.technical) more.append(el("p", "hq-code", `[${item.technical}]`));
    const focusText = `${plain(item.headline)}${hasText(item.consequence) ? ` ${plain(item.consequence)}` : ""}`;
    more.append(askButton(focusText, item.at));
    main.append(more);
    row.append(when, main);
    return row;
  }
  async function loadEarlier() {
    const snapshot = HQ.snapshot();
    if (ui.olderBusy || ui.olderDone || !snapshot) return;
    ui.olderBusy = true;
    ui.olderNote = "";
    HQ.rerender("story");
    const started = parseIso(snapshot.account?.started_at);
    let before = ui.olderBefore || snapshot.story?.next_before;
    for (let hop = 0; hop < 5 && before; hop += 1) {
      const result = await HQ.api.get("story", {before});
      const page = result.ok ? result.body?.story : null;
      if (!page || !Array.isArray(page.items)) {
        ui.olderNote = "Couldn't load earlier records. Try again in a moment.";
        break;
      }
      for (const item of page.items) if (item?.id) ui.older.set(item.id, item);
      const windowStart = parseIso(page.window_start);
      if (windowStart !== null && (ui.olderWindow === null || windowStart < ui.olderWindow)) ui.olderWindow = windowStart;
      const next = page.next_before;
      if (!HQ.isIso(next) || next === before || (started !== null && parseIso(next) <= started)) {
        ui.olderDone = true;
        before = null;
        break;
      }
      before = next;
      if (page.items.length) break;
    }
    ui.olderBefore = before;
    ui.olderBusy = false;
    HQ.rerender("story");
  }

  HQ.view("story", {
    // window_start and next_before move with every request; only what they change on screen counts: the
    // first day shown, and whether this page was cut short (review T3).
    deps: snapshot => {
      const story = obj(snapshot.story);
      const start = parseIso(story.window_start);
      const next = story.next_before;
      return [list(story.items), !HQ.isIso(next) ? null : next === story.window_start ? "window" : next,
        start === null ? null : HQ.localDayKey(start), ui.storyFilter, ui.older.size, ui.olderBusy, ui.olderDone,
        ui.olderNote, list(snapshot.departments).map(dept => dept.name)];
    },
    render(root, snapshot) {
      const filters = el("div", "hq-filters");
      filters.setAttribute("role", "group");
      filters.setAttribute("aria-label", "Show in the story");
      for (const [key, label] of STORY_FILTERS) {
        const control = button("hq-filter", label);
        control.dataset.focusKey = `story-filter:${key}`;
        control.setAttribute("aria-pressed", String(ui.storyFilter === key));
        control.addEventListener("click", () => { ui.storyFilter = key; HQ.rerender("story"); });
        filters.append(control);
      }
      root.append(filters);
      const all = storyItems(snapshot);
      const byDay = new Map();
      for (const item of all) {
        const key = HQ.localDayKey(parseIso(item.at));
        if (!byDay.has(key)) byDay.set(key, []);
        byDay.get(key).push(item);
      }
      const nowMs = HQ.serverNow();
      const windows = [parseIso(snapshot.story?.window_start), ui.olderWindow].filter(value => value !== null);
      const oldest = Math.min(nowMs, ...windows, ...all.map(item => parseIso(item.at)));
      const timeline = el("div", "hq-story-days");
      const today = new Date(HQ.dayStart(nowMs));
      for (let offset = 0; offset < 120; offset += 1) {
        const day = new Date(today.getFullYear(), today.getMonth(), today.getDate() - offset).getTime();
        if (day + 86400000 <= oldest && offset > 0) break;
        const key = HQ.localDayKey(day);
        const dayItems = byDay.get(key) || [];
        const shown = dayItems.filter(storyMatches);
        const label = dayLabel(day);
        const rows = el("ul", "hq-story-list");
        for (const item of shown) rows.append(storyRow(item, snapshot));
        const none = empty(dayItems.length ? "Nothing of this kind on this day." : "No recorded activity");
        if (offset === 0) {
          const block = el("section", "hq-story-day is-today");
          const head = el("h3", "hq-story-day-title", label);
          block.append(head, el("p", "hq-story-summary", daySummary(dayItems)));
          block.append(shown.length ? rows : none);
          timeline.append(block);
          continue;
        }
        const block = HQ.keepOpen(el("details", "hq-story-day"), `story-day:${key}`);
        block.dataset.row = `day:${key}`;
        const summary = el("summary", "");
        summary.append(el("span", "hq-story-day-title", label), el("span", "hq-story-summary",
          dayItems.length ? daySummary(dayItems) : "No recorded activity"));
        block.append(summary, shown.length ? rows : none);
        timeline.append(block);
      }
      root.append(timeline);
      const footer = el("div", "hq-story-footer");
      if (ui.olderNote) footer.append(el("p", "hq-warn-text", ui.olderNote));
      if (ui.olderDone) footer.append(el("p", "hq-muted", "You've reached the start of the saved records."));
      else if (snapshot.story?.next_before) {
        const more = button("button button-secondary", ui.olderBusy ? "Loading…" : "Load earlier");
        more.dataset.focusKey = "story-earlier";
        more.disabled = ui.olderBusy;
        more.addEventListener("click", loadEarlier);
        footer.append(more);
      }
      root.append(footer);
    },
  });

  // --- Money, allowance and market weather ----------------------------------------------------------

  function money(value) {
    if (!value || typeof value.display !== "string") return null;
    return textEl("span", "", [{t: "money", amount: value.amount, currency: value.currency, display: value.display}]);
  }
  function term(key, words) { return textEl("span", "", [{t: "term", key, v: words}]); }
  // A negative amount without its sign, for "short by …" sentences (the display's minus is U+2212).
  function unsigned(value) {
    if (!value || typeof value.display !== "string") return null;
    return {...value, amount: str(value.amount).replace(/^-/, ""), display: value.display.replace(/^[\u2212-]/, "")};
  }
  function stat(label, value, note) {
    const box = el("div", "hq-stat");
    const name = el("p", "hq-stat-label");
    name.append(label);
    box.append(name, el("p", "hq-stat-value"));
    box.lastChild.append(value || el("span", "hq-unknown", "Not known yet"));
    if (note) box.append(note);
    return box;
  }

  HQ.view("money", {
    deps: snapshot => [snapshot.money, snapshot.mode, snapshot.viewer?.can_act,
      list(snapshot.decisions).map(card => [card.id, card.shown, card.available])],
    render(root, snapshot) {
      const block = obj(snapshot.money);
      const live = snapshot.mode === "live";
      if (!block.available) {
        const note = el("div", "hq-notice");
        note.dataset.tone = "attention";
        note.append(icon("sev-watch", "hq-icon hq-notice-icon"), textEl("p", "hq-notice-text", block.unavailable_reason));
        root.append(note);
      }
      const score = el("div", "hq-score");
      const label = el("p", "hq-score-label");
      label.append(live ? "Account value" : term("practice_money", "Practice money"));
      const value = el("p", "hq-score-value");
      value.append(money(block.practice_value) || el("span", "hq-unknown", "Not known yet"));
      if (block.practice_value && !block.value_final) value.append(" ", term("estimate", "estimate"));
      score.append(label, value);
      const opening = obj(block.opening);
      const natives = list(opening.native).filter(item => item && typeof item.display === "string");
      if (natives.length) {
        const line = el("p", "hq-score-note", "Started with ");
        natives.forEach((item, index) => { if (index) line.append(" and "); line.append(money(item)); });
        if (parseIso(opening.at) !== null) line.append(" on ", time(opening.at, "date"));
        if (opening.reporting) line.append(" (worth ", money(opening.reporting), " then)");
        line.append(".");
        score.append(line);
      }
      root.append(score);
      const change = obj(block.change);
      const changeLine = el("p", "hq-change");
      if (change.total) {
        changeLine.append(el("strong", "", "Change since start "), money(change.total));
        if (change.currency_effect && change.team_effect) {
          changeLine.append(": ", term("currency_effect", "exchange rate"), " ", money(change.currency_effect), " · ",
            term("team_effect", "the team's trading"), " ", money(change.team_effect), ".");
        } else changeLine.append(".");
        if (hasText(change.unknown_reason)) { changeLine.append(" "); renderText(changeLine, change.unknown_reason); }
      } else {
        changeLine.append(el("strong", "", "Change since start: "), "Can't split this yet: ");
        renderText(changeLine, change.unknown_reason);
      }
      root.append(changeLine);
      const notes = list(block.estimate_notes);
      if (notes.length) {
        const box = el("ul", "hq-estimates");
        for (const note of notes) {
          const row = textEl("li", "hq-estimate", note.text);
          row.dataset.tone = note.tone === "attention" ? "attention" : "calm";
          box.append(row);
        }
        root.append(box);
      }
      const holdings = obj(block.holdings);
      const grid = el("div", "hq-stats");
      grid.append(stat("In coins", money(holdings.coins)), stat("Cash", money(holdings.cash)),
        stat("Open orders", el("span", "", str(HQ.count(holdings.open_orders) ?? "Not known"))));
      const unclear = stat(term("unclear_order", "Orders being checked"), el("span", "", str(HQ.count(holdings.unclear_orders) ?? "Not known")));
      if (holdings.unclear_orders > 0) unclear.classList.add("is-attention");
      grid.append(unclear);
      root.append(grid);
      const counts = obj(block.counts);
      if (Number.isSafeInteger(counts.decisions)) {
        const practice = live ? "" : "practice ";
        const line = el("p", "hq-counts",
          `${plural(counts.decisions, "Trader decision")} (${plural(counts.waits, "wait")}) · ${plural(counts.orders, `${practice}order`)} · ` +
          `${plural(counts.completed_trades, "completed trade")} · ${plural(counts.research_notes, "research note")} ` +
          `(${counts.notes_in_use} in use now) · ${plural(counts.lessons, "lesson")}`);
        root.append(line);
        if (counts.completed_trades === 0 && !live) {
          root.append(el("p", "hq-muted", "No trades yet: waiting is a valid choice; buying and selling are simply untested."));
        }
      }
      const pays = obj(block.pays_for_itself);
      const paysBox = el("section", "hq-pays");
      paysBox.dataset.status = str(pays.status);
      const heading = el("h3", "hq-subhead");
      // W8-C (D254): on the paper account this is a comparison only. Paper money is simulated and never pays a real
      // bill or refills the budget (AGENTS.md), so the box never says the run "pays for itself" or has money "left".
      heading.append(term("pays_for_itself", live ? "Pays for itself?" : "Paper result against real costs"));
      paysBox.append(heading);
      const verdict = el("p", "hq-pays-verdict");
      if (live && pays.status === "covered") verdict.append("Yes, for this period: ", money(pays.net), " left after costs.");
      else if (live && pays.status === "not_covered") verdict.append("Not yet: ", money(pays.net), " after costs.");
      else if (pays.status === "covered") verdict.append("On paper, the result is ", money(pays.net), " above the real costs you entered.");
      else if (pays.status === "not_covered") verdict.append("On paper, the result is ", money(unsigned(pays.net)), " short of the real costs you entered.");
      else verdict.append("Not known yet");
      paysBox.append(verdict);
      if (!live) paysBox.append(el("p", "hq-muted", "A comparison only: paper money is simulated, never pays a real bill and never refills your budget."));
      const missing = list(pays.missing);
      if (missing.length) {
        const rows = el("ul", "hq-missing");
        for (const item of missing) {
          const row = textEl("li", "", item.text);
          const fix = item.code === "subscription_bills_unconfirmed" ? "record_bill" :
            item.code === "other_costs_unconfirmed" ? "declare_bills_complete" : null;
          const card = fix && item.fixable_here ? findDecision(snapshot, fix) : null;
          if (card?.shown && snapshot.viewer?.can_act && decide()) {
            row.append(" ", decide().button(card, {small: true, focusKey: `missing:${item.code}`}));
          }
          rows.append(row);
        }
        paysBox.append(rows);
      }
      if (hasText(pays.honest_note)) paysBox.append(textEl("p", "hq-muted", pays.honest_note));
      root.append(paysBox);
      if (hasText(block.at_risk)) root.append(textEl("p", "hq-at-risk", block.at_risk));
    },
  });

  function gauge(percent, line) {
    const wrap = el("div", "hq-gauge");
    const meter = el("meter", "hq-meter", `${percentText(percent)}`);
    meter.min = 0;
    meter.max = 100;
    if (typeof line === "number") {
      meter.low = line;
      meter.high = Math.min(100, line + 10);
    }
    meter.optimum = 100;
    meter.value = percent;
    meter.setAttribute("aria-label", `${percentText(percent)} of the allowance left`);
    wrap.append(meter);
    if (typeof line === "number") {
      const mark = el("span", `hq-gauge-line hq-at-${Math.max(0, Math.min(100, Math.round(line / 5) * 5))}`);
      mark.setAttribute("aria-hidden", "true");
      wrap.append(mark);
    }
    return wrap;
  }

  HQ.view("allowance", {
    deps: snapshot => [snapshot.team?.allowance, snapshot.team?.ai],
    render(root, snapshot) {
      const allowance = obj(snapshot.team?.allowance);
      const ai = obj(snapshot.team?.ai);
      if (hasText(allowance.plain)) root.append(textEl("p", "hq-allowance-plain", allowance.plain));
      const line = typeof allowance.stop_line_percent === "number" ? allowance.stop_line_percent : null;
      for (const window of list(allowance.windows)) {
        const box = el("div", "hq-window");
        const title = window.label === "weekly" ? "This week" : window.label === "5-hour" ? "5-hour limit" :
          window.duration_minutes ? `Limit of ${fmt.duration(window.duration_minutes * 60)}` : "Other limit";
        const head = el("p", "hq-window-head");
        head.append(el("span", "hq-window-name", title));
        const left = percentText(window.remaining_percent);
        head.append(el("span", "hq-window-value", left ? `${left} left` : "Not reported"));
        box.append(head);
        if (left) box.append(gauge(window.remaining_percent, line));
        const refill = el("p", "hq-muted");
        if (parseIso(window.resets_at) !== null) refill.append("Refills ", time(window.resets_at, "day_clock"));
        else refill.append("Refill time not reported");
        box.append(refill);
        root.append(box);
      }
      // The reserve rule is on (with its line), off, or unreadable (contract team.allowance.stop_line_state).
      // An unreadable rule is never shown as "no rule"; an older server without the field falls back on the
      // line itself: a number means on, null means it couldn't be read (review U5).
      const facts = el("ul", "hq-plain-list");
      const stop = el("li", "");
      const rule = ["on", "off", "unknown"].includes(allowance.stop_line_state) ? allowance.stop_line_state :
        line !== null ? "on" : "unknown";
      if (rule === "on" && line !== null) stop.append(term("stop_line", "New AI work stops"), ` at ${percentText(line)} (it needs more than that).`);
      else if (rule === "off") stop.append("No reserve rule is set.");
      else stop.append("The reserve rule couldn't be read.");
      facts.append(stop);
      if (list(allowance.unreported_windows).length && !/isn't reported/.test(plain(allowance.plain))) {
        facts.append(el("li", "", "A second, shorter limit isn't reported."));
      }
      const checked = el("li", "");
      if (parseIso(allowance.observed_at) !== null) checked.append("Checked ", time(allowance.observed_at, "relative"), ".");
      else checked.append("No allowance reading yet.");
      facts.append(checked);
      if (hasText(ai.plain)) {
        const state = el("li", "", "AI: ");
        renderText(state, ai.plain);
        facts.append(state);
      }
      facts.append(el("li", "", "Your own use of the same AI subscription counts against this allowance."));
      root.append(facts);
    },
  });

  // --- AI spending (T40): real costs from receipts per department, the testing alert and the prepaid credit ---
  // Every figure and sentence comes from the server (contract ai_spending). A part that is null has no call of
  // that kind and is left out; an unresolved hold that can't be valued says so. Nothing here is ever "$0.00"
  // for a cost that isn't known, and a cost under half a cent reads "under $0.01".

  const underACent = value => Number(value?.amount) > 0 && Number(value?.amount) < 0.005;
  function spendMoney(value) {
    if (!value || typeof value.display !== "string") return null;
    return underACent(value) ? el("span", "hq-money", "under $0.01") : money(value);
  }
  function spendAmounts(amounts) {
    const box = el("dl", "hq-spend-amounts");
    const add = (label, ...value) => {
      const item = el("div", "hq-spend-amount");
      const name = el("dt", "");
      name.append(label);
      const data = el("dd", "");
      data.append(...value);
      item.append(name, data);
      box.append(item);
    };
    if (amounts.actual) add("From receipts", spendMoney(amounts.actual));
    if (amounts.estimated) add(term("estimated_cost", "Estimated"), spendMoney(amounts.estimated));
    const open = HQ.count(amounts.unresolved_calls) || 0;
    if (open > 0) {
      const held = spendMoney(amounts.unresolved);
      if (held) add(term("unresolved_cost", "Not known yet"), ...(underACent(amounts.unresolved) ? [] : ["up to "]), held,
        ` (${plural(open, "call")})`);
      else add(term("unresolved_cost", "Not known yet"), `${plural(open, "call")}, cost can't be worked out yet`);
    }
    add("Calls", el("span", "hq-num", str(HQ.count(amounts.calls) ?? "Not known")));
    return box;
  }
  function spendService(service) {
    const box = el("section", "hq-spend");
    box.dataset.service = str(service.service);
    box.dataset.status = str(service.status);
    box.append(el("h3", "hq-subhead", str(service.name)));
    if (hasText(service.headline)) box.append(textEl("p", "hq-spend-headline", service.headline));
    const alert = obj(service.alert);
    if (hasText(alert.text)) {
      const note = el("p", "hq-spend-alert");
      note.dataset.state = str(alert.state);
      note.append(icon(alert.state === "below" ? "sev-info" : "sev-watch", "hq-icon hq-icon-small"), " ");
      renderText(note, alert.text);
      box.append(note);
    }
    if (hasText(service.monthly_limit?.text)) box.append(textEl("p", "hq-spend-limit", service.monthly_limit.text));
    if (hasText(service.credit?.text)) box.append(textEl("p", "hq-spend-credit", service.credit.text));
    const lines = list(service.lines);
    if (lines.length) {
      const rows = el("ul", "hq-spend-lines");
      for (const line of lines) {
        const row = el("li", "hq-spend-line");
        row.dataset.line = str(line.id);
        row.append(el("p", "hq-spend-name", str(line.name)), spendAmounts(obj(line.amounts)));
        rows.append(row);
      }
      box.append(rows);
    }
    // Explanations follow from the figures themselves, so the server sends no copy for them.
    const total = obj(service.total);
    const month = obj(service.monthly_limit?.month);
    if ((HQ.count(total.unresolved_calls) || 0) > 0 || (HQ.count(month.unresolved_calls) || 0) > 0) {
      box.append(el("p", "hq-muted", "Calls whose use isn't known yet count at the most they could cost, never as free."));
    }
    if (total.estimated) {
      box.append(el("p", "hq-muted", "Estimated costs are calls charged at the most they could cost, because their use was never reported."));
    }
    const tests = HQ.count(service.synthetic_calls) || 0;
    if (tests > 0) {
      box.append(el("p", "hq-muted", tests === 1 ? "1 test call with made-up answers isn't counted." :
        `${tests} test calls with made-up answers aren't counted.`));
    }
    return box;
  }
  function codexUse(codex) {
    const box = el("section", "hq-spend hq-codex");
    box.append(el("h3", "hq-subhead", "AI allowance by department"));
    if (hasText(codex.text)) box.append(textEl("p", "hq-spend-headline", codex.text));
    const lines = list(codex.lines);
    if (lines.length) {
      const rows = el("ul", "hq-spend-lines");
      for (const line of lines) {
        const row = el("li", "hq-spend-line");
        row.dataset.line = str(line.id);
        const turns = HQ.count(line.turns) ?? 0;
        const share = percentText(line.share_percent);
        const words = [plural(turns, "turn"), share ? `${share} of the team's reported use` : "use not reported"];
        const unreported = HQ.count(line.unreported_turns) || 0;
        if (unreported > 0 && share) words.push(`${unreported} not reported`);
        row.append(el("p", "hq-spend-name", str(line.name)), el("p", "hq-muted", words.join(" · ")));
        rows.append(row);
      }
      box.append(rows);
    }
    box.append(el("p", "hq-muted", "The allowance is shared with your own ChatGPT use, so each share is of the team's reported use, not of the allowance."));
    const unreported = HQ.count(codex.unreported_turns) || 0;
    if (unreported > 0) {
      box.append(el("p", "hq-muted", unreported === 1 ? "1 turn didn't report its use; shares count only what was reported." :
        `${unreported} turns didn't report their use; shares count only what was reported.`));
    }
    return box;
  }

  HQ.view("spending", {
    deps: snapshot => [snapshot.ai_spending ?? null],
    render(root, snapshot) {
      const block = snapshot.ai_spending;
      if (!block || typeof block !== "object") {
        root.append(el("p", "hq-muted", "AI spending isn't available from the server right now. The Costs page has every recorded cost."));
        return;
      }
      for (const service of list(block.services)) if (service && typeof service === "object") root.append(spendService(service));
      root.append(codexUse(obj(block.codex)));
      if (hasText(block.paper_note)) root.append(textEl("p", "hq-spend-note", block.paper_note));
      const more = obj(block.link);
      if (more.href) root.append(link(more.href, `${str(more.label)} ›`, "hq-more-link"));
    },
  });

  // --- Today's trading (W8-C): the daily loss limit, the scorecard and the price collector ----------------------
  // Every figure and sentence comes from the server (contract trading); an older server leaves the block out. A state
  // always has a word and a shape, never colour alone.

  const tradingBlock = snapshot => snapshot.trading && typeof snapshot.trading === "object" ? snapshot.trading : null;
  const LOSS_CHIP = Object.freeze({
    within: ["Within the limit", "hq-chip-soft"], reached: ["Limit reached", "hq-chip-warn"],
    stopped: ["No new buying today", "hq-chip-warn"], unknown: ["Can't be checked now", "hq-chip-warn"],
    not_recorded: ["Not recorded yet", "hq-chip-soft"], not_applied: ["Not measured here", "hq-chip-soft"],
  });
  function notServed(root, what) {
    root.append(el("p", "hq-muted", `${what} isn't available from the server right now.`));
  }
  function lossMeter(loss) {
    const used = Math.max(0, Math.min(100, loss.used_percent));
    const wrap = el("div", "hq-loss-meter");
    const meter = el("meter", "hq-meter hq-meter-loss", `${percentText(used)}`);
    meter.min = 0;
    meter.max = 100;
    meter.low = 50;
    meter.high = 80;
    meter.optimum = 0;
    meter.value = used;
    meter.setAttribute("aria-label", `${percentText(used)} of today's loss limit used`);
    wrap.append(meter, el("p", "hq-muted hq-loss-used", `${percentText(used)} of the limit used`));
    return wrap;
  }
  HQ.view("loss", {
    deps: snapshot => [tradingBlock(snapshot)?.loss ?? null],
    render(root, snapshot) {
      const loss = obj(tradingBlock(snapshot)?.loss);
      if (!loss.state) return notServed(root, "Today's loss limit");
      const head = el("p", "hq-loss-head");
      head.dataset.state = str(loss.state);
      const [label, tone] = LOSS_CHIP[loss.state] || ["", "hq-chip-soft"];
      if (label) head.append(el("span", `hq-chip ${tone}`, label), " ");
      renderText(head, loss.headline);
      root.append(head);
      if (typeof loss.used_percent === "number" && Number.isFinite(loss.used_percent)) root.append(lossMeter(loss));
      if (loss.day_start_value) {
        const grid = el("div", "hq-stats hq-loss-stats");
        grid.append(stat("At the day start", money(loss.day_start_value)), stat("Now", money(loss.value)));
        if (loss.limit_amount) grid.append(stat(term("loss_limit", "Daily loss limit"), money(loss.limit_amount)));
        if (parseIso(loss.resumes_at) !== null) grid.append(stat("Next day start", time(loss.resumes_at, "day_clock")));
        root.append(grid);
      }
      if (hasText(loss.detail)) root.append(textEl("p", "hq-muted hq-loss-detail", loss.detail));
    },
  });

  function beatsChip(beats, ahead, behind, unknown) {
    if (beats === true) return el("span", "hq-chip hq-chip-safe hq-chip-soft", `✓ ${ahead}`);
    if (beats === false) return el("span", "hq-chip hq-chip-warn", `✗ ${behind}`);
    return el("span", "hq-chip hq-chip-soft", `? ${unknown}`);
  }
  HQ.view("scorecard", {
    deps: snapshot => [tradingBlock(snapshot)?.scorecard ?? null],
    render(root, snapshot) {
      const card = obj(tradingBlock(snapshot)?.scorecard);
      if (typeof card.available !== "boolean") return notServed(root, "The scorecard");
      const head = el("p", "hq-scorecard-head");
      if (card.status === "provisional") head.append(el("span", "hq-chip hq-chip-warn", "Provisional"), " ");
      renderText(head, card.headline);
      root.append(head);
      if (!card.available) {
        if (hasText(card.unavailable_reason)) root.append(textEl("p", "hq-muted", card.unavailable_reason));
      } else {
        const beats = el("p", "hq-scorecard-beats");
        beats.append(beatsChip(card.beats_cash, "Beats cash", "Behind cash", "Cash"), " ",
          beatsChip(card.beats_btc, "Beats holding Bitcoin", "Behind holding Bitcoin", "Bitcoin can't be compared"));
        root.append(beats);
        const facts = list(card.facts);
        if (facts.length) {
          const grid = el("div", "hq-stats hq-scorecard-facts");
          for (const fact of facts) grid.append(stat(str(fact.label), textEl("span", "", fact.value)));
          root.append(grid);
        }
        const lines = list(card.lines);
        if (lines.length) {
          const rows = el("ul", "hq-plain-list hq-scorecard-lines");
          for (const line of lines) rows.append(textEl("li", "", line));
          root.append(rows);
        }
        const reasons = list(card.provisional);
        if (reasons.length) {
          const why = HQ.keepOpen(el("details", "hq-scorecard-why"), "scorecard-provisional");
          why.append(el("summary", "", "Why it's provisional"));
          const rows = el("ul", "hq-plain-list");
          for (const reason of reasons) rows.append(textEl("li", "", reason));
          const more = HQ.count(card.more_provisional) || 0;
          if (more > 0) rows.append(el("li", "hq-muted", `And ${plural(more, "more reason")}.`));
          why.append(rows);
          root.append(why);
        }
      }
      if (hasText(card.note)) root.append(textEl("p", "hq-scorecard-note", card.note));
    },
  });

  const COLLECTOR_CHIP = Object.freeze({
    ok: ["Working", "hq-chip-soft"], attention: ["Needs a look", "hq-chip-warn"], off: ["Switched off", "hq-chip-soft"],
    no_records: ["No records", "hq-chip-soft"],
  });
  HQ.view("collector", {
    deps: snapshot => [tradingBlock(snapshot)?.collector ?? null],
    render(root, snapshot) {
      const collector = obj(tradingBlock(snapshot)?.collector);
      if (!collector.state) return notServed(root, "The price collector's status");
      const head = el("p", "hq-collector-head");
      head.dataset.state = str(collector.state);
      const [label, tone] = COLLECTOR_CHIP[collector.state] || ["", "hq-chip-soft"];
      if (label) head.append(el("span", `hq-chip ${tone}`, label), " ");
      renderText(head, collector.headline);
      root.append(head);
      const lines = list(collector.lines);
      if (lines.length) {
        const rows = el("ul", "hq-plain-list hq-collector-lines");
        for (const line of lines) rows.append(textEl("li", "", line));
        root.append(rows);
      }
    },
  });

  const TREND = Object.freeze({below: "↘", above: "↗", level: "→", not_enough_history: "·"});
  const COIN = Object.freeze({"BTC/USD": "Bitcoin", "ETH/USD": "Ether"});
  HQ.view("market", {
    deps: snapshot => snapshot.market,
    render(root, snapshot) {
      const items = list(snapshot.market);
      if (!items.length) {
        root.append(empty("No prices are recorded yet."));
        return;
      }
      const grid = el("div", "hq-market-grid");
      for (const item of items) {
        const card = el("article", "hq-market");
        card.dataset.trend = str(item.trend);
        const head = el("p", "hq-market-head");
        head.append(el("span", "hq-market-symbol", str(item.symbol)), el("span", "hq-muted", COIN[item.symbol] || ""));
        const price = el("p", "hq-market-price");
        price.append(money(item.price) || el("span", "hq-unknown", "No price yet"));
        card.append(head, price);
        if (parseIso(item.price_at) !== null) {
          const age = el("p", "hq-muted", "Price from ");
          age.append(time(item.price_at, "relative"));
          card.append(age);
        }
        const averages = el("p", "hq-market-avg");
        averages.append(term("avg_20h", "20-hour average"), " ", money(item.avg_20h) || "not known", " · ",
          term("avg_50h", "50-hour average"), " ", money(item.avg_50h) || "not known");
        card.append(averages);
        const trend = el("p", "hq-market-trend");
        const arrow = el("span", "hq-trend-mark", TREND[item.trend] || "·");
        arrow.setAttribute("aria-hidden", "true");
        trend.append(arrow);
        renderText(trend, item.text);
        card.append(trend);
        grid.append(card);
      }
      root.append(grid);
    },
  });

  // --- Help (Decide tab) --------------------------------------------------------------------------------

  HQ.view("help", {
    deps: snapshot => [snapshot.links, snapshot.mode],
    render(root, snapshot) {
      const toggle = el("label", "hq-switch");
      const input = el("input", "");
      input.type = "checkbox";
      input.checked = HQ.technical();
      input.dataset.focusKey = "technical-toggle";
      input.addEventListener("change", () => HQ.setTechnical(input.checked));
      const words = el("span", "hq-switch-words");
      words.append(el("span", "hq-switch-title", "Show technical names"), el("span", "hq-muted", "Adds the raw record names in [brackets]. Stored on this device only."));
      toggle.append(input, words);
      root.append(toggle);
      const welcome = button("button button-secondary", "Show the welcome again");
      welcome.dataset.focusKey = "welcome-again";
      welcome.addEventListener("click", () => {
        HQ.store.remove(HQ.KEYS.welcome);
        HQ.rerender("welcome");
        doc.getElementById("hq")?.scrollIntoView({behavior: "smooth"});
        doc.querySelector('.hq-tabs a[href="#hq"]')?.click();
      });
      root.append(welcome);
      const links = obj(snapshot.links);
      const views = el("section", "hq-help-block");
      views.append(el("h3", "hq-subhead", "Detailed views"));
      const rows = el("ul", "hq-link-list");
      for (const [key, label] of [["journal", "Account journal (detailed numbers)"], ["mission_control", "Mission Control (build progress & tests)"],
        ["trading", "Trading records"], ["organisation", "Organisation records"], ["costs", "Costs"],
        ["improvements", "Improvements"], ["owner", "Owner controls"]]) {
        if (!HQ.safePath(links[key])) continue;
        const row = el("li", "");
        row.append(link(links[key], `${label} ›`, "hq-more-link"));
        rows.append(row);
      }
      views.append(rows);
      if (HQ.safePath(links.review_export)) {
        const report = link(links.review_export, "Download the full report for a review", "button button-secondary");
        report.download = "trade-graph-review.json";
        views.append(report);
      }
      root.append(views);
      const live = snapshot.mode === "live";
      const sections = HQ.keepOpen(el("details", "hq-help-block"), "help:sections");
      sections.append(el("summary", "", "What each part of HQ shows"));
      const list_ = el("dl", "hq-glossary");
      for (const entry of Object.values(glossary()?.sectionHelp(live) || {})) {
        const row = el("div", "hq-glossary-row");
        row.append(el("dt", "", entry.title), el("dd", "", entry.text));
        list_.append(row);
      }
      sections.append(list_);
      root.append(sections);
      const words_ = HQ.keepOpen(el("details", "hq-help-block"), "help:glossary");
      words_.append(el("summary", "", "Glossary: every underlined word"));
      const entries = glossary()?.glossaryList(live);
      if (entries) words_.append(entries);
      root.append(words_);
    },
  });

  // --- Tabs (phones) ---------------------------------------------------------------------------------

  function initTabs() {
    const nav = doc.querySelector(".hq-tabs");
    const anchors = [...doc.querySelectorAll(".hq-tabs a[href^='#'], .hq-anchors a[href^='#']")];
    const tabs = nav ? [...nav.querySelectorAll("a[href^='#']")] : [];
    const tabMode = () => globalThis.matchMedia?.("(max-width: 767px)").matches;
    function setTab(id, save = true) {
      if (!SECTIONS.includes(id)) return;
      for (const section of doc.querySelectorAll("[data-hq-section]")) section.classList.toggle("is-active", section.id === id);
      for (const anchor of anchors) {
        if (anchor.hash === `#${id}`) anchor.setAttribute("aria-current", "page");
        else anchor.removeAttribute("aria-current");
      }
      if (save) HQ.store.set(HQ.KEYS.tab, id);
    }
    const fromHash = location.hash.slice(1);
    const stored = HQ.store.get(HQ.KEYS.tab);
    setTab(SECTIONS.includes(fromHash) ? fromHash : SECTIONS.includes(stored) ? stored : "hq", false);
    nav?.addEventListener("click", event => {
      const anchor = event.target instanceof Element ? event.target.closest("a[href^='#']") : null;
      if (!anchor) return;
      const id = anchor.hash.slice(1);
      if (!SECTIONS.includes(id)) return;
      event.preventDefault();
      setTab(id);
      history.replaceState(null, "", `#${id}`);
      if (tabMode()) globalThis.scrollTo({top: 0});
      else doc.getElementById(id)?.scrollIntoView();
    });
    nav?.addEventListener("keydown", event => {
      const index = tabs.indexOf(doc.activeElement);
      if (index < 0) return;
      let next = null;
      if (event.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
      else if (event.key === "ArrowLeft") next = tabs[(index - 1 + tabs.length) % tabs.length];
      else if (event.key === "Home") next = tabs[0];
      else if (event.key === "End") next = tabs[tabs.length - 1];
      if (!next) return;
      event.preventDefault();
      next.focus();
      next.click();
    });
    globalThis.addEventListener("hashchange", () => setTab(location.hash.slice(1)));
    doc.addEventListener("click", event => {
      const anchor = event.target instanceof Element ? event.target.closest("a[href^='#']") : null;
      if (!anchor || anchor.closest(".hq-tabs")) return;
      const id = anchor.hash.slice(1);
      if (SECTIONS.includes(id) && tabMode()) {
        event.preventDefault();
        if (HQ.sheet.isOpen()) HQ.sheet.close();
        setTab(id);
        history.replaceState(null, "", `#${id}`);
        globalThis.scrollTo({top: 0});
      }
    });
  }

  // --- Desktop balance ---------------------------------------------------------------------------------
  // On desktop, Right now, the map, Calendar and Story run down the left; Needs you, Up next, Money and Decide
  // down the right; Prompt settings and Help & glossary form a row below both (hq.css). In quiet or empty states
  // the right column ran up to 1 600 px below Story, leaving a blank under it. While it ends more than
  // BALANCE_SLACK px below Story, Decide's last panels (Your bills, then Review check-ins) join the row, stacked
  // in its first third. They go just before Prompt settings, so the document order (phones, iPads, keyboard,
  // screen readers) and the anchors never change. Only from 1200 px: narrower, a third is too tight for the
  // prompt fields, so iPads in landscape keep the usual two-column row.

  const BALANCE_SLACK = 300;
  const DESKTOP = "(min-width: 1200px)";
  let balanceWaiting = false;
  function balanceDecide() {
    const section = doc.getElementById("decide");
    const row = section?.querySelector(".hq-decide-more");
    const story = doc.getElementById("story");
    if (!row || !story) return 0;
    const movable = [section.querySelector(".hq-review"), section.querySelector(".hq-bills")].filter(Boolean);
    // Moving a panel would take the focus out of a field being typed in; try again once it leaves.
    balanceWaiting = movable.some(panel => panel.contains(doc.activeElement));
    if (balanceWaiting) return null;
    let count = 0;
    if (globalThis.matchMedia?.(DESKTOP)?.matches) {
      const box = node => node.getBoundingClientRect();
      const first = section.querySelector(".hq-panel");
      const gap = (first && typeof globalThis.getComputedStyle === "function"
        ? parseFloat(globalThis.getComputedStyle(first).marginBottom) : NaN) || 20;
      const size = panel => (panel.hidden ? 0 : box(panel).height + gap);
      const right = [...doc.querySelectorAll(".hq-board > .hq-side, #money > .hq-panel, #decide > .hq-panel")].filter(node => !node.hidden);
      if (!right.length) return 0;
      // How far the right column would end below Story with every panel in its usual place.
      let excess = Math.max(...right.map(node => box(node).bottom)) - box(story).bottom;
      for (const panel of movable) if (panel.parentNode === row) excess += size(panel);
      for (const panel of [...movable].reverse()) {
        if (excess <= BALANCE_SLACK) break;
        count += 1;
        excess -= size(panel);
      }
    }
    const moving = movable.slice(movable.length - count);
    movable.forEach((panel, index) => {
      const inRow = moving.includes(panel);
      if (panel.parentNode === (inRow ? row : section)) return;
      if (!inRow) { section.insertBefore(panel, row); return; }
      const next = movable.slice(index + 1).find(other => other.parentNode === row) || row.querySelector(".hq-prompt");
      row.insertBefore(panel, next);
    });
    if (count) row.setAttribute("data-moved", String(count));
    else row.removeAttribute("data-moved");
    return count;
  }
  function initBalance() {
    let frame = null;
    const run = () => { frame = null; balanceDecide(); };
    const schedule = () => {
      if (frame !== null) return;
      if (typeof globalThis.requestAnimationFrame === "function") frame = globalThis.requestAnimationFrame(run);
      else run();
    };
    // Any change in either column's height: a render, an opened panel, a new width, fonts arriving.
    if (typeof globalThis.ResizeObserver === "function") {
      const observer = new globalThis.ResizeObserver(schedule);
      for (const node of doc.querySelectorAll(".hq-board > *, #calendar, #story, #money > .hq-panel, #decide > .hq-panel")) observer.observe(node);
    }
    globalThis.matchMedia?.(DESKTOP)?.addEventListener?.("change", schedule);
    HQ.on("snapshot", schedule);
    doc.addEventListener("focusout", () => { if (balanceWaiting) schedule(); });
    schedule();
  }

  HQ.define("render", {openRoom, initTabs, initBalance, balanceDecide, cadence, deptName, findDecision, lightBadge, LIGHT_LABEL, time,
    liveTime, chip, empty, plural, sinceFacts, seriesTimes, estimateSeries});
})();
