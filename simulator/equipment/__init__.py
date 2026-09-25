"""Equipment module: maintainable assets, health, status, redundancy changeover."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..common import ConfigError, deep_merge
from ..events import Event, EventType
from ..simulation.module import ModuleContext, SimulationModule
from ..state import CauseRef

STATUSES = ("RUNNING", "DEGRADED", "STANDBY", "STOPPED", "FAILED", "UNDER_MAINTENANCE")
RUN_STATES = ("RUN", "STANDBY", "STOP")


@dataclass
class Asset:
    id: str
    asset_type: str
    rated: Dict[str, float]
    thresholds: Dict[str, float]
    degradation: Dict[str, float]
    condition: Dict[str, float]
    maintenance: Dict
    redundancy_group: Optional[str]
    intrinsic_health: float
    desired_state: str
    run_hours: float
    under_maintenance: bool = False
    status: str = "STOPPED"
    health_band: str = "GOOD"
    changeover_due_s: Optional[int] = None


class EquipmentModule(SimulationModule):
    name = "equipment"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("equipment")
        defaults = cfg.get("defaults", {})
        self.changeover_delay = int(defaults.get("changeover_delay_s", 30))
        self.assets: Dict[str, Asset] = {}
        self._rng = ctx.rng.get("equipment")
        for aid, spec in (cfg.get("assets") or {}).items():
            if not ctx.state.has_entity(aid):
                raise ConfigError(f"Asset '{aid}' is not an equipment element in site.yaml")
            merged = deep_merge(defaults, spec)
            init = merged.get("initial", {})
            state = init.get("state", "RUN")
            if state not in RUN_STATES:
                raise ConfigError(f"Asset {aid}: initial state must be one of {RUN_STATES}")
            a = Asset(id=aid, asset_type=merged.get("asset_type", "asset"), rated=dict(merged.get("rated", {})),
                      thresholds=dict(merged["thresholds"]), degradation=dict(merged["degradation"]),
                      condition=dict(merged["condition_monitoring"]), maintenance=dict(merged.get("maintenance", {})),
                      redundancy_group=merged.get("redundancy_group"),
                      intrinsic_health=float(init.get("health", 1.0)), desired_state=state,
                      run_hours=float(init.get("run_hours", 0.0)))
            self.assets[aid] = a
            rec = ctx.state.entity(aid)
            rec.meta.update({"asset": True, "asset_type": a.asset_type, "redundancy_group": a.redundancy_group,
                             "rated": a.rated, "thresholds": a.thresholds, "maintenance": a.maintenance})
            rec.properties.update({"efficiency": 1.0})
            rec.properties.update({f"rated_{k}": float(v) for k, v in a.rated.items()})
            # simulation-model quantities (not plant measurements; vibration is the observable indicator)
            rec.meta["model_internal"] = ["health", "efficiency", "available_flow", "available_steam", "available_kw",
                                          "capability", "supply_fraction", "supply_temperature_rise"]
            rec.units.update({"health": "fraction", "vibration": "mm/s", "run_hours": "h"})
            self._resolve(a, publish=False)
        ctx.bus.subscribe(self._on_maintenance_started, [EventType.MAINTENANCE_STARTED])
        ctx.bus.subscribe(self._on_maintenance_completed, [EventType.MAINTENANCE_COMPLETED])

    # ------------------------------------------------------------------ helpers
    def health(self, a: Asset) -> float:
        dmg = self.ctx.state.fault_effects.value(a.id, "damage")
        return max(0.0, min(1.0, a.intrinsic_health - dmg))

    def _cause(self, a: Asset) -> Optional[CauseRef]:
        return self.ctx.state.causal.get(a.id)

    def _publish(self, etype: EventType, a: Asset, payload: dict, severity: str = "info",
                 cause: Optional[Event] = None) -> Event:
        ref = self._cause(a)
        kw = {}
        if cause is None and ref is not None:
            kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id}
        return self.ctx.bus.publish(etype, self.name, a.id, payload, cause=cause, severity=severity, **kw)

    def _resolve(self, a: Asset, publish: bool = True, cause: Optional[Event] = None,
                 reason: str = "") -> None:
        h = self.health(a)
        failed_by_fault = self.ctx.state.fault_effects.value(a.id, "failed") > 0
        if a.under_maintenance:
            status = "UNDER_MAINTENANCE"
        elif failed_by_fault or h <= a.thresholds["failed"]:
            status = "FAILED"
        elif a.desired_state == "RUN":
            status = "DEGRADED" if h < a.thresholds["degraded"] else "RUNNING"
        elif a.desired_state == "STANDBY":
            status = "STANDBY"
        else:
            status = "STOPPED"
        running = status in ("RUNNING", "DEGRADED")
        props = self.ctx.state.entity(a.id).properties
        c = a.condition
        vib = c["vibration_base_mm_s"] + c["vibration_gain_mm_s"] * (1.0 - h) ** 2
        if running:
            vib += float(self._rng.normal(0.0, c["vibration_noise_mm_s"]))
        else:
            vib = 0.0
        props.update({"health": round(h, 6), "status": status, "is_running": 1.0 if running else 0.0,
                      "desired_state": a.desired_state, "run_hours": round(a.run_hours, 4),
                      "vibration": round(max(vib, 0.0), 4), "role": self._role(a)})
        band = "GOOD" if h >= a.thresholds["degraded"] else ("POOR" if h > a.thresholds["failed"] else "FAILED")
        if publish and band != a.health_band and band != "GOOD" and band != "FAILED":
            self._publish(EventType.EQUIPMENT_DEGRADED, a, {"health": round(h, 4), "band": band,
                                                             "threshold": a.thresholds["degraded"]},
                          severity="warning", cause=cause)
        a.health_band = band
        if publish and status != a.status:
            self._publish(EventType.EQUIPMENT_STATE_CHANGED, a,
                          {"old": a.status, "new": status, "reason": reason, "health": round(h, 4)},
                          severity="warning" if status in ("FAILED", "DEGRADED") else "info", cause=cause)
            if status == "FAILED":
                self._publish(EventType.EQUIPMENT_FAILED, a, {"health": round(h, 4), "reason": reason
                                                               or ("fault" if failed_by_fault else "worn out")},
                              severity="critical", cause=cause)
        a.status = status

    def _role(self, a: Asset) -> str:
        if not a.redundancy_group:
            return "single"
        return "duty" if a.desired_state == "RUN" else "standby"

    def group(self, gid: str) -> List[Asset]:
        return [x for x in self.assets.values() if x.redundancy_group == gid]

    # ------------------------------------------------------------------ simulation
    def pre_step(self, t: int) -> None:
        dt_h = 1.0 / 3600.0
        for a in self.assets.values():
            if a.status in ("RUNNING", "DEGRADED"):
                a.run_hours += dt_h
                rate = a.degradation.get("rate_per_h", 0.0)
                noise = a.degradation.get("noise_std_per_h", 0.0)
                wear = rate * dt_h + (float(self._rng.normal(0.0, noise)) * dt_h ** 0.5 if noise else 0.0)
                a.intrinsic_health = max(0.0, min(1.0, a.intrinsic_health - max(wear, 0.0)))
            self._resolve(a)
        self._redundancy(t)

    def _redundancy(self, t: int) -> None:
        groups = sorted({a.redundancy_group for a in self.assets.values() if a.redundancy_group})
        for gid in groups:
            members = self.group(gid)
            if any(m.status in ("RUNNING", "DEGRADED") for m in members):
                for m in members:
                    m.changeover_due_s = None
                continue
            candidates = [m for m in members if m.status == "STANDBY"]
            if not candidates:
                continue
            best = sorted(candidates, key=lambda m: (-self.health(m), m.id))[0]
            if best.changeover_due_s is None:
                best.changeover_due_s = t + self.changeover_delay
            elif t >= best.changeover_due_s:
                best.changeover_due_s = None
                best.desired_state = "RUN"
                self._resolve(best, reason="automatic changeover (no running unit in group)")

    # ------------------------------------------------------------------ commands
    def command(self, asset_id: str, state: str, actor: str = "operator") -> None:
        if asset_id not in self.assets:
            raise KeyError(f"'{asset_id}' is not a maintainable asset")
        if state not in RUN_STATES:
            raise ValueError(f"state must be one of {RUN_STATES}")
        a = self.assets[asset_id]
        if a.under_maintenance and state == "RUN":
            raise ValueError(f"{asset_id} is under maintenance")
        a.desired_state = state
        self._resolve(a, reason=f"command by {actor}")

    def _on_maintenance_started(self, ev: Event) -> None:
        a = self.assets.get(ev.target or "")
        if a is None or not ev.payload.get("isolates_equipment", True):
            return
        was_running = a.desired_state == "RUN"
        a.under_maintenance = True
        a.desired_state = "STOP"
        if was_running and a.redundancy_group:
            standby = [m for m in self.group(a.redundancy_group) if m is not a and m.status == "STANDBY"]
            if standby:
                s = sorted(standby, key=lambda m: (-self.health(m), m.id))[0]
                s.desired_state = "RUN"
                self._resolve(s, cause=ev, reason=f"planned changeover for maintenance of {a.id}")
        self._resolve(a, cause=ev, reason="isolated for maintenance")

    def _on_maintenance_completed(self, ev: Event) -> None:
        a = self.assets.get(ev.target or "")
        if a is None or not ev.payload.get("isolates_equipment", True):
            return
        restore = float(ev.payload.get("restore_health", a.maintenance.get("restore_health", 0.98)))
        old = self.health(a)
        a.intrinsic_health = max(a.intrinsic_health, restore) if ev.payload.get("kind") == "inspection" \
            else restore
        cleared = self.ctx.state.fault_effects.clear_target(a.id, ["damage", "efficiency_loss", "failed"])
        a.under_maintenance = False
        others_running = a.redundancy_group and any(
            m.status in ("RUNNING", "DEGRADED") for m in self.group(a.redundancy_group) if m is not a)
        a.desired_state = "STANDBY" if others_running else "RUN"
        self._resolve(a, cause=ev, reason="returned to service after maintenance")
        self.ctx.bus.publish(EventType.EQUIPMENT_REPAIRED, self.name, a.id,
                             {"health_before": round(old, 4), "health_after": round(self.health(a), 4),
                              "work_order": ev.payload.get("work_order_id"), "cleared_fault_effects": cleared},
                             cause=ev)
        self.ctx.state.causal.clear(a.id)

    def summary(self) -> dict:
        return {aid: {k: self.ctx.state.get(aid, k) for k in ("status", "health", "vibration", "role")}
                for aid in self.assets}
