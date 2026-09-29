// UNS inspector front end. Read-only: it only GETs the inspector's view of what arrived over MQTT.
"use strict";

const $ = (id) => document.getElementById(id);
const state = { selected: null, treeSig: "", tree: [], open: new Set(), dismissedScope: null, filter: "" };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function hms(t) {
  if (t === null || t === undefined) return "–";
  const s = Math.floor(t), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
function num(v) {
  if (typeof v !== "number") return esc(v);
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  return a !== 0 && (a < 0.01 || a >= 1e6) ? v.toExponential(3) : v.toFixed(a < 10 ? 3 : 2);
}
function val(v) {
  if (v === null || v === undefined) return '<span class="muted">null</span>';
  if (typeof v === "object") return `<span class="mono">${esc(JSON.stringify(v))}</span>`;
  return typeof v === "number" ? num(v) : esc(v);
}
async function get(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
// "Equipment Module (lower-level equipment, ISA-88)" -> "Equipment Module"
function short(concept) { return String(concept || "").replace(/\s*\(.*$/, ""); }
function pill(el, text, cls) { el.textContent = text; el.className = "pill " + (cls || ""); }

// ------------------------------------------------------------------ status bar
async function refreshStatus() {
  let s;
  try { s = await get("/api/status"); }
  catch (e) { pill($("p-mqtt"), "Inspector: unreachable", "bad"); return; }
  const m = s.mqtt;
  pill($("p-mqtt"), `MQTT: ${m.connected ? "CONNECTED" : "DISCONNECTED"} · ${m.host}:${m.port}`, m.connected ? "ok" : "bad");
  pill($("p-pub"), `Publisher: ${s.publisher || "unknown"}`, s.publisher === "online" ? "ok" : s.publisher ? "warn" : "");
  const life = s.lifecycle || {};
  const lc = { RUNNING: "ok", COMPLETED: "ok", PAUSED: "warn", READY: "warn" }[life.status] || "";
  pill($("p-life"), `Lifecycle: ${life.status || "–"}`, lc);
  $("p-life").title = `retained lifecycle: ${life.status || "–"} since simulation_time ${life.simulation_time ?? "–"} s`;
  const t = s.latest_simulation_time;
  $("p-time").textContent = `t = ${hms(t)} (${t ?? "–"} s)`;
  $("p-scope").textContent = `Scope: ${s.operational_scope_id || "–"}`;
  const sim = (s.links || {}).simulator_ui;
  $("l-sim").hidden = !sim;
  if (sim) $("l-sim").href = sim;
  const ch = s.scope_change;
  if (ch && ch.current === s.operational_scope_id && state.dismissedScope !== ch.current) {
    $("scope-new").textContent = ch.current;
    $("scope-old").textContent = ch.previous;
    $("scope-banner").hidden = false;
  } else {
    $("scope-banner").hidden = true;
  }
  $("foot").textContent = `root ${s.root} · ${s.counts.topics} current topics · ${s.counts.events} recent events kept in memory ` +
    `· ${m.messages_received} MQTT messages received · connects ${m.connects}, disconnects ${m.disconnects} · ` +
    `live view only: nothing is stored`;
}

// ------------------------------------------------------------------ ISA-95 tree
async function refreshTree() {
  let nodes;
  try { nodes = await get("/api/tree"); } catch (e) { return; }
  const sig = JSON.stringify(nodes);
  if (sig === state.treeSig) return;
  state.treeSig = sig;
  state.tree = nodes;
  renderTree();
}
function renderTree() {
  const nodes = state.tree, f = state.filter.toLowerCase();
  const kids = new Map(), ids = new Set(nodes.map((n) => n.entity_id));
  for (const n of nodes) {
    const p = ids.has(n.parent) ? n.parent : null;
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(n);
  }
  const match = (n) => !f || [n.entity_id, n.name, n.isa95, n.entity_type].some((x) => String(x || "").toLowerCase().includes(f));
  const keep = new Set();
  const visit = (n) => { let k = match(n); for (const c of kids.get(n.entity_id) || []) k = visit(c) || k; if (k) keep.add(n.entity_id); return k; };
  for (const r of kids.get(null) || []) visit(r);
  const li = (n) => {
    if (!keep.has(n.entity_id)) return "";
    const c = (kids.get(n.entity_id) || []).map(li).join("");
    const native = n.entity_id.split(":").slice(1).join(":");
    return `<li><div class="node${n.entity_id === state.selected ? " sel" : ""}" data-id="${esc(n.entity_id)}" title="${esc(n.entity_id)}">` +
      `<span>${esc(native)}</span><span class="t" title="${esc(n.isa95 || "")}">${esc(short(n.isa95) || n.entity_type)}</span>` +
      (n.records ? `<span class="r" title="records (orders, lots, samples, ...) under this entity">${n.records}</span>` : "") +
      `</div>${c ? `<ul>${c}</ul>` : ""}</li>`;
  };
  $("tree").innerHTML = `<ul>${(kids.get(null) || []).map(li).join("")}</ul>`;
  $("tree-count").textContent = `(${nodes.length} entities)`;
}
$("tree").addEventListener("click", (e) => {
  const n = e.target.closest(".node");
  if (n) select(n.dataset.id);
});
$("tree-filter").addEventListener("input", (e) => { state.filter = e.target.value; renderTree(); });
$("scope-dismiss").addEventListener("click", () => { state.dismissedScope = $("scope-new").textContent; $("scope-banner").hidden = true; });

function select(id) {
  state.selected = id;
  history.replaceState(null, "", "#" + encodeURIComponent(id));
  renderTree();
  refreshEntity();
}

// ------------------------------------------------------------------ selected entity
function sparkline(series, unit) {
  const pts = series.filter((p) => typeof p[1] === "number");
  if (pts.length < 2) return `<svg class="spark"></svg>`;
  const W = 140, H = 26, pad = 3;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  let lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = (t) => pad + ((t - t0) / Math.max(t1 - t0, 1)) * (W - 2 * pad);
  const y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad);
  const d = pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const data = esc(JSON.stringify(pts));
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" data-pts="${data}" data-unit="${esc(unit)}" ` +
    `aria-label="recent samples, simulation time ${hms(t0)} to ${hms(t1)}"><polyline points="${d}"/>` +
    `<circle r="3" cx="${x(last[0]).toFixed(1)}" cy="${y(last[1]).toFixed(1)}"/></svg>`;
}
document.addEventListener("mousemove", (e) => {
  const svg = e.target.closest && e.target.closest("svg.spark[data-pts]");
  if (!svg) return;
  const pts = JSON.parse(svg.dataset.pts), r = svg.getBoundingClientRect();
  const i = Math.round(((e.clientX - r.left) / r.width) * (pts.length - 1));
  const p = pts[Math.max(0, Math.min(pts.length - 1, i))];
  const out = svg.parentElement.querySelector(".spark-read");
  if (out) out.textContent = `t=${hms(p[0])}  ${num(p[1])} ${svg.dataset.unit}`;
});

function eventRow(m, showEntity) {
  const d = m.data || {};
  const pl = d.payload && Object.keys(d.payload).length ? JSON.stringify(d.payload) : "";
  const cc = [d.causation_id && `causation ${d.causation_id}`, d.correlation_id && `correlation ${d.correlation_id}`].filter(Boolean).join(" · ");
  return `<div class="ev"><span class="id">${esc(d.event_id)}</span><span class="ty">${esc(d.event_type)}</span>` +
    `<span class="tm" title="simulation_time">t=${hms(d.simulation_time)}</span>` +
    `<span class="pl">${showEntity ? esc(d.entity_id) + " " : ""}${esc(pl)}${cc ? " · " + esc(cc) : ""}</span></div>`;
}

function rawMessage(m) {
  const key = m.topic;
  return `<details data-key="${esc(key)}"${state.open.has(key) ? " open" : ""}><summary>${esc(m.topic)}</summary>` +
    `<div class="muted">QoS ${m.qos} · retain flag ${m.retain ? "true (delivered from the broker's retained store)" : "false (live delivery)"} · received ${esc(m.received_at)}</div>` +
    `<pre class="raw">${esc(m.payload)}</pre></details>`;
}

async function refreshEntity() {
  const id = state.selected;
  if (!id) return;
  let e;
  try { e = await get("/api/entity/" + encodeURIComponent(id)); }
  catch (err) { $("detail").innerHTML = `<p class="muted">${esc(id)} is not (or no longer) in the UNS.</p>`; return; }
  if (id !== state.selected) return;
  document.querySelectorAll("#detail details[data-key]").forEach((d) => d.open ? state.open.add(d.dataset.key) : state.open.delete(d.dataset.key));

  const isa = e.isa95 || {};
  const lineage = e.lineage.map((l) => `<span class="step" data-id="${esc(l.entity_id)}"><small>${esc(l.isa95 || l.entity_type)}</small>${esc(l.entity_id.split(":").slice(1).join(":"))}</span>`)
    .join('<span class="arrow">›</span>');

  const st = e.state && e.state.data;
  let stateHtml = '<p class="muted">This entity publishes no state topic (its values are measurements or metadata).</p>';
  if (st) {
    const units = st.units || {};
    const rows = Object.entries(st.state || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${val(v)} ${esc(units[k] || "")}</td></tr>`).join("");
    stateHtml = `<table class="kv">${rows}</table><div class="muted">simulation_time ${st.simulation_time} (${hms(st.simulation_time)}) · ` +
      `scope <span class="mono">${esc(st.operational_scope_id)}</span> · observed_at <span class="mono">${esc(st.observed_at)}</span> (transport time)</div>`;
  }

  const meas = e.measurements.map((m) => `<tr><td class="mono">${esc(m.variable)}</td><td class="num">${val(m.value)}</td><td>${esc(m.unit)}</td>` +
    `<td class="num">${m.simulation_time} <span class="muted">(${hms(m.simulation_time)})</span></td>` +
    `<td>${sparkline(m.series, m.unit)}<div class="spark-read"></div></td><td class="obs">${esc(m.observed_at)}</td></tr>`).join("");

  const records = e.records.map((r) => {
    const s = r.state || {};
    const summary = ["status", "state", "disposition", "quality_status", "priority"].filter((k) => k in s).map((k) => `${k}=${s[k]}`).join(" ");
    return `<tr><td class="mono">${esc(r.entity_id)}</td><td>${esc(summary)}</td><td class="num">${r.simulation_time ?? "–"}</td></tr>`;
  }).join("");

  const cfg = e.meta && e.meta.configuration && Object.keys(e.meta.configuration).length
    ? `<table class="kv">${Object.entries(e.meta.configuration).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${val(v)}</td></tr>`).join("")}</table>` : "";

  $("detail").innerHTML = `
    <div class="card"><h3><span class="mono">${esc(e.entity_id)}</span><span class="tag">${esc(isa.concept || e.entity_type)}</span>
      <span class="muted">${esc(e.name || "")}</span></h3>
      <table class="kv">
        <tr><th>entity_type</th><td class="mono">${esc(e.entity_type)}</td></tr>
        <tr><th>ISA-95 concept</th><td>${esc(isa.concept || "–")} <span class="muted">${esc(isa.mapping || "")} ${esc(isa.mapping_id || "")}</span></td></tr>
        <tr><th>parent</th><td class="mono">${esc(e.parent || "–")}</td></tr>
        <tr><th>children</th><td class="mono">${e.children.map(esc).join(", ") || "–"}</td></tr>
      </table>
      <div class="insp-h">ISA-95 position</div><div class="lineage">${lineage}</div></div>
    <div class="card"><h3>State <span class="tag retained">CURRENT RETAINED STATE</span><span class="muted">latest value of the retained state topic, not history</span></h3>${stateHtml}</div>
    <div class="card"><h3>Measurements <span class="tag retained">LIVE</span><span class="muted">sparkline = samples received since this inspector connected (current scope)</span></h3>
      ${meas ? `<table class="kv"><tr><th>variable</th><th>value</th><th>unit</th><th>simulation_time</th><th>recent</th><th title="wall-clock publication time: transport metadata, not manufacturing time">observed_at (transport)</th></tr>${meas}</table>` : '<p class="muted">No measurement topics.</p>'}</div>
    <div class="card"><h3>Recent operational events <span class="muted">this entity and below · current scope · received live (events are not retained)</span></h3>
      ${e.events.length ? e.events.map((m) => eventRow(m, true)).join("") : '<p class="muted">No events received for this part of the tree since the inspector connected.</p>'}</div>
    ${records ? `<div class="card"><h3>Records under this entity <span class="tag retained">CURRENT RETAINED STATE</span></h3><table class="kv"><tr><th>record</th><th>state</th><th>simulation_time</th></tr>${records}</table></div>` : ""}
    ${cfg ? `<div class="card"><h3>Configuration <span class="muted">from the retained meta topic</span></h3>${cfg}</div>` : ""}
    <div class="card topics"><h3>MQTT topics <span class="muted">actual topics received for this entity · expand for the raw MQTT message</span></h3>
      <div class="muted mono">${esc(e.topic_prefix)}…</div>${e.topics.map(rawMessage).join("")}</div>`;
}
$("detail").addEventListener("click", (e) => {
  const s = e.target.closest(".lineage .step");
  if (s) select(s.dataset.id);
});
$("detail").addEventListener("toggle", (e) => {
  const d = e.target;
  if (d.dataset && d.dataset.key) (d.open ? state.open.add(d.dataset.key) : state.open.delete(d.dataset.key));
}, true);

async function refreshEvents() {
  try {
    const ev = await get("/api/events?limit=40");
    $("all-events").innerHTML = ev.length ? ev.map((m) => eventRow(m, true)).join("") : '<p class="muted">No events received yet.</p>';
  } catch (e) { /* the status bar reports the problem */ }
}

// ------------------------------------------------------------------ polling
async function tick() {
  await refreshStatus();
  await refreshEvents();
  if (state.selected) await refreshEntity();
}
async function slowTick() { await refreshTree(); }

(async function init() {
  await refreshTree();
  const fromHash = decodeURIComponent(location.hash.slice(1));
  select(fromHash || "equipment_module:EM-REACTOR");
  await tick();
  setInterval(tick, 1000);
  setInterval(slowTick, 3000);
})();
