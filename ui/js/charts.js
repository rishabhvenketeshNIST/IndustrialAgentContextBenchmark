// Trend charts: one chart per measure (small multiples, one y-axis each), 2px lines,
// synchronised crosshair + tooltip across all charts. Setpoint / true-value overlays share
// the measure's unit, so they live on the same axis with a legend.
import { esc, fmt, hms } from "./api.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export class TrendChart {
  constructor(container, spec, onHover) {
    this.spec = spec;              // {id, label, unit, series:[{name, label, role}]}
    this.onHover = onHover;
    this.el = document.createElement("div");
    this.el.className = "chart";
    this.el.innerHTML = `<div class="title"><span>${esc(spec.label)} <span class="muted">${esc(spec.unit || "")}</span></span>
      <span class="now"></span></div><div class="legend"></div><canvas></canvas>`;
    container.appendChild(this.el);
    this.canvas = this.el.querySelector("canvas");
    this.nowEl = this.el.querySelector(".now");
    this.legendEl = this.el.querySelector(".legend");
    this.data = { time: [], series: {} };
    this.hoverT = null;
    this.canvas.addEventListener("mousemove", (e) => this._move(e));
    this.canvas.addEventListener("mouseleave", () => this.onHover(null, null));
  }

  setData(data, series) {
    this.data = data;
    this.series = series;
    const leg = series.length > 1 ? series.map(s =>
      `<span><i class="${s.role === "sp" ? "dash" : ""}" style="background:${this._color(s)}"></i>${esc(s.label)}</span>`).join("") : "";
    this.legendEl.innerHTML = leg;
    const main = data.series[series[0].name] || [];
    const last = [...main].reverse().find(v => v !== null && v !== undefined);
    this.nowEl.textContent = last !== undefined ? fmt(last, 3) : "–";
    this.draw();
  }

  _color(s) {
    return s.role === "sp" ? css("--series-2") : s.role === "true" ? css("--series-3") : css("--series-1");
  }

  _geom() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
    }
    return { dpr, w, h, l: 52, r: 22, t: 6, b: 18 };
  }

  _extent() {
    const t = this.data.time;
    let lo = Infinity, hi = -Infinity;
    for (const s of this.series) for (const v of this.data.series[s.name] || []) {
      if (v === null || v === undefined) continue;
      if (v < lo) lo = v; if (v > hi) hi = v;
    }
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (hi - lo < 1e-9) { const p = Math.abs(hi) * 0.01 || 0.5; lo -= p; hi += p; }
    const pad = (hi - lo) * 0.08;
    return { t0: t.length ? t[0] : 0, t1: t.length ? Math.max(t[t.length - 1], t[0] + 1) : 1, lo: lo - pad, hi: hi + pad };
  }

  draw() {
    const g = this._geom(), ctx = this.canvas.getContext("2d");
    ctx.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
    ctx.clearRect(0, 0, g.w, g.h);
    const { t0, t1, lo, hi } = this._extent();
    this.ext = { t0, t1, lo, hi };
    const X = t => g.l + (t - t0) / (t1 - t0) * (g.w - g.l - g.r);
    const Y = v => g.t + (1 - (v - lo) / (hi - lo)) * (g.h - g.t - g.b);
    this.X = X; this.Y = Y; this.g = g;
    ctx.font = "10px system-ui, sans-serif";
    ctx.fillStyle = css("--text-muted");
    ctx.strokeStyle = css("--grid");
    ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i++) {                      // recessive grid + y ticks
      const v = lo + (hi - lo) * i / 3, y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(g.l, y); ctx.lineTo(g.w - g.r, y); ctx.stroke();
      ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillText(fmt(v, Math.abs(hi - lo) < 1 ? 3 : 1), g.l - 4, y);
    }
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    for (let i = 0; i <= 4; i++) {
      const t = t0 + (t1 - t0) * i / 4;
      ctx.fillText(hms(t).slice(0, 5), X(t), g.h - g.b + 4);
    }
    ctx.strokeStyle = css("--axis");
    ctx.beginPath(); ctx.moveTo(g.l, g.h - g.b + 0.5); ctx.lineTo(g.w - g.r, g.h - g.b + 0.5); ctx.stroke();
    const time = this.data.time;
    for (const s of [...this.series].reverse()) {
      const vals = this.data.series[s.name] || [];
      ctx.strokeStyle = this._color(s);
      ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.setLineDash(s.role === "sp" ? [5, 4] : []);
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < time.length; i++) {
        const v = vals[i];
        if (v === null || v === undefined) { pen = false; continue; }
        const x = X(time[i]), y = Y(v);
        if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (this.hoverT !== null && time.length) {             // crosshair
      const x = X(this.hoverT);
      ctx.strokeStyle = css("--text-muted"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, g.t); ctx.lineTo(x, g.h - g.b); ctx.stroke();
      const i = this._index(this.hoverT);
      for (const s of this.series) {
        const v = (this.data.series[s.name] || [])[i];
        if (v === null || v === undefined) continue;
        ctx.fillStyle = this._color(s); ctx.strokeStyle = css("--surface-1"); ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(X(this.data.time[i]), Y(v), 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      }
    }
  }

  _index(t) {
    const time = this.data.time;
    let lo = 0, hi = time.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (time[m] < t) lo = m; else hi = m; }
    return Math.abs(time[lo] - t) <= Math.abs(time[hi] - t) ? lo : hi;
  }

  _move(e) {
    if (!this.data.time.length || !this.X) return;
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const { t0, t1 } = this.ext, g = this.g;
    const t = t0 + (x - g.l) / (g.w - g.l - g.r) * (t1 - t0);
    this.onHover(Math.max(t0, Math.min(t1, t)), e);
  }

  tooltipRows(t) {
    if (!this.data.time.length) return "";
    const i = this._index(t);
    return this.series.map(s => {
      const v = (this.data.series[s.name] || [])[i];
      return `<div><b>${esc(s.label)}</b> ${fmt(v, 3)} ${esc(this.spec.unit || "")}</div>`;
    }).join("");
  }

  setHover(t) { this.hoverT = t; this.draw(); }
  destroy() { this.el.remove(); }
}
