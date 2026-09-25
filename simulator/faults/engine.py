"""Fault engine - benchmark-operator component. The benchmark controls the cause;
the simulator determines the consequences.

Fault life cycle::

    CREATED --schedule--> SCHEDULED --trigger--> ACTIVE --stop/duration--> STOPPED
       |                                            |                          |
       +--------------------- reset ----------------+--------------------------+--> RESET

* ``stop`` ends the cause. Transient effects (disturbances, sensor overlays,
  capacity losses, delays) disappear; persistent damage (degradation,
  efficiency loss, failure) remains until the equipment is repaired.
* ``reset`` removes every contribution of the fault (restores the cause-free
  state) and archives it.
* Repairs (EQUIPMENT_REPAIRED) remediate persistent faults on that equipment.

Nothing here is reachable from an agent-facing interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..common import fmt_hms
from ..events import Event, EventType
from ..simulation.module import ModuleContext, SimulationModule
from ..state import CauseRef
from ..tep import catalog
from .types import REGISTRY, FaultValidationError, catalog_list

PROGRESSIONS = ("step", "gradual", "intermittent")
TRIGGERS = ("simulation_time", "manual", "event")
STATUSES = ("CREATED", "SCHEDULED", "ACTIVE", "STOPPED", "RESET")


@dataclass
class Fault:
    id: str
    type: str
    target: str
    trigger_mode: str = "manual"
    start_time: Optional[int] = None
    trigger_event: Optional[str] = None
    trigger_target: Optional[str] = None
    trigger_delay_s: int = 0
    duration: Optional[int] = None
    severity: float = 1.0
    progression_mode: str = "step"
    ramp_s: Optional[int] = None
    period_s: int = 600
    duty: float = 0.5
    parameters: Dict[str, Any] = field(default_factory=dict)
    observability: Dict[str, Any] = field(default_factory=dict)
    expected_effects: List[str] = field(default_factory=list)
    description: str = ""
    status: str = "CREATED"
    category: str = ""
    activated_at: Optional[int] = None
    stopped_at: Optional[int] = None
    intensity: float = 0.0
    stop_reason: str = ""
    remediated: bool = False
    history: List[dict] = field(default_factory=list)
    _pending_event_time: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "category": self.category, "target": self.target,
            "trigger": {"mode": self.trigger_mode, "time": self.start_time, "event_type": self.trigger_event,
                        "event_target": self.trigger_target, "delay_s": self.trigger_delay_s},
            "start_time": self.start_time, "start_hms": fmt_hms(self.start_time) if self.start_time is not None
            else None, "duration": self.duration, "severity": self.severity,
            "progression": {"mode": self.progression_mode, "ramp_s": self.ramp_s, "period_s": self.period_s,
                            "duty": self.duty},
            "parameters": dict(self.parameters), "observability": dict(self.observability),
            "expected_effects": list(self.expected_effects), "description": self.description,
            "status": self.status, "activated_at": self.activated_at, "stopped_at": self.stopped_at,
            "intensity": round(self.intensity, 6), "stop_reason": self.stop_reason, "remediated": self.remediated,
            "history": list(self.history)}


def fault_from_spec(spec: dict) -> Fault:
    """Build a Fault from the scenario / API schema (see docs/06_scenarios/fault_injection.md)."""
    spec = dict(spec)
    if "id" not in spec or "type" not in spec or "target" not in spec:
        raise FaultValidationError("fault needs 'id', 'type' and 'target'")
    trig = spec.get("trigger") or {}
    if isinstance(trig, str):
        trig = {"mode": trig}
    mode = trig.get("mode") or ("simulation_time" if spec.get("start_time") is not None else "manual")
    start = trig.get("time", spec.get("start_time"))
    prog = spec.get("progression") or "step"
    if isinstance(prog, str):
        prog = {"mode": prog}
    obs = dict(spec.get("observability") or {})
    if "direct_indication" in spec:
        obs["direct_indication"] = bool(spec["direct_indication"])
    f = Fault(
        id=str(spec["id"]), type=str(spec["type"]), target=str(spec["target"]), trigger_mode=mode,
        start_time=int(start) if start is not None else None, trigger_event=trig.get("event_type"),
        trigger_target=trig.get("event_target"), trigger_delay_s=int(trig.get("delay_s", 0)),
        duration=int(spec["duration"]) if spec.get("duration") not in (None, "", 0) else None,
        severity=float(spec.get("severity", 1.0)), progression_mode=prog.get("mode", "step"),
        ramp_s=int(prog["ramp_s"]) if prog.get("ramp_s") else None, period_s=int(prog.get("period_s", 600)),
        duty=float(prog.get("duty", 0.5)), parameters=dict(spec.get("parameters") or {}), observability=obs,
        expected_effects=list(spec.get("expected_effects") or []), description=spec.get("description", ""))
    return f


class FaultEngine(SimulationModule):
    name = "faults"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        self.state = ctx.state
        self.process = ctx.process
        self.clock = ctx.clock
        self.config = ctx.config
        self.tep_mapping = ctx.tep_mapping
        self.faults: Dict[str, Fault] = ctx.state.collection("faults")
        self._started_events: Dict[str, Event] = {}
        self._trigger_events: Dict[str, Event] = {}
        self._rng = ctx.rng.get("faults")
        ctx.bus.subscribe(self._on_event)
        ctx.bus.subscribe(self._on_repaired, [EventType.EQUIPMENT_REPAIRED])
        for spec in ctx.config.get("faults", []) or []:
            self.create_fault(spec)

    # ------------------------------------------------------------------ helpers
    def resolve_measurement(self, target: str) -> str:
        """Accept XMEAS(n) or EQUIPMENT.property (mapped to its XMEAS)."""
        t = target.strip()
        if t.upper().startswith("XMEAS"):
            vid = catalog.normalize_id(t)
            if vid not in catalog.ALL_VARIABLES:
                raise FaultValidationError(f"Unknown measurement {target}")
            return vid
        if "." in t:
            eq, prop = t.split(".", 1)
            vid = self.tep_mapping.resolve_property(eq, prop)
            if vid and vid.startswith("XMEAS"):
                return vid
        raise FaultValidationError(f"Sensor fault target must be XMEAS(n) or a mapped EQUIPMENT.property: {target}")

    def active_faults(self) -> List[Fault]:
        return [f for f in self.faults.values() if f.status == "ACTIVE"]

    def _ftype(self, f: Fault):
        return REGISTRY[f.type]

    def _publish(self, etype: EventType, f: Fault, payload: dict, cause: Optional[Event] = None) -> Event:
        base = {"fault_id": f.id, "fault_type": f.type, "category": f.category, "target": f.target,
                "severity": f.severity}
        base.update(payload)
        return self.ctx.bus.publish(etype, "benchmark.fault_engine", f.target, base, correlation_id=f.id,
                                    causation_id=cause.event_id if cause else None,
                                    severity="warning" if etype == EventType.FAULT_STARTED else "info")

    def _log(self, f: Fault, what: str, **kw) -> None:
        f.history.append({"t": self.clock.time_s, "action": what, **kw})

    # ------------------------------------------------------------------ API
    def create_fault(self, spec: dict) -> Fault:
        f = spec if isinstance(spec, Fault) else fault_from_spec(spec)
        if f.id in self.faults and self.faults[f.id].status != "RESET":
            raise FaultValidationError(f"Fault '{f.id}' already exists")
        if f.type not in REGISTRY:
            raise FaultValidationError(f"Unknown fault type '{f.type}'. Known: {sorted(REGISTRY)}")
        ft = REGISTRY[f.type]
        f.category = ft.category
        if not 0.0 <= f.severity <= 1.0:
            raise FaultValidationError("severity must be within [0, 1]")
        if f.progression_mode not in PROGRESSIONS:
            raise FaultValidationError(f"progression must be one of {PROGRESSIONS}")
        if f.progression_mode == "gradual" and not ft.supports_gradual:
            raise FaultValidationError(f"{f.type} does not support gradual progression")
        if f.progression_mode == "gradual" and not (f.ramp_s or f.duration):
            raise FaultValidationError("gradual progression needs progression.ramp_s or a duration")
        if f.progression_mode == "intermittent" and (f.period_s <= 0 or not 0 < f.duty < 1):
            raise FaultValidationError("intermittent progression needs period_s > 0 and 0 < duty < 1")
        if f.trigger_mode not in TRIGGERS:
            raise FaultValidationError(f"trigger mode must be one of {TRIGGERS}")
        if f.trigger_mode == "simulation_time" and (f.start_time is None or f.start_time < 0):
            raise FaultValidationError("simulation_time trigger needs a start_time >= 0")
        if f.trigger_mode == "event":
            if not f.trigger_event or f.trigger_event not in EventType.__members__:
                raise FaultValidationError("event trigger needs a valid trigger.event_type")
            if f.trigger_event in ("FAULT_CREATED", "FAULT_SCHEDULED", "FAULT_STARTED", "FAULT_STOPPED",
                                   "FAULT_RESET"):
                raise FaultValidationError("faults cannot be triggered by fault events")
        if f.duration is not None and f.duration <= 0:
            raise FaultValidationError("duration must be positive (omit for open-ended)")
        ft.validate(self, f)
        if not f.observability:
            f.observability = dict(ft.default_observability)
        if not f.expected_effects:
            f.expected_effects = list(ft.default_expected_effects)
        self.faults[f.id] = f
        self._log(f, "created")
        self._publish(EventType.FAULT_CREATED, f, {"spec": f.to_dict()})
        if f.trigger_mode == "simulation_time":
            self.schedule_fault(f.id, f.start_time)
        return f

    def schedule_fault(self, fault_id: str, start_time: int) -> Fault:
        f = self._get(fault_id)
        if f.status not in ("CREATED", "SCHEDULED"):
            raise FaultValidationError(f"Fault {fault_id} cannot be scheduled in status {f.status}")
        if int(start_time) < self.clock.time_s:
            raise FaultValidationError("cannot schedule a fault in the past")
        f.trigger_mode, f.start_time, f.status = "simulation_time", int(start_time), "SCHEDULED"
        self._log(f, "scheduled", start_time=f.start_time)
        self._publish(EventType.FAULT_SCHEDULED, f, {"start_time": f.start_time})
        return f

    def start_fault(self, fault_id: str, cause: Optional[Event] = None) -> Fault:
        f = self._get(fault_id)
        if f.status not in ("CREATED", "SCHEDULED", "STOPPED"):
            raise FaultValidationError(f"Fault {fault_id} cannot be started in status {f.status}")
        if f.status == "STOPPED":
            self._ftype(f).remove(self, f, reset=True)
        f.status, f.activated_at, f.stopped_at, f.stop_reason, f.remediated = "ACTIVE", self.clock.time_s, None, "", False
        if f.start_time is None or f.trigger_mode != "simulation_time":
            f.start_time = self.clock.time_s
        self._log(f, "started")
        ev = self._publish(EventType.FAULT_STARTED, f, {"observability": f.observability,
                                                         "expected_effects": f.expected_effects}, cause=cause)
        self._started_events[f.id] = ev
        for key in self._ftype(f).cause_keys(self, f):
            self.state.causal.set(key, CauseRef(ev.event_id, f.id, self.clock.time_s, f"fault:{f.id}"))
        self._apply(f)
        return f

    def stop_fault(self, fault_id: str, reason: str = "stopped by benchmark operator") -> Fault:
        f = self._get(fault_id)
        if f.status != "ACTIVE":
            raise FaultValidationError(f"Fault {fault_id} is not active (status {f.status})")
        ft = self._ftype(f)
        f.status, f.stopped_at, f.stop_reason = "STOPPED", self.clock.time_s, reason
        if not ft.persistent:
            ft.remove(self, f, reset=False)
            self.state.causal.clear_correlation(f.id)
            f.intensity = 0.0
        self._log(f, "stopped", reason=reason)
        self._publish(EventType.FAULT_STOPPED, f, {"reason": reason, "persistent_effect": ft.persistent
                                                   and not f.remediated})
        return f

    def reset_fault(self, fault_id: str) -> Fault:
        f = self._get(fault_id)
        if f.status == "RESET":
            raise FaultValidationError(f"Fault {fault_id} is already reset")
        self._ftype(f).remove(self, f, reset=True)
        self.state.fault_effects.clear_fault(f.id)
        self.state.causal.clear_correlation(f.id)
        f.status, f.intensity = "RESET", 0.0
        if f.stopped_at is None:
            f.stopped_at = self.clock.time_s
        self._log(f, "reset")
        self._publish(EventType.FAULT_RESET, f, {})
        return f

    def get_fault_status(self, fault_id: str) -> dict:
        return self._get(fault_id).to_dict()

    def list_faults(self) -> List[dict]:
        return [f.to_dict() for f in sorted(self.faults.values(), key=lambda x: x.id)]

    def catalog(self) -> List[dict]:
        out = []
        for d in catalog_list():
            try:
                d["targets"] = REGISTRY[d["type"]].targets(self)
            except Exception:  # pragma: no cover - defensive
                d["targets"] = []
            out.append(d)
        return out

    def _get(self, fault_id: str) -> Fault:
        if fault_id not in self.faults:
            raise KeyError(f"Unknown fault '{fault_id}'")
        return self.faults[fault_id]

    # ------------------------------------------------------------------ progression
    def _intensity(self, f: Fault, t: int) -> float:
        el = t - (f.activated_at or t)
        if f.progression_mode == "step":
            return 1.0
        if f.progression_mode == "gradual":
            ramp = f.ramp_s or f.duration or 1
            return max(0.0, min(1.0, el / ramp))
        phase = (el % f.period_s) / f.period_s
        return 1.0 if phase < f.duty else 0.0

    def _apply(self, f: Fault) -> None:
        if f.remediated:
            return
        f.intensity = self._intensity(f, self.clock.time_s)
        self._ftype(f).apply(self, f, f.intensity)

    def pre_step(self, t: int) -> None:
        for f in sorted(self.faults.values(), key=lambda x: x.id):
            if f.status == "SCHEDULED" and f.start_time is not None and t >= f.start_time:
                self.start_fault(f.id)
            elif f.status == "CREATED" and f.trigger_mode == "event" and f._pending_event_time is not None \
                    and t >= f._pending_event_time:
                f._pending_event_time = None
                self.start_fault(f.id, cause=self._trigger_events.pop(f.id, None))
            if f.status == "ACTIVE":
                if f.duration is not None and t >= (f.activated_at or 0) + f.duration:
                    self._apply(f)
                    self.stop_fault(f.id, "duration elapsed")
                else:
                    self._apply(f)

    def _on_event(self, ev: Event) -> None:
        if ev.source == "benchmark.fault_engine":
            return
        for f in self.faults.values():
            if (f.status == "CREATED" and f.trigger_mode == "event" and f._pending_event_time is None
                    and ev.type.value == f.trigger_event
                    and (not f.trigger_target or f.trigger_target == ev.target)):
                f._pending_event_time = ev.simulation_time + f.trigger_delay_s
                self._trigger_events[f.id] = ev

    def _on_repaired(self, ev: Event) -> None:
        for fid in ev.payload.get("cleared_fault_effects", []) or []:
            f = self.faults.get(fid)
            if f is None or f.status not in ("ACTIVE", "STOPPED"):
                continue
            f.remediated = True
            f.intensity = 0.0
            self._log(f, "remediated", work_order=ev.payload.get("work_order"))
            self.state.causal.clear_correlation(f.id)
            if f.status == "ACTIVE":
                self.stop_fault(f.id, f"remediated by repair ({ev.payload.get('work_order')})")

    def summary(self) -> dict:
        by = {}
        for f in self.faults.values():
            by[f.status] = by.get(f.status, 0) + 1
        return by
