"""Alarm module (ISA-18.2 style states).

States: NORMAL -> ACTIVE_UNACK -> ACTIVE_ACK -> NORMAL, or ACTIVE_UNACK -> RTN_UNACK -> NORMAL
(an alarm that returned to normal still needs acknowledgement). Alarms evaluate
transmitted process values and canonical-state entity properties with on/off
delays and a clearing deadband.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from ..common import ConfigError, fmt_hms
from ..events import EventType
from ..simulation.module import ModuleContext, SimulationModule
from ..tep import catalog
from ..tep import control_scheme as cs

PRIORITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


@dataclass
class AlarmDefinition:
    id: str
    source: str                 # XMEAS(n) | ENTITY.prop | QUALITY(XMEAS(n)) | SHUTDOWN
    type: str                   # HIGH | LOW | EQUALS
    limit: Any
    priority: str
    message: str
    equipment_id: Optional[str]
    property: str
    category: str
    on_delay_s: int
    off_delay_s: int
    deadband: float
    maintenance: Optional[str] = None
    maintenance_target: Optional[str] = None
    maintenance_priority: int = 2
    basis: str = ""

    def to_dict(self) -> dict:
        d = dict(vars(self))
        return d


@dataclass
class Alarm:
    alarm_id: str
    definition: AlarmDefinition
    state: str = "NORMAL"
    activation_time: Optional[int] = None
    acknowledged: bool = False
    ack_time: Optional[int] = None
    acknowledged_by: Optional[str] = None
    clear_time: Optional[int] = None
    value: Any = None
    activation_value: Any = None
    count: int = 0
    _pending_since: Optional[int] = None
    _clear_since: Optional[int] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = self.definition
        return {"alarm_id": self.alarm_id, "equipment_id": d.equipment_id, "property": d.property,
                "source": d.source, "type": d.type, "threshold": d.limit, "priority": d.priority,
                "message": d.message, "category": d.category, "state": self.state,
                "activation_time": self.activation_time,
                "activation_hms": fmt_hms(self.activation_time) if self.activation_time is not None else None,
                "acknowledgement": {"acknowledged": self.acknowledged, "time": self.ack_time,
                                    "by": self.acknowledged_by},
                "clear_time": self.clear_time, "value": self.value, "activation_value": self.activation_value,
                "count": self.count, "active": self.state in ("ACTIVE_UNACK", "ACTIVE_ACK")}


class AlarmModule(SimulationModule):
    name = "alarms"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("alarms")
        self.mapping = ctx.tep_mapping
        d = cfg.get("defaults", {})
        self._don, self._doff = int(d.get("on_delay_s", 10)), int(d.get("off_delay_s", 30))
        self._db = float(d.get("deadband_fraction", 0.01))
        defs: List[AlarmDefinition] = []
        for category in ("process", "equipment", "utilities", "inventory", "quality"):
            for spec in cfg.get(category, []) or []:
                defs.append(self._definition(spec, category))
        sat = cfg.get("controller_saturation")
        if sat:
            for lid, loop in cs.LOOPS.items():
                if loop.output_kind != "XMV":
                    continue
                cm = self.mapping.loops[lid]
                for kind, limit in (("HIGH", sat["high"]), ("LOW", sat["low"])):
                    defs.append(self._definition({
                        "id": f"OUT{'H' if kind == 'HIGH' else 'L'}-L{lid:02d}", "source": f"{cm}.output",
                        "type": kind, "limit": limit, "priority": sat.get("priority", "MEDIUM"),
                        "on_delay_s": sat.get("on_delay_s", 30),
                        "message": f"{loop.tag} output saturated {'high' if kind == 'HIGH' else 'low'} "
                                   f"({catalog.XMV[loop.output_index - 1].name})"}, "control"))
        mq = cfg.get("measurement_quality")
        if mq:
            for idx, b in self.mapping.xmeas.items():
                defs.append(self._definition({
                    "id": f"BADPV-{idx:02d}", "source": f"QUALITY(XMEAS({idx}))", "type": "EQUALS", "limit": "BAD",
                    "priority": mq.get("priority", "HIGH"), "on_delay_s": mq.get("on_delay_s", 2),
                    "off_delay_s": 2, "message": f"Transmitter {b.tag or b.var_id} signal bad ({b.name})"},
                    "instrumentation"))
        defs.append(AlarmDefinition("ESD-TRIP", "SHUTDOWN", "EQUALS", True, "CRITICAL",
                                    "Process interlock trip (TEP shutdown)", "SITE-TE", "shutdown", "safety",
                                    0, 0, 0.0, basis="teprob.f shutdown logic (ISD)"))
        ids = [x.id for x in defs]
        if len(ids) != len(set(ids)):
            raise ConfigError("Duplicate alarm ids")
        alarms = ctx.state.collection("alarms")
        self._getters = {}
        for df in defs:
            alarms[df.id] = Alarm(df.id, df)
            self._getters[df.id] = self._make_getter(df)
        self._by_id = alarms

    def _definition(self, spec: dict, category: str) -> AlarmDefinition:
        for k in ("id", "source", "type", "priority"):
            if k not in spec:
                raise ConfigError(f"Alarm definition missing '{k}': {spec}")
        if spec["priority"] not in PRIORITIES:
            raise ConfigError(f"Alarm {spec['id']}: priority must be one of {PRIORITIES}")
        typ = spec["type"].upper()
        if typ not in ("HIGH", "LOW", "EQUALS"):
            raise ConfigError(f"Alarm {spec['id']}: bad type {typ}")
        src = spec["source"]
        limit = spec.get("value", spec.get("limit")) if typ == "EQUALS" else spec.get("limit")
        if limit is None:
            raise ConfigError(f"Alarm {spec['id']}: needs 'limit' (or 'value' for EQUALS)")
        st = self.ctx.state
        if src.upper().startswith("XMEAS"):
            vid = catalog.normalize_id(src)
            b = self.mapping.binding(vid)
            eq, prop = b.equipment_id, b.property
        elif src.startswith("QUALITY("):
            vid = catalog.normalize_id(src[len("QUALITY("):-1])
            b = self.mapping.binding(vid)
            eq, prop = b.equipment_id, f"{b.property}.quality"
        else:
            if "." not in src:
                raise ConfigError(f"Alarm {spec['id']}: bad source {src}")
            eq, prop = src.split(".", 1)
            if not st.has_entity(eq):
                raise ConfigError(f"Alarm {spec['id']}: unknown entity {eq}")
        if spec.get("maintenance") not in (None, "corrective", "inspection"):
            raise ConfigError(f"Alarm {spec['id']}: maintenance must be corrective|inspection")
        return AlarmDefinition(
            spec["id"], src, typ, limit, spec["priority"], spec.get("message", spec["id"]), eq, prop, category,
            int(spec.get("on_delay_s", self._don)), int(spec.get("off_delay_s", self._doff)),
            float(spec.get("deadband_fraction", self._db)), spec.get("maintenance"),
            spec.get("maintenance_target"), int(spec.get("maintenance_priority", 2)), spec.get("basis", ""))

    # ------------------------------------------------------------------ evaluation
    def _make_getter(self, d: AlarmDefinition):
        st = self.ctx.state
        s = d.source
        if s == "SHUTDOWN":
            return lambda: st.process.shutdown
        if s.startswith("QUALITY("):
            i = int(catalog.normalize_id(s[len("QUALITY("):-1])[6:-1]) - 1
            return lambda: st.process.quality[i]
        if s.upper().startswith("XMEAS"):
            i = int(catalog.normalize_id(s)[6:-1]) - 1
            return lambda: float(st.process.xmeas[i])
        props = st.entity(d.equipment_id).properties
        prop = d.property
        return lambda: props.get(prop)

    @staticmethod
    def _condition(d: AlarmDefinition, v: Any, active: bool) -> bool:
        if v is None:
            return False
        if d.type == "EQUALS":
            return v == d.limit
        lim = float(d.limit)
        db = abs(lim) * d.deadband if active else 0.0
        if d.type == "HIGH":
            return float(v) > lim - db
        return float(v) < lim + db

    def post_step(self, t: int) -> None:
        for a in self._by_id.values():
            d = a.definition
            v = self._getters[a.alarm_id]()
            a.value = v if not isinstance(v, float) else round(v, 5)
            active = a.state in ("ACTIVE_UNACK", "ACTIVE_ACK")
            cond = self._condition(d, v, active)
            if cond and not active:
                a._clear_since = None
                if a._pending_since is None:
                    a._pending_since = t
                if t - a._pending_since >= d.on_delay_s:
                    self._activate(a, t)
            elif not cond and active:
                a._pending_since = None
                if a._clear_since is None:
                    a._clear_since = t
                if t - a._clear_since >= d.off_delay_s:
                    self._clear(a, t)
            else:
                if not cond:
                    a._pending_since = None
                else:
                    a._clear_since = None

    def _cause_keys(self, d: AlarmDefinition) -> List[str]:
        keys = []
        if d.source.upper().startswith("XMEAS") or d.source.startswith("QUALITY("):
            src = d.source[len("QUALITY("):-1] if d.source.startswith("QUALITY(") else d.source
            keys.append(catalog.normalize_id(src))
        if d.equipment_id:
            keys.append(d.equipment_id)
        if d.maintenance_target:
            keys.append(d.maintenance_target)
        return keys

    def _activate(self, a: Alarm, t: int) -> None:
        d = a.definition
        a.state, a.activation_time, a.acknowledged, a.ack_time, a.acknowledged_by = "ACTIVE_UNACK", t, False, None, None
        a.clear_time, a.activation_value, a._pending_since = None, a.value, None
        a.count += 1
        ref = self.ctx.state.causal.first(self._cause_keys(d))
        kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
        payload = {"alarm_id": a.alarm_id, "equipment_id": d.equipment_id, "property": d.property,
                   "source": d.source, "type": d.type, "threshold": d.limit, "priority": d.priority,
                   "value": a.value, "message": d.message, "category": d.category}
        if d.maintenance:
            payload.update({"maintenance": d.maintenance, "maintenance_priority": d.maintenance_priority,
                            "maintenance_target": d.maintenance_target or d.equipment_id})
        sev = {"CRITICAL": "critical", "HIGH": "warning", "MEDIUM": "warning"}.get(d.priority, "info")
        ev = self.ctx.bus.publish(EventType.ALARM_ACTIVATED, self.name, d.equipment_id, payload, severity=sev, **kw)
        a.correlation_id = ev.correlation_id

    def _clear(self, a: Alarm, t: int) -> None:
        a.clear_time, a._clear_since = t, None
        a.state = "NORMAL" if a.acknowledged else "RTN_UNACK"
        self.ctx.bus.publish(EventType.ALARM_CLEARED, self.name, a.definition.equipment_id,
                             {"alarm_id": a.alarm_id, "value": a.value, "state": a.state},
                             correlation_id=a.correlation_id)

    def acknowledge(self, alarm_id: str, actor: str = "operator") -> Alarm:
        a = self._by_id.get(alarm_id)
        if a is None:
            raise KeyError(f"Unknown alarm {alarm_id}")
        if a.state not in ("ACTIVE_UNACK", "RTN_UNACK"):
            raise ValueError(f"Alarm {alarm_id} is not awaiting acknowledgement (state {a.state})")
        t = self.ctx.clock.time_s
        a.acknowledged, a.ack_time, a.acknowledged_by = True, t, actor
        a.state = "ACTIVE_ACK" if a.state == "ACTIVE_UNACK" else "NORMAL"
        self.ctx.bus.publish(EventType.ALARM_ACKNOWLEDGED, actor, a.definition.equipment_id,
                             {"alarm_id": alarm_id, "state": a.state}, correlation_id=a.correlation_id)
        return a

    def summary(self) -> dict:
        alarms = self._by_id.values()
        return {"active": sum(1 for a in alarms if a.state in ("ACTIVE_UNACK", "ACTIVE_ACK")),
                "unacknowledged": sum(1 for a in alarms if a.state in ("ACTIVE_UNACK", "RTN_UNACK"))}
