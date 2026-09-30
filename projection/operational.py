"""The operational context projection: what an operational consumer may observe, and how it is structured.

One ``OperationalProjection`` per simulation engine (one operational scope). It reads the simulator only
through the operational boundary (``api/operational.py``) and the canonical context model
(``contract/context_model.yaml``), and returns plain Python structures in simulation time. It knows
nothing about any transport or store: the UNS (uns/publisher.py) turns it into MQTT messages, the
Historian will store it. There is no wall-clock time here (docs/CONTEXT_PROJECTION_PRINCIPLES.md, P6).

What it provides, for canonical ids ``<entity_type>:<native_id>``:

    placement         ISA-95 placement of every entity and record (Isa95Placement; no second hierarchy)
    meta(cid)         static description: identity, ISA-95 concept, parent, children, configuration
    measurements(cid) (variable, fields) of every operational measurement under an entity
    entity_state(cid) discrete operational state of an entity (None if it has none)
    records()         operationally observable records (orders, lots, samples, ...) with their fields
    events_since(n)   operational events (OE-/LC- ids) after the first n
    lifecycle()       simulation status, simulated start and duration
    scope_id          the opaque operational scope id (api.operational.operational_scope_id)

Which properties are state, measurement or configuration comes from their semantic class in
contract/canonical_contract.yaml; which are observable at all from api.operational.properties (the
owning modules' ``model_internal``/``unobservable`` tags); which record collections are observable from
the context model's ``observability``. None of these is a list of field names kept here.

``StateChangeFilter`` holds the report-by-exception rule for state, shared by consumers: a discrete
change is reported at once, a change of continuous quantities alone at the consumer's next sampling tick.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import numpy as np
import yaml

from api import operational
from simulator.common import iso
from simulator.isa95 import EquipmentLevel
from simulator.tep import catalog
from simulator.tep import control_scheme as cs

ROOT_DIR = Path(__file__).resolve().parents[1]
CONTEXT_MODEL = ROOT_DIR / "contract" / "context_model.yaml"
CANONICAL_CONTRACT = ROOT_DIR / "contract" / "canonical_contract.yaml"

# Channel of an entity property, from its semantic class in contract/canonical_contract.yaml.
MEASUREMENT_CLASSES = {"condition_indicator", "utility_observation", "inventory_quantity", "counter", "availability"}
META_CLASSES = {"configuration"}
# Production line (state.production of the production unit)
PRODUCTION_STATE = ("state", "current_lot", "current_order")
PRODUCTION_MEASUREMENTS = {"rate_kg_h": "kg/h", "rate_smoothed_kg_h": "kg/h", "total_kg": "kg", "accepted_kg": "kg",
                           "rejected_kg": "kg", "run_time_s": "s", "down_time_s": "s"}
RECORD_EXCLUDE = {"alarms": ("value",)}      # the alarm source's current value is its measurement
# Record fields that are continuous quantities although stored as integers (a forecast in seconds).
# Floats are always continuous.
RECORD_CONTINUOUS = {"production_orders": ("projected_end",)}
# Timestamp semantics of a measurement (context model vocabulary; docs/CONTEXT_PROJECTION_PRINCIPLES.md P5)
STEP_STATE, ANALYZER_SAMPLE = "step_state", "analyzer_sample"


# ---------------------------------------------------------------------------- values
def clean(x: Any) -> Any:
    """Plain JSON-compatible values: numpy scalars and arrays to Python, NaN and infinity to None."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return clean(x.tolist())
    return x


def snap(x: Any) -> Any:
    """Structural copy of containers, so a later in-place mutation cannot hide a change."""
    if isinstance(x, dict):
        return {k: snap(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [snap(v) for v in x]
    return x


def discrete_projection(body: dict, collection: Optional[str] = None) -> Any:
    """The discrete content of a state body: floats and declared continuous record fields replaced by
    a marker. Used to decide when a record's state must be reported at once."""
    d = _discrete(body)
    for k in RECORD_CONTINUOUS.get(collection, ()):
        if k in d.get("state", {}):
            d["state"][k] = "#"
    return d


def _discrete(x: Any) -> Any:
    """The body with every float replaced by a marker."""
    if isinstance(x, dict):
        return {k: _discrete(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_discrete(v) for v in x]
    return "#" if isinstance(x, float) else x


# ---------------------------------------------------------------------------- context model
def canonical_id(entity_type: str, native_id: str) -> str:
    return f"{entity_type}:{native_id}"


def load_context_model(path: Path = CONTEXT_MODEL) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_contract(path: Path = CANONICAL_CONTRACT) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class Isa95Placement:
    """ISA-95 placement of every entity and record of one engine, derived from the simulator's hierarchy
    (configs/site.yaml) and the canonical context model. No hierarchy is defined here:

    * hierarchy elements: their ISA-95 parent chain;
    * utilities: under the work center that supplies them (``supplied_by``);
    * materials, products: under the site (site-scoped definitions);
    * records: under the entity named by their context-model relationship (a work order under the asset
      it is for, a sample under the analyzer or laboratory it came from, ...).
    """

    def __init__(self, engine, context_model: Optional[dict] = None) -> None:
        self.engine = engine
        self.cm = context_model or load_context_model()
        types = self.cm["entity_types"]
        self.level_type = {d["source"]["hierarchy_level"]: t for t, d in types.items()
                           if "hierarchy_level" in d["source"]}
        self.kind_type = {d["source"]["entity_kind"]: t for t, d in types.items() if "entity_kind" in d["source"]}
        # only collections whose records are operational are projected
        self.collection_type = {d["source"]["collection"]: t for t, d in types.items()
                                if "collection" in d["source"] and d["observability"] == "operational"}
        self.isa95 = {m["id"]: m for m in self.cm["isa95_mapping"]}
        self.types = types
        self._path: Dict[str, Tuple[str, ...]] = {}       # canonical id -> path of canonical ids
        self._native: Dict[str, str] = {}                 # entity native id -> canonical id
        self._build()

    def _build(self) -> None:
        e = self.engine
        h = e.hierarchy

        def walk(eid: str, parent: Tuple[str, ...]) -> None:
            el = h.get(eid)
            cid = canonical_id(self.level_type[el.level.value], eid)
            self._path[cid] = parent + (cid,)
            self._native[eid] = cid
            for c in el.children:
                walk(c, self._path[cid])
        walk(h.root.id, ())
        site = self._native[h.by_level(EquipmentLevel.SITE)[0].id]
        self.site = site
        st = e.state
        for rec in sorted(st.entities_of_kind("utility"), key=lambda r: r.id):
            cid = canonical_id(self.kind_type["utility"], rec.id)
            self._path[cid] = self._path[self._native[rec.meta["supplied_by"]]] + (cid,)
            self._native[rec.id] = cid
        for rec in sorted(st.entities_of_kind("material"), key=lambda r: r.id):
            cid = canonical_id(self.kind_type["material"], rec.id)
            self._path[cid] = self._path[site] + (cid,)
            self._native[rec.id] = cid
        for pid in sorted(e.config["production"]["products"]):
            cid = canonical_id("product", pid)
            self._path[cid] = self._path[site] + (cid,)
        line = e.config["production"]["line"]["id"]
        self.production_unit = self._native[line]
        self._entities = sorted(self._path)         # entity nodes; records are registered later
        wh = e.config["warehouse"]["locations"]
        self._record_parent_fixed = {
            "production_lots": self.production_unit,      # produced_for / produced_on: the production line
            "production_orders": self.production_unit,    # orders_product -> produced_on
            "technicians": site,                          # personnel are not placed in the equipment hierarchy
            "shipments": self._native[wh["dispatch"]],    # dispatched from the finished-goods bay
        }

    def entity_canonical_id(self, native_id: str) -> Optional[str]:
        return self._native.get(native_id)

    def entity_ids(self) -> List[str]:
        """Canonical ids of entity nodes (hierarchy elements, utilities, materials, products)."""
        return list(self._entities)

    def record_ids(self) -> List[str]:
        return sorted(set(self._path) - set(self._entities))

    def path(self, cid: str) -> Tuple[str, ...]:
        return self._path[cid]

    def parent(self, cid: str) -> Optional[str]:
        p = self._path[cid]
        return p[-2] if len(p) > 1 else None

    def record_parent(self, collection: str, record) -> str:
        """The entity a record is placed under (its context-model relationship)."""
        if collection in self._record_parent_fixed:
            return self._record_parent_fixed[collection]
        native = {
            "work_orders": lambda r: r.asset_id,                   # work_order_for
            "material_lots": lambda r: r.location,                 # located_in
            "spare_parts": lambda r: r.location,                   # located_in
            "purchase_orders": lambda r: r.location,               # delivered to
            "quality_samples": lambda r: r.source,                 # sampled_from
            "alarms": lambda r: r.definition.equipment_id,         # alarm_on
        }[collection](record)
        return self._native.get(native, self.site)

    def record_id(self, collection: str, native_id: str) -> str:
        return canonical_id(self.collection_type[collection], native_id)

    def register_record(self, collection: str, record, native_id: str) -> str:
        cid = self.record_id(collection, native_id)
        if cid not in self._path:
            self._path[cid] = self._path[self.record_parent(collection, record)] + (cid,)
        return cid

    def isa95_of(self, cid: str) -> dict:
        etype = cid.split(":", 1)[0]
        m = self.isa95.get(self.types[etype]["isa95"], {})
        return {"concept": m.get("isa95"), "mapping": m.get("mapping"), "mapping_id": m.get("id")}


# ---------------------------------------------------------------------------- report by exception
class StateChangeFilter:
    """The report-by-exception rule for state, one instance per consumer (it remembers what that
    consumer last reported). ``report()`` says whether a state observation must be reported now:

    * entity state (``continuous`` false) is discrete: reported on any change;
    * record state (``continuous`` = the record collection) also holds continuous quantities (lot and
      stock kilograms, a projected end): a change of those alone is reported at the next ``tick``.
    """

    def __init__(self) -> None:
        self._raw: Dict[Any, dict] = {}
        self._last: Dict[Any, Tuple[Any, Any]] = {}

    def known(self, key) -> bool:
        return key in self._last

    def report(self, key, fields: dict, tick: bool, continuous, baseline: Optional[dict] = None) -> bool:
        """``baseline``: the consumer's last known state for a key it has not seen in this session (a
        restarted consumer continuing an earlier report); it is used only if the key is unknown."""
        if self._raw.get(key) == fields:
            return False                    # fast path: identical to the last handled observation
        body = clean(fields)
        disc = discrete_projection(body, continuous) if continuous else body
        last = self._last.get(key)
        if last is None and baseline is not None:
            prev = {k: baseline.get(k) for k in fields}
            last = self._last[key] = (discrete_projection(prev, continuous) if continuous else prev, prev)
        if last is not None:
            if last[0] == disc and last[1] != body and not tick:
                return False                # only continuous quantities changed: wait for the next tick
                                            # (not recorded as handled, so the next tick reports it)
            if last[1] == body:
                self._raw[key] = snap(fields)
                return False                # nothing changed
        self._raw[key] = snap(fields)
        self._last[key] = (disc, body)
        return True


# ---------------------------------------------------------------------------- the projection
class OperationalProjection:
    """The operational projection of one simulation engine (one operational scope)."""

    def __init__(self, engine, context_model: Optional[dict] = None, contract: Optional[dict] = None) -> None:
        self.engine = engine
        self.cm = context_model or load_context_model()
        self.contract = contract or load_contract()
        self.scope_id = operational.operational_scope_id(engine)
        self.placement = Isa95Placement(engine, self.cm)
        self._events = operational.OperationalEventView(engine.bus)
        self._chan_cache: Dict[Tuple[str, str], str] = {}

    # ------------------------------------------------------------------ time
    def simulation_time(self) -> int:
        return self.engine.clock.time_s

    def simulation_timestamp(self) -> str:
        return self.engine.clock.timestamp()

    # ------------------------------------------------------------------ entities
    def entity_ids(self) -> List[str]:
        return self.placement.entity_ids()

    def meta(self, cid: str) -> dict:
        """Static description of an entity node."""
        ns, eng, st = self.placement, self.engine, self.engine.state
        etype, native = cid.split(":", 1)
        meta = {"native_id": native, "isa95": ns.isa95_of(cid), "parent": ns.parent(cid)}
        if etype == "product":
            p = eng.config["production"]["products"][native]
            meta.update({"name": p.get("name"), "unit": p.get("unit"), "yields_material": f"material:{p['material']}",
                         "bill_of_materials": {f"material:{m}": q for m, q in p.get("bill_of_materials", {}).items()}})
        else:
            rec = st.entity(native)
            meta["name"] = rec.name
            if native in eng.hierarchy:
                el = eng.hierarchy.get(native)
                meta.update({"level": el.level.value, "class": el.equipment_class, "origin": el.origin.value,
                             "description": el.description, "attributes": dict(el.attributes),
                             "children": [ns.entity_canonical_id(c) for c in el.children]})
            if etype == "utility":
                meta.update({"utility_type": rec.meta.get("utility_type"),
                             "supplied_by": ns.entity_canonical_id(rec.meta["supplied_by"]),
                             "serves": [ns.entity_canonical_id(x) for x in rec.meta.get("serves", [])]})
            if etype == "material":
                meta.update({"attributes": rec.meta.get("attributes", {}), "specification": rec.meta.get("specification", {}),
                             "attribute_units": rec.meta.get("attribute_units", {})})
            meta["configuration"] = {k: v for k, v in operational.properties(rec).items()
                                     if self._channel(rec, k) == "meta"}
            meta["measurements"] = sorted(leaf for leaf, _ in self.measurements(cid))
        return meta

    # ------------------------------------------------------------------ property routing
    def _scope(self, rec) -> Optional[str]:
        if rec.kind == "utility":
            return "utility"
        if rec.id == self.engine.config["quality"].get("laboratory"):
            return "laboratory"
        if rec.kind == "material":
            return "material"
        if rec.meta.get("level") == "StorageUnit":
            return "storage"
        if rec.meta.get("asset"):
            return "asset"
        return None

    def _channel(self, rec, prop: str) -> str:
        """meta | state | measurement | bound (projected as a process variable) | loop"""
        key = (rec.id, prop)
        if key in self._chan_cache:
            return self._chan_cache[key]
        self._chan_cache[key] = ch = self._route(rec, prop)
        return ch

    def _route(self, rec, prop: str) -> str:
        m = self.engine.mapping
        if (rec.id, prop) in {(b.equipment_id, b.property) for b in list(m.xmeas.values()) + list(m.xmv.values())}:
            return "bound"
        if rec.id in m.loops.values():
            return "loop"
        scope = self._scope(rec)
        if scope:
            table = self.contract["properties"][scope]
            spec = table.get(prop)
            if spec is None:
                spec = next((v for k, v in table.items() if "*" in k and prop.startswith(k.rstrip("*"))), None)
            if spec is None and scope == "laboratory":
                spec = {"class": "quality_result"}
            cls = (spec or {}).get("class")
            if cls in META_CLASSES:
                return "meta"
            if cls in MEASUREMENT_CLASSES:
                return "measurement"
        return "state"

    # ------------------------------------------------------------------ measurements
    def measurements(self, cid: str) -> List[Tuple[str, dict]]:
        """(variable, fields) for every operational measurement of an entity node, at the current
        simulation time. ``fields``: variable, value, unit, source, and per kind quality/tag/property
        (XMEAS), property (XMV), loop/loop_tag/pv/output (setpoints and controller outputs)."""
        st, m = self.engine.state, self.engine.mapping
        etype, native = cid.split(":", 1)
        out: List[Tuple[str, dict]] = []
        p = st.process
        for idx, b in sorted(m.xmeas.items()):
            if b.equipment_id == native:
                v = f"measurement:XMEAS({idx})"
                out.append((v, {"variable": v, "value": float(p.xmeas[idx - 1]), "unit": catalog.XMEAS[idx - 1].unit,
                                "quality": p.quality[idx - 1], "property": b.property, "tag": b.tag}))
        for idx, b in sorted(m.xmv.items()):
            if b.equipment_id == native:
                v = f"manipulated_variable:XMV({idx})"
                out.append((v, {"variable": v, "value": float(p.xmv[idx - 1]), "unit": "%", "property": b.property}))
        for lid, cm in sorted(m.loops.items()):
            if cm != native:
                continue
            loop = cs.LOOPS[lid]
            row = next(r for r in p.loops if r["loop_id"] == lid)
            pv_unit = catalog.XMEAS[loop.pv - 1].unit
            if loop.output_kind == "XMV":
                out_unit, target = "%", f"manipulated_variable:XMV({loop.output_index})"
            else:
                slave = cs.loop_for_setpoint(loop.output_index)
                out_unit = catalog.XMEAS[cs.LOOPS[slave].pv - 1].unit
                target = f"setpoint of control_module:{m.loops[slave]}"
            common = {"loop": lid, "loop_tag": loop.tag, "pv": f"measurement:XMEAS({loop.pv})"}
            out.append(("setpoint", dict(common, variable="setpoint", value=row["setpoint"], unit=pv_unit)))
            out.append(("controller_output", dict(common, variable="controller_output", value=row["output"],
                                                  unit=out_unit, output=target)))
        if etype != "product" and st.has_entity(native):
            rec = st.entity(native)
            for prop, val in sorted(operational.properties(rec).items()):
                if self._channel(rec, prop) == "measurement" and isinstance(val, (int, float, np.number)) \
                        and not isinstance(val, bool):
                    out.append((prop, {"variable": prop, "value": val, "unit": rec.units.get(prop, "")}))
        if cid == self.placement.production_unit:
            for k, unit in PRODUCTION_MEASUREMENTS.items():
                out.append((k, {"variable": k, "value": st.production.get(k), "unit": unit}))
        return [(leaf, dict(fields, source="simulator")) for leaf, fields in out]

    @staticmethod
    def measurement_semantics(variable: str) -> str:
        """Timestamp semantics of a measurement variable: ``analyzer_sample`` for the sampled XMEAS
        (catalog measurement_type, with sample_period_h and dead_time_h), else ``step_state``."""
        if variable.startswith("measurement:XMEAS("):
            v = catalog.XMEAS[int(variable[len("measurement:XMEAS("):-1]) - 1]
            if v.measurement_type == catalog.MeasurementType.SAMPLED:
                return ANALYZER_SAMPLE
        return STEP_STATE

    # ------------------------------------------------------------------ state and records
    def entity_state(self, cid: str) -> Optional[dict]:
        """``{"state": {...}, "units": {...}}``: the discrete operational state of an entity node, or
        None if it has none."""
        etype, native = cid.split(":", 1)
        st = self.engine.state
        if etype == "product":
            return None
        rec = st.entity(native)
        props = operational.properties(rec)
        state = {k: v for k, v in props.items() if self._channel(rec, k) in ("state",)}
        if native in self.engine.mapping.loops.values():
            state.update({k: props[k] for k in ("mode", "saturated") if k in props})
        if cid == self.placement.production_unit:
            state.update({f"line_{k}" if k == "state" else k: st.production.get(k) for k in PRODUCTION_STATE})
        if not state:
            return None
        units = {k: rec.units[k] for k in state if k in rec.units}
        return {"state": state, "units": units}

    def records(self) -> Iterator[Tuple[str, str, dict]]:
        """(collection, canonical id, ``{"state": {...}}``) of every operationally observable record, in
        a stable order; each record is placed (registered) under its ISA-95 parent."""
        ns, st = self.placement, self.engine.state
        for coll in sorted(ns.collection_type):
            for rid, rec in list(st.collection(coll).items()):
                cid = ns.register_record(coll, rec, rid)
                d = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
                for k in RECORD_EXCLUDE.get(coll, ()):
                    d.pop(k, None)
                yield coll, cid, {"state": d}

    # ------------------------------------------------------------------ lifecycle and events
    def lifecycle(self) -> dict:
        eng = self.engine
        return {"status": eng.status, "simulation_start": iso(eng.clock.start), "duration_seconds": eng.duration_s}

    def event_entity(self, target: Optional[str]) -> str:
        """The canonical id an event is about: its target entity, else the record it concerns, else the site."""
        ns = self.placement
        if target:
            cid = ns.entity_canonical_id(target)
            if cid:
                return cid
            st = self.engine.state
            for coll in sorted(ns.collection_type):
                if target in st.collection(coll):
                    return ns.register_record(coll, st.collection(coll)[target], target)
        return ns.site

    def event_count(self) -> int:
        """Number of operational events so far in this scope."""
        return len(self._events.records())

    def events_since(self, n: int) -> List[dict]:
        """Operational events after the first ``n`` of the scope, in stream order. Each has entity_id,
        event_id (OE-/LC-), event_type, source, severity, payload, causation_id, correlation_id and the
        simulation time (and timestamp) at which it happened."""
        out = []
        for r in self._events.records(n):
            out.append({"entity_id": self.event_entity(r["target"]), "event_id": r["event_id"],
                        "event_type": r["type"], "source": r["source"], "severity": r["severity"],
                        "payload": r["payload"], "causation_id": r["causation_id"],
                        "correlation_id": r["correlation_id"], "simulation_time": r["simulation_time"],
                        "simulation_timestamp": r["timestamp"]})
        return out
