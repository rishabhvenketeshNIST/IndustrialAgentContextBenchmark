import { api, esc, fmt, hms, pill, post, statusClass, toast } from "./api.js";
import { TrendChart } from "./charts.js";
import { Schematic } from "./schematic.js";
import * as P from "./panels.js";

const $ = (id) => document.getElementById(id);
const LEVEL_ABBR = { Enterprise: "ENT", Site: "SITE", Area: "AREA", ProcessCell: "PC", Unit: "UNIT", ProductionLine: "PL",
  WorkCell: "WCL", ProductionUnit: "PU", StorageZone: "SZ", StorageUnit: "SU", WorkCenter: "WC", WorkUnit: "WU",
  EquipmentModule: "EM", ControlModule: "CM" };

const ui = {
  snap: null, selected: "EM-REACTOR", tab: "process", expanded: new Set(["ENT-ACME", "SITE-TE", "AREA-REACTION", "PU-REACTOR",
    "AREA-UTILITIES", "WC-CW"]), treeBuilt: false, lastEvent: null, tick: 0, catalog: null, loopsByPv: {},
  trends: ["XMEAS(9)", "XMV(10)", "UT-CW-REACTOR.capacity_fraction", "WU-CWP-101A.health", "XMEAS(7)", "XMEAS(40)"],
  charts: [], hoverT: null, busy: false, eventFilter: "", faultForm: null, faultCatalog: null,
};

// ------------------------------------------------------------------------------ theme
function applyTheme(t) {
  if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
  try { t ? localStorage.setItem("theme", t) : localStorage.removeItem("theme"); } catch { /* storage unavailable */ }
  ui.charts.forEach(c => c.draw());
}
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.setAttribute("data-theme", t); } catch { /* ignore */ }

// ------------------------------------------------------------------------------ controls
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); await poll(true); return r; }
  catch (e) { toast(e.message, true); }
}

function bindControls() {
  $("btn-start").onclick = () => act(() => post("/api/simulation/start"));
  $("btn-pause").onclick = () => act(() => post("/api/simulation/pause"));
  $("btn-resume").onclick = () => act(() => post("/api/simulation/resume"));
  $("btn-step").onclick = () => act(() => post("/api/simulation/step", { n: parseInt($("step-size").value) }));
  $("btn-reset").onclick = () => act(async () => {
    const d = $("duration-select").value;
    await post("/api/simulation/reset", d ? { duration_seconds: parseInt(d) } : {});
    resetView();
  }, "Simulation reset to t = 0");
  $("btn-load").onclick = () => loadScenario($("scenario-select").value);
  $("speed-select").onchange = (e) => act(() => post("/api/simulation/speed", { speed: parseFloat(e.target.value) }));
  $("btn-export-json").onclick = () => { window.location = "/api/benchmark/export/json"; };
  $("btn-export-csv").onclick = () => { window.location = "/api/benchmark/export/csv"; };
  $("btn-theme").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const dark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(dark ? "light" : "dark");
  };
  $("btn-expand").onclick = () => { walk(ui.snap.hierarchy, n => ui.expanded.add(n.id)); renderTree(); };
  document.querySelectorAll(".tab").forEach(b => b.onclick = () => setTab(b.dataset.tab));
  $("trend-window").onchange = () => refreshTrends();
  $("trend-true").onchange = () => rebuildCharts();
  $("btn-add-trend").onclick = openPicker;
  $("picker-q").oninput = renderPicker;
  document.addEventListener("click", (e) => {
    if (!$("picker").contains(e.target) && e.target.id !== "btn-add-trend") $("picker").classList.add("hidden");
  });
  document.body.addEventListener("click", onAction);
  document.body.addEventListener("change", onChangeAction);
}

async function loadScenario(id) {
  const d = $("duration-select").value;
  await act(async () => {
    await post("/api/simulation/create", { scenario_id: id, ...(d ? { duration_seconds: parseInt(d) } : {}) });
    resetView();
  }, `Loaded ${id}`);
}

function resetView() {
  ui.lastEvent = null; ui.treeBuilt = false; ui.faultCatalog = null;
  rebuildCharts();
}

async function onAction(e) {
  const t = e.target.closest("[data-action]");
  if (!t || e.target.tagName === "SELECT") return;
  const a = t.dataset.action;
  if (a === "select") { select(t.dataset.id); }
  else if (a === "trend") { addTrend(t.dataset.series); }
  else if (a === "op") {
    await act(() => post(`/api/operator/${t.dataset.op}`, JSON.parse(t.dataset.params || "{}")), "Operator action executed");
    renderTab(true);
  } else if (a === "fault") {
    await act(() => post(`/api/benchmark/faults/${encodeURIComponent(t.dataset.id)}/${t.dataset.op}`), `Fault ${t.dataset.op}`);
    renderTab(true);
  } else if (a === "fault-create") {
    let spec;
    try { spec = P.readFaultForm(); } catch (err) { toast("Parameters must be valid JSON", true); return; }
    await act(async () => {
      await post("/api/benchmark/faults", spec);
      if (t.dataset.start === "1") await post(`/api/benchmark/faults/${encodeURIComponent(spec.id)}/start`);
    }, `Fault ${spec.id} injected`);
    ui.faultForm = null;
    renderTab(true);
  } else if (a === "set-sp") {
    const v = parseFloat($(`sp-${t.dataset.loop}`).value);
    await act(() => post("/api/operator/set_setpoint", { loop_id: parseInt(t.dataset.loop), value: v }), "Setpoint changed");
  } else if (a === "set-xmv") {
    const v = parseFloat($(`xmv-${t.dataset.xmv}`).value);
    await act(() => post("/api/operator/set_xmv", { index: parseInt(t.dataset.xmv), value: v }), "Output changed");
  } else if (a === "asset-cmd") {
    await act(() => post("/api/operator/equipment_command", { asset_id: t.dataset.id, state: t.dataset.state }), "Command sent");
  } else if (a === "request-wo") {
    await act(() => post("/api/operator/request_maintenance", { asset_id: t.dataset.id, kind: t.dataset.kind, priority: 2 }),
      "Maintenance requested");
  } else if (a === "order-material") {
    const q = prompt("Quantity to order (kg)", "10000");
    if (q) await act(() => post("/api/operator/order_material", { storage_id: t.dataset.id, quantity_kg: parseFloat(q) }), "Purchase order created");
  } else if (a === "create-order") {
    const o = { order_id: $("no-id").value.trim(), product_id: "PROD-GH-M1", quantity: parseFloat($("no-qty").value),
      priority: parseInt($("no-prio").value), planned_start: parseInt($("no-start").value), planned_end: parseInt($("no-end").value) };
    await act(() => post("/api/operator/create_order", { order: o }), "Order created");
    renderTab(true);
  } else if (a === "load-scenario") { await loadScenario(t.dataset.id); }
  else if (a === "dup-scenario") {
    const nid = prompt("New scenario id", t.dataset.id + "-COPY");
    if (nid) { await act(() => post(`/api/benchmark/scenarios/${encodeURIComponent(t.dataset.id)}/duplicate`, { new_id: nid }), "Duplicated"); loadScenarioList(); renderTab(true); }
  } else if (a === "save-scenario") {
    await act(async () => {
      const sc = await api("/api/benchmark/scenarios/current");
      sc.id = $("save-id").value.trim(); if ($("save-name").value) sc.name = $("save-name").value;
      await post("/api/benchmark/scenarios", sc);
    }, "Scenario saved");
    loadScenarioList(); renderTab(true);
  }
}

async function onChangeAction(e) {
  const t = e.target;
  if (t.dataset.action === "loop-mode") {
    await act(() => post("/api/operator/set_loop_mode", { loop_id: parseInt(t.dataset.loop), mode: t.value }), "Loop mode changed");
  } else if (t.dataset.action === "plant-mode") {
    await act(() => post("/api/operator/set_control_mode", { mode: t.value }), "Control mode changed");
  } else if (t.dataset.action === "event-filter") {
    ui.eventFilter = t.value; renderTab(true);
  } else if (t.dataset.action === "fault-type") {
    ui.faultForm = { type: t.value }; renderTab(true);
  }
}

// ------------------------------------------------------------------------------ polling
async function poll(force = false) {
  if (ui.busy && !force) return;
  ui.busy = true;
  try {
    const snap = await api("/api/benchmark/ui/snapshot" + (ui.lastEvent ? `?after_event=${ui.lastEvent}` : ""));
    ui.snap = snap;
    if (snap.events && snap.events.length) ui.lastEvent = snap.events[snap.events.length - 1].event_id;
    renderTop(snap);
    if (!snap.hierarchy) return;
    if (!ui.treeBuilt) renderTree(); else updateTreeStatus();
    ui.schematic.update(snap, ui.selected);
    ui.tick++;
    if (force || ui.tick % 2 === 0) { renderDetail(); refreshTrends(); }
    if (force || ui.tick % 3 === 0) renderTab();
  } catch (e) {
    $("clock-sub").textContent = "server unreachable: " + e.message;
  } finally { ui.busy = false; }
}

function renderTop(snap) {
  const s = snap.simulation;
  if (!s || s.status === "NO_SIMULATION") return;
  $("clock-t").textContent = `${s.clock.time_hms} / ${hms(s.duration_s)}`;
  $("clock-sub").textContent = `${s.clock.timestamp} · ${s.scenario.id} · seed ${s.manifest.seed} · ${s.manifest.tep_backend}`;
  $("clock-bar").style.width = `${100 * s.progress}%`;
  const run = s.running ? "RUNNING" : s.status;
  badge("badge-run", run, run === "RUNNING" ? "good" : run === "COMPLETED" ? "" : "warning", run === "RUNNING" ? "▶" : run === "COMPLETED" ? "■" : "❚❚");
  const sd = s.process.shutdown;
  badge("badge-process", sd ? `SHUTDOWN: ${s.process.shutdown_reason}` : `PROCESS ${s.process.control_mode}`,
    sd ? "critical" : "good", sd ? "⛔" : "●");
  const a = s.alarms || { active: 0, unacknowledged: 0 };
  badge("badge-alarms", `${a.active} active · ${a.unacknowledged} unack`, a.active ? "critical" : "good", a.active ? "▲" : "✓");
  $("btn-start").disabled = s.status !== "READY";
  $("btn-pause").disabled = !s.running;
  $("btn-resume").disabled = s.running || s.status === "COMPLETED";
  $("btn-step").disabled = s.status === "COMPLETED";
  const sp = $("speed-select");
  if (document.activeElement !== sp) {
    if (![...sp.options].some(o => parseFloat(o.value) === s.speed)) sp.add(new Option(`${s.speed}×`, s.speed));
    sp.value = String(s.speed);
  }
  const p = s.production, oee = p.oee || {};
  const ut = snap.utilities || {};
  const kpis = [
    ["Production", `${fmt(p.rate_smoothed_kg_h, 0)} <small>kg/h</small>`, pill(p.state)],
    ["Produced", `${fmt(p.total_kg / 1000, 1)} <small>t</small>`, ""],
    ["Accepted / rejected", `${fmt(p.accepted_kg / 1000, 1)} / ${fmt(p.rejected_kg / 1000, 1)} <small>t</small>`, ""],
    ["OEE", `${fmt(100 * (oee.oee ?? 1), 1)} <small>%</small>`, ""],
    ["Reactor T", `${fmt(snap.process.xmeas[8], 2)} <small>°C</small>`, ""],
    ["Reactor P", `${fmt(snap.process.xmeas[6], 0)} <small>kPa</small>`, ""],
    ...Object.values(ut).map(u => [u.name, `${fmt(100 * u.properties.availability, 0)} <small>% avail</small>`, pill(u.properties.status)]),
  ];
  $("kpis").innerHTML = kpis.map(([k, v, extra]) => `<div class="kpi"><div class="k">${esc(k)} ${extra}</div><div class="v">${v}</div></div>`).join("");
}

function badge(id, text, cls, icon) {
  const b = $(id);
  b.className = `badge st-${cls}`;
  b.innerHTML = `<span class="dot"></span><span class="icon">${icon}</span><span>${esc(text)}</span>`;
}

// ------------------------------------------------------------------------------ tree
function walk(n, f) { f(n); (n.children || []).forEach(c => walk(c, f)); }

function statusDot(n) {
  const s = n.rollup_status;
  const cls = s === "FAILED" ? "critical" : s === "UNDER_MAINTENANCE" ? "maint" : ["DEGRADED", "CONSTRAINED"].includes(s) ? "warning"
    : s === "ALARM" ? "serious" : "";
  return `<span class="sdot ${cls}" title="${esc(s)}"></span>`;
}

function renderTree() {
  const root = ui.snap.hierarchy;
  const node = (n) => {
    const kids = n.children || [];
    const open = ui.expanded.has(n.id);
    return `<li><div class="tnode ${ui.selected === n.id ? "sel" : ""}" data-node="${esc(n.id)}">
      <span class="tw" data-toggle="${esc(n.id)}">${kids.length ? (open ? "▾" : "▸") : ""}</span>
      <span class="lvl" title="${esc(n.level)}">${LEVEL_ABBR[n.level] || n.level}</span>
      <span class="nm" title="${esc(n.id)}">${esc(n.name)}</span>
      ${n.origin === "ENTERPRISE" ? '<span class="ent" title="added by the enterprise layer (not in TEP)">ent</span>' : ""}
      <span class="spacer"></span><span data-status="${esc(n.id)}">${statusDot(n)}${n.active_alarms ? `<span class="acount">${n.active_alarms}</span>` : ""}</span></div>
      ${kids.length && open ? `<ul>${kids.map(node).join("")}</ul>` : ""}</li>`;
  };
  $("tree").innerHTML = node(root);
  $("tree").querySelectorAll("[data-toggle]").forEach(el => el.onclick = (e) => {
    e.stopPropagation();
    const id = el.dataset.toggle;
    ui.expanded.has(id) ? ui.expanded.delete(id) : ui.expanded.add(id);
    renderTree();
  });
  $("tree").querySelectorAll("[data-node]").forEach(el => el.onclick = () => select(el.dataset.node));
  ui.treeBuilt = true;
}

function updateTreeStatus() {
  walk(ui.snap.hierarchy, n => {
    const el = document.querySelector(`[data-status="${CSS.escape(n.id)}"]`);
    if (el) el.innerHTML = statusDot(n) + (n.active_alarms ? `<span class="acount">${n.active_alarms}</span>` : "");
  });
}

function select(id) {
  if (!id) return;
  ui.selected = id;
  // expand ancestors so the node is visible
  const path = [];
  const find = (n, trail) => { if (n.id === id) { path.push(...trail); return true; } return (n.children || []).some(c => find(c, [...trail, n.id])); };
  if (ui.snap?.hierarchy) find(ui.snap.hierarchy, []);
  path.forEach(p => ui.expanded.add(p));
  if (ui.snap?.hierarchy) renderTree();
  renderDetail();
  if (ui.snap) ui.schematic.update(ui.snap, id);
}

// ------------------------------------------------------------------------------ details
async function renderDetail() {
  const id = ui.selected;
  const box = $("detail");
  if (!id || !ui.snap) return;
  if (box.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;  // don't clobber typing
  let ent;
  try { ent = await api(`/api/benchmark/entities/${encodeURIComponent(id)}`); } catch (e) { box.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  const meta = ent.meta || {};
  const props = ent.properties || {};
  const units = ent.units || {};
  const crumbs = (meta.path || []).map(p => `<a data-action="select" data-id="${esc(p)}">${esc(p)}</a>`).join(" › ");
  const status = props.status || ent.status_rollup?.rollup_status;
  let html = `<div class="detail-head"><h3>${esc(ent.name)}</h3><div class="crumbs">${crumbs || esc(ent.kind)}</div>
    <div class="tagrow">${meta.level ? pill(meta.level, "") : pill(ent.kind, "")}${meta.origin ? pill(meta.origin === "TEP" ? "TEP equipment" : "Enterprise layer", "") : ""}
    ${status ? pill(status) : ""}${meta.equipment_class ? pill(meta.equipment_class, "") : ""}</div>
    ${meta.description ? `<div class="fault-desc">${esc(meta.description)}</div>` : ""}</div>`;
  if (id === "SITE-TE" || id === "ENT-ACME") {
    html += `<div class="section-title">Plant control mode</div><select data-action="plant-mode">
      ${["CLOSED_LOOP", "MANUAL"].map(m => `<option ${ui.snap.process.control_mode === m ? "selected" : ""}>${m}</option>`).join("")}</select>
      <div class="fault-desc">CLOSED_LOOP runs the native temain_mod.f loops. MANUAL suspends them; XMVs are then operator-set.</div>`;
  }
  if (ent.variables && ent.variables.length) {
    html += `<div class="section-title">TEP variables</div><table class="grid"><tr><th>Var</th><th>Property</th><th class="num">Value</th><th>Unit</th><th></th></tr>
      ${ent.variables.map(v => `<tr><td class="mono">${esc(v.id)}</td><td>${esc(v.property)}${v.tag ? ` <span class="muted">${esc(v.tag)}</span>` : ""}</td>
        <td class="num">${fmt(v.value.value, 3)}${v.value.quality !== "GOOD" ? " " + pill(v.value.quality, "critical") : ""}</td><td>${esc(v.unit)}</td>
        <td>${`<span class="addtrend" data-action="trend" data-series="${esc(v.id)}">trend</span>`}</td></tr>`).join("")}</table>`;
  }
  if (ent.loops && ent.loops.length) {
    html += `<div class="section-title">Control loops</div>` + ent.loops.map(l => {
      const xmvIdx = l.output_kind === "XMV" ? parseInt(l.output_id.slice(4)) : null;
      return `<div class="loopbox"><div class="row"><b class="mono">${esc(l.tag)}</b> ${esc(l.name)} ${l.saturated ? pill("OUTPUT SATURATED", "warning") : ""}
          ${l.override ? pill(l.override, "warning") : ""}</div>
        <div class="kv" style="margin:4px 0">
          <span class="k">measurement (${esc(l.pv_id)})</span><span class="v">${fmt(l.pv, 4)}</span>
          <span class="k">setpoint</span><span class="v">${fmt(l.setpoint, 4)}</span>
          <span class="k">output (${esc(l.output_id)})</span><span class="v">${fmt(l.output, 4)}</span>
          <span class="k">algorithm</span><span class="v">${esc(l.algorithm)} · ${l.period_s}s</span></div>
        <div class="row">mode <select data-action="loop-mode" data-loop="${l.loop_id}">${["AUTO", "CAS", "MAN"].map(m =>
          `<option ${l.mode === m ? "selected" : ""} ${m === "CAS" && !l.cascade_parent ? "disabled" : ""}>${m}</option>`).join("")}</select>
          ${l.mode !== "CAS" ? `SP <input id="sp-${l.loop_id}" type="number" step="any" value="${fmt(l.setpoint, 4)}"><button class="small" data-action="set-sp" data-loop="${l.loop_id}">set</button>`
            : `<span class="muted">SP from loop ${l.cascade_parent}</span>`}
          ${l.mode === "MAN" && xmvIdx ? `OUT <input id="xmv-${xmvIdx}" type="number" step="any" value="${fmt(l.output, 2)}"><button class="small" data-action="set-xmv" data-xmv="${xmvIdx}">set</button>` : ""}
          <span class="addtrend" data-action="trend" data-series="${esc(l.pv_id)}">trend PV/SP</span></div></div>`;
    }).join("");
  }
  if (meta.asset) {
    html += `<div class="section-title">Asset</div><div class="row filters">
      ${["RUN", "STANDBY", "STOP"].map(s => `<button class="small" data-action="asset-cmd" data-id="${esc(id)}" data-state="${s}">${s.toLowerCase()}</button>`).join("")}
      <button class="small" data-action="request-wo" data-id="${esc(id)}" data-kind="corrective">request repair</button>
      <button class="small" data-action="trend" data-series="${esc(id)}.health">trend health</button></div>`;
  }
  if (ent.kind === "utility" || id.startsWith("WC-")) {
    html += `<div class="row filters"><button class="small" data-action="request-wo" data-id="${esc(ent.kind === "utility" ? meta.supplied_by : id)}" data-kind="inspection">request inspection</button></div>`;
  }
  const hidden = new Set(meta.unobservable || []);
  const rows = Object.entries(props).filter(([k, v]) => !hidden.has(k) && (typeof v !== "object" || v === null || Array.isArray(v)));
  if (rows.length) {
    html += `<div class="section-title">Properties</div><table class="grid">${rows.map(([k, v]) => `<tr><td>${esc(k)}</td>
      <td class="num">${Array.isArray(v) ? esc(v.join(", ")) : fmt(v, 4)}</td><td class="muted">${esc(units[k] || "")}</td></tr>`).join("")}</table>`;
  }
  if (ent.alarms && ent.alarms.length) {
    html += `<div class="section-title">Alarms</div>` + ent.alarms.map(a => `<div>${pill(a.priority, a.priority === "CRITICAL" ? "critical" : "warning")}
      ${esc(a.message)} ${pill(a.state)}</div>`).join("");
  }
  if (ent.children && ent.children.length) {
    html += `<div class="section-title">Contains</div>` + ent.children.map(c => `<div><a class="addtrend" data-action="select" data-id="${esc(c)}">${esc(c)}</a></div>`).join("");
  }
  box.innerHTML = html;
}

// ------------------------------------------------------------------------------ tabs
function setTab(tab) {
  ui.tab = tab;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tabpane").forEach(p => p.classList.toggle("active", p.id === "pane-" + tab));
  renderTab(true);
}

const FORM_TABS = new Set(["faults", "scenario", "production"]);
async function renderTab(force = false) {
  const tab = ui.tab;
  if (tab === "process") return;
  const pane = $("pane-" + tab);
  if (!force && FORM_TABS.has(tab) && pane.contains(document.activeElement) &&
      ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
  if (tab === "faults" && !force) {   // keep a half-filled form: remember values
    const g = (i) => document.getElementById(i);
    if (g("ff-type")) ui.faultForm = { type: g("ff-type").value, target: g("ff-target").value, id: g("ff-id").value,
      severity: g("ff-sev").value, start: g("ff-start").value, duration: g("ff-dur").value, progression: g("ff-prog").value,
      ramp: g("ff-ramp").value, params: g("ff-params").value };
  }
  const scroll = pane.scrollTop;
  try {
    const r = { variables: P.renderVariables, utilities: P.renderUtilities, maintenance: P.renderMaintenance,
      inventory: P.renderInventory, quality: P.renderQuality, production: P.renderProduction, alarms: P.renderAlarms,
      causal: P.renderCausal, scenario: (p) => P.renderScenario(p, ui.snap.simulation), faults: P.renderFaults }[tab];
    await r(pane, ui);
    pane.scrollTop = scroll;
  } catch (e) { pane.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------------------ trends
function metaFor(name) { return (ui.catalog || []).find(m => m.name === name) || { name, label: name, unit: "" }; }

function chartSpec(name) {
  const m = metaFor(name);
  const series = [{ name, label: name.startsWith("XMEAS") ? "transmitted" : m.label, role: "main" }];
  if (name.startsWith("XMEAS(")) {
    const lp = ui.loopsByPv[name];
    if (lp) series.push({ name: `SP:${lp}`, label: `setpoint (${lp})`, role: "sp" });
    if ($("trend-true").checked) series.push({ name: `TRUE:${name}`, label: "true (process)", role: "true" });
  }
  const label = name.startsWith("XMEAS") || name.startsWith("XMV") ? `${name} ${m.label}` : m.label;
  return { id: name, label, unit: m.unit, series };
}

function rebuildCharts() {
  ui.charts.forEach(c => c.destroy());
  ui.charts = [];
  const grid = $("trend-grid");
  for (const name of ui.trends) {
    const c = new TrendChart(grid, chartSpec(name), onChartHover);
    ui.charts.push(c);
  }
  $("trend-chips").innerHTML = ui.trends.map(n => `<span class="chip">${esc(n)}<button data-remove="${esc(n)}" title="remove">×</button></span>`).join(" ");
  $("trend-chips").querySelectorAll("[data-remove]").forEach(b => b.onclick = () => {
    ui.trends = ui.trends.filter(x => x !== b.dataset.remove); rebuildCharts();
  });
  refreshTrends();
}

function addTrend(name) {
  if (!ui.trends.includes(name)) { ui.trends.unshift(name); rebuildCharts(); toast(`Trending ${name}`); }
}

async function refreshTrends() {
  if (!ui.charts.length || !ui.snap?.simulation?.clock) return;
  const now = ui.snap.simulation.clock.time_s;
  const w = parseInt($("trend-window").value);
  const names = [...new Set(ui.charts.flatMap(c => c.spec.series.map(s => s.name)))];
  try {
    const data = await api(`/api/benchmark/history?series=${encodeURIComponent(names.join("|"))}${w ? `&since=${Math.max(0, now - w)}` : ""}&max_points=1200`);
    for (const c of ui.charts) c.setData(data, c.spec.series);
  } catch (e) { /* series may not exist in a new run */ }
}

function onChartHover(t, e) {
  ui.charts.forEach(c => c.setHover(t));
  const tip = $("tooltip");
  if (t === null) { tip.classList.add("hidden"); return; }
  const chart = ui.charts.find(c => c.canvas === e.target);
  tip.innerHTML = `<div class="mono">${hms(t)}</div>` + (chart ? chart.tooltipRows(t) : "");
  tip.style.left = `${e.clientX + 14}px`; tip.style.top = `${e.clientY - 10}px`;
  tip.classList.remove("hidden");
}

function openPicker(e) {
  const p = $("picker");
  const r = e.target.getBoundingClientRect();
  p.style.left = `${r.left}px`; p.style.top = `${Math.max(10, r.top - 370)}px`;
  p.classList.remove("hidden");
  $("picker-q").value = ""; renderPicker(); $("picker-q").focus();
}

function renderPicker() {
  const q = $("picker-q").value.toLowerCase();
  const items = (ui.catalog || []).filter(m => !m.name.startsWith("TRUE:") && !m.name.startsWith("SP:") &&
    (m.name.toLowerCase().includes(q) || m.label.toLowerCase().includes(q) || m.group.toLowerCase().includes(q))).slice(0, 120);
  $("picker-list").innerHTML = items.map(m => `<div class="item" data-name="${esc(m.name)}"><span>${esc(m.label)}</span>
    <span class="muted mono">${esc(m.name)}</span></div>`).join("");
  $("picker-list").querySelectorAll(".item").forEach(el => el.onclick = () => { addTrend(el.dataset.name); $("picker").classList.add("hidden"); });
}

// ------------------------------------------------------------------------------ init
async function loadScenarioList() {
  const list = await api("/api/benchmark/scenarios");
  const cur = ui.snap?.simulation?.scenario?.id;
  $("scenario-select").innerHTML = list.filter(s => !s.error).map(s => `<option value="${esc(s.id)}" ${s.id === cur ? "selected" : ""}>${esc(s.id)} – ${esc(s.name)}</option>`).join("");
}

async function init() {
  ui.schematic = new Schematic($("schematic"), select, addTrend);
  bindControls();
  await poll(true);
  if (!ui.snap?.hierarchy) {           // no simulation yet: create the demo
    await post("/api/simulation/create", { scenario_id: "SCN-COOL-001" });
    await poll(true);
  }
  ui.catalog = await api("/api/benchmark/history/catalog");
  const loops = await api("/api/process/loops");
  for (const l of loops) ui.loopsByPv[l.pv_id] = l.tag;
  await loadScenarioList();
  rebuildCharts();
  const h = new URLSearchParams(location.hash.slice(1));      // deep links: #tab=faults&select=EM-REACTOR
  if (h.get("select")) ui.selected = h.get("select");
  if (["light", "dark"].includes(h.get("theme"))) applyTheme(h.get("theme"));
  select(ui.selected);
  if (h.get("tab")) setTab(h.get("tab"));
  setInterval(() => poll(), 1000);
  window.addEventListener("resize", () => ui.charts.forEach(c => c.draw()));
}

init();
