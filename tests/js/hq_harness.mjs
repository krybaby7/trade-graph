// Test harness for the HQ page scripts: a small DOM, a fake clock, fake timers, a scripted fetch and an
// in-memory localStorage. Each `loadHQ()` call builds a fresh page and runs the real hq-*.js files in their
// own vm context, so tests never share state. No browser, no network, no packages beyond Node itself.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import {fileURLToPath} from "node:url";
import {webcrypto} from "node:crypto";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
// HQ_STATIC_DIR runs the same tests against another copy of the scripts (e.g. an older commit, to see them fail).
export const STATIC = process.env.HQ_STATIC_DIR || path.join(ROOT, "src", "trade_graph", "web", "static");
// HQ_FIXTURES_DIR runs them against other snapshots (e.g. fixtures with or without the optional contract keys).
export const FIXTURES = process.env.HQ_FIXTURES_DIR || path.join(ROOT, "tests", "fixtures", "hq");
export const SCRIPTS = ["hq-core.js", "hq-glossary.js", "hq-render.js", "hq-prompts.js", "hq-decide.js", "hq-checkins.js"];
export const VIEWS = ["welcome", "notices", "now", "attention", "upnext", "map", "calendar", "checkins", "story", "money",
  "allowance", "spending", "market", "decide", "review", "bills", "prompt", "help"];

export function fixture(name) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, `snapshot_${name}.json`), "utf8"));
}
export const clone = value => JSON.parse(JSON.stringify(value));
export const shiftIso = (iso, ms) => new Date(Date.parse(iso) + ms).toISOString();

// --- Selectors: tag, #id, .class, [attr], [attr="v"], [attr^="v"], descendant and child combinators, lists ---

function splitTop(text, separator) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let current = "";
  for (const character of text) {
    if (quote) { if (character === quote) quote = null; current += character; continue; }
    if (character === "\"" || character === "'") { quote = character; current += character; continue; }
    if (character === "[" || character === "(") depth += 1;
    if (character === "]" || character === ")") depth -= 1;
    if (depth === 0 && separator.test(character)) { parts.push(current); current = ""; continue; }
    current += character;
  }
  parts.push(current);
  return parts;
}
function parseCompound(text) {
  const compound = {tag: null, id: null, classes: [], attrs: []};
  const pattern = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[\s*([\w-]+)\s*(?:(\^?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]+)))?\s*\]/gy;
  let match;
  let index = 0;
  pattern.lastIndex = 0;
  while (index < text.length && (match = pattern.exec(text))) {
    if (match[1]) compound.tag = match[1].toLowerCase();
    else if (match[2]) compound.id = match[2];
    else if (match[3]) compound.classes.push(match[3]);
    else compound.attrs.push({name: match[4], op: match[5] || null, value: match[6] ?? match[7] ?? match[8] ?? null});
    index = pattern.lastIndex;
  }
  if (index !== text.length) throw new Error(`Unsupported selector part: ${text}`);
  return compound;
}
function parseSelector(text) {
  return splitTop(text.trim(), /,/).map(part => {
    const tokens = part.trim().replace(/\s*>\s*/g, " > ").split(/\s+/);
    const steps = [];
    let combinator = " ";
    for (const token of tokens) {
      if (token === ">") { combinator = ">"; continue; }
      steps.push({combinator, compound: parseCompound(token)});
      combinator = " ";
    }
    return steps;
  });
}
function matchesCompound(element, compound) {
  if (element.nodeType !== 1) return false;
  if (compound.tag && element.localName !== compound.tag) return false;
  if (compound.id && element.getAttribute("id") !== compound.id) return false;
  for (const name of compound.classes) if (!element.classList.contains(name)) return false;
  for (const attr of compound.attrs) {
    const value = element.getAttribute(attr.name);
    if (value === null) return false;
    if (attr.op === "=" && value !== attr.value) return false;
    if (attr.op === "^=" && !value.startsWith(attr.value)) return false;
  }
  return true;
}
function matchesSteps(element, steps, index = steps.length - 1) {
  if (!matchesCompound(element, steps[index].compound)) return false;
  if (index === 0) return true;
  const combinator = steps[index].combinator;
  let parent = element.parentNode;
  if (combinator === ">") return Boolean(parent && parent.nodeType === 1 && matchesSteps(parent, steps, index - 1));
  while (parent && parent.nodeType === 1) {
    if (matchesSteps(parent, steps, index - 1)) return true;
    parent = parent.parentNode;
  }
  return false;
}
function matches(element, selector) { return parseSelector(selector).some(steps => matchesSteps(element, steps)); }
function* descendants(node) {
  for (const child of node.childNodes) {
    if (child.nodeType === 1) { yield child; yield* descendants(child); }
    else if (child.nodeType === 11) yield* descendants(child);
  }
}

// --- Nodes ---------------------------------------------------------------------------------------------

export function makeDom() {
  class Event {
    constructor(type, init = {}) { this.type = type; this.bubbles = Boolean(init.bubbles); this.defaultPrevented = false; this.target = null; }
    preventDefault() { this.defaultPrevented = true; }
    stopPropagation() { this._stopped = true; }
  }
  class Node {
    constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; this._listeners = new Map(); }
    get firstChild() { return this.childNodes[0] || null; }
    get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
    get nextSibling() { const p = this.parentNode; return p ? p.childNodes[p.childNodes.indexOf(this) + 1] || null : null; }
    get isConnected() { let node = this; while (node) { if (node === this.ownerDocument) return true; node = node.parentNode; } return false; }
    get textContent() { return this.childNodes.map(child => child.textContent).join(""); }
    set textContent(value) { this.replaceChildren(); if (value !== null && value !== undefined && value !== "") this.append(String(value)); }
    _adopt(nodes) {
      const result = [];
      for (const node of nodes) {
        if (node === null || node === undefined) continue;
        if (typeof node === "string" || typeof node === "number") result.push(new Text(this.ownerDocument, String(node)));
        else if (node.nodeType === 11) {
          for (const child of node.childNodes) child.parentNode = null;
          result.push(...node.childNodes);
          node.childNodes = [];
        }
        else result.push(node);
      }
      return result;
    }
    _insert(nodes, index) {
      const adopted = this._adopt(nodes);
      for (const node of adopted) node.remove();
      let at = index === undefined ? this.childNodes.length : Math.min(index, this.childNodes.length);
      for (const node of adopted) { node.parentNode = this; this.childNodes.splice(at, 0, node); at += 1; }
    }
    append(...nodes) { this._insert(nodes); }
    prepend(...nodes) { this._insert(nodes, 0); }
    appendChild(node) { this._insert([node]); return node; }
    insertBefore(node, reference) {
      if (!reference) return this.appendChild(node);
      node.remove?.();
      this._insert([node], this.childNodes.indexOf(reference));
      return node;
    }
    removeChild(node) { node.remove(); return node; }
    replaceChildren(...nodes) {
      for (const child of this.childNodes) child.parentNode = null;
      this.childNodes = [];
      this._insert(nodes);
    }
    remove() {
      const parent = this.parentNode;
      if (!parent) return;
      const index = parent.childNodes.indexOf(this);
      if (index >= 0) parent.childNodes.splice(index, 1);
      this.parentNode = null;
    }
    after(...nodes) { const p = this.parentNode; if (p) p._insert(nodes, p.childNodes.indexOf(this) + 1); }
    before(...nodes) { const p = this.parentNode; if (p) p._insert(nodes, p.childNodes.indexOf(this)); }
    replaceWith(...nodes) {
      const parent = this.parentNode;
      if (!parent) return;
      const index = parent.childNodes.indexOf(this);
      this.remove();
      parent._insert(nodes, index);
    }
    contains(node) { while (node) { if (node === this) return true; node = node.parentNode; } return false; }
    addEventListener(type, callback) { if (!this._listeners.has(type)) this._listeners.set(type, []); this._listeners.get(type).push(callback); }
    removeEventListener(type, callback) { const list = this._listeners.get(type) || []; const i = list.indexOf(callback); if (i >= 0) list.splice(i, 1); }
    dispatchEvent(event) {
      event.target = event.target || this;
      let node = this;
      while (node) {
        for (const callback of [...(node._listeners.get(event.type) || [])]) callback.call(node, event);
        if (!event.bubbles || event._stopped) break;
        node = node.parentNode;
      }
      return !event.defaultPrevented;
    }
    querySelectorAll(selector) {
      const groups = parseSelector(selector);
      return [...descendants(this)].filter(element => groups.some(steps => matchesSteps(element, steps)));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  class Text extends Node {
    constructor(doc, data) { super(doc); this.nodeType = 3; this.data = data; }
    get textContent() { return this.data; }
    set textContent(value) { this.data = String(value); }
    cloneNode() { return new Text(this.ownerDocument, this.data); }
  }
  class DocumentFragment extends Node {
    constructor(doc) { super(doc); this.nodeType = 11; }
  }
  const FOCUSABLE = new Set(["button", "input", "select", "textarea", "a"]);
  const BOOLEAN = {hidden: "hidden", disabled: "disabled", open: "open", readOnly: "readonly"};
  class Element extends Node {
    constructor(doc, tag) {
      super(doc);
      this.nodeType = 1;
      this.localName = tag.toLowerCase();
      this.tagName = tag.toUpperCase();
      this._attrs = new Map();
      this._value = undefined;
      this.checked = false;
      this.scrollTop = 0;
      const element = this;
      this.dataset = new Proxy({}, {
        get: (_, key) => typeof key === "string" ? element.getAttribute(`data-${key.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}`) ?? undefined : undefined,
        set: (_, key, value) => { element.setAttribute(`data-${key.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}`, String(value)); return true; },
        has: (_, key) => element.hasAttribute(`data-${String(key).replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}`),
        deleteProperty: (_, key) => { element.removeAttribute(`data-${String(key).replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)}`); return true; },
      });
      this.classList = {
        add: (...names) => { const set = new Set(element.className.split(/\s+/).filter(Boolean)); names.forEach(n => set.add(n)); element.className = [...set].join(" "); },
        remove: (...names) => { element.className = element.className.split(/\s+/).filter(n => n && !names.includes(n)).join(" "); },
        toggle: (name, force) => {
          const has = element.classList.contains(name);
          const want = force === undefined ? !has : Boolean(force);
          if (want) element.classList.add(name); else element.classList.remove(name);
          return want;
        },
        contains: name => element.className.split(/\s+/).includes(name),
      };
    }
    // A <select> reads as its first option until something is chosen, like a browser.
    get value() {
      if (this._value !== undefined) return this._value;
      return this.localName === "select" ? this.querySelector("option")?.value ?? "" : "";
    }
    set value(value) { this._value = String(value); }
    get children() { return this.childNodes.filter(node => node.nodeType === 1); }
    get firstElementChild() { return this.children[0] || null; }
    get nextElementSibling() {
      const p = this.parentNode;
      if (!p) return null;
      const siblings = p.childNodes.filter(node => node.nodeType === 1);
      return siblings[siblings.indexOf(this) + 1] || null;
    }
    get className() { return this.getAttribute("class") || ""; }
    set className(value) { this.setAttribute("class", value); }
    get id() { return this.getAttribute("id") || ""; }
    set id(value) { this.setAttribute("id", value); }
    get href() { return this.getAttribute("href") || ""; }
    set href(value) { this.setAttribute("href", value); }
    get hash() { const href = this.href; const i = href.indexOf("#"); return i < 0 ? "" : href.slice(i); }
    get tabIndex() {
      const value = this.getAttribute("tabindex");
      if (value !== null) return Number(value);
      return FOCUSABLE.has(this.localName) ? 0 : -1;
    }
    set tabIndex(value) { this.setAttribute("tabindex", String(value)); }
    setAttribute(name, value) { this._attrs.set(name.toLowerCase(), String(value)); }
    getAttribute(name) { const value = this._attrs.get(name.toLowerCase()); return value === undefined ? null : value; }
    hasAttribute(name) { return this._attrs.has(name.toLowerCase()); }
    removeAttribute(name) { this._attrs.delete(name.toLowerCase()); }
    matches(selector) { return matches(this, selector); }
    closest(selector) {
      let node = this;
      while (node && node.nodeType === 1) { if (matches(node, selector)) return node; node = node.parentNode; }
      return null;
    }
    focus() {
      if (this.disabled || !this.isConnected) return;
      if (this.tabIndex < 0 && !this.hasAttribute("tabindex")) return;
      if (this.closest("dialog") && !this.closest("dialog").open) return;
      this.ownerDocument.activeElement = this;
    }
    blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
    click() {
      if (this.disabled) return;
      if (this.localName === "input" && (this.type === "checkbox" || this.type === "radio")) {
        this.checked = this.type === "radio" ? true : !this.checked;
        this.dispatchEvent(new Event("click", {bubbles: true}));
        this.dispatchEvent(new Event("change", {bubbles: true}));
        return;
      }
      this.dispatchEvent(new Event("click", {bubbles: true}));
    }
    getBoundingClientRect() { return {top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0}; }
    scrollIntoView() {}
    select() {}
    setSelectionRange() {}
    cloneNode(deep) {
      const copy = this.ownerDocument.createElement(this.localName);
      for (const [name, value] of this._attrs) copy._attrs.set(name, value);
      if (deep) for (const child of this.childNodes) copy.append(child.cloneNode(true));
      return copy;
    }
  }
  for (const [property, attribute] of Object.entries(BOOLEAN)) {
    Object.defineProperty(Element.prototype, property, {
      get() { return this.hasAttribute(attribute); },
      set(value) { if (value) this.setAttribute(attribute, ""); else this.removeAttribute(attribute); },
    });
  }
  class HTMLDetailsElement extends Element {}
  class HTMLDialogElement extends Element {
    showModal() { this.open = true; }
    close() { if (!this.open) return; this.open = false; this.dispatchEvent(new Event("close")); }
  }
  class HTMLTemplateElement extends Element {
    constructor(doc, tag) { super(doc, tag); this.content = new DocumentFragment(doc); }
  }
  class Document extends Node {
    constructor() {
      super(null);
      this.ownerDocument = this;
      this.nodeType = 9;
      this.hidden = false;
      this.documentElement = this.createElement("html");
      this.head = this.createElement("head");
      this.body = this.createElement("body");
      this.documentElement.append(this.head, this.body);
      this.childNodes = [this.documentElement];
      this.documentElement.parentNode = this;
      this.activeElement = this.body;
    }
    createElement(tag) {
      const name = tag.toLowerCase();
      if (name === "dialog") return new HTMLDialogElement(this, name);
      if (name === "details") return new HTMLDetailsElement(this, name);
      if (name === "template") return new HTMLTemplateElement(this, name);
      return new Element(this, name);
    }
    createTextNode(text) { return new Text(this, String(text)); }
    createDocumentFragment() { return new DocumentFragment(this); }
    getElementById(id) { return this.querySelector(`#${id}`); }
    execCommand() { return false; }
  }
  return {Event, Node, Text, DocumentFragment, Element, HTMLDetailsElement, HTMLDialogElement, HTMLTemplateElement, Document};
}

// --- Page, clock, timers, fetch, storage ---------------------------------------------------------------

function buildPage(doc) {
  const el = (tag, attrs = {}, ...children) => {
    const node = doc.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    node.append(...children);
    return node;
  };
  doc.body.className = "hq-page";
  doc.head.append(el("meta", {name: "csrf-token", content: "test-csrf"}));
  const header = el("header", {class: "hq-header"}, el("p", {class: "hq-fresh", "data-hq-fresh": ""}),
    el("div", {class: "hq-banner", "data-hq-banner": "", hidden: ""}));
  const now = el("article", {class: "hq-panel hq-now", "data-hq-now-card": ""},
    el("h2", {id: "hq-h-now"}, "Right now"), el("span", {"data-hq-now-chip": ""}), el("p", {"data-hq-now-headline": ""}),
    el("div", {"data-hq": "now"}));
  const attention = el("article", {class: "hq-panel"}, el("h2", {}, "Needs you ", el("span", {"data-hq-attention-count": ""})),
    el("div", {"data-hq": "attention"}));
  const root = el("div", {"data-hq-root": ""}, header);
  for (const section of ["hq", "calendar", "story", "money", "decide"]) root.append(el("section", {id: section, "data-hq-section": ""}));
  const hq = root.querySelector("#hq");
  hq.append(el("div", {class: "hq-slot hq-slot-welcome", "data-hq": "welcome", "data-hq-hide-empty": ""}),
    el("div", {class: "hq-slot hq-slot-notices", "data-hq": "notices", "data-hq-hide-empty": ""}), now, attention);
  for (const name of VIEWS) {
    if (root.querySelector(`[data-hq="${name}"]`)) continue;
    const panel = el("article", {class: "hq-panel"}, el("div", {"data-hq": name, ...(name === "checkins" ? {"data-hq-hide-empty": ""} : {})}));
    root.querySelector(name === "calendar" || name === "checkins" ? "#calendar" : name === "story" ? "#story" :
      ["money", "allowance", "spending", "market"].includes(name) ? "#money" : ["decide", "review", "bills", "prompt", "help"].includes(name) ? "#decide" : "#hq")
      .append(panel);
  }
  const dialog = el("dialog", {class: "hq-sheet", "data-hq-sheet": ""}, el("div", {class: "hq-sheet-content", "data-hq-sheet-content": ""}));
  const icon = el("template", {"data-hq-icon": ""});
  icon.content.append(el("svg", {class: "hq-icon"}, el("use")));
  doc.body.append(root, dialog, el("div", {class: "hq-toast", role: "status", "data-hq-toast": ""}), icon);
}

export function response(status, body) {
  return {status, ok: status >= 200 && status < 300, json: async () => (body === undefined ? null : clone(body))};
}

export function loadHQ({tz = "UTC", now = Date.UTC(2026, 9, 9, 14, 0, 0), storage = {}, fetch, scripts = SCRIPTS, bind = false} = {}) {
  process.env.TZ = tz;
  const dom = makeDom();
  const doc = new dom.Document();
  buildPage(doc);
  const clock = {now};
  const RealDate = Date;
  class FakeDate extends RealDate {
    constructor(...args) { if (args.length) super(...args); else super(clock.now); }
    static now() { return clock.now; }
  }
  const timers = new Map();
  let nextTimer = 1;
  const calls = [];
  const store = new Map(Object.entries(storage).map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)]));
  const localStorage = {
    getItem: key => store.has(key) ? store.get(key) : null,
    setItem: (key, value) => { store.set(key, String(value)); },
    removeItem: key => { store.delete(key); },
  };
  const listeners = new Map();
  const reloads = [];
  const scrolls = [];
  const sandbox = {
    document: doc, console, Date: FakeDate, Intl, Promise, Proxy, URL, TextEncoder, AbortController, Blob,
    Element: dom.Element, HTMLDetailsElement: dom.HTMLDetailsElement, HTMLDialogElement: dom.HTMLDialogElement,
    crypto: webcrypto, isSecureContext: false, navigator: {}, localStorage, sessionStorage: localStorage,
    location: {origin: "http://hq.test", hash: "", reload: () => reloads.push(clock.now)},
    history: {replaceState() {}},
    matchMedia: () => ({matches: true}),
    scrollTo() {}, scrollBy: (x, y) => scrolls.push(y),
    setTimeout: (callback, ms = 0) => { const id = nextTimer++; timers.set(id, {callback, at: clock.now + Math.max(0, ms)}); return id; },
    clearTimeout: id => { timers.delete(id); },
    setInterval: () => 0, clearInterval() {},
    addEventListener: (type, callback) => { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); },
    fetch: async (url, init = {}) => {
      const call = {url: String(url), method: init.method || "GET", body: init.body ? JSON.parse(init.body) : undefined, at: clock.now};
      calls.push(call);
      if (!fetch) return response(200, null);
      return fetch(call);
    },
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  for (const name of scripts) vm.runInContext(fs.readFileSync(path.join(STATIC, name), "utf8"), context, {filename: name});
  const HQ = sandbox.TradeGraphHQ;
  if (bind) HQ.bindGlobal();
  async function flush() {
    for (let round = 0; round < 5; round += 1) {
      await new Promise(resolve => setImmediate(resolve));
    }
  }
  async function advance(ms) {
    const end = clock.now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]);
      clock.now = Math.max(clock.now, due[1].at);
      due[1].callback();
      await flush();
    }
    clock.now = end;
    await flush();
  }
  const $ = selector => doc.querySelector(selector);
  const $$ = selector => doc.querySelectorAll(selector);
  const view = name => doc.querySelector(`[data-hq="${name}"]`);
  const text = node => (node?.textContent || "").replace(/\s+/g, " ").trim();
  function show(snapshot, receivedAt = clock.now) {
    const data = clone(snapshot);
    return HQ.setSnapshot(data, receivedAt);
  }
  function toggle(details, open = true) {
    details.open = open;
    details.dispatchEvent(new dom.Event("toggle"));
  }
  const byText = (selector, words) => [...doc.querySelectorAll(selector)].find(node => text(node).includes(words)) || null;
  return {HQ, doc, dom, clock, timers, calls, store, sandbox, reloads, scrolls, listeners, flush, advance, $, $$, view, text, show,
    toggle, byText, use: name => HQ.use(name)};
}
