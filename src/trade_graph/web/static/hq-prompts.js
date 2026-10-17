/* Trade Graph HQ review prompts. Built in the browser from the latest snapshot plus settings kept on this
   device; nothing here is ever sent to Trade Graph. Copy: clipboard, then share sheet, then a text box. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const {el, button, list, obj, str, fmt, parseIso} = HQ;

  const DEFAULTS = Object.freeze({
    project_folder: "my Trade Graph project folder",
    review_notes_folder: "my private review notes",
    authorised_model: "",
    dashboard_address: "",
  });
  const FIELDS = Object.freeze([
    ["project_folder", "Project folder", "Where your review chat finds the Trade Graph project."],
    ["review_notes_folder", "Review notes folder", "Where your review chat keeps its private notes."],
    ["authorised_model", "Authorised model", "Leave empty to say “the model I authorised for Trade Graph”."],
    ["dashboard_address", "Dashboard address", "The address your review chat should open."],
  ]);
  const EMPTY_MEANS = Object.freeze({authorised_model: "the model I authorised for Trade Graph"});
  const KIND_PARAGRAPH = Object.freeze({
    baseline: "This is a baseline: record current values for later comparison.",
    interval: "Compare with my previous check-in.",
    bedtime: "Check overnight readiness: overnight turns, allowance reserve, recent problems, open practice positions, and the PC's sleep settings (report only). Recommend 'leave running' or 'pause the AI', naming the HQ button I would press.",
    next_day: "Compare the last 24 hours with the check-in a day ago; separate practice results from fees and unknown costs; one day does not establish profitability.",
  });
  // A live account's prompt never calls its positions or results "practice" (review C1).
  const KIND_PARAGRAPH_LIVE = Object.freeze({
    ...KIND_PARAGRAPH,
    bedtime: "Check overnight readiness: overnight turns, allowance reserve, recent problems, open positions, and the PC's sleep settings (report only). Recommend 'leave running' or 'pause the AI', naming the HQ button I would press.",
    next_day: "Compare the last 24 hours with the check-in a day ago; separate trading results from fees and unknown costs; one day does not establish profitability.",
  });
  const kindParagraph = (kind, live) => (live ? KIND_PARAGRAPH_LIVE : KIND_PARAGRAPH)[kind] || null;

  function settings() {
    const saved = obj(HQ.store.getJson(HQ.KEYS.promptSettings));
    const result = {};
    for (const key of Object.keys(DEFAULTS)) {
      const value = typeof saved[key] === "string" ? saved[key].trim().slice(0, 300) : "";
      result[key] = value || DEFAULTS[key];
    }
    if (!result.dashboard_address) result.dashboard_address = location.origin;
    return result;
  }
  function save(key, value) {
    const saved = obj(HQ.store.getJson(HQ.KEYS.promptSettings));
    const next = {...saved, [key]: str(value).slice(0, 300)};
    HQ.store.setJson(HQ.KEYS.promptSettings, next);
  }

  const local = at => { const ms = parseIso(at); return ms === null ? "not planned" : fmt.localDateTime(ms); };
  const localShort = at => { const ms = parseIso(at); return ms === null ? "not planned" : fmt.dayClock(ms); };
  function modelLine(chosen) {
    return chosen.authorised_model ? `Use only ${chosen.authorised_model}, the model I authorised for Trade Graph.` :
      "Use only the model I authorised for Trade Graph.";
  }
  function header(chosen) {
    return `Project: ${chosen.project_folder}. Dashboard: ${chosen.dashboard_address}.`;
  }
  function nowStamp() {
    const now = HQ.serverNow();
    return `${fmt.localDateTime(now)} (${fmt.utcStamp(now)} UTC)`;
  }

  // Template from spec §C.8 (paraphrases docs/PAPER-REVIEW-SCHEDULE-2026-10-07.md).
  function reviewText({checkin, focus} = {}) {
    const snapshot = HQ.snapshot() || {};
    const facts = obj(snapshot.prompt_facts);
    const since = obj(facts.since);
    const chosen = settings();
    const live = snapshot.mode === "live";
    const practice = live ? "" : "practice ";
    const zone = fmt.timeZone();
    const title = checkin?.title ? str(checkin.title) : "Ad-hoc review";
    const planned = checkin?.planned_at ? local(checkin.planned_at) : fmt.localDateTime(HQ.serverNow());
    const sinceLabel = HQ.isIso(since.from) ? `my last check-in at ${local(since.from)}` : str(since.label) || "the last 24 hours";
    const turns = list(facts.next_turns).map(turn => `${str(turn.name)} ${localShort(turn.at)}`).join("; ") || "none planned";
    const flagged = list(facts.flagged).map(str).filter(Boolean).join("; ") || "nothing";
    const stopLine = snapshot.team?.allowance?.stop_line_percent;
    // The reserve rule as HQ read it (contract team.allowance.stop_line_state): on with its line, off, or unreadable.
    const reserve = typeof stopLine === "number" ? `whether the above-${stopLine}% admission rule is met` :
      snapshot.team?.allowance?.stop_line_state === "off" ? "whether the reserve admission rule is really off (HQ read it as off)" :
        "whether the configured admission rule is met (HQ couldn't read it)";
    const lines = [
      `Review my running Trade Graph ${live ? "live account" : "paper test"} — check-in "${title}" planned for ${planned} (${zone}); now ${nowStamp()}.`,
      `${header(chosen)} Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and ${chosen.review_notes_folder}. Verify the actual running database and image through the owner launcher; repository source may be newer than what is deployed.`,
      `Inspect only: do not start AI jobs, tests, retries, restarts, deployments, orders, pauses/resumes or configuration changes. ${modelLine(chosen)}`,
      `What my HQ page showed at ${local(snapshot.as_of)} (verify against records):`,
      `- Team: ${str(facts.team) || "not known"}. Setting: ${str(facts.setting) || "not known"}. AI allowance: ${str(facts.allowance) || "not known"}.`,
      `- Since ${sinceLabel}: ${since.decisions ?? "?"} Trader decisions (${since.waits ?? "?"} waits), ${since.orders ?? "?"} ${practice}orders, ${since.completed_trades ?? "?"} completed trades, ${since.research_notes ?? "?"} research notes, ${since.problems ?? "?"} problems.`,
      `- Next turns: ${turns}.`,
      `- ${live ? "Account value" : "Practice money"} ${str(facts.practice_value) || "not known yet"} (${facts.value_final ? "final" : "estimate"}); ${live ? "pays for itself" : "paper result against real costs"}: ${str(facts.pays_for_itself) || "not known yet"}.`,
      `- HQ flagged: ${flagged}.`,
      `Report: worker last sign of life; freshness of prices, euro rate and hourly history; each department's last and next turn; real successes and failures and why; Trader decisions; ${practice}orders and trades; balances and accounting; current AI allowance with its timestamp, ${reserve}, and which limits are not reported.`,
      `A valid "wait" is not a failure; no trades means trading behaviour is untested; keep unknown costs unknown; don't count my own chat use as team usage.`,
    ];
    const paragraph = kindParagraph(checkin?.prompt_kind, live);
    if (paragraph) lines.push(paragraph);
    if (focus?.text) lines.push(`Focus: ${str(focus.text).replace(/\s+/g, " ").trim()} (${local(focus.at)}).`);
    lines.push("Save a redacted private summary with UTC times and my time zone. Return a short status, issues and one recommended next action for me to decide; do not apply changes.");
    return lines.join("\n");
  }

  function checkupText() {
    const snapshot = HQ.snapshot() || {};
    const service = obj(snapshot.team?.service);
    const chosen = settings();
    const silent = Number.isSafeInteger(service.silent_seconds) ? fmt.duration(service.silent_seconds) : "an unknown time";
    const last = service.last_sign_of_life_at ? `${local(service.last_sign_of_life_at)} (${fmt.utcStamp(parseIso(service.last_sign_of_life_at))} UTC)` : "not recorded";
    return [
      `Check on my Trade Graph team — HQ shows no sign of life for ${silent}. Last sign of life: ${last}. Now ${nowStamp()} (${fmt.timeZone()}).`,
      `${header(chosen)} Read IMPLEMENTATION-STATUS.md and docs/NORMAL-PAPER-OPERATION.md.`,
      `Inspect only: do not start, stop, restart, retry, deploy or change anything. ${modelLine(chosen)}`,
      "Find out whether the PC was asleep, whether Docker and the team's program are running, and when the last sign of life was written to the running database. Note that pressing Start in HQ would only reconnect to a stuck program.",
      "Return a short status, what you found and one recommended next action for me to decide; do not apply changes.",
    ].join("\n");
  }

  function safetyText() {
    const snapshot = HQ.snapshot() || {};
    const pause = obj(snapshot.team?.pause);
    const chosen = settings();
    const since = pause.since ? `${local(pause.since)} (${fmt.utcStamp(parseIso(pause.since))} UTC)` : "an unrecorded time";
    return [
      `Investigate an automatic safety stop in my Trade Graph ${snapshot.mode === "live" ? "live account" : "paper test"}. HQ says: ${HQ.plain(snapshot.now?.headline) || "safety stop"} Since ${since}. Now ${nowStamp()} (${fmt.timeZone()}).`,
      `${header(chosen)} Read IMPLEMENTATION-STATUS.md, docs/NORMAL-PAPER-OPERATION.md and docs/FINANCIAL-TRANSITION.md.`,
      `Inspect only: do not lift the pause, resume, stop, restart, place or cancel orders, or change configuration. ${modelLine(chosen)}`,
      "Find the persisted pause record (who set it, profile, reason, time), the event that triggered it, open orders and holdings, and whether the cause is still present. List the documented recovery options and what each would do, without applying any.",
      "Return a short status, issues and one recommended next action for me to decide; do not apply changes.",
    ].join("\n");
  }

  function fallback(text, title) {
    HQ.sheet.open({
      title: title || "Copy this text",
      kind: "copy",
      build: body => {
        body.append(el("p", "hq-muted", "Copying isn't available on this connection. Press and hold the text to copy it, or use Select all."));
        const area = el("textarea", "hq-copy-area");
        area.readOnly = true;
        area.value = text;
        area.rows = 12;
        area.setAttribute("aria-label", title || "Text to copy");
        const select = button("button", "Select all");
        select.dataset.autofocus = "";
        const status = el("p", "hq-form-status");
        status.setAttribute("role", "status");
        select.addEventListener("click", () => {
          area.focus();
          area.select();
          area.setSelectionRange(0, area.value.length);
          let copied = false;
          try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
          status.textContent = copied ? "Copied." : "Selected. Use your device's Copy command.";
        });
        const actions = el("div", "hq-actions hq-sheet-actions");
        actions.append(select);
        body.append(area, actions, status);
      },
    });
  }

  async function copy(text, {title, after} = {}) {
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function" && globalThis.isSecureContext) {
        await navigator.clipboard.writeText(text);
        if (after) after(); else HQ.toast("Copied. Paste it into your review chat.");
        return true;
      }
    } catch (_) { /* Fall through to share or the text box. */ }
    if (navigator.share && globalThis.isSecureContext) {
      try {
        await navigator.share({text});
        if (after) after();
        return true;
      } catch (error) {
        if (error && error.name === "AbortError") return false;
      }
    }
    fallback(text, title);
    return false;
  }

  function dueCheckin() {
    const items = list(HQ.snapshot()?.checkins?.items);
    return items.find(item => item.state === "due" || item.state === "overdue") || null;
  }

  function copyReview(options = {}) {
    const text = reviewText(options);
    const due = options.checkin && ["due", "overdue"].includes(options.checkin.state) ? options.checkin : dueCheckin();
    const canMark = due && HQ.snapshot()?.viewer?.can_act && HQ.use("checkins");
    return copy(text, {
      title: "Review prompt",
      after: () => {
        if (canMark) HQ.toast("Copied. Paste it into your review chat.", {label: "Mark done", run: control => HQ.use("checkins").done(due.checkin_id, control)});
        else HQ.toast("Copied. Paste it into your review chat.");
      },
    });
  }
  const copyCheckup = () => copy(checkupText(), {title: "Check-up prompt"});
  const copySafety = () => copy(safetyText(), {title: "Technical steps"});

  HQ.view("prompt", {
    deps: () => [settings()],
    render(root) {
      root.append(el("p", "hq-muted", "These stay on this device and are never sent to Trade Graph."));
      const form = el("form", "hq-form");
      form.noValidate = true;
      form.addEventListener("submit", event => event.preventDefault());
      const chosen = settings();
      const saved = obj(HQ.store.getJson(HQ.KEYS.promptSettings));
      for (const [key, label, hint] of FIELDS) {
        const field = el("label", "hq-field");
        field.append(el("span", "hq-field-label", label));
        const input = el("input", "");
        input.type = key === "dashboard_address" ? "url" : "text";
        input.maxLength = 300;
        input.autocomplete = "off";
        input.spellcheck = false;
        input.value = typeof saved[key] === "string" ? saved[key] : "";
        // The placeholder is what the prompt says when the field is left empty. Only the dashboard address falls
        // back to this page's address; an empty model field means "the model I authorised" (desktop check).
        input.placeholder = (!saved[key] && chosen[key]) || EMPTY_MEANS[key] ||
          (key === "dashboard_address" ? location.origin : DEFAULTS[key]);
        input.dataset.focusKey = `prompt:${key}`;
        input.addEventListener("change", () => save(key, input.value.trim()));
        field.append(input, el("span", "hq-field-hint", hint));
        form.append(field);
      }
      root.append(form);
      const actions = el("div", "hq-actions");
      const copyNow = button("button", "Copy review prompt");
      copyNow.prepend(HQ.icon("copy", "hq-icon hq-icon-small"));
      copyNow.dataset.focusKey = "prompt-copy";
      copyNow.addEventListener("click", () => copyReview({}));
      const preview = button("button button-secondary", "Preview the prompt");
      preview.dataset.focusKey = "prompt-preview";
      preview.addEventListener("click", () => HQ.sheet.open({
        title: "Review prompt preview",
        kind: "copy",
        from: preview,
        build: body => {
          const area = el("textarea", "hq-copy-area");
          area.readOnly = true;
          area.rows = 14;
          area.value = reviewText({});
          area.setAttribute("aria-label", "Review prompt");
          const go = button("button", "Copy");
          go.dataset.autofocus = "";
          go.addEventListener("click", () => copyReview({}));
          const row = el("div", "hq-actions hq-sheet-actions");
          row.append(go);
          body.append(area, row);
        },
      }));
      actions.append(copyNow, preview);
      root.append(actions);
    },
  });

  HQ.define("prompts", {reviewText, checkupText, safetyText, copy, copyReview, copyCheckup, copySafety, settings, kindParagraph});
})();
