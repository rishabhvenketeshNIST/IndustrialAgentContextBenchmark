# Canonical contract audit

The audit behind contract version 0.1.0, extended for 0.2.0 (the operational boundary) ([contract](CANONICAL_SIMULATOR_CONTRACT.md),
`contract/canonical_contract.yaml`, [invariants](CANONICAL_SIMULATOR_INVARIANTS.md)).

* **Method.** The contract was derived from the implementation, the configuration and the tests, not
  from earlier documentation:
  * the entity kinds, properties, collections and writers were observed in a full SCN-COOL-001 run;
  * the couplings were read from the constructed `CouplingEngine`;
  * the routes were read from the FastAPI application;
  * the event types were read from `EventType`.
* **Behaviour.** No simulator behaviour was changed. The contract is documentation plus tests that
  read the implementation.

## Coverage

| Area | Covered | Evidence of completeness |
|---|---|---|
| Entities | 96: 87 ISA-95 elements (10 levels), 4 utilities, 5 materials | contract §5; entity kinds in the YAML |
| Record collections | 12 (work orders, technicians, spare parts, purchase orders, material lots, production orders, production lots, quality samples, shipments, alarms, operator log, faults) | `test_collections_match_contract` |
| Entity properties | every property observed in the full demo run (195 property × entity-class combinations), classified by 58 property entries, 5 loop-property entries and 2 TEP-binding rules | `test_every_entity_property_has_contract_semantics` |
| Semantic classes | 19 classes in the YAML; contract §6 defines each principal class by meaning, non-meaning, origin, TEP influence and observability | contract §6; `semantic_classes` in the YAML |
| Process variables | XMEAS 41, XMV 12, IDV 20, SETPT 20 (19 used), 19 loops with periods | `test_process_variable_catalog_matches_contract` |
| TEP boundary | 15 couplings plus 2 defined-but-uncoupled parameters; all other Fortran writes enumerated | `test_tep_boundary_couplings_match_contract`, `test_only_tep_boundary_relations_write_into_tep` |
| Coupling | 67 relations in 5 categories; 11 relationship types | contract §18 |
| Events | 46 types with visibility, id sequence and the state change each records | `test_event_types_and_visibility_match_contract` |
| API routes | 66 `/api` routes classified operational / control / benchmark (contract 0.2.0) | `test_every_api_route_is_classified` |
| Ground truth | fault records, cause channels, causal registry, correlation, FAULT_* events, overlays, model-internal state | contract §20 |
| Time | simulation time, step, timestamps, wall clock, event phase, analyzer and sample times, trends | contract §21 |
| Invariants | 14 holding (I1–I14); I13–I14 added in contract 0.2.0 | invariants document |

## Ambiguities discovered (A1–A12)

These are recorded, not resolved. None was fixed, because each fix would change behaviour or output.

| # | Ambiguity | Evidence | Consequence for consumers |
|---|---|---|---|
| A1 | `feed_impurity_deviation`, `feed_a_fraction_deviation` and `feed_b_fraction_deviation` are model-internal by intent but not tagged `unobservable` | `InventoryModule.setup` tags only the attributes and `supply_availability` | exposed by `/api/inventory` and `/api/entities` (G5) |
| A2 | One property name, several meanings: `flow` (a feed-line measurement vs a derived utility flow), `temperature` (a reactor measurement vs the CW supply temperature, which is also a TEP input), `pressure`, `status` (asset, utility, storage, record), `health` (asset condition vs a derived utility aggregate) | variable reference | consumers must key semantics on (entity class, property), never on the property name alone |
| A3 | Utility `status` is "operational", but DEGRADED is triggered by model-internal `availability < 0.999` | `UtilitiesModule._classify`; in the demo, `UT-CW-REACTOR` goes DEGRADED at 01:02:57, 18 min before the first observable symptom (vibration alarm at 01:20:33) | the status and its event reveal hidden capacity loss early; decide whether a real plant could know this |
| A4 | `CR-POWER-PROCESS-DEMAND` is category `utility_observation`, which never propagates causal labels, yet its output feeds `process_supply_fraction` → MCC → pump and compressor capability → `VRNG`, `CPFLMX`. It reads `XMEAS(20)` and `XMV(12)`, so a TEP → enterprise → TEP path exists | `configs/coupling.yaml` | under power constraints, TEP state feeds back into its own boundary through power demand, and causal labels on that path are suppressed. In a healthy plant the fraction stays clipped at 1 (no effect). |
| A5 | `UT-POWER` carries `pressure` and `temperature` properties that are always null | `UtilitiesModule.setup` registers them for every service | meaningless fields in the entity |
| A6 | Correlation semantics are heuristic: 0.5 % deviation from the t = 0 baseline, the first cause wins, labels persist until repair or reset, and some consequence events have none (QA-OFFSPEC) | `CouplingEngine._propagate_cause`, `AlarmModule._cause_keys` | correlation ids are evaluator labels, not a causal fact per event |
| A7 | `CanonicalState.snapshot(include_truth=False)` exists but no caller uses it | `simulator/state/__init__.py` | no truth-free view exists today |
| A8 | Storage units are `equipment`-kind entities, distinguished only by ISA-95 level `StorageUnit` | `_register_hierarchy` | consumers must use `meta.level`, not `kind`, to find storage |
| A9 | "availability" means different things: for a utility, the capacity fraction; for storage, a heel/ramp function of usable stock; assets have none | `UtilitiesModule._classify`, `InventoryModule._refresh_storage` | not a time-based availability (uptime); do not merge the two |
| A10 | `efficiency` = 1.0 placeholder on WU-TX-401, WU-MCC-401 and EM-AGITATOR; no relation computes it | variable reference | looks like a capability but carries no information |
| A11 | Sensor overlay `stuck` is implemented but no fault type creates it; no sensor-state event exists | `simulator/equipment/instrumentation.py` | sensor faults are visible only as quality flags and BADPV alarms |
| A12 | Events of the same step carry different times: pre-step events `t`, post-step and TEP-derived events `t + 1`; `LC-` and `EV-` ids are separate sequences | `SimulationEngine._step_once`, `EventBus.publish` | order simulation events by `EV-` id; order lifecycle events against them by `simulation_time` |

## Operational boundary audit (contract 0.2.0)

Every ground-truth finding was traced to where the information originates and to whether an operational
consumer could recover the hidden cause from it. The classes are:

1. confirmed operational ground-truth leak;
2. legitimate operational information;
3. internal-only information that must not be operational;
4. ambiguity needing a documented decision.

**Principle of the fix:** ground truth stays inside the simulator unchanged. Operational routes
(`/api/*` outside `/api/benchmark/*`) are built by one module, `api/operational.py`. Truth-bearing
routes and evaluator views are under `/api/benchmark/*`.

| # | Finding | Class | Where it leaked | What changed | Regression test |
|---|---|---|---|---|---|
| G1 | fault id in `correlation_id`; `causation_id` → hidden `FAULT_STARTED` | 1 | `/api/events`, snapshot | operational correlation and causation rebuilt from operational causation only; alarm → work order links kept | `test_operational_event_stream_is_self_contained_and_gap_free`, `test_operational_routes_do_not_reveal_fault_or_scenario_identity` |
| G2 | health, efficiency, capability | 1 | entities, equipment, maintenance, utilities, snapshot | properties tagged `model_internal` withheld, 404 like missing | `test_model_internal_properties_are_hidden_and_indistinguishable_from_missing` |
| G3 | boundary values | 3 | process image, coupling, snapshot | removed from the operational image; `/api/benchmark/process/image` | `test_history_and_process_image_exclude_truth` |
| G4 | `TRUE:XMEAS` history | 3 | history and catalog | not listed and rejected like unknown series; `/api/benchmark/history` | same |
| G5 | lot composition deviations | 1 | inventory, entities | tagged `unobservable` (A1 resolved) | `test_model_internal_properties_are_hidden_and_indistinguishable_from_missing` |
| G6 | exports | 3 | `/api/export/*` | moved to `/api/benchmark/export/*` | `test_truth_routes_are_not_operational`, `test_export` |
| G7 | scenario files | 3 | `/api/scenarios*` | moved to `/api/benchmark/scenarios*` | `test_truth_routes_are_not_operational`, `test_scenario_save_duplicate` |
| G8 | TEP internal states | 3 | `/api/process/internal-states` | moved to `/api/benchmark/process/internal-states` | `test_truth_routes_are_not_operational` |
| G9 | coupling relation values | 3 | `/api/coupling` | moved to `/api/benchmark/coupling` | same |
| G10 | UI shows truth next to operational views | 4 (D4) | UI | the UI is the benchmark console: its truth-bearing reads use `/api/benchmark/*`; it behaves as before | `test_ui_is_served`, `test_snapshot_reflects_engine_state` |
| G11 | manifest: fault list, `active_faults`, scenario id, run id, seed, TEP seed, configuration hash, event count | 1 | `/api/simulation/manifest`, `/api/simulation` | operational manifest keeps start, duration, mode, versions, library hashes, progress and shutdown only. The seed allows a fault-free twin to be replayed and differenced; the hash can be matched against scenario files; the event count reveals hidden events. | `test_manifest_and_simulation_state_carry_no_scenario_identity` |
| G12 | scenario name and description | 1 | `/api/simulation`, snapshot, `SIMULATION_STARTED`/`RESET` payloads | removed operationally; `/api/benchmark/simulation` | `test_operational_routes_do_not_reveal_fault_or_scenario_identity` |
| G13 (new) | asset DEGRADED status, `EQUIPMENT_DEGRADED`, RUNNING↔DEGRADED transitions, hierarchy roll-ups | 1 | entities, hierarchy, events | DEGRADED reported as RUNNING; those events withheld. In the demo this hid the 01:10:34 degradation, 10 min before the vibration alarm. | `test_hidden_degradation_is_not_reported_as_status` |
| G14 (new) | `EQUIPMENT_FAILED.reason` ("fault" vs "worn out"), health in equipment event payloads, `EQUIPMENT_REPAIRED.cleared_fault_effects` (fault ids) | 1 | events | payload fields removed | `test_operational_event_stream_is_self_contained_and_gap_free` |
| G15 (new, was A3) | utility status, availability, capacity fields, health; `UTILITY_STATE_CHANGED` | 1 | utilities, events | tagged `model_internal`; event type withheld. In the demo this hid DEGRADED at 01:02:57, 18 min before the first symptom. | `test_hidden_degradation_is_not_reported_as_status` |
| G16 (new) | gaps in sequential `EV-` ids at hidden events | 1 | `/api/events` | operational ids `OE-n` over the operational stream; the evaluator maps them via `/api/benchmark/events` (`operational_id`) | `test_operational_stream_matches_fault_free_run_until_the_first_symptom` |

**Evidence that the boundary holds.** Until the first observable symptom (the vibration alarm at
01:20:33), the operational event stream of SCN-COOL-001 is identical to that of the same scenario
without the fault: ids, types, targets, times and correlation all match.

**Evaluator access preserved.** `/api/benchmark/manifest`, `/api/benchmark/simulation`,
`/api/benchmark/events` (true correlation plus `operational_id`), `/api/benchmark/entities/{id}`,
`/api/benchmark/process/image`, `/api/benchmark/history`, `/api/benchmark/ground-truth`, exports
and scenario files (`test_evaluator_routes_retain_the_true_cause`).

**Simulator behaviour unchanged.** The only simulator edits are metadata tags:
* `UtilitiesModule` tags utility model-internal properties;
* `InventoryModule` tags `*_deviation` and no longer tags `supply_availability`;
* `EnterpriseView` gained a status hook for roll-ups.

The full SCN-COOL-001 run has the same internal event log, trends, final TEP states and run id as
before.

### Judged legitimate operational information (class 2)

| Item | Why it is legitimate |
|---|---|
| `vibration` and its alarms | a condition-monitoring instrument; it is the designed observable symptom |
| storage `supply_availability` and the `IA-*-E` alarms | a function of released stock and configuration. Reclassified from unobservable, since the old tag was over-conservative and inconsistent with the alarms defined on it. |
| cooling-water `utilization` | flow / min(available, capacity) equals XMV(10)/100 of the previous step exactly (`test_cooling_water_utilization_equals_valve_position`) |
| power `process_demand`, `shed_load`, `voltage` | electrical metering and executed load shedding |
| spare part `blocked` flag and work-order wait reasons | a stores system shows blocked stock (decision D3) |
| inspection findings | the product of a technician's inspection, an observation act with a delay |
| lab results, including a `quality_failure` offset | the lab result is the observation, including lab error |
| material lot `attributes` | certificate composition; the fault deviation is applied only to the storage properties, which are hidden |
| static TEP catalog (`/api/process/catalog`, including the IDV list) | published process documentation, not live flags |

### Decisions (class 4, no code change)

* **D1:** utility meter readings stay operational. CW header pressure, steam pressure and temperature,
  CW supply temperature and bus voltage model real instruments. They are, however, noise-free
  algebraic functions of capability, and the CW supply temperature is the random-walk *mean*, so they
  are cleaner than real instruments.
* **D2:** power `utilization` stays operational. Its denominator is the site power supply limit
  (transformer rating less a grid capacity loss), which a site energy system would know. The
  `UA-PWR-UTIL` alarm uses it.
* **D3:** spare-part `blocked` stays operational (see above).
* **D4:** the web UI is the benchmark console, not an operational interface. It shows truth and has the
  Fault Injection tab; a person using it is the benchmark operator.
* **D5:** `POST /api/simulation/create` accepts an inline scenario, including faults. That is a
  control-plane path to define causes outside `/api/benchmark`, not an information leak. It is left
  unchanged and should be restricted when a system under test is connected.

### Power-demand path (A4): investigated, not a leak

`CR-POWER-PROCESS-DEMAND` computes electrical load from compressor power (`XMEAS(20)`), agitator speed
(`XMV(12)`) and running motors. Under a power shortage, a higher load lowers
`process_supply_fraction`, and with it the MCC supply to the pumps, boiler and compressor, and so
`VRNG` and `CPFLMX`. This is a **legitimate physical coupling** (load vs supply), not an observation
leak: it exposes nothing, and in a healthy plant the fraction stays clipped at 1. Its category
`utility_observation` only means that causal labels do not propagate *from* the process load, which is
correct, because the process is not a cause. Labels from a power capacity fault still propagate
through `available_capacity`. Documentation corrected; no code change.

**Status of A1–A12 after contract 0.2.0:**

* A1: resolved (tagged `unobservable`).
* A3: resolved at the operational boundary (G15).
* A4: investigated; it is a legitimate coupling, not a leak (see below).
* A9: storage `supply_availability` reclassified as operational.
* A2, A5, A6, A7, A8, A10, A11 and A12: open; none of them exposes ground truth operationally.

## Ground-truth exposures found in the contract 0.1.0 audit

Two exposures were added to the existing G1–G10 list in
[benchmark limitations](11_limitations/benchmark_limitations.md):

* **G11** `GET /api/simulation/manifest` returns the run manifest *with* the fault list and
  `active_faults`.
* **G12** `GET /api/simulation` (and so `/api/ui/snapshot`) returns the scenario description. The
  SCN-COOL-001 description names the hidden fault.

## Undocumented behaviour now documented

* the TEP → enterprise → TEP power-demand path (A4);
* early utility-status detection of a hidden cause (A3);
* storage units as equipment-kind entities (A8);
* wall-clock time appears in no simulation output (contract §21).

## Implementation inconsistencies

A1, A5, A6 (the missing QA-OFFSPEC correlation), A7 and A10. They are listed in
[documentation vs implementation](11_limitations/documentation_vs_implementation.md) where they concern
stated intent.

## Decisions required from the project owner

1. **Resolved in contract 0.2.0:**
   * the observable view (now the operational boundary, I13 and I14);
   * A1 (tagged);
   * A3 (utility status is model-internal and not served; an observable-only status could be added
     later);
   * A4 (not a leak).
2. **D1–D5** above: confirm or revise.
3. **Access control:** serve operational routes separately or behind authentication before connecting
   a system under test.
4. **A4 label propagation:** should causal labels propagate through the power-demand relation? This
   affects only canonical correlation labels, not behaviour, and remains open.
5. **A2 / A5 / A10:** rename or remove overloaded and placeholder fields? Any of these is a MAJOR
   contract change.
6. **Versioning:** should the contract version be recorded in the run manifest?

## How the contract was validated

* `tests/test_contract.py`: 9 tests, all passing. The property-coverage test was negatively tested:
  removing one entry from an in-memory copy of the contract made it fail for all 10 assets.
* **No behaviour change:** the full SCN-COOL-001 run is identical before and after this work (event
  log, trends and final TEP states).
* Full results are recorded in `CHANGELOG.md`.
