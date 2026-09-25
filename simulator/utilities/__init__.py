"""Utilities module: cooling water, steam and electrical power services.

Quantities (capacity, available_capacity, demand, flow, temperature, pressure,
health) are produced by the causal relations in configs/coupling.yaml. This
module registers the utility service entities and owns the derived operational
classification: availability, status (NORMAL / DEGRADED / CONSTRAINED /
UNAVAILABLE) and the UTILITY_STATE_CHANGED events.
"""
from __future__ import annotations

from typing import Dict

from ..common import ConfigError
from ..events import EventType
from ..simulation.module import ModuleContext, SimulationModule

UTILITY_PROPERTIES = ("capacity", "available_capacity", "demand", "utilization", "flow", "temperature",
                      "pressure", "health", "availability", "status")

# Model-internal quantities (metadata only; nothing in the simulation reads this tag). They are computed
# from equipment health, capability and fault causes, which no plant instrument measures; the status is
# classified from them. Meter-like values (flow, demand, pressure, temperature, voltage) stay operational.
# Utilization is model-internal where its denominator is hidden capacity: for steam it is the degraded
# boiler capacity. For cooling water, flow / min(available, capacity) equals XMV/100 exactly, and the
# power supply limit is treated as known to the site energy system (docs/CANONICAL_CONTRACT_AUDIT.md).
MODEL_INTERNAL = ("available_capacity", "available_for_process", "capacity_fraction", "process_supply_fraction",
                  "availability", "health", "status")
MODEL_INTERNAL_BY_TYPE = {"steam": ("utilization",)}


class UtilitiesModule(SimulationModule):
    name = "utilities"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("utilities")
        self.thresholds = dict(cfg.get("status_thresholds", {}))
        self.services: Dict[str, dict] = cfg.get("services", {}) or {}
        self._status: Dict[str, str] = {}
        for sid, spec in self.services.items():
            if spec.get("supplied_by") and not ctx.state.hierarchy.__contains__(spec["supplied_by"]):
                raise ConfigError(f"Utility {sid} supplied by unknown equipment {spec['supplied_by']}")
            props = {"capacity": float(spec["capacity"]), "status": "NORMAL", "availability": 1.0,
                     "temperature": None, "pressure": None}
            props.update({f"nominal_{k}": float(v) for k, v in (spec.get("nominal") or {}).items()})
            units = {"capacity": spec.get("unit", ""), "available_capacity": spec.get("unit", ""),
                     "demand": spec.get("unit", ""), "utilization": "fraction", "availability": "fraction",
                     "health": "fraction"}
            units.update(spec.get("units", {}))
            ctx.state.register_entity(sid, "utility", spec.get("name", sid), props, units,
                                      {"utility_type": spec.get("utility_type"),
                                       "supplied_by": spec.get("supplied_by"), "serves": spec.get("serves", []),
                                       "model_internal": list(MODEL_INTERNAL) + list(
                                           MODEL_INTERNAL_BY_TYPE.get(spec.get("utility_type"), ()))})
            self._status[sid] = "NORMAL"

    def _classify(self, sid: str) -> tuple:
        st = self.ctx.state
        cap = float(st.get(sid, "capacity") or 0.0)
        frac = st.get(sid, "capacity_fraction")
        if frac is None:
            avail = float(st.get(sid, "available_capacity") or 0.0)
            frac = min(1.0, avail / cap) if cap > 0 else 0.0
        availability = max(0.0, min(1.0, float(frac)))
        util = float(st.get(sid, "utilization") or 0.0)
        health = float(st.get(sid, "health") if st.get(sid, "health") is not None else 1.0)
        T = self.thresholds
        if availability < T.get("unavailable_availability", 0.02):
            status = "UNAVAILABLE"
        elif util >= T.get("constrained_utilization", 0.97):
            status = "CONSTRAINED"
        elif availability < T.get("degraded_availability", 0.999) or health < T.get("degraded_health", 0.75):
            status = "DEGRADED"
        else:
            status = "NORMAL"
        return availability, status

    def post_step(self, t: int) -> None:
        st = self.ctx.state
        for sid in self.services:
            availability, status = self._classify(sid)
            st.set(sid, "availability", round(availability, 6))
            st.set(sid, "status", status)
            if status != self._status[sid]:
                ref = st.causal.get(sid)
                kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
                self.ctx.bus.publish(
                    EventType.UTILITY_STATE_CHANGED, self.name, sid,
                    {"old": self._status[sid], "new": status, "availability": round(availability, 4),
                     "utilization": round(float(st.get(sid, "utilization") or 0), 4),
                     "available_capacity": round(float(st.get(sid, "available_capacity") or 0), 2)},
                    severity="warning" if status != "NORMAL" else "info", **kw)
                self._status[sid] = status

    def summary(self) -> dict:
        return {sid: {p: self.ctx.state.get(sid, p) for p in UTILITY_PROPERTIES} for sid in self.services}
