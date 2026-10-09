/* Server-rendered refresh; monetary calculations remain on the server. */
"use strict";
(() => {
  let busy = false;
  let lastSuccess = Date.now();
  let failed = false;
  const pending = new WeakMap();
  function freshness() {
    const target = document.querySelector("[data-live-freshness]");
    const state = failed ? "Offline / refresh failed · showing last snapshot" :
      Date.now() - lastSuccess > 90000 ? "Stale / refresh overdue" : "Fresh";
    if (target) target.textContent = `${state} · ${Math.floor((Date.now() - lastSuccess) / 1000)}s since successful refresh`;
  }
  async function refresh(force = false) {
    if (busy || document.hidden || !force && document.activeElement?.closest("form")) return;
    busy = true;
    try {
      const response = await fetch(window.location.pathname + window.location.search,
        {credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(12000)});
      if (response.redirected && new URL(response.url).pathname === "/login") {
        window.location.assign("/login");
        return;
      }
      if (!response.ok) throw new Error("Refresh unavailable");
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const incoming = doc.querySelector("[data-live-content]");
      const current = document.querySelector("[data-live-content]");
      if (!incoming || !current) throw new Error("Incomplete snapshot");
      const open = [...current.querySelectorAll("details")].map(item => item.open);
      current.replaceWith(incoming);
      incoming.querySelectorAll("details").forEach((item, index) => { item.open = Boolean(open[index]); });
      lastSuccess = Date.now();
      failed = false;
    } catch (_) { failed = true; }
    finally { busy = false; freshness(); }
  }
  document.addEventListener("click", event => {
    if (event.target.closest("[data-live-refresh]")) refresh(true);
  });
  document.addEventListener("submit", async event => {
    if (event.target.matches("[data-report-period]")) {
      event.preventDefault();
      const cutoff = new Date(new FormData(event.target).get("end_at")).toISOString();
      window.location.assign("/?end_at=" + encodeURIComponent(cutoff));
      return;
    }
    const form = event.target.closest("[data-expense-form]");
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    const status = form.querySelector(".form-result");
    button.disabled = true;
    try {
      let body = pending.get(form);
      if (!body) {
        const fields = new FormData(form);
        body = Object.fromEntries(fields);
        body.request_id = crypto.randomUUID();
        body.record_type = form.dataset.recordType;
        for (const key of ["incurred_at", "period_start", "period_end"]) {
          if (body[key]) body[key] = new Date(body[key]).toISOString();
        }
        if (body.department_weights) body.department_weights = JSON.parse(body.department_weights);
        if (body.expense_kinds) body.expense_kinds = body.expense_kinds === "both" ? ["subscription", "other"] : [body.expense_kinds];
        pending.set(form, body);
      }
      const response = await fetch("/api/v1/owner/expenses", {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""},
        body: JSON.stringify(body)});
      if (!response.ok) {
        const output = await response.json();
        if (response.status < 500) pending.delete(form);
        throw new Error(typeof output.detail === "string" ? output.detail : "Expense evidence was not accepted. Check the period, allocation and unique reference.");
      }
      pending.delete(form);
      status.textContent = "Expense evidence recorded. Refresh to view its allocation.";
      form.reset();
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  window.addEventListener("offline", () => { failed = true; freshness(); });
  window.addEventListener("online", () => refresh(true));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(() => refresh(), 30000);
  setInterval(freshness, 1000);
})();
