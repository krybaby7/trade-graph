/* Trade Graph HQ price charts (T41): draws GET /api/v1/hq/chart with the vendored TradingView Lightweight Charts.
   The server projects every candle, indicator value, line and marker from saved records and writes every sentence;
   this file only draws them and lays out the words. The library is fetched from HQ's own origin, with a pinned
   integrity, the first time a chart is shown (D195). Its own attribution logo stays off: it would add a <style>
   element, which the page's policy refuses, and send HQ's address to TradingView. The panel's attribution line in
   hq.html names TradingView, links to its site and to the licence and notice, and no chart is drawn without it (D196).
   Words show the server's exact decimal text; only the canvas gets plain numbers, to place things. */
"use strict";

(() => {
  const HQ = globalThis.TradeGraphHQ;
  if (!HQ) return;
  const doc = document;
  const {el, button, list, str} = HQ;

  const LIBRARY = Object.freeze({
    // The pinned version in the address lets the browser keep the file for a year (W8-C, D253); HQ sends everything
    // else no-store. scripts/vendor_lightweight_charts.py --check keeps it equal to VENDOR.json's version.
    src: "/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js?v=5.2.1",
    integrity: "sha384-KkYqTZlM13Zya6fVUF3IGCBOQ1ehFeJVVgxaVvD/0MebineF9EI8Jkd3NnWNChqN",
  });
  const SCHEMA = "trade-graph.hq.chart.v1";
  const REFRESH_MS = 60000;
  const HALTED = new Set(["signed_out", "forbidden", "outdated"]);
  const TONES = Object.freeze(["surface", "text", "muted", "border", "accent", "warning", "danger", "success"]);
  // Only for a browser that can't read the theme's colour tokens; every HQ page reads them from dashboard.css.
  const FALLBACK = Object.freeze({surface: "white", text: "black", muted: "gray", border: "lightgray", accent: "blue",
    warning: "darkorange", danger: "firebrick", success: "green"});
  const INTERVALS = Object.freeze([1, 5, 15, 60]);
  const INTERVAL_WORDS = new Map([[1, "1 min"], [5, "5 min"], [15, "15 min"], [60, "1 hour"]]);
  const INTERVAL_LABEL = new Map([[1, "1-minute"], [5, "5-minute"], [15, "15-minute"], [60, "1-hour"]]);
  const REASON_WORDS = Object.freeze({day_plan: "day plan", held: "held", alarm: "alarm", shortlist: "shortlist",
    reference: "market reference"});
  // How each kind of line looks: a theme colour token and a line style (the library's LineStyle: 0 solid, 1 dotted,
  // 2 dashed). The legend repeats the style in words beside each line, so colour is never the only difference.
  const LINE_LOOK = Object.freeze({
    alarm: {tone: "warning", style: 2, dash: "dashed"},
    entry: {tone: "accent", style: 0, dash: "solid"},
    stop: {tone: "danger", style: 2, dash: "dashed"},
    take_profit: {tone: "success", style: 2, dash: "dashed"},
    order: {tone: "muted", style: 1, dash: "dotted"},
  });
  const MARKER_LOOK = Object.freeze({
    buy: {tone: "accent", shape: "arrowUp", position: "atPriceBottom", word: "Buy"},
    sell: {tone: "text", shape: "arrowDown", position: "atPriceTop", word: "Sell"},
    alarm: {tone: "warning", shape: "circle", position: "atPriceMiddle", word: "Alarm"},
  });
  const INDICATOR_TONES = Object.freeze(["accent", "warning", "muted", "text"]);
  const OUTPUT_LOOKS = Object.freeze([{style: 0, dash: "solid"}, {style: 2, dash: "dashed"}, {style: 1, dash: "dotted"},
    {style: 3, dash: "dashed"}]);
  const MARKERS_LISTED = 20;
  const yearFormat = new Intl.DateTimeFormat("en-GB", {year: "numeric"});
  const monthFormat = new Intl.DateTimeFormat("en-GB", {month: "short"});
  const dayFormat = new Intl.DateTimeFormat("en-GB", {day: "numeric", month: "short"});

  const state = {
    symbol: null, interval: null, document: null, drawing: null, timer: null, loading: null, busy: false, again: false,
    started: false, loadedAt: 0, seen: typeof globalThis.IntersectionObserver !== "function", initialised: false,
    themeWatching: false, schemeWatching: false, controlsKey: null,
  };

  const panel = () => doc.querySelector("[data-hq-charts]");
  const part = name => panel()?.querySelector(`[data-hq-chart-${name}]`) || null;
  const tabMode = () => Boolean(globalThis.matchMedia?.("(max-width: 767px)")?.matches);

  function shown() {
    const box = panel();
    if (!box || doc.hidden || box.hidden || !state.seen) return false;
    const section = box.closest("[data-hq-section]");
    return !(tabMode() && section && !section.classList.contains("is-active"));
  }
  // The licence asks for the attribution wherever a chart is shown: without its line beside the box, nothing is drawn.
  function attributed() {
    const line = part("attribution");
    return Boolean(line && !line.hidden && line.querySelector("a[rel]"));
  }
  function say(words) {
    const node = part("status");
    if (!node) return;
    node.textContent = words || "";
    node.hidden = !words;
  }

  // --- The library: fetched from HQ itself, once, with its pinned integrity ---------------------------------------

  function library() {
    if (globalThis.LightweightCharts) return Promise.resolve(globalThis.LightweightCharts);
    if (!state.loading) {
      state.loading = new Promise((resolve, reject) => {
        const script = doc.createElement("script");
        script.src = LIBRARY.src;
        script.integrity = LIBRARY.integrity;
        script.async = true;
        script.addEventListener("load", () => {
          if (globalThis.LightweightCharts) resolve(globalThis.LightweightCharts);
          else reject(new Error("The chart library did not start."));
        });
        script.addEventListener("error", () => reject(new Error("The chart library did not load.")));
        doc.head.append(script);
      });
      state.loading.catch(() => { state.loading = null; });  // the next refresh tries again
    }
    return state.loading;
  }

  // --- Words and numbers -------------------------------------------------------------------------------------------

  function colours() {
    let style = null;
    try { style = globalThis.getComputedStyle?.(doc.documentElement) || null; } catch (_) { style = null; }
    const result = {};
    for (const tone of TONES) result[tone] = str(style?.getPropertyValue?.(`--${tone}`)).trim() || FALLBACK[tone];
    return result;
  }
  const msOf = time => Number(time) * 1000;
  function tickWords(time, type) {
    const ms = msOf(time);
    if (type === 0) return yearFormat.format(ms);
    if (type === 1) return monthFormat.format(ms);
    if (type === 2) return dayFormat.format(ms);
    return HQ.fmt.clock(ms);
  }
  const timeWords = time => `${HQ.fmt.dateWords(msOf(time))}, ${HQ.fmt.clock(msOf(time))}`;
  function priceFormat(precision) {
    const places = Number.isSafeInteger(precision) && precision >= 0 && precision <= 12 ? precision : 2;
    return {type: "price", precision: places, minMove: 10 ** -places};
  }
  function scaleFormat(values) {
    const largest = values.reduce((most, value) => Math.max(most, Math.abs(value)), 0);
    return priceFormat(largest >= 10 ? 2 : largest >= 1 ? 3 : 6);
  }
  // The price scale also takes in the account's own order levels near the price (resting entries, safety exits and
  // other open orders within LEVEL_REACH of the newest close), so the money at stake is on screen. Alarm levels show
  // once the price comes near them; every level, on screen or not, is in the legend.
  const LEVEL_REACH = 0.1;
  const ORDER_KINDS = new Set(["entry", "stop", "take_profit", "order"]);
  function levelsNear(data) {
    const rows = list(data.candles);
    const close = rows.length ? Number(rows[rows.length - 1][4]) : NaN;
    if (!Number.isFinite(close) || close <= 0) return [];
    return list(data.lines).filter(line => line.pane === "price" && ORDER_KINDS.has(line.kind)).map(line => Number(line.price))
      .filter(level => Number.isFinite(level) && Math.abs(level - close) <= close * LEVEL_REACH);
  }
  function slotTimes(window_) {
    const times = [];
    for (let time = window_.first_open; time <= window_.now_open; time += window_.step_seconds) times.push(time);
    return times;
  }

  // --- Controls, legend and readout (plain DOM, words only) ------------------------------------------------------

  function controls(data) {
    const root = part("controls");
    if (!root) return;
    const key = JSON.stringify([list(data.coins).map(coin => [coin.symbol, coin.reasons]), data.symbol, data.interval_minutes]);
    if (key === state.controlsKey) return;
    state.controlsKey = key;
    const coin = el("label", "hq-chart-coin");
    const select = el("select", "hq-chart-select");
    for (const item of list(data.coins)) {
      const words = list(item.reasons).map(reason => REASON_WORDS[reason] || reason).join(", ");
      const option = el("option", "", `${str(item.symbol)} · ${words}`);
      option.value = str(item.symbol);
      select.append(option);
    }
    if (data.symbol) select.value = data.symbol;
    select.addEventListener("change", () => choose({symbol: select.value, interval: state.interval}));
    coin.append(el("span", "hq-chart-control-label", "Coin"), select);
    const sizes = el("div", "hq-chart-intervals");
    sizes.setAttribute("role", "group");
    sizes.setAttribute("aria-label", "Candle size");
    for (const interval of INTERVALS) {
      const control = button("hq-chart-interval", INTERVAL_WORDS.get(interval));
      control.setAttribute("aria-pressed", String(interval === data.interval_minutes));
      control.addEventListener("click", () => choose({symbol: state.symbol, interval}));
      sizes.append(control);
    }
    root.replaceChildren(coin, sizes);
  }

  function swatch(tone, dash) {
    const mark = el("span", "hq-swatch");
    mark.dataset.tone = tone;
    mark.dataset.dash = dash;
    mark.setAttribute("aria-hidden", "true");
    return mark;
  }
  function entry(words, tone, dash, className = "") {
    const item = el("li", `hq-chart-key ${className}`.trim());
    if (tone) item.append(swatch(tone, dash));
    item.append(el("span", "", words));
    return item;
  }
  function legend(data, drawn) {
    const root = part("legend");
    if (!root) return;
    const items = [];
    if (drawn) items.push(entry("Candles: hollow when the price rose, filled when it fell.", "text", "candle"));
    let tone = 0;
    for (const indicator of list(data.indicators)) {
      if (indicator.status === "ready") {
        const outputs = list(indicator.outputs);
        const parts = outputs.length > 1 ? ` (${outputs.map((output, index) => `${output.label.split(" · ").pop()} ${OUTPUT_LOOKS[index % OUTPUT_LOOKS.length].dash}`).join(", ")})` : "";
        const where = indicator.pane === "price" ? "on the prices" : "in its own panel below";
        items.push(entry(`${indicator.label}: ${indicator.title}, ${where}${parts}.`, INDICATOR_TONES[tone % INDICATOR_TONES.length], "solid"));
        tone += 1;
      } else {
        items.push(entry(`${indicator.label}: ${str(indicator.status_text)}`, null, null, "hq-muted"));
      }
    }
    for (const line of list(data.lines)) {
      const look = LINE_LOOK[line.kind] || LINE_LOOK.order;
      items.push(entry(`${line.text} (${look.dash} line)`, look.tone, look.dash));
    }
    for (const words of list(data.elsewhere)) items.push(entry(str(words), null, null, "hq-muted"));
    for (const words of list(data.notes)) items.push(entry(str(words), null, null, "hq-muted"));
    const markers = list(data.markers);
    if (markers.length) {
      const box = HQ.keepOpen(el("details", "hq-chart-marks"), "chart:marks");
      box.append(el("summary", "", `Marked on the chart (${markers.length})`));
      const marks = el("ul", "hq-plain-list");
      for (const marker of markers.slice(-MARKERS_LISTED).reverse()) {
        const look = MARKER_LOOK[marker.kind] || MARKER_LOOK.alarm;
        const item = el("li", "", `${look.word}: ${str(marker.text)}, `);
        item.append(HQ.renderText(el("span"), [{t: "time", at: marker.at, style: "relative_clock"}], {inert: true}));
        marks.append(item);
      }
      box.append(marks);
      const holder = el("li", "hq-chart-key");
      holder.append(box);
      items.push(holder);
    }
    root.replaceChildren(...items);
  }

  function readout(data, time) {
    const node = part("readout");
    if (!node) return;
    const rows = list(data?.candles);
    if (!rows.length || !data.window) { node.textContent = ""; return; }
    const row = time === undefined || time === null ? rows[rows.length - 1] : rows.find(item => item[0] === time) || null;
    const at = row ? row[0] : time;
    node.textContent = row
      ? `${timeWords(at)} · Open ${row[1]} · High ${row[2]} · Low ${row[3]} · Close ${row[4]}`
      : `${timeWords(at)} · No candle saved for this time.`;
  }

  // --- Drawing -----------------------------------------------------------------------------------------------------

  function candleData(data) {
    const rows = new Map(list(data.candles).map(row => [row[0], row]));
    return slotTimes(data.window).map(time => {
      const row = rows.get(time);
      return row ? {time, open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4])} : {time};
    });
  }
  function lineData(data, values) {
    const {first_open: first, step_seconds: step} = data.window;
    return list(values).map((value, index) => ({time: first + index * step, value}));
  }
  function markerData(data, colors) {
    return list(data.markers).map(marker => {
      const look = MARKER_LOOK[marker.kind] || MARKER_LOOK.alarm;
      const base = {time: marker.t, shape: look.shape, color: colors[look.tone], text: look.word};
      return marker.price === null || marker.price === undefined
        ? {...base, position: "aboveBar"}
        : {...base, position: look.position, price: Number(marker.price)};
    });
  }
  function layoutKey(data) {
    return JSON.stringify([data.symbol, data.interval_minutes, data.price_precision,
      list(data.indicators).map(item => [item.key, item.status, list(item.outputs).map(output => output.name)])]);
  }

  function build(LW, box, data) {
    const colors = colours();
    const chart = LW.createChart(box, {
      autoSize: true, width: box.clientWidth || 600, height: box.clientHeight || 320,
      layout: {attributionLogo: false, background: {type: "solid", color: colors.surface}, textColor: colors.text,
        panes: {separatorColor: colors.border, separatorHoverColor: colors.border}},
      grid: {vertLines: {color: colors.border}, horzLines: {color: colors.border}},
      rightPriceScale: {borderColor: colors.border},
      timeScale: {borderColor: colors.border, timeVisible: true, secondsVisible: false, rightOffset: 3, tickMarkFormatter: tickWords},
      localization: {timeFormatter: timeWords},
      // The page keeps scrolling over the chart (a wheel or an up-and-down swipe never gets caught by it); drag
      // sideways to look back, and pinch or drag the time axis to zoom.
      handleScroll: {mouseWheel: false, vertTouchDrag: false},
      handleScale: {mouseWheel: false},
    });
    const format = priceFormat(data.price_precision);
    const candles = chart.addSeries(LW.CandlestickSeries, {
      upColor: colors.surface, borderUpColor: colors.success, wickUpColor: colors.success,
      autoscaleInfoProvider: base => {
        const found = base();
        const levels = levelsNear(state.document || data);
        if (!found?.priceRange || !levels.length) return found;
        return {...found, priceRange: {minValue: Math.min(found.priceRange.minValue, ...levels),
          maxValue: Math.max(found.priceRange.maxValue, ...levels)}};
      },
      downColor: colors.danger, borderDownColor: colors.danger, wickDownColor: colors.danger, priceFormat: format,
    });
    const series = [];
    let pane = 0;
    let tone = 0;
    for (const indicator of list(data.indicators)) {
      if (indicator.status !== "ready") continue;
      const own = indicator.pane !== "price";
      const index = own ? (pane += 1) : 0;
      const color = colors[INDICATOR_TONES[tone % INDICATOR_TONES.length]];
      tone += 1;
      const values = list(indicator.outputs).flatMap(output => list(output.values));
      list(indicator.outputs).forEach((output, position) => {
        const look = OUTPUT_LOOKS[position % OUTPUT_LOOKS.length];
        // Lines on the prices carry no label of their own (the legend names them; labels crowded a phone's chart);
        // a panel of its own shows its name.
        const common = {color, title: own ? output.label : "", priceLineVisible: false, lastValueVisible: false,
          priceFormat: own ? scaleFormat(values) : format};
        const made = output.style === "histogram"
          ? chart.addSeries(LW.HistogramSeries, common, index)
          : chart.addSeries(LW.LineSeries, {...common, lineWidth: 2, lineStyle: look.style, crosshairMarkerVisible: false}, index);
        series.push({key: indicator.key, name: output.name, api: made});
      });
    }
    box.setAttribute("data-panes", String(1 + pane));
    if (pane) chart.panes?.()?.[0]?.setStretchFactor?.(3);
    const drawing = {lib: LW, key: layoutKey(data), chart, candles, series, lines: [], colors,
      markers: LW.createSeriesMarkers(candles, [])};
    chart.subscribeCrosshairMove(param => readout(state.document, param?.time));
    update(drawing, data);
    return drawing;
  }

  function update(drawing, data) {
    drawing.candles.setData(candleData(data));
    const outputs = new Map(list(data.indicators).flatMap(item => list(item.outputs).map(output => [`${item.key}|${output.name}`, output])));
    for (const item of drawing.series) item.api.setData(lineData(data, outputs.get(`${item.key}|${item.name}`)?.values));
    for (const handle of drawing.lines) handle.series.removePriceLine(handle.line);
    drawing.lines = [];
    const firstOf = new Map();
    for (const item of drawing.series) if (!firstOf.has(item.key)) firstOf.set(item.key, item.api);
    for (const line of list(data.lines)) {
      const target = line.pane === "price" ? drawing.candles : firstOf.get(line.pane);
      if (!target) continue;
      const look = LINE_LOOK[line.kind] || LINE_LOOK.order;
      const made = target.createPriceLine({price: Number(line.price), color: drawing.colors[look.tone],
        lineWidth: line.kind === "entry" ? 2 : 1, lineStyle: look.style, axisLabelVisible: true, title: line.title});
      drawing.lines.push({series: target, line: made});
    }
    drawing.markers.setMarkers(markerData(data, drawing.colors));
    part("box")?.setAttribute("aria-label", `Price chart of ${data.symbol}, ${INTERVAL_LABEL.get(data.interval_minutes)} candles`);
    readout(data);
  }

  function destroy() {
    if (!state.drawing) return;
    try { state.drawing.chart.remove(); } catch (error) { console.error(error); }
    state.drawing = null;
    part("box")?.removeAttribute("data-panes");
  }
  function visibleRange(data) {
    const drawing = state.drawing;
    if (!drawing || !state.document || state.document.symbol !== data.symbol) return null;
    if (state.document.interval_minutes !== data.interval_minutes) return null;
    try { return drawing.chart.timeScale().getVisibleLogicalRange?.() || null; } catch (_) { return null; }
  }
  function draw(LW, data, range = null) {
    const box = part("box");
    if (!box) return;
    if (state.drawing && state.drawing.lib === LW && state.drawing.key === layoutKey(data)) {
      update(state.drawing, data);
      return;
    }
    destroy();
    state.drawing = build(LW, box, data);
    if (range) {
      try { state.drawing.chart.timeScale().setVisibleLogicalRange(range); } catch (_) { /* The newest candles show. */ }
    }
  }
  function redraw() {
    const LW = globalThis.LightweightCharts;
    const data = state.document;
    if (!LW || !data || !state.drawing) return;
    const range = visibleRange(data);
    destroy();
    draw(LW, data, range);
  }

  async function render(data) {
    const range = visibleRange(data);
    controls(data);
    if (data.status !== "ok") {
      state.document = data;
      legend(data, false);
      destroy();
      readout(null);
      say(str(data.status_text));
      return;
    }
    if (!attributed()) {
      state.document = data;
      legend(data, false);
      destroy();
      say("The chart can't be shown here: its attribution line is missing.");
      return;
    }
    let LW;
    try { LW = await library(); } catch (_) {
      state.document = data;
      legend(data, false);
      say("The chart couldn't be drawn: its drawing library didn't load. HQ will try again in a minute.");
      return;
    }
    state.document = data;
    say("");
    legend(data, true);
    draw(LW, data, range);
  }

  // --- Reading -------------------------------------------------------------------------------------------------------

  function schedule() {
    clearTimeout(state.timer);
    if (!HALTED.has(HQ.connection())) state.timer = setTimeout(tick, REFRESH_MS);
  }
  function tick() {
    if (shown()) load();
    else schedule();
  }
  async function load() {
    if (state.busy) { state.again = true; return; }
    state.busy = true;
    try {
      const result = await HQ.api.get("chart", {symbol: state.symbol, interval: state.interval, timeout: 20000});
      state.loadedAt = Date.now();
      const data = result.body;
      if (!result.ok || !data || data.schema !== SCHEMA) {
        say(state.drawing ? "The chart couldn't be loaded just now; it shows what HQ saw last. HQ will try again in a minute."
          : "The chart couldn't be loaded just now. HQ will try again in a minute.");
        return;
      }
      if (data.status === "coin_not_listed") {
        const first = str(list(data.coins)[0]?.symbol);
        if (first && first !== state.symbol) {
          state.symbol = first;
          state.interval = null;
          state.again = true;
          return;
        }
      }
      if (data.status !== "coin_not_listed") {
        state.symbol = data.symbol;
        state.interval = data.interval_minutes;
      }
      await render(data);
    } catch (error) {
      console.error(error);
      say("The chart couldn't be drawn just now. HQ will try again in a minute.");
    } finally {
      state.busy = false;
      schedule();
      if (state.again) {
        state.again = false;
        load();
      }
    }
  }
  function choose(next) {
    state.symbol = next.symbol || state.symbol;
    state.interval = INTERVALS.includes(next.interval) ? next.interval : state.interval;
    load();
  }
  function wake() {
    if (!shown()) return;
    if (!state.started) {
      state.started = true;
      load();
    } else if (Date.now() - state.loadedAt >= REFRESH_MS) load();
  }

  // --- Start ---------------------------------------------------------------------------------------------------------

  function watchTheme() {
    const Observer = globalThis.MutationObserver;
    if (!state.themeWatching && typeof Observer === "function") {
      new Observer(changes => {
        if (list(changes).some(change => change.attributeName === "data-theme")) redraw();
      }).observe(doc.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
      state.themeWatching = true;
    }
    const scheme = globalThis.matchMedia?.("(prefers-color-scheme: dark)");
    if (!state.schemeWatching && typeof scheme?.addEventListener === "function") {
      scheme.addEventListener("change", redraw);
      state.schemeWatching = true;
    }
  }
  function init() {
    const box = panel();
    if (!box || state.initialised) return;
    state.initialised = true;
    watchTheme();
    const later = () => setTimeout(wake, 0);
    if (typeof globalThis.IntersectionObserver === "function") {
      new globalThis.IntersectionObserver(seen => {
        if (list(seen).some(item => item.isIntersecting)) { state.seen = true; wake(); }
      }, {rootMargin: "200px"}).observe(box);
    }
    doc.addEventListener("visibilitychange", later);
    globalThis.addEventListener?.("hashchange", later);
    doc.addEventListener("click", event => {
      if (event.target instanceof Element && event.target.closest("a[href='#money']")) later();
    });
    globalThis.matchMedia?.("(max-width: 767px)")?.addEventListener?.("change", later);
    wake();
  }

  HQ.define("charts", {init, wake, watchTheme, load, LIBRARY});
})();
