"use strict";
// Bloomberg-style functions: the server builds each screen as plain data, this file draws it.
// Feed text is untrusted here too: textContent only.

const fn = { list: {}, seq: 0, current: null, symbol: null };
// Bloomberg's yellow keys and <GO>; typed out of habit, they carry no meaning here
const FILLER = new Set(["GO", "<GO>", "US", "EQUITY", "CURNCY", "INDEX", "COMDTY", "CORP", "GOVT"]);

api("/api/fn").then((list) => { list.forEach((f) => { fn.list[f.name] = f; }); });

function parseCommand(text) {
  const raw = text.trim().toUpperCase().split(/\s+/);
  const words = raw.filter((w, i) => i === 0 || !FILLER.has(w));
  const yellow = raw.length > 1 && FILLER.has(raw[1]) && raw[1] !== "GO" && raw[1] !== "<GO>";   // "HP US" is the stock, not the function
  if (fn.list[words[0]] && !yellow) return { name: words[0], symbol: null, arg: words[1] || "" };
  if (words.length > 1 && fn.list[words[1]]) return { name: words[1], symbol: words[0], arg: words[2] || "" };
  return { name: null, symbol: words[0], arg: "" };
}

function closeScreens() {
  $("#fn").hidden = true; $("#map").hidden = true;
  fn.current = null; fn.seq++;
  if (typeof stopMap === "function") stopMap();
}

// The command bar's entry point. Returns false when the command failed (the bar keeps its text).
async function go(text) {
  $("#cmd-msg").classList.remove("hint"); $("#cmd-msg").textContent = "";
  const cmd = parseCommand(text);
  if (!cmd.name || cmd.name === "GP") {
    const symbol = cmd.symbol || fn.symbol || state.symbol;
    if (!symbol || !(await loadSymbol(symbol))) return false;
    fn.symbol = state.symbol;
    closeScreens();
    return true;
  }
  if (cmd.name === "MAP") { closeScreens(); openMap(); return true; }
  // like Bloomberg, the last security stays loaded: `AAPL DES` then `FA` is Apple's financials
  if (cmd.symbol) fn.symbol = cmd.symbol;
  const symbol = fn.list[cmd.name].symbol || cmd.symbol ? fn.symbol || state.symbol : "";
  return showFunction(cmd.name, symbol || "", cmd.arg);
}

function drawCell(value, tag) {
  const node = el(tag);
  const c = value === null || value === undefined ? { v: "—" } : typeof value === "object" ? value : { v: String(value) };
  const text = c.v === undefined ? "—" : c.v;
  if (c.u && /^https?:\/\//.test(c.u)) {
    const a = el("a", text); a.href = c.u; a.target = "_blank"; a.rel = "noopener noreferrer";
    node.append(a);
  } else node.textContent = text;
  if (c.c) node.classList.add(c.c);
  if (c.s) { node.classList.add("link"); node.title = "Load " + c.s; node.addEventListener("click", () => go(c.s)); }
  if (c.f) { node.classList.add("link"); node.addEventListener("click", () => go(c.f)); }
  if (/^[+\-±]?[\d,.]+ ?(%|bp|[KMBT]|σ)?$|^—$/.test(text)) node.classList.add("n");
  return node;
}

const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function drawLine(data) {
  const W = 1000, H = 240, L = 50, R = 10, T = 10, B = 24;
  const colours = ["#ffb000", "#4aa3ff", "#c77dff", "#00d26a"];
  const all = data.series.flatMap((s) => s.values).filter((v) => v !== null && v !== undefined);
  const root = svg("svg", { class: "line", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
  if (!all.length || data.labels.length < 2) return root;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (i) => L + (i / (data.labels.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4;
    root.append(svg("line", { class: "axis", x1: L, x2: W - R, y1: y(v), y2: y(v) }), svg("text", { x: L - 4, y: y(v) + 3, "text-anchor": "end" }, v.toFixed(2)));
  }
  if (lo < 0 && hi > 0) root.append(svg("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: "#555" }));
  const step = Math.max(1, Math.ceil(data.labels.length / 12));
  data.labels.forEach((label, i) => { if (i % step === 0) root.append(svg("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, label)); });
  data.series.forEach((s, n) => {
    const colour = colours[n % colours.length];
    let run = [];
    const flush = () => { if (run.length > 1) root.append(svg("polyline", { points: run.join(" "), fill: "none", stroke: colour, "stroke-width": 1.5, "vector-effect": "non-scaling-stroke" })); run = []; };
    s.values.forEach((v, i) => { if (v === null || v === undefined) flush(); else run.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); });
    flush();
    root.append(svg("text", { x: L + 8 + n * 130, y: T + 10, style: `fill:${colour}` }, "— " + s.name));
  });
  return root;
}

function drawSection(section) {
  const box = el("div");
  if (section.title) box.append(el("h3", section.title));
  if (section.kv) {
    const grid = el("div", null, "kv");
    section.kv.forEach(([label, value]) => { const row = el("div"); row.append(el("span", label), drawCell(value, "span")); if (row.textContent.length > 48) row.classList.add("wide"); grid.append(row); });
    box.append(grid);
  } else if (section.columns) {
    const tbl = el("table", null, "grid"), head = el("tr");
    section.columns.forEach((c) => head.append(el("th", c)));
    tbl.append(head);
    section.rows.forEach((cells) => { const tr = el("tr"); cells.forEach((c) => tr.append(drawCell(c, "td"))); tbl.append(tr); });
    // a header sits over numbers when its first row is a number
    if (section.rows.length) [...tbl.rows[1].cells].forEach((td, i) => { if (td.classList.contains("n") && head.cells[i]) head.cells[i].classList.add("n"); });
    box.append(tbl);
  } else if (section.text) box.append(el("div", section.text, "text"));
  else if (section.line) box.append(drawLine(section.line));
  return box;
}

function drawMenu(name, symbol) {
  const forSymbol = !!fn.list[name].symbol;
  $("#fn-menu").replaceChildren(...Object.values(fn.list).filter((f) => !!f.symbol === forSymbol && f.name !== "MAP").map((f) => {
    const b = el("button", f.name, f.name === name ? "on" : "");
    b.title = f.about;
    b.addEventListener("click", () => go(forSymbol && symbol ? `${symbol} ${f.name}` : f.name));
    return b;
  }));
}

async function showFunction(name, symbol, arg) {
  const seq = ++fn.seq;
  if (typeof stopMap === "function") stopMap();
  $("#map").hidden = true; $("#fn").hidden = false;
  $("#cmd-msg").textContent = "";
  $("#fn-title").textContent = (symbol ? symbol.toUpperCase() + "  " : "") + name;
  drawMenu(name, symbol);
  $("#fn-body").replaceChildren(el("div", "LOADING " + name + " …", "loading"));
  let data;
  try {
    data = await api(`/api/fn/${name}?symbol=${encodeURIComponent(symbol)}&arg=${encodeURIComponent(arg)}`);
  } catch (e) {
    if (seq === fn.seq) $("#fn-body").replaceChildren(el("div", e.message, "msg"));
    return true;   // the screen shows the reason; the command itself was understood
  }
  if (seq !== fn.seq) return true;
  fn.current = { name, symbol, arg };
  $("#fn-title").textContent = data.title + "   <" + name + ">";
  const body = $("#fn-body");
  body.replaceChildren(...data.sections.map(drawSection));
  if (data.note) body.append(el("div", data.note, "note"));
  body.scrollTop = 0;
  return true;
}

document.querySelectorAll(".screen .close").forEach((b) => b.addEventListener("click", closeScreens));
document.querySelectorAll("#quick button").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#settings").open && (!$("#fn").hidden || !$("#map").hidden)) closeScreens();
});
// while typing, show what the function under the cursor does
$("#cmd").addEventListener("input", (e) => {
  const cmd = parseCommand(e.target.value);
  const msg = $("#cmd-msg");
  msg.textContent = cmd.name && fn.list[cmd.name] ? `${cmd.name}: ${fn.list[cmd.name].about}` : "";
  msg.classList.toggle("hint", !!msg.textContent);
});
