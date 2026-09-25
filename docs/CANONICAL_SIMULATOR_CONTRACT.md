# Canonical Simulator Contract

| | |
|---|---|
| **Contract version** | 0.1.0 (draft) |
| **Describes simulator version** | 1.0.0 (`simulator/__init__.py` `__version__`) |
| **Machine-readable part** | `contract/canonical_contract.yaml` (checked by `tests/test_contract.py`) |
| **Invariants** | [CANONICAL_SIMULATOR_INVARIANTS.md](CANONICAL_SIMULATOR_INVARIANTS.md) |
| **Audit and open decisions** | [CANONICAL_CONTRACT_AUDIT.md](CANONICAL_CONTRACT_AUDIT.md) |

## 1. Purpose

This contract states precisely what the Enterprise Simulator produces, what each piece of
information means, who owns it, how it changes, whether it can affect TEP, and whether it is
operational information or benchmark ground truth.

Future layers (UNS, historian, knowledge graph, i3X, MCP, agents) are expected to consume the
simulator *through* these semantics. None of those layers exists in this repository.

```
SIMULATED REALITY → ENTERPRISE SIMULATOR → CANONICAL CONTRACT → {UNS, Historian, KG} → i3X → MCP → Agent
                     (defines reality)      (describes reality)
```

## 2. Scope

**In scope:**

* the canonical state (`CanonicalState`): entities, process image, record collections and production
  summary;
* the event log;
* the TEP boundary;
* coupling semantics, scenario and fault semantics;
* ground-truth classification;
* time and reproducibility;
* the classification of API routes by ground-truth exposure.

**Out of scope:**

* how any downstream layer represents this information (topic names, KG ontology, MCP tools);
* UI presentation;
* how the simulator is implemented internally, beyond what is needed to define the semantics.

## 3. Design principles

1. **The simulator defines reality; the contract describes it.** The contract never motivates a
   change to physics, control, coupling, fault behaviour, timing or event semantics.
2. **TEP is the only process physics.** Enterprise effects reach TEP only through defined process
   inputs (§17). The enterprise layer never rewrites process measurements or states.
3. **Every value has one owner.** Every property and record has exactly one writing module (the
   observed writers are listed in [variable reference](05_state_and_events/variable_reference.md)).
4. **Truth and observation are different things** (§6, §20). Semantics that the implementation does
   not separate cleanly are recorded as ambiguities, not invented as new fields.

## 4. Source of truth and the contract vs the implementation

* **Behaviour:** the implementation (`simulator/`, `configs/`, `scenarios/`, the Fortran sources)
  is authoritative for what happens.
* **Documented semantics:** this contract is authoritative for what the produced information *means*.
* **If the implementation violates the contract, that is a defect.** It is resolved deliberately: fix
  the implementation, or revise the contract under §23. It is never resolved by silently
  reinterpreting the simulator's output.
* **If they disagree and it is unclear which is right**, the disagreement is recorded in the
  [contract audit](CANONICAL_CONTRACT_AUDIT.md) as an ambiguity (A-number) and left for an owner
  decision.

Precedence for readers: README → [MASTER_SIMULATOR_SPEC](MASTER_SIMULATOR_SPEC.md) → **this
contract** → subsystem and reference documentation → source code and tests.

## 5. Entity model

| Entity type | Identifier (examples) | Parent | Purpose | Source | Lifecycle |
|---|---|---|---|---|---|
| Enterprise | `ENT-ACME` | none | root of the ISA-95 hierarchy | `configs/site.yaml` | static |
| Site | `SITE-TE` | Enterprise | the Tennessee Eastman site; also the target of site-wide events | `configs/site.yaml` | static |
| Area, WorkCenter, ProductionUnit, WorkUnit, EquipmentModule, ControlModule, StorageZone, StorageUnit | `AREA-…`, `WC-CW`, `PU-STRIPPER`, `WU-CWP-101A`, `EM-REACTOR`, `CM-TIC-RCW`, `SU-TK-101` | per `ALLOWED_CHILDREN` in `simulator/isa95/__init__.py` | plant structure; TEP variables bound to equipment | `configs/site.yaml`, `configs/tep_mapping.yaml` | static |
| Utility service | `UT-CW-REACTOR`, `UT-CW-CONDENSER`, `UT-STEAM`, `UT-POWER` | supplied by a WorkCenter/WorkUnit (`supplied_by`) | resource supply to the process | `configs/utilities.yaml` | static |
| Material | `MAT-A`, `MAT-D`, `MAT-E`, `MAT-AC`, `MAT-GH` | none | raw materials and product | `configs/materials.yaml` | static |
| Material lot | `LOT-D-2611`, `LOT-D-R001` | a StorageUnit (`location`) | received raw material with composition | initial lots; deliveries | created on receipt, consumed FIFO |
| Production lot | `PL-0003` | an order (`order_id`), a tank (`location`) | product made in one period | `ProductionModule` | IN_PROCESS → AWAITING_QC → RELEASED / QUARANTINE / REJECTED |
| Production order | `PO-2026-0201` | product | demand to fulfil | scenario `production_orders`; operator | order state machine (`simulator/production/orders.py`) |
| Work order | `WO-00001` | an asset or system (`asset_id`) | maintenance job | `MaintenanceModule` | REQUESTED → WAITING_* → SCHEDULED → IN_PROGRESS → COMPLETED / CANCELLED |
| Quality sample | `QS-00009` | a lot (`lot_id`), a product or material (`subject_id`) | analyzer or lab result | `QualityModule` | created with its result |
| Alarm | `TAH-09`, `VAH-CWP101A`, `OUTH-L10` | an entity (`equipment_id`) | configured limit on a value | `configs/alarms.yaml` plus generated ones | NORMAL ↔ ACTIVE_UNACK / ACTIVE_ACK / RTN_UNACK |
| Technician, spare part, purchase order, shipment | `TECH-01`, `SP-IMP-101`, `PO-00001`, shipment id | none | maintenance resources, procurement, dispatch | configs; modules | per module |
| Fault **[benchmark]** | `F-COOL-001` | a target entity | injected cause | scenario `faults`; `/api/benchmark` | CREATED → SCHEDULED → ACTIVE → STOPPED → RESET |
| Event | `EV-0000037`, `LC-0000001` | none | immutable record of something that happened | every module | append-only |
| Process variable | `XMEAS(9)`, `XMV(10)`, `IDV(4)`, `SETPT(10)` | a bound equipment entity | TEP variable | `simulator/tep/catalog.py`, `configs/tep_mapping.yaml` | static identity, value every second |

**How entities are stored:**

* ISA-95 elements (including storage units), utilities and materials are *entities*: they have an
  id, a `kind` (`equipment` | `utility` | `material`), `properties`, `units` and `meta`.
* Lots, orders, work orders, samples, alarms, technicians, parts, purchase orders, shipments,
  operator log and faults are *records* in named collections.
* The identity of every entity and record is stable for the whole run.

The full entity list is in [isa95 entity table](02_manufacturing_model/isa95_entity_table.md). The
machine-readable kinds and collections are in `contract/canonical_contract.yaml`.

## 6. State model: the semantic classes

Every value belongs to exactly one class. Each class is defined by what it means, what it does not
mean, where it comes from, whether it can influence TEP, and whether normal operational interfaces
show it.

| Class | Means | Does NOT mean | Origin / updater | Can influence TEP? | Operationally observable? |
|---|---|---|---|---|---|
| **measurement** | a TEP XMEAS value: *true* (`process.xmeas_true`, TEFUNC incl. TEP noise) or *transmitted* (`process.xmeas`, after instrumentation) | a guaranteed-correct value (the transmitted form can be biased); anything the enterprise layer computed | TEP; `ProcessInterface.sync` | only through native control (the transmitted form feeds CONTRLn) | transmitted: yes; true: should not be (leak G4) |
| **setpoint** | the target of a native loop, `SETPT(k)` | a measurement; a limit | TEINIT constants; cascade master; operator | yes (native control) | yes |
| **controller output** | what a loop writes: an XMV, or another loop's setpoint | a flow or position; a measurement | native `CONTRLn` | yes | yes (`process.loops[*].output`) |
| **manipulated variable** | TEP XMV, a valve/actuator *command* in % | the valve position (VPOS lags with VTAU); a delivered flow | native control; operator in MAN | yes | yes |
| **condition** (`health`) | physical condition of an asset, 0..1 | a measurement; availability; capability | `EquipmentModule` (wear, fault damage, repair) | yes, via capability relations | **no**: model-internal (tagged `model_internal`) |
| **condition indicator** (`vibration`) | observable symptom derived from condition | a direct health reading | `EquipmentModule` | no | yes |
| **capability** (`efficiency`, `available_flow`, `available_capacity`, `capacity_fraction`, `capability`, …) | what an asset or service *can* deliver given its condition | what it is delivering; a measurement | `CouplingEngine` relations | yes: this is the path into TEP | **no**: model-internal |
| **availability** | a 0..1 fraction of design supply that can be delivered (utility `availability`; storage `supply_availability`) | time-based availability (uptime %); health | `UtilitiesModule`; `InventoryModule` | storage: yes; utility `availability` itself: no (its source `capacity_fraction` does) | no: model-internal (ambiguity A9) |
| **utility observation** (`flow`, `demand`, `utilization`, `pressure`, `voltage`, `temperature`) | a meter-like value computed by an algebraic approximation | a physically simulated quantity | `CouplingEngine` (`utility_observation` relations) | CW supply `temperature` and power `process_demand`: yes (A4); others: no | yes |
| **discrete state** (`status`, `is_running`, `desired_state`, `role`, record status) | the current value of a finite state | the moment it changed (that is an event) | owning module | asset `is_running`/`status`: yes (via capability) | yes (utility `status`: see A3) |
| **derived value** | computed each step from other values, no independent memory | a cause | relation or module | depends on inputs | per the table |
| **event** | an immutable record that something happened at a simulation time | the current state | publishing module | no (events trigger handlers, which change state) | operational events: yes; FAULT_*: no |
| **boundary parameter** | enterprise-computed TEP input (§17) | a measurement; a TEP state | `CouplingEngine` → `ProcessInterface.set_boundary` | yes, by definition | should not be (leak G3) |
| **configuration** | static design value (`rated_*`, `nominal_*`, `capacity`) | live state | configs | some are relation inputs | yes |
| **ground truth** | the injected cause and its attribution: faults, cause channels, causal registry, correlation ids | an observation | `FaultEngine`, `CouplingEngine` | the fault's cause channels feed modules and relations | **no** by intent; partly exposed today (§20) |

Per-property classification of every property the simulator produces is in
`contract/canonical_contract.yaml`, with owner, observability and TEP influence.
`test_every_entity_property_has_contract_semantics` fails if a property appears that has no class.

## 7. Measurements (XMEAS 1–41)

* **Identity and units:** `simulator/tep/catalog.py`, transcribed from `teprob.f`. Reference table:
  [measurements](03_tep/measurements.md).
* **Direction:** TEP → enterprise. Written only by TEFUNC.
* **Update:** XMEAS 1–22 every second with TEP noise; XMEAS 23–36 sampled every 0.1 h with 0.1 h dead
  time; XMEAS 37–41 every 0.25 h with 0.25 h dead time. A value changes only when TEP updates it.
* **Two forms per step:**
  * `process.xmeas_true`: what TEFUNC computed.
  * `process.xmeas`: transmitted, after bias, drift or dropout overlays, with a quality flag
    `GOOD`/`BAD`. Mapped equipment properties (for example `EM-REACTOR.temperature`) are copies of
    the transmitted form.
* **Readers of the true form:** inventory consumption (the tank drains at the true rate) and nothing
  else in enterprise logic. The trip check uses noise-free TEP internals.

## 8. Setpoints

* **Where:** `SETPT(1..20)`, of which 19 loops use 19 (`process.setpoints`, loop table
  `process.loops`).
* **Writers:**
  * initial values from `simulator/tep/control_scheme.py` (the constants of `temain_mod.f` MAIN);
  * for cascade slaves, the master loop;
  * otherwise the operator (`set_setpoint`), which is refused while an executing master owns the
    setpoint.
* **Not clamped:** the native scheme does not clamp setpoints. A master's output can therefore take
  physically meaningless values (native wind-up, §11).

## 9. Manipulated variables (XMV 1–12)

* **Identity:** `simulator/tep/catalog.py`, reference table [manipulated variables](03_tep/manipulated_variables.md).
* **Meaning:** a command in %. The valve position `VPOS` is a TEP state that follows with a first-order
  lag.
* **Writers:** the owning native loop, or the operator when that loop is not executing (loop in MAN,
  or plant MANUAL).
* **Clamping:** `CONSHAND` clamps XMV 1–11 to 0–100 % after the controllers run. XMV(12), agitator
  speed, has no loop.
* **Disturbances (IDV 1–20):** binary process disturbances, written only by the fault engine's
  TEP-native fault types ([disturbances](03_tep/disturbances.md)).

## 10. Equipment state

For an asset (`meta.asset`, 10 assets in `configs/equipment.yaml`):

| Term | Implementation meaning | Example (demo) |
|---|---|---|
| `health` (condition) | `intrinsic_health − fault damage`, clipped to 0..1. Intrinsic health wears only while running and is restored by corrective maintenance. | P-101A 0.97 → 0.348 |
| `status` (state) | RUNNING, DEGRADED (health < 0.75 while running), STANDBY, STOPPED, FAILED (health ≤ 0.05 or `failed` channel), UNDER_MAINTENANCE | P-101A DEGRADED at 01:10:34 |
| `is_running` | 1 when status is RUNNING or DEGRADED | — |
| `efficiency` (capability) | `clip(health) × (1 − efficiency_loss)` for pumps, cooling tower, boiler and compressor. **Placeholder 1.0** on WU-TX-401, WU-MCC-401 and EM-AGITATOR (A10). | — |
| `available_flow` / `available_steam` / `available_kw` / `capability` / `supply_fraction` / `supply_temperature_rise` (capability) | deliverable output given efficiency, running state and electrical supply | P-101A `available_flow = 1100 × eff × run × pf` |
| availability | assets have no availability property. Availability exists only for utilities and storage (A9). | — |
| `vibration` | observable indicator `1.8 + 10·(1 − health)² + noise` while running, else 0 | VAH alarm at 3.8 mm/s |

**What "capacity_fraction = 0.7" means.** For a utility,
`capacity_fraction = clip(available_capacity / design capacity, 0, 1)`. It is the fraction of the
**circuit design flow** that the running supply equipment *can* deliver. It is not health, not uptime,
and not the flow actually delivered. Because pumps are rated above design (1100 vs 1000), a pump at
health 0.7 does **not** give 0.7: it gives min(1, 1100·0.7/1000) = 0.77 if it is the only running
pump.

## 11. Control semantics

* **Measurement → controller → controller output → manipulated variable → process response.** Each of
  these steps is a separate value, stored and shown separately (`process.loops` has `pv`, `setpoint`,
  `output`, `mode`, `saturated`, `error`).
* **The native control law is unmodified `temain_mod.f`:**
  * 19 velocity-form P/PI loops, executed before each integration step when due (every 3 s, 360 s
    or 900 s);
  * controllers act on the *transmitted* measurements;
  * no anti-windup: XMV is clamped by `CONSHAND`, cascade setpoints are not clamped.
* **Saturation, wind-up and undershoot are native TEP behaviour** (demo: `SETPT(10)` reaches −675.7).
  They are part of the simulated reality and are not defects of the contract.
* **The enterprise layer adds only mode handling:**
  * plant CLOSED_LOOP/MANUAL; loop AUTO/CAS/MAN (a loop in MAN is not called);
  * bumpless MAN → AUTO through `ERROLDn`;
  * validated operator setpoints and outputs.

Details: [native control](03_tep/native_control.md), [loop table](03_tep/native_control_loops.md).

## 12. Utility / resource state

All four utilities are **abstracted, derived capacity constraints**. None is physically simulated. The
full relations are in [coupling relations](04_coupling/coupling_relations.md).

| Resource | Capacity (design) | Available capacity from | Demand / flow from | Status | TEP coupling |
|---|---|---|---|---|---|
| `UT-CW-REACTOR` | 1000 (= native `VRNG(10)`) | pumps WU-CWP-101A/B `available_flow`, minus `capacity_loss` | `XMV(10) × VRNG(10)/100` (observation, previous step) | NORMAL / DEGRADED / CONSTRAINED / UNAVAILABLE | `capacity_fraction` → `VRNG(10)`; supply `temperature` → `SZERO(5)` |
| `UT-CW-CONDENSER` | 1200 (= `VRNG(11)`) | pumps WU-CWP-201A/B | `XMV(11) × VRNG(11)/100` | same | → `VRNG(11)`; → `SZERO(6)` |
| `UT-STEAM` | configured | boiler `available_steam`, minus loss and other demand | `XMEAS(19)` + other demand | same | `capacity_fraction` → `VRNG(9)` |
| `UT-POWER` | configured | transformer `available_kw`, minus loss | process demand from `XMEAS(20)`, `XMV(12)` and running motors (A4) | same | indirect: `process_supply_fraction` → MCC → pumps, boiler, compressor capability |

**Status rules** (`UtilitiesModule._classify`):

* **UNAVAILABLE** when availability < 0.02;
* **CONSTRAINED** when utilization ≥ 0.97;
* **DEGRADED** when availability < 0.999 or health < 0.75;
* **NORMAL** otherwise.

`availability` is the capacity fraction. Steam pressure and temperature, and bus voltage, are computed
but enter TEP nowhere.

## 13. Production state

* **Metering:** `rate_kg_h = transmitted XMEAS(17) × 613.4`, and 0 after a trip.
* **Line state:** RUNNING / REDUCED_RATE / DOWN, from a 5-minute moving average against 14,076 kg/h.
* **Lots:** one lot per 3600 s of running, closed early on an order change, order completion or DOWN.
* **Orders:** run one at a time.
* **KPIs:** `state.production` holds rate, totals, run/down time and OEE.
* **Reference:** [production state](05_state_and_events/production_state.md).

## 14. Quality state

* **Samples:** a sample is created whenever the transmitted product analyzer (XMEAS 37–41) updates.
* **Tests:** configured expressions with limits (`configs/quality.yaml`).
* **Represented period:** each sample represents product made at `taken_s − 900`, and is assigned to
  the lot that was running then.
* **Lot decision:** 900 s after a lot closes, from the fraction of its samples that failed:
  0 → RELEASED, ≤ 25 % → QUARANTINE, otherwise REJECTED; no samples → QUARANTINE.
* **Incoming material:** inspected 600 s after receipt.
* **Reference:** [quality state](05_state_and_events/quality_state.md).

## 15. Maintenance state

* **Work orders:**
  * **Kinds:** corrective, inspection, planned.
  * **Priorities:** 1–4, with response times 600 / 1500 / 3600 / 14400 s.
  * **Constraints:** technician skills and shifts, spare parts, ± 10 % seeded duration jitter.
* **Effect on equipment:**
  * Corrective and planned work isolate the asset at start and switch to a standby unit.
  * Completion restores health and clears the `damage`, `efficiency_loss` and `failed` channels.
* **Inspections** flag only assets that are *running* and below the thresholds.
* **Planned work:** preventive maintenance comes only from a scenario's `planned_maintenance`.
* **Reference:** [maintenance state](05_state_and_events/maintenance_state.md).

## 16. Events

**Fields:**

* `event_id`: `EV-nnnnnnn` for simulation events. `LC-nnnnnnn` for lifecycle events (start, pause,
  resume, reset), which depend on wall-clock interaction.
* `timestamp`: ISO-8601 UTC, `simulation_start + simulation_time`.
* `simulation_time`: integer seconds.
* `type` (46 types), `source` (module), `target` (entity or record id), `payload`, `severity`.
* `visibility`: `operational` | `benchmark`.
* `correlation_id`, `causation_id`.

**State vs event:**

* **State is a current value; an event is the record of a change or occurrence.**
  `EQUIPMENT_DEGRADED` records the moment the health band left GOOD; `status = DEGRADED` is the
  state. Replaying events rebuilds discrete states, but not continuous values.
* **Some events record no state change:** `MATERIAL_CONSUMED` is a periodic posting every 900 s;
  `QUALITY_SAMPLE_TAKEN` records an occurrence.

**Ordering and lifecycle:**

* The bus is synchronous: handlers run inside `publish`. The log is append-only and ordered, and
  `EV-` ids are strictly increasing.
* Events have no lifecycle of their own; records such as alarms and work orders do, and events
  report their transitions.

**Visibility and correlation:**

* The five `FAULT_*` types are `benchmark` visibility.
* `correlation_id` and `causation_id` on operational events may carry a fault id. That is ground
  truth (§20).

**References:** machine-readable list in `contract/canonical_contract.yaml`; observed payloads in
[event catalog](05_state_and_events/event_catalog.md); lifecycle in
[event lifecycle](05_state_and_events/event_lifecycle.md).

## 17. TEP boundary

**What enterprise state may influence TEP.** Only these 15 couplings, each written in the pre-step
and only when the value changes. `test_tep_boundary_couplings_match_contract` enforces the list.

| Enterprise variable | Transformation | TEP target | Physical meaning | TEFUNC use |
|---|---|---|---|---|
| `UT-CW-REACTOR.capacity_fraction` | `1000 × frac` | `VRNG(10)` | reactor CW flow at 100 % valve | `FWR = VPOS(10)·VRNG(10)/100` |
| `UT-CW-CONDENSER.capacity_fraction` | `1200 × frac` | `VRNG(11)` | condenser CW flow at 100 % valve | `FWS = VPOS(11)·VRNG(11)/100` |
| `UT-CW-REACTOR.temperature` | identity | `SZERO(5)` | mean of reactor CW inlet temperature walk | `TCWR = TESUB8(5,TIME) + IDV(4)·5` |
| `UT-CW-CONDENSER.temperature` | identity | `SZERO(6)` | mean of condenser CW inlet temperature walk | `TCWS = TESUB8(6,TIME) + IDV(5)·5` |
| `UT-STEAM.capacity_fraction` | `0.03 × frac` | `VRNG(9)` | stripper steam heat-transfer range | `UAC = VPOS(9)·VRNG(9)·(1 + TESUB8(9))/100` |
| `EM-COMPRESSOR.capability` | `280275 × cap` | `CPFLMX` | recycle compressor capacity | `FLMS = CPFLMX + FLCOEF·(1 − PR³)` |
| `SU-SPH-103.supply_availability` | `100 × avail` | `VRNG(3)` | A feed line capacity | `FTM(3) = VPOS(3)·(1 − IDV(6))·VRNG(3)/100` |
| `SU-TK-101.supply_availability` | `400 × avail` | `VRNG(1)` | D feed line capacity | `FTM(1) = VPOS(1)·VRNG(1)/100` |
| `SU-TK-102.supply_availability` | `400 × avail` | `VRNG(2)` | E feed line capacity | `FTM(2) = VPOS(2)·VRNG(2)/100` |
| `SU-SPH-104.supply_availability` | `1500 × avail` | `VRNG(4)` | A+C feed line capacity | `FTM(4) = VPOS(4)·(1 − 0.2·IDV(7))·VRNG(4)/100` |
| `SU-TK-101.feed_impurity_deviation` | `0.0001 + dev` | `XST(2,1)` | B impurity in D feed | `FCM(I,1) = XST(I,1)·FTM(1)` |
| `SU-TK-102.feed_impurity_deviation` | `0.0001 + dev` | `XST(6,2)` | F impurity in E feed | `FCM(I,2) = XST(I,2)·FTM(2)` |
| `SU-SPH-103.feed_impurity_deviation` | `0.0001 + dev` | `XST(2,3)` | B impurity in A feed | `FCM(I,3) = XST(I,3)·FTM(3)` |
| `SU-SPH-104.feed_a_fraction_deviation` | `0.485 + dev` | `SZERO(1)` | mean A fraction of stream 4 | `XST(1,4) = TESUB8(1,TIME) − …` |
| `SU-SPH-104.feed_b_fraction_deviation` | `0.005 + dev` | `SZERO(2)` | mean B fraction of stream 4 | `XST(2,4) = TESUB8(2,TIME) + …` |

**Constraints:**

* **Clamping:** values are clamped to each parameter's range (`simulator/tep/boundary.py`).
* **Native at t = 0:** every transformation yields exactly the TEINIT value when the enterprise is
  healthy (invariant I2).
* **Timing:**
  * `VRNG`, `CPFLMX` and `XST` act in the same step.
  * `SZERO` acts at the next random-walk knot (0.1–1.7 h depending on the walk).
* **Not coupled:** `SZERO(3)` (D feed temperature) and `SZERO(4)` (stream 4 temperature) are defined
  but not driven by any relation.

**Other enterprise writes into Fortran memory** (complete list in
[coupling contract](04_coupling/coupling_contract.md)):

* IDV flags (fault engine only);
* an operator XMV (only for a loop in MAN);
* an operator setpoint (only when no executing master owns it);
* `ERROLDn` on a bumpless mode change;
* the transmitted-PV swap into `/PV/` during `CONTRLn`, restored immediately;
* initialisation.

**What TEP state may influence enterprise state:**

* transmitted XMEAS: alarms, quality, production, utility observations, mapped properties;
* XMV: utility flow and power-demand relations, control-module properties, saturation alarms;
* true XMEAS: inventory consumption only;
* the shutdown flag;
* loop table and setpoints.

**What is never directly overwritten:**

* TEP states and derivatives;
* the XMEAS values TEFUNC computes;
* the TEFUNC equations;
* controller tuning after initialisation;
* trip limits.

The enterprise layer never manufactures an outcome by writing a process value.

## 18. Coupling and relationship semantics

**The executed coupling graph:**

* 61 definitions in `configs/coupling.yaml`, expanding to 67 relations.
* They are validated at start-up (known references, a single writer per output, no cycles,
  whitelisted expressions), topologically sorted, and evaluated every step before the TEP step.
* Relations reading `process.*` see the previous step's values.

**Relationship types.** Relationships are not all causal. The variable graph
([edge types](07_variable_graph/edge_types.md)) uses the following types, derived from the
implementation:

| Type | Meaning | Executed dynamically by the simulator? |
|---|---|---|
| `cause_injection` | a benchmark cause enters simulated state | yes |
| `capability_constraint` | condition limits what equipment can deliver | yes (relations) |
| `supply_dependency` | a supply aggregates or depends on another | yes (relations) |
| `tep_boundary_interface` | enterprise value written into TEP | yes (the only edges into TEP) |
| `process_response` | consequence inside TEP | **no**: TEP computes it; the edge documents it (partly hand-curated) |
| `control_measurement` / `control_actuation` / `control_cascade` | native loop wiring | yes (native Fortran) |
| `derivation` | value computed from others; not a physical cause | yes |
| `state_transition` | a record changes state because of another state | yes (module code) |
| `event_trigger` | threshold crossing → event → record | yes |
| correlation (runtime label, not an edge) | an event is attributed to a fault | heuristic labelling only; changes no value |

"A relationship exists" means the implementation contains a mechanism by which A can change B.
"The simulation executes it" means code runs that mechanism every step. `process_response` edges exist
but are executed by TEP's own equations, not by any relation.

## 19. Scenario model

**Configured before the run** (scenario file plus `config_overrides` on `configs/*.yaml`):

* id, name, description, `seed`, `duration_seconds`, `simulation_start`, backend, control mode;
* **faults** (the causes);
* production orders;
* planned maintenance;
* scripted operator actions (the only reproducible interventions).

**Emerges during the run:**

* every **effect**: equipment condition, capacity, process response, alarms, work orders, lots,
  quality results;
* **interventions** by policy (maintenance, changeover) or by an interactive operator;
* **recovery** (process transients after capacity returns).

**Terms:**

| Term | Meaning in this implementation |
|---|---|
| fault | a record with type, target, trigger, severity, progression and duration **[benchmark]** |
| cause | the value a fault writes into a cause channel (`fault_effects`), an IDV flag, or a sensor overlay |
| effect | anything computed downstream of a cause; never written by the fault engine |
| intervention | an operator command, a scripted operator action, or a maintenance action |
| recovery | the process and enterprise return after an intervention. Not guaranteed: in the demo it is incomplete at 3 h. |
| ground truth | the fault records, cause channels, causal registry, correlation ids, expected effects |

**Stop vs reset:** stop ends the cause, and persistent damage remains until repair; reset removes the
fault's contributions entirely. See [scenario model](06_scenarios/scenario_model.md) and
[fault injection](06_scenarios/fault_injection.md).

## 20. Ground truth

| Information | Class | Where it lives | Intended audience |
|---|---|---|---|
| fault records, types, targets, severity, intensity, expected effects, observability notes | benchmark | `collections.faults`, scenario files | benchmark operator / evaluator |
| cause channels | benchmark | `state.fault_effects` | benchmark |
| causal registry | benchmark | `state.causal` | benchmark |
| `correlation_id`, `causation_id` when they carry a fault id | benchmark | every event | benchmark |
| FAULT_* events | benchmark | event log (`visibility = benchmark`) | benchmark |
| sensor overlays | benchmark | instrumentation | benchmark |
| true XMEAS, TEP states, boundary values | simulation truth | process image | evaluator; development |
| `health`, capability, availability, lot composition and deviations | simulation truth (model-internal) | entity properties | evaluator; development |
| transmitted XMEAS, XMV, setpoints, modes, alarms, statuses, vibration, utility observations, records | operational | canonical state | a plant operator; a future system under test |

**Simulation truth vs operationally observable information:**

* **Enforced today:**
  * fault *control* exists only under `/api/benchmark/*`;
  * FAULT_* events are excluded from `/api/events`.
* **Not enforced today:**
  * the other truth reaches operational routes, as leaks G1–G12
    ([benchmark limitations](11_limitations/benchmark_limitations.md));
  * `contract/canonical_contract.yaml` → `api_routes` records, for every route, which exposures it
    carries.
* **Rule for downstream layers:** an agent-facing layer must be built from an explicit **allow-list**
  of operational classes. It must strip fault-derived correlation, and must not forward development
  or benchmark routes. Until that exists, the current API is a benchmark-operator and development
  interface.

## 21. Time semantics

| Quantity | Meaning |
|---|---|
| simulation time | integer seconds since start (`clock.time_s`); the only clock the simulation uses |
| step | 1 simulated second = one TEP Euler step (`DELTAT` = float32(1/3600) h) |
| timestamps | `simulation_start` (scenario, UTC) + simulation time, ISO-8601 at second resolution |
| wall-clock time | used only to pace the runner (speed × elapsed time, at most 2000 steps per 20 ms tick); it never appears in simulation data |
| event time | pre-step events carry `t`; TEP-derived and post-step events carry `t + 1` |
| process image | values after the integration from `t` to `t + 1` |
| analyzer values | XMEAS 23–41 change only at sample times and represent the process one dead time earlier |
| quality sample | `taken_s` = analyzer update time; `represents_s = taken_s − 900` |
| trend samples | every 10 simulated seconds (in-memory, 20,000 samples) |
| record times | `requested_s`, `start_s`, `qc_due_s`, … are simulation seconds |
| duration | `duration_seconds`; the run stops there (`SIMULATION_COMPLETED`) |

## 22. Determinism and reproducibility

Identical scenario, configuration and **compiled TEP library** give identical event logs,
trajectories and trends. This is tested on 2 h of the demo. The mechanisms:

* named PCG64 random streams per module, plus a derived TEP seed;
* common blocks zeroed before TEINIT;
* single-precision native controller constants;
* a fixed module order and sorted iteration;
* a synchronous event bus;
* `LC-` ids for wall-clock-dependent lifecycle events.

**Manifest:** records the run id, seeds, backend, library and source SHA-256, simulator version and
configuration hash.

**Not reproducible:**

* across library builds or platforms;
* between the Python and Fortran backends;
* for interactive commands not expressed as scripted operator actions.

**Reference:** [seeds and reproducibility](10_operation/seeds_and_reproducibility.md).

## 23. Versioning

There are two independent versions:

* **Simulator version** (`simulator.__version__`, currently 1.0.0, recorded in every run manifest):
  identifies the *behaviour*.
* **Contract version** (`contract.version` in `contract/canonical_contract.yaml`, currently 0.1.0):
  identifies the *documented semantics*. It is not stored in run outputs (see §25).

**Rules (semantic versioning of the contract):**

* **MAJOR:** a documented meaning changes or is removed, for example a property changes class, an
  event type is removed, or a boundary coupling changes target or transformation. Downstream layers
  must be reviewed.
* **MINOR:** something is added with new semantics, for example a new event type, property,
  coupling or route classification.
* **PATCH:** wording or clarification with no semantic change.
* **Behaviour changes:** a simulator change that alters any documented semantics requires a contract
  change in the same commit. `tests/test_contract.py` enforces this for events, boundary couplings,
  properties, collections and routes.
* **Pre-1.0:** the contract stays 0.x while the ambiguities in the
  [contract audit](CANONICAL_CONTRACT_AUDIT.md) remain undecided.

## 24. Current limitations of the contract

* **Ambiguities:** several semantics are ambiguous in the implementation and are recorded rather than
  resolved (A1–A12 in the audit). Examples: utility `status` depends on model-internal availability;
  the power demand is labelled an observation but feeds supply; property names are reused with
  different meanings across entity kinds.
* **Payloads:** event payloads are documented by observation (event catalog), not by a per-type
  schema.
* **Process variables:** the per-variable semantics of XMEAS, XMV and IDV are referenced from the
  generated tables, not duplicated here.
* **Ground-truth exposure:** the contract documents it; it does not prevent it.

## 25. Future considerations

These are not implemented:

* an allow-listed **observable view** served separately from benchmark and development routes, with
  fault-derived correlation stripped (resolves G1–G12);
* tagging `*_deviation` storage properties `unobservable` (A1);
* per-event-type payload schemas;
* recording the contract version in the run manifest;
* distinct property names where one name has several meanings (A2);
* a dedicated sensor-state event and a `stuck` sensor fault type (A11).
