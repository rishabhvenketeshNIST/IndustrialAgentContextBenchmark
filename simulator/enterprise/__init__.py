"""Enterprise read model: ISA-95 views with status roll-ups over the canonical state.

Generic entity/property access - no variable-specific accessors.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..common import to_jsonable
from ..isa95 import EquipmentLevel

_SEVERITY = {"FAILED": 4, "UNDER_MAINTENANCE": 3, "DEGRADED": 2, "CONSTRAINED": 2, "ALARM": 2,
             "STANDBY": 0, "STOPPED": 0, "RUNNING": 0, "NORMAL": 0, "OK": 0}
_PRIO = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


class EnterpriseView:
    """``status_of(entity_id)`` returns the status used for roll-ups. The default is the canonical
    status; the operational API passes the operational status (api/operational.py)."""

    def __init__(self, engine, status_of: Optional[Callable[[str], Any]] = None) -> None:
        self.e = engine
        self.status_of = status_of or (lambda eid: engine.state.get(eid, "status"))

    # ------------------------------------------------------------------ status rollups
    def _alarm_index(self) -> Dict[str, List[dict]]:
        idx: Dict[str, List[dict]] = {}
        for a in self.e.state.collection("alarms").values():
            if a.state in ("ACTIVE_UNACK", "ACTIVE_ACK"):
                idx.setdefault(a.definition.equipment_id, []).append(
                    {"alarm_id": a.alarm_id, "priority": a.definition.priority, "message": a.definition.message})
        return idx

    def element_status(self, element_id: str, alarms: Optional[Dict[str, List[dict]]] = None) -> dict:
        h = self.e.hierarchy
        alarms = alarms if alarms is not None else self._alarm_index()
        ids = [element_id] + [d.id for d in h.descendants(element_id)]
        worst, worst_status, n_alarm, top_prio = 0, "OK", 0, None
        for i in ids:
            s = self.status_of(i)
            if isinstance(s, str) and _SEVERITY.get(s, 0) > worst:
                worst, worst_status = _SEVERITY[s], s
            for a in alarms.get(i, []):
                n_alarm += 1
                if top_prio is None or _PRIO[a["priority"]] > _PRIO[top_prio]:
                    top_prio = a["priority"]
        own = self.status_of(element_id)
        return {"status": own if isinstance(own, str) else ("ALARM" if n_alarm and worst < 2 else worst_status),
                "rollup_status": worst_status if worst else ("ALARM" if n_alarm else "OK"),
                "active_alarms": n_alarm, "top_alarm_priority": top_prio}

    def tree(self) -> dict:
        alarms = self._alarm_index()

        def node(eid: str) -> dict:
            e = self.e.hierarchy.get(eid)
            d = {"id": e.id, "name": e.name, "level": e.level.value, "origin": e.origin.value,
                 "class": e.equipment_class}
            d.update(self.element_status(eid, alarms))
            d["children"] = [node(c) for c in e.children]
            return d
        return node(self.e.hierarchy.root.id)

    # ------------------------------------------------------------------ entities
    def entity(self, entity_id: str) -> dict:
        st = self.e.state
        rec = st.entity(entity_id)
        d = rec.to_dict()
        if entity_id in self.e.hierarchy:
            d["status_rollup"] = self.element_status(entity_id)
            d["children"] = list(self.e.hierarchy.get(entity_id).children)
            d["variables"] = [b.to_dict() | {"value": self._var_value(b.var_id)}
                              for b in self.e.mapping.bindings_for(entity_id)]
            d["loops"] = [l for l in st.process.loops
                          if self.e.mapping.loops.get(l["loop_id"]) in [entity_id] +
                          [x.id for x in self.e.hierarchy.descendants(entity_id)]]
        d["alarms"] = [a.to_dict() for a in st.collection("alarms").values()
                       if a.definition.equipment_id == entity_id and a.state != "NORMAL"]
        return to_jsonable(d)

    def _var_value(self, var_id: str) -> Any:
        p = self.e.state.process
        kind, idx = var_id.split("(")
        i = int(idx.rstrip(")")) - 1
        if kind == "XMEAS":
            return {"value": float(p.xmeas[i]), "quality": p.quality[i]}
        return {"value": float(p.xmv[i]), "quality": "GOOD"}

    def property(self, entity_id: str, prop: str) -> dict:
        rec = self.e.state.entity(entity_id)
        if prop not in rec.properties:
            raise KeyError(f"Entity '{entity_id}' has no property '{prop}'")
        return {"entity_id": entity_id, "property": prop, "value": to_jsonable(rec.properties[prop]),
                "unit": rec.units.get(prop), "timestamp": self.e.clock.timestamp(),
                "simulation_time": self.e.clock.time_s}

    def elements(self, level: Optional[str] = None, parent: Optional[str] = None) -> List[dict]:
        h = self.e.hierarchy
        out = []
        alarms = self._alarm_index()
        for el in h.elements.values():
            if level and el.level.value != level:
                continue
            if parent and el.parent_id != parent:
                continue
            d = el.to_dict()
            d.update(self.element_status(el.id, alarms))
            out.append(d)
        return out

    def enterprise(self) -> dict:
        root = self.e.hierarchy.root
        return root.to_dict() | self.element_status(root.id)

    def site(self) -> dict:
        s = self.e.hierarchy.by_level(EquipmentLevel.SITE)[0]
        d = s.to_dict() | self.element_status(s.id)
        d["production"] = to_jsonable(self.e.state.production)
        return d
