"use strict";
// Every string from a feed is untrusted: text goes in through textContent only.

const $ = (sel) => document.querySelector(sel);
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}
const putJson = (path, body) =>
  api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function fmtPrice(p) {
  if (p === null || p === undefined || !isFinite(p)) return "—";
  const a = Math.abs(p);
  const digits = a >= 1000 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : 8;
  return p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtUsd(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
  return "$" + v.toFixed(0);
}
function fmtAge(seconds) {
  if (seconds < 90) return Math.max(0, Math.round(seconds)) + "s";
  if (seconds < 5400) return Math.round(seconds / 60) + "m";
  if (seconds < 172800) return Math.round(seconds / 3600) + "h";
  return Math.round(seconds / 86400) + "d";
}
function fmtTime(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? hm : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " + hm;
}
const nowS = () => Date.now() / 1000;
const dirClass = (x) => (x > 0 ? "up" : x < 0 ? "down" : "dim");

// ---------------------------------------------------------------- state
const state = {
  watchlist: [],            // [{id, asset}]
  strip: [],
  quotes: {},               // id -> {price, ts, prev}
  symbol: null, asset: null, interval: "1d",
  candles: [],
  feeds: {},                // feed -> {state, detail, ts}
};

// ---------------------------------------------------------------- quotes
function quote(id) { return state.quotes[id] || (state.quotes[id] = { price: null, ts: null, prev: null }); }

function paintQuote(id, flash) {
  const q = quote(id);
  const chg = q.price !== null && q.prev ? q.price / q.prev - 1 : null;
  const chgText = chg === null ? "" : (chg >= 0 ? "+" : "") + (chg * 100).toFixed(2) + "%";
  document.querySelectorAll(`[data-id="${CSS.escape(id)}"]`).forEach((row) => {
    const px = row.querySelector(".px"), ch = row.querySelector(".chg"), age = row.querySelector(".age");
    px.textContent = fmtPrice(q.price);
    ch.textContent = chgText;
    ch.className = "chg " + dirClass(chg || 0);
    if (age) age.textContent = q.ts && nowS() - q.ts > 120 ? fmtAge(nowS() - q.ts) : "";
    if (flash) {
      row.classList.remove("flash-up", "flash-down");
      void row.offsetWidth;
      row.classList.add(flash > 0 ? "flash-up" : "flash-down");
    }
  });
  if (id === state.symbol) $("#chart-last").textContent = fmtPrice(q.price) + (chgText ? "  " + chgText : "");
}

function onTick(t) {
  const q = quote(t.symbol);
  if (q.ts && t.ts < q.ts) return;   // an older source must not overwrite a newer price
  const flash = q.price === null ? 0 : Math.sign(t.price - q.price);
  q.price = t.price; q.ts = t.ts;
  paintQuote(t.symbol, flash);
  if (t.symbol === state.symbol) chartTick(t);
}

async function loadPrev(id) {
  try {
    const data = await api(`/api/candles?symbol=${encodeURIComponent(id)}&interval=1d&limit=2`);
    const c = data.candles, q = quote(id);
    if (c.length > 1) q.prev = c[c.length - 2].close;
    if (q.price === null && c.length) { q.price = c[c.length - 1].close; q.ts = c[c.length - 1].time; }
    paintQuote(id, 0);
  } catch (e) { /* no history for this symbol: the row stays blank */ }
}

function quoteRow(item, cls) {
  const row = el("div", null, cls);
  row.dataset.id = item.id;
  row.append(el("span", item.id.replace("-USD", ""), "sym"), el("span", "—", "px"), el("span", "", "chg"));
  row.addEventListener("click", () => loadSymbol(item.id));
  return row;
}

async function loadWatchlist() {
  state.watchlist = await api("/api/watchlist");
  const box = $("#watchlist");
  box.replaceChildren(...state.watchlist.map((w) => {
    const row = quoteRow(w, "row");
    row.append(el("span", "", "age"));
    return row;
  }));
  state.watchlist.forEach((w) => { paintQuote(w.id, 0); if (quote(w.id).prev === null) loadPrev(w.id); });
  markSelected();
}

async function loadStrip() {
  state.strip = await api("/api/strip");
  const names = { "^GSPC": "S&P", "^IXIC": "NASDAQ", "^VIX": "VIX", "DX-Y.NYB": "DXY", "^TNX": "US10Y", "GC=F": "GOLD", "CL=F": "OIL", "BTC-USD": "BTC" };
  $("#strip").replaceChildren(...state.strip.map((s) => {
    const q = el("span", null, "q");
    q.dataset.id = s.id;
    q.append(el("b", names[s.id] || s.id), el("span", "—", "px"), document.createTextNode(" "), el("span", "", "chg"));
    q.addEventListener("click", () => loadSymbol(s.id));
    return q;
  }));
  state.strip.forEach((s) => loadPrev(s.id));
}

function markSelected() {
  document.querySelectorAll("#watchlist .row").forEach((r) => r.classList.toggle("sel", r.dataset.id === state.symbol));
}

// ---------------------------------------------------------------- chart
const INTERVALS = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400, "1wk": 604800, "1mo": 2592000 };
const TZ = -new Date().getTimezoneOffset() * 60;   // show local time on a UTC-only chart
const chartOpts = {
  autoSize: true,
  layout: { background: { color: "#070707" }, textColor: "#ffb000", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11 },
  grid: { vertLines: { color: "#151515" }, horzLines: { color: "#151515" } },
  rightPriceScale: { borderColor: "#2a2a2a", minimumWidth: 64 },
  timeScale: { borderColor: "#2a2a2a", timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 },
};
let chart, rsiChart, sCandle, sVol, sSma20, sSma50, sRsi;

function initChart() {
  chart = LightweightCharts.createChart($("#chart"), chartOpts);
  sCandle = chart.addCandlestickSeries({ upColor: "#00d26a", downColor: "#ff3b30", borderVisible: false, wickUpColor: "#00d26a", wickDownColor: "#ff3b30" });
  sVol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  sSma20 = chart.addLineSeries({ color: "#4aa3ff", lineWidth: 1, lastValueVisible: false, priceLineVisible: false, title: "SMA20" });
  sSma50 = chart.addLineSeries({ color: "#c77dff", lineWidth: 1, lastValueVisible: false, priceLineVisible: false, title: "SMA50" });
  rsiChart = LightweightCharts.createChart($("#rsi"), { ...chartOpts, timeScale: { visible: false } });
  sRsi = rsiChart.addLineSeries({ color: "#ffb000", lineWidth: 1, title: "RSI14", priceLineVisible: false });
  [30, 70].forEach((v) => sRsi.createPriceLine({ price: v, color: "#555", lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
  let syncing = false;
  const link = (a, b) => a.timeScale().subscribeVisibleLogicalRangeChange((r) => {
    if (syncing || !r) return;
    syncing = true; b.timeScale().setVisibleLogicalRange(r); syncing = false;
  });
  link(chart, rsiChart); link(rsiChart, chart);
  $("#intervals").replaceChildren(...Object.keys(INTERVALS).map((iv) => {
    const b = el("button", iv);
    b.dataset.iv = iv;
    b.addEventListener("click", () => { state.interval = iv; if (state.symbol) loadSymbol(state.symbol); });
    return b;
  }));
}

function sma(c, n) {
  const out = []; let sum = 0;
  for (let i = 0; i < c.length; i++) {
    sum += c[i].close; if (i >= n) sum -= c[i - n].close;
    if (i >= n - 1) out.push({ time: c[i].time, value: sum / n });
  }
  return out;
}
function rsi(c, n) {
  const out = []; let gain = 0, loss = 0;
  for (let i = 1; i < c.length; i++) {
    const d = c[i].close - c[i - 1].close, g = Math.max(d, 0), l = Math.max(-d, 0);
    if (i <= n) { gain += g / n; loss += l / n; } else { gain = (gain * (n - 1) + g) / n; loss = (loss * (n - 1) + l) / n; }
    if (i >= n) out.push({ time: c[i].time, value: loss === 0 ? 100 : 100 - 100 / (1 + gain / loss) });
  }
  return out;
}
const volBar = (c) => ({ time: c.time, value: c.volume, color: c.close >= c.open ? "#0a5c34" : "#6e1b16" });

function drawChart() {
  const c = state.candles;
  sCandle.setData(c); sVol.setData(c.map(volBar));
  sSma20.setData(sma(c, 20)); sSma50.setData(sma(c, 50)); sRsi.setData(rsi(c, 14));
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, c.length - 160), to: c.length + 4 });
}

let loadSeq = 0;
async function loadSymbol(text) {
  const seq = ++loadSeq;
  $("#cmd-msg").textContent = "";
  let data;
  try {
    data = await api(`/api/candles?symbol=${encodeURIComponent(text)}&interval=${state.interval}`);
  } catch (e) {
    $("#cmd-msg").textContent = e.message === "symbol not found" ? "NOT FOUND: " + text : "ERROR: " + e.message;
    return false;
  }
  if (seq !== loadSeq) return true;   // a newer request won
  state.symbol = data.symbol; state.asset = data.asset;
  const intraday = INTERVALS[state.interval] < 86400;
  state.candles = data.candles.map((c) => ({ ...c, time: intraday ? c.time + TZ : c.time }));
  $("#chart-title").textContent = data.symbol + "  " + state.interval.toUpperCase();
  document.querySelectorAll("#intervals button").forEach((b) => b.classList.toggle("on", b.dataset.iv === state.interval));
  drawChart(); markSelected();
  if (quote(data.symbol).prev === null) loadPrev(data.symbol); else paintQuote(data.symbol, 0);
  return true;
}

function chartTick(t) {
  const c = state.candles; if (!c.length) return;
  const step = INTERVALS[state.interval], last = c[c.length - 1];
  const time = step < 86400 ? Math.floor(t.ts / step) * step + TZ : last.time;
  let bar;
  if (time > last.time) { bar = { time, open: t.price, high: t.price, low: t.price, close: t.price, volume: 0 }; c.push(bar); }
  else if (time === last.time || step >= 86400) { bar = last; bar.close = t.price; bar.high = Math.max(bar.high, t.price); bar.low = Math.min(bar.low, t.price); }
  else return;
  sCandle.update(bar);
}

// ---------------------------------------------------------------- command bar
async function runCommand(raw) {
  const text = raw.trim();
  if (!text) return;
  const ids = state.watchlist.map((w) => w.id);
  try {
    if (text.startsWith("+")) { await putJson("/api/watchlist", [...ids, text.slice(1)]); await loadWatchlist(); }
    else if (text.startsWith("-")) {
      const gone = text.slice(1).toUpperCase();
      await putJson("/api/watchlist", ids.filter((id) => id !== gone && id.replace("-USD", "") !== gone));
      await loadWatchlist();
    } else if (!(await loadSymbol(text))) return;
    $("#cmd").value = "";
  } catch (e) { $("#cmd-msg").textContent = "NOT FOUND: " + text.replace(/^[+-]/, ""); }
}
$("#cmd").addEventListener("keydown", (e) => {
  if (e.key === "Enter") runCommand(e.target.value);
  if (e.key === "Escape") { e.target.value = ""; $("#cmd-msg").textContent = ""; }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("#cmd") && !$("#settings").open) { e.preventDefault(); $("#cmd").focus(); }
});

// ---------------------------------------------------------------- news
const news = { items: new Map(), minWeight: 1, watchOnly: false };
const arrow = (d) => (d > 0 ? "▲" : d < 0 ? "▼" : "•");

function chip(symbol, direction) {
  const c = el("span", `${symbol.replace("-USD", "")} ${arrow(direction)}`, "chip " + dirClass(direction));
  c.dataset.symbol = symbol;
  if (state.watchlist.some((w) => w.id === symbol)) c.classList.add("wl");
  c.addEventListener("click", (e) => { e.stopPropagation(); loadSymbol(symbol); });
  return c;
}

function newsRow(n) {
  const a = n.analysis, row = el("div", null, `news w${a.weight}`);
  row.dataset.id = n.id; row.dataset.ts = n.ts;
  const head = el("div", null, "head");
  const more = n.outlets.length > 1 ? ` +${n.outlets.length - 1}` : "";
  const src = el("span", n.source + more, "src");
  src.title = n.outlets.join(", ");
  head.append(el("span", fmtTime(n.ts)), src, el("span", "▮".repeat(a.weight) + "▯".repeat(5 - a.weight), "weight"));
  const title = el("div", null, "title");
  title.append(el("span", arrow(a.sentiment), "mood " + dirClass(a.sentiment)), document.createTextNode(n.title));
  const link = el("a", "↗");
  if (/^https?:\/\//.test(n.url)) { link.href = n.url; link.target = "_blank"; link.rel = "noopener noreferrer"; }
  link.addEventListener("click", (e) => e.stopPropagation());
  title.append(link);
  const chips = el("div", null, "chips");
  chips.append(...a.impacts.slice(0, 12).map((i) => chip(i.symbol, i.direction)));
  const why = el("ul", null, "why"); why.hidden = true;
  why.append(...a.reasons.map((r) => el("li", r)));
  if (n.summary) why.prepend(el("li", n.summary));
  title.addEventListener("click", () => { why.hidden = !why.hidden; });
  row.append(head, title, chips, why);
  return row;
}

function newsVisible(n) {
  if (n.analysis.weight < news.minWeight) return false;
  return !news.watchOnly || n.analysis.impacts.some((i) => state.watchlist.some((w) => w.id === i.symbol));
}

function onNews(n) {
  news.items.set(n.id, n);
  const box = $("#news"), old = box.querySelector(`[data-id="${CSS.escape(n.id)}"]`);
  if (!newsVisible(n)) { if (old) old.remove(); return; }
  const row = newsRow(n);
  if (old) { old.replaceWith(row); return; }
  const after = [...box.children].find((r) => Number(r.dataset.ts) <= n.ts);   // newest first
  box.insertBefore(row, after || null);
  while (box.children.length > 300) box.lastChild.remove();
}

function repaintNews() {
  const sorted = [...news.items.values()].sort((a, b) => b.ts - a.ts).filter(newsVisible).slice(0, 300);
  $("#news").replaceChildren(...sorted.map(newsRow));
}
$("#news-weight").addEventListener("click", (e) => {
  news.minWeight = (news.minWeight % 4) + 1;
  e.target.textContent = `W${news.minWeight}+`; e.target.classList.toggle("on", news.minWeight > 1); repaintNews();
});
$("#news-watch").addEventListener("click", (e) => { news.watchOnly = !news.watchOnly; e.target.classList.toggle("on", news.watchOnly); repaintNews(); });

// ---------------------------------------------------------------- feed status
const PANEL_FEEDS = {
  "tag-prices": ["crypto", "stocks", "quotes"],
  "tag-news": ["rss", "finnhub-news", "sec-8k"],
  "tag-bottom": ["crypto", "btc-onchain", "eth-onchain", "sec-filings", "volume", "gdelt"],
};
function paintStatus() {
  $("#feeds").replaceChildren(...Object.entries(state.feeds).sort().map(([name, s]) => {
    const chip = el("span", name, "s-" + s.state);
    chip.title = s.state + (s.detail ? ": " + s.detail : "");
    return chip;
  }));
  for (const [tagId, feeds] of Object.entries(PANEL_FEEDS)) {
    const known = feeds.filter((f) => state.feeds[f]).map((f) => ({ name: f, ...state.feeds[f] }));
    const tag = $("#" + tagId), down = known.filter((f) => f.state === "down");
    if (down.length) { tag.textContent = `STALE ${down.map((f) => f.name).join(",")} ${fmtAge(nowS() - Math.min(...down.map((f) => f.ts)))}`; tag.className = "tag stale"; }
    else if (known.some((f) => f.state === "ok")) { tag.textContent = "LIVE"; tag.className = "tag live"; }
    else if (known.some((f) => f.state === "closed")) { tag.textContent = "CLOSED"; tag.className = "tag closed"; }
    else { tag.textContent = known.length ? "OFF" : "NO FEED"; tag.className = "tag off"; }
  }
}

// ---------------------------------------------------------------- tabs
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  document.querySelectorAll("#bottom .tab").forEach((t) => { t.hidden = t.id !== name; });
  $("#whale-filters").hidden = name !== "whales";
}

// ---------------------------------------------------------------- live socket
const handlers = { tick: onTick, news: onNews, status: (s) => { state.feeds[s.feed] = s; paintStatus(); } };
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (m) => { const ev = JSON.parse(m.data); const h = handlers[ev.kind]; if (h) h(ev); };
  ws.onclose = () => {
    state.feeds.server = { state: "down", detail: "terminal process not reachable", ts: nowS() };
    paintStatus(); setTimeout(connect, 2000);
  };
  ws.onopen = async () => {
    delete state.feeds.server;
    Object.assign(state.feeds, await api("/api/status")); paintStatus();
    const prices = await api("/api/prices");
    for (const [id, p] of Object.entries(prices)) onTick({ symbol: id, price: p.price, ts: p.ts });
  };
}

async function start() {
  initChart();
  await Promise.all([loadWatchlist(), loadStrip()]);
  (await api("/api/events?kind=news&limit=300")).forEach((n) => news.items.set(n.id, n));
  repaintNews();
  connect();
  setInterval(() => { paintStatus(); state.watchlist.forEach((w) => paintQuote(w.id, 0)); }, 5000);
  if (state.watchlist.length) loadSymbol(state.watchlist[0].id);
}
start();
