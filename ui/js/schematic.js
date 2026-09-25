// Operational process schematic of the TEP flowsheet (Downs & Vogel): feeds -> reactor -> condenser ->
// separator -> compressor/recycle + purge; separator liquid -> stripper -> product. Utilities shown as
// supply boxes coupled to the units they serve. Not a P&ID.
import { esc, fmt } from "./api.js";

const NS = "http://www.w3.org/2000/svg";

// ---- units (entity id -> shape) ------------------------------------------------------------
const UNITS = [
  { id: "EM-REACTOR", name: "Reactor R-101", x: 290, y: 95, w: 130, h: 175, shape: "vessel" },
  { id: "EM-RX-COOLING", name: "Cooling bundle", x: 305, y: 222, w: 100, h: 32, shape: "rect", small: true },
  { id: "EM-AGITATOR", name: "Agitator", x: 330, y: 60, w: 50, h: 24, shape: "rect", small: true },
  { id: "EM-CONDENSER", name: "Condenser E-101", x: 470, y: 115, w: 110, h: 50, shape: "rect" },
  { id: "EM-SEPARATOR", name: "Separator V-102", x: 630, y: 95, w: 110, h: 130, shape: "vessel" },
  { id: "EM-COMPRESSOR", name: "Compressor K-101", x: 800, y: 95, w: 110, h: 60, shape: "rect" },
  { id: "EM-PURGE", name: "Purge", x: 960, y: 165, w: 90, h: 40, shape: "rect", small: true },
  { id: "EM-STRIPPER", name: "Stripper C-101", x: 630, y: 300, w: 110, h: 170, shape: "vessel" },
  { id: "EM-STRIPPER-STEAM", name: "Reboiler (steam)", x: 760, y: 410, w: 90, h: 32, shape: "rect", small: true },
  { id: "SU-TK-501", name: "Product TK-501", x: 960, y: 470, w: 120, h: 55, shape: "rect" },
  { id: "UT-CW-REACTOR", name: "Reactor CW", x: 20, y: 330, w: 150, h: 58, shape: "utility" },
  { id: "UT-CW-CONDENSER", name: "Condenser CW", x: 450, y: 215, w: 150, h: 58, shape: "utility" },
  { id: "UT-STEAM", name: "LP steam", x: 890, y: 395, w: 140, h: 58, shape: "utility" },
  { id: "UT-POWER", name: "Electrical power", x: 960, y: 60, w: 150, h: 58, shape: "utility" },
  { id: "EM-FEED-A", name: "A feed (1)", x: 20, y: 72, w: 90, h: 28, shape: "feed" },
  { id: "EM-FEED-D", name: "D feed (2)", x: 20, y: 132, w: 90, h: 28, shape: "feed" },
  { id: "EM-FEED-E", name: "E feed (3)", x: 20, y: 192, w: 90, h: 28, shape: "feed" },
  { id: "EM-FEED-AC", name: "A/C feed (4)", x: 20, y: 436, w: 90, h: 28, shape: "feed" },
];

// ---- pipes ---------------------------------------------------------------------------------
const PIPES = [
  { d: [[110, 86], [250, 86], [250, 175], [290, 175]] },                 // A
  { d: [[110, 146], [250, 146]] },                                        // D
  { d: [[110, 206], [250, 206], [250, 175]] },                            // E
  { d: [[420, 110], [450, 110], [450, 140], [470, 140]] },                // reactor vapour -> condenser
  { d: [[580, 140], [630, 140]] },                                        // condenser -> separator
  { d: [[740, 125], [800, 125]] },                                        // separator vapour -> compressor
  { d: [[770, 125], [770, 185], [960, 185]] },                            // purge takeoff
  { d: [[855, 95], [855, 35], [250, 35], [250, 86]] },                    // recycle header (stream 8)
  { d: [[685, 225], [685, 300]] },                                        // separator liquid -> stripper
  { d: [[630, 320], [560, 320], [560, 290], [250, 290], [250, 206]] },    // stripper overhead (stream 5)
  { d: [[110, 450], [630, 450]] },                                        // A/C -> stripper
  { d: [[685, 470], [685, 500], [960, 500]] },                            // product (stream 11)
  { d: [[890, 425], [850, 425]], cls: "steam" },                          // steam
  { d: [[170, 355], [355, 355], [355, 254]], cls: "cw" },                 // reactor CW
  { d: [[525, 215], [525, 165]], cls: "cw" },                             // condenser CW
  { d: [[960, 90], [910, 110]], cls: "power" },                           // power -> compressor
];

// ---- valves (XMV) ----------------------------------------------------------------------------
const VALVES = [
  { xmv: 3, id: "CM-FV-03", x: 170, y: 86 }, { xmv: 1, id: "CM-FV-01", x: 170, y: 146 },
  { xmv: 2, id: "CM-FV-02", x: 170, y: 206 }, { xmv: 4, id: "CM-FV-04", x: 170, y: 450 },
  { xmv: 5, id: "CM-RV-05", x: 760, y: 35 }, { xmv: 6, id: "CM-PV-06", x: 900, y: 185 },
  { xmv: 7, id: "CM-LV-07", x: 685, y: 262 }, { xmv: 8, id: "CM-LV-08", x: 780, y: 500 },
  { xmv: 9, id: "CM-SV-09", x: 870, y: 425 }, { xmv: 10, id: "CM-TV-10", x: 230, y: 355 },
  { xmv: 11, id: "CM-CV-11", x: 525, y: 192 }, { xmv: 12, id: "CM-SC-12", x: 395, y: 72, drive: true },
];

// ---- live value labels ([var, x, y, label]) ------------------------------------------------------
const VALUES = [
  ["XMEAS(1)", 120, 60, "FI-01"], ["XMEAS(2)", 120, 120, "FI-02"], ["XMEAS(3)", 120, 180, "FI-03"],
  ["XMEAS(4)", 120, 424, "FI-04"], ["XMEAS(6)", 115, 262, "FI-06 rx feed"],
  ["XMEAS(7)", 428, 185, "PI-07"], ["XMEAS(8)", 428, 208, "LI-08"], ["XMEAS(9)", 428, 231, "TI-09"],
  ["XMEAS(21)", 250, 395, "TI-21 CW out"], ["XMEAS(22)", 600, 180, "TI-22"],
  ["XMEAS(11)", 750, 160, "TI-11"], ["XMEAS(12)", 750, 183, "LI-12"], ["XMEAS(13)", 750, 206, "PI-13"],
  ["XMEAS(14)", 695, 280, "FI-14"], ["XMEAS(5)", 800, 60, "FI-05 recycle"], ["XMEAS(20)", 800, 165, "JI-20 kW"],
  ["XMEAS(10)", 960, 215, "FI-10 purge"], ["XMEAS(30)", 960, 238, "AI-30 B"],
  ["XMEAS(15)", 505, 340, "LI-15"], ["XMEAS(16)", 505, 363, "PI-16"], ["XMEAS(18)", 505, 386, "TI-18"],
  ["XMEAS(19)", 760, 380, "FI-19 steam"], ["XMEAS(17)", 700, 520, "FI-17 prod"],
  ["XMEAS(40)", 830, 525, "AI-40 G"], ["XMEAS(41)", 830, 548, "AI-41 H"], ["XMEAS(38)", 960, 540, "AI-38 E"],
];

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

export class Schematic {
  constructor(svg, onSelect, onAddTrend) {
    this.svg = svg; this.onSelect = onSelect; this.onAddTrend = onAddTrend;
    this.units = {}; this.valves = {}; this.values = {};
    this._build();
  }

  _build() {
    const s = this.svg;
    s.innerHTML = "";
    const defs = el("defs", {}, s);
    const m = el("marker", { id: "arr", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" }, defs);
    el("path", { d: "M0,0 L10,5 L0,10 z", class: "arrow" }, m);
    for (const p of PIPES) {
      el("polyline", { points: p.d.map(q => q.join(",")).join(" "),
        class: "pipe" + (p.cls === "cw" ? " cw" : p.cls === "power" ? " power" : ""), "marker-end": "url(#arr)" }, s);
    }
    el("text", { x: 470, y: 30, class: "lbl" }, s).textContent = "recycle (stream 8)";
    el("text", { x: 380, y: 285, class: "lbl" }, s).textContent = "stripper overhead (stream 5)";
    el("text", { x: 880, y: 495, class: "lbl" }, s).textContent = "product (11)";
    for (const u of UNITS) {
      const g = el("g", { class: "unit", "data-id": u.id }, s);
      if (u.shape === "vessel") {
        el("rect", { x: u.x, y: u.y, width: u.w, height: u.h, rx: 28 }, g);
      } else {
        el("rect", { x: u.x, y: u.y, width: u.w, height: u.h, rx: u.shape === "utility" ? 4 : 6,
          "stroke-dasharray": u.shape === "utility" ? "4 3" : null }, g);
      }
      const tx = u.x + u.w / 2;
      el("text", { x: tx, y: u.y + (u.small ? 20 : 20), "text-anchor": "middle", class: "uname" }, g).textContent = u.name;
      const st = el("text", { x: tx, y: u.y + (u.small ? 31 : 36), "text-anchor": "middle", class: "ustat" }, g);
      if (u.small) st.setAttribute("class", "ustat hidden");
      g.addEventListener("click", () => this.onSelect(u.id));
      this.units[u.id] = { g, st, u };
    }
    for (const v of VALVES) {
      const g = el("g", { class: "valve", "data-id": v.id, transform: `translate(${v.x},${v.y})`, style: "cursor:pointer" }, s);
      if (v.drive) el("circle", { r: 9, fill: "var(--surface-1)", stroke: "var(--text-secondary)", "stroke-width": 1.5 }, g);
      else el("path", { d: "M-10,-7 L10,7 L10,-7 L-10,7 Z" }, g);
      const t = el("text", { x: 0, y: -11, "text-anchor": "middle", class: "val" }, g);
      g.addEventListener("click", () => this.onSelect(v.id));
      this.valves[v.xmv] = { g, t, v };
    }
    for (const [vid, x, y, label] of VALUES) {
      const g = el("g", { class: "valg", transform: `translate(${x},${y})` }, s);
      el("rect", { x: -2, y: -12, width: 132, height: 17, rx: 3, class: "valbg" }, g);
      const lt = el("text", { x: 2, y: 1, class: "lbl" }, g); lt.textContent = label;
      const vt = el("text", { x: 126, y: 1, "text-anchor": "end", class: "val" }, g);
      g.style.cursor = "pointer";
      g.addEventListener("click", () => this.onAddTrend(vid));
      el("title", {}, g).textContent = `${vid} - click to trend`;
      this.values[vid] = { g, vt };
    }
    this.banner = el("g", { class: "shutdown-banner hidden" }, s);
    el("rect", { x: 330, y: 500, width: 380, height: 34, rx: 6 }, this.banner);
    this.bannerText = el("text", { x: 520, y: 522, "text-anchor": "middle" }, this.banner);
  }

  update(snap, selected) {
    const p = snap.process;
    if (!p) return;
    const units = {};
    for (const [id, props] of Object.entries(snap.equipment || {})) units[id] = props.status;
    for (const [id, u] of Object.entries(snap.utilities || {})) units[id] = u.properties.status;
    const alarmsBy = {};
    for (const a of snap.alarms || []) if (a.active) alarmsBy[a.source] = a;
    for (const [id, o] of Object.entries(this.units)) {
      const status = units[id];
      o.g.setAttribute("class", `unit${status ? " st-" + status : ""}${selected === id ? " sel" : ""}`);
      let text = status || "";
      if (id.startsWith("UT-")) {
        const u = snap.utilities[id].properties;
        text = `${status} · ${fmt(100 * (u.availability ?? 0), 0)}% avail · ${fmt(100 * (u.utilization ?? 0), 0)}% used`;
      } else if (id === "SU-TK-501") {
        text = "";
      }
      o.st.textContent = text;
    }
    for (const [idx, o] of Object.entries(this.valves)) {
      const v = p.xmv[idx - 1];
      o.t.textContent = `${fmt(v, 1)}%`;
      const sat = idx <= 11 && (v >= 99.9 || v <= 0.1);
      o.g.setAttribute("class", `valve${sat ? " sat" : ""}`);
      o.g.querySelector("title")?.remove();
      el("title", {}, o.g).textContent = `XMV(${idx}) ${fmt(v, 2)} %${sat ? " (saturated)" : ""}`;
    }
    for (const [vid, o] of Object.entries(this.values)) {
      const i = parseInt(vid.slice(6)) - 1;
      const q = p.quality[i];
      const alarm = alarmsBy[vid];
      o.vt.textContent = (q === "BAD" ? "? " : alarm ? "▲ " : "") + fmt(p.xmeas[i], 2);
      o.g.setAttribute("class", "valg" + (alarm ? " val-alarm" : "") + (q === "BAD" ? " val-bad" : ""));
    }
    const sd = p.shutdown;
    this.banner.setAttribute("class", "shutdown-banner" + (sd ? "" : " hidden"));
    if (sd) this.bannerText.textContent = `⛔ PROCESS SHUTDOWN - ${p.shutdown_reason}`;
  }
}
