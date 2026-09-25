"""Materials module: material master data, material lots and TEP feed conversions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from ..common import ConfigError
from ..simulation.module import ModuleContext, SimulationModule
from ..tep import catalog


def kg_per_h_factor(tep_spec: dict) -> float:
    """Multiplier converting the TEP feed measurement into kg/h (TEFUNC unit equations)."""
    conv = tep_spec.get("conversion")
    if conv == "kg_h":
        return 1.0
    if conv == "kscmh":
        fracs = tep_spec.get("mole_fractions") or {}
        if abs(sum(fracs.values()) - 1.0) > 1e-6:
            raise ConfigError(f"Mole fractions must sum to 1: {fracs}")
        mw = sum(x * catalog.COMPONENT_MW[c] for c, x in fracs.items())
        return catalog.KSCMH_TO_LBMOL_PER_H * mw * catalog.LB_TO_KG
    raise ConfigError(f"Unknown conversion '{conv}'")


@dataclass
class MaterialLot:
    lot_id: str
    material_id: str
    location: str
    initial_kg: float
    quantity_kg: float
    received_s: int
    supplier: Optional[str]
    attributes: Dict[str, float] = field(default_factory=dict)
    quality_status: str = "RELEASED"      # RELEASED | QUARANTINE | REJECTED | CONSUMED
    consumed_kg: float = 0.0

    def to_dict(self) -> dict:
        return {"lot_id": self.lot_id, "material_id": self.material_id, "location": self.location,
                "initial_kg": round(self.initial_kg, 3), "quantity_kg": round(self.quantity_kg, 3),
                "consumed_kg": round(self.consumed_kg, 3), "received_s": self.received_s,
                "supplier": self.supplier, "attributes": dict(self.attributes),
                "quality_status": self.quality_status}


class MaterialsModule(SimulationModule):
    name = "materials"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("materials")
        self.materials: Dict[str, dict] = cfg.get("materials", {}) or {}
        self.factors: Dict[str, float] = {}
        for mid, spec in self.materials.items():
            props = {"category": spec.get("category"), "unit": spec.get("unit", "kg")}
            meta = {"attributes": spec.get("attributes", {}), "specification": spec.get("specification", {}),
                    "attribute_units": spec.get("attribute_units", {}), "tep": spec.get("tep")}
            if spec.get("tep"):
                self.factors[mid] = kg_per_h_factor(spec["tep"])
                meta["kg_per_h_per_measurement_unit"] = self.factors[mid]
                props["tep_measurement"] = catalog.normalize_id(spec["tep"]["measurement"])
            ctx.state.register_entity(mid, "material", spec.get("name", mid), props, {}, meta)
        lots = ctx.state.collection("material_lots")
        for spec in cfg.get("initial_lots", []) or []:
            mid = spec["material"]
            if mid not in self.materials:
                raise ConfigError(f"Lot {spec['id']} references unknown material {mid}")
            attrs = dict(self.materials[mid].get("attributes", {}))
            attrs.update(spec.get("attributes", {}))
            lots[spec["id"]] = MaterialLot(spec["id"], mid, spec["storage"], float(spec["quantity_kg"]),
                                           float(spec["quantity_kg"]), 0, spec.get("supplier"), attrs)
