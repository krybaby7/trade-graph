// HQ price charts (T41): hq-charts.js on the harness page with a scripted GET /api/v1/hq/chart.
// TEST STUB: `recordingLibrary()` below is NOT TradingView Lightweight Charts. It records the calls hq-charts.js makes
// on the small part of the library's v5 API that HQ uses (createChart, addSeries, createPriceLine,
// createSeriesMarkers, panes, timeScale, crosshair), so these tests check what HQ asks the library to draw. The real
// vendored library is checked in a browser (planning/handoffs/W5-B.md) and its file by tests/test_hq_charts_static.py.
import test from "node:test";
import assert from "node:assert/strict";
import {SCRIPTS, clone, fixture, loadHQ, response} from "./hq_harness.mjs";

const CHART_SCRIPTS = [...SCRIPTS, "hq-charts.js"];
const STEP = 900;
const FIRST = 1760580000;

function chartDocument(changes = {}) {
  const slots = 4;
  return {
    schema: "trade-graph.hq.chart.v1", as_of: "2026-10-16T08:00:01.000Z", status: "ok", status_text: null,
    coins: [{symbol: "SOL/USD", reasons: ["day_plan", "held"]}, {symbol: "BTC/USD", reasons: ["reference"]}],
    symbol: "SOL/USD", interval_minutes: 15, intervals: [1, 5, 15, 60], source: "kraken_public_ohlc", price_precision: 2,
    window: {first_open: FIRST, step_seconds: STEP, slots, now_open: FIRST + 5 * STEP},
    newest_close: "2026-10-16T07:45:00.000Z",
    candles: [
      [FIRST, "100.10", "100.40", "99.90", "100.20", "12.5"],
      [FIRST + STEP, "100.20", "100.30", "99.80", "99.85", "3"],
      [FIRST + 3 * STEP, "99.85", "100.00", "99.70", "99.95", "4.25"],
    ],
    missing: [FIRST + 2 * STEP],
    indicators: [
      {key: "SMA|15|timeperiod=20", indicator: "SMA", title: "Simple moving average", label: "SMA 20",
       parameters: {timeperiod: 20}, interval_minutes: 15, pane: "price", status: "ready", status_text: null,
       chosen_by: ["day_plan"], outputs: [{name: "value", label: "SMA 20", style: "line", values: [100, 100.1, 100.05, 100]}]},
      {key: "MACD|15|fastperiod=12,signalperiod=9,slowperiod=26", indicator: "MACD", title: "MACD", label: "MACD 12, 26, 9",
       parameters: {fastperiod: 12, slowperiod: 26, signalperiod: 9}, interval_minutes: 15, pane: "own", status: "ready",
       status_text: null, chosen_by: ["day_plan"], outputs: [
         {name: "macd", label: "MACD", style: "line", values: [0.1, 0.2, 0.15, 0.05]},
         {name: "signal", label: "Signal", style: "line", values: [0.1, 0.12, 0.13, 0.11]},
         {name: "histogram", label: "Histogram", style: "histogram", values: [0, 0.08, 0.02, -0.06]}]},
      {key: "RSI|15|timeperiod=14", indicator: "RSI", title: "Relative Strength Index", label: "RSI 14",
       parameters: {timeperiod: 14}, interval_minutes: 15, pane: "own", status: "gapped",
       status_text: "Not drawn: 1 candle it needs is missing. Nothing is filled in.", chosen_by: ["day_plan", "alarm"], outputs: []},
    ],
    lines: [
      {kind: "alarm", price: "95", title: "Alarm", text: "Trader's alarm: mid price crosses below 95", pane: "price", state: "armed"},
      {kind: "entry", price: "99.5", title: "Buy", text: "Resting buy of 5 SOL at 99.5", pane: "price", state: "open"},
      {kind: "stop", price: "97", title: "Stop", text: "Safety exit for 5 SOL: stop at 97", pane: "price", state: "open"},
      {kind: "take_profit", price: "104", title: "Target", text: "Safety exit for 5 SOL: target at 104", pane: "price", state: "open"},
      {kind: "alarm", price: "0", title: "Alarm", text: "Trader's alarm: MACD below 0", pane: "MACD|15|fastperiod=12,signalperiod=9,slowperiod=26", state: "armed"},
    ],
    markers: [{kind: "buy", t: FIRST + 3 * STEP, at: "2026-10-16T07:31:00.000Z", price: "99.5", text: "Bought 5 SOL at 99.5"}],
    elsewhere: ["EMA 20 is watched on the 1 hour chart."],
    notes: ["1 candle is missing in this window. Gaps stay empty; nothing is filled in."],
    ...changes,
  };
}

// TEST STUB of the library surface (see the header): records every call, draws nothing.
function recordingLibrary() {
  const log = {charts: [], markers: []};
  const definition = name => Object.freeze({type: name});
  const library = {
    CandlestickSeries: definition("Candlestick"), LineSeries: definition("Line"), HistogramSeries: definition("Histogram"),
    createChart(container, options) {
      const chart = {container, options, series: [], removed: false, crosshair: [], applied: [], panesMade: []};
      chart.api = {
        addSeries(def, seriesOptions, paneIndex = 0) {
          const series = {type: def.type, options: seriesOptions, pane: paneIndex, data: null, lines: [], removedLines: 0};
          series.api = {
            setData(data) { series.data = data; },
            createPriceLine(line) { series.lines.push(line); return {line}; },
            removePriceLine() { series.removedLines += 1; },
            applyOptions(more) { Object.assign(series.options, more); },
          };
          chart.series.push(series);
          return series.api;
        },
        panes() { return [0, 1, 2, 3].map(index => ({setStretchFactor(value) { chart.panesMade[index] = value; }})); },
        timeScale() { return {getVisibleLogicalRange: () => null, setVisibleLogicalRange() {}, applyOptions() {}}; },
        subscribeCrosshairMove(callback) { chart.crosshair.push(callback); },
        applyOptions(more) { chart.applied.push(more); },
        remove() { chart.removed = true; },
      };
      log.charts.push(chart);
      return chart.api;
    },
    createSeriesMarkers(series, markers) {
      const entry = {series, markers};
      log.markers.push(entry);
      return {setMarkers(next) { entry.markers = next; }};
    },
  };
  return {library, log};
}

function chartPanel(page, {attribution = true} = {}) {
  const {doc} = page;
  const el = (tag, attrs = {}, ...children) => {
    const node = doc.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    node.append(...children);
    return node;
  };
  const panel = el("article", {class: "hq-panel hq-charts", "data-hq-charts": ""},
    el("div", {"data-hq-chart-controls": ""}), el("p", {"data-hq-chart-status": ""}),
    el("div", {class: "hq-chart-box", "data-hq-chart-box": ""}), el("p", {"data-hq-chart-readout": ""}),
    el("ul", {"data-hq-chart-legend": ""}));
  if (attribution) {
    panel.append(el("p", {class: "hq-chart-attribution", "data-hq-chart-attribution": ""},
      "Charts by ", el("a", {href: "tradingview-home", rel: "noopener noreferrer"}, "TradingView"), " Lightweight Charts"));
  }
  doc.querySelector("#money").append(panel);
  doc.querySelector("#money").classList.add("is-active");
  return panel;
}

async function start({document = chartDocument(), attribution = true, fail = false, failLibrary = false, answers} = {}) {
  const {library, log} = recordingLibrary();
  const queue = answers ? [...answers] : null;
  const page = loadHQ({scripts: CHART_SCRIPTS, fetch: async call => {
    if (call.url.startsWith("/api/v1/hq/chart")) {
      if (fail) return response(500, {detail: "synthetic failure"});
      return response(200, queue ? queue.shift() || queue.at(-1) || document : document);
    }
    return response(200, fixture("busy"));
  }});
  page.show(fixture("busy"));
  const scripts = [];
  const appendChild = page.doc.head.append.bind(page.doc.head);
  page.doc.head.append = (...nodes) => {
    for (const node of nodes) {
      if (node.localName === "script") {
        scripts.push(node);
        setImmediate(() => {
          if (!failLibrary) page.sandbox.LightweightCharts = library;
          node.dispatchEvent(new page.dom.Event(failLibrary ? "error" : "load"));
        });
      }
    }
    appendChild(...nodes);
  };
  page.sandbox.getComputedStyle = () => ({getPropertyValue: name => `token(${name})`});
  chartPanel(page, {attribution});
  page.use("charts").init();
  await page.flush();
  await page.flush();
  return {...page, library, log, scripts};
}

const chartCalls = page => page.calls.filter(call => call.url.startsWith("/api/v1/hq/chart"));

test("the library is loaded once from HQ itself, with its pinned integrity, after the first chart document", async () => {
  const page = await start();
  assert.equal(chartCalls(page).length, 1);
  assert.equal(chartCalls(page)[0].url, "/api/v1/hq/chart");
  assert.equal(page.scripts.length, 1);
  assert.equal(page.scripts[0].src, "/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js");
  assert.match(page.scripts[0].integrity, /^sha384-[A-Za-z0-9+/]{64}$/);
  assert.equal(page.log.charts.length, 1);
  const options = page.log.charts[0].options;
  assert.equal(options.layout.attributionLogo, false);
  assert.equal(options.autoSize, true);
  assert.equal(options.handleScroll.vertTouchDrag, false, "an up-and-down swipe scrolls the page, not the chart");
  assert.equal(options.handleScale.mouseWheel, false);
  assert.equal(options.layout.textColor, "token(--text)");
  assert.equal(options.layout.background.color, "token(--surface)");
  await page.advance(60000);
  assert.equal(chartCalls(page).length, 2, "the chart refreshes every minute while it is shown");
  assert.equal(page.scripts.length, 1, "the library is never fetched twice");
});

test("candles keep their gaps, end at the forming candle and read out the exact saved prices", async () => {
  const page = await start();
  const chart = page.log.charts[0];
  const candles = chart.series.find(series => series.type === "Candlestick");
  assert.deepEqual(clone(candles.data.map(item => item.time)), [FIRST, FIRST + STEP, FIRST + 2 * STEP, FIRST + 3 * STEP, FIRST + 4 * STEP, FIRST + 5 * STEP]);
  assert.deepEqual(clone(candles.data[0]), {time: FIRST, open: 100.1, high: 100.4, low: 99.9, close: 100.2});
  assert.deepEqual(clone(candles.data[2]), {time: FIRST + 2 * STEP}, "a missing candle stays an empty slot");
  assert.deepEqual(clone(candles.data[5]), {time: FIRST + 5 * STEP}, "the forming candle is an empty slot");
  // Hollow candles when the price rose, filled when it fell: a shape, not colour alone.
  assert.equal(candles.options.upColor, "token(--surface)");
  assert.equal(candles.options.borderUpColor, "token(--success)");
  assert.equal(candles.options.downColor, "token(--danger)");
  assert.deepEqual(clone(candles.options.priceFormat), {type: "price", precision: 2, minMove: 0.01});
  const readout = page.$("[data-hq-chart-readout]");
  assert.match(page.text(readout), /99\.85.*100\.00.*99\.70.*99\.95/);
  chart.crosshair[0]({time: FIRST + STEP});
  assert.match(page.text(readout), /Open 100\.20 · High 100\.30 · Low 99\.80 · Close 99\.85/);
  chart.crosshair[0]({time: FIRST + 2 * STEP});
  assert.match(page.text(readout), /No candle/);
  assert.equal(chart.container.getAttribute("aria-label"), "Price chart of SOL/USD, 15-minute candles");
});

test("indicators: overlays on the price pane, others on their own pane, unready ones only in words", async () => {
  const page = await start();
  const chart = page.log.charts[0];
  const sma = chart.series.find(series => series.type === "Line" && series.pane === 0);
  assert.equal(sma.options.title, "", "lines on the prices are named in the legend, not on the chart");
  assert.equal(sma.options.color, "token(--accent)");
  assert.deepEqual(sma.data.map(item => [item.time, item.value]), [[FIRST, 100], [FIRST + STEP, 100.1], [FIRST + 2 * STEP, 100.05], [FIRST + 3 * STEP, 100]]);
  const macd = chart.series.filter(series => series.pane === 1);
  assert.deepEqual(macd.map(series => [series.type, series.options.title]), [["Line", "MACD"], ["Line", "Signal"], ["Histogram", "Histogram"]]);
  assert.equal(chart.series.some(series => /RSI/.test(series.options.title || "")), false);
  assert.deepEqual(clone(macd[0].options.priceFormat), {type: "price", precision: 6, minMove: 0.000001});
  assert.equal(chart.container.getAttribute("data-panes"), "2");
  assert.equal(chart.panesMade[0], 3);
  const legend = page.text(page.$("[data-hq-chart-legend]"));
  assert.match(legend, /RSI 14.*Not drawn: 1 candle it needs is missing/);
  assert.match(legend, /EMA 20 is watched on the 1 hour chart/);
  assert.match(legend, /1 candle is missing in this window/);
});

test("alarm, order and safety exit lines carry their own look and words; fills are markers", async () => {
  const page = await start();
  const chart = page.log.charts[0];
  const candles = chart.series.find(series => series.type === "Candlestick");
  assert.deepEqual(candles.lines.map(line => [line.price, line.title, line.lineStyle, line.color]), [
    [95, "Alarm", 2, "token(--warning)"], [99.5, "Buy", 0, "token(--accent)"], [97, "Stop", 2, "token(--danger)"],
    [104, "Target", 2, "token(--success)"]]);
  const macd = chart.series.find(series => series.options.title === "MACD");
  assert.deepEqual(macd.lines.map(line => [line.price, line.title]), [[0, "Alarm"]]);
  // Order and exit levels within a tenth of the newest close widen the price scale so they show; alarm levels and
  // the MACD pane's level do not.
  const scale = candles.options.autoscaleInfoProvider(() => ({priceRange: {minValue: 99.7, maxValue: 100.4}}));
  assert.deepEqual(clone(scale.priceRange), {minValue: 97, maxValue: 104});
  assert.equal(page.log.markers.length, 1);
  assert.deepEqual(page.log.markers[0].markers.map(marker => [marker.time, marker.shape, marker.position, marker.price, marker.text]),
    [[FIRST + 3 * STEP, "arrowUp", "atPriceBottom", 99.5, "Buy"]]);
  const legend = page.text(page.$("[data-hq-chart-legend]"));
  for (const words of ["Trader's alarm: mid price crosses below 95", "Resting buy of 5 SOL at 99.5", "Safety exit for 5 SOL: stop at 97",
    "Bought 5 SOL at 99.5"]) assert.ok(legend.includes(words), words);
  const swatches = page.$$("[data-hq-chart-legend] .hq-swatch");
  assert.ok(swatches.some(node => node.dataset.tone === "warning" && node.dataset.dash === "dashed"));
});

test("a refresh with the same layout updates the drawing in place; a new layout rebuilds it", async () => {
  const second = chartDocument({candles: chartDocument().candles.slice(1)});
  const third = chartDocument({indicators: chartDocument().indicators.slice(0, 1)});
  const page = await start({answers: [chartDocument(), second, third]});
  await page.advance(60000);
  assert.equal(page.log.charts.length, 1);
  const candles = page.log.charts[0].series.find(series => series.type === "Candlestick");
  assert.deepEqual(clone(candles.data[0]), {time: FIRST});
  assert.equal(candles.removedLines, 4, "old lines are removed before the new ones are drawn");
  await page.advance(60000);
  assert.equal(page.log.charts.length, 2);
  assert.equal(page.log.charts[0].removed, true);
});

test("coin and candle size controls ask for exactly that chart", async () => {
  const page = await start();
  const buttons = page.$$("[data-hq-chart-controls] button");
  assert.deepEqual(buttons.map(node => page.text(node)), ["1 min", "5 min", "15 min", "1 hour"]);
  assert.equal(buttons[2].getAttribute("aria-pressed"), "true");
  buttons[3].click();
  await page.flush();
  assert.equal(chartCalls(page).at(-1).url, "/api/v1/hq/chart?symbol=SOL%2FUSD&interval=60");
  const select = page.$("[data-hq-chart-controls] select");
  assert.deepEqual(select.querySelectorAll("option").map(node => page.text(node)), ["SOL/USD · day plan, held", "BTC/USD · market reference"]);
  select.value = "BTC/USD";
  select.dispatchEvent(new page.dom.Event("change", {bubbles: true}));
  await page.flush();
  assert.equal(chartCalls(page).at(-1).url, "/api/v1/hq/chart?symbol=BTC%2FUSD&interval=15");
});

test("no price history: the reason is shown and the library is never loaded", async () => {
  const page = await start({document: chartDocument({status: "no_price_history", status_text: "Price history isn't connected to HQ yet.",
    candles: [], missing: [], indicators: [], window: null, newest_close: null, price_precision: null, markers: []})});
  assert.equal(page.scripts.length, 0);
  assert.equal(page.log.charts.length, 0);
  assert.match(page.text(page.$("[data-hq-chart-status]")), /Price history isn't connected to HQ yet/);
  assert.match(page.text(page.$("[data-hq-chart-legend]")), /Resting buy of 5 SOL at 99\.5/, "the saved lines are still listed");
});

test("a coin that left the list falls back to the first listed coin", async () => {
  const page = await start({answers: [chartDocument({status: "coin_not_listed", symbol: null, interval_minutes: null, candles: [],
    missing: [], indicators: [], lines: [], markers: [], window: null, newest_close: null, price_precision: null}), chartDocument()]});
  await page.flush();
  assert.equal(chartCalls(page).at(-1).url, "/api/v1/hq/chart?symbol=SOL%2FUSD");
  assert.equal(page.log.charts.length, 1);
});

test("without the attribution line beside it no chart is drawn", async () => {
  const page = await start({attribution: false});
  assert.equal(page.log.charts.length, 0);
  assert.match(page.text(page.$("[data-hq-chart-status]")), /can't be shown/);
});

test("a failed read or library keeps the last drawing and says so", async () => {
  const failing = await start({fail: true});
  assert.match(failing.text(failing.$("[data-hq-chart-status]")), /couldn't be loaded/);
  assert.equal(failing.log.charts.length, 0);
  const broken = await start({failLibrary: true});
  assert.match(broken.text(broken.$("[data-hq-chart-status]")), /couldn't be drawn/);
});

test("a theme change redraws the chart in the new colours", async () => {
  const page = await start();
  let observer = null;
  page.sandbox.MutationObserver = class { constructor(callback) { observer = callback; } observe() {} };
  page.use("charts").watchTheme();
  page.sandbox.getComputedStyle = () => ({getPropertyValue: name => `dark(${name})`});
  observer([{attributeName: "data-theme"}]);
  await page.flush();
  assert.equal(page.log.charts.length, 2);
  assert.equal(page.log.charts[0].removed, true);
  assert.equal(page.log.charts[1].options.layout.textColor, "dark(--text)");
});

test("nothing is read while the Money tab is hidden on a phone", async () => {
  const {library} = recordingLibrary();
  const page = loadHQ({scripts: CHART_SCRIPTS, fetch: async () => response(200, clone(chartDocument()))});
  page.show(fixture("busy"));
  chartPanel(page);
  page.doc.querySelector("#money").classList.remove("is-active");
  page.sandbox.LightweightCharts = library;
  page.use("charts").init();
  await page.flush();
  assert.equal(chartCalls(page).length, 0);
  page.doc.querySelector("#money").classList.add("is-active");
  page.use("charts").wake();
  await page.flush();
  assert.equal(chartCalls(page).length, 1);
});
