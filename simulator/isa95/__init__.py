"""ISA-95 role-based equipment hierarchy (IEC 62264-1 / OPC UA for ISA-95, OPC 10030).

Levels and permitted containment follow the ISA-95 equipment hierarchy model:

    Enterprise > Site > Area > {Process Cell > Unit,
                                Production Unit,
                                Production Line > Work Cell,
                                Storage Zone > Storage Unit,
                                Work Center > Work Unit}
    lower-level equipment: Equipment Module > Control Module (ISA-88 style)

TEP variables are *not* equipment: they are attached to equipment elements as
properties (see configs/tep_mapping.yaml).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional

from ..common import ConfigError


class EquipmentLevel(str, Enum):
    ENTERPRISE = "Enterprise"
    SITE = "Site"
    AREA = "Area"
    PROCESS_CELL = "ProcessCell"
    UNIT = "Unit"
    PRODUCTION_LINE = "ProductionLine"
    WORK_CELL = "WorkCell"
    PRODUCTION_UNIT = "ProductionUnit"
    STORAGE_ZONE = "StorageZone"
    STORAGE_UNIT = "StorageUnit"
    WORK_CENTER = "WorkCenter"
    WORK_UNIT = "WorkUnit"
    EQUIPMENT_MODULE = "EquipmentModule"
    CONTROL_MODULE = "ControlModule"


L = EquipmentLevel
_LOWER = {L.EQUIPMENT_MODULE, L.CONTROL_MODULE}

ALLOWED_CHILDREN: Dict[EquipmentLevel, set] = {
    L.ENTERPRISE: {L.SITE},
    L.SITE: {L.AREA},
    L.AREA: {L.PROCESS_CELL, L.PRODUCTION_UNIT, L.PRODUCTION_LINE, L.STORAGE_ZONE, L.WORK_CENTER},
    L.PROCESS_CELL: {L.UNIT},
    L.UNIT: set(_LOWER),
    L.PRODUCTION_UNIT: {L.UNIT} | _LOWER,
    L.PRODUCTION_LINE: {L.WORK_CELL},
    L.WORK_CELL: set(_LOWER),
    L.STORAGE_ZONE: {L.STORAGE_UNIT},
    L.STORAGE_UNIT: set(_LOWER),
    L.WORK_CENTER: {L.WORK_UNIT},
    L.WORK_UNIT: set(_LOWER),
    L.EQUIPMENT_MODULE: set(_LOWER),
    L.CONTROL_MODULE: {L.CONTROL_MODULE},
}


class Origin(str, Enum):
    TEP = "TEP"                 # equipment that exists in the Downs & Vogel process model
    ENTERPRISE = "ENTERPRISE"   # added by the enterprise simulation layer


@dataclass
class EquipmentElement:
    id: str
    name: str
    level: EquipmentLevel
    parent_id: Optional[str]
    origin: Origin
    description: str = ""
    equipment_class: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    children: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "level": self.level.value, "parent_id": self.parent_id,
                "origin": self.origin.value, "description": self.description,
                "equipment_class": self.equipment_class, "attributes": dict(self.attributes),
                "children": list(self.children)}


class Hierarchy:
    def __init__(self, elements: List[EquipmentElement]) -> None:
        self.elements: Dict[str, EquipmentElement] = {}
        for e in elements:
            if e.id in self.elements:
                raise ConfigError(f"Duplicate equipment id '{e.id}'")
            self.elements[e.id] = e
        for e in elements:
            if e.parent_id is not None:
                if e.parent_id not in self.elements:
                    raise ConfigError(f"'{e.id}' references unknown parent '{e.parent_id}'")
                self.elements[e.parent_id].children.append(e.id)
        self.validate()

    # -- construction -----------------------------------------------------
    @classmethod
    def from_config(cls, cfg: dict) -> "Hierarchy":
        """Build from the nested ``configs/site.yaml`` structure."""
        elements: List[EquipmentElement] = []

        def walk(node: dict, parent: Optional[str]) -> None:
            for key in ("id", "name", "level"):
                if key not in node:
                    raise ConfigError(f"Equipment element missing '{key}': {node}")
            try:
                level = EquipmentLevel(node["level"])
            except ValueError as exc:
                raise ConfigError(f"Invalid ISA-95 level '{node['level']}' for {node['id']}") from exc
            origin = Origin(node.get("origin", "ENTERPRISE"))
            elements.append(EquipmentElement(
                id=node["id"], name=node["name"], level=level, parent_id=parent, origin=origin,
                description=node.get("description", ""), equipment_class=node.get("class", ""),
                attributes=dict(node.get("attributes", {}))))
            for child in node.get("children", []) or []:
                walk(child, node["id"])

        walk(cfg["enterprise"], None)
        return cls(elements)

    # -- validation -----------------------------------------------------------
    def validate(self) -> None:
        roots = [e for e in self.elements.values() if e.parent_id is None]
        if len(roots) != 1 or roots[0].level != L.ENTERPRISE:
            raise ConfigError("Hierarchy must have exactly one root at the Enterprise level")
        for e in self.elements.values():
            if e.parent_id is None:
                continue
            parent = self.elements[e.parent_id]
            if e.level not in ALLOWED_CHILDREN[parent.level]:
                raise ConfigError(f"ISA-95 violation: {e.level.value} '{e.id}' cannot be contained in "
                                  f"{parent.level.value} '{parent.id}'")
        # cycle check
        for e in self.elements.values():
            seen = set()
            cur = e
            while cur.parent_id is not None:
                if cur.id in seen:
                    raise ConfigError(f"Cycle in hierarchy at '{cur.id}'")
                seen.add(cur.id)
                cur = self.elements[cur.parent_id]

    # -- queries --------------------------------------------------------------
    @property
    def root(self) -> EquipmentElement:
        return next(e for e in self.elements.values() if e.parent_id is None)

    def get(self, element_id: str) -> EquipmentElement:
        try:
            return self.elements[element_id]
        except KeyError as exc:
            raise KeyError(f"Unknown equipment element '{element_id}'") from exc

    def __contains__(self, element_id: str) -> bool:
        return element_id in self.elements

    def by_level(self, level: EquipmentLevel) -> List[EquipmentElement]:
        return [e for e in self.elements.values() if e.level == level]

    def ancestors(self, element_id: str) -> List[EquipmentElement]:
        out = []
        cur = self.get(element_id)
        while cur.parent_id is not None:
            cur = self.elements[cur.parent_id]
            out.append(cur)
        return out

    def path(self, element_id: str) -> List[str]:
        return [e.id for e in reversed(self.ancestors(element_id))] + [element_id]

    def area_of(self, element_id: str) -> Optional[str]:
        e = self.get(element_id)
        if e.level == L.AREA:
            return e.id
        for a in self.ancestors(element_id):
            if a.level == L.AREA:
                return a.id
        return None

    def descendants(self, element_id: str) -> Iterator[EquipmentElement]:
        for cid in self.get(element_id).children:
            yield self.elements[cid]
            yield from self.descendants(cid)

    def tree(self, element_id: Optional[str] = None) -> dict:
        e = self.get(element_id) if element_id else self.root
        d = e.to_dict()
        d["children"] = [self.tree(c) for c in e.children]
        return d
