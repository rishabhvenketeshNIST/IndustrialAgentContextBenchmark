"""Maintenance module: requests, work orders, planning, technicians, spare parts, execution.

Work order life cycle::

    REQUESTED -> WAITING_PARTS / WAITING_TECHNICIAN -> SCHEDULED -> IN_PROGRESS -> COMPLETED
                                                                              (or CANCELLED)

Kinds: corrective (repairs and isolates the asset), inspection (walks a utility
system and raises corrective orders for under-performing assets) and planned
(preventive tasks from the scenario). Completion is published as
MAINTENANCE_COMPLETED; the equipment module restores the asset condition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional

from ..common import ConfigError, fmt_hms
from ..events import Event, EventType
from ..simulation.module import ModuleContext, SimulationModule

OPEN = ("REQUESTED", "WAITING_PARTS", "WAITING_TECHNICIAN", "SCHEDULED", "IN_PROGRESS")


@dataclass
class Technician:
    id: str
    name: str
    skills: List[str]
    shift_start_h: int
    shift_end_h: int
    status: str = "AVAILABLE"         # AVAILABLE | BUSY | OFF_SHIFT
    work_order: Optional[str] = None

    def on_shift(self, hour: float) -> bool:
        if self.shift_start_h <= self.shift_end_h:
            return self.shift_start_h <= hour < self.shift_end_h
        return hour >= self.shift_start_h or hour < self.shift_end_h

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "skills": list(self.skills),
                "shift": f"{self.shift_start_h:02d}:00-{self.shift_end_h:02d}:00", "status": self.status,
                "work_order": self.work_order}


@dataclass
class WorkOrder:
    wo_id: str
    asset_id: str
    kind: str                     # corrective | inspection | planned
    priority: int
    description: str
    requested_s: int
    requested_by: str
    skill: str
    duration_s: int
    parts: Dict[str, int] = field(default_factory=dict)
    restore_health: Optional[float] = None
    status: str = "REQUESTED"
    technician: Optional[str] = None
    scheduled_start_s: Optional[int] = None
    actual_start_s: Optional[int] = None
    completed_s: Optional[int] = None
    wait_reason: str = ""
    findings: List[str] = field(default_factory=list)
    not_before_s: int = 0
    correlation_id: Optional[str] = None
    request_event: Optional[str] = None

    @property
    def isolates_equipment(self) -> bool:
        return self.kind in ("corrective", "planned")

    def to_dict(self) -> dict:
        return {"wo_id": self.wo_id, "asset_id": self.asset_id, "kind": self.kind, "priority": self.priority,
                "description": self.description, "requested_s": self.requested_s,
                "requested_hms": fmt_hms(self.requested_s), "requested_by": self.requested_by,
                "skill": self.skill, "duration_s": self.duration_s, "parts": dict(self.parts),
                "status": self.status, "technician": self.technician, "scheduled_start_s": self.scheduled_start_s,
                "actual_start_s": self.actual_start_s, "completed_s": self.completed_s,
                "wait_reason": self.wait_reason, "findings": list(self.findings)}


class MaintenanceModule(SimulationModule):
    name = "maintenance"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("maintenance")
        self.response = {int(k): int(v) for k, v in (cfg.get("response_time_s") or {}).items()}
        self.jitter = float(cfg.get("duration_jitter_fraction", 0.1))
        self.req_policy = cfg.get("requests", {})
        self.inspection = cfg.get("inspection", {})
        self._rng = ctx.rng.get("maintenance")
        self._seq = 0
        techs = ctx.state.collection("technicians")
        for t in cfg.get("technicians", []):
            techs[t["id"]] = Technician(t["id"], t["name"], list(t.get("skills", [])),
                                        int(t["shift"]["start_h"]), int(t["shift"]["end_h"]))
        if not techs:
            raise ConfigError("At least one technician is required")
        ctx.state.collection("work_orders")
        self._planned = sorted(ctx.config.get("planned_maintenance", []) or [],
                               key=lambda p: (int(p["start_s"]), p["asset_id"]))
        for p in self._planned:
            if not ctx.state.has_entity(p["asset_id"]):
                raise ConfigError(f"Planned maintenance for unknown asset {p['asset_id']}")
        ctx.bus.subscribe(self._on_alarm, [EventType.ALARM_ACTIVATED])
        ctx.bus.subscribe(self._on_failure, [EventType.EQUIPMENT_FAILED])

    # ------------------------------------------------------------------ helpers
    def _hour(self, t: int) -> float:
        dt = self.ctx.clock.start + timedelta(seconds=t)
        return dt.hour + dt.minute / 60.0

    def _asset_meta(self, asset_id: str) -> dict:
        return self.ctx.state.entity(asset_id).meta

    def _open_for(self, asset_id: str) -> Optional[WorkOrder]:
        for wo in self.ctx.state.collection("work_orders").values():
            if wo.asset_id == asset_id and wo.status in OPEN:
                return wo
        return None

    def request(self, asset_id: str, kind: str, priority: int, requested_by: str, description: str = "",
                cause: Optional[Event] = None, duration_s: Optional[int] = None) -> WorkOrder:
        st = self.ctx.state
        if not st.has_entity(asset_id):
            raise KeyError(f"Unknown asset {asset_id}")
        if kind not in ("corrective", "inspection", "planned"):
            raise ValueError("kind must be corrective | inspection | planned")
        if not 1 <= int(priority) <= 4:
            raise ValueError("priority must be 1..4")
        existing = self._open_for(asset_id)
        if existing is not None:
            return existing
        meta = self._asset_meta(asset_id)
        mcfg = meta.get("maintenance", {}) or {}
        if kind == "inspection":
            skill = self.inspection.get("skill", "mechanical")
            dur = int(float(self.inspection.get("duration_min", 20)) * 60)
            parts, restore = {}, None
            desc = description or f"Inspect {st.entity(asset_id).name}"
        else:
            if not meta.get("asset"):
                raise ValueError(f"{asset_id} is not a maintainable asset")
            skill = mcfg.get("skill", "mechanical")
            dur = int(float(mcfg.get("duration_min", 60)) * 60)
            parts = {k: int(v) for k, v in (mcfg.get("parts") or {}).items()}
            restore = mcfg.get("restore_health")
            desc = description or mcfg.get("task", "Corrective maintenance")
        if duration_s is not None:
            dur = int(duration_s)
        if self.jitter > 0:
            dur = int(round(dur * (1.0 + float(self._rng.uniform(-self.jitter, self.jitter)))))
        self._seq += 1
        t = self.ctx.clock.time_s
        wo = WorkOrder(f"WO-{self._seq:05d}", asset_id, kind, int(priority), desc, t, requested_by, skill, dur,
                       parts, restore)
        wo.not_before_s = t + self.response.get(int(priority), 3600)
        st.collection("work_orders")[wo.wo_id] = wo
        ev = self.ctx.bus.publish(EventType.MAINTENANCE_REQUESTED, self.name, asset_id,
                                  {"work_order_id": wo.wo_id, "kind": kind, "priority": wo.priority,
                                   "description": desc, "requested_by": requested_by}, cause=cause,
                                  severity="warning" if priority <= 2 else "info")
        wo.correlation_id, wo.request_event = ev.correlation_id, ev.event_id
        return wo

    def _pub(self, etype: EventType, wo: WorkOrder, payload: dict, severity: str = "info") -> Event:
        base = {"work_order_id": wo.wo_id, "kind": wo.kind, "isolates_equipment": wo.isolates_equipment}
        base.update(payload)
        return self.ctx.bus.publish(etype, self.name, wo.asset_id, base, correlation_id=wo.correlation_id,
                                    causation_id=wo.request_event, severity=severity)

    # ------------------------------------------------------------------ event triggers
    def _on_alarm(self, ev: Event) -> None:
        kind = ev.payload.get("maintenance")
        if not kind:
            return
        target = ev.payload.get("maintenance_target") or ev.payload.get("equipment_id")
        if not target:
            return
        prio = int(ev.payload.get("maintenance_priority", 2))
        self.request(target, kind, prio, f"alarm {ev.payload.get('alarm_id')}",
                     f"{'Inspect' if kind == 'inspection' else 'Repair'}: {ev.payload.get('message', '')}".strip(),
                     cause=ev)

    def _on_failure(self, ev: Event) -> None:
        pol = self.req_policy.get("on_equipment_failure")
        if pol and ev.target and self._asset_meta(ev.target).get("asset"):
            self.request(ev.target, pol.get("kind", "corrective"), int(pol.get("priority", 1)),
                         "equipment failure", f"Repair failed {self.ctx.state.entity(ev.target).name}", cause=ev)

    # ------------------------------------------------------------------ simulation
    def pre_step(self, t: int) -> None:
        st = self.ctx.state
        while self._planned and int(self._planned[0]["start_s"]) <= t:
            p = self._planned.pop(0)
            self.request(p["asset_id"], "planned", int(p.get("priority", 3)), "maintenance plan",
                         p.get("task", "Planned maintenance"), duration_s=p.get("duration_s"))
        hour = self._hour(t)
        techs = st.collection("technicians")
        for tech in techs.values():
            if tech.status != "BUSY":
                tech.status = "AVAILABLE" if tech.on_shift(hour) else "OFF_SHIFT"
        delay = int(st.fault_effects.value("MAINTENANCE", "response_delay_s"))
        for wo in sorted(st.collection("work_orders").values(), key=lambda w: (w.priority, w.requested_s, w.wo_id)):
            if wo.status == "IN_PROGRESS":
                if t >= wo.actual_start_s + wo.duration_s:
                    self._complete(wo, t)
                continue
            if wo.status not in OPEN:
                continue
            self._plan(wo, t, delay)

    def _plan(self, wo: WorkOrder, t: int, delay: int) -> None:
        st = self.ctx.state
        parts = st.collection("spare_parts")
        missing = []
        for pid, q in sorted(wo.parts.items()):
            p = parts.get(pid)
            reserved_here = q if wo.status == "SCHEDULED" else 0
            if p is None or p.blocked or (p.on_hand - p.reserved + reserved_here) < q:
                missing.append(pid)
        if missing:
            self._wait(wo, "WAITING_PARTS", f"spare parts unavailable: {', '.join(missing)}")
            return
        if wo.status in ("REQUESTED", "WAITING_PARTS", "WAITING_TECHNICIAN"):
            tech = self._pick_technician(wo.skill)
            if tech is None:
                self._wait(wo, "WAITING_TECHNICIAN", f"no available technician with skill '{wo.skill}'")
                return
            wo.technician = tech.id
            tech.status, tech.work_order = "BUSY", wo.wo_id
            wo.status = "SCHEDULED"
            wo.wait_reason = ""
            wo.scheduled_start_s = max(t, wo.not_before_s + delay)
            self._pub(EventType.MAINTENANCE_SCHEDULED, wo, {"technician": tech.id, "parts": dict(wo.parts),
                                                             "scheduled_start_s": wo.scheduled_start_s,
                                                             "duration_s": wo.duration_s})
            return
        if wo.status == "SCHEDULED":
            wo.scheduled_start_s = max(wo.scheduled_start_s or t, wo.not_before_s + delay)
            if t >= wo.scheduled_start_s:
                wo.status, wo.actual_start_s = "IN_PROGRESS", t
                self._pub(EventType.MAINTENANCE_STARTED, wo, {"technician": wo.technician, "parts": dict(wo.parts),
                                                               "duration_s": wo.duration_s,
                                                               "description": wo.description})

    def _wait(self, wo: WorkOrder, status: str, reason: str) -> None:
        if wo.status != status or wo.wait_reason != reason:
            wo.status, wo.wait_reason = status, reason
            self._pub(EventType.MAINTENANCE_WAITING, wo, {"status": status, "reason": reason}, severity="warning")

    def _pick_technician(self, skill: str) -> Optional[Technician]:
        techs = sorted(self.ctx.state.collection("technicians").values(), key=lambda x: x.id)
        for tech in techs:
            if tech.status == "AVAILABLE" and skill in tech.skills:
                return tech
        return None

    def _complete(self, wo: WorkOrder, t: int) -> None:
        st = self.ctx.state
        wo.status, wo.completed_s = "COMPLETED", t
        tech = st.collection("technicians").get(wo.technician)
        if tech is not None:
            tech.status, tech.work_order = "AVAILABLE", None
        if wo.kind == "inspection":
            wo.findings = self._inspect(wo)
        ev = self._pub(EventType.MAINTENANCE_COMPLETED, wo,
                       {"technician": wo.technician, "restore_health": wo.restore_health,
                        "findings": list(wo.findings), "duration_s": wo.duration_s})
        for asset in wo.findings:
            self.request(asset, "corrective", wo.priority, f"inspection {wo.wo_id}",
                         f"Repair under-performing {st.entity(asset).name} (found by {wo.wo_id})", cause=ev)

    def _inspect(self, wo: WorkOrder) -> List[str]:
        st = self.ctx.state
        th = self.inspection.get("finding_thresholds", {})
        scope = [wo.asset_id] + [e.id for e in st.hierarchy.descendants(wo.asset_id)] \
            if wo.asset_id in st.hierarchy else [wo.asset_id]
        found = []
        for aid in scope:
            if not st.has_entity(aid) or not st.entity(aid).meta.get("asset"):
                continue
            eff = st.get(aid, "efficiency")
            h = st.get(aid, "health")
            running = st.get(aid, "is_running", 0.0) > 0
            if running and ((eff is not None and eff < th.get("efficiency", 0.85)) or
                            (h is not None and h < th.get("health", 0.75))):
                found.append(aid)
        return found

    def cancel(self, wo_id: str, actor: str = "operator") -> WorkOrder:
        wo = self.ctx.state.collection("work_orders").get(wo_id)
        if wo is None:
            raise KeyError(f"Unknown work order {wo_id}")
        if wo.status not in OPEN or wo.status == "IN_PROGRESS":
            raise ValueError(f"Work order {wo_id} cannot be cancelled in status {wo.status}")
        tech = self.ctx.state.collection("technicians").get(wo.technician) if wo.technician else None
        if tech is not None and tech.work_order == wo_id:
            tech.status, tech.work_order = "AVAILABLE", None
        wo.status = "CANCELLED"
        self._pub(EventType.MAINTENANCE_WAITING, wo, {"status": "CANCELLED", "reason": f"cancelled by {actor}"})
        return wo

    def summary(self) -> dict:
        wos = self.ctx.state.collection("work_orders").values()
        return {"open": sum(1 for w in wos if w.status in OPEN), "completed": sum(1 for w in wos
                                                                                   if w.status == "COMPLETED")}
