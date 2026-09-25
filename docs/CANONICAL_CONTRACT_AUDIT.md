# Canonical contract audit

The audit behind contract version 0.1.0 ([contract](CANONICAL_SIMULATOR_CONTRACT.md),
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
| API routes | 56 `/api` routes classified operational / control / development / benchmark, with ground-truth exposures | `test_every_api_route_is_classified` |
| Ground truth | fault records, cause channels, causal registry, correlation, FAULT_* events, overlays, model-internal state | contract §20 |
| Time | simulation time, step, timestamps, wall clock, event phase, analyzer and sample times, trends | contract §21 |
| Invariants | 12 holding (I1–I12) and 2 target invariants violated today (T1, T2) | invariants document |

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

## Ground-truth exposures found in this audit

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

1. **Observable view:** define an allow-listed view for any agent-facing layer (T1, T2, G1–G12).
2. **A1:** tag `*_deviation` as `unobservable`? This is a behaviour and output change.
3. **A3:** should utility status use only observable inputs (utilization, meter readings)?
4. **A4:** is the power-demand feedback into TEP intended, and should its label propagation be
   enabled?
5. **A2 / A5 / A10:** rename or remove overloaded and placeholder fields? Any of these is a MAJOR
   contract change.
6. **Versioning:** should the contract version be recorded in the run manifest?

## How the contract was validated

* `tests/test_contract.py`: 9 tests, all passing. The property-coverage test was negatively tested:
  removing one entry from an in-memory copy of the contract made it fail for all 10 assets.
* **No behaviour change:** the full SCN-COOL-001 run is identical before and after this work (event
  log, trends and final TEP states).
* Full results are recorded in `CHANGELOG.md`.
