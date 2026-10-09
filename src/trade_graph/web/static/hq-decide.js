/* Trade Graph HQ decisions: cards, preview → confirm, and the owner POSTs. Each decision id maps to one
   hard-coded endpoint and body (spec §B.8); nothing is ever posted to a URL taken from the snapshot.
   Request identity follows progress.js / dashboard.js: an uncertain outcome keeps its request and resends it. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const {el, list, obj, str, renderText, textEl, hasText, fmt, parseIso} = HQ;

  const ENDPOINT = Object.freeze({
    start_trading: "start-trading", pause_ai: "pause", resume: "resume", start_optimisation: "start-optimisation",
    stop_safely: "stop-service", record_bill: "expenses", declare_bills_complete: "expenses",
  });
  const TEAM = ["start_trading", "resume", "pause_ai", "stop_safely", "start_optimisation"];
  const BILLS = ["record_bill", "declare_bills_complete"];
  const UNCERTAIN = "We don't know if it went through. Pressing again resends the same request, which is safe.";
  const IN_PROGRESS = "Another owner action is still being processed. Try again in a moment; it resends the same request.";
  const NOT_ACCEPTED = "That wasn't accepted. Nothing changed.";
  const OFFLINE = "Can't confirm the current state.";
  const BARRIERS = Object.freeze({
    "unresolved orders": "A practice order's status is unclear",
    "unresolved billing": "An AI charge is uncertain",
    "unresolved invoice differences": "A recorded invoice doesn't match",
    "version recovery pending": "An improvement is mid-switch",
    "system pause requires controller recovery": "Automatic safety stop",
    "requested pause management is incomplete": "The stop step hasn't finished. Start the team and wait about a minute.",
    "owner state changed during reconciliation; newer pause retained": "Another change happened at the same time",
  });
  const DETAILS = [
    [/system pause requires controller recovery/i, "Can't pause over an automatic safety stop (it already protects)."],
    [/owner state changed/i, "Another change happened at the same time; the newer setting stays."],
    [/reconciliation incomplete/i, "Resume couldn't finish its double-check, so the pause stays. Try again later."],
    [/Optimisation requires|pause prevents new AI work/i, "Needs the team running normally, no pause, and no review already in progress."],
    [/already active or unresolved/i, "A review is already in progress. Follow it in Story."],
    [/Service control unavailable/i, "The team's program couldn't be reached. Nothing changed."],
  ];
  const SPLIT_FIVE = Object.freeze({leader: "0.2", research: "0.2", trader: "0.2", learning: "0.2", optimisation: "0.2"});
  const OTHER_PRESETS = Object.freeze([["hosting", "Hosting"], ["electricity", "Electricity"], ["internet", "Internet"],
    ["service", "Other service"]]);
  const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;

  // --- Pending requests (tg-hq-pending:<decisionId>) ------------------------------------------------

  const pending = Object.freeze({
    get(id) {
      const stored = HQ.store.getJson(HQ.KEYS.pending(id));
      return stored && typeof stored.request_id === "string" && stored.body && typeof stored.body === "object" &&
        stored.body.request_id === stored.request_id ? stored : null;
    },
    set(id, body) { HQ.store.setJson(HQ.KEYS.pending(id), {request_id: body.request_id, body, saved_at: HQ.toIso(HQ.serverNow())}); },
    clear(id) { HQ.store.remove(HQ.KEYS.pending(id)); },
  });

  const cardFor = id => list(HQ.snapshot()?.decisions).find(card => card && card.id === id) || null;

  // --- Outcomes ------------------------------------------------------------------------------------

  function plainDetail(id, detail, status) {
    const raw = typeof detail === "string" ? detail : "";
    let message = null;
    if (id === "record_bill" || id === "declare_bills_complete") {
      if (status === 409) message = "This bill or period is already recorded.";
      else if (status === 422) {
        message = id === "record_bill" ? "That bill wasn't accepted. Check the amount, dates and share. Nothing was saved." :
          "That wasn't accepted. The month must be finished. Nothing was saved.";
      }
    }
    if (!message) message = DETAILS.find(([pattern]) => pattern.test(raw))?.[1] || NOT_ACCEPTED;
    if (HQ.technical() && raw) message += ` [${raw.slice(0, 200)}]`;
    return message;
  }
  function success(id, body) {
    const result = obj(body);
    switch (id) {
      case "start_trading": return result.attached ? "Already running; reconnected." : "Started.";
      case "pause_ai": return "Paused. Protection continues.";
      case "resume": return "Resumed. Departments are back on their timetable.";
      case "start_optimisation": return "Requested. Follow it in Story.";
      case "stop_safely": return result.service_stop_requested === false ?
        "Kept running in protect-only mode because something is held." : "Stopping. Nothing is watched once it's off.";
      case "record_bill": return "Bill recorded.";
      case "declare_bills_complete": return "Confirmed.";
      default: return "Done.";
    }
  }
  function classify(id, result) {
    if (result.network || result.status >= 500) return {kind: "uncertain", message: UNCERTAIN};
    if (result.ok) { pending.clear(id); return {kind: "done", message: success(id, result.body)}; }
    if (result.status === 401) { pending.clear(id); return {kind: "failed", message: "You're signed out. Sign in again, then try once more."}; }
    const detail = result.body?.detail;
    if (result.status === 409) {
      if (detail && typeof detail === "object" && detail.reason === "stale revision") {
        pending.clear(id);
        if (id === "resume") return {kind: "stale"};
        return {kind: "failed", message: "Something changed at the same moment, so nothing was changed. Check the latest state and try again."};
      }
      if (detail && typeof detail === "object" && detail.reason === "resume blocked") {
        pending.clear(id);
        const reasons = list(detail.barriers).map(item => BARRIERS[item] || "a check HQ can't describe in plain words");
        return {kind: "failed", message: `Resume was refused: ${reasons.join("; ") || "a check didn't pass"}. Nothing changed.`};
      }
      if (typeof detail === "string" && /in progress/i.test(detail)) return {kind: "uncertain", message: IN_PROGRESS};
      if (typeof detail === "string" && /pause persisted/i.test(detail)) {
        pending.clear(id);
        return {kind: "done", message: "Paused. Protection is still being double-checked."};
      }
    }
    pending.clear(id);
    return {kind: "failed", message: plainDetail(id, detail, result.status)};
  }
  async function ownerRevision() {
    const result = await HQ.api.get("config");
    const revision = result.ok ? result.body?.revision : null;
    return Number.isSafeInteger(revision) && revision >= 0 ? revision : null;
  }
  function staleRevision(result) {
    const detail = result.body?.detail;
    return result.status === 409 && detail && typeof detail === "object" && detail.reason === "stale revision" &&
      Number.isSafeInteger(detail.revision);
  }
  const BUILD = Object.freeze({
    start_trading: async () => ({mode: "paper"}),
    pause_ai: async () => {
      const revision = await ownerRevision();
      if (revision === null) return null;
      return {expected_revision: revision, profile: "MANAGE_ONLY", reason: "Owner HQ: pause AI, keep protection"};
    },
    resume: async () => {
      const revision = await ownerRevision();
      return revision === null ? null : {expected_revision: revision};
    },
    start_optimisation: async () => ({}),
    stop_safely: async () => ({position_policy: "manage-only"}),
  });
  async function submit(id, build) {
    const endpoint = ENDPOINT[id];
    if (!endpoint) return {kind: "failed", message: NOT_ACCEPTED};
    let body = pending.get(id)?.body || null;
    if (!body) {
      const builder = build || BUILD[id];
      if (!builder) return {kind: "failed", message: NOT_ACCEPTED};
      const built = await builder();
      if (!built) return {kind: "failed", message: "Couldn't read the current owner setting. Nothing changed; try again."};
      body = {request_id: HQ.uuid(), ...built};
      pending.set(id, body);
    }
    let result = await HQ.api.post(endpoint, body);
    if (id === "pause_ai" && staleRevision(result)) {
      // Nothing was stored for the first id (controls.py), so one retry uses a new id and the current revision.
      body = {...body, request_id: HQ.uuid(), expected_revision: result.body.detail.revision};
      pending.set(id, body);
      result = await HQ.api.post(endpoint, body);
    }
    return classify(id, result);
  }

  // --- Buttons and cards -----------------------------------------------------------------------------

  function button(card, {primary = false, small = false, focusKey} = {}) {
    const control = HQ.button(`button${primary ? "" : " button-secondary"}${small ? " hq-button-small" : ""}`, card.button_label);
    if (focusKey) control.dataset.focusKey = focusKey;
    if (pending.get(card.id)) control.append(el("span", "hq-pending-mark", " · outcome unknown"));
    control.addEventListener("click", () => open(card.id, control));
    if (card.available) return control;
    control.disabled = true;
    const fragment = document.createDocumentFragment();
    fragment.append(control);
    if (hasText(card.unavailable_because)) fragment.append(textEl("span", "hq-unavailable", card.unavailable_because));
    return fragment;
  }
  function decisionCard(card) {
    const box = el("article", "hq-decision");
    box.dataset.recommended = String(Boolean(card.recommended));
    box.append(el("h3", "hq-decision-title", card.title));
    if (card.recommended) {
      const why = el("p", "hq-recommend");
      why.append(HQ.icon("sev-act", "hq-icon hq-icon-small"), el("strong", "", "HQ recommends this. "));
      renderText(why, card.recommended_because);
      box.append(why);
    } else if (hasText(card.preview?.when)) {
      box.append(textEl("p", "hq-muted", card.preview.when));
    }
    if (pending.get(card.id)) box.append(el("p", "hq-pending", "An earlier press has an unknown outcome. Open it to resend the same request safely."));
    const actions = el("div", "hq-actions");
    actions.append(button(card, {primary: card.recommended, focusKey: `decide:${card.id}`}));
    box.append(actions);
    return box;
  }
  function pendingIds() { return [...TEAM, ...BILLS].filter(id => pending.get(id)); }

  HQ.view("decide", {
    deps: snapshot => [snapshot.decisions, snapshot.viewer, pendingIds()],
    render(root, snapshot) {
      if (!snapshot.viewer?.can_act) {
        const note = el("div", "hq-notice");
        note.dataset.tone = "info";
        note.append(HQ.icon("sev-info", "hq-icon hq-notice-icon"),
          textEl("p", "hq-notice-text", hasText(snapshot.viewer?.act_block_reason) ? snapshot.viewer.act_block_reason :
            [{t: "text", v: "View only. Decisions need the owner's sign-in on this device."}]));
        root.append(note);
        return;
      }
      const cards = TEAM.map(cardFor).filter(card => card?.shown);
      cards.sort((a, b) => Number(Boolean(b.recommended)) - Number(Boolean(a.recommended)));
      if (!cards.length) { root.append(el("p", "hq-empty", "No team controls apply right now.")); return; }
      const grid = el("div", "hq-decision-grid");
      for (const card of cards) grid.append(decisionCard(card));
      root.append(grid);
    },
  });

  HQ.view("bills", {
    deps: snapshot => [snapshot.decisions, snapshot.viewer?.can_act, pendingIds()],
    render(root, snapshot) {
      root.append(el("p", "hq-muted", "Bills are real costs you paid, such as your AI subscription. They never touch the practice account."));
      if (!snapshot.viewer?.can_act) {
        root.append(el("p", "hq-muted", "Bills can be recorded by the owner on a signed-in device."));
        return;
      }
      const cards = BILLS.map(cardFor).filter(card => card?.shown);
      const grid = el("div", "hq-decision-grid");
      for (const card of cards) grid.append(decisionCard(card));
      root.append(grid);
    },
  });

  // --- Preview sheet ---------------------------------------------------------------------------------

  function section(container, label, ...nodes) {
    const row = el("div", "hq-preview-row");
    row.append(el("dt", "", label));
    const value = el("dd", "");
    for (const node of nodes) if (node) value.append(node);
    row.append(value);
    container.append(row);
  }
  function bullets(items) {
    const box = el("ul", "hq-bullets");
    for (const item of list(items)) if (hasText(item)) box.append(textEl("li", "", item));
    return box.childNodes.length ? box : null;
  }
  function blockersList(card) {
    const items = list(card.facts?.predicted_blockers);
    if (!items.length) return null;
    const box = el("ul", "hq-bullets");
    for (const blocker of items) {
      const row = textEl("li", "", blocker.text);
      row.append(" ", el("span", `hq-chip ${blocker.definite ? "hq-chip-warn" : "hq-chip-soft"}`,
        blocker.definite ? "definite" : "may clear during its double-check"));
      box.append(row);
    }
    return box;
  }
  const REVERSIBLE = Object.freeze({yes: "Reversible", partly: "Partly reversible", no: "Permanent"});

  /* options: {notice, summary: Node[], build: () => Promise<body>, back: () => void} */
  function preview(body, card, options = {}) {
    if (options.notice) body.append(el("p", "hq-sheet-notice", options.notice));
    const stored = pending.get(card.id);
    if (stored) {
      const sent = parseIso(stored.saved_at);
      body.append(el("p", "hq-sheet-notice is-warning",
        `An earlier press of “${card.button_label}” has an unknown outcome${sent === null ? "" : ` (sent ${fmt.rel(sent)})`}. ` +
        "Confirming resends that same request, which is safe."));
    }
    const facts = el("dl", "hq-preview");
    const what = el("div", "");
    for (const node of options.summary || []) what.append(node);
    const listed = bullets(options.summary?.length ? card.preview?.what_happens?.slice(1) : card.preview?.what_happens);
    if (listed) what.append(listed);
    section(facts, "What happens", what);
    section(facts, "Cost", textEl("p", "", card.preview?.cost));
    const undo = textEl("p", "", card.preview?.undo);
    undo.append(" ", el("span", `hq-chip ${card.preview?.reversible === "no" ? "hq-chip-warn" : "hq-chip-soft"}`,
      REVERSIBLE[card.preview?.reversible] || "Check first"));
    section(facts, "Can I undo?", undo);
    const says = el("div", "");
    if (card.recommended) says.append(textEl("p", "", card.recommended_because));
    else if (hasText(card.preview?.when)) says.append(textEl("p", "", card.preview.when));
    if (!card.available && hasText(card.unavailable_because)) says.append(textEl("p", "hq-warn-text", card.unavailable_because));
    section(facts, "HQ says", says);
    const now = el("div", "");
    const listedNow = bullets(card.preview?.right_now);
    if (listedNow) now.append(listedNow);
    const blockers = blockersList(card);
    if (blockers) now.append(el("p", "", "Resume will probably be refused because:"), blockers);
    if (!now.childNodes.length) now.append(el("p", "hq-muted", "Nothing else is affected right now."));
    section(facts, "Right now", now);
    body.append(facts);

    let checked = card.weight !== "careful";
    if (card.weight === "careful") {
      const box = el("label", "hq-check");
      const input = el("input", "");
      input.type = "checkbox";
      input.addEventListener("change", () => { checked = input.checked; sync(); });
      box.append(input, el("span", "", "I've double-checked this"));
      body.append(box);
    }
    const status = el("p", "hq-form-status");
    status.setAttribute("role", "status");
    const actions = el("div", "hq-actions hq-sheet-actions");
    const cancel = HQ.button("button button-secondary", options.back ? "Back" : "Cancel");
    if (options.back) cancel.addEventListener("click", options.back);
    else cancel.dataset.hqClose = "";
    const confirm = HQ.button("button", stored ? "Send again" : card.confirm_label);
    confirm.dataset.autofocus = "";
    actions.append(cancel, confirm);
    body.append(status, actions);
    let busy = false;
    let finished = false;
    function sync() {
      const online = HQ.online();
      confirm.disabled = busy || finished || !card.available || !checked || !online || !HQ.snapshot()?.viewer?.can_act;
      if (!online && !busy && !finished) {
        status.textContent = OFFLINE;
        if (!actions.querySelector("[data-recheck]")) {
          const recheck = HQ.button("button button-secondary", "Check again");
          recheck.dataset.recheck = "";
          recheck.addEventListener("click", async () => {
            recheck.disabled = true;
            await HQ.refresh();
            recheck.remove();
            status.textContent = HQ.online() ? "Up to date. Review the details, then confirm." : OFFLINE;
            sync();
          });
          actions.prepend(recheck);
        }
      }
    }
    sync();
    confirm.addEventListener("click", async () => {
      if (confirm.disabled) return;
      busy = true;
      sync();
      status.dataset.tone = "";
      status.textContent = "Sending…";
      const outcome = await submit(card.id, options.build);
      busy = false;
      if (outcome.kind === "stale") {
        await HQ.refresh();
        open(card.id, null, "Things changed since you opened this. Here is the latest; check it and confirm again.");
        return;
      }
      status.textContent = outcome.message;
      status.dataset.tone = outcome.kind;
      if (outcome.kind === "done") {
        finished = true;
        HQ.poller.fast();
        HQ.toast(outcome.message);
        HQ.renderAll(true);
        const close = HQ.button("button", "Close");
        close.dataset.hqClose = "";
        actions.replaceChildren(close);
        close.focus();
      } else if (outcome.kind === "uncertain") {
        confirm.textContent = "Send again";
        HQ.renderAll(true);
      } else {
        finished = true;
        HQ.renderAll(true);
        const close = HQ.button("button button-secondary", "Close");
        close.dataset.hqClose = "";
        cancel.replaceWith(close);
      }
      sync();
    });
  }

  function open(id, opener, notice) {
    const card = cardFor(id);
    if (!card) { HQ.toast("That decision isn't available right now."); return; }
    if (id === "plan_checkin") { HQ.use("checkins")?.plan(opener); return; }
    if (id === "record_bill" && !pending.get(id)) { billWizard(card, opener); return; }
    if (id === "declare_bills_complete" && !pending.get(id)) { completenessWizard(card, opener); return; }
    const stored = pending.get(id);
    const summary = stored && BILLS.includes(id) ? [billSummary(id, stored.body)] : [];
    HQ.sheet.open({
      title: card.title,
      kind: "decision",
      from: opener,
      build: body => preview(body, card, {notice, summary}),
    });
  }

  // --- Bills ---------------------------------------------------------------------------------------

  const pad = number => String(number).padStart(2, "0");
  function money(amount, currency) {
    const [whole, fraction = ""] = amount.split(".");
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const cents = fraction.length >= 2 ? fraction : fraction.padEnd(2, "0");
    const prefix = {USD: "$", EUR: "€", GBP: "£"}[currency];
    return prefix ? `${prefix}${grouped}.${cents}` : `${grouped}.${cents} ${currency}`;
  }
  function percent(share) {
    const [whole, fraction = ""] = share.split(".");
    const digits = fraction.padEnd(2, "0");
    const integer = String(Number(`${whole}${digits.slice(0, 2)}`));
    const rest = digits.slice(2).replace(/0+$/, "");
    return rest ? `${integer}.${rest}` : integer;
  }
  function monthOptions(pastOnly) {
    const now = new Date(HQ.serverNow());
    const options = [];
    for (let back = pastOnly ? 1 : 0; back < 19; back += 1) {
      const start = new Date(now.getFullYear(), now.getMonth() - back, 1);
      options.push({value: `${start.getFullYear()}-${pad(start.getMonth() + 1)}`, label: fmt.monthWords(start.getTime())});
    }
    return options;
  }
  function monthPeriod(value) {
    const match = /^(\d{4})-(\d\d)$/.exec(value || "");
    if (!match) return null;
    const year = Number(match[1]);
    const month = Number(match[2]) - 1;
    const start = new Date(year, month, 1);
    const end = new Date(year, month + 1, 1);
    return {start: HQ.toIso(start.getTime()), end: HQ.toIso(end.getTime()), label: fmt.monthWords(start.getTime()), key: `${year}${pad(month + 1)}`};
  }
  function field(label, control, hint) {
    const box = el("label", "hq-field");
    box.append(el("span", "hq-field-label", label), control);
    if (hint) box.append(el("span", "hq-field-hint", hint));
    return box;
  }
  function select(options, value) {
    const control = el("select", "");
    for (const option of options) {
      const node = el("option", "", option.label);
      node.value = option.value;
      control.append(node);
    }
    if (value !== undefined) control.value = value;
    return control;
  }
  function choices(name, options, value, onChange) {
    const group = el("div", "hq-choices");
    group.setAttribute("role", "radiogroup");
    for (const option of options) {
      const box = el("label", "hq-choice");
      const input = el("input", "");
      input.type = "radio";
      input.name = name;
      input.value = option.value;
      input.checked = option.value === value;
      input.addEventListener("change", () => { if (input.checked) onChange(option.value); });
      const words = el("span", "hq-choice-words");
      words.append(el("span", "hq-choice-title", option.label));
      if (option.hint) words.append(el("span", "hq-field-hint", option.hint));
      box.append(input, words);
      group.append(box);
    }
    return group;
  }
  function wizardFrame(body, step, total, title) {
    body.replaceChildren();
    body.append(el("p", "hq-step", `Step ${step} of ${total}`), el("h3", "hq-sheet-label", title));
  }
  function wizardActions(body, onBack, onNext, nextLabel = "Next") {
    const error = el("p", "hq-form-status");
    error.setAttribute("role", "status");
    error.dataset.tone = "failed";
    const actions = el("div", "hq-actions hq-sheet-actions");
    const back = HQ.button("button button-secondary", onBack ? "Back" : "Cancel");
    if (onBack) back.addEventListener("click", onBack);
    else back.dataset.hqClose = "";
    const next = HQ.button("button", nextLabel);
    next.addEventListener("click", () => {
      const problem = onNext();
      error.textContent = problem || "";
    });
    actions.append(back, next);
    body.append(error, actions);
    return next;
  }
  function billSummary(id, data) {
    const box = el("p", "hq-bill-summary");
    if (id === "record_bill") {
      const period = parseIso(data.period_start);
      const month = period === null ? "the chosen period" : fmt.monthWords(period);
      const share = typeof data.graph_share === "string" ? percent(data.graph_share) : "?";
      box.textContent = `Saves a real bill of ${money(str(data.amount_native), str(data.native_currency))} for ${month}; ` +
        `${share}% counts as Trade Graph's cost. Spends nothing and doesn't touch the practice account.`;
    } else {
      const period = parseIso(data.period_start);
      const month = period === null ? "that month" : fmt.monthWords(period);
      const kinds = list(data.expense_kinds);
      const words = kinds.length === 2 ? "AI subscription or other running-cost" : kinds[0] === "other" ? "other running-cost" : "AI subscription";
      box.textContent = `Tells HQ there are no other ${words} bills for ${month}. A later bill for that month cancels this until you confirm again.`;
    }
    return box;
  }
  function billWizard(card, opener) {
    const today = new Date(HQ.serverNow());
    const draft = {
      kind: "subscription", preset: "hosting", amount: "", currency: "USD",
      month: `${today.getFullYear()}-${pad(today.getMonth() + 1)}`,
      date: `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`,
      share: "1", custom: "", split: "shared",
    };
    const presets = list(card.facts?.graph_share_presets).filter(value => DECIMAL.test(value));
    const shares = (presets.length ? presets : ["0.25", "0.5", "0.75", "1"]).map(value => ({value, label: `${percent(value)}%`}));
    HQ.sheet.open({
      title: card.title,
      kind: "decision",
      from: opener,
      build: body => {
        const stepKind = () => {
          wizardFrame(body, 1, 3, "What kind of bill is it?");
          const presetBox = el("div", "hq-subchoices");
          const renderPresets = () => {
            presetBox.replaceChildren();
            if (draft.kind !== "other") return;
            presetBox.append(choices("bill-preset", OTHER_PRESETS.map(([value, label]) => ({value, label})), draft.preset,
              value => { draft.preset = value; }));
          };
          body.append(choices("bill-kind", [
            {value: "subscription", label: "AI subscription", hint: "The plan the team's AI runs on."},
            {value: "other", label: "Other running cost", hint: "Hosting, electricity, internet or another service."},
          ], draft.kind, value => { draft.kind = value; renderPresets(); }), presetBox);
          renderPresets();
          wizardActions(body, null, () => { stepAmount(); return ""; });
        };
        const stepAmount = () => {
          wizardFrame(body, 2, 3, "Amount and dates");
          const amount = el("input", "");
          amount.inputMode = "decimal";
          amount.autocomplete = "off";
          amount.value = draft.amount;
          amount.placeholder = "20.00";
          amount.dataset.autofocus = "";
          const currency = select([{value: "USD", label: "US dollars (USD)"}, {value: "EUR", label: "Euros (EUR)"},
            {value: "GBP", label: "Pounds (GBP)"}], draft.currency);
          const month = select(monthOptions(false), draft.month);
          const date = el("input", "");
          date.type = "date";
          date.value = draft.date;
          date.max = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
          const row = el("div", "hq-field-row");
          row.append(field("Full bill amount", amount, "The whole bill, before Trade Graph's share."), field("Currency", currency));
          body.append(row, field("Month it covers", month), field("Bill date", date, "The date on the bill. It can't be in the future."));
          wizardActions(body, () => { draft.amount = amount.value.trim(); stepKind(); }, () => {
            draft.amount = amount.value.trim();
            draft.currency = currency.value;
            draft.month = month.value;
            draft.date = date.value;
            if (!DECIMAL.test(draft.amount) || !/[1-9]/.test(draft.amount)) return "Enter the amount as a number, for example 20 or 19.99.";
            const parts = /^(\d{4})-(\d\d)-(\d\d)$/.exec(draft.date);
            if (!parts) return "Choose the bill date.";
            const incurred = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])).getTime();
            if (!Number.isFinite(incurred) || incurred > HQ.serverNow()) return "The bill date can't be in the future.";
            if (!monthPeriod(draft.month)) return "Choose the month the bill covers.";
            stepShare();
            return "";
          });
        };
        const stepShare = () => {
          wizardFrame(body, 3, 3, "Trade Graph's share");
          body.append(el("p", "hq-muted", "Only this share counts as Trade Graph's cost. For example 50% of a $20 bill is $10."));
          const custom = el("input", "");
          custom.inputMode = "decimal";
          custom.placeholder = "0.4";
          custom.value = draft.custom;
          const customBox = field("Custom share (a number from 0 to 1)", custom);
          customBox.hidden = draft.share !== "custom";
          body.append(choices("bill-share", [...shares, {value: "custom", label: "Another share"}], draft.share, value => {
            draft.share = value;
            customBox.hidden = value !== "custom";
          }), customBox);
          body.append(el("h3", "hq-sheet-label", "Split between departments"));
          body.append(choices("bill-split", [
            {value: "shared", label: "Not split by department"},
            {value: "equal", label: "Equal across 5 scheduled departments", hint: "Leader, Research, Trader, Learning and Optimisation, 20% each."},
          ], draft.split, value => { draft.split = value; }));
          wizardActions(body, () => { draft.custom = custom.value.trim(); stepAmount(); }, () => {
            draft.custom = custom.value.trim();
            const share = draft.share === "custom" ? draft.custom : draft.share;
            if (!DECIMAL.test(share) || Number(share) > 1) return "Enter the share as a number from 0 to 1, for example 0.5.";
            stepPreview(share);
            return "";
          }, "Review");
        };
        const stepPreview = share => {
          const period = monthPeriod(draft.month);
          const parts = /^(\d{4})-(\d\d)-(\d\d)$/.exec(draft.date);
          const incurred = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
          const billId = `owner-bill-${parts[1]}${parts[2]}${parts[3]}-${HQ.randomHex(8)}`;
          const data = {
            record_type: "expense",
            bill_id: billId,
            billing_scope: draft.kind === "subscription" ? "ai-subscription" : `other-${draft.preset}`,
            expense_kind: draft.kind,
            amount_native: draft.amount,
            native_currency: draft.currency,
            incurred_at: HQ.toIso(incurred.getTime()),
            period_start: period.start,
            period_end: period.end,
            graph_share: share,
            allocation_policy: "Owner HQ: Trade Graph share of this bill",
            department_weights: draft.split === "shared" ? {shared: "1"} : {...SPLIT_FIVE},
            department_allocation_label: draft.split === "shared" ? "Owner HQ: not split by department" :
              "Owner HQ: equal split across five scheduled departments",
            evidence_ref: `owner-evidence:${billId}`,
          };
          body.replaceChildren();
          preview(body, card, {summary: [billSummary("record_bill", data)], build: async () => data, back: stepShare});
        };
        stepKind();
      },
    });
  }
  function completenessWizard(card, opener) {
    const months = monthOptions(true);
    const suggested = parseIso(card.facts?.suggested_period?.start);
    const draft = {month: months[0]?.value, kinds: "subscription"};
    if (suggested !== null) {
      const date = new Date(suggested);
      const value = `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;
      if (months.some(option => option.value === value)) draft.month = value;
    }
    HQ.sheet.open({
      title: card.title,
      kind: "decision",
      from: opener,
      build: body => {
        const stepChoose = () => {
          wizardFrame(body, 1, 1, "Which month, and which bills?");
          const month = select(months, draft.month);
          month.dataset.autofocus = "";
          body.append(field("Finished month", month, "Only months that have ended can be confirmed."));
          body.append(choices("complete-kinds", [
            {value: "subscription", label: "AI subscription bills"},
            {value: "other", label: "Other running-cost bills"},
            {value: "both", label: "Both kinds"},
          ], draft.kinds, value => { draft.kinds = value; }));
          wizardActions(body, null, () => {
            draft.month = month.value;
            const period = monthPeriod(draft.month);
            if (!period || parseIso(period.end) > HQ.serverNow()) return "Choose a month that has ended.";
            const data = {
              record_type: "completeness",
              period_start: period.start,
              period_end: period.end,
              expense_kinds: draft.kinds === "both" ? ["other", "subscription"] : [draft.kinds],
              evidence_ref: `owner-evidence:complete-${period.key}-${HQ.randomHex(8)}`,
            };
            body.replaceChildren();
            preview(body, card, {summary: [billSummary("declare_bills_complete", data)], build: async () => data, back: stepChoose});
            return "";
          }, "Review");
        };
        stepChoose();
      },
    });
  }

  HQ.define("decide", {button, open, pending, submit, classify, cardFor, preview});
})();
