"""TEP variable -> ISA-95 equipment/property mapping (configs/tep_mapping.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..common import ConfigError
from ..tep import catalog
from ..tep import control_scheme as cs
from . import EquipmentLevel, Hierarchy


@dataclass(frozen=True)
class VariableBinding:
    var_id: str                  # XMEAS(9)
    equipment_id: str            # EM-REACTOR
    property: str                # temperature
    area_id: Optional[str]
    unit: str
    name: str
    tag: Optional[str] = None
    process_equipment_id: Optional[str] = None

    def to_dict(self) -> dict:
        v = catalog.variable(self.var_id)
        d = v.to_dict()
        d.update({"equipment_id": self.equipment_id, "property": self.property, "area_id": self.area_id,
                  "tag": self.tag, "process_equipment_id": self.process_equipment_id})
        return d


@dataclass
class TEPMapping:
    xmeas: Dict[int, VariableBinding] = field(default_factory=dict)
    xmv: Dict[int, VariableBinding] = field(default_factory=dict)
    idv: Dict[int, List[str]] = field(default_factory=dict)
    idv_notes: Dict[int, str] = field(default_factory=dict)
    loops: Dict[int, str] = field(default_factory=dict)   # loop_id -> control module id

    @classmethod
    def from_config(cls, cfg: dict, hierarchy: Hierarchy) -> "TEPMapping":
        m = cls()

        def check(eq: str, what: str) -> None:
            if eq not in hierarchy:
                raise ConfigError(f"{what} maps to unknown equipment '{eq}'")

        for idx, spec in (cfg.get("xmeas") or {}).items():
            idx = int(idx)
            v = catalog.XMEAS[idx - 1]
            check(spec["equipment"], v.id)
            m.xmeas[idx] = VariableBinding(v.id, spec["equipment"], spec["property"],
                                           hierarchy.area_of(spec["equipment"]), v.unit, v.name, spec.get("tag"))
        for idx, spec in (cfg.get("xmv") or {}).items():
            idx = int(idx)
            v = catalog.XMV[idx - 1]
            check(spec["equipment"], v.id)
            pe = spec.get("process_equipment")
            if pe:
                check(pe, v.id)
            m.xmv[idx] = VariableBinding(v.id, spec["equipment"], spec["property"],
                                         hierarchy.area_of(spec["equipment"]), v.unit, v.name,
                                         None, pe)
        for idx, spec in (cfg.get("idv") or {}).items():
            idx = int(idx)
            eqs = spec["equipment"] if isinstance(spec["equipment"], list) else [spec["equipment"]]
            for eq in eqs:
                check(eq, f"IDV({idx})")
            m.idv[idx] = eqs
            if spec.get("code_effect"):
                m.idv_notes[idx] = spec["code_effect"]
        # control loops -> control modules (declared in site.yaml attributes.loop_id)
        for e in hierarchy.elements.values():
            lid = e.attributes.get("loop_id")
            if lid is not None:
                if int(lid) not in cs.LOOPS:
                    raise ConfigError(f"{e.id} references unknown native loop {lid}")
                if e.level != EquipmentLevel.CONTROL_MODULE:
                    raise ConfigError(f"Loop {lid} must be a ControlModule, got {e.level.value}")
                m.loops[int(lid)] = e.id
        m.validate()
        return m

    def validate(self) -> None:
        missing = [i for i in range(1, catalog.NUM_XMEAS + 1) if i not in self.xmeas]
        if missing:
            raise ConfigError(f"XMEAS without equipment mapping: {missing}")
        missing = [i for i in range(1, catalog.NUM_XMV + 1) if i not in self.xmv]
        if missing:
            raise ConfigError(f"XMV without equipment mapping: {missing}")
        missing = [i for i in range(1, catalog.NUM_IDV + 1) if i not in self.idv]
        if missing:
            raise ConfigError(f"IDV without equipment mapping: {missing}")
        missing = [lid for lid in cs.LOOPS if lid not in self.loops]
        if missing:
            raise ConfigError(f"Native loops without control module: {missing}")
        seen = {}
        for b in self.xmeas.values():
            key = (b.equipment_id, b.property)
            if key in seen:
                raise ConfigError(f"{b.var_id} and {seen[key]} both map to {key}")
            seen[key] = b.var_id

    def bindings_for(self, equipment_id: str) -> List[VariableBinding]:
        out = [b for b in self.xmeas.values() if b.equipment_id == equipment_id]
        out += [b for b in self.xmv.values() if b.equipment_id == equipment_id
                or b.process_equipment_id == equipment_id]
        return out

    def binding(self, var_id: str) -> VariableBinding:
        vid = catalog.normalize_id(var_id)
        kind, idx = vid.split("(")
        idx = int(idx.rstrip(")"))
        table = {"XMEAS": self.xmeas, "XMV": self.xmv}.get(kind)
        if table is None or idx not in table:
            raise KeyError(f"No equipment binding for {var_id}")
        return table[idx]

    def resolve_property(self, equipment_id: str, prop: str) -> Optional[str]:
        """Return the TEP variable id behind an equipment property, if any."""
        for b in self.xmeas.values():
            if b.equipment_id == equipment_id and b.property == prop:
                return b.var_id
        for b in self.xmv.values():
            if b.equipment_id == equipment_id and b.property == prop:
                return b.var_id
        return None
