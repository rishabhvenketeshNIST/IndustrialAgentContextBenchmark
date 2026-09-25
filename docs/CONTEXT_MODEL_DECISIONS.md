# Context model decisions

Decisions taken in building the [canonical context model](CONTEXT_MODEL.md) (CM-nn), inconsistencies
found while reverse-engineering the simulator (I-nn), the resolution of the questions that were open
in 0.1.0 (U-nn), and what remains ambiguous (R-nn).
Where a genuine modeling ambiguity exists, it is recorded here rather than resolved silently.

## Decisions

| # | Decision | Reason | Alternatives not taken |
|---|---|---|---|
| CM-01 | The simulator's hierarchy (`configs/site.yaml`, 87 elements) is the only hierarchy. Level names and ids are used verbatim; no ISA-95 entities are added. | the simulator is authoritative; it already uses ISA-95 level names | a separate ISA-95 tree; renaming elements |
| CM-02 | TEP process equipment (reactor, separator, stripper, compressor, …) stays **EquipmentModule** under its ProductionUnit. The ISA-95 Unit level is NOT_REPRESENTED. | that is how the simulator models them: each has control modules, and the production unit is the operating context | reading them as Units (the common ISA-88 view for a reactor) |
| CM-03 | Production hierarchy = the five TEP Areas, each with one ProductionUnit; product output is attributed to `PU-STRIPPER`. | `configs/production.yaml` `line.id`; the production module meters XMEAS(17) there | a site-level production line entity |
| CM-04 | WorkCenter / WorkUnit are concrete levels for the utility systems and the QC laboratory. | the simulator uses them so (the B2MML enumeration allows it) | treating them only as generic ISA-95 terms |
| CM-05 | Utility services (`UT-*`) are entities outside the hierarchy, related by `supplied_by` (a work center) and `serves` (equipment). | `configs/utilities.yaml`; ISA-95 has no utility object | modelling utilities as materials, or as capabilities of the work center only |
| CM-06 | "Maintainable asset" is a **role** on existing hierarchy elements, not a separate identity. | `configs/equipment.yaml` keys assets by element id | separate ISA-95 physical-asset objects |
| CM-07 | Canonical identity is `<entity_type>:<native_id>`; aliases (loop number and tag, instrument tag, bound property) resolve to one canonical id. | native ids are unique only per type (`PO-2026-*` production orders vs `PO-*` purchase orders) | bare native ids |
| CM-08 | A control loop's canonical identity is its `pid_loop` control module (e.g. loop 18 / TC-RX → `control_module:CM-TIC-RX`). | `configs/tep_mapping.yaml` maps each loop to exactly one control module | a separate loop identity |
| CM-09 | A production order maps to an ISA-95 Production (Operations) Request / Job Order: MODELING_CHOICE. | quantity, product, window, priority, customer, state machine; no segment requirements | Work Order; Production Schedule entry |
| CM-10 | A production lot is a record type of its own, a lot of **product** `PROD-GH-M1`. | the lot record stores `product_id`; the material `MAT-GH` is linked only through `products.material`, which no logic reads (I-03); resolved by `yields_material` (U-03) | normalising lots to material `MAT-GH` |
| CM-11 | A measurement's canonical identity is its TEP variable (`XMEAS(n)`); the bound equipment property and the instrument tag are views of it. | the same value appears in the process image and as a property (`ProcessInterface.sync`) | the property as primary identity |
| CM-12 | Operators are not entities; the `actor` string of an operator action is kept as-is. | the simulator has no operator records | inventing a Person for the operator |
| CM-13 | Alarms and events are canonical entity types although ISA-95 does not model them. | they are first-class in the simulator and essential context | leaving them out |
| CM-14 | Observability is taken from the operational boundary: the owning module's tags plus the boundary rules. Equipment status is `derived_operational`. | the boundary is authoritative; `test_observation_observability_agrees_with_the_operational_boundary` enforces it | classifying by property name |
| CM-15 | Equipment and utility **capability** is evaluator-only, although ISA-95 treats capability as Level 3 operational information. | in this benchmark, capability is derived from hidden health and fault causes (operational boundary G2 and G15) | exposing capability as in a real MES |
| CM-16 | *Structural* supply relationships (`supports_utility`: pumps → reactor CW service) are operational design knowledge. The coupling equations and their values are evaluator-only. | design structure is plant documentation; the values reveal hidden condition (confirmed by U-04) | hiding structure too; exposing equations |
| CM-17 | Where a TEP disturbance acts (`disturbance_acts_on`, from `configs/tep_mapping.yaml`) is operational documentation; whether it is active is evaluator-only. | the static TEP catalog is published process documentation; live IDV flags are ground truth | hiding the catalog |
| CM-18 | Timestamp semantics are `step_state`, `analyzer_sample`, `record_fields`, `event_time` or `static`. The canonical state stores no per-value change time. | this is how the simulator stores values | adding change timestamps to the simulator |
| CM-19 | The operator-action log collection is `internal`: no route serves it; the corresponding `OPERATOR_ACTION` events are operational. | current API | exposing the log |

## Inconsistencies found (documentation vs code, or within configuration)

None of these was changed; simulator behaviour and configuration are frozen.

| # | Finding | Evidence | Impact |
|---|---|---|---|
| I-01 | `simulator/isa95/__init__.py` describes "Work Center > Work Unit" as a sibling of Process Cell / Production Unit / Production Line / Storage Zone. In IEC 62264-1 these are also the generic terms for those levels. | module docstring | terminology only; the mapping records CM-04 |
| I-02 | `EM-COMPRESSOR`, `EM-RX-COOLING`, `EM-CONDENSER` and `EM-STRIPPER-STEAM` carry an `attributes.utility` in `configs/site.yaml`, but `EM-AGITATOR` does not, although `UT-POWER` serves it (`configs/utilities.yaml`). `UT-POWER` also serves `WC-CW` and `WC-STEAM`, which have no such attribute. | site.yaml vs utilities.yaml | the context model takes `serves` from utilities.yaml; the attribute is descriptive |
| I-03 | Production lots and orders reference `PROD-GH-M1`, while product tanks hold material `MAT-GH`. The only link, `products.*.material`, is read by no logic. | configs/production.yaml, warehouse module | resolved in the context model by `yields_material` (U-03); configuration unchanged |
| I-04 | `EquipmentLevel` defines Unit, ProcessCell, ProductionLine and WorkCell, but no element uses them. | `simulator/isa95/__init__.py`, site.yaml | none; mapped NOT_REPRESENTED |

## Resolved decisions (context model 0.2.0)

The seven questions left open in context model 0.1.0 were resolved as follows. None of the resolutions
changes the simulator, the API or the configuration.

### U-01: TEP equipment as Equipment Modules (kept)

TEP process equipment (reactor, cooling bundle, agitator, condenser, separator, compressor, purge,
stripper, reboiler, feed lines) keeps its ISA-95 / ISA-88 mapping as **Equipment Module** of its
Production Unit (CM-02). The simulator hierarchy is authoritative, and no Unit level is introduced to
make the hierarchy more ISA-95-complete. The Unit reading is recorded in the
[ISA-95 mapping](ISA95_SIMULATOR_MAPPING.md) only as an alternative interpretation that was not
selected.

### U-02: utility status (kept out of the operational context)

Utility `status`, `availability`, capacity fields and `health` originate from model-internal state
(equipment health, capability, fault causes). They stay `evaluator_only`.

**Future derived observation (not implemented):** an operational utility status may be added later
only as a derivation, in the context or projection layer, computed *solely* from operationally
observable information: utility meter readings (flow, pressure, temperature, voltage), cooling-water
utilization, alarms, and equipment run states. Such a derivation must:

* never read an `evaluator_only` value;
* be documented as a derived observation;
* be named so that it is not confused with the simulator's own utility `status`.

### U-03: product and material (resolved)

What the simulator actually does:

| Identifier | What it represents | Where it is used |
|---|---|---|
| `PROD-GH-M1` | a **Product Definition**: *what is made and how it is judged*. It is "G/H mixture, Mode 1, 50/50 by mass", with a bill of materials (kg of MAT-A/D/E/AC per kg) and a quality specification (four tests). | production orders and production lots (`product_id`); scheduling (material requirements from the BOM); quality (`product_specifications`, sample subject) |
| `MAT-GH` | a **Material Definition**: *the substance*, category `finished_good`, unit kg. It is a canonical-state entity. | product tanks SU-TK-501/502/503 (`material` property, set by the warehouse module); dispatch |

They are **distinct**: one is a recipe-and-specification, the other a substance. Several product
definitions could make the same material, for example to a different specification; nothing in the
simulator treats the two ids as interchangeable.

**Canonical relationship `yields_material`:**

| Aspect | Value |
|---|---|
| direction | `product` → `material` |
| cardinality | each product yields exactly 1 material; a material is yielded by 0..n products (today MAT-GH ← PROD-GH-M1) |
| source | configuration (`configs/production.yaml` `products.<id>.material`); static |
| operational observability | operational master data. No route serves it directly today, so a projection sources it from configuration. |
| derived relationship | `lot_material`: production lot → material, derived as `lot_of` (product) then `yields_material` (configuration + runtime); `derived_operational` |

This also resolves I-03. `tests/test_context_model.py::test_u03_product_yields_exactly_one_material_and_lots_are_stored_as_it`
checks it: every product yields exactly one finished-good material, and every production lot sits in
a tank that stores that material.

### U-04: utility topology (kept operational)

**Asset/service topology** is plant design knowledge and is operational:

* which equipment provides a utility (`supports_utility`: pumps P-101A/B → reactor cooling water);
* which work center supplies it (`supplied_by`);
* which equipment it serves (`serves`).

**Hidden fault causality** stays `evaluator_only`:

* health, capability, utility status and capacity;
* the coupling equations and their values;
* process influences;
* correlation to faults.

The topology relationship carries no health, status or fault attribute, so exposing it reveals only
the plant's structure.

### U-05: canonical identity (frozen)

| Aspect | Rule |
|---|---|
| native id | the simulator's own identifier, unchanged (e.g. `PO-2026-0201`, `XMEAS(9)`, `CM-TIC-RX`) |
| canonical id | `<entity_type>:<native_id>`, **frozen**. Type names match `^[a-z][a-z_]*$`. Native ids contain no `:` (tested), so the first `:` splits a canonical id unambiguously. |
| collisions | resolved by the type qualifier: `production_order:PO-2026-0201` ≠ `purchase_order:PO-00001` |
| stability and uniqueness scope | *static* identities (hierarchy elements, utilities, materials, products, measurements, manipulated variables, disturbances, control loops, quality tests) are unique and stable across runs of the same configuration. *Run-scoped* identities (records and events) are unique within one run; the simulator restarts their numbering when a simulation is created or reset. |
| aliases | loop number and tag, instrument tag, bound property → one canonical id; never identities themselves |
| relationship references | always canonical ids, at both ends |
| projections | UNS topics, historian tags and KG IRIs carry the canonical id verbatim (e.g. a `canonical_id` attribute). Any path or IRI form must be reversible to it; characters such as `(` `)` are escaped by the transport, never changed in the canonical id. |

`tests/test_context_model.py::test_u05_canonical_ids_are_type_qualified_unambiguous_and_frozen` and
`test_every_simulator_object_has_one_unique_canonical_identity` enforce this.

### U-06: customers and suppliers (out of scope)

No customer or supplier entities are introduced:

* customers remain a string attribute of production orders;
* suppliers remain a string attribute of lots and purchase orders, plus the descriptive `suppliers`
  catalog.

They become entity types only if a concrete benchmark scenario needs them.

### U-07: lifecycle events (included)

The lifecycle events the simulator produces are part of the canonical event model. They are temporal
operational events, not ground truth:

| Event | Operational id | Entity | State transition | Operational payload | Removed by the boundary |
|---|---|---|---|---|---|
| SIMULATION_STARTED | `LC-` | `site:SITE-TE` | READY → RUNNING | `duration_seconds` | `run_id`, `scenario_id`, `seed` |
| SIMULATION_PAUSED | `LC-` | `site:SITE-TE` | RUNNING → PAUSED | `time_s` | — |
| SIMULATION_RESUMED | `LC-` | `site:SITE-TE` | PAUSED → RUNNING | `time_s` | — |
| SIMULATION_RESET | `LC-` | `site:SITE-TE` | any → READY; a new run begins (record and event ids restart) | none | `scenario_id` |
| SIMULATION_COMPLETED | `OE-` | `site:SITE-TE` | RUNNING → COMPLETED | `simulated_seconds` | `run_id` |

* **Timestamp:** `simulation_time` and ISO `timestamp` (event time).
* **Ordering:** `LC-` events reflect when a user acted, so they have their own id sequence and never
  shift `OE-` ids.
* **Domain state-transition events** (equipment, production, orders, lots, work orders, alarms,
  material) are covered by the canonical contract (`records_change_of`). Utility and equipment
  health-band transitions stay withheld.
* **Test:** `tests/test_context_model.py::test_u07_lifecycle_events_are_operational_and_carry_no_ground_truth`.

## Remaining ambiguity

| # | Question | Why it is open | Current handling |
|---|---|---|---|
| R-01 | How does an operational consumer tell two runs apart? | Run-scoped ids (records, `OE-`/`LC-` events) restart after a reset or a new simulation. The run id identifies the scenario, so the operational boundary removes it, and no opaque operational run key exists. | Projections must scope run-scoped identities to one run (e.g. start a new namespace on `SIMULATION_RESET` / `SIMULATION_STARTED`). Defining an opaque run key would be an operational-boundary change and needs a decision. |

Source:
- `contract/context_model.yaml`
- `configs/site.yaml`
- `configs/utilities.yaml`
- `configs/production.yaml`
