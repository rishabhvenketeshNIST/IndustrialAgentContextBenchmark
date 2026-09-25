# Canonical Context Model

| | |
|---|---|
| **Context model version** | 0.2.0 (draft; decisions U-01 to U-07 resolved) |
| **Describes** | simulator 1.0.0, canonical contract 0.2.0 |
| **Machine-readable definition** | `contract/context_model.yaml` (checked by `tests/test_context_model.py`) |
| **ISA-95 mapping** | [ISA95_SIMULATOR_MAPPING.md](ISA95_SIMULATOR_MAPPING.md) |
| **Decisions** | [CONTEXT_MODEL_DECISIONS.md](CONTEXT_MODEL_DECISIONS.md) |
| **Rules for UNS / historian / KG** | [CONTEXT_PROJECTION_PRINCIPLES.md](CONTEXT_PROJECTION_PRINCIPLES.md) |

## 1. Purpose

The canonical context model organises what the enterprise simulator already produces into five parts:

* **identity** (what things exist);
* **observations** (what can be seen about them);
* **state** (who owns which part of reality);
* **events** (what happened);
* **relationships** (how things connect).

It gives future information systems one shared vocabulary for the simulated plant. The simulator
already follows ISA-95, so the model also records how each concept corresponds to ISA-95.

It is a description, not a new system:

* **Not a second source of truth.** The simulator remains the source of truth; the context model adds
  no state and no behaviour.
* **Not a second hierarchy.** Every entity type selects entities or records that the simulator already
  has, under their own identifiers.
* **Not a redesign.** Where ISA-95 would model something differently, the simulator's model is kept
  and the mapping explains the difference.

```
 ISA-95 (reference semantics)          Enterprise simulator (implementation reality)
              \                                     /
               └──────── Canonical context model ──┘
                                 │
                       operational boundary
                                 │
                  future projections: UNS · Historian · KG → i3X → MCP → Agent
```

The evaluator never reads the context projections for ground truth. It uses `/api/benchmark/*`.

## 2. Relationship to the simulator and to ISA-95

| Question | Answer |
|---|---|
| Where do the entities come from? | the simulator: `configs/site.yaml` (hierarchy), `configs/utilities.yaml`, `configs/materials.yaml`, `configs/equipment.yaml`, `configs/production.yaml`, `configs/quality.yaml`, and the canonical-state collections created at run time |
| What does ISA-95 contribute? | reference semantics: what an Area, Work Unit, Material Lot or Maintenance Work Order *means* in manufacturing operations management (IEC 62264 Parts 1-4) |
| What if they disagree? | the simulator wins; the mapping is classified MODELING_CHOICE or PARTIAL and explained |
| What if ISA-95 has something the simulator lacks? | it is classified NOT_REPRESENTED. That is not a defect, and nothing is invented to fill it |

The simulator already uses ISA-95 level names: the hierarchy code (`simulator/isa95/__init__.py`)
validates containment against the IEC 62264-1 role-based equipment hierarchy, with ISA-88 equipment
and control modules below it. Most level mappings are therefore direct.

## 3. The existing hierarchy (reverse-engineered)

The simulator implements one equipment hierarchy of 87 elements (`configs/site.yaml`). Every element
is also an entity in the canonical state, with `kind = equipment` and `meta.level`.

```
ENT-ACME  Enterprise  ACME Manufacturing
└── SITE-TE  Site  Tennessee Eastman Manufacturing Site
    ├── AREA-FEED        Area  ── PU-FEED        ProductionUnit ── EM-FEED-A/D/E/AC ── CM-* valves + flow loops
    ├── AREA-REACTION    Area  ── PU-REACTOR     ProductionUnit ── EM-REACTOR, EM-RX-COOLING, EM-AGITATOR ── CM-*
    ├── AREA-SEPARATION  Area  ── PU-SEPARATION  ProductionUnit ── EM-CONDENSER, EM-SEPARATOR ── CM-*
    ├── AREA-RECYCLE     Area  ── PU-RECYCLE     ProductionUnit ── EM-COMPRESSOR, EM-PURGE ── CM-*
    ├── AREA-STRIPPING   Area  ── PU-STRIPPER    ProductionUnit ── EM-STRIPPER, EM-STRIPPER-STEAM ── CM-*
    ├── AREA-PRODUCT     Area  ── SZ-PRODUCT StorageZone ── SU-TK-501/502/503
    │                          └─ WC-QC WorkCenter ── WU-QC-LAB WorkUnit
    ├── AREA-RAW         Area  ── SZ-RAW StorageZone ── SU-SPH-103, SU-TK-101, SU-TK-102, SU-SPH-104
    │                          └─ SZ-WAREHOUSE StorageZone ── SU-WH-MRO, SU-WH-FG
    └── AREA-UTILITIES   Area  ── WC-CW WorkCenter ── WU-CT-101, WU-CWP-101A/B, WU-CWP-201A/B
                               ├─ WC-STEAM WorkCenter ── WU-BLR-301
                               └─ WC-POWER WorkCenter ── WU-TX-401, WU-MCC-401
```

| Level | Count | Examples | Origin |
|---|---|---|---|
| Enterprise | 1 | ENT-ACME | enterprise layer |
| Site | 1 | SITE-TE | enterprise layer |
| Area | 8 | 5 TEP process sections (feed, reaction, separation, recycle, stripping) + product, raw material, utilities | TEP / enterprise layer |
| ProductionUnit | 5 | one per TEP process area | TEP |
| WorkCenter | 4 | WC-CW, WC-STEAM, WC-POWER, WC-QC | enterprise layer |
| WorkUnit | 9 | 4 CW pumps, cooling tower, boiler, transformer, MCC, QC bench | enterprise layer |
| StorageZone | 3 | SZ-PRODUCT, SZ-RAW, SZ-WAREHOUSE | enterprise layer |
| StorageUnit | 9 | 3 product tanks, 4 raw-material storages, 2 warehouse bays | enterprise layer |
| EquipmentModule | 13 | reactor, cooling bundle, agitator, condenser, separator, compressor, purge, stripper, reboiler, 4 feed lines | TEP |
| ControlModule | 34 | 19 PID loops, 11 control valves, 1 speed drive, 3 online analyzers | TEP |

**Three hierarchies, one tree.** They are easy to conflate, so they are separated here:

* **Organisational hierarchy:** Enterprise → Site → Area. There is no department, crew or organisation
  chart; technicians are not placed in the tree.
* **Physical equipment hierarchy:** the whole tree, down to control modules.
* **Production hierarchy:** the five TEP Areas, each with one ProductionUnit, follow the process flow
  (feed → reaction → separation ↔ recycle → stripping). The product is metered at `PU-STRIPPER`
  (`configs/production.yaml`, `line.id`). The enterprise Areas (product, raw material, utilities)
  support production but contain no production unit.

**Outside the tree:**

* utility services (`UT-*`), materials (`MAT-*`) and the product definition (`PROD-GH-M1`);
* all records: lots, orders, work orders, samples, alarms, technicians, spare parts, purchase orders,
  shipments, faults;
* TEP variables (XMEAS, XMV, IDV), which are *bound* to equipment properties
  (`configs/tep_mapping.yaml`) but are not equipment.

## 4. Entity model and identity

**Canonical identity** is `<entity_type>:<native_id>`, where the native id is the simulator's own,
unchanged. The rule is **frozen** (decision U-05).

* **Type qualifier:** it is required, because production orders (`PO-2026-0201`) and purchase orders
  (`PO-00001`) share a prefix.
* **Unambiguous split:** native ids never contain `:`, so the first `:` splits a canonical id.
* **References:** every relationship reference uses canonical ids.
* **Uniqueness scope:**
  * *static* identities (hierarchy elements, utilities, materials, products, process variables, loops,
    quality tests) are stable across runs;
  * *run-scoped* identities (records and events) are unique within one run, because the simulator
    restarts their numbering on create and reset (see R-01 in the
    [decisions](CONTEXT_MODEL_DECISIONS.md)).
* **Projections:** UNS topics, historian tags and KG IRIs carry the canonical id verbatim and must be
  reversible to it.

Aliases resolve to one canonical id:

* a control loop's number (18) and tag (TC-RX) → `control_module:CM-TIC-RX`;
* an instrument tag (TI-09) → `measurement:XMEAS(9)`;
* a bound property (`EM-REACTOR.temperature`) → `measurement:XMEAS(9)`.

| Entity type | Selects (source) | Owner | Lifecycle | Observability | ISA-95 |
|---|---|---|---|---|---|
| enterprise … control_module (10 types) | hierarchy elements by level | `Hierarchy` (configs/site.yaml) | static | operational | see §3 and the mapping |
| utility | entities of kind `utility` (4) | `UtilitiesModule` | static | operational (some properties evaluator-only) | modeling choice |
| material | entities of kind `material` (5) | `MaterialsModule` | static | operational | Material Definition |
| product | `production.products` (PROD-GH-M1) | `ProductionModule` | static | operational | Product Definition (partial) |
| material_lot | `material_lots` | `InventoryModule` | received → consumed | operational | Material Lot |
| production_lot | `production_lots` | `ProductionModule` | IN_PROCESS → AWAITING_QC → disposition | operational | Material Lot of the product |
| production_order | `production_orders` | `SchedulingModule` | PLANNED → … → COMPLETED/CANCELLED | operational | Production Request / Job Order |
| purchase_order, shipment | `purchase_orders`, `shipments` | Inventory / Warehouse | OPEN → RECEIVED; SCHEDULED → SHIPPED | operational | Level 4 / inventory operations |
| work_order | `work_orders` | `MaintenanceModule` | REQUESTED → … → COMPLETED | operational | Maintenance Work Order |
| technician | `technicians` | `MaintenanceModule` | AVAILABLE / BUSY / OFF_SHIFT | operational | Person (partial) |
| spare_part | `spare_parts` | `InventoryModule` | stock | operational | MRO material + inventory |
| quality_sample, quality_test | `quality_samples`; `quality.product_specifications` | `QualityModule` | created with result; static | operational | QA test response; test specification |
| alarm | `alarms` | `AlarmModule` | ISA-18.2 states | operational | not ISA-95 |
| measurement, manipulated_variable, disturbance | TEP catalog (XMEAS 41, XMV 12, IDV 20) | TEP | static identity | operational (live IDV state evaluator-only) | Level 1-2 data |
| control_loop | 19 native loops → pid_loop control modules | TEP native control | static | operational | Level 2 control |
| maintainable_asset | a role on 10 hierarchy elements | `EquipmentModule` | static role | operational | Physical Asset (partial) |
| event | event log | `EventBus` | append-only | derived_operational (OE ids) | not an ISA-95 object |
| operator_action | `operator_log` | `OperatorModule` | append-only | internal (no route) | — |
| fault | `faults` | `FaultEngine` | benchmark lifecycle | **evaluator_only** | not ISA-95 |

`tests/test_context_model.py` checks the identity rules against a full demo run: every simulator
entity and record has exactly one canonical identity, and no identity is duplicated.

## 5. Observations

An observation is a value about an entity at a time. Every observation type has a subject, a unit
source, timestamp semantics and an observability class (`contract/context_model.yaml`,
`observations`).

| Observation | Subject | Examples | Observability |
|---|---|---|---|
| process_measurement | measurement | transmitted XMEAS + quality | operational |
| true_process_measurement | measurement | `xmeas_true` | evaluator_only |
| manipulated_variable_value, setpoint, controller_output, loop_mode, controller_saturation | control loop / XMV | XMV(10) = 41 %, TC-RX SP 120.4 °C | operational |
| process_shutdown | site | shutdown flag and reason | operational |
| equipment_run_state, condition_indicator, run_hours | maintainable asset | `is_running`, `vibration`, `run_hours` | operational |
| equipment_status | maintainable asset | `status` (DEGRADED reported as RUNNING) | derived_operational |
| equipment_condition | maintainable asset | `health`, `efficiency`, `available_flow`, … | evaluator_only |
| utility_meter, utility_utilization | utility | flow, pressure, temperature, voltage; utilization (not steam) | operational |
| utility_state | utility | status, availability, capacity fraction, health | evaluator_only (U-02: a future *derived* operational status may use only observable inputs) |
| inventory_quantity | storage unit | quantity, level, supply availability, status | operational |
| material_composition | storage unit | feed impurity and deviations | evaluator_only |
| quality_result, lot_status | sample, lot | QS-00009 FAIL on G_MASS_PCT; PL-0003 QUARANTINE | operational |
| production_state, order_status | production unit, order | line RUNNING, rate, OEE; order progress | operational |
| maintenance_state, alarm_state, procurement_state | records | WO status, alarm state, PO status | operational |
| process_internal_state, fault_truth | site, fault | TEP states, IDV flags, boundary, causes | evaluator_only |

**Measurement, setpoint, controller output and manipulated variable are different observations**
([canonical contract §6](CANONICAL_SIMULATOR_CONTRACT.md#6-state-model-the-semantic-classes)). The
context model never merges them.

## 6. State

State is owned by exactly one domain. The context model adds no state.

| Domain | Owner | Holds | Observability |
|---|---|---|---|
| process_physical | TEP (TEFUNC) | 50 states, true XMEAS | evaluator_only; observed through transmitted measurements |
| process_control | TEP native control + operator | XMV, SETPT, loop modes | operational |
| equipment_operational | EquipmentModule | run state, status, vibration, run hours | derived_operational |
| equipment_condition | EquipmentModule + CouplingEngine | health, capability | evaluator_only |
| utility_supply | CouplingEngine + UtilitiesModule | meters; capacity, availability, status | meters operational, the rest evaluator_only |
| material_inventory | InventoryModule + WarehouseModule | stock, lots, locations, parts, purchase orders, shipments | operational |
| production | ProductionModule + SchedulingModule | line state, rate, lots, orders | operational |
| quality | QualityModule | samples, results, lot disposition | operational |
| maintenance | MaintenanceModule | work orders, technicians | operational |
| benchmark | FaultEngine + causal registry | faults, causes, correlation | evaluator_only |

## 7. Events

The event types are the 46 of the canonical contract. The context model does not redefine them.

| Aspect | Operational | Evaluator |
|---|---|---|
| identity | `OE-nnnnnnn` (sequential over the operational stream), `LC-` for lifecycle | `EV-nnnnnnn`, `LC-`; `operational_id` maps to the OE id |
| types | the 46 types minus the withheld ones (FAULT_*, UTILITY_STATE_CHANGED, EQUIPMENT_DEGRADED, RUNNING↔DEGRADED transitions) | all 46 |
| entity | `target` (an entity or record id) and `source` (the publishing module) | same |
| payload | redacted (no health, fault ids, failure origin, scenario identity) | full |
| relationships | `causation_id`/`correlation_id` rebuilt from operational causation | true correlation to faults |
| timestamp | `simulation_time` (s) and ISO `timestamp` = `simulation_start` + time; pre-step events carry t, post-step events t+1 | same |
| state transition | per type (`records_change_of` in the contract) | same |

Operational event ids are safe to expose: they carry no information about withheld events.

**Lifecycle events** (decision U-07) are temporal operational events. They all target
`site:SITE-TE`; the simulator has no run entity.

| Event | Id | Transition | Operational payload |
|---|---|---|---|
| SIMULATION_STARTED | `LC-` | READY → RUNNING | `duration_seconds` (run id, scenario id and seed removed) |
| SIMULATION_PAUSED | `LC-` | RUNNING → PAUSED | `time_s` |
| SIMULATION_RESUMED | `LC-` | PAUSED → RUNNING | `time_s` |
| SIMULATION_RESET | `LC-` | any → READY; a new run begins and run-scoped ids restart | none (scenario id removed) |
| SIMULATION_COMPLETED | `OE-` | RUNNING → COMPLETED | `simulated_seconds` (run id removed) |

## 8. Relationships

Relationships are taken from where the simulator defines them. Each one has a source and an
observability class (`contract/context_model.yaml`, `relationships`). They are not all causal.

| Group | Relationships | Source | Observability |
|---|---|---|---|
| hierarchy | contains | site.yaml | operational |
| process variables | measures, actuates, manipulates, controls, uses_measurement, disturbance_acts_on | tep_mapping.yaml, control_scheme.py | operational (the live disturbance state is evaluator-only) |
| utilities | supplied_by, serves, supports_utility | utilities.yaml, coupling.yaml structure | operational |
| material | feeds, stores, located_in, lot_of, bill_of_materials, purchase_for | site.yaml, materials.yaml, records | operational |
| product ↔ material | yields_material (product → material, 1 → 1; a material is yielded by 0..n products); lot_material (derived) | production.yaml `products.<id>.material` | operational master data / derived_operational (U-03) |
| production | produced_for, orders_product, produced_on | records, production.yaml | operational |
| quality | sampled_from, sample_of, tested_against | sample records | operational |
| maintenance | work_order_for, assigned_to, requires_part, asset_role_of, redundant_with | records, equipment.yaml | operational |
| alarms and events | alarm_on, event_about, operationally_caused_by | alarm definitions, operational stream | operational / derived_operational |
| **causal truth** | fault_targets, correlated_with_fault, coupling_relation (equations and values), process_influence | fault engine, causal registry, coupling.yaml | **evaluator_only** |

**Observable relationship vs causal truth.**

* **Asset/service topology is operational** (decision U-04). "UT-CW-REACTOR is supplied by WC-CW,
  whose pumps P-101A/B provide it (`supports_utility`)" is plant design knowledge, the kind of fact
  found on a P&ID. The topology relationship carries no health, status or fault attribute.
* **Mechanism and cause are evaluator-only.** "The reactor CW capacity equals the sum of the pumps'
  available flow" (a coupling equation with hidden values), and "this alarm was caused by fault
  F-COOL-001", are evaluator-only.
* **The rule:** the context model never turns a causal label into a relationship.

## 9. Timestamps and units

| Timestamp semantics | Meaning |
|---|---|
| step_state | the value in the canonical state after the step to `clock.time_s`. The canonical state keeps **no per-value change time**, so a consumer samples at step boundaries. |
| analyzer_sample | XMEAS 23-41 change only at analyzer sample times and represent the process one dead time earlier (0.1 h or 0.25 h) |
| record_fields | records carry their own times in simulation seconds (`requested_s`, `taken_s`, `represents_s`, `start_s`, `qc_due_s`, …) |
| event_time | event `simulation_time` and ISO `timestamp` |
| static | configuration-defined identities |

All times are simulation seconds from `simulation_start` (UTC). Wall-clock time appears in no
simulation data.

**Units** come from the TEP catalog (XMEAS, XMV, states), from entity `units`
(`EntityRecord.units`), from configuration (`attribute_units`, quality test units), or are stated
literally (`%`, `kg`, `kg/h`, `enumeration`, `boolean`, `fraction`). Every observation type states
which.

## 10. Observability: what exists vs what is observable

Observability is a separate dimension from existence. It follows the operational boundary
(`api/operational.py`), which is authoritative. It is never inferred from a name: properties are hidden
because their owning module tags them (`meta.model_internal`, `meta.unobservable`).

| Class | Meaning | Examples |
|---|---|---|
| operational | served by operational routes as-is | transmitted XMEAS, vibration, work orders |
| derived_operational | served in a derived form | asset status without DEGRADED; OE event stream |
| evaluator_only | simulator truth, `/api/benchmark` only | health, capability, utility status, true XMEAS, boundary values, faults |
| derived_evaluator_only | derived from evaluator-only information | canonical asset status DEGRADED; canonical correlation |
| internal | served by no route | operator log collection |

`test_observation_observability_agrees_with_the_operational_boundary` checks, for every entity in a
full demo run, that the context model's classification equals what the boundary actually does. **The
context model cannot reintroduce information the boundary removed.**

## 11. Observability conditions (future benchmark dimension; not implemented)

Future benchmark conditions (noisy, delayed, missing, stale or conflicting observations) are a
transformation *after* the operational observation, never a change to simulator truth:

```
Physical truth (TEP, enterprise state)          ← the evaluator reads this (/api/benchmark)
    ↓
Simulator
    ↓
Operational observation (operational boundary)
    ↓
Observability transformation (future: noise, delay, dropout, staleness, conflicts)
    ↓
Agent-visible context
```

The context model identifies the attachment point for such conditions: observation type, subject and
timestamp semantics. It defines none of them.

## 12. Evaluator-only information

The evaluator reads `/api/benchmark/*`, not the context projections:

* fault records and causes;
* the causal registry and true correlation;
* true XMEAS, TEP states, IDV flags and boundary values;
* equipment condition and capability;
* utility status and capacity;
* lot composition;
* the canonical event ids and the mapping to operational ids.

## 13. Future projections

The UNS, historian and knowledge graph will be *projections* of this model:

* **UNS:** current operational state;
* **historian:** operational observations over time;
* **KG:** identities and relationships.

All three use the same canonical identities and never redefine semantics. The rules are in
[CONTEXT_PROJECTION_PRINCIPLES.md](CONTEXT_PROJECTION_PRINCIPLES.md). None of them is implemented.

## 14. Example: reactor cooling in the demo

| Canonical id | Type | Selected fact | Observability |
|---|---|---|---|
| `work_unit:WU-CWP-101A` | work unit (also a maintainable asset) | contained in `work_center:WC-CW`; redundant with `WU-CWP-101B` | operational |
| `utility:UT-CW-REACTOR` | utility | supplied_by `WC-CW`; serves `EM-RX-COOLING`; pressure falls as the pump degrades | operational (meter) |
| same | | status DEGRADED at 01:02:57; capacity fraction 0.382 | evaluator_only |
| `measurement:XMEAS(21)` | measurement | measures `EM-RX-COOLING.cw_outlet_temperature` (TI-21); PV of TC-RCW | operational |
| `control_module:CM-TIC-RCW` | control loop 10 | controls `manipulated_variable:XMV(10)`, which `CM-TV-10` actuates and which manipulates `EM-RX-COOLING` | operational |
| `alarm:VAH-CWP101A` | alarm | alarm_on `WU-CWP-101A`; activated 01:20:33 as `OE-…` | operational |
| `work_order:WO-00001` | work order | work_order_for `WU-CWP-101A`; assigned_to `technician:TECH-01`; requires_part `SP-IMP-101`; caused operationally by the alarm | operational |
| `production_lot:PL-0003` | production lot | produced_for `production_order:PO-2026-0202`; lot_of `product:PROD-GH-M1`; QUARANTINE | operational |
| `fault:F-COOL-001` | fault | fault_targets `WU-CWP-101A`; correlated_with the alarm and the lot decision | **evaluator_only** |

Source:
- `contract/context_model.yaml`
- `tests/test_context_model.py`
- `configs/site.yaml`
- `simulator/isa95/__init__.py` — `EquipmentLevel`, `ALLOWED_CHILDREN`
- `api/operational.py` — `hidden_properties`, `OperationalEventView`
