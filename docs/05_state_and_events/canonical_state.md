# Canonical state

`CanonicalState` (`simulator/state/__init__.py`) is the simulator's internal source of truth. It is an
in-memory Python object, not a Unified Namespace and not persisted. All modules read and write it; the
API serialises views of it.

## Contents

| Part | Type | Owner(s) | Content |
|---|---|---|---|
| `entities` | `id → EntityRecord{kind, name, properties, units, meta}` | the module owning each property ([variable reference](variable_reference.md)) | 87 equipment elements, 4 utility services, 5 materials |
| `process` | `ProcessState` | `ProcessInterface.sync` only | TEP process image (below) |
| `collections` | `name → {id → record}` | per collection (below) | typed records |
| `causal` | `CausalRegistry` | fault engine and coupling engine | entity key → the event explaining its abnormal state |
| `fault_effects` | `FaultEffects` | fault engine (write); owning modules and coupling (read) | cause channels |
| `run` | dict | engine | manifest fields |
| `production` | dict | `ProductionModule` | line state, rate, totals, OEE, current lot and order |

## Process image (`ProcessState`)

| Field | Meaning | Truth or observation |
|---|---|---|
| `xmeas_true[41]` | XMEAS as computed by TEP (with TEP's own noise) | **process truth** |
| `xmeas[41]` | transmitted XMEAS after sensor overlays | observation |
| `quality[41]` | GOOD / BAD per channel (BAD = dropout) | observation |
| `xmv[12]` | manipulated variables | commanded (truth) |
| `setpoints` | loop id → SETPT | commanded |
| `loops` | per loop: pv (transmitted), pv_quality, setpoint, output, mode, error, saturated, override … | mixed |
| `idv[20]` | disturbance flags | **benchmark truth** (a TEP-native fault is visible here) |
| `control_mode`, `shutdown`, `shutdown_reason`, `shutdown_time_s`, `tep_time_h` | — | truth |
| `boundary` | current boundary parameter values | **simulation truth**; reveals causes |
| `analyzer_updates` | group → new result this step | internal |

## Collections

| Collection | Record | Owner |
|---|---|---|
| `alarms` | `Alarm` (with its `AlarmDefinition`) | AlarmModule |
| `work_orders`, `technicians` | `WorkOrder`, `Technician` | MaintenanceModule |
| `spare_parts`, `purchase_orders` | `SparePart`, `PurchaseOrder` | InventoryModule |
| `material_lots` | `MaterialLot` | MaterialsModule creates the initial lots; InventoryModule creates received lots and consumes all of them; QualityModule decides their status through `LOT_STATE_CHANGED` |
| `production_orders` | `ProductionOrder` | SchedulingModule (creation, release, sequencing), ProductionModule (progress, completion, pause) |
| `production_lots` | `ProductionLot` | ProductionModule (open/close); QualityModule (disposition); WarehouseModule (location) |
| `quality_samples` | `QualitySample` | QualityModule |
| `shipments` | `Shipment` | WarehouseModule |
| `faults` | `Fault` | FaultEngine (**benchmark**) |
| `operator_log` | dict entries | OperatorModule |

Some records have several writers. Production orders and lots are the clearest cases, and they change
only through defined functions (`transition`) or event handlers. There is no enforcement of property
ownership: modules *could* write any property. The ownership table is observed behaviour, not a
guarantee.

## Serialisation

* **`CanonicalState.snapshot(include_truth)`:** `include_truth=False` drops `xmeas_true`, `idv` and
  the `faults` collection. `SimulationEngine.snapshot` exposes this, but **no API route calls it with
  `include_truth=False`**.
* **`to_jsonable`** converts numpy values and removes non-finite floats (they become `null`).

## What is authoritative, per kind of state

| State | Authoritative source | Everything else is a copy |
|---|---|---|
| Process measurements | TEP `/PV/ XMEAS` → `process.xmeas_true` | `process.xmeas`, equipment properties, loop PVs, trend buffer |
| Manipulated variables | TEP `/PV/ XMV` | `process.xmv`, control-module properties |
| Setpoints and modes | TEP `/CTRLALL/` + adapter loop modes | `process.setpoints`, `process.loops` |
| Equipment condition | `EquipmentModule` (`Asset.intrinsic_health` − fault damage) | `health` property |
| Equipment capability | coupling relations (`efficiency`, `available_flow`, `capability`) | — |
| Utility capacity | coupling relations; status/availability by `UtilitiesModule` | — |
| TEP boundary | `ProcessInterface._boundary_cache` = TEP memory after clamping | `process.boundary` |
| Inventory | `MaterialLot` records | storage entity properties (recomputed every step) |
| Production | `ProductionModule` + order and lot records | `state.production` |
| Quality | `QualitySample` + lot status | lab entity properties |
| Maintenance | `WorkOrder`, `Technician` records | — |
| Events | `EventBus.log` | exports, API views |
| Benchmark ground truth | `faults` collection, `FaultEffects`, `CausalRegistry`, sensor overlays | event correlation fields |

Source:
- `simulator/state/__init__.py` — `CanonicalState`, `ProcessState`, `EntityRecord`, `CausalRegistry`, `CanonicalState.snapshot`
- `simulator/faults/effects.py` — `FaultEffects`
- `simulator/simulation/process_interface.py` — `ProcessInterface.sync`
