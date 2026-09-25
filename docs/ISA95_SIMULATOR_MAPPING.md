# ISA-95 ↔ simulator mapping

This page maps the simulator's **existing** concepts to ISA-95 (IEC 62264) reference concepts. It does
not create a hierarchy: the simulator's model is authoritative, and ISA-95 supplies meaning. The
machine-readable form is `isa95_mapping` in `contract/context_model.yaml`, with the same ids.

**ISA-95 parts referred to:**

* IEC 62264-1: models and terminology, including the equipment hierarchy and the Level 0-4
  functional hierarchy;
* IEC 62264-2: object model attributes (equipment, physical asset, material, personnel, product
  definition, operations request/response);
* IEC 62264-3: activity models for production, maintenance, quality and inventory operations;
* IEC 62264-4: MOM integration objects (work master, job order).

**Mapping classifications:**

| Classification | Meaning |
|---|---|
| DIRECT | the simulator concept naturally corresponds to the ISA-95 concept |
| COMPOSITE | several simulator concepts together represent it |
| DERIVED | it can be derived from simulator state but is not a first-class object |
| MODELING_CHOICE | more than one mapping is reasonable; the selected one is documented |
| PARTIAL | the simulator represents only part of it |
| NOT_REPRESENTED | ISA-95 defines it, the simulator does not. **Not a defect**, and nothing is added to fill it |

## Functional hierarchy (IEC 62264-1 Levels 0-4)

| Level | ISA-95 meaning | In the simulator |
|---|---|---|
| 0 | the physical process | TEP physics (`teprob.f`, TEFUNC) |
| 1 | sensing and manipulating | XMEAS (41 measurements, through the instrumentation layer), XMV (12 valve and drive commands) |
| 2 | monitoring, supervisory and automated control | 19 native loops (`temain_mod.f`), alarms, interlock trip |
| 3 | manufacturing operations management | maintenance, quality, inventory, warehouse, production metering and lots, scheduling |
| 4 | business planning and logistics | production orders with customers, purchase orders to suppliers, shipments. They are simple, and modelled in the same process as Level 3. |

## A. Equipment and organisational hierarchy

The simulator's level names come from the ISA-95 role-based equipment hierarchy.
`EquipmentLevel` in `simulator/isa95/__init__.py` holds the same values as the B2MML level
enumeration, and containment is validated against it.

| Simulator concept | Simulator location | ISA-95 concept | Mapping | Rationale |
|---|---|---|---|---|
| ENT-ACME | `configs/site.yaml`, level Enterprise | Enterprise | DIRECT | root of the hierarchy |
| SITE-TE | level Site | Site | DIRECT | one site, one continuous process |
| AREA-* (8) | level Area | Area | DIRECT | five follow TEP process sections, three are enterprise areas (product and QC, raw material, utilities) |
| PU-* (5) | level ProductionUnit | Production Unit | DIRECT | the ISA-95 work-center type for continuous production. There is one per process area. |
| WC-CW, WC-STEAM, WC-POWER, WC-QC | level WorkCenter | Work Center | MODELING_CHOICE | In IEC 62264-1, "work center" is also the generic term for process cells, production units, production lines and storage zones. The simulator uses it as a concrete level (as the B2MML enumeration allows) for utility systems and the laboratory. |
| WU-* (9) | level WorkUnit | Work Unit | DIRECT | pumps, cooling tower, boiler, transformer, MCC and QC bench, as work units of those work centers |
| SZ-* (3), SU-* (9) | levels StorageZone, StorageUnit | Storage Zone, Storage Unit | DIRECT | product tank farm, raw-material zone, warehouse |
| EM-* (13) | level EquipmentModule, directly under a ProductionUnit | Equipment Module (lower-level equipment, ISA-88) | MODELING_CHOICE | Reactor, separator, stripper, compressor and so on could be read as Units. The simulator models them as equipment modules of their production unit (decision CM-02). |
| CM-* (34) | level ControlModule | Control Module (ISA-88) | DIRECT | loops, valves, drive and analyzers |
| (none) | `EquipmentLevel.UNIT` exists in code, unused | Unit | NOT_REPRESENTED | no element uses it |
| (none) | enumerated, unused | Process Cell, Production Line, Work Cell | NOT_REPRESENTED | batch and discrete work-center types do not apply to TEP |
| element `class` (reactor_vessel, centrifugal_pump, …) | `configs/site.yaml` | Equipment Class | PARTIAL | a class name per element; no class hierarchy or class properties |
| maintainable assets (10) | `configs/equipment.yaml` | Physical Asset | PARTIAL | the asset role sits on the same identity as the equipment role. There is no separate physical-asset object and no asset-to-equipment assignment history (decision CM-06). |
| efficiency, available_*, capability; utility capacity | coupling relations | Equipment Capability | PARTIAL | simulated and **evaluator-only** here, although a real MES would treat capability as Level 3 information (decision CM-15) |
| organisation (departments, crews) | — | organisational units | NOT_REPRESENTED | there is no organisation chart; the hierarchy is equipment-based |

**Organisational vs physical vs production hierarchy.** One tree serves all three
([context model §3](CONTEXT_MODEL.md#3-the-existing-hierarchy-reverse-engineered)):

* **organisational:** Enterprise, Site and Area only;
* **physical:** everything down to control modules;
* **production:** the five TEP areas and their production units, with output metered at `PU-STRIPPER`.

### TEP process equipment, one by one

TEP is a continuous chemical process, and not every item maps cleanly to ISA-95. For each item, the
table gives the plausible ISA-95 readings, the one selected, and why.

| Existing simulator element | Possible ISA-95 interpretations | Selected | Reason (simulator semantics) |
|---|---|---|---|
| EM-REACTOR (Reactor R-101) | Unit; Equipment Module; production resource | Equipment Module of PU-REACTOR | modelled as `EquipmentModule` with its own control modules; PU-REACTOR is the production unit |
| EM-RX-COOLING (cooling bundle) | Equipment Module; part of the reactor | Equipment Module (sibling of the reactor) | it has its own valve and loop and is served by UT-CW-REACTOR |
| EM-AGITATOR | Equipment Module; asset | Equipment Module + maintainable asset | has a condition model; its condition does not reach TEP |
| EM-CONDENSER, EM-SEPARATOR | Units of a separation cell; Equipment Modules | Equipment Modules of PU-SEPARATION | as modelled |
| EM-COMPRESSOR | Unit; Equipment Module; asset | Equipment Module + maintainable asset | capability reaches TEP through `CPFLMX` |
| EM-PURGE | Equipment Module; stream | Equipment Module | has a valve, loop and analyzer |
| EM-STRIPPER, EM-STRIPPER-STEAM | Unit; Equipment Modules | Equipment Modules of PU-STRIPPER | PU-STRIPPER is also the production line (`configs/production.yaml`) |
| EM-FEED-A/D/E/AC | streams; Equipment Modules | Equipment Modules of PU-FEED | fed by storage units (`attributes.feeds`) |
| CM-AT-* (analyzers) | Control Modules; quality resources | Control Modules | CM-AT-PRODUCT is also the sample source for quality |
| TEP streams (1-11) | Material flows; segments | not entities | only as attributes (feed line `stream`) and XMEAS bindings |

## B. Production

| Simulator concept | Simulator location | ISA-95 concept | Mapping | Rationale |
|---|---|---|---|---|
| production orders (PO-2026-*) | `production_orders` collection; scenario `production_orders` | Production (Operations) Request; Job Order (Part 4) | MODELING_CHOICE | quantity, product, planned window, priority and customer, with a state machine. They carry no segment requirements (decision CM-09). |
| scheduling (release, block, single-line sequence) | `SchedulingModule` | Production (Operations) Schedule | PARTIAL | planned windows and release rules; no schedule object |
| produced / accepted / rejected kg, lots, status history | order record, `state.production` | Production (Operations) Response / Performance | DERIVED | derived from metering and QC |
| PROD-GH-M1 | `configs/production.yaml` products | Product Definition | PARTIAL | bill of materials (kg/kg) and quality specification; yields exactly one Material Definition, MAT-GH (`yields_material`, decision U-03); no product segments or parameters |
| process segments, operations definitions, work masters | — | Process Segment / Operations Definition / Work Master | NOT_REPRESENTED | the process is one continuous TEP operation |
| production line state and rate | `state.production`, line `PU-STRIPPER` | Production performance (actual) | DERIVED | from the transmitted XMEAS(17) |
| nominal rate; hidden capability | `configs/production.yaml`, coupling | Production Capability | PARTIAL | nominal rate only; the real capability is evaluator-only |
| OEE | `state.production.oee` | KPI (IEC 62264-3 performance analysis; ISO 22400) | DERIVED | availability × performance × quality |

## C. Material

| Simulator concept | Simulator location | ISA-95 concept | Mapping | Rationale |
|---|---|---|---|---|
| MAT-A, MAT-D, MAT-E, MAT-AC, MAT-GH | entities of kind `material` | Material Definition | DIRECT | name, unit, attributes, specification |
| material `category` (raw_material, finished_good) | materials.yaml | Material Class | PARTIAL | a category string; no class objects |
| material lots (LOT-*) | `material_lots` | Material Lot | DIRECT | quantity, location, supplier, certificate attributes, quality status |
| production lots (PL-*) | `production_lots` | Material Lot (of the product) | MODELING_CHOICE | a separate record type carrying the product id `PROD-GH-M1`; its material is `MAT-GH` through `yields_material` (decisions CM-10 and U-03) |
| sublots | — | Material Sublot | NOT_REPRESENTED | |
| storage and inventory quantities | storage units, `InventoryModule` | inventory (Part 3 inventory operations); lot location | COMPOSITE | stock is the sum of lots at a location |
| MATERIAL_CONSUMED, MATERIAL_RECEIVED, INVENTORY_MOVED; shipments | events, `shipments` | material consumed/produced actuals; material transfers | COMPOSITE | consumption posted every 900 s; FIFO lots |
| spare parts (SP-*) | `spare_parts` | Material Definition (MRO) + inventory | COMPOSITE | definition and stock in one record |
| purchase orders (PO-*) | `purchase_orders` | Level 4 procurement | PARTIAL | outside the Level 3 object models |

## D. Equipment, measurements and control

| Simulator concept | Simulator location | ISA-95 concept | Mapping | Rationale |
|---|---|---|---|---|
| equipment `status`, run state | `EquipmentModule` | equipment operational state | DIRECT | DEGRADED is operationally reported as RUNNING (operational boundary) |
| `health` | `EquipmentModule` | equipment condition (asset health) | PARTIAL | one 0..1 value, **evaluator-only** |
| `vibration` | `EquipmentModule` | condition-monitoring measurement | DIRECT | the observable symptom |
| XMEAS / XMV bound to equipment properties | `configs/tep_mapping.yaml` | equipment property values (Level 1-2 data) | DERIVED | TEP variables are properties of equipment, not equipment |
| 19 native control loops | `control_scheme.py`, pid_loop control modules | Level 2 control | PARTIAL | the Level 3 object models do not model control loops |
| IDV 1-20 | TEP catalog | — | NOT_REPRESENTED | process-model disturbances |
| alarms | `AlarmModule` | — (ISA-18.2) | NOT_REPRESENTED | alarm management is outside ISA-95 |
| events | event log | exchanged transactions and messages (no generic event object) | PARTIAL | the canonical event model is simulator-defined |

## E. Maintenance

| Simulator concept | Simulator location | ISA-95 concept | Mapping | Rationale |
|---|---|---|---|---|
| work orders (WO-*) with MAINTENANCE_REQUESTED / COMPLETED | `work_orders`, events | Maintenance Request, Maintenance Work Order, Maintenance Response (Part 3) | COMPOSITE | one record plus its request and completion events |
| work-order kinds corrective / inspection / planned | work order `kind` | maintenance work types | DIRECT | |
| technicians (TECH-*) with skills and shifts | `technicians` | Person with qualifications | PARTIAL | skills are strings; no personnel class; shift times only |
| personnel classes | — | Personnel Class | NOT_REPRESENTED | |
| the operator | actor string on operator actions | Person | NOT_REPRESENTED | operators are not entities (decision CM-12) |
| maintenance resources: spare parts, response times | inventory, `configs/maintenance.yaml` | maintenance resource management | PARTIAL | |
| repair / changeover | `EquipmentModule` handlers | maintenance execution | DIRECT | |

## F. Quality

| Simulator concept | Simulator location | ISA-95 concept | Mapping | Rationale |
|---|---|---|---|---|
| product tests (G_MASS_PCT, GH_PURITY, E_IMPURITY, F_BYPRODUCT); material specifications | `configs/quality.yaml`, materials.yaml | QA Test Specification | DIRECT | expression, limits, target |
| quality samples (QS-*) with results | `quality_samples` | QA test request and response (test result) | COMPOSITE | one record holds the sample, the results and the overall verdict |
| lot disposition RELEASED / QUARANTINE / REJECTED | production lots, material lots | Material Lot status | DIRECT | |
| laboratory WU-QC-LAB, analyzer CM-AT-PRODUCT | hierarchy | quality test resources | DIRECT | |
| off-spec alarms | alarms | — | NOT_REPRESENTED | ISA-18.2 |

## G. Utilities

ISA-95 has no dedicated utility object. The simulator represents each utility service (`UT-*`) as an
entity *outside* the equipment hierarchy:

* it is **supplied by** a work center (`WC-CW`, `WC-STEAM`, `WC-POWER`);
* it **serves** equipment modules or work centers (`configs/utilities.yaml`);
* it carries meter readings (operational) and capacity, availability and status (evaluator-only).

| Simulator concept | ISA-95 reading | Mapping | Rationale |
|---|---|---|---|
| UT-CW-REACTOR, UT-CW-CONDENSER, UT-STEAM, UT-POWER | supply capability of the supplying Work Center, and a consumed utility material | MODELING_CHOICE | a service entity is kept (decision CM-05); consumption is not recorded as material consumption |
| utility systems WC-CW, WC-STEAM, WC-POWER | Work Centers | MODELING_CHOICE | see A |
| pumps, cooling tower, boiler, transformer, MCC | Work Units + physical assets | DIRECT / PARTIAL | |
| utility meter readings | equipment property values | DERIVED | algebraic approximations (decision D1 in the contract audit) |

Source:
- `contract/context_model.yaml`
- `configs/site.yaml`
- `simulator/isa95/__init__.py` — `EquipmentLevel`, `ALLOWED_CHILDREN`
- `tests/test_context_model.py`
