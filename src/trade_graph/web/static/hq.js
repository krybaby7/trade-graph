/* Trade Graph HQ bootstrap: read the JSON island, render it at once, then poll GET /api/v1/hq. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const doc = document;

  HQ.bindGlobal();
  HQ.use("render")?.initTabs();

  let data = null;
  try { data = JSON.parse(doc.getElementById("hq-snapshot")?.textContent || "null"); } catch (_) { data = null; }
  if (!HQ.setSnapshot(data, Date.now())) {
    // An empty or unreadable island: keep the server-rendered first paint and ask for a fresh snapshot.
    HQ.freshness();
    HQ.refresh();
  }
  HQ.poller.start();

  // "Since you last looked": the page counts from the value read at load; the new value is saved after the
  // page has been visible for 10 s, and again when it is hidden or closed.
  let visibleFor = 0;
  let visibleSince = doc.hidden ? null : Date.now();
  function remember() {
    const snapshot = HQ.snapshot();
    const seen = visibleFor + (visibleSince === null ? 0 : Date.now() - visibleSince);
    if (snapshot && seen >= 10000) HQ.store.set(HQ.KEYS.lastSeen, snapshot.as_of);
  }
  doc.addEventListener("visibilitychange", () => {
    if (doc.hidden) {
      if (visibleSince !== null) visibleFor += Date.now() - visibleSince;
      visibleSince = null;
      remember();
    } else visibleSince = Date.now();
  });
  globalThis.addEventListener("pagehide", remember);
  setInterval(remember, 5000);
})();
