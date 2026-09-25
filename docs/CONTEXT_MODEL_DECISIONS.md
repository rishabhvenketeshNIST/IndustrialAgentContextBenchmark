# Context model decisions

Decisions taken in building the [canonical context model](CONTEXT_MODEL.md) (CM-nn), inconsistencies
found while reverse-engineering the simulator (I-nn), and questions that need a human decision (U-nn).
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
| CM-10 | A production lot is a record type of its own, a lot of **product** `PROD-GH-M1`. | the lot record stores `product_id`; the material `MAT-GH` is linked only through `products.material`, which no logic reads (I-03) | normalising lots to material `MAT-GH` |
| CM-11 | A measurement's canonical identity is its TEP variable (`XMEAS(n)`); the bound equipment property and the instrument tag are views of it. | the same value appears in the process image and as a property (`ProcessInterface.sync`) | the property as primary identity |
| CM-12 | Operators are not entities; the `actor` string of an operator action is kept as-is. | the simulator has no operator records | inventing a Person for the operator |
| CM-13 | Alarms and events are canonical entity types although ISA-95 does not model them. | they are first-class in the simulator and essential context | leaving them out |
| CM-14 | Observability is taken from the operational boundary: the owning module's tags plus the boundary rules. Equipment status is `derived_operational`. | the boundary is authoritative; `test_observation_observability_agrees_with_the_operational_boundary` enforces it | classifying by property name |
| CM-15 | Equipment and utility **capability** is evaluator-only, although ISA-95 treats capability as Level 3 operational information. | in this benchmark, capability is derived from hidden health and fault causes (operational boundary G2 and G15) | exposing capability as in a real MES |
| CM-16 | *Structural* supply relationships (`supports_utility`: pumps → reactor CW service) are operational design knowledge. The coupling equations and their values are evaluator-only. | design structure is plant documentation; the values reveal hidden condition | hiding structure too; exposing equations |
| CM-17 | Where a TEP disturbance acts (`disturbance_acts_on`, from `configs/tep_mapping.yaml`) is operational documentation; whether it is active is evaluator-only. | the static TEP catalog is published process documentation; live IDV flags are ground truth | hiding the catalog |
| CM-18 | Timestamp semantics are `step_state`, `analyzer_sample`, `record_fields`, `event_time` or `static`. The canonical state stores no per-value change time. | this is how the simulator stores values | adding change timestamps to the simulator |
| CM-19 | The operator-action log collection is `internal`: no route serves it; the corresponding `OPERATOR_ACTION` events are operational. | current API | exposing the log |

## Inconsistencies found (documentation vs code, or within configuration)

None of these was changed; simulator behaviour and configuration are frozen.

| # | Finding | Evidence | Impact |
|---|---|---|---|
| I-01 | `simulator/isa95/__init__.py` describes "Work Center > Work Unit" as a sibling of Process Cell / Production Unit / Production Line / Storage Zone. In IEC 62264-1 these are also the generic terms for those levels. | module docstring | terminology only; the mapping records CM-04 |
| I-02 | `EM-COMPRESSOR`, `EM-RX-COOLING`, `EM-CONDENSER` and `EM-STRIPPER-STEAM` carry an `attributes.utility` in `configs/site.yaml`, but `EM-AGITATOR` does not, although `UT-POWER` serves it (`configs/utilities.yaml`). `UT-POWER` also serves `WC-CW` and `WC-STEAM`, which have no such attribute. | site.yaml vs utilities.yaml | the context model takes `serves` from utilities.yaml; the attribute is descriptive |
| I-03 | Production lots and orders reference `PROD-GH-M1`, while product tanks hold material `MAT-GH`. The only link, `products.*.material`, is read by no logic. | configs/production.yaml, warehouse module | product ↔ material identity is weak (U-03) |
| I-04 | `EquipmentLevel` defines Unit, ProcessCell, ProductionLine and WorkCell, but no element uses them. | `simulator/isa95/__init__.py`, site.yaml | none; mapped NOT_REPRESENTED |

## Unresolved decisions requiring human approval

| # | Question | Current state | Options |
|---|---|---|---|
| U-01 | Confirm CM-02: TEP equipment as Equipment Modules rather than Units? | EquipmentModule | keep; or document "Unit" as an alias interpretation for downstream ISA-95 tools |
| U-02 | Utility status is evaluator-only, so operational consumers get meters but no status. Should a future projection define an observable-only utility status? | none | none; a status derived only from meters and alarms (a context-layer derivation, not a simulator change) |
| U-03 | Product vs material identity (`PROD-GH-M1` vs `MAT-GH`) | two ids, weakly linked | keep both with `lot_of → product`; add a context relationship product → material from `products.material` |
| U-04 | Is structural `supports_utility` operational (CM-16)? | operational | keep; or restrict to evaluator-only if structural knowledge is part of a diagnosis task |
| U-05 | Canonical id string format for projections (e.g. `ctx:work_unit/WU-CWP-101A`, a UNS topic path, a KG IRI) | `<entity_type>:<native_id>` | decide when the first projection is built; the mapping from this form must be lossless |
| U-06 | Level 4 parties (customers, suppliers): customers are strings on orders; suppliers are a descriptive catalog | not entity types | keep as attributes; or add party entity types |
| U-07 | Should projections expose `LC-` lifecycle events (start, pause, resume, reset), which reflect wall-clock user actions? | operational | keep; or filter in projections that model a plant rather than a simulation |

Source:
- `contract/context_model.yaml`
- `configs/site.yaml`
- `configs/utilities.yaml`
- `configs/production.yaml`
