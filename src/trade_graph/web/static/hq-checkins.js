/* Trade Graph HQ owner check-ins (spec §C.7): list, plan sheet, done / skip / move / cancel and .ics export.
   Shown only when the snapshot says the owner's check-in store is available. HQ sends nothing anywhere. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const {el, list, obj, str, fmt, parseIso} = HQ;

  const STATE_LABEL = Object.freeze({
    planned: "Planned", due: "Due now", overdue: "Overdue", missed: "Missed", done: "Done", skipped: "Skipped",
    cancelled: "Cancelled",
  });
  const KIND_LABEL = Object.freeze({
    baseline: "Baseline", interval: "Regular review", bedtime: "Bedtime check", next_day: "Next-day review", custom: "Custom",
  });
  const TERMINAL = new Set(["done", "skipped", "cancelled"]);
  const UNCERTAIN = "We don't know if it went through. Pressing again resends the same request, which is safe.";
  const SAME_AGAIN = "Sending again uses what you sent before.";
  const REPLAYED = "Already saved earlier.";
  const ACTION_WORDS = Object.freeze({done: "mark it done", skip: "skip it", reschedule: "move it", cancel: "cancel it", plan: "plan it"});
  const pad = number => String(number).padStart(2, "0");

  const store = () => HQ.snapshot()?.checkins || null;
  const find = id => list(store()?.items).find(item => item.checkin_id === id) || null;
  const canWrite = () => Boolean(HQ.snapshot()?.viewer?.can_act && store()?.available);
  const pendingKey = id => `checkin:${id}`;
  // HQ.readPending drops saved requests older than a day, so an old one is never resent (review S4).
  const readPending = key => {
    const stored = HQ.readPending(key);
    return stored && stored.body && typeof stored.body.request_id === "string" ? stored : null;
  };
  const readPlanPending = () => {
    const stored = HQ.readPending("plan_checkin");
    return stored && Array.isArray(stored.items) ? stored : null;
  };

  // --- Sending ---------------------------------------------------------------------------------------

  function conflict(detail) {
    const text = typeof detail === "string" ? detail : "";
    if (/unknown check-in/.test(text)) return "HQ doesn't know that check-in any more. Nothing changed.";
    if (/already planned/.test(text)) return "That check-in already exists. Nothing changed.";
    if (/already closed/.test(text)) return "That check-in is already closed. Nothing changed.";
    return "That wasn't accepted. Nothing changed.";
  }
  function classify(result) {
    if (result.network || result.status >= 500) return {kind: "uncertain", message: UNCERTAIN};
    if (result.ok) return {kind: "done", checkin: result.body?.checkin || null, replayed: result.body?.replayed === true};
    if (result.status === 401) return {kind: "failed", message: "You're signed out. Sign in again, then try once more."};
    if (result.status === 409) return {kind: "failed", message: conflict(result.body?.detail)};
    if (result.status === 404 || result.status === 405) {
      return {kind: "failed", message: "Check-ins aren't available on this installation yet. Nothing changed."};
    }
    if (result.status === 422) {
      return {kind: "failed", message: "That wasn't accepted. Check the time (up to 60 days ahead) and the words: no file paths or secret-looking text. Nothing changed."};
    }
    return {kind: "failed", message: "That wasn't accepted. Nothing changed."};
  }
  // Returns the outcome plus the body actually sent, so success messages describe what was saved, not
  // what the form shows now (review S5).
  async function sendOne(key, build) {
    let body = readPending(key)?.body || null;
    if (!body) {
      body = {request_id: HQ.uuid(), ...build()};
      HQ.savePending(key, {request_id: body.request_id, body});
    }
    const outcome = classify(await HQ.api.post("checkins", body));
    if (outcome.kind !== "uncertain") HQ.clearPending(key);
    return {...outcome, sent: body};
  }
  const saved = (outcome, words) => outcome.kind !== "done" ? null : outcome.replayed ? REPLAYED : words;
  // Owner notes may not contain control characters (owner_checkins validation), so line breaks become " / ".
  const cleanNote = value => str(value).replace(/\r\n|\r|\n/g, " / ").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 500);
  const cleanTitle = value => str(value).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 80);
  function newId(ms) {
    const date = new Date(ms);
    return `c-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}-${HQ.randomHex(6)}`;
  }

  // --- Sheet pieces ----------------------------------------------------------------------------------

  function status() {
    const node = el("p", "hq-form-status");
    node.setAttribute("role", "status");
    return node;
  }
  function confirmRow(label, onConfirm, {back} = {}) {
    const row = el("div", "hq-actions hq-sheet-actions");
    const cancel = HQ.button("button button-secondary", back ? "Back" : "Cancel");
    if (back) cancel.addEventListener("click", back);
    else cancel.dataset.hqClose = "";
    const go = HQ.button("button", label);
    go.addEventListener("click", onConfirm);
    row.append(cancel, go);
    return {row, go, cancel};
  }
  /* After an unknown outcome the saved request is what gets resent, so `lock` makes the form read-only
     and the note says so; edits can't be silently dropped (review S5). `extra` buttons join Close. */
  function finish(row, go, cancel, note, outcome, message, {lock, extra} = {}) {
    note.textContent = message || outcome.message || "";
    note.dataset.tone = outcome.kind;
    if (outcome.kind === "uncertain") {
      go.disabled = false;
      go.textContent = "Send again";
      if (lock) {
        lock();
        note.textContent = `${note.textContent} ${SAME_AGAIN}`;
      }
      return;
    }
    const close = HQ.button("button", "Close");
    close.dataset.hqClose = "";
    row.replaceChildren(...(extra || []), close);
    close.focus();
    HQ.poller.fast();
    HQ.renderAll(true);
  }
  function guard(go, note) {
    if (!HQ.online()) { note.textContent = "Can't confirm the current state."; go.disabled = true; return false; }
    return true;
  }
  function pendingNotice(body, key) {
    const stored = readPending(key);
    if (!stored) return null;
    const words = ACTION_WORDS[stored.body.action] || "change it";
    const sent = parseIso(stored.saved_at);
    const node = el("p", "hq-sheet-notice is-warning",
      `An earlier request to ${words}${sent === null ? "" : ` (sent ${fmt.rel(sent)})`} has an unknown outcome. Confirming resends that same request, which is safe.`);
    body.append(node);
    return stored;
  }
  function itemLine(item) {
    const line = el("p", "hq-checkin-when");
    const ms = parseIso(item.planned_at);
    line.append(ms === null ? "Time not recorded" : fmt.localDateTime(ms));
    line.append(` · ${KIND_LABEL[item.prompt_kind] || "Review"}`);
    return line;
  }

  // --- Done / skip / move / cancel ----------------------------------------------------------------------

  function done(id, opener) {
    const item = find(id);
    if (!item || !canWrite()) return;
    HQ.sheet.open({
      title: `Mark “${item.title}” done`,
      kind: "checkin",
      from: opener,
      build: body => {
        body.append(itemLine(item));
        const stored = pendingNotice(body, pendingKey(id));
        const area = el("textarea", "");
        area.maxLength = 500;
        area.rows = 3;
        area.placeholder = "Paste the 2-line verdict";
        area.dataset.autofocus = "";
        const label = el("label", "hq-field");
        label.append(el("span", "hq-field-label", "Note (optional)"), area,
          el("span", "hq-field-hint", "Up to 500 characters. Line breaks are saved as “ / ”."));
        if (!stored) body.append(label);
        const note = status();
        const {row, go, cancel} = confirmRow(stored ? "Send again" : "Mark done", async () => {
          if (!guard(go, note)) return;
          go.disabled = true;
          note.textContent = "Saving…";
          const text = cleanNote(area.value);
          const outcome = await sendOne(pendingKey(id), () => ({checkin_id: id, action: "done", ...(text ? {note: text} : {})}));
          // The follow-up offer lives in the finished sheet itself; a toast would sit under the sheet (U4).
          const next = outcome.kind === "done" && canWrite() ? planNextButton() : null;
          finish(row, go, cancel, note, outcome, saved(outcome, "Marked done."), {
            lock: () => { area.readOnly = true; },
            extra: next ? [next] : [],
          });
        });
        body.append(note, row);
      },
    });
  }
  // "Plan the next one in 4 h": one tap after Mark done, rounded up to the next 5 minutes.
  function nextSlot() {
    const at = new Date(HQ.serverNow() + 4 * 3600000);
    at.setMinutes(Math.ceil(at.getMinutes() / 5) * 5, 0, 0);
    return {at: at.getTime(), kind: "interval", title: "Follow-up review"};
  }
  function planNextButton() {
    const control = HQ.button("button button-secondary", "Plan the next one in 4 h");
    control.addEventListener("click", () => plan(control, [nextSlot()]));
    return control;
  }
  function more(id, opener) {
    const item = find(id);
    if (!item || !canWrite()) return;
    HQ.sheet.open({
      title: item.title,
      kind: "checkin",
      from: opener,
      build: body => {
        const menu = () => {
          body.replaceChildren(itemLine(item));
          const stored = readPending(pendingKey(id));
          if (stored) {
            pendingNotice(body, pendingKey(id));
            const note = status();
            const {row, go, cancel} = confirmRow("Send again", async () => {
              if (!guard(go, note)) return;
              go.disabled = true;
              const outcome = await sendOne(pendingKey(id), () => stored.body);
              finish(row, go, cancel, note, outcome, saved(outcome, "Saved."));
            });
            body.append(note, row);
            return;
          }
          const options = el("div", "hq-menu");
          for (const [label, hint, run] of [
            ["Move it", "Pick a new time.", moveForm],
            ["Skip it", "Keeps a record that you skipped this one.", skipForm],
            ["Cancel it", "Removes it from your plan; the history stays.", cancelForm],
          ]) {
            const choice = HQ.button("hq-menu-item");
            choice.append(el("span", "hq-choice-title", label), el("span", "hq-field-hint", hint));
            choice.addEventListener("click", run);
            options.append(choice);
          }
          options.firstChild.dataset.autofocus = "";
          body.append(options);
          options.firstChild.focus();
        };
        const moveForm = () => {
          body.replaceChildren(itemLine(item));
          const input = el("input", "");
          input.type = "datetime-local";
          const start = parseIso(item.planned_at) ?? HQ.serverNow();
          const local = new Date(Math.max(start, HQ.serverNow() + 3600000));
          local.setMinutes(Math.ceil(local.getMinutes() / 5) * 5, 0, 0);
          input.value = `${local.getFullYear()}-${pad(local.getMonth() + 1)}-${pad(local.getDate())}T${pad(local.getHours())}:${pad(local.getMinutes())}`;
          const label = el("label", "hq-field");
          label.append(el("span", "hq-field-label", "New time (your time)"), input, el("span", "hq-field-hint", "Up to 60 days ahead."));
          body.append(label);
          input.focus();
          const note = status();
          const {row, go, cancel} = confirmRow("Move it", async () => {
            const ms = parseLocal(input.value);
            if (ms === null || ms <= HQ.serverNow() || ms > HQ.serverNow() + 60 * 86400000) {
              note.textContent = "Choose a time in the future, up to 60 days ahead.";
              return;
            }
            if (!guard(go, note)) return;
            go.disabled = true;
            note.textContent = "Saving…";
            const outcome = await sendOne(pendingKey(id), () => ({checkin_id: id, action: "reschedule", planned_at: HQ.toIso(ms)}));
            const sentAt = parseIso(outcome.sent?.planned_at);
            finish(row, go, cancel, note, outcome, saved(outcome, sentAt === null ? "Moved." : `Moved to ${fmt.localDateTime(sentAt)}.`), {
              lock: () => { input.disabled = true; },
            });
          }, {back: menu});
          body.append(note, row);
        };
        const skipForm = () => {
          body.replaceChildren(itemLine(item));
          const area = el("textarea", "");
          area.maxLength = 500;
          area.rows = 2;
          area.placeholder = "Why you skipped it (optional)";
          const label = el("label", "hq-field");
          label.append(el("span", "hq-field-label", "Note (optional)"), area);
          body.append(label);
          area.focus();
          const note = status();
          const {row, go, cancel} = confirmRow("Skip it", async () => {
            if (!guard(go, note)) return;
            go.disabled = true;
            const text = cleanNote(area.value);
            const outcome = await sendOne(pendingKey(id), () => ({checkin_id: id, action: "skip", ...(text ? {note: text} : {})}));
            finish(row, go, cancel, note, outcome, saved(outcome, "Skipped."), {
              lock: () => { area.readOnly = true; },
            });
          }, {back: menu});
          body.append(note, row);
        };
        const cancelForm = () => {
          body.replaceChildren(itemLine(item));
          body.append(el("p", "", "This removes it from your plan. Its history stays, and HQ sends nothing anywhere."));
          const note = status();
          const {row, go, cancel} = confirmRow("Yes, cancel it", async () => {
            if (!guard(go, note)) return;
            go.disabled = true;
            const outcome = await sendOne(pendingKey(id), () => ({checkin_id: id, action: "cancel"}));
            finish(row, go, cancel, note, outcome, saved(outcome, "Cancelled."), {lock: () => {}});
          }, {back: menu});
          body.append(note, row);
          go.focus();
        };
        menu();
      },
    });
  }
  function parseLocal(value) {
    const match = /^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)/.exec(value || "");
    if (!match) return null;
    const ms = new Date(+match[1], +match[2] - 1, +match[3], +match[4], +match[5]).getTime();
    return Number.isFinite(ms) ? ms : null;
  }

  // --- Plan ----------------------------------------------------------------------------------------

  function usualSet() {
    const now = HQ.serverNow();
    const today = new Date(now);
    const last = parseIso(store()?.last_done_at);
    const doneToday = last !== null && HQ.dayDiff(last) === 0;
    const slots = [[12, 30, "interval", "Midday review"], [16, 30, "interval", "Afternoon review"],
      [20, 30, "interval", "Evening review"], [22, 30, "bedtime", "Bedtime check"]];
    const result = [];
    for (const [hour, minute, kind, title] of slots) {
      const at = new Date(today.getFullYear(), today.getMonth(), today.getDate(), hour, minute).getTime();
      if (at > now) result.push({at, kind, title});
    }
    if (result.length && !doneToday && result[0].kind === "interval") result[0] = {...result[0], kind: "baseline", title: "Baseline review"};
    return result;
  }
  function planOptions() {
    const options = [];
    const suggested = parseIso(store()?.suggested_at);
    if (suggested !== null && suggested > HQ.serverNow()) {
      options.push({value: "suggested", label: "After the Trader's next turn",
        hint: `${fmt.dayClock(suggested)}, so the review sees its result.`,
        items: () => [{at: suggested, kind: "interval", title: "Review after the Trader's turn"}]});
    }
    const usual = usualSet();
    options.push({value: "usual", label: "Today's usual set",
      hint: usual.length ? usual.map(slot => fmt.clock(slot.at)).join(", ") : "All of today's usual times have passed.",
      disabled: !usual.length, items: () => usual});
    const tomorrow = new Date(HQ.serverNow());
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setMinutes(Math.ceil(tomorrow.getMinutes() / 5) * 5, 0, 0);
    options.push({value: "tomorrow", label: "Tomorrow at this time", hint: fmt.dayClock(tomorrow.getTime()),
      items: () => [{at: tomorrow.getTime(), kind: "next_day", title: "Next-day review"}]});
    options.push({value: "custom", label: "Custom", hint: "Pick a time, a name and how often.", items: null});
    return options;
  }
  function plan(opener, preset) {
    if (!canWrite()) return;
    const card = HQ.use("decide")?.cardFor("plan_checkin");
    HQ.sheet.open({
      title: card?.title || "Plan a review check-in",
      kind: "checkin",
      from: opener,
      build: body => {
        const stored = readPlanPending();
        if (stored && stored.items.some(entry => entry.state === "pending")) {
          previewPlan(body, card, stored.items.map(entry => entry.body), true);
          return;
        }
        if (preset) { previewPlan(body, card, preset.map(toBody), false); return; }
        choose(body, card);
      },
    });
  }
  function toBody(slot) {
    return {checkin_id: newId(slot.at), action: "plan", planned_at: HQ.toIso(slot.at), prompt_kind: slot.kind, title: slot.title};
  }
  function choose(body, card) {
    body.replaceChildren();
    body.append(el("p", "hq-muted", "Check-ins are reminders for you to review the run with your separate AI chat. They show in your HQ calendar."));
    const options = planOptions();
    let chosen = options.find(option => !option.disabled)?.value || "custom";
    const group = el("div", "hq-choices");
    group.setAttribute("role", "radiogroup");
    const custom = el("div", "hq-subchoices");
    const when = el("input", "");
    when.type = "datetime-local";
    const start = new Date(HQ.serverNow() + 3600000);
    start.setMinutes(Math.ceil(start.getMinutes() / 5) * 5, 0, 0);
    when.value = `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}T${pad(start.getHours())}:${pad(start.getMinutes())}`;
    const title = el("input", "");
    title.maxLength = 80;
    title.value = "Review";
    const kind = el("select", "");
    for (const [value, label] of Object.entries(KIND_LABEL)) {
      const option = el("option", "", label);
      option.value = value;
      kind.append(option);
    }
    kind.value = "interval";
    const repeat = el("select", "");
    for (let days = 1; days <= 7; days += 1) {
      const option = el("option", "", days === 1 ? "Just once" : `Every day for ${days} days`);
      option.value = String(days);
      repeat.append(option);
    }
    const labelled = (text, control, hint) => {
      const box = el("label", "hq-field");
      box.append(el("span", "hq-field-label", text), control);
      if (hint) box.append(el("span", "hq-field-hint", hint));
      return box;
    };
    custom.append(labelled("When (your time)", when), labelled("Name", title, "Up to 80 characters."),
      labelled("Kind of review", kind), labelled("Repeat", repeat, "Daily repeats keep the same local time, even across clock changes."));
    for (const option of options) {
      const box = el("label", "hq-choice");
      const input = el("input", "");
      input.type = "radio";
      input.name = "checkin-plan";
      input.value = option.value;
      input.disabled = Boolean(option.disabled);
      input.checked = option.value === chosen;
      input.addEventListener("change", () => { chosen = option.value; custom.hidden = chosen !== "custom"; });
      const words = el("span", "hq-choice-words");
      words.append(el("span", "hq-choice-title", option.label), el("span", "hq-field-hint", option.hint));
      box.append(input, words);
      group.append(box);
    }
    custom.hidden = chosen !== "custom";
    body.append(group, custom);
    const note = status();
    note.dataset.tone = "failed";
    const {row} = confirmRow("Next", () => {
      const option = options.find(entry => entry.value === chosen);
      let slots;
      if (option?.items) slots = option.items();
      else {
        const first = parseLocal(when.value);
        const name = cleanTitle(title.value);
        if (first === null || first <= HQ.serverNow()) { note.textContent = "Choose a time in the future."; return; }
        if (!name) { note.textContent = "Give the check-in a short name."; return; }
        const date = new Date(first);
        slots = [];
        for (let day = 0; day < Number(repeat.value || 1); day += 1) {
          const at = new Date(date.getFullYear(), date.getMonth(), date.getDate() + day, date.getHours(), date.getMinutes()).getTime();
          slots.push({at, kind: kind.value, title: name});
        }
      }
      const limit = HQ.serverNow() + 60 * 86400000;
      if (!slots.length || slots.some(slot => slot.at > limit)) { note.textContent = "Check-ins can be planned up to 60 days ahead."; return; }
      previewPlan(body, card, slots.map(toBody), false, () => choose(body, card));
    });
    body.append(note, row);
  }
  function previewPlan(body, card, bodies, resending, back) {
    body.replaceChildren();
    if (resending) {
      body.append(el("p", "hq-sheet-notice is-warning",
        "An earlier plan has an unknown outcome. Confirming resends those same requests, which is safe."));
    }
    const rows = el("ul", "hq-bullets");
    for (const entry of bodies) {
      const ms = parseIso(entry.planned_at);
      rows.append(el("li", "", `${ms === null ? "?" : fmt.localDateTime(ms)} · ${entry.title} (${(KIND_LABEL[entry.prompt_kind] || "Review").toLowerCase()})`));
    }
    const facts = el("dl", "hq-preview");
    const add = (label, node) => {
      const row = el("div", "hq-preview-row");
      const value = el("dd", "");
      value.append(node);
      row.append(el("dt", "", label), value);
      facts.append(row);
    };
    add("Times", rows);
    add("What happens", HQ.textEl("p", "", card?.preview?.what_happens?.[0] || [{t: "text", v: "Adds reminders to your HQ calendar. HQ sends nothing anywhere."}]));
    add("Cost", HQ.textEl("p", "", card?.preview?.cost || [{t: "text", v: "Free."}]));
    add("Can I undo?", HQ.textEl("p", "", card?.preview?.undo || [{t: "text", v: "Yes: move or cancel any time."}]));
    body.append(facts);
    const note = status();
    const count = bodies.length;
    const label = resending ? "Send again" : `Yes, add ${count} check-in${count === 1 ? "" : "s"}`;
    const {row, go, cancel} = confirmRow(label, async () => {
      if (!guard(go, note)) return;
      go.disabled = true;
      note.textContent = "Saving…";
      let stored = readPlanPending();
      if (!stored) {
        stored = {saved_at: HQ.toIso(HQ.serverNow()), items: bodies.map(entry => ({body: {request_id: HQ.uuid(), ...entry}, state: "pending"}))};
        HQ.store.setJson(HQ.KEYS.pending("plan_checkin"), stored);
      }
      const failures = [];
      let uncertain = false;
      let replayed = 0;
      for (const entry of stored.items) {
        if (entry.state !== "pending") continue;
        const outcome = classify(await HQ.api.post("checkins", entry.body));
        if (outcome.kind === "uncertain") { uncertain = true; break; }
        entry.state = outcome.kind === "done" ? "done" : "failed";
        if (outcome.replayed) replayed += 1;
        if (outcome.kind !== "done") failures.push(`${entry.body.title}: ${outcome.message}`);
        HQ.store.setJson(HQ.KEYS.pending("plan_checkin"), stored);   // keeps its first saved_at
      }
      const added = stored.items.filter(entry => entry.state === "done").length;
      if (uncertain) {
        // Going back to pick other times would resend these same requests, so the way back closes (S5).
        finish(row, go, cancel, note, {kind: "uncertain", message: UNCERTAIN}, null, {lock: () => {
          if (!back) return;
          const close = HQ.button("button button-secondary", "Close");
          close.dataset.hqClose = "";
          cancel.replaceWith(close);
        }});
        return;
      }
      HQ.clearPending("plan_checkin");
      const fresh = added - replayed;
      const words = [];
      if (fresh > 0) words.push(`Added ${fresh} check-in${fresh === 1 ? "" : "s"}. Re-download your calendar file if you use one.`);
      if (replayed > 0) words.push(REPLAYED);
      if (!added) words.push("Nothing was added.");
      // The finished sheet says it (role="status"); a toast here would only repeat it.
      finish(row, go, cancel, note, {kind: failures.length ? "failed" : "done"}, [...words, ...failures].join(" "));
    }, {back: resending ? null : back});
    go.dataset.autofocus = "";
    body.append(note, row);
    go.focus();
  }

  // --- .ics export ---------------------------------------------------------------------------------

  function icsEscape(value) { return str(value).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n"); }
  function icsStamp(ms) {
    const date = new Date(ms);
    return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`;
  }
  // RFC 5545 folds lines longer than 75 octets.
  function fold(line) {
    const encoder = new TextEncoder();
    const parts = [];
    let current = "";
    for (const character of line) {
      if (encoder.encode(current + character).length > (parts.length ? 74 : 75)) {
        parts.push(current);
        current = character;
      } else current += character;
    }
    parts.push(current);
    return parts.join("\r\n ");
  }
  function icsText() {
    const now = HQ.serverNow();
    const items = list(store()?.items).filter(item => !TERMINAL.has(item.state) && (parseIso(item.planned_at) ?? 0) > now);
    if (!items.length) return null;
    const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Trade Graph//HQ check-ins//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"];
    for (const item of items) {
      lines.push("BEGIN:VEVENT", `UID:${item.checkin_id}@trade-graph-hq`, `DTSTAMP:${icsStamp(now)}`,
        `DTSTART:${icsStamp(parseIso(item.planned_at))}`, "DURATION:PT15M",
        `SUMMARY:${icsEscape(`Trade Graph check-in: ${item.title}`)}`,
        "DESCRIPTION:Open Trade Graph HQ and tap Copy review prompt.",
        "BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${icsEscape(`Trade Graph check-in: ${item.title}`)}`, "TRIGGER:-PT10M",
        "END:VALARM", "END:VEVENT");
    }
    lines.push("END:VCALENDAR");
    return `${lines.map(fold).join("\r\n")}\r\n`;
  }
  function exportIcs(opener) {
    const text = icsText();
    if (!text) { HQ.toast("No future check-ins to add. Plan one first."); return; }
    try {
      const url = URL.createObjectURL(new Blob([text], {type: "text/calendar"}));
      const anchor = el("a", "hq-hidden-download");
      anchor.href = url;
      anchor.download = "trade-graph-checkins.ics";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 20000);
      HQ.toast("Calendar file ready. Re-download after changes; your calendar won't update by itself.");
    } catch (_) {
      HQ.sheet.open({
        title: "Calendar file",
        kind: "copy",
        from: opener,
        build: body => {
          body.append(el("p", "hq-muted", "Saving a file isn't available here. Copy this text into a file named trade-graph-checkins.ics."));
          const area = el("textarea", "hq-copy-area");
          area.readOnly = true;
          area.rows = 12;
          area.value = text;
          area.setAttribute("aria-label", "Calendar file text");
          body.append(area);
        },
      });
    }
  }

  // --- Buttons used by other sections ---------------------------------------------------------------------

  function actions(id, container, {focusPrefix = "checkin", compact = false} = {}) {
    const item = find(id);
    if (!item) return;
    const copy = HQ.button(`button ${compact ? "button-secondary hq-button-small" : ""}`.trim(), compact ? "Copy prompt" : "Copy review prompt");
    copy.dataset.focusKey = `${focusPrefix}:copy`;
    copy.prepend(HQ.icon("copy", "hq-icon hq-icon-small"));
    copy.addEventListener("click", () => HQ.use("prompts")?.copyReview({checkin: item}));
    container.append(copy);
    if (!canWrite() || TERMINAL.has(item.state)) return;
    const mark = HQ.button(`button button-secondary${compact ? " hq-button-small" : ""}`, "Done");
    mark.dataset.focusKey = `${focusPrefix}:done`;
    mark.addEventListener("click", () => done(id, mark));
    const menu = HQ.button(`button button-secondary hq-button-icon${compact ? " hq-button-small" : ""}`, "⋯");
    menu.setAttribute("aria-label", `More for “${item.title}”: move, skip or cancel`);
    menu.dataset.focusKey = `${focusPrefix}:more`;
    menu.addEventListener("click", () => more(id, menu));
    container.append(mark, menu);
    if (readPending(pendingKey(id))) container.append(el("span", "hq-pending-mark", "outcome unknown"));
  }
  function itemRow(item, prefix) {
    const row = el("li", "hq-checkin");
    row.dataset.state = str(item.state);
    const head = el("p", "hq-checkin-head");
    head.append(el("span", "hq-chip hq-checkin-state", STATE_LABEL[item.state] || str(item.state)), el("strong", "", item.title));
    row.append(head, itemLine(item));
    if (item.state === "done" && parseIso(item.done_at) !== null) {
      const when = el("p", "hq-muted", "Done ");
      when.append(HQ.textEl("span", "", [{t: "time", at: item.done_at, style: "day_clock"}], {inert: true}));
      row.append(when);
    }
    if (item.note) row.append(el("p", "hq-checkin-note", `“${item.note}”`));
    const buttons = el("div", "hq-actions hq-actions-compact");
    actions(item.checkin_id, buttons, {focusPrefix: `${prefix}:${item.checkin_id}`, compact: true});
    if (buttons.childNodes.length) row.append(buttons);
    return row;
  }
  function toolbar(prefix) {
    const bar = el("div", "hq-actions");
    if (canWrite()) {
      const add = HQ.button("button", "Plan a check-in");
      add.prepend(HQ.icon("plus", "hq-icon hq-icon-small"));
      add.dataset.focusKey = `${prefix}:plan`;
      add.addEventListener("click", () => plan(add));
      bar.append(add);
    }
    const ics = HQ.button("button button-secondary", "Add to my calendar (.ics)");
    ics.dataset.focusKey = `${prefix}:ics`;
    ics.addEventListener("click", () => exportIcs(ics));
    bar.append(ics);
    return bar;
  }

  HQ.view("checkins", {
    deps: snapshot => [snapshot.checkins, snapshot.viewer?.can_act],
    render(root, snapshot) {
      const data = snapshot.checkins;
      if (!data) return;
      if (!data.available) {
        root.append(el("p", "hq-muted", "Check-ins aren't available on this installation yet."));
        return;
      }
      root.append(toolbar("cal-checkins"));
      root.append(el("p", "hq-field-hint", "Re-download after changes; your calendar won't update by itself."));
      const items = list(data.items).slice().sort((a, b) => (parseIso(a.planned_at) ?? 0) - (parseIso(b.planned_at) ?? 0));
      if (!items.length) { root.append(el("p", "hq-empty", "No check-ins planned yet.")); return; }
      const rows = el("ul", "hq-checkin-list");
      for (const item of items) rows.append(itemRow(item, "cal-checkins"));
      root.append(rows);
    },
  });

  HQ.view("review", {
    deps: snapshot => [snapshot.checkins, snapshot.viewer?.can_act],
    render(root, snapshot) {
      const data = snapshot.checkins;
      const copy = HQ.button("button button-secondary", "Copy review prompt");
      copy.prepend(HQ.icon("copy", "hq-icon hq-icon-small"));
      copy.dataset.focusKey = "review:copy";
      copy.addEventListener("click", () => HQ.use("prompts")?.copyReview({}));
      if (!data || !data.available) {
        root.append(el("p", "hq-muted", "Paste the review prompt into your separate AI chat. It asks the right questions without changing anything."));
        const bar = el("div", "hq-actions");
        bar.append(copy);
        root.append(bar);
        return;
      }
      const next = find(data.next_id);
      if (next) {
        root.append(el("h3", "hq-subhead", "Next check-in"));
        const rows = el("ul", "hq-checkin-list");
        rows.append(itemRow(next, "review"));
        root.append(rows);
      } else root.append(el("p", "hq-muted", "No check-in is planned."));
      const last = parseIso(data.last_done_at);
      if (last !== null) {
        const line = el("p", "hq-muted", "Last one done ");
        line.append(HQ.textEl("span", "", [{t: "time", at: data.last_done_at, style: "relative"}], {inert: true}), ".");
        root.append(line);
      }
      const bar = toolbar("review");
      bar.prepend(copy);
      root.append(bar);
    },
  });

  HQ.define("checkins", {actions, done, more, plan, exportIcs, icsText, usualSet});
})();
