"""The UNS topic namespace, derived from the simulator's ISA-95 hierarchy and the canonical context model.

No hierarchy is defined here. Every entity node is placed by facts the simulator and the context
model already contain:

* hierarchy elements: their ISA-95 parent chain (configs/site.yaml), one topic segment per level;
* utilities: under the work center that supplies them (``supplied_by``);
* materials, products, technicians: under the site (site-scoped definitions and personnel);
* records: under the entity named by their context-model relationship (e.g. a work order under the
  asset it is for, a sample under the analyzer or laboratory it came from).

A topic segment is a canonical id ``<entity_type>:<native_id>`` escaped for MQTT (see ``escape``).
Channel segments (``meta``, ``state``, ``lifecycle``, ``measurement``, ``event``, ``uns``) never contain
':' and so cannot be confused with entity segments.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from simulator.isa95 import EquipmentLevel

from . import DEFAULT_ROOT

ROOT_DIR = Path(__file__).resolve().parents[1]
CONTEXT_MODEL = ROOT_DIR / "contract" / "context_model.yaml"

# Characters with a meaning in MQTT topics (level separator, wildcards, NUL) and the escape character.
_ESCAPES = {"%": "%25", "/": "%2F", "+": "%2B", "#": "%23", "\x00": "%00"}


def escape(segment: str) -> str:
    """Escape a canonical id for use as one MQTT topic level. Only '%', '/', '+', '#' and NUL are
    percent-encoded (plus a leading '$', reserved for broker topics). The payload always carries the
    unescaped canonical id."""
    out = "".join(_ESCAPES.get(c, c) for c in segment)
    return "%24" + out[1:] if out.startswith("$") else out


def unescape(segment: str) -> str:
    for plain, enc in [("$", "%24")] + [(k, v) for k, v in _ESCAPES.items() if k != "%"] + [("%", "%25")]:
        segment = segment.replace(enc, plain)
    return segment


def canonical_id(entity_type: str, native_id: str) -> str:
    return f"{entity_type}:{native_id}"


def load_context_model(path: Path = CONTEXT_MODEL) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class Namespace:
    """Topic paths for one engine (one identity scope)."""

    def __init__(self, engine, context_model: Optional[dict] = None, root: str = DEFAULT_ROOT) -> None:
        self.engine = engine
        self.root = root.strip("/")
        self.cm = context_model or load_context_model()
        types = self.cm["entity_types"]
        self.level_type = {d["source"]["hierarchy_level"]: t for t, d in types.items()
                           if "hierarchy_level" in d["source"]}
        self.kind_type = {d["source"]["entity_kind"]: t for t, d in types.items() if "entity_kind" in d["source"]}
        # only collections whose records are operational are part of the UNS
        self.collection_type = {d["source"]["collection"]: t for t, d in types.items()
                                if "collection" in d["source"] and d["observability"] == "operational"}
        self.isa95 = {m["id"]: m for m in self.cm["isa95_mapping"]}
        self.types = types
        self._path: Dict[str, Tuple[str, ...]] = {}       # canonical id -> path of canonical ids
        self._native: Dict[str, str] = {}                 # entity native id -> canonical id
        self._topics: Dict[Tuple[str, str, Optional[str]], str] = {}
        self._build()

    # ------------------------------------------------------------------ construction
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

    # ------------------------------------------------------------------ lookups
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
        """The entity a record is published under (its context-model relationship)."""
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

    # ------------------------------------------------------------------ topics
    def topic(self, cid: str, channel: str, leaf: Optional[str] = None) -> str:
        key = (cid, channel, leaf)
        t = self._topics.get(key)
        if t is None:
            parts = [self.root] + [escape(s) for s in self._path[cid]] + [channel]
            if leaf is not None:
                parts.append(escape(leaf))
            t = self._topics[key] = "/".join(parts)
        return t

    def publisher_status_topic(self) -> str:
        return self.topic(self.site, "uns", "publisher")
