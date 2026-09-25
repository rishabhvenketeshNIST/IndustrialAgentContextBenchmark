"""Fault type registry.

Two categories, deliberately distinct:

* TEP_NATIVE_FAULT - toggles a native TEP disturbance IDV(k). The consequence is
  computed entirely by teprob.f. teprob.f treats IDV as binary (IDV>0 -> 1), so
  severity is not meaningful and gradual progression is rejected.
* ENTERPRISE_FAULT - writes *cause* contributions to fault-effect channels (or
  the instrumentation layer). The owning simulator module computes the
  consequences, which may reach TEP only through the coupling model.

New fault types are added by subclassing :class:`FaultType` and decorating
with :func:`register`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from ..equipment.instrumentation import SensorOverlay
from ..tep import catalog

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Fault, FaultEngine

TEP_NATIVE = "TEP_NATIVE_FAULT"
ENTERPRISE = "ENTERPRISE_FAULT"

REGISTRY: Dict[str, "FaultType"] = {}


def register(cls):
    inst = cls()
    REGISTRY[inst.name] = inst
    return cls


class FaultValidationError(ValueError):
    pass


class FaultType:
    name = "abstract"
    category = ENTERPRISE
    description = ""
    persistent = False                 # effect stays after stop (until repair or reset)
    supports_gradual = True
    parameters: Dict[str, dict] = {}   # name -> {default, description}
    default_observability = {"direct_indication": False}
    default_expected_effects: List[str] = []

    def targets(self, engine: "FaultEngine") -> List[str]:
        return []

    def validate(self, engine: "FaultEngine", fault: "Fault") -> None:
        valid = self.targets(engine)
        if valid and fault.target not in valid:
            raise FaultValidationError(f"{self.name}: invalid target '{fault.target}'. Valid: {valid}")

    def cause_keys(self, engine: "FaultEngine", fault: "Fault") -> List[str]:
        return [fault.target]

    def apply(self, engine: "FaultEngine", fault: "Fault", intensity: float) -> None:
        raise NotImplementedError

    def remove(self, engine: "FaultEngine", fault: "Fault", reset: bool) -> None:
        engine.state.fault_effects.clear_fault(fault.id)

    def describe(self) -> dict:
        return {"type": self.name, "category": self.category, "description": self.description,
                "persistent": self.persistent, "supports_gradual": self.supports_gradual,
                "parameters": self.parameters, "default_observability": self.default_observability,
                "default_expected_effects": self.default_expected_effects}


# --------------------------------------------------------------------------- TEP native
_IDV_TARGET = "TEP"


class _IDVFault(FaultType):
    category = TEP_NATIVE
    supports_gradual = False
    idv = 0

    def targets(self, engine):
        return [_IDV_TARGET] + list(engine.tep_mapping.idv.get(self.idv, []))

    def validate(self, engine, fault):
        super().validate(engine, fault)
        if fault.progression_mode == "gradual":
            raise FaultValidationError(f"{self.name}: TEP disturbances are binary in teprob.f; "
                                       "use progression 'step' or 'intermittent'")
        if abs(fault.severity - 1.0) > 1e-9:
            raise FaultValidationError(f"{self.name}: severity must be 1.0 (IDV is on/off in teprob.f)")

    def cause_keys(self, engine, fault):
        return [f"IDV({self.idv})"] + list(engine.tep_mapping.idv.get(self.idv, []))

    def apply(self, engine, fault, intensity):
        engine.process.set_disturbance(self.idv, intensity > 0.5)

    def remove(self, engine, fault, reset):
        others = [f for f in engine.active_faults() if f.type == self.name and f.id != fault.id]
        if not others:
            engine.process.set_disturbance(self.idv, False)


def _make_idv(k: int):
    v = catalog.IDV[k - 1]

    class _T(_IDVFault):
        name = f"IDV{k}"
        idv = k
        description = f"TEP native disturbance IDV({k}): {v.description}"
        default_observability = {"direct_indication": False, "note": "consequences observable in XMEAS"}
    _T.__name__ = f"IDV{k}Fault"
    return _T


for _k in range(1, 21):
    register(_make_idv(_k))
UNDOCUMENTED_IDV = {16, 17, 18, 19, 20}


# --------------------------------------------------------------------------- equipment
def _assets(engine, types: Optional[tuple] = None) -> List[str]:
    out = []
    for e in engine.state.entities.values():
        if e.meta.get("asset") and (types is None or e.meta.get("asset_type") in types):
            out.append(e.id)
    return sorted(out)


@register
class EquipmentDegradation(FaultType):
    name = "equipment_degradation"
    persistent = True
    description = "Progressive mechanical wear: equipment health falls by severity (absolute) at full intensity."
    default_expected_effects = ["equipment health decreases", "vibration increases",
                                "capacity-linked utility or process capability decreases"]
    default_observability = {"direct_indication": False, "observable_via": ["vibration", "process response"]}

    def targets(self, engine):
        return _assets(engine)

    def apply(self, engine, fault, intensity):
        engine.state.fault_effects.set(fault.target, "damage", fault.id, fault.severity * intensity)


@register
class CoolingDegradation(FaultType):
    name = "cooling_degradation"
    persistent = True
    description = ("Loss of cooling capability. On a CW pump: mechanical degradation (health). On the cooling "
                   "tower: thermal-performance loss (supply temperature rise). On a CW utility service: "
                   "capacity loss.")
    default_expected_effects = ["cooling water capacity or supply temperature degrades",
                                "reactor/condenser cooling valve opens further",
                                "reactor temperature rises if the valve saturates", "product composition shifts"]

    def targets(self, engine):
        return _assets(engine, ("centrifugal_pump", "cooling_tower")) + \
            sorted(e.id for e in engine.state.entities_of_kind("utility") if e.meta.get("utility_type") ==
                   "cooling_water")

    def _channel(self, engine, target):
        ent = engine.state.entity(target)
        if ent.kind == "utility":
            return "capacity_loss"
        return "thermal_degradation" if ent.meta.get("asset_type") == "cooling_tower" else "damage"

    def apply(self, engine, fault, intensity):
        engine.state.fault_effects.set(fault.target, self._channel(engine, fault.target), fault.id,
                                       fault.severity * intensity)


@register
class PumpEfficiencyLoss(FaultType):
    name = "pump_efficiency_loss"
    persistent = True
    description = ("Hydraulic efficiency loss (impeller wear/recirculation) without a change in mechanical "
                   "condition indicators. Efficiency multiplier = 1 - severity.")
    default_observability = {"direct_indication": False, "observable_via": ["utility flow / valve position"]}
    default_expected_effects = ["pump deliverable flow decreases", "utility capacity decreases"]

    def targets(self, engine):
        return _assets(engine, ("centrifugal_pump", "centrifugal_compressor", "boiler"))

    def apply(self, engine, fault, intensity):
        engine.state.fault_effects.set(fault.target, "efficiency_loss", fault.id, fault.severity * intensity)


@register
class EquipmentFailure(FaultType):
    name = "equipment_failure"
    persistent = True
    supports_gradual = False
    description = "Sudden failure (trip). Equipment stops until repaired; redundant units may auto-start."
    default_observability = {"direct_indication": True, "observable_via": ["equipment status", "alarms"]}
    default_expected_effects = ["equipment FAILED", "auto-changeover if a standby exists", "work order raised"]

    def targets(self, engine):
        return _assets(engine)

    def apply(self, engine, fault, intensity):
        if intensity > 0.5:
            engine.state.fault_effects.set(fault.target, "failed", fault.id, 1.0)


# --------------------------------------------------------------------------- sensors
class _SensorFault(FaultType):
    kind = "bias"

    def targets(self, engine):
        return [f"XMEAS({i})" for i in range(1, catalog.NUM_XMEAS + 1)]

    def validate(self, engine, fault):
        fault.target = engine.resolve_measurement(fault.target)
        super().validate(engine, fault)

    def cause_keys(self, engine, fault):
        b = engine.tep_mapping.binding(fault.target)
        return [fault.target, b.equipment_id]

    def _index(self, fault) -> int:
        return int(catalog.normalize_id(fault.target)[6:-1])

    def _magnitude(self, fault) -> float:
        return 0.0

    def apply(self, engine, fault, intensity):
        instr = engine.process.instrumentation
        idx = self._index(fault)
        if not any(o.fault_id == fault.id for o in instr.overlays.get(idx, [])):
            instr.add(idx, SensorOverlay(fault.id, self.kind, self._magnitude(fault), engine.clock.time_s,
                                         intensity))
        instr.set_intensity(fault.id, intensity)

    def remove(self, engine, fault, reset):
        engine.process.instrumentation.remove_fault(fault.id)


def _base(fault) -> float:
    v = catalog.variable(fault.target)
    return abs(v.base_value) if v.base_value else 1.0


@register
class SensorBias(_SensorFault):
    name = "sensor_bias"
    kind = "bias"
    description = "Constant offset on a transmitter. Controllers act on the biased value; the process does not."
    parameters = {"bias": {"default": None, "description": "offset in engineering units "
                                                          "(default: severity x 10% of base value)"}}
    default_expected_effects = ["transmitted value offset", "closed loop drives the true process off target"]

    def _magnitude(self, fault):
        b = fault.parameters.get("bias")
        return float(b) if b is not None else fault.severity * 0.10 * _base(fault)


@register
class SensorDrift(_SensorFault):
    name = "sensor_drift"
    kind = "drift"
    description = "Linearly growing transmitter error."
    parameters = {"rate_per_h": {"default": None, "description": "drift rate in units/h "
                                                                 "(default: severity x 5% of base value per h)"}}

    def _magnitude(self, fault):
        r = fault.parameters.get("rate_per_h")
        return float(r) if r is not None else fault.severity * 0.05 * _base(fault)


@register
class SensorDropout(_SensorFault):
    name = "sensor_dropout"
    kind = "dropout"
    supports_gradual = False
    description = "Loss of signal: the DCS holds the last good value and flags the point BAD."
    default_observability = {"direct_indication": True, "observable_via": ["measurement quality BAD"]}


# --------------------------------------------------------------------------- utilities
@register
class UtilityCapacityLoss(FaultType):
    name = "utility_capacity_loss"
    description = "Loss of a fraction (= severity) of a utility service's deliverable capacity."
    default_expected_effects = ["utility available capacity falls", "coupled TEP boundary limited"]

    def targets(self, engine):
        return sorted(e.id for e in engine.state.entities_of_kind("utility"))

    def apply(self, engine, fault, intensity):
        engine.state.fault_effects.set(fault.target, "capacity_loss", fault.id, fault.severity * intensity)


# --------------------------------------------------------------------------- materials
@register
class RawMaterialQualityDeviation(FaultType):
    name = "raw_material_quality_deviation"
    description = ("Composition of the raw material being fed deviates from its certificate. "
                   "Deviation = severity x max_deviation on the chosen attribute.")
    parameters = {"attribute": {"default": None, "description": "lot attribute (default: first attribute)"},
                  "max_deviation": {"default": None, "description": "absolute deviation at severity 1 "
                                                                     "(default: 4x the spec margin)"}}
    default_expected_effects = ["feed composition changes at the TEP boundary", "purge/product composition shift"]

    def targets(self, engine):
        return sorted(m for m, spec in engine.config.get("materials", {}).get("materials", {}).items()
                      if spec.get("attributes"))

    def _attr(self, engine, fault) -> str:
        attrs = engine.config["materials"]["materials"][fault.target]["attributes"]
        a = fault.parameters.get("attribute") or next(iter(attrs))
        if a not in attrs:
            raise FaultValidationError(f"{fault.target} has no attribute '{a}' ({list(attrs)})")
        return a

    def validate(self, engine, fault):
        super().validate(engine, fault)
        self._attr(engine, fault)

    def apply(self, engine, fault, intensity):
        spec = engine.config["materials"]["materials"][fault.target]
        a = self._attr(engine, fault)
        dev = fault.parameters.get("max_deviation")
        if dev is None:
            lim = spec.get("specification", {}).get(a, {})
            nominal = spec["attributes"][a]
            ref = lim.get("max", nominal * 2 if nominal else 0.01)
            dev = 4.0 * (ref - nominal) if ref != nominal else 0.01
        engine.state.fault_effects.set(f"{fault.target}.{a}", "value", fault.id,
                                       float(dev) * fault.severity * intensity)


@register
class RawMaterialShortage(FaultType):
    name = "raw_material_shortage"
    description = ("Supplier cannot deliver while active, and a fraction (= severity) of on-hand stock is "
                   "rejected/unavailable. Written-off stock does not return when the fault stops.")
    default_expected_effects = ["deliveries delayed", "storage level drops", "feed supply lost when empty",
                                "production orders blocked"]
    default_observability = {"direct_indication": True, "observable_via": ["inventory", "purchase orders"]}

    def targets(self, engine):
        return sorted(m for m, spec in engine.config.get("materials", {}).get("materials", {}).items()
                      if spec.get("category") == "raw_material")

    def apply(self, engine, fault, intensity):
        fe = engine.state.fault_effects
        fe.set(fault.target, "supply_disruption", fault.id, 1.0 if intensity > 0 else 0.0)
        fe.set(fault.target, "stock_loss", fault.id, fault.severity * intensity)


# --------------------------------------------------------------------------- maintenance
@register
class MaintenanceDelay(FaultType):
    name = "maintenance_delay"
    description = "Maintenance dispatch is delayed by severity x max_delay_s."
    parameters = {"max_delay_s": {"default": 7200, "description": "delay at severity 1"}}
    default_expected_effects = ["work orders start later", "degraded equipment stays in service longer"]

    def targets(self, engine):
        return ["MAINTENANCE"]

    def apply(self, engine, fault, intensity):
        engine.state.fault_effects.set("MAINTENANCE", "response_delay_s", fault.id,
                                       float(fault.parameters.get("max_delay_s", 7200)) * fault.severity * intensity)


@register
class SparePartShortage(FaultType):
    name = "spare_part_shortage"
    supports_gradual = False
    description = "Spare part stock is unavailable (lost, wrong part, quality hold)."
    default_expected_effects = ["work orders wait for parts"]

    def targets(self, engine):
        return sorted(engine.state.collection("spare_parts").keys())

    def apply(self, engine, fault, intensity):
        engine.state.fault_effects.set(fault.target, "stock_blocked", fault.id, 1.0 if intensity > 0 else 0.0)


# --------------------------------------------------------------------------- production & quality
@register
class ProductionOrderDelay(FaultType):
    name = "production_order_delay"
    description = "Order release from planning is delayed by severity x max_delay_s (or blocked)."
    parameters = {"max_delay_s": {"default": 3600, "description": "delay at severity 1"},
                  "block": {"default": False, "description": "block release while active"}}

    def targets(self, engine):
        return ["ERP"] + sorted(engine.state.collection("production_orders").keys())

    def apply(self, engine, fault, intensity):
        fe = engine.state.fault_effects
        if fault.parameters.get("block"):
            fe.set(fault.target, "blocked", fault.id, 1.0 if intensity > 0 else 0.0)
        else:
            fe.set(fault.target, "release_delay_s", fault.id,
                   float(fault.parameters.get("max_delay_s", 3600)) * fault.severity * intensity)


@register
class QualityFailure(FaultType):
    name = "quality_failure"
    description = ("Quality testing error (analyzer/lab calibration): reported test values are offset by "
                   "severity x offset. Product itself is unaffected.")
    parameters = {"offset": {"default": None, "description": "offset at severity 1 "
                                                             "(default: 2 x specification width)"}}
    default_expected_effects = ["false out-of-specification results", "lots quarantined or rejected"]

    def targets(self, engine):
        out = []
        for tests in engine.config.get("quality", {}).get("product_specifications", {}).values():
            out += [t["id"] for t in tests.get("tests", [])]
        return sorted(out)

    def apply(self, engine, fault, intensity):
        off = fault.parameters.get("offset")
        if off is None:
            for tests in engine.config["quality"]["product_specifications"].values():
                for t in tests["tests"]:
                    if t["id"] == fault.target:
                        lo, hi = t.get("low"), t.get("high")
                        width = (hi - lo) if (lo is not None and hi is not None) else abs(hi or lo or 1.0) * 0.5
                        off = 2.0 * width if hi is not None else -2.0 * width
        engine.state.fault_effects.set(fault.target, "test_offset", fault.id, float(off) * fault.severity * intensity)


def catalog_list() -> List[dict]:
    out = []
    for name, ft in REGISTRY.items():
        d = ft.describe()
        if ft.category == TEP_NATIVE:
            d["documented"] = int(name[3:]) not in UNDOCUMENTED_IDV
        out.append(d)
    return out
