/* The server calculates every monetary amount. Browser inputs retain decimal strings. */
"use strict";

(() => {
  const themeButton = document.querySelector("[data-theme-toggle]");
  try {
    const savedTheme = localStorage.getItem("trade-graph-theme");
    if (savedTheme === "light" || savedTheme === "dark") document.documentElement.dataset.theme = savedTheme;
  } catch (_) { /* The dashboard also works without local storage. */ }
  themeButton?.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const theme = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("trade-graph-theme", theme); } catch (_) { /* Optional preference. */ }
  });

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const ownerForms = [...document.querySelectorAll("form[data-command]")];
  const leaderForm = document.querySelector("form[data-leader-task]");
  const ownerStatus = document.querySelector("[data-owner-status]");
  const pending = new WeakMap();
  const activeForms = new WeakSet();
  let ownerRevision = null;
  let ownerBusy = false;

  function result(form, message, failed = false) {
    const target = form.querySelector(".form-result");
    if (target) {
      target.textContent = message;
      target.classList.toggle("error", failed);
    }
  }

  async function jsonResponse(response) {
    try { return await response.json(); }
    catch (_) { return {detail: `The service returned HTTP ${response.status} without a JSON response.`}; }
  }

  function detail(body) {
    return typeof body.detail === "string" ? body.detail.slice(0, 700) : "The service rejected this request.";
  }

  function integer(input, label) {
    if (!/^\d+$/.test(String(input))) throw new Error(`${label} must be a whole number.`);
    const parsed = Number.parseInt(input, 10);
    if (!Number.isSafeInteger(parsed)) throw new Error(`${label} is outside the supported range.`);
    return parsed;
  }

  function decimal(input, label) {
    const text = String(input || "").trim();
    if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(text)) {
      throw new Error(`${label} must be a nonnegative decimal amount, for example 1.25.`);
    }
    return text;
  }

  function names(input) { return String(input || "").split(",").map(item => item.trim()).filter(Boolean); }

  function requestId() {
    if (!globalThis.crypto?.randomUUID) throw new Error("A browser secure context is required for request IDs.");
    return crypto.randomUUID();
  }

  function buildCommand(form, revision) {
    const fields = new FormData(form);
    const body = {request_id: requestId(), expected_revision: revision};
    const command = form.dataset.command;
    if (command === "budgets") {
      body.deployment_id = String(fields.get("deployment_id")).trim();
      for (const name of ["total", "period", "priority_reserve", "daily", "root"]) {
        body[name] = decimal(fields.get(name), name.replaceAll("_", " "));
      }
      body.roles = {};
      for (const [name, amount] of fields.entries()) {
        if (name.startsWith("role_")) body.roles[name.slice(5)] = decimal(amount, name.slice(5));
      }
    } else if (command === "config") {
      for (const name of ["allowed_venues", "allowed_symbols", "allowed_change_classes"]) body[name] = names(fields.get(name));
      for (const name of ["maximum_gross_exposure_fraction", "maximum_single_asset_exposure_fraction"]) {
        body[name] = decimal(fields.get(name), name.replaceAll("_", " "));
      }
      body.maximum_quote_age_seconds = integer(fields.get("maximum_quote_age_seconds"), "Quote age");
      body.budget_exhaustion_profile = fields.get("budget_exhaustion_profile");
    } else if (command === "pause") {
      body.profile = fields.get("profile");
      body.reason = String(fields.get("reason")).trim();
      if (body.profile === "STOPPED") body.position_policy = fields.get("position_policy");
    } else if (form.hasAttribute("data-leader-task")) {
      body.role = fields.get("role");
      body.objective = String(fields.get("objective")).trim();
      body.allocated_spend = decimal(fields.get("allocated_spend"), "Allocated spend");
      body.max_attempts = integer(fields.get("max_attempts"), "Maximum attempts");
      for (const name of ["parent_id", "root_task_id"]) {
        const identifier = String(fields.get(name) || "").trim();
        if (identifier) body[name] = identifier;
      }
    }
    return body;
  }

  function fill(form, name, value) {
    const field = form.elements.namedItem(name);
    if (field && value !== undefined && value !== null) field.value = Array.isArray(value) ? value.join(", ") : String(value);
  }

  function ownerFields(enabled) {
    for (const form of ownerForms) form.querySelector("[data-owner-fields]").disabled = !enabled;
  }

  async function loadOwner(populate = true) {
    try {
      const response = await fetch("/api/v1/owner/config", {credentials: "same-origin", cache: "no-store"});
      const body = await jsonResponse(response);
      if (!response.ok) throw new Error(detail(body));
      ownerRevision = integer(body.revision, "Owner revision");
      document.querySelector("[data-owner-revision]").textContent = String(ownerRevision);
      document.querySelector("[data-owner-profile]").textContent = body.pause?.profile || "No persisted pause";
      if (populate) {
        const budgetForm = ownerForms.find(form => form.dataset.command === "budgets");
        if (body.budget && budgetForm) {
          for (const name of ["deployment_id", "total", "period", "priority_reserve", "daily", "root"]) fill(budgetForm, name, body.budget[name]);
          for (const [role, amount] of Object.entries(body.budget.roles || {})) fill(budgetForm, `role_${role}`, amount);
        }
        const configForm = ownerForms.find(form => form.dataset.command === "config");
        if (body.policy && configForm) {
          for (const name of ["allowed_venues", "allowed_symbols", "allowed_change_classes", "maximum_gross_exposure_fraction", "maximum_single_asset_exposure_fraction", "maximum_quote_age_seconds", "budget_exhaustion_profile"]) {
            fill(configForm, name, body.policy[name]);
          }
        }
      }
      const blocked = Array.isArray(body.pending_commands) && body.pending_commands.length > 0;
      ownerStatus.textContent = blocked ? "A protected command is in progress. Reconcile it before issuing another command." :
        csrf ? `Protected owner revision ${ownerRevision} loaded. Every change is checked against this revision.` :
          `Protected owner revision ${ownerRevision} loaded for reading. Browser writes require a cookie session.`;
      ownerFields(Boolean(csrf) && !blocked && !ownerBusy);
      return true;
    } catch (error) {
      ownerFields(false);
      ownerStatus.textContent = `Controls unavailable: ${error.message}`;
      return false;
    }
  }

  async function submitCommand(form, endpoint, revision, isOwner) {
    if (activeForms.has(form)) return;
    let body = pending.get(form);
    if (!body) {
      try { body = buildCommand(form, revision); }
      catch (error) { result(form, error.message, true); return; }
    }
    pending.set(form, body);
    activeForms.add(form);
    const button = form.querySelector('button[type="submit"]');
    const label = button.dataset.commandLabel || button.textContent;
    button.dataset.commandLabel = label;
    button.disabled = true;
    if (isOwner) { ownerBusy = true; ownerFields(false); }
    result(form, "Sending the protected command…");
    let retainPending = false;
    try {
      const response = await fetch(endpoint, {
        method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
        body: JSON.stringify(body)
      });
      const output = await jsonResponse(response);
      if (!response.ok) {
        const message = detail(output);
        retainPending = response.status >= 500 || response.status === 409 && message.includes("in progress");
        result(form, `${message}${retainPending ? " Retry this same request after reconciliation." : " Review the current state before submitting again."}`, true);
      } else {
        const revisionNote = output.revision === undefined ? "" : ` Revision ${output.revision}.`;
        if (form.dataset.command === "pause") result(form, `Management profile: ${output.profile || body.profile}.${output.achieved === false ? " Requested outcome is not yet verified." : ""}${revisionNote}`);
        else if (form.dataset.command === "resume") result(form, `Reconciled state: ${output.profile || "RUNNING"}.${revisionNote}`);
        else if (form.hasAttribute("data-leader-task")) {
          form.dataset.revision = String(output.revision);
          result(form, `Task persisted: ${output.task_id || "see task journal"}.${revisionNote} Refresh to view the assignment.`);
        } else result(form, `Protected ${form.dataset.command === "budgets" ? "real allowance" : "policy"} saved.${revisionNote}`);
      }
    } catch (_) {
      retainPending = true;
      result(form, "The command outcome is unknown after a connection failure. Retry sends the same request ID and values; do not issue a replacement command.", true);
    } finally {
      activeForms.delete(form);
      if (!retainPending) pending.delete(form);
      button.disabled = false;
      button.textContent = retainPending ? "Retry pending command" : label;
      if (isOwner) {
        ownerBusy = false;
        if (retainPending) {
          ownerFields(false);
          form.querySelector("[data-owner-fields]").disabled = false;
          for (const field of form.querySelectorAll("input, select, textarea")) field.disabled = true;
          ownerStatus.textContent = "A command outcome is pending. Retry its existing request ID before issuing further changes.";
        } else {
          for (const field of form.querySelectorAll("input, select, textarea")) field.disabled = false;
          await loadOwner(false);
        }
      } else if (retainPending) {
        for (const field of form.querySelectorAll("input, select, textarea")) field.disabled = true;
      } else {
        for (const field of form.querySelectorAll("input, select, textarea")) field.disabled = false;
        try {
          const response = await fetch("/api/v1/tasks", {credentials: "same-origin", cache: "no-store"});
          const current = await jsonResponse(response);
          if (response.ok && current.leader_revision !== undefined) form.dataset.revision = String(current.leader_revision);
        } catch (_) { /* The next server revision check still protects a stale form. */ }
      }
    }
  }

  if (ownerForms.length) {
    loadOwner();
    for (const form of ownerForms) form.addEventListener("submit", event => {
      event.preventDefault();
      if (!csrf || ownerRevision === null || ownerBusy) return;
      submitCommand(form, `/api/v1/owner/${form.dataset.command}`, ownerRevision, true);
    });
  }

  if (leaderForm) {
    let revision;
    try { revision = integer(leaderForm.dataset.revision, "Leader revision"); } catch (_) { revision = null; }
    leaderForm.querySelector("[data-leader-fields]").disabled = !csrf || revision === null;
    if (!csrf) result(leaderForm, "Task writes require an authenticated leader cookie session with CSRF protection.");
    leaderForm.addEventListener("submit", event => {
      event.preventDefault();
      if (!csrf) return;
      submitCommand(leaderForm, "/api/v1/leader/tasks", integer(leaderForm.dataset.revision, "Leader revision"), false);
    });
  }

  const login = document.querySelector("form[data-login]");
  login?.addEventListener("submit", async event => {
    event.preventDefault();
    const button = login.querySelector('button[type="submit"]');
    const field = login.elements.namedItem("session_token");
    const sessionToken = field.value.trim();
    if (!sessionToken) return;
    button.disabled = true;
    result(login, "Opening the private dashboard…");
    try {
      const response = await fetch("/api/v1/session", {
        method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({session_token: sessionToken})
      });
      const body = await jsonResponse(response);
      if (!response.ok) throw new Error(detail(body));
      field.value = "";
      window.location.assign("/");
    } catch (error) { field.value = ""; result(login, error.message, true); }
    finally { button.disabled = false; }
  });
})();
