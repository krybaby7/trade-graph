/* The server owns progress, test outcomes and financial values. */
"use strict";

(() => {
  const root = document.getElementById("mission-control");
  if (!root) return;
  const one = selector => root.querySelector(selector);
  const all = selector => [...root.querySelectorAll(selector)];
  const list = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === "string" ? value.slice(0, 12000) :
    value == null ? "" : typeof value === "object" ? JSON.stringify(value).slice(0, 12000) : String(value);
  const label = status => text(status || "pending").replaceAll("_", " ").replace(/^./, character => character.toUpperCase());
  const tone = status => ["completed", "done", "passed", "success"].includes(status) ? "complete" :
    ["running", "in_progress", "queued"].includes(status) ? "active" :
    ["failed", "error"].includes(status) ? "failed" : ["blocked", "unavailable"].includes(status) ? "blocked" : "pending";
  const count = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : "—";
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const owner = root.dataset.role === "owner";
  const preview = document.body.dataset.preview === "true";
  let snapshot = null;
  let selectedDepartment = all("[data-department-id]")[0]?.dataset.departmentId || "leader";
  let taskFilter = "all";
  let connected = false;
  let authExpired = false;
  let runningCheck = null;
  let serviceAction = null;
  const serviceRequests = new Map();
  let pollTimer = null;
  let polling = false;
  let pollController = null;
  let receivedAt = null;
  const unknownChecks = new Map();
  const acknowledgedChecks = new Map();
  const signatures = new Map();

  function element(tag, className = "", content = "") {
    const node = document.createElement(tag);
    node.className = className;
    if (content !== "") node.textContent = text(content);
    return node;
  }
  function write(selector, value, fallback = "—") {
    const node = one(selector);
    const content = text(value) || fallback;
    if (node && node.textContent !== content) node.textContent = content;
  }
  function badge(status) { return element("span", `mc-status ${tone(status)}`, label(status)); }
  function safePath(value) { return typeof value === "string" && value.startsWith("/") && !value.startsWith("//") && !/[\\\x00-\x20]/.test(value) ? value : null; }
  function time(value) {
    if (!value) return "Time not recorded";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? text(value) : new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
    }).format(parsed);
  }
  function typeLabel(kind, synthetic, checkId) {
    if (synthetic || checkId === "offline-loop" || ["offline", "offline-loop", "local", "synthetic", "local_simulation"].includes(kind)) return "Local simulation";
    if (checkId === "kraken-public" || ["public", "kraken-public", "public_api"].includes(kind)) return "Public API";
    if (["account", "private", "authenticated", "private_api"].includes(kind)) return "Account API";
    if (kind === "live") return "Planned real orders";
    if (kind === "validation") return "Planned validation";
    return "Planned check";
  }
  function changed(key, data) {
    const signature = JSON.stringify(data);
    if (signatures.get(key) === signature) return false;
    signatures.set(key, signature);
    return true;
  }
  function syncState(message, state) {
    write("[data-sync-label]", message);
    const node = one(".mc-sync");
    node.classList.toggle("is-connected", state === "connected");
    node.classList.toggle("is-offline", state === "offline");
    if (receivedAt) write("[data-last-sync]", `Last received ${time(receivedAt)}`);
  }
  function loginNotice() {
    authExpired = true;
    connected = false;
    syncState("Session ended — sign in again", "offline");
    if (!one("[data-login-link]")) {
      const link = element("a", "mc-text-link", "Sign in →");
      link.href = "/login";
      link.dataset.loginLink = "";
      one(".mc-sync").append(link);
    }
    updateControls();
    renderLifecycle(snapshot?.lifecycle);
  }
  function renderProject(project) {
    write("[data-project-completed]", count(project.completed));
    write("[data-project-total]", count(project.total));
    write("[data-stat-completed]", count(project.completed));
    write("[data-stat-progress]", count(project.in_progress));
    write("[data-stat-blocked]", count(project.blocked));
    write("[data-roadmap-total]", `${count(project.total)} tasks`);
    write("[data-project-source]", text(project.source || "unavailable").replaceAll("_", " "));
    write("[data-project-updated]", project.updated_at || "No update recorded");
    const percent = typeof project.percent === "number" && Number.isFinite(project.percent) ? Math.min(100, Math.max(0, project.percent)) : null;
    write("[data-project-percent]", percent == null ? "—" : String(percent));
    one("[data-ring-value]")?.setAttribute("stroke-dasharray", `${percent || 0} 100`);
    one("[data-project-ring]")?.setAttribute("aria-label", `${count(project.completed)} of ${count(project.total)} implementation tasks completed`);
    if (!changed("tasks", project.tasks)) return;
    const target = one("[data-task-list]");
    const open = new Set(all(".mc-task[open]").map(node => node.dataset.taskId));
    const fragment = document.createDocumentFragment();
    for (const task of list(project.tasks)) {
      const row = element("details", "mc-task");
      row.dataset.taskId = text(task.id);
      row.dataset.taskStatus = text(task.status);
      row.open = open.has(text(task.id));
      const summary = element("summary");
      summary.append(element("span", `mc-task-number ${tone(task.status)}`, task.id), element("strong", "", task.title), badge(task.status), element("span", "mc-disclosure-arrow", "⌄"));
      const body = element("div", "mc-task-body");
      body.append(element("p", "", task.description || "No description recorded."));
      const facts = element("dl");
      for (const [name, value] of [["Phase", task.phase || "Not specified"], ["Depends on", list(task.dependencies).join(" · ") || "No prerequisites"], ["Recorded evidence", task.evidence_summary || "No completion evidence recorded yet."]]) {
        const pair = element("div");
        pair.append(element("dt", "", name), element("dd", "", value));
        facts.append(pair);
      }
      body.append(facts);
      row.append(summary, body);
      fragment.append(row);
    }
    if (!fragment.childNodes.length) fragment.append(element("p", "mc-empty", "Task data is not available. No progress has been estimated."));
    target.replaceChildren(fragment);
    filterTasks();
  }
  function filterTasks() {
    let visible = 0;
    for (const row of all("[data-task-id]")) {
      const match = taskFilter === "all" || row.dataset.taskStatus === taskFilter || taskFilter === "done" && row.dataset.taskStatus === "completed";
      row.hidden = !match;
      if (match) visible++;
    }
    for (const button of all("[data-task-filter]")) {
      const active = button.dataset.taskFilter === taskFilter;
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-pressed", String(active));
    }
    one("[data-task-empty]").hidden = visible > 0 || !all("[data-task-id]").length;
  }
  function renderDepartments(departments) {
    if (!changed("departments", departments)) return;
    const target = one("[data-department-graph]");
    const ids = new Set(list(departments).map(department => text(department.id)));
    for (const node of all("[data-department-id]")) if (!ids.has(node.dataset.departmentId)) node.remove();
    for (const department of list(departments)) {
      let node = all("[data-department-id]").find(item => item.dataset.departmentId === text(department.id));
      if (!node) {
        node = element("button", "mc-node");
        node.type = "button";
        node.dataset.departmentId = text(department.id);
        node.setAttribute("aria-controls", "department-detail");
        target.append(node);
      }
      node.dataset.departmentPurpose = text(department.purpose);
      node.dataset.departmentApi = text(department.api);
      node.dataset.departmentStatus = text(department.status);
      node.dataset.departmentLastActivity = text(department.last_activity);
      const top = element("span", "mc-node-top");
      const symbol = element("span", "mc-node-symbol", text(department.name).slice(0, 1));
      symbol.setAttribute("aria-hidden", "true");
      const state = element("span", `mc-node-state ${tone(department.status)}`);
      state.append(element("span"), document.createTextNode(label(department.status)));
      top.append(symbol, state);
      node.replaceChildren(top, element("strong", "", department.name), element("span", "mc-node-caption", ["secretary", "execution", "market"].includes(department.id) ? "Protected software" : "AI department"));
      node.disabled = false;
    }
    if (!ids.has(selectedDepartment)) selectedDepartment = ids.values().next().value || "";
    selectDepartment(selectedDepartment);
    requestAnimationFrame(drawEdges);
  }
  function selectDepartment(id) {
    selectedDepartment = id;
    const department = list(snapshot?.departments).find(item => item.id === id);
    const node = all("[data-department-id]").find(item => item.dataset.departmentId === id);
    for (const button of all("[data-department-id]")) {
      const selected = button.dataset.departmentId === id;
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    if (!node) return;
    write("[data-detail-name]", department?.name || node.querySelector("strong")?.textContent || "Department");
    const status = department?.status || node.dataset.departmentStatus;
    const statusNode = one("[data-detail-status]");
    statusNode.className = `mc-status ${tone(status)}`;
    statusNode.textContent = label(status);
    write("[data-detail-purpose]", department?.purpose || node.dataset.departmentPurpose, "No purpose recorded.");
    write("[data-detail-api]", department?.api || node.dataset.departmentApi, "No direct external API");
    write("[data-detail-activity]", department?.last_activity || node.dataset.departmentLastActivity, "No activity recorded yet");
    const names = new Map(list(snapshot?.departments).map(item => [item.id, item.name]));
    const connections = list(snapshot?.connections).filter(edge => edge.from === id || edge.to === id).map(edge => `${names.get(edge.from) || edge.from} → ${names.get(edge.to) || edge.to}: ${text(edge.label)}`);
    write("[data-detail-connections]", connections.join("; "), "No connections recorded in this snapshot.");
    const recordLink = one("[data-detail-link]");
    recordLink.href = preview ? "#graph-title" : safePath(department?.href) || "/organization";
    recordLink.textContent = preview ? "Open the dashboard server to view records" : "Open department records →";
    for (const path of all(".mc-edge")) path.classList.toggle("is-related", path.dataset.from === id || path.dataset.to === id);
  }
  function drawEdges() {
    const graph = one("[data-department-graph]");
    const svg = one(".mc-edges");
    const target = one("[data-edges]");
    if (!graph || !svg || !target) return;
    const bounds = graph.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
    const fragment = document.createDocumentFragment();
    for (const connection of list(snapshot?.connections)) {
      const nodes = all("[data-department-id]");
      const source = nodes.find(node => node.dataset.departmentId === connection.from);
      const destination = nodes.find(node => node.dataset.departmentId === connection.to);
      if (!source || !destination) continue;
      const a = source.getBoundingClientRect();
      const b = destination.getBoundingClientRect();
      const dx = (b.left + b.width / 2) - (a.left + a.width / 2);
      const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
      const horizontal = Math.abs(dx) > Math.abs(dy);
      const x1 = a.left - bounds.left + a.width / 2 + (horizontal ? Math.sign(dx) * a.width / 2 : 0);
      const y1 = a.top - bounds.top + a.height / 2 + (horizontal ? 0 : Math.sign(dy) * a.height / 2);
      const x2 = b.left - bounds.left + b.width / 2 - (horizontal ? Math.sign(dx) * (b.width / 2 + 3) : 0);
      const y2 = b.top - bounds.top + b.height / 2 - (horizontal ? 0 : Math.sign(dy) * (b.height / 2 + 3));
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const middleX = (x1 + x2) / 2;
      const middleY = (y1 + y2) / 2;
      path.setAttribute("d", horizontal ? `M ${x1} ${y1} C ${middleX} ${y1}, ${middleX} ${y2}, ${x2} ${y2}` : `M ${x1} ${y1} C ${x1} ${middleY}, ${x2} ${middleY}, ${x2} ${y2}`);
      path.setAttribute("class", `mc-edge${connection.from === selectedDepartment || connection.to === selectedDepartment ? " is-related" : ""}`);
      path.dataset.from = text(connection.from);
      path.dataset.to = text(connection.to);
      fragment.append(path);
    }
    target.replaceChildren(fragment);
  }
  function renderMilestones(milestones) {
    if (!changed("milestones", milestones)) return;
    const fragment = document.createDocumentFragment();
    list(milestones).forEach((milestone, index) => {
      const item = element("li", `mc-milestone ${tone(milestone.status)}`);
      const marker = element("span", "mc-milestone-marker", milestone.status === "completed" ? "✓" : String(index + 1));
      marker.setAttribute("aria-hidden", "true");
      const body = element("div");
      body.append(badge(milestone.status), element("h3", "", milestone.title), element("p", "", milestone.description));
      if (list(milestone.task_ids).length) body.append(element("span", "mc-small-note", milestone.task_ids.join(" · ")));
      item.append(marker, body);
      fragment.append(item);
    });
    if (!fragment.childNodes.length) fragment.append(element("li", "mc-empty", "Milestones are not available yet."));
    one("[data-milestones]").replaceChildren(fragment);
  }
  function renderChecks(checks) {
    if (changed("checks", checks)) {
      const fragment = document.createDocumentFragment();
      for (const check of list(checks)) {
        const card = element("article", "mc-check");
        card.dataset.checkId = text(check.id);
        const heading = element("div", "mc-check-heading");
        const status = element("span", "mc-status pending", "No run yet");
        status.dataset.checkStatus = "";
        heading.append(element("span", "mc-type-tag", typeLabel(check.kind, false, check.id)), status);
        card.append(heading, element("h3", "", check.label), element("p", "", check.description));
        const actions = element("div", "mc-check-action");
        if (check.id === "kraken-account") {
          const button = element("button", "mc-button", "Run from owner CLI");
          button.type = "button";
          button.dataset.runCheck = "kraken-account";
          button.disabled = true;
          actions.append(button, element("code", "", "uv run trade-graph kraken-read-only --database runtime/trade_graph.sqlite --owner-directory ~/.local/share/trade-graph-owner/kraken --symbol BTC/USD"));
        } else if (check.available && owner) {
          const button = element("button", "mc-button", check.id === "offline-loop" ? "Run local rehearsal →" : check.id === "kraken-public" ? "Check public Kraken API →" : "Run check →");
          button.type = "button";
          button.dataset.runCheck = text(check.id);
          button.disabled = true;
          actions.append(button);
        } else actions.append(element("span", "mc-locked", check.available ? "Owner access needed" : "Not available yet"));
        const reason = element("p", "mc-small-note", check.reason || (owner ? "Controls require a connected owner session." : "This check requires owner access."));
        reason.dataset.checkReason = "";
        const feedback = element("p", "mc-check-feedback");
        feedback.dataset.checkFeedback = "";
        feedback.setAttribute("role", "status");
        feedback.setAttribute("aria-live", "polite");
        actions.append(reason, feedback);
        card.append(actions);
        fragment.append(card);
      }
      if (!fragment.childNodes.length) fragment.append(element("p", "mc-empty", "No test missions are registered."));
      one("[data-checks]").replaceChildren(fragment);
    }
    for (const card of all("[data-check-id]")) {
      const run = list(snapshot?.test_runs).find(item => item.check_id === card.dataset.checkId);
      const status = card.querySelector("[data-check-status]");
      status.textContent = run ? label(run.status) : "No run yet";
      status.className = `mc-status ${tone(run?.status)}`;
    }
    updateControls();
  }
  function updateControls() {
    for (const button of all("[data-run-check]")) {
      const check = list(snapshot?.checks).find(item => item.id === button.dataset.runCheck);
      const activeRun = list(snapshot?.test_runs).some(run => run.check_id === button.dataset.runCheck && ["queued", "running"].includes(run.status)) || acknowledgedChecks.has(button.dataset.runCheck);
      button.disabled = button.dataset.runCheck === "kraken-account" || preview || !connected || !owner || !csrf || authExpired || Boolean(runningCheck) || activeRun || !check?.available || unknownChecks.has(button.dataset.runCheck);
      const card = button.closest("[data-check-id]");
      const reason = card.querySelector("[data-check-reason]");
      reason.textContent = button.dataset.runCheck === "kraken-account" ? "Browser execution is disabled. Run the protected owner CLI on the WSL host." : preview ? "This is a saved preview. Run checks from the live dashboard server." :
        unknownChecks.has(button.dataset.runCheck) ? "The request outcome is uncertain. Check the saved run history before trying again." :
        activeRun ? "This check is running. Its saved status will update automatically." :
        !csrf ? "Sign in with an owner browser session to run this check." :
        !connected ? "Waiting for the dashboard connection. The last saved snapshot stays visible." :
        check?.reason || "This check does not place orders or make paid AI calls.";
    }
  }
  function renderRuns(runs) {
    if (!changed("runs", runs)) return;
    const open = new Set(all(".mc-run[open]").map(node => node.dataset.runId));
    const fragment = document.createDocumentFragment();
    for (const run of list(runs)) {
      const row = element("details", "mc-run");
      row.dataset.runId = text(run.run_id);
      row.open = open.has(text(run.run_id));
      const summary = element("summary");
      const name = element("span", "mc-run-label");
      name.append(element("strong", "", run.label), element("span", "", `${typeLabel(run.kind, run.synthetic, run.check_id)} · ${time(run.started_at)}`));
      summary.append(element("span", `mc-run-icon ${tone(run.status)}`, run.status === "passed" ? "✓" : "›"), name, badge(run.status), element("span", "mc-disclosure-arrow", "⌄"));
      const body = element("div", "mc-run-body");
      body.append(element("p", "", run.summary || "The run has not produced a summary yet."));
      if (run.account_observation) {
        const account = run.account_observation;
        const observation = element("div", "mc-account-observation");
        observation.append(element("h4", "", `Historical observation · ${text(account.symbol)}`));
        const facts = element("dl");
        for (const [name, value] of [
          ["Observation time", time(account.observation_finished_at)],
          ["Verified at", time(account.verified_at)],
          ["Freshness", `${account.freshness === "fresh" ? "Fresh historical capture" : "Stale historical capture"} · Maximum age ${count(account.maximum_age_seconds)} seconds from observation time`],
          ["Transport", run.synthetic ? "Synthetic injected transport; actual connectivity unverified" : "Actual collector-owned HTTPS transport"],
          ["Authenticated private reads", count(account.authenticated_private_read_count)],
          ["Source identity", "Current collector and adapter verified at import"]
        ]) {
          const pair = element("div");
          pair.append(element("dt", "", name), element("dd", "", value));
          facts.append(pair);
        }
        observation.append(facts, element("h4", "", "Pending checks"));
        const pending = element("ul");
        for (const item of list(account.pending_checks)) pending.append(element("li", "", item));
        observation.append(pending);
        body.append(observation);
      }
      const steps = element("ol", "mc-run-steps");
      for (const step of list(run.steps)) {
        const item = element("li");
        const detail = element("div");
        detail.append(element("strong", "", step.label), element("p", "", step.detail || "No detail recorded."));
        item.append(badge(step.status), detail);
        steps.append(item);
      }
      body.append(steps, element("span", "mc-small-note", `Run ${text(run.run_id)}${run.finished_at ? ` · Finished ${time(run.finished_at)}` : ""}`));
      row.append(summary, body);
      fragment.append(row);
    }
    if (!fragment.childNodes.length) {
      const empty = element("div", "mc-empty mc-empty-runs");
      empty.append(element("span", "", "◎"), element("h3", "", "Your first result belongs here."), element("p", "", "No saved test runs yet. Start with a local rehearsal or a public Kraken API check when the controls are available."));
      fragment.append(empty);
    }
    one("[data-test-runs]").replaceChildren(fragment);
  }
  function renderActivity(activity) {
    if (!changed("activity", activity)) return;
    const fragment = document.createDocumentFragment();
    for (const event of list(activity)) {
      const item = element("li", "mc-activity-item");
      const body = element("div");
      body.append(element("span", "mc-activity-kind", `${text(event.kind || "event").replaceAll("_", " ")} · ${text(event.source || "journal")}`), element("h3", "", event.title), element("p", "", event.detail || "No additional detail recorded."), element("time", "", time(event.at)));
      if (!preview && safePath(event.href)) {
        const link = element("a", "mc-text-link", "Open record →");
        link.href = safePath(event.href);
        body.append(link);
      }
      item.append(element("span", "mc-activity-marker"), body);
      fragment.append(item);
    }
    if (!fragment.childNodes.length) fragment.append(element("li", "mc-empty", "No runtime activity is recorded yet. Departments will appear active only when there is journal evidence."));
    one("[data-activity]").replaceChildren(fragment);
  }
  function renderLifecycle(lifecycle) {
    const service = lifecycle?.service || {};
    const ready = lifecycle?.prerequisites || {};
    const profile = lifecycle?.pause_profile || "RUNNING";
    write("[data-runtime-mode]", `${label(service.mode || "paper")} · ${service.mode === "live" && ready.live_available ? "Protected live startup eligible; final admission repeats before effects" : "Live disabled"}`);
    write("[data-runtime-status]", `Service ${label(service.status || "IDLE")}. AI ${ready.ai_available ? "available" : "unavailable or paused"}. Portfolio ${label(profile)}.${service.error_type ? ` Last failure: ${text(service.error_type)}.` : ""}`);
    const prerequisiteReasons = [...list(ready.reasons), ...(service.mode === "live" ? list(ready.live_reasons) : [])];
    write("[data-runtime-prerequisites]", prerequisiteReasons.join(" ") || "Owner prerequisites satisfied for this configured mode.");
    const resume = lifecycle?.live_resume;
    write("[data-live-resume-status]", resume ? `Live Resume ${text(resume.request_id)}: ${label(resume.status)}. ${text(resume.reason)} Deadline ${time(resume.deadline_at)}.` :
      "No queued live Resume. Use Owner controls to request reconciliation and resume within current live authority.");
    const cycle = lifecycle?.optimisation;
    const scheduleStatus = `Automatic Optimisation scheduling is ${lifecycle?.automatic_optimisation ? "enabled" : "disabled"}.`;
    write("[data-optimisation-status]", cycle ? `Optimisation ${text(cycle.task_id)}: ${label(cycle.status)}; ${list(cycle.tasks).map(task => `${label(task.role)} ${label(task.status)}`).join(" · ")}. Deadline ${time(cycle.deadline_at)}. ${scheduleStatus}` : `Optimisation: no requested cycle recorded. ${scheduleStatus}`);
    const usage = lifecycle?.ai_usage;
    const providers = list(usage?.providers).map(provider => `${text(provider.provider)}${provider.ai_paused ? " paused" : ""}: ${text(provider.quota)}${provider.reason ? ` (${text(provider.reason)})` : ""}`).join(" · ");
    const quotaPolicies = list(usage?.quota_policy_routes).map(route => {
      if (!route.enabled) return `${text(route.provider)}: reserve policy disabled`;
      const windows = list(route.readings).map(reading => `${text(reading.window || reading.name)} ${reading.remaining_percent == null ? "unknown" : `${text(reading.remaining_percent)}% remaining`}`).join("; ");
      return `${text(route.provider)}: ${route.admitted ? "AI admitted" : "AI paused"}; reserve ${text(route.reserve_remaining_percent)}%, execution headroom ${text(route.execution_headroom_percent)}%, requires above ${text(route.admission_threshold_remaining_percent)}% remaining. ${windows} ${list(route.blockers).join(" ")} Missing windows: ${list(route.unavailable_windows).join(", ") || "none reported"}. Running-call consumption and unreported provider windows remain unknown.`;
    }).join(" · ");
    write("[data-ai-usage]", `Subscription quota: ${providers || "unknown"}. ${quotaPolicies} Unknown usage/cost records: ${count(usage?.unknown_usage_or_cost_records)}. Subscription usage, real expenses and virtual capital remain separate.`);
    const active = ["STARTING", "RUNNING", "MANAGEMENT_ONLY", "STOPPING", "ATTACHED_EXISTING"].includes(service.status);
    for (const button of all("[data-service-action]")) {
      const action = button.dataset.serviceAction;
      const enabled = action === "start-trading" ? ready[`${service.mode || "paper"}_available`] : action === "start-optimisation" ?
        service.status === "RUNNING" && ready.ai_available && profile === "RUNNING" && !["QUEUED", "RUNNING", "BLOCKED"].includes(cycle?.status) : active;
      button.disabled = !connected || !owner || !csrf || Boolean(serviceAction) || !enabled;
    }
  }
  async function controlService(button) {
    const action = button.dataset.serviceAction;
    if (button.disabled || serviceAction || !owner || !csrf || !connected) return;
    serviceAction = action;
    const feedback = one("[data-service-feedback]");
    feedback.classList.remove("is-error");
    feedback.textContent = "Recording owner request…";
    renderLifecycle(snapshot?.lifecycle);
    let requestId = serviceRequests.get(action);
    if (!requestId) {
      try { requestId = sessionStorage.getItem(`tg-service-${action}`); } catch (_) { /* storage can be unavailable */ }
      requestId ||= crypto.randomUUID();
      serviceRequests.set(action, requestId);
      try { sessionStorage.setItem(`tg-service-${action}`, requestId); } catch (_) { /* current page keeps the identity */ }
    }
    const body = action === "pause" ? {profile: "MANAGE_ONLY", reason: "Owner dashboard AI pause"} :
      action === "start-trading" ? {request_id: requestId, mode: snapshot?.lifecycle?.service?.mode || "paper"} :
      action === "stop-service" ? {request_id: requestId, position_policy: "manage-only"} : {request_id: requestId};
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    let definite = false;
    try {
      const response = await fetch(`/api/v1/owner/${action}`, {method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: JSON.stringify(body), signal: controller.signal});
      const result = await response.json();
      definite = true;
      if (response.status === 401 || response.status === 403) loginNotice();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `Request rejected (HTTP ${response.status}).`);
      feedback.textContent = action === "start-optimisation" ? `One bounded cycle ${text(result.task_id)} recorded. Follow its saved status below.` :
        action === "start-trading" ? `${result.attached ? "Attached to existing" : "Recorded startup for"} service ${text(result.run_id) || "already owned"}. Check its actual status below.` :
        action === "pause" ? "AI paused. Reconciliation and protection continue." : text(result.management);
      await poll();
    } catch (error) {
      feedback.classList.add("is-error");
      feedback.textContent = definite ? text(error.message) : "Request outcome uncertain. Saved status will refresh; another click reuses this request identity.";
    } finally {
      clearTimeout(timeout);
      if (definite) {
        serviceRequests.delete(action);
        try { sessionStorage.removeItem(`tg-service-${action}`); } catch (_) { /* no persisted request */ }
      }
      serviceAction = null;
      renderLifecycle(snapshot?.lifecycle);
    }
  }
  function render(data) {
    snapshot = data;
    renderLifecycle(data.lifecycle);
    renderProject(data.project || {});
    write("[data-service-label]", data.service?.status === "running" ? "Trading service is running" : data.service?.status === "idle" ? "Trading service is idle" : "Not observed");
    write("[data-service-note]", data.service?.last_seen ? `Last observed ${time(data.service.last_seen)}` : "Dashboard access does not mean agents are running");
    write("[data-next-title]", data.next_step?.title, "Awaiting the next verified step");
    write("[data-next-description]", data.next_step?.description, "No next step recorded.");
    const warning = one("[data-storage-warning]");
    warning.hidden = !data.test_storage_error;
    warning.textContent = text(data.test_storage_error);
    renderDepartments(data.departments);
    if (changed("connections", data.connections)) { selectDepartment(selectedDepartment); requestAnimationFrame(drawEdges); }
    renderMilestones(data.milestones);
    renderRuns(data.test_runs);
    for (const [id, attemptedAt] of unknownChecks) {
      if (list(data.test_runs).some(run => run.check_id === id && Date.parse(run.started_at) >= attemptedAt - 2000)) unknownChecks.delete(id);
    }
    for (const [id, runId] of acknowledgedChecks) {
      const run = list(data.test_runs).find(item => item.run_id === runId);
      if (run && !["queued", "running"].includes(run.status)) acknowledgedChecks.delete(id);
    }
    renderChecks(data.checks);
    renderActivity(data.activity);
  }
  async function poll() {
    clearTimeout(pollTimer);
    if (preview || polling || document.hidden || authExpired) return;
    polling = true;
    const controller = new AbortController();
    pollController = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/v1/progress", {credentials: "same-origin", cache: "no-store", signal: controller.signal});
      if (response.status === 401 || response.status === 403) { loginNotice(); return; }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data || typeof data !== "object" || Array.isArray(data) || !data.project) throw new Error("Invalid progress response");
      connected = true;
      receivedAt = new Date().toISOString();
      render(data);
      syncState("Dashboard connected · updates every 5 seconds", "connected");
    } catch (_) {
      connected = false;
      if (!document.hidden) syncState("Updates unavailable · showing last saved snapshot", "offline");
      updateControls();
      renderLifecycle(snapshot?.lifecycle);
    } finally {
      clearTimeout(timeout);
      polling = false;
      if (pollController === controller) pollController = null;
      if (!document.hidden && !authExpired) pollTimer = setTimeout(poll, 5000);
    }
  }
  async function runCheck(button) {
    const checkId = button.dataset.runCheck;
    if (button.dataset.runCheck === "kraken-account" || button.disabled || runningCheck || !owner || !csrf || !connected) return;
    runningCheck = checkId;
    const feedback = button.closest("[data-check-id]").querySelector("[data-check-feedback]");
    feedback.classList.remove("is-error");
    feedback.textContent = "Starting this check. Results will appear in the saved run history.";
    updateControls();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    const attemptedAt = Date.now();
    let acknowledged = false;
    try {
      const response = await fetch(`/api/v1/progress/tests/${encodeURIComponent(checkId)}/run`, {
        method: "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf}, body: "{}"
      });
      if (response.status === 401 || response.status === 403) { loginNotice(); throw new Error("The check was not accepted. Sign in again or review owner access."); }
      const body = await response.json();
      if (!response.ok) {
        acknowledged = true;
        throw new Error(typeof body.detail === "string" ? body.detail : `The service rejected the check (HTTP ${response.status}).`);
      }
      if (!body.run_id) throw new Error("The service did not return a saved run identifier.");
      acknowledged = true;
      if (["queued", "running"].includes(body.status)) acknowledgedChecks.set(checkId, body.run_id);
      feedback.textContent = `Run ${text(body.run_id)} recorded. Its actual status and results are in the run history.`;
      await poll();
    } catch (error) {
      if (!acknowledged && !authExpired) {
        unknownChecks.set(checkId, attemptedAt);
        feedback.textContent = "The request outcome is uncertain. We will look for its saved run before allowing another attempt.";
      } else feedback.textContent = text(error.message || "This check could not be started.");
      feedback.classList.add("is-error");
    } finally {
      clearTimeout(timeout);
      runningCheck = null;
      updateControls();
    }
  }
  root.addEventListener("click", event => {
    const department = event.target.closest("[data-department-id]");
    if (department && !department.disabled) selectDepartment(department.dataset.departmentId);
    const filter = event.target.closest("[data-task-filter]");
    if (filter && !filter.disabled) { taskFilter = filter.dataset.taskFilter; filterTasks(); }
    const serviceButton = event.target.closest("[data-service-action]");
    if (serviceButton) void controlService(serviceButton);
    const button = event.target.closest("[data-run-check]");
    if (button) void runCheck(button);
  });
  for (const node of all("[data-department-id], [data-task-filter]")) node.disabled = false;
  selectDepartment(selectedDepartment);
  if (globalThis.ResizeObserver) new ResizeObserver(() => requestAnimationFrame(drawEdges)).observe(one("[data-department-graph]"));
  else window.addEventListener("resize", drawEdges);
  if (preview) {
    try {
      const data = JSON.parse(document.getElementById("mission-control-preview-data")?.textContent || "null");
      if (data && typeof data === "object" && !Array.isArray(data)) render(data);
    } catch (_) { /* The server-rendered snapshot remains readable. */ }
    syncState("Saved preview · live updates need the dashboard server", "paused");
    updateControls();
    return;
  }
  document.addEventListener("visibilitychange", () => {
    clearTimeout(pollTimer);
    if (document.hidden) {
      pollController?.abort();
      syncState("Updates paused · tab is hidden", "paused");
    } else void poll();
  });
  window.addEventListener("pagehide", () => { clearTimeout(pollTimer); pollController?.abort(); });
  void poll();
})();
