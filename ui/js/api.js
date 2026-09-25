// REST client and small DOM helpers.
export async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error((data && data.error) || (data && data.detail && JSON.stringify(data.detail)) || r.statusText);
  return data;
}

export const post = (path, body = {}) => api(path, { method: "POST", body });

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function fmt(v, d = 2) {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "number") {
    if (!isFinite(v)) return "–";
    const a = Math.abs(v);
    if (a >= 10000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    if (a >= 100) return v.toFixed(Math.min(d, 1));
    return v.toFixed(d);
  }
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function hms(s) {
  if (s === null || s === undefined) return "–";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`;
}

export function toast(msg, err = false) {
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "");
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), err ? 6000 : 2500);
}

// status -> status-colour class (status colours are reserved for state and always paired with text)
export function statusClass(s) {
  switch (s) {
    case "FAILED": case "UNAVAILABLE": case "CRITICAL": case "REJECTED": case "EMPTY": case "SHUTDOWN": case "DOWN":
    case "BLOCKED": case "FAIL":
      return "critical";
    case "DEGRADED": case "CONSTRAINED": case "HIGH": case "QUARANTINE": case "LOW": case "PAUSED": case "WAITING_PARTS":
    case "WAITING_TECHNICIAN": case "REDUCED_RATE": case "ACTIVE_UNACK": case "DELAYED": case "RTN_UNACK":
      return "warning";
    case "MEDIUM": case "ACTIVE_ACK": return "serious";
    case "UNDER_MAINTENANCE": case "IN_PROGRESS": case "SCHEDULED": return "maint";
    case "RUNNING": case "NORMAL": case "RELEASED": case "COMPLETED": case "PASS": case "OK": return "good";
    default: return "";
  }
}

export function pill(text, cls) {
  return `<span class="pill ${cls ?? statusClass(text)}">${esc(text)}</span>`;
}
