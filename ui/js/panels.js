// Tab renderers. Each returns HTML for its pane from REST data. Interactive elements use
// data-action attributes handled by app.js (event delegation).
import { api, esc, fmt, hms, pill, statusClass } from "./api.js";

const kv = (pairs) => `<div class="kv">${pairs.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${v}</span>`).join("")}</div>`;
const bar = (frac) => `<div class="bar"><div style="width:${Math.max(0, Math.min(100, 100 * (frac || 0)))}%"></div></div>`;
const trendLink = (series, text = "trend") => `<span class="addtrend" data-action="trend" data-series="${esc(series)}">${text}</span>`;

// ------------------------------------------------------------------ loops & variables
export async function renderVariables(pane) {
  const [meas, mvs, loops] = await Promise.all([api("/api/process/measurements"), api("/api/process/manipulated-variables"),
    api("/api/process/loops")]);
  const loopRows = loops.map(l => `<tr>
    <td class="mono">${esc(l.tag)}</td><td>${esc(l.name)}</td><td class="mono">${esc(l.pv_id)}</td>
    <td class="num">${fmt(l.pv, 3)}</td><td class="num">${fmt(l.setpoint, 3)}</td>
    <td class="mono">${esc(l.output_id)}</td><td class="num">${fmt(l.output, 3)}${l.saturated ? " " + pill("SAT", "warning") : ""}</td>
    <td>${pill(l.mode, l.mode === "MAN" ? "warning" : "")}${l.override ? " " + pill(l.override, "warning") : ""}</td>
    <td class="num">${l.period_s}s</td>
    <td><button class="small" data-action="select" data-id="${esc(l.control_module)}">open</button></td></tr>`).join("");
  const measRows = meas.map(m => `<tr class="clickable" data-action="select" data-id="${esc(m.equipment_id)}">
    <td class="mono">${esc(m.id)}</td><td class="mono">${esc(m.tag || "")}</td><td>${esc(m.name)}</td>
    <td class="num">${fmt(m.value, 4)}</td><td>${esc(m.unit)}</td><td>${m.quality === "GOOD" ? "" : pill(m.quality, "critical")}</td>
    <td>${esc(m.equipment_id)} · ${esc(m.property)}</td><td>${esc(m.measurement_type === "sampled" ? `sampled ${m.sample_period_h} h` : "continuous")}</td>
    <td>${trendLink(m.id)}</td></tr>`).join("");
  const mvRows = mvs.map(m => `<tr><td class="mono">${esc(m.id)}</td><td>${esc(m.name)}</td><td class="num">${fmt(m.value, 3)}</td>
    <td>${esc(m.equipment_id)}</td><td>${m.controlled_by_loop ? "loop " + m.controlled_by_loop : "manual"}</td><td>${trendLink(m.id)}</td></tr>`).join("");
  pane.innerHTML = `
    <div class="section-title">Native control loops (temain_mod.f CONTRLn) - setpoint, measurement and output are separate</div>
    <table class="grid"><tr><th>Tag</th><th>Loop</th><th>PV</th><th class="num">PV value</th><th class="num">Setpoint</th>
      <th>Output</th><th class="num">Output value</th><th>Mode</th><th class="num">Period</th><th></th></tr>${loopRows}</table>
    <div class="section-title">Manipulated variables XMV(1..12)</div>
    <table class="grid"><tr><th>Id</th><th>Name</th><th class="num">%</th><th>Final control element</th><th>Driven by</th><th></th></tr>${mvRows}</table>
    <div class="section-title">Process measurements XMEAS(1..41) - transmitted values</div>
    <table class="grid"><tr><th>Id</th><th>Tag</th><th>Name</th><th class="num">Value</th><th>Unit</th><th>Quality</th>
      <th>Equipment · property</th><th>Type</th><th></th></tr>${measRows}</table>`;
}

// ------------------------------------------------------------------ utilities
export async function renderUtilities(pane) {
  const [ut, mt] = await Promise.all([api("/api/utilities"), api("/api/maintenance")]);
  const cards = Object.values(ut).map(u => {
    const p = u.properties, un = u.units || {};
    return `<div class="card"><h4><span data-action="select" data-id="${esc(u.id)}" style="cursor:pointer">${esc(u.name)}</span>${pill(p.status)}</h4>
      ${kv([["capacity", `${fmt(p.capacity)} ${esc(un.capacity || "")}`], ["available", fmt(p.available_capacity)],
        ["availability", `${fmt(100 * p.availability, 1)} %`], ["demand / flow", fmt(p.flow)],
        ["utilization", `${fmt(100 * p.utilization, 1)} %`], ["temperature", p.temperature == null ? "–" : `${fmt(p.temperature)} ${esc(un.temperature || "")}`],
        ["pressure", p.pressure == null ? (p.voltage != null ? `${fmt(p.voltage)} kV` : "–") : `${fmt(p.pressure)} ${esc(un.pressure || "")}`],
        ["health", fmt(p.health, 3)]])}
      <div class="muted" style="font-size:11px">utilization</div>${bar(p.utilization)}
      <div>${trendLink(u.id + ".utilization", "trend utilization")} · ${trendLink(u.id + ".available_capacity", "trend capacity")}</div></div>`;
  }).join("");
  const assets = mt.assets.filter(a => a.id.startsWith("WU-")).map(a => `<tr class="clickable" data-action="select" data-id="${esc(a.id)}">
    <td>${esc(a.name)}</td><td>${pill(a.status)}</td><td>${esc(a.role)}</td><td class="num">${fmt(a.health, 3)}</td>
    <td class="num">${fmt(a.efficiency, 3)}</td><td class="num">${fmt(a.vibration, 2)}</td></tr>`).join("");
  pane.innerHTML = `<div class="section-title">Utility services</div><div class="cards">${cards}</div>
    <div class="section-title">Utility equipment</div>
    <table class="grid"><tr><th>Asset</th><th>Status</th><th>Role</th><th class="num">Health</th><th class="num">Efficiency</th>
    <th class="num">Vibration mm/s</th></tr>${assets}</table>`;
}

// ------------------------------------------------------------------ maintenance
export async function renderMaintenance(pane) {
  const m = await api("/api/maintenance");
  const wos = m.work_orders.map(w => `<tr><td class="mono">${esc(w.wo_id)}</td><td>${esc(w.asset_id)}</td><td>${esc(w.kind)}</td>
    <td class="num">P${w.priority}</td><td>${esc(w.description)}</td><td>${pill(w.status)}</td><td>${esc(w.technician || "")}</td>
    <td class="mono">${hms(w.requested_s)}</td><td class="mono">${hms(w.scheduled_start_s)}</td><td class="mono">${hms(w.completed_s)}</td>
    <td>${esc(w.wait_reason || (w.findings || []).join(", "))}</td>
    <td>${["REQUESTED", "WAITING_PARTS", "WAITING_TECHNICIAN", "SCHEDULED"].includes(w.status) ?
      `<button class="small" data-action="op" data-op="cancel_work_order" data-params='${esc(JSON.stringify({ wo_id: w.wo_id }))}'>cancel</button>` : ""}</td></tr>`).join("");
  const techs = m.technicians.map(t => `<tr><td>${esc(t.id)}</td><td>${esc(t.name)}</td><td>${esc(t.skills.join(", "))}</td>
    <td>${esc(t.shift)}</td><td>${pill(t.status, t.status === "BUSY" ? "maint" : t.status === "AVAILABLE" ? "good" : "")}</td><td>${esc(t.work_order || "")}</td></tr>`).join("");
  const assets = m.assets.map(a => `<tr class="clickable" data-action="select" data-id="${esc(a.id)}"><td>${esc(a.name)}</td><td>${pill(a.status)}</td>
    <td class="num">${fmt(a.health, 3)}</td><td>${bar(a.health)}</td><td class="num">${fmt(a.efficiency, 3)}</td>
    <td class="num">${fmt(a.vibration, 2)}</td><td class="num">${fmt(a.run_hours, 0)}</td></tr>`).join("");
  const parts = m.spare_parts.map(p => `<tr><td class="mono">${esc(p.part_id)}</td><td>${esc(p.name)}</td><td class="num">${p.on_hand}</td>
    <td class="num">${p.reserved}</td><td class="num">${p.available}</td><td>${p.blocked ? pill("BLOCKED", "critical") : ""}</td></tr>`).join("");
  pane.innerHTML = `<div class="section-title">Work orders</div>
    <table class="grid"><tr><th>WO</th><th>Asset</th><th>Kind</th><th>Prio</th><th>Task</th><th>Status</th><th>Tech</th>
    <th>Requested</th><th>Start</th><th>Done</th><th>Note</th><th></th></tr>${wos || '<tr><td colspan="12" class="muted">no work orders</td></tr>'}</table>
    <div class="section-title">Asset condition</div>
    <table class="grid"><tr><th>Asset</th><th>Status</th><th class="num">Health</th><th style="width:120px"></th><th class="num">Efficiency</th>
    <th class="num">Vibration</th><th class="num">Run h</th></tr>${assets}</table>
    <div class="section-title">Technicians</div>
    <table class="grid"><tr><th>Id</th><th>Name</th><th>Skills</th><th>Shift</th><th>Status</th><th>Work order</th></tr>${techs}</table>
    <div class="section-title">Spare parts (MRO store)</div>
    <table class="grid"><tr><th>Part</th><th>Name</th><th class="num">On hand</th><th class="num">Reserved</th><th class="num">Available</th><th></th></tr>${parts}</table>`;
}

// ------------------------------------------------------------------ inventory & warehouse
export async function renderInventory(pane) {
  const inv = await api("/api/inventory");
  const cards = inv.storage.map(s => `<div class="card"><h4><span data-action="select" data-id="${esc(s.id)}" style="cursor:pointer">${esc(s.name)}</span>${pill(s.status)}</h4>
    ${kv([["material", esc(s.material)], ["on hand", `${fmt(s.quantity_kg)} kg`], ["usable", `${fmt(s.usable_kg)} kg`],
      ["reserved", `${fmt(s.reserved_kg)} kg`], ["available", `${fmt(s.available_kg)} kg`], ["consumption", `${fmt(s.consumption_kg_h)} kg/h`],
      ["current lot", esc(s.current_lot || "–")]])}
    <div class="muted" style="font-size:11px">level ${fmt(s.level_pct, 1)} %</div>${bar(s.level_pct / 100)}
    <div>${trendLink(s.id + ".level_pct", "trend level")} · <span class="addtrend" data-action="order-material" data-id="${esc(s.id)}">order material</span></div></div>`).join("");
  const lots = inv.material_lots.filter(l => l.quality_status !== "CONSUMED" || l.consumed_kg > 0).map(l => `<tr><td class="mono">${esc(l.lot_id)}</td>
    <td>${esc(l.material_id)}</td><td>${esc(l.location)}</td><td class="num">${fmt(l.quantity_kg)}</td><td class="num">${fmt(l.consumed_kg)}</td>
    <td>${pill(l.quality_status)}</td><td>${esc(l.supplier || "")}</td><td class="mono">${esc(Object.entries(l.attributes).map(([k, v]) => `${k}=${v}`).join(" "))}</td></tr>`).join("");
  const pos = inv.purchase_orders.map(p => `<tr><td class="mono">${esc(p.po_id)}</td><td>${esc(p.item_id)}</td><td>${esc(p.item_type)}</td>
    <td class="num">${fmt(p.quantity)}</td><td>${esc(p.supplier || "")}</td><td class="mono">${hms(p.ordered_s)}</td><td class="mono">${hms(p.due_s)}</td>
    <td>${pill(p.status, p.status === "RECEIVED" ? "good" : p.status === "DELAYED" ? "warning" : "")}</td></tr>`).join("");
  const tanks = Object.entries(inv.warehouse.tanks).map(([id, t]) => `<tr class="clickable" data-action="select" data-id="${esc(id)}"><td>${esc(id)}</td>
    <td class="num">${fmt(t.quantity_kg)}</td><td>${esc((t.lots || []).join(", "))}</td></tr>`).join("");
  const ships = inv.shipments.map(s => `<tr><td class="mono">${esc(s.shipment_id)}</td><td class="mono">${hms(s.due_s)}</td><td class="num">${fmt(s.quantity_kg)}</td>
    <td>${pill(s.status, s.status === "SHIPPED" ? "good" : s.status === "DELAYED" ? "warning" : "")}</td><td class="num">${fmt(s.shipped_kg)}</td></tr>`).join("");
  pane.innerHTML = `<div class="section-title">Raw material storage</div><div class="cards">${cards}</div>
    <div class="section-title">Material lots</div><table class="grid"><tr><th>Lot</th><th>Material</th><th>Location</th><th class="num">Remaining kg</th>
    <th class="num">Consumed kg</th><th>Quality</th><th>Supplier</th><th>Certificate attributes</th></tr>${lots}</table>
    <div class="section-title">Purchase orders</div><table class="grid"><tr><th>PO</th><th>Item</th><th>Type</th><th class="num">Qty</th><th>Supplier</th>
    <th>Ordered</th><th>Due</th><th>Status</th></tr>${pos || '<tr><td colspan="8" class="muted">none</td></tr>'}</table>
    <div class="section-title">Warehouse - finished product tanks (shipped total ${fmt(inv.warehouse.shipped_total_kg)} kg)</div>
    <table class="grid"><tr><th>Tank</th><th class="num">kg</th><th>Lots</th></tr>${tanks}</table>
    <div class="section-title">Dispatch</div><table class="grid"><tr><th>Shipment</th><th>Due</th><th class="num">kg</th><th>Status</th><th class="num">Shipped</th></tr>
    ${ships || '<tr><td colspan="5" class="muted">none yet</td></tr>'}</table>`;
}

// ------------------------------------------------------------------ quality
export async function renderQuality(pane) {
  const q = await api("/api/quality");
  const specs = Object.entries(q.specifications).map(([pid, tests]) => tests.map(t => `<tr><td>${esc(pid)}</td><td class="mono">${esc(t.id)}</td>
    <td>${esc(t.name)}</td><td class="num">${t.low ?? "–"}</td><td class="num">${t.target ?? "–"}</td><td class="num">${t.high ?? "–"}</td>
    <td class="mono">${esc(t.expression)}</td></tr>`).join("")).join("");
  const lots = q.lots.map(l => `<tr><td class="mono">${esc(l.lot_id)}</td><td>${esc(l.order_id || "–")}</td><td class="mono">${esc(l.start_hms)}</td>
    <td class="mono">${esc(l.end_hms || "…")}</td><td class="num">${fmt(l.quantity_kg)}</td><td>${pill(l.status)}</td><td>${l.samples.length}</td>
    <td>${esc(l.location)}</td><td>${esc(l.disposition_reason)}</td></tr>`).join("");
  const samples = q.samples.slice(0, 80).map(s => `<tr><td class="mono">${esc(s.sample_id)}</td><td class="mono">${esc(s.taken_hms)}</td>
    <td>${esc(s.subject_type)}</td><td>${esc(s.lot_id || "–")}</td><td>${pill(s.overall)}</td>
    <td>${s.results.map(r => `<span class="${r.pass ? "" : "dev"}">${esc(r.test_id)}=${fmt(r.value, 3)}${r.pass ? "" : " ✗"}</span>`).join(" · ")}</td></tr>`).join("");
  pane.innerHTML = `<div class="section-title">Specifications (configs/quality.yaml)</div>
    <table class="grid"><tr><th>Product</th><th>Test</th><th>Name</th><th class="num">Low</th><th class="num">Target</th><th class="num">High</th><th>From TEP analyzer</th></tr>${specs}</table>
    <div class="section-title">Production lots</div>
    <table class="grid"><tr><th>Lot</th><th>Order</th><th>Start</th><th>End</th><th class="num">kg</th><th>Status</th><th>Samples</th><th>Location</th><th>Disposition</th></tr>
    ${lots || '<tr><td colspan="9" class="muted">none</td></tr>'}</table>
    <div class="section-title">Samples &amp; results (latest first)</div>
    <table class="grid"><tr><th>Sample</th><th>Taken</th><th>Subject</th><th>Lot</th><th>Result</th><th>Tests</th></tr>
    ${samples || '<tr><td colspan="6" class="muted">no samples yet (product analyzer reports every 0.25 h)</td></tr>'}</table>`;
}

// ------------------------------------------------------------------ production
export async function renderProduction(pane) {
  const [orders, prod] = await Promise.all([api("/api/production-orders"), api("/api/production")]);
  const rows = orders.map(o => `<tr><td class="mono">${esc(o.order_id)}</td><td>${esc(o.product_id)}</td><td>${esc(o.customer || "")}</td>
    <td class="num">P${o.priority}</td><td class="num">${fmt(o.quantity)} ${esc(o.unit)}</td><td>${bar(o.progress)}<span class="muted">${fmt(100 * o.progress, 1)} %</span></td>
    <td>${pill(o.status)}${o.late ? " " + pill("LATE", "warning") : ""}</td><td class="mono">${hms(o.planned_start)}–${hms(o.planned_end)}</td>
    <td class="mono">${hms(o.actual_start)}–${hms(o.actual_end)}</td><td class="mono">${hms(o.projected_end)}</td>
    <td class="num">${fmt(o.produced_kg)}</td><td class="num">${fmt(o.rejected_kg)}</td><td>${esc(o.status_reason)}</td>
    <td>${["PLANNED", "BLOCKED"].includes(o.status) ? `<button class="small" data-action="op" data-op="order_command" data-params='${esc(JSON.stringify({ order_id: o.order_id, command: "release" }))}'>release</button>` : ""}
    ${o.status === "RUNNING" ? `<button class="small" data-action="op" data-op="order_command" data-params='${esc(JSON.stringify({ order_id: o.order_id, command: "pause" }))}'>pause</button>` : ""}
    ${o.status === "PAUSED" ? `<button class="small" data-action="op" data-op="order_command" data-params='${esc(JSON.stringify({ order_id: o.order_id, command: "resume" }))}'>resume</button>` : ""}
    ${!["COMPLETED", "CANCELLED"].includes(o.status) ? `<button class="small" data-action="op" data-op="order_command" data-params='${esc(JSON.stringify({ order_id: o.order_id, command: "cancel" }))}'>cancel</button>` : ""}</td></tr>`).join("");
  const oee = prod.oee || {};
  pane.innerHTML = `<div class="cards">
      <div class="card"><h4>Line ${esc(prod.line_id)} ${pill(prod.state)}</h4>${kv([["rate", `${fmt(prod.rate_smoothed_kg_h)} kg/h`],
        ["nominal", `${fmt(prod.nominal_rate_kg_h)} kg/h`], ["total produced", `${fmt(prod.total_kg)} kg`], ["accepted", `${fmt(prod.accepted_kg)} kg`],
        ["rejected", `${fmt(prod.rejected_kg)} kg`], ["current lot", esc(prod.current_lot || "–")], ["current order", esc(prod.current_order || "–")]])}</div>
      <div class="card"><h4>OEE</h4>${kv([["availability", `${fmt(100 * oee.availability, 1)} %`], ["performance", `${fmt(100 * oee.performance, 1)} %`],
        ["quality", `${fmt(100 * oee.quality, 1)} %`], ["OEE", `${fmt(100 * oee.oee, 1)} %`]])}</div></div>
    <div class="section-title">Production orders</div>
    <table class="grid"><tr><th>Order</th><th>Product</th><th>Customer</th><th>Prio</th><th class="num">Quantity</th><th style="width:110px">Progress</th>
    <th>Status</th><th>Planned</th><th>Actual</th><th>Projected end</th><th class="num">Produced</th><th class="num">Rejected</th><th>Reason</th><th></th></tr>${rows}</table>
    <div class="section-title">New order</div>
    <div class="filters"><input id="no-id" placeholder="order id" style="width:130px"><input id="no-qty" type="number" placeholder="kg" value="15000">
      <input id="no-start" type="number" placeholder="planned start s" value="0"><input id="no-end" type="number" placeholder="planned end s" value="10800">
      <select id="no-prio"><option>1</option><option>2</option><option selected>3</option><option>4</option><option>5</option></select>
      <button data-action="create-order">create</button></div>`;
}

// ------------------------------------------------------------------ alarms & events
export async function renderAlarms(pane, ui) {
  const typeFilter = ui.eventFilter || "";
  const [alarms, events] = await Promise.all([api("/api/alarms"),
    api(`/api/events?limit=400${typeFilter ? "&types=" + encodeURIComponent(typeFilter) : ""}`)]);
  const arow = alarms.map(a => `<tr><td>${pill(a.priority, a.priority === "CRITICAL" ? "critical" : a.priority === "HIGH" ? "warning" : "serious")}</td>
    <td class="mono">${esc(a.alarm_id)}</td><td>${esc(a.message)}</td><td>${pill(a.state)}</td>
    <td class="mono">${hms(a.activation_time)}</td><td class="num">${fmt(a.activation_value, 3)}</td><td class="num">${fmt(a.value, 3)}</td>
    <td class="num">${esc(a.threshold)}</td><td data-action="select" data-id="${esc(a.equipment_id)}" style="cursor:pointer">${esc(a.equipment_id)}</td>
    <td>${["ACTIVE_UNACK", "RTN_UNACK"].includes(a.state) ? `<button class="small" data-action="op" data-op="acknowledge_alarm" data-params='${esc(JSON.stringify({ alarm_id: a.alarm_id }))}'>ack</button>` : esc(a.acknowledgement.by || "")}</td></tr>`).join("");
  const erow = [...events].reverse().map(e => `<tr class="evt-sev-${esc(e.severity)}"><td class="mono">${esc(e.event_id)}</td>
    <td class="mono">${hms(e.simulation_time)}</td><td>${esc(e.type)}</td><td>${esc(e.source)}</td><td>${esc(e.target || "")}</td>
    <td class="mono" style="font-size:11px">${esc(summarize(e.payload))}</td></tr>`).join("");
  const types = ["", "ALARM_ACTIVATED,ALARM_CLEARED", "EQUIPMENT_STATE_CHANGED,EQUIPMENT_DEGRADED,EQUIPMENT_FAILED,EQUIPMENT_REPAIRED",
    "MAINTENANCE_REQUESTED,MAINTENANCE_SCHEDULED,MAINTENANCE_STARTED,MAINTENANCE_COMPLETED,MAINTENANCE_WAITING",
    "QUALITY_RESULT_CREATED,LOT_STATE_CHANGED", "UTILITY_STATE_CHANGED", "MATERIAL_CONSUMED,MATERIAL_RECEIVED,MATERIAL_ORDERED,MATERIAL_SHORTAGE",
    "PRODUCTION_ORDER_RELEASED,PRODUCTION_ORDER_STARTED,PRODUCTION_ORDER_PAUSED,PRODUCTION_ORDER_BLOCKED,PRODUCTION_ORDER_COMPLETED,PRODUCTION_STATE_CHANGED",
    "OPERATOR_ACTION,SETPOINT_CHANGED,CONTROL_MODE_CHANGED,MANIPULATED_VARIABLE_CHANGED"];
  const labels = ["all", "alarms", "equipment", "maintenance", "quality", "utilities", "materials", "production", "operator"];
  pane.innerHTML = `<div class="section-title">Alarms <button class="small" data-action="op" data-op="acknowledge_all" data-params="{}">acknowledge all</button></div>
    <table class="grid"><tr><th>Priority</th><th>Alarm</th><th>Message</th><th>State</th><th>Activated</th><th class="num">At</th><th class="num">Now</th>
    <th class="num">Limit</th><th>Equipment</th><th>Ack</th></tr>${arow || '<tr><td colspan="10" class="muted">no alarms</td></tr>'}</table>
    <div class="section-title">Operational event log (benchmark ground-truth events are excluded)</div>
    <div class="filters"><span class="muted">filter</span><select data-action="event-filter">${types.map((t, i) =>
      `<option value="${esc(t)}" ${t === typeFilter ? "selected" : ""}>${labels[i]}</option>`).join("")}</select></div>
    <table class="grid"><tr><th>Id</th><th>Time</th><th>Type</th><th>Source</th><th>Target</th><th>Payload</th></tr>${erow}</table>`;
}

function summarize(p) {
  const keys = ["message", "new", "status", "reason", "alarm_id", "overall", "failing_tests", "kind", "description", "quantity_kg", "action", "params", "value"];
  const parts = [];
  for (const k of keys) if (p[k] !== undefined && p[k] !== null && p[k] !== "") parts.push(`${k}=${typeof p[k] === "object" ? JSON.stringify(p[k]) : p[k]}`);
  return parts.slice(0, 5).join("  ");
}

// ------------------------------------------------------------------ causal model
export async function renderCausal(pane) {
  const c = await api("/api/coupling");
  const b = Object.entries(c.boundary).map(([k, v]) => {
    const nom = c.boundary_nominal[k], d = c.boundary_definitions[k];
    const dev = Math.abs(v - nom) > 1e-9 * Math.max(1, Math.abs(nom));
    return `<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(d.fortran_symbol)}</td><td class="num ${dev ? "dev" : ""}">${fmt(v, 5)}</td>
      <td class="num">${fmt(nom, 5)}</td><td>${esc(d.unit)}</td><td style="font-size:11px">${esc(d.teprob_usage)}</td></tr>`;
  }).join("");
  const cats = {};
  for (const r of c.relations) (cats[r.category] ||= []).push(r);
  const rel = Object.entries(cats).map(([cat, rs]) => `<div class="section-title">${esc(cat.replace(/_/g, " "))}</div>` + rs.map(r => {
    const dev = r.baseline !== null && Math.abs(r.value - r.baseline) > 0.005 * Math.max(Math.abs(r.baseline), 1e-6);
    return `<div class="rel"><b class="mono">${esc(r.output)}</b> = <code>${esc(r.expression)}</code>
      <span class="num ${dev ? "dev" : "muted"}">→ ${fmt(r.value, 4)}${dev ? ` (baseline ${fmt(r.baseline, 4)})` : ""}</span>
      <div class="muted" style="font-size:11px">${esc(Object.entries(r.inputs).map(([k, v]) => `${k}: ${v}`).join(" · "))}${r.description ? " — " + esc(r.description) : ""}</div></div>`;
  }).join("")).join("");
  pane.innerHTML = `<p class="fault-desc">Causal relations are data (configs/coupling.yaml). The enterprise layer influences TEP
    <b>only</b> through the boundary parameters below; everything downstream is computed by teprob.f. Highlighted values deviate from baseline.</p>
    <div class="section-title">TEP boundary parameters</div>
    <table class="grid"><tr><th>Parameter</th><th>Fortran</th><th class="num">Value</th><th class="num">TEINIT</th><th>Unit</th><th>Consumed in TEFUNC</th></tr>${b}</table>
    <div class="graph-list">${rel}</div>`;
}

// ------------------------------------------------------------------ scenario
export async function renderScenario(pane, sim) {
  const list = await api("/api/scenarios");
  const man = sim.manifest || {};
  pane.innerHTML = `<div class="cards"><div class="card"><h4>Run manifest</h4>${kv([["run id", esc(man.run_id)], ["scenario", esc(man.scenario_id)],
      ["seed", esc(man.seed)], ["TEP seed (G)", esc(man.tep_seed)], ["TEP backend", esc(man.tep_backend)], ["simulator", esc(man.simulator_version)],
      ["config hash", `<span title="${esc(man.configuration_hash)}">${esc((man.configuration_hash || "").slice(0, 16))}…</span>`],
      ["start", esc(man.simulation_start)], ["duration", hms(man.duration_seconds)]])}</div>
    <div class="card"><h4>${esc(sim.scenario?.name || "")}</h4><div class="fault-desc">${esc(sim.scenario?.description || "")}</div></div></div>
    <div class="section-title">Scenario library (scenarios/)</div>
    <table class="grid"><tr><th>Id</th><th>Name</th><th class="num">Seed</th><th>Duration</th><th class="num">Faults</th><th>File</th><th></th></tr>
    ${list.map(s => `<tr><td class="mono">${esc(s.id)}</td><td>${esc(s.name || s.error || "")}</td><td class="num">${esc(s.seed ?? "")}</td>
      <td>${hms(s.duration_seconds)}</td><td class="num">${esc(s.faults ?? "")}</td><td class="mono">${esc(s.file)}</td>
      <td><button class="small" data-action="load-scenario" data-id="${esc(s.id)}">load</button>
      <button class="small" data-action="dup-scenario" data-id="${esc(s.id)}">duplicate</button></td></tr>`).join("")}</table>
    <div class="section-title">Save current run as scenario (includes interactively created faults)</div>
    <div class="filters"><input id="save-id" placeholder="new scenario id"><input id="save-name" placeholder="name" style="width:260px">
      <button data-action="save-scenario">save</button></div>`;
}

// ------------------------------------------------------------------ fault injection (benchmark)
export async function renderFaults(pane, ui) {
  const [catalog, faults] = await Promise.all([ui.faultCatalog ? Promise.resolve(ui.faultCatalog) : api("/api/benchmark/faults/catalog"),
    api("/api/benchmark/faults")]);
  ui.faultCatalog = catalog;
  const sel = ui.faultForm || { type: "cooling_degradation" };
  const ft = catalog.find(c => c.type === sel.type) || catalog[0];
  const groups = { TEP_NATIVE_FAULT: [], ENTERPRISE_FAULT: [] };
  for (const c of catalog) groups[c.category].push(c);
  const opt = (c) => `<option value="${esc(c.type)}" ${c.type === ft.type ? "selected" : ""}>${esc(c.type)}${c.documented === false ? " (undocumented)" : ""}</option>`;
  const row = (f) => `<tr><td class="mono">${esc(f.id)}</td><td>${esc(f.type)}</td><td>${pill(f.category === "TEP_NATIVE_FAULT" ? "TEP native" : "enterprise", "")}</td>
    <td>${esc(f.target)}</td><td class="num">${fmt(f.severity, 2)}</td><td>${esc(f.progression.mode)}</td><td class="mono">${hms(f.start_time)}</td>
    <td>${f.duration ? hms(f.duration) : "open"}</td><td class="num">${fmt(f.intensity, 2)}</td><td>${pill(f.status, f.status === "ACTIVE" ? "critical" : f.status === "SCHEDULED" ? "warning" : "")}${f.remediated ? " " + pill("remediated", "good") : ""}</td>
    <td>${["CREATED", "SCHEDULED", "STOPPED"].includes(f.status) ? `<button class="small" data-action="fault" data-op="start" data-id="${esc(f.id)}">start</button>` : ""}
      ${f.status === "ACTIVE" ? `<button class="small" data-action="fault" data-op="stop" data-id="${esc(f.id)}">stop</button>` : ""}
      ${f.status !== "RESET" ? `<button class="small" data-action="fault" data-op="reset" data-id="${esc(f.id)}">reset</button>` : ""}</td></tr>`;
  const bucket = (st) => faults.filter(f => st.includes(f.status));
  const table = (rows) => `<table class="grid"><tr><th>Id</th><th>Type</th><th>Category</th><th>Target</th><th class="num">Severity</th><th>Progression</th>
    <th>Start</th><th>Duration</th><th class="num">Intensity</th><th>Status</th><th></th></tr>${rows.map(row).join("") || '<tr><td colspan="11" class="muted">none</td></tr>'}</table>`;
  const params = Object.entries(ft.parameters || {});
  pane.innerHTML = `<div class="bench-banner"><b>⚠ BENCHMARK OPERATOR FUNCTION.</b> Fault injection defines <i>causes</i>; the simulator determines
    consequences. These controls and fault ground truth are served only under <span class="mono">/api/benchmark</span> and must never be exposed
    to an agent interface.</div>
    <div class="form-grid">
      <label>Fault type</label><select id="ff-type" data-action="fault-type">
        <optgroup label="TEP native disturbances (teprob.f IDV)">${groups.TEP_NATIVE_FAULT.map(opt).join("")}</optgroup>
        <optgroup label="Enterprise faults">${groups.ENTERPRISE_FAULT.map(opt).join("")}</optgroup></select>
      <label>Target</label><select id="ff-target">${(ft.targets || []).map(t => `<option ${t === sel.target ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
      <label>Fault id</label><input id="ff-id" value="${esc(sel.id || "F-" + String(faults.length + 1).padStart(3, "0"))}">
      <label>Severity (0–1)</label><input id="ff-sev" type="number" min="0" max="1" step="0.05" value="${ft.category === "TEP_NATIVE_FAULT" ? 1 : (sel.severity ?? 0.5)}" ${ft.category === "TEP_NATIVE_FAULT" ? "disabled" : ""}>
      <label>Start time (s)</label><input id="ff-start" type="number" min="0" placeholder="empty = start manually" value="${esc(sel.start ?? "")}">
      <label>Duration (s)</label><input id="ff-dur" type="number" min="1" placeholder="empty = open-ended" value="${esc(sel.duration ?? "")}">
      <label>Progression</label><select id="ff-prog">${["step", ...(ft.supports_gradual ? ["gradual"] : []), "intermittent"].map(p => `<option ${p === sel.progression ? "selected" : ""}>${p}</option>`).join("")}</select>
      <label>Ramp / period (s)</label><input id="ff-ramp" type="number" min="1" placeholder="gradual ramp or intermittent period" value="${esc(sel.ramp ?? "")}">
      <label>Parameters (JSON)</label><textarea id="ff-params" class="full" placeholder='${esc(params.length ? JSON.stringify(Object.fromEntries(params.map(([k, v]) => [k, v.default]))) : "{}")}'>${esc(sel.params ?? "")}</textarea>
      <label>Direct indication</label><select id="ff-direct"><option value="false">no (hidden cause)</option><option value="true" ${ft.default_observability?.direct_indication ? "selected" : ""}>yes</option></select>
      <span></span><span></span>
    </div>
    <div class="fault-desc"><b>${esc(ft.type)}</b> · ${esc(ft.category)} · ${esc(ft.description)}${ft.persistent ? " <i>Persistent: effect remains after stop until repair or reset.</i>" : ""}
      ${params.length ? "<br>Parameters: " + params.map(([k, v]) => `<span class="mono">${esc(k)}</span> – ${esc(v.description)}`).join("; ") : ""}</div>
    <div class="filters"><button class="primary" data-action="fault-create" data-start="0">Inject (create / schedule)</button>
      <button data-action="fault-create" data-start="1">Inject &amp; start now</button></div>
    <div class="section-title">Active faults</div>${table(bucket(["ACTIVE"]))}
    <div class="section-title">Scheduled / created</div>${table(bucket(["SCHEDULED", "CREATED"]))}
    <div class="section-title">Completed (stopped / reset)</div>${table(bucket(["STOPPED", "RESET"]))}`;
}

export function readFaultForm() {
  const g = (id) => document.getElementById(id);
  const spec = { id: g("ff-id").value.trim(), type: g("ff-type").value, target: g("ff-target").value,
    severity: parseFloat(g("ff-sev").value || "1"), direct_indication: g("ff-direct").value === "true" };
  const start = g("ff-start").value, dur = g("ff-dur").value, ramp = g("ff-ramp").value, prog = g("ff-prog").value;
  if (start !== "") spec.trigger = { mode: "simulation_time", time: parseInt(start) };
  if (dur !== "") spec.duration = parseInt(dur);
  spec.progression = { mode: prog };
  if (ramp !== "") { if (prog === "gradual") spec.progression.ramp_s = parseInt(ramp); else spec.progression.period_s = parseInt(ramp); }
  const p = g("ff-params").value.trim();
  if (p) spec.parameters = JSON.parse(p);
  return spec;
}
