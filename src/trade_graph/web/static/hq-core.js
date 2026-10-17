/* Trade Graph HQ core: namespace, storage, time words, text segments, network, sheet, toast and polling.
   The server projects every state. This file only formats it and moves it; it never infers state. */
"use strict";

(() => {
  if (globalThis.TradeGraphHQ) return;
  const doc = document;
  // Tab mode and the JS-only controls switch on with this class (hq.css).
  doc.documentElement.classList.add("hq-js");

  const SCHEMA = "trade-graph.hq.v1";
  const ISO_RE = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,6}))?Z$/;
  // A saved "outcome unknown" request older than this is dropped instead of being resent (review S4).
  const PENDING_MAX_AGE_MS = 86400000;
  // Connection states that stop polling until the page is reloaded or the owner signs in again.
  const HALTED = new Set(["signed_out", "forbidden", "outdated"]);
  // Same expression as hq_contract.ALLOWED_PATH_PATTERN; links from JSON are rendered only when they match.
  const ALLOWED_PATH = /^\/(?:$|journal$|progress$|trading$|organization$|costs$|changes(?:\/[^/]+)?$|owner$|decisions\/[^/]+$|api\/v1\/review-export$)/;
  const KEYS = Object.freeze({
    welcome: "tg-hq-welcome-v1",
    lastSeen: "tg-hq-last-seen",
    technical: "tg-hq-technical",
    promptSettings: "tg-hq-prompt-settings",
    tab: "tg-hq-tab",
    pending: id => `tg-hq-pending:${id}`,
  });

  /* store:begin — the only code on this page that touches browser storage. */
  function area(kind) {
    try { return kind === "session" ? globalThis.sessionStorage : globalThis.localStorage; } catch (_) { return null; }
  }
  const store = Object.freeze({
    get(key, kind) {
      try {
        const value = area(kind)?.getItem(key);
        return typeof value === "string" ? value : null;
      } catch (_) { return null; }
    },
    set(key, value, kind) {
      try { area(kind)?.setItem(key, String(value)); return true; } catch (_) { return false; }
    },
    remove(key, kind) {
      try { area(kind)?.removeItem(key); } catch (_) { /* Optional preference. */ }
    },
    getJson(key, kind) {
      const raw = store.get(key, kind);
      if (raw === null) return null;
      try { return JSON.parse(raw); } catch (_) { return null; }
    },
    setJson(key, value, kind) {
      try { return store.set(key, JSON.stringify(value), kind); } catch (_) { return false; }
    },
  });
  /* store:end */

  // --- Saved requests whose outcome is unknown (tg-hq-pending:<id>) -----------------------------------
  // An entry without a readable saved_at, or older than a day, is removed and never resent: an old
  // "Start" replayed days later would otherwise be reported as a fresh success.

  function readPending(id) {
    const key = KEYS.pending(id);
    const stored = store.getJson(key);
    if (!stored || typeof stored !== "object") return null;
    const saved = parseIso(stored.saved_at);
    const age = saved === null ? null : serverNow() - saved;
    if (age === null || age > PENDING_MAX_AGE_MS || age < -600000) {
      store.remove(key);
      return null;
    }
    return stored;
  }
  function savePending(id, value) {
    return store.setJson(KEYS.pending(id), {...value, saved_at: toIso(serverNow())});
  }
  function clearPending(id) { store.remove(KEYS.pending(id)); }

  // --- Small helpers ---------------------------------------------------------------------------

  const list = value => Array.isArray(value) ? value : [];
  const obj = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const str = value => typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
  const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;

  function randomBytes(length) {
    const source = globalThis.crypto;
    if (!source || typeof source.getRandomValues !== "function") throw new Error("This browser has no secure random source.");
    return source.getRandomValues(new Uint8Array(length));
  }
  function randomHex(length) {
    return [...randomBytes(Math.ceil(length / 2))].map(byte => byte.toString(16).padStart(2, "0")).join("").slice(0, length);
  }
  // crypto.randomUUID needs a secure context; plain-HTTP LAN pages (an iPad at home) fall back to RFC 4122 v4.
  function uuid() {
    const source = globalThis.crypto;
    if (source && typeof source.randomUUID === "function") {
      try { return source.randomUUID(); } catch (_) { /* Fall through to getRandomValues. */ }
    }
    const bytes = randomBytes(16);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  // --- Time -----------------------------------------------------------------------------------

  function parseIso(value) {
    if (typeof value !== "string") return null;
    const match = ISO_RE.exec(value);
    if (!match) return null;
    const millis = match[7] ? Number(match[7].padEnd(3, "0").slice(0, 3)) : 0;
    const result = Date.UTC(+match[1], +match[2] - 1, +match[3], +match[4], +match[5], +match[6], millis);
    return Number.isFinite(result) ? result : null;
  }
  const isIso = value => parseIso(value) !== null;
  const toIso = ms => new Date(ms).toISOString();

  const clockFormat = new Intl.DateTimeFormat(undefined, {hour: "numeric", minute: "2-digit"});
  const weekdayFormat = new Intl.DateTimeFormat("en-GB", {weekday: "short"});
  const dayMonthFormat = new Intl.DateTimeFormat("en-GB", {day: "numeric", month: "short"});
  const monthYearFormat = new Intl.DateTimeFormat("en-GB", {month: "long", year: "numeric"});
  const pad = number => String(number).padStart(2, "0");

  function serverNow() { return Date.now() + state.skew; }
  function dayStart(ms) { const date = new Date(ms); date.setHours(0, 0, 0, 0); return date.getTime(); }
  function dayDiff(ms, now = serverNow()) { return Math.round((dayStart(ms) - dayStart(now)) / 86400000); }
  function localDayKey(ms) { const date = new Date(ms); return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`; }
  function clock(ms) { return clockFormat.format(ms); }
  function weekday(ms) { return weekdayFormat.format(ms); }
  function dateWords(ms) {
    const date = new Date(ms);
    const text = `${weekdayFormat.format(date)} ${dayMonthFormat.format(date)}`;
    return date.getFullYear() === new Date(serverNow()).getFullYear() ? text : `${text} ${date.getFullYear()}`;
  }
  function monthWords(ms) { return monthYearFormat.format(ms); }
  function dayClock(ms) {
    const diff = dayDiff(ms);
    const time = clock(ms);
    if (diff === 0) return `today ${time}`;
    if (diff === 1) return `tomorrow ${time}`;
    if (diff === -1) return `yesterday ${time}`;
    if (Math.abs(diff) <= 6) return `${weekday(ms)} ${time}`;
    return `${dateWords(ms)}, ${time}`;
  }
  function span(hours, minutes) { return minutes ? `${hours} h ${minutes} min` : `${hours} h`; }
  function rel(ms) {
    const delta = ms - serverNow();
    const size = Math.abs(delta);
    if (size < 45000) return "just now";
    if (size < 3600000) {
      const minutes = Math.min(59, Math.max(1, Math.round(size / 60000)));
      return delta > 0 ? `in ${minutes} min` : `${minutes} min ago`;
    }
    if (size < 86400000) {
      let hours = Math.floor(size / 3600000);
      let minutes = Math.round((size - hours * 3600000) / 60000);
      if (minutes === 60) { hours += 1; minutes = 0; }
      return delta > 0 ? `in ${span(hours, minutes)}` : `${span(hours, minutes)} ago`;
    }
    return dayClock(ms);
  }
  function relClock(ms) {
    const delta = ms - serverNow();
    if (Math.abs(delta) >= 86400000) return dayClock(ms);
    const at = dayDiff(ms) === 0 ? clock(ms) : dayClock(ms);
    if (Math.abs(delta) < 45000) return `just now (${at})`;
    return delta > 0 ? `${rel(ms)} (${at})` : `${at} (${rel(ms)})`;
  }
  function styled(ms, style) {
    switch (style) {
      case "relative": return rel(ms);
      case "clock": return clock(ms);
      case "day_clock": return dayClock(ms);
      case "date": return dateWords(ms);
      default: return relClock(ms);
    }
  }
  function utcClock(ms) { const date = new Date(ms); return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`; }
  function utcStamp(ms) {
    const date = new Date(ms);
    return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${utcClock(ms)}`;
  }
  function localDateTime(ms) {
    const date = new Date(ms);
    return `${weekdayFormat.format(date)} ${dayMonthFormat.format(date)} ${date.getFullYear()}, ${clock(ms)}`;
  }
  // "Fri 9 Oct 2026, 20:56 your time · 17:56 UTC" — replaces title tooltips, which touch screens can't show.
  function fullTime(ms) {
    const date = new Date(ms);
    const sameDay = date.getUTCDate() === date.getDate() && date.getUTCMonth() === date.getMonth();
    const utcDay = sameDay ? "" : ` (${weekdayFormat.format(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), 12))} ${date.getUTCDate()})`;
    return `${localDateTime(ms)} your time · ${utcClock(ms)} UTC${utcDay}`;
  }
  function duration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total} s`;
    if (total < 3600) return `${Math.floor(total / 60)} min`;
    if (total < 86400) return span(Math.floor(total / 3600), Math.floor((total % 3600) / 60));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    return hours ? `${days} d ${hours} h` : `${days} d`;
  }
  function timeZone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "local time"; } catch (_) { return "local time"; }
  }

  // --- DOM ------------------------------------------------------------------------------------

  function el(tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null && text !== "") node.textContent = String(text);
    return node;
  }
  function button(className, text) {
    const node = el("button", className, text);
    node.type = "button";
    return node;
  }
  function safePath(value) { return typeof value === "string" && ALLOWED_PATH.test(value) ? value : null; }
  function link(href, text, className) {
    const path = safePath(href);
    if (!path) return el("span", className, text);
    const node = el("a", className, text);
    node.href = path;
    return node;
  }
  // SVG icons are cloned from a <template> in hq.html, so no namespace URL appears in script.
  function icon(name, className = "hq-icon") {
    const source = doc.querySelector("template[data-hq-icon]")?.content?.firstElementChild;
    if (!source || !/^[a-z0-9_-]+$/.test(name)) return el("span", className);
    const svg = source.cloneNode(true);
    svg.setAttribute("class", className);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.querySelector("use")?.setAttribute("href", `/static/hq-icons.svg#${name}`);
    return svg;
  }
  function withCode(node, code) {
    if (!state.technical || typeof code !== "string" || !code) return node;
    const fragment = doc.createDocumentFragment();
    fragment.append(node, el("span", "hq-code", ` [${code}]`));
    return fragment;
  }
  const TIME_STYLES = new Set(["relative", "clock", "day_clock", "relative_clock", "date"]);
  function timeNode(at, style, inert) {
    const ms = parseIso(at);
    if (ms === null) return doc.createTextNode("an unrecorded time");
    const chosen = TIME_STYLES.has(style) ? style : "relative_clock";
    const time = el("time", inert ? "hq-time-text" : "", styled(ms, chosen));
    time.dateTime = at;
    time.dataset.at = at;
    time.dataset.style = chosen;
    if (inert) return time;
    const wrap = el("span", "hq-time-wrap");
    const control = button("hq-time");
    control.setAttribute("aria-expanded", "false");
    control.append(time);
    const full = el("span", "hq-time-full", fullTime(ms));
    full.hidden = true;
    wrap.append(control, full);
    return wrap;
  }
  function segmentNode(segment, inert) {
    if (!segment || typeof segment !== "object") return null;
    switch (segment.t) {
      case "text": return withCode(doc.createTextNode(str(segment.v)), segment.code);
      case "time": return timeNode(segment.at, segment.style, inert);
      case "duration": return el("span", "hq-num", duration(segment.seconds));
      case "money": {
        const node = el("span", "hq-money", str(segment.display));
        return withCode(node, state.technical ? `${str(segment.amount)} ${str(segment.currency)}` : null);
      }
      case "percent": return el("span", "hq-num", str(segment.display));
      case "term": {
        const key = /^[a-z0-9_]+$/.test(str(segment.key)) ? segment.key : "";
        const node = inert || !key ? el("span", "hq-term-text", str(segment.v)) : button("hq-term", str(segment.v));
        if (key) node.dataset.term = key;
        return withCode(node, segment.code);
      }
      case "link": return link(segment.href, str(segment.v), "hq-link");
      default: return null;
    }
  }
  // A tappable time or term is a button, and browsers may wrap before or after a button even next to "(" or
  // ")", which left "(tomorrow 3:00" on one line and ")." alone on the next. Opening punctuation just before a
  // time, term or amount, and closing punctuation just after it, stay on its line.
  const OPENING = /[(\[‘“]+$/;
  const CLOSING = /^[)\].,;:!?’”]+/;
  const KEEPS_PUNCTUATION = new Set(["time", "term", "money"]);
  function renderText(container, text, options = {}) {
    const items = [];
    for (const segment of list(text)) {
      const node = segmentNode(segment, Boolean(options.inert));
      if (!node) continue;
      items.push(segment.t === "text" ? {words: str(segment.v), code: segment.code} : {node, keeps: KEEPS_PUNCTUATION.has(segment.t)});
    }
    items.forEach((item, index) => {
      if (!item.keeps) return;
      const before = items[index - 1];
      const after = items[index + 1];
      // A technical code stays right after its own words, so their opening punctuation stays with them too.
      const lead = before?.words !== undefined && !(state.technical && before.code) ? OPENING.exec(before.words)?.[0] || "" : "";
      const trail = after?.words !== undefined ? CLOSING.exec(after.words)?.[0] || "" : "";
      if (!lead && !trail) return;
      if (lead) before.words = before.words.slice(0, -lead.length);
      if (trail) after.words = after.words.slice(trail.length);
      const together = el("span", "hq-nobreak");
      together.append(...[lead, item.node, trail].filter(part => part !== ""));
      item.node = together;
    });
    for (const item of items) {
      if (item.words === undefined) container.append(item.node);
      else if (item.words || (state.technical && item.code)) container.append(withCode(doc.createTextNode(item.words), item.code));
    }
    return container;
  }
  function textEl(tag, className, text, options) { return renderText(el(tag, className), text, options); }
  // Plain words for prompts and summaries; times use their own style.
  function plain(text) {
    return list(text).map(segment => {
      if (!segment || typeof segment !== "object") return "";
      if (segment.t === "time") { const ms = parseIso(segment.at); return ms === null ? "an unrecorded time" : styled(ms, segment.style); }
      if (segment.t === "duration") return duration(segment.seconds);
      if (segment.t === "money" || segment.t === "percent") return str(segment.display);
      return str(segment.v);
    }).join("").replace(/\s+/g, " ").trim();
  }
  const hasText = text => list(text).some(segment => segment && typeof segment === "object");

  // --- State and events -----------------------------------------------------------------------

  const state = {
    snapshot: null,
    skew: 0,
    connection: "ok",        // ok | failed | server_error | signed_out | forbidden | outdated
    technical: store.get(KEYS.technical) === "1",
    calendarDays: 7,
    lastSeen: null,
    sheet: false,
  };
  const seen = store.get(KEYS.lastSeen);
  state.lastSeen = isIso(seen) ? seen : null;

  const listeners = new Map();
  function on(event, callback) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(callback);
  }
  function emit(event, payload) {
    for (const callback of listeners.get(event) || []) {
      try { callback(payload); } catch (error) { console.error(error); }
    }
  }
  function validSnapshot(data) {
    return Boolean(data) && typeof data === "object" && data.schema === SCHEMA && isIso(data.as_of);
  }
  function setSnapshot(data, receivedAt = Date.now()) {
    if (!validSnapshot(data)) return false;
    state.snapshot = data;
    state.skew = parseIso(data.as_of) - receivedAt;
    renderAll();
    emit("snapshot", data);
    freshness();
    return true;
  }
  function setConnection(value) {
    if (state.connection === value) return;
    state.connection = value;
    freshness();
    emit("connection", value);
  }
  function setTechnical(value) {
    state.technical = Boolean(value);
    store.set(KEYS.technical, state.technical ? "1" : "0");
    renderAll(true);
  }

  // --- Views: one renderer per [data-hq="name"] container ----------------------------------------

  const views = new Map();
  function view(name, spec) { views.set(name, {deps: spec.deps || (snapshot => snapshot), render: spec.render, sig: null, version: 0}); }

  // What the viewer expanded survives every re-render (review T3): <details> remember their own key in
  // `kept`, and tapped time buttons are re-expanded by row and instant after the container is rebuilt.
  const kept = new Set();
  function keepOpen(box, key) {
    if (!key) return box;
    box.dataset.keep = key;
    box.open = kept.has(key);
    box.addEventListener("toggle", () => { if (box.open) kept.add(key); else kept.delete(key); });
    return box;
  }
  function timeKey(control) {
    return `${control.closest("[data-row]")?.dataset?.row || ""}|${control.querySelector("time")?.dataset?.at || ""}`;
  }
  function openTimes(container) {
    return new Set([...container.querySelectorAll('.hq-time[aria-expanded="true"]')].map(timeKey));
  }
  function reopenTimes(container, keys) {
    if (!keys.size) return;
    for (const control of container.querySelectorAll(".hq-time")) {
      if (!keys.has(timeKey(control))) continue;
      control.setAttribute("aria-expanded", "true");
      if (control.nextElementSibling) control.nextElementSibling.hidden = false;
    }
  }
  // Keeps the reader's place when a container above or around them is rebuilt. Browsers with scroll
  // anchoring handle a container wholly above the viewport themselves; Safari on iPad does not.
  const anchoring = (() => {
    try { return Boolean(globalThis.CSS?.supports?.("overflow-anchor", "auto")); } catch (_) { return false; }
  })();
  function scrollAnchor(container) {
    if (typeof container.getBoundingClientRect !== "function" || typeof globalThis.scrollBy !== "function") return null;
    const box = container.getBoundingClientRect();
    if (!box || box.top >= 0 || !box.height) return null;
    if (box.bottom <= 0) return anchoring ? null : {height: box.height};
    for (const node of container.querySelectorAll("[data-row]")) {
      const top = node.getBoundingClientRect().top;
      if (top >= 0) return {row: node.dataset.row, top};
    }
    return null;
  }
  function restoreScroll(container, anchor) {
    if (!anchor) return;
    let shift = 0;
    if (anchor.row === undefined) shift = container.getBoundingClientRect().height - anchor.height;
    else {
      const node = [...container.querySelectorAll("[data-row]")].find(item => item.dataset.row === anchor.row);
      if (node) shift = node.getBoundingClientRect().top - anchor.top;
    }
    if (shift) globalThis.scrollBy(0, shift);
  }

  function renderView(name, force) {
    const entry = views.get(name);
    const snapshot = state.snapshot;
    const container = doc.querySelector(`[data-hq="${name}"]`);
    if (!entry || !container || !snapshot) return;
    let signature = null;
    try {
      signature = JSON.stringify([entry.deps(snapshot), state.technical, entry.version, localDayKey(serverNow()), state.connection]);
    } catch (_) { signature = null; }
    if (!force && signature !== null && signature === entry.sig) return;
    entry.sig = signature;
    const active = doc.activeElement;
    const focusKey = active && container.contains(active) ? active.dataset?.focusKey : null;
    const times = openTimes(container);
    const anchor = scrollAnchor(container);
    const fragment = doc.createDocumentFragment();
    try {
      entry.render(fragment, snapshot);
      container.replaceChildren(fragment);
    } catch (error) {
      console.error(error);
      container.replaceChildren(el("p", "hq-error", "This part couldn't be shown. The rest of HQ still works."));
    }
    const empty = !container.childNodes.length;
    container.classList.toggle("is-empty", empty);
    // Panels whose content only exists for some viewers (check-ins, welcome, notices) disappear when empty.
    if (container.hasAttribute("data-hq-hide-empty")) (container.closest(".hq-panel") || container).hidden = empty;
    reopenTimes(container, times);
    restoreScroll(container, anchor);
    if (focusKey) {
      const target = [...container.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === focusKey);
      target?.focus({preventScroll: true});
    }
  }
  function rerender(name) {
    const entry = views.get(name);
    if (!entry) return;
    entry.version += 1;
    renderView(name, true);
  }
  function renderAll(force) { for (const name of views.keys()) renderView(name, force); }

  // --- Network: every endpoint is a fixed literal; nothing is ever posted to a URL from JSON. ------

  function csrf() { return doc.querySelector('meta[name="csrf-token"]')?.content || ""; }
  const ENDPOINTS = Object.freeze({
    hq: (init, options) => fetch(`/api/v1/hq?calendar_days=${options.days === 14 ? 14 : 7}`, init),
    story: (init, options) => fetch(`/api/v1/hq/story?before=${encodeURIComponent(options.before)}&limit=60`, init),
    chart: (init, options) => fetch(`/api/v1/hq/chart${chartQuery(options)}`, init),
    wakes: (init, options) => fetch(`/api/v1/hq/wakes${wakesQuery(options)}`, init),
    plans: init => fetch("/api/v1/hq/plans", init),
    config: init => fetch("/api/v1/owner/config", init),
    pause: init => fetch("/api/v1/owner/pause", init),
    resume: init => fetch("/api/v1/owner/resume", init),
    "start-trading": init => fetch("/api/v1/owner/start-trading", init),
    "start-optimisation": init => fetch("/api/v1/owner/start-optimisation", init),
    "stop-service": init => fetch("/api/v1/owner/stop-service", init),
    expenses: init => fetch("/api/v1/owner/expenses", init),
    checkins: init => fetch("/api/v1/owner/checkins", init),
  });
  const READS = new Set(["hq", "story", "config", "chart", "wakes", "plans"]);
  // T41: a price chart is asked for by coin and candle size; both are optional (the server picks the defaults).
  function chartQuery(options) {
    const parts = [];
    if (typeof options.symbol === "string" && /^[A-Z0-9]{1,16}\/[A-Z]{3,5}$/.test(options.symbol)) {
      parts.push(`symbol=${encodeURIComponent(options.symbol)}`);
    }
    if ([1, 5, 15, 60].includes(options.interval)) parts.push(`interval=${options.interval}`);
    return parts.length ? `?${parts.join("&")}` : "";
  }
  // T39: the wake log is read newest first; an older page continues below a wake's cursor.
  function wakesQuery(options) {
    return Number.isSafeInteger(options.before) && options.before > 0 ? `?before=${options.before}` : "";
  }
  async function call(name, options = {}) {
    const go = ENDPOINTS[name];
    const write = options.body !== undefined;
    if (!go || READS.has(name) === write) throw new TypeError(`HQ cannot ${write ? "post to" : "read"} ${name}`);
    if (name === "story" && !isIso(options.before)) throw new TypeError("Story pages need a timestamp");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 15000);
    const headers = {Accept: "application/json"};
    const init = {method: write ? "POST" : "GET", credentials: "same-origin", cache: "no-store", headers, signal: controller.signal};
    if (write) {
      headers["Content-Type"] = "application/json";
      headers["X-CSRF-Token"] = csrf();
      init.body = JSON.stringify(options.body);
    }
    try {
      const response = await go(init, options);
      let body = null;
      try { body = await response.json(); } catch (_) { body = null; }
      if (response.status === 401) signedOut();
      return {network: false, status: response.status, ok: response.ok, body};
    } catch (_) {
      return {network: true, status: 0, ok: false, body: null};
    } finally {
      clearTimeout(timer);
    }
  }
  const api = Object.freeze({
    get: (name, options = {}) => call(name, {...options, body: undefined}),
    post: (name, body, options = {}) => call(name, {...options, body}),
  });

  // --- Freshness pill and signed-out banner ---------------------------------------------------------

  function ageWords(seconds) { return seconds < 60 ? `${seconds} s` : duration(seconds); }
  function freshness() {
    const pill = doc.querySelector("[data-hq-fresh]");
    if (!pill) return;
    const snapshot = state.snapshot;
    const seenAt = snapshot ? `, showing what we saw at ${clock(parseIso(snapshot.as_of))}` : "";
    let tone = "ok";
    let mark = "●";
    let words;
    if (state.connection === "signed_out") { tone = "offline"; mark = "■"; words = "Signed out"; }
    else if (state.connection === "forbidden") { tone = "offline"; mark = "■"; words = "This sign-in can't view HQ"; }
    else if (state.connection === "outdated") { tone = "old"; mark = "▲"; words = "HQ was updated: reload the page"; }
    else if (state.connection === "server_error") { tone = "offline"; mark = "■"; words = "HQ had a problem; retrying"; }
    else if (!snapshot) {
      tone = state.connection === "failed" ? "offline" : "ok";
      mark = state.connection === "failed" ? "■" : "●";
      words = state.connection === "failed" ? "Can't reach HQ yet" : "Loading…";
    } else if (state.connection === "failed") {
      tone = "offline"; mark = "■";
      words = `Can't reach HQ${seenAt}`;
    } else {
      const age = Math.max(0, Math.round((serverNow() - parseIso(snapshot.as_of)) / 1000));
      if (age > 75) { tone = "old"; mark = "▲"; words = `Out of date: last update ${ageWords(age)} ago`; }
      else words = age < 5 ? "Updated just now" : `Updated ${ageWords(age)} ago`;
    }
    pill.dataset.tone = tone;
    const glyph = el("span", "hq-fresh-mark", mark);
    glyph.setAttribute("aria-hidden", "true");
    pill.replaceChildren(glyph, el("span", "", words));
  }
  // Signed out, a sign-in that may no longer view HQ, or a page older than the server: polling stops
  // and a banner says what to do. No automatic redirect or reload ever happens (spec §C.3).
  function halt(connection, build, tone = "danger") {
    if (state.connection === connection) return;
    poller.stop();
    setConnection(connection);
    const banner = doc.querySelector("[data-hq-banner]");
    if (!banner) return;
    banner.dataset.tone = tone;
    banner.replaceChildren(build());
    banner.hidden = false;
  }
  function signInLine(words) {
    const box = el("p", "hq-banner-text", words);
    const anchor = el("a", "", "Sign in again ›");
    anchor.href = "/login";
    box.append(anchor);
    return box;
  }
  function signedOut() { halt("signed_out", () => signInLine("You're signed out. ")); }
  function forbidden() { halt("forbidden", () => signInLine("This sign-in can't view HQ any more. ")); }
  function outdated() {
    halt("outdated", () => {
      const box = el("div", "hq-banner-row");
      const reload = button("button hq-banner-action", "Reload");
      reload.addEventListener("click", () => globalThis.location?.reload());
      box.append(el("p", "hq-banner-text", "HQ was updated. Reload the page."), reload);
      return box;
    }, "info");
  }

  // --- Polling: every 30 s while visible, never while a sheet is open -----------------------------

  let inflight = null;
  let inflightDays = null;
  // ok: a fresh snapshot; failed: no answer (network); server_error: an answer HQ can't use. Only these
  // two back off; a 401, a 403 or a newer server stop polling instead (review T9).
  function classify(result) {
    if (result.network) return "failed";
    if (result.status === 401) return "signed_out";
    if (result.status === 403) return "forbidden";
    if (result.ok && typeof result.body?.schema === "string" && result.body.schema !== SCHEMA) return "outdated";
    if (result.ok && validSnapshot(result.body)) return "ok";
    return "server_error";
  }
  async function refresh() {
    // A request for another calendar range finishes first, then this one asks for the current range.
    while (inflight && inflightDays !== state.calendarDays) {
      try { await inflight; } catch (_) { /* The newer request below decides the state. */ }
    }
    if (HALTED.has(state.connection)) return false;
    if (inflight) return inflight;
    const days = state.calendarDays;
    inflightDays = days;
    const request = (async () => {
      const result = await api.get("hq", {days, timeout: 12000});
      const outcome = classify(result);
      if (outcome === "ok") {
        setConnection("ok");
        setSnapshot(result.body, Date.now());
        return true;
      }
      if (outcome === "signed_out") signedOut();
      else if (outcome === "forbidden") forbidden();
      else if (outcome === "outdated") outdated();
      else setConnection(outcome);
      return false;
    })();
    inflight = request;
    try { return await request; } finally { if (inflight === request) { inflight = null; inflightDays = null; } }
  }
  const poller = (() => {
    let timer = null;
    let paused = false;
    let stopped = false;
    let failures = 0;
    let fastUntil = 0;
    let fastPending = false;
    function delay() {
      if (Date.now() < fastUntil) return 3000;
      if (failures) return [30000, 60000, 120000][Math.min(failures, 3) - 1];
      return Math.max(5, Number(state.snapshot?.poll_seconds) || 30) * 1000;
    }
    function schedule(ms) {
      clearTimeout(timer);
      if (!stopped && !paused) timer = setTimeout(tick, ms === undefined ? delay() : ms);
    }
    async function tick() {
      if (stopped || paused || doc.hidden) return;
      const ok = await refresh();
      failures = ok ? 0 : failures + 1;
      schedule();
    }
    return Object.freeze({
      start() { schedule(); },
      now() { schedule(0); },
      // After a decision: poll every 3 s for 30 s. While a sheet holds polling, the window starts when it
      // closes, so the result sheet staying open doesn't use it up (review T6).
      fast() {
        if (paused) { fastPending = true; return; }
        fastUntil = Date.now() + 30000;
        schedule(3000);
      },
      pause() { paused = true; clearTimeout(timer); },
      resume() {
        paused = false;
        if (fastPending) { fastPending = false; fastUntil = Date.now() + 30000; }
        schedule(0);
      },
      stop() { stopped = true; clearTimeout(timer); },
    });
  })();
  // "Show 2 weeks" during a poll: the poll finishes, then a request for the new range is always sent (T7).
  function setCalendarDays(days) {
    state.calendarDays = days === 14 ? 14 : 7;
    return refresh();
  }

  // --- Sheet: one shared <dialog>; inline below its block where showModal is missing ---------------

  const sheet = (() => {
    let opener = null;
    let onClose = null;
    const dialog = () => doc.querySelector("[data-hq-sheet]");
    const content = () => dialog()?.querySelector("[data-hq-sheet-content]") || dialog();
    const modal = () => typeof globalThis.HTMLDialogElement === "function" &&
      typeof globalThis.HTMLDialogElement.prototype.showModal === "function";
    function isOpen() { return state.sheet; }
    function open({title, build, from, onClose: callback, kind}) {
      const box = dialog();
      if (!box) return null;
      if (!state.sheet) opener = from instanceof Element ? from : doc.activeElement;
      onClose = typeof callback === "function" ? callback : null;
      const head = el("div", "hq-sheet-head");
      const heading = el("h2", "hq-sheet-title", title);
      heading.id = "hq-sheet-title";
      heading.tabIndex = -1;
      const close = button("hq-sheet-close");
      close.setAttribute("aria-label", "Close");
      close.dataset.hqClose = "";
      close.append(icon("close", "hq-icon hq-icon-small"));
      head.append(heading, close);
      // Messages raised while a sheet is open appear here, between the title and the scrolling body:
      // the page's own toast sits underneath the modal sheet where it can't be seen or tapped (review U4).
      const notes = el("div", "hq-sheet-toast");
      notes.setAttribute("role", "status");
      notes.setAttribute("aria-live", "polite");
      notes.dataset.hqSheetToast = "";
      const body = el("div", "hq-sheet-body");
      content().replaceChildren(head, notes, body);
      box.dataset.kind = kind || "";
      build(body);
      const wasOpen = state.sheet;
      state.sheet = true;
      poller.pause();
      if (!wasOpen) {
        if (modal()) {
          box.classList.remove("is-inline");
          if (!box.open) box.showModal();
        } else {
          const anchor = opener?.closest?.("[data-hq]") || opener?.closest?.("[data-hq-section]");
          if (anchor) anchor.after(box);
          box.classList.add("is-inline");
          box.setAttribute("open", "");
        }
      }
      // A disabled confirm button (careful decisions wait for the tick box) can't take focus: use the title.
      const first = [...body.querySelectorAll("[data-autofocus]")].find(node => !node.disabled);
      (first || heading).focus({preventScroll: !modal()});
      if (!modal()) box.scrollIntoView({block: "nearest"});
      return body;
    }
    function cleanup() {
      if (!state.sheet) return;
      state.sheet = false;
      content()?.replaceChildren();
      const callback = onClose;
      onClose = null;
      const target = opener;
      opener = null;
      let focusTarget = target?.isConnected ? target : null;
      const key = target?.dataset?.focusKey;
      if (!focusTarget && key) focusTarget = [...doc.querySelectorAll("[data-focus-key]")].find(node => node.dataset.focusKey === key);
      focusTarget?.focus({preventScroll: true});
      if (callback) { try { callback(); } catch (error) { console.error(error); } }
      if (!HALTED.has(state.connection)) poller.resume();
    }
    function close() {
      const box = dialog();
      if (!box || !state.sheet) return;
      if (modal() && box.open) { box.close(); return; }   // the "close" event runs cleanup
      box.removeAttribute("open");
      cleanup();
    }
    function bind() {
      const box = dialog();
      if (!box) return;
      box.addEventListener("close", cleanup);
      box.addEventListener("click", event => { if (event.target === box) close(); });
    }
    return Object.freeze({open, close, isOpen, bind});
  })();

  // --- Toast ------------------------------------------------------------------------------------

  let toastTimer = null;
  function hideToast() {
    const node = doc.querySelector("[data-hq-toast]");
    if (node) {
      node.classList.remove("is-visible");
      node.replaceChildren();
    }
    for (const notes of doc.querySelectorAll("[data-hq-sheet-toast]")) notes.replaceChildren();
  }
  // While a sheet is open the message goes inside it (its live region), so it is visible, tappable and
  // announced; otherwise it uses the page's toast area above the tab bar.
  function toast(message, action) {
    const inSheet = state.sheet ? doc.querySelector("[data-hq-sheet] [data-hq-sheet-toast]") : null;
    const node = inSheet || doc.querySelector("[data-hq-toast]");
    if (!node) return;
    hideToast();
    const box = el("div", "hq-toast-box");
    box.append(el("p", "hq-toast-text", message));
    const actions = el("div", "hq-toast-actions");
    if (action && typeof action.run === "function") {
      const go = button("button hq-toast-action", action.label);
      go.addEventListener("click", () => { hideToast(); action.run(go); });
      actions.append(go);
    }
    const dismiss = button("hq-toast-close");
    dismiss.setAttribute("aria-label", "Dismiss message");
    dismiss.append(icon("close", "hq-icon hq-icon-small"));
    dismiss.addEventListener("click", hideToast);
    actions.append(dismiss);
    box.append(actions);
    node.replaceChildren(box);
    node.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, action ? 15000 : 7000);
  }

  // --- Ticker and global listeners ---------------------------------------------------------------

  function tickTimes() {
    for (const node of doc.querySelectorAll("time[data-at][data-style]")) {
      const ms = parseIso(node.dataset.at);
      if (ms === null) continue;
      const words = styled(ms, node.dataset.style);
      if (node.textContent !== words) node.textContent = words;
    }
  }
  function bindGlobal() {
    sheet.bind();
    doc.addEventListener("click", event => {
      const target = event.target instanceof Element ? event.target : null;
      const control = target?.closest(".hq-time");
      if (control) {
        const full = control.nextElementSibling;
        const open = control.getAttribute("aria-expanded") !== "true";
        control.setAttribute("aria-expanded", String(open));
        if (full) full.hidden = !open;
        return;
      }
      if (target?.closest("[data-hq-close]")) sheet.close();
    });
    doc.addEventListener("visibilitychange", () => { if (!doc.hidden && !state.sheet) poller.now(); });
    globalThis.addEventListener("online", () => { if (!state.sheet) poller.now(); });
    globalThis.addEventListener("offline", () => { if (!HALTED.has(state.connection)) setConnection("failed"); });
    setInterval(tickTimes, 15000);
    setInterval(freshness, 5000);
  }

  const modules = new Map();
  function define(name, value) {
    if (modules.has(name)) throw new Error(`HQ module ${name} is already defined`);
    modules.set(name, Object.freeze(value));
  }
  function use(name) { return modules.get(name) || null; }

  const HQ = Object.freeze({
    SCHEMA, KEYS, store, PENDING_MAX_AGE_MS,
    readPending, savePending, clearPending,
    define, use, on, emit,
    list, obj, str, count, uuid, randomHex,
    parseIso, isIso, toIso, serverNow, dayDiff, dayStart, localDayKey,
    fmt: Object.freeze({clock, rel, relClock, dayClock, dateWords, monthWords, weekday, styled, fullTime, duration,
      localDateTime, utcStamp, utcClock, timeZone}),
    el, button, icon, link, safePath, renderText, textEl, plain, hasText, keepOpen,
    api, csrf,
    snapshot: () => state.snapshot,
    connection: () => state.connection,
    online: () => state.connection === "ok" && Boolean(state.snapshot),
    technical: () => state.technical,
    setTechnical,
    lastSeen: () => state.lastSeen,
    calendarDays: () => state.calendarDays,
    setCalendarDays,
    setSnapshot, validSnapshot, refresh,
    view, rerender, renderAll,
    sheet, toast, poller, freshness, tickTimes, bindGlobal,
  });
  Object.defineProperty(globalThis, "TradeGraphHQ", {value: HQ, writable: false, configurable: false, enumerable: false});
})();
