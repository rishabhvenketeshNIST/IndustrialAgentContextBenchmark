"""Canonical internal state of the enterprise simulator (not a UNS).

All modules read and write simulation state here instead of reaching into one
another. Three kinds of content:

* ``entities``     - generic entity/property store for everything that has an
                     identity: ISA-95 equipment elements, utility services,
                     materials. Properties are plain values.
* ``process``      - the TEP process image (true + transmitted measurements,
                     XMVs, setpoints, loops, disturbances, boundary parameters).
* ``collections``  - typed records owned by modules (work orders, lots,
                     samples, production orders, alarms, ...), keyed by id.

``causal`` records which upstream event currently explains an abnormal entity,
so downstream events can carry correlation/causation ids (ground truth).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from ..common import to_jsonable
from ..faults.effects import FaultEffects
from ..tep.catalog import NUM_IDV, NUM_XMEAS, NUM_XMV


@dataclass
class EntityRecord:
    id: str
    kind: str                      # equipment | utility | material | storage | ...
    name: str
    properties: Dict[str, Any] = field(default_factory=dict)
    units: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "name": self.name, "properties": to_jsonable(self.properties),
                "units": dict(self.units), "meta": to_jsonable(self.meta)}


@dataclass
class ProcessState:
    xmeas_true: np.ndarray = field(default_factory=lambda: np.zeros(NUM_XMEAS))
    xmeas: np.ndarray = field(default_factory=lambda: np.zeros(NUM_XMEAS))   # transmitted (observed)
    quality: List[str] = field(default_factory=lambda: ["GOOD"] * NUM_XMEAS)
    xmv: np.ndarray = field(default_factory=lambda: np.zeros(NUM_XMV))
    idv: np.ndarray = field(default_factory=lambda: np.zeros(NUM_IDV, dtype=int))
    setpoints: Dict[int, float] = field(default_factory=dict)
    loops: List[dict] = field(default_factory=list)
    control_mode: str = "CLOSED_LOOP"
    shutdown: bool = False
    shutdown_reason: Optional[str] = None
    shutdown_time_s: Optional[int] = None
    tep_time_h: float = 0.0
    boundary: Dict[str, float] = field(default_factory=dict)
    analyzer_updates: Dict[str, bool] = field(default_factory=dict)   # group -> new result this step

    def to_dict(self, include_truth: bool = True) -> dict:
        d = {
            "xmeas": to_jsonable(self.xmeas), "quality": list(self.quality), "xmv": to_jsonable(self.xmv),
            "setpoints": {str(k): v for k, v in self.setpoints.items()}, "loops": to_jsonable(self.loops),
            "control_mode": self.control_mode, "shutdown": self.shutdown,
            "shutdown_reason": self.shutdown_reason, "shutdown_time_s": self.shutdown_time_s,
            "tep_time_h": self.tep_time_h, "boundary": to_jsonable(self.boundary),
        }
        if include_truth:
            d["xmeas_true"] = to_jsonable(self.xmeas_true)
            d["idv"] = to_jsonable(self.idv)
        return d


@dataclass
class CauseRef:
    event_id: str
    correlation_id: str
    since_s: int
    origin: str


class CausalRegistry:
    """Maps entity keys (e.g. ``WU-CWP-101A``, ``tep.boundary.reactor_cw_max_flow``,
    ``XMEAS(9)``) to the event that currently explains their abnormal state."""

    def __init__(self) -> None:
        self._refs: Dict[str, CauseRef] = {}

    def set(self, key: str, ref: CauseRef) -> None:
        self._refs[key] = ref

    def set_if_absent(self, key: str, ref: CauseRef) -> None:
        if key not in self._refs:
            self._refs[key] = ref

    def get(self, key: str) -> Optional[CauseRef]:
        return self._refs.get(key)

    def first(self, keys: Iterable[str]) -> Optional[CauseRef]:
        for k in keys:
            r = self._refs.get(k)
            if r is not None:
                return r
        return None

    def clear(self, key: str) -> None:
        self._refs.pop(key, None)

    def clear_correlation(self, correlation_id: str) -> None:
        for k in [k for k, r in self._refs.items() if r.correlation_id == correlation_id]:
            del self._refs[k]

    def reset(self) -> None:
        self._refs.clear()

    def to_dict(self) -> dict:
        return {k: vars(v) for k, v in sorted(self._refs.items())}


class CanonicalState:
    def __init__(self, hierarchy, clock) -> None:
        self.hierarchy = hierarchy
        self.clock = clock
        self.entities: Dict[str, EntityRecord] = {}
        self.process = ProcessState()
        self.collections: Dict[str, Dict[str, Any]] = {}
        self.causal = CausalRegistry()
        self.fault_effects = FaultEffects()   # cause channels written only by the fault engine
        self.run: Dict[str, Any] = {}
        self.production: Dict[str, Any] = {}   # site production summary (owned by production module)

    # ---- entities -------------------------------------------------------------
    def register_entity(self, id: str, kind: str, name: str, properties: Optional[dict] = None,
                        units: Optional[dict] = None, meta: Optional[dict] = None) -> EntityRecord:
        if id in self.entities:
            rec = self.entities[id]
            rec.properties.update(properties or {})
            rec.units.update(units or {})
            rec.meta.update(meta or {})
            return rec
        rec = EntityRecord(id, kind, name, dict(properties or {}), dict(units or {}), dict(meta or {}))
        self.entities[id] = rec
        return rec

    def has_entity(self, id: str) -> bool:
        return id in self.entities

    def entity(self, id: str) -> EntityRecord:
        try:
            return self.entities[id]
        except KeyError as exc:
            raise KeyError(f"Unknown entity '{id}'") from exc

    def get(self, id: str, prop: str, default: Any = None) -> Any:
        rec = self.entities.get(id)
        if rec is None:
            return default
        return rec.properties.get(prop, default)

    def set(self, id: str, prop: str, value: Any, unit: Optional[str] = None) -> None:
        rec = self.entity(id)
        rec.properties[prop] = value
        if unit:
            rec.units[prop] = unit

    def entities_of_kind(self, kind: str) -> List[EntityRecord]:
        return [e for e in self.entities.values() if e.kind == kind]

    # ---- collections ----------------------------------------------------------------
    def collection(self, name: str) -> Dict[str, Any]:
        return self.collections.setdefault(name, {})

    def records(self, name: str) -> List[Any]:
        return list(self.collection(name).values())

    # ---- serialisation ----------------------------------------------------------------
    def snapshot(self, include_truth: bool = True) -> dict:
        return {
            "run": to_jsonable(self.run),
            "clock": self.clock.to_dict(),
            "process": self.process.to_dict(include_truth=include_truth),
            "entities": {k: v.to_dict() for k, v in self.entities.items()},
            "collections": {name: [to_jsonable(r) for r in coll.values()]
                            for name, coll in self.collections.items()
                            if include_truth or name not in ("faults",)},
            "production": to_jsonable(self.production),
        }
