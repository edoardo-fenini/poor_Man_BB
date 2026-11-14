"use strict";
// MAP: news, conflict zones, ships, planes and earthquakes on one world map.
// Popups are built as DOM nodes, never HTML strings: every label here comes from a feed.

const mapState = { map: null, layers: {}, timers: [], on: new Set(["news", "zones", "ships", "military", "quakes"]) };
const SHIP_COLOURS = { tanker: "#ff8c00", cargo: "#00d26a", passenger: "#e8e8e8", other: "#777" };

function popup(title, lines) {
  const box = el("div");
  box.append(el("b", title));
  lines.forEach((line) => box.append(line instanceof Node ? line : el("div", line)));
  return box;
}
function storyLine(s) {
  const row = el("div", null, "story");
  row.append(el("span", arrow(s.sentiment) + " ", dirClass(s.sentiment)), document.createTextNode(`${fmtTime(s.ts)} ${s.source}: ${s.title} `));
  if (/^https?:\/\//.test(s.url)) { const a = el("a", "↗"); a.href = s.url; a.target = "_blank"; a.rel = "noopener noreferrer"; row.append(a); }
  return row;
}
const dot = (lat, lon, colour, radius, fill = 0.7) =>
  L.circleMarker([lat, lon], { radius, color: colour, weight: 1, fillColor: colour, fillOpacity: fill });

// each layer: how often to reload, and how to turn the server's answer into markers
const MAP_LAYERS = {
  news: { label: "NEWS", every: 60, async load() {
    return (await api("/api/map/news")).map((p) => {
      const heavy = Math.max(...p.stories.map((s) => s.weight));
      const marker = dot(p.lat, p.lon, "#ffb000", 4 + Math.min(p.stories.length, 10), 0.45);
      if (heavy >= 4) marker.setStyle({ color: "#fff", weight: 2 });
      return marker
        .bindTooltip(`${p.place}: ${p.stories.length} stor${p.stories.length === 1 ? "y" : "ies"}`)
        .bindPopup(() => popup(p.place, p.stories.map(storyLine)), { maxWidth: 420 });
    });
  } },
  zones: { label: "CONFLICTS", every: 60, async load() {
    return (await api("/api/map/zones")).map((z) => {
      const hot = z.ratio >= 2;
      return L.circle([z.lat, z.lon], { radius: 250000 + Math.min(z.count, 40) * 12000, color: hot ? "#ff3b30" : "#8a2a2a", weight: hot ? 2 : 1, fillOpacity: hot ? 0.25 : 0.1, dashArray: "4 4" })
        .bindTooltip(`${z.zone}: ${z.count} today` + (z.ratio ? `, ${z.ratio.toFixed(1)}× usual` : ""))
        .bindPopup(() => {
          const chips = el("div", null, "chips");
          chips.append(...z.assets.map((a) => chip(a.symbol, a.direction)));
          return popup(z.zone, [`${z.count} conflict headlines today` + (z.ratio ? `, ${z.ratio.toFixed(1)}× the usual` : ""), chips]);
        });
    });
  } },
  ships: { label: "SHIPS", every: 20, async load() {
    const data = await api("/api/map/ships");
    paintTraffic(data.traffic);
    return data.ships.map((s) => dot(s.lat, s.lon, SHIP_COLOURS[s.cls] || SHIP_COLOURS.other, 3)
      .bindTooltip(`${s.name || "ship " + s.id} · ${s.cls} · ${(s.speed || 0).toFixed(1)} kn` + (s.dest ? ` → ${s.dest}` : "")));
  } },
  planes: { label: "PLANES", every: 15, async load() {
    const m = mapState.map;
    if (m.getZoom() < 5) { $("#map-msg").textContent = "Zoom in to load planes"; return []; }
    $("#map-msg").textContent = "";
    const c = m.getCenter(), radius = Math.min(250, Math.round(m.distance(c, m.getBounds().getNorthEast()) / 1852));
    const lon = ((c.lng + 540) % 360) - 180;
    return (await api(`/api/map/planes?lat=${c.lat.toFixed(2)}&lon=${lon.toFixed(2)}&radius=${radius}`)).filter((p) => !p.mil).map(planeDot);
  } },
  military: { label: "MILITARY", every: 30, async load() { return (await api("/api/map/military")).map(planeDot); } },
  quakes: { label: "QUAKES", every: 300, async load() {
    return (await api("/api/map/quakes")).map((q) => dot(q.lat, q.lon, "#c77dff", 2 + (q.mag - 4) * 4, 0.3)
      .bindTooltip(`M${q.mag.toFixed(1)} ${q.place} · ${fmtAge(nowS() - q.ts)} ago`));
  } },
};
function planeDot(p) {
  return dot(p.lat, p.lon, p.mil ? "#ff3b30" : "#4aa3ff", p.mil ? 4 : 2.5)
    .bindTooltip(`${p.flight || p.reg || p.id} · ${p.type}${p.mil ? " · MILITARY" : ""} · ${Math.round(p.alt)} ft · ${Math.round(p.speed)} kn · hdg ${Math.round(p.track)}°`);
}

function paintTraffic(traffic) {
  const side = $("#map-side"), feed = state.feeds.ships;
  const nodes = [el("h3", "CHOKEPOINT TRAFFIC")];
  if (!traffic.length || (feed && feed.state !== "ok")) nodes.push(el("p", feed && feed.detail ? feed.detail : "No ship feed yet."));
  if (traffic.length) {
    const head = el("div", null, "row head");
    head.append(el("span", "Sea lane"), el("span", "Ships"), el("span", "Tank"), el("span", "Stop"));
    nodes.push(head, ...traffic.map((t) => {
      const row = el("div", null, "row");
      row.title = `${t.ships} ships, ${t.tankers} tankers, ${t.cargo} cargo, ${t.stopped} under 1 knot`;
      row.append(el("span", t.name, "white"), el("span", t.ships), el("span", t.tankers), el("span", t.stopped, t.stopped > t.ships / 2 && t.ships > 5 ? "down" : ""));
      row.addEventListener("click", () => mapState.map.flyTo([t.lat, t.lon], 8));
      return row;
    }));
  }
  nodes.push(el("h3", "LEGEND"),
    el("p", "Amber circle: news by place, white rim for a heavy story. Dashed red: conflict zone, bright when hotter than usual."),
    el("p", "Ships: orange tanker, green cargo, white passenger. Ships load only inside the listed sea lanes."),
    el("p", "Planes: blue civil (zoom in), red military (worldwide). Purple: earthquake, magnitude 4.5+."),
    el("p", "Click a marker for details. Click a sea lane to fly there."));
  side.replaceChildren(...nodes);
}

async function refreshLayer(name) {
  if (!mapState.on.has(name) || $("#map").hidden) return;
  let markers;
  try { markers = await MAP_LAYERS[name].load(); } catch (e) { $("#map-msg").textContent = `${MAP_LAYERS[name].label}: ${e.message}`; return; }
  mapState.layers[name].clearLayers();
  if (mapState.on.has(name)) markers.forEach((m) => m.addTo(mapState.layers[name]));
}

function initMap() {
  const m = mapState.map = L.map("map-canvas", { preferCanvas: true, worldCopyJump: true, minZoom: 2 }).setView([25, 30], 3);
  // OpenStreetMap's own tiles need no key; style.css turns them dark
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 12, attribution: "© OpenStreetMap contributors" }).addTo(m);
  for (const name of Object.keys(MAP_LAYERS)) mapState.layers[name] = L.layerGroup().addTo(m);
  $("#map-layers").replaceChildren(...Object.entries(MAP_LAYERS).map(([name, layer]) => {
    const b = el("button", layer.label, mapState.on.has(name) ? "on" : "");
    b.dataset.layer = name;
    b.addEventListener("click", () => {
      mapState.on.has(name) ? mapState.on.delete(name) : mapState.on.add(name);
      b.classList.toggle("on", mapState.on.has(name));
      if (mapState.on.has(name)) refreshLayer(name); else { mapState.layers[name].clearLayers(); if (name === "planes") $("#map-msg").textContent = ""; }
    });
    return b;
  }));
  let moved;
  m.on("moveend", () => { clearTimeout(moved); moved = setTimeout(() => refreshLayer("planes"), 400); });
}

function openMap() {
  $("#fn").hidden = true; $("#map").hidden = false;
  if (!mapState.map) initMap();
  mapState.map.invalidateSize();
  paintTraffic([]);
  stopMap();
  for (const [name, layer] of Object.entries(MAP_LAYERS)) {
    refreshLayer(name);
    mapState.timers.push(setInterval(() => refreshLayer(name), layer.every * 1000));
  }
}
function stopMap() { mapState.timers.forEach(clearInterval); mapState.timers = []; }
