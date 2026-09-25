# Documentation audit

Complete documentation pass over the enterprise simulator. The implementation was treated as the
source of truth. **No simulator behaviour was changed.** Verified: no file under `simulator/`, `api/`,
`configs/`, `ui/`, `tests/`, `scenarios/`, nor `run.py`, `Dockerfile` or `requirements.txt`, was
modified in this pass.

## Files created

**Documentation tree (`docs/`, 97 new Markdown files + 2 generated graph files):**

* `README.md` (read this first), `MASTER_SIMULATOR_SPEC.md`, `DOCUMENTATION_AUDIT.md`
* `01_overview/`: simulator_overview, goals_and_scope, design_principles, architecture, terminology
* `02_manufacturing_model/`: isa95_model, enterprise, site, areas, equipment, utilities,
  materials_and_storage, production, maintenance, quality, isa95_entity_table *(generated)*
* `03_tep/`: tep_overview, tep_version_and_provenance, process_units, native_control,
  safety_and_shutdown, tep_integration; measurements, manipulated_variables, disturbances,
  native_control_loops *(generated)*
* `04_coupling/`: coupling_architecture, coupling_contract, boundary_variables, equipment_to_tep,
  utilities_to_tep, maintenance_to_tep, quality_from_tep, coupling_graph; coupling_relations *(generated)*
* `05_state_and_events/`: canonical_state, state_model, event_model, equipment_state, utility_state,
  production_state, quality_state, maintenance_state, event_lifecycle; variable_reference, event_catalog
  *(generated)*
* `06_scenarios/`: scenario_model, fault_injection, fault_taxonomy, causal_chains,
  intervention_and_recovery; `scenario_examples/` SCN-COOL-001 (worked example), SCN-BASELINE,
  SCN-FAULT-LIBRARY, SCN-COOL-001_trace *(generated)*
* `07_variable_graph/`: graph_model, node_types, edge_types, causal_semantics, graph_generation;
  variable_graph.json and variable_graph.html *(generated)*
* `08_api/`: api_overview, simulation_api, state_api, event_api, benchmark_api, examples
* `09_validation/`: validation_strategy, tep_equivalence, determinism, coupling_validation,
  scenario_validation, test_catalog
* `10_operation/`: installation, running, configuration, seeds_and_reproducibility, logging, exports,
  troubleshooting
* `11_limitations/`: known_limitations, modeling_assumptions, tep_limitations, coupling_limitations,
  benchmark_limitations, future_extensions, documentation_gaps, documentation_vs_implementation
* `12_developer_guide/`: adding_equipment, adding_utilities, adding_faults, adding_scenarios,
  adding_couplings, adding_events, testing_changes

**Documentation tooling (`scripts/`; does not affect the simulator):**

* `build_variable_graph.py` + `variable_graph_template.html`: the variable-graph generator and viewer,
  moved into the repository from a development scratch folder and extended with edge semantics.
* `check_docs.py`: link, anchor, path, source-symbol and test-reference checker.

## Files updated

| File | Change |
|---|---|
| `README.md` | corrected D1-D4 (ground truth, 2 h reproducibility test, Python version, untested Docker); demo recovery described as incomplete at 3 h; docs links point to the new tree |
| `docs/LEARNING_GUIDE.md` | rewritten after the documentation pass into 17 progressive levels + "20 things" + "10 easy to misunderstand"; re-verified against code |
| `scripts/generate_docs_tables.py` | rewritten: evidence-based generator (instrumented demo run) for 9 reference files |

## Systems documented

TEP integration (sources, build, adapter, both backends, fidelity details); native control (loops,
modes, wind-up); shutdown; ISA-95 model (87 elements); equipment, instrumentation, utilities,
materials, inventory, warehouse, quality, production, scheduling, maintenance, alarms, operator
modules; coupling (67 relations, 17 boundary parameters, contract); canonical state and variable
ownership; events (46 types); causal registry; fault engine (34 types); scenarios; variable graph (195
nodes, 258 edges, semantic taxonomy); API (operational and benchmark); UI; exports; logging;
configuration; reproducibility; validation; limitations; extension.

## Source areas inspected

All Python under `simulator/` (tep, isa95, enterprise, equipment, utilities, materials, inventory,
warehouse, quality, production, scheduling, maintenance, alarms, faults, coupling, events, operator,
simulation, state, scenarios, common), `api/`, `run.py`, `scripts/`; the Fortran sources and their
READMEs; all 12 configuration files; the 3 scenarios; `ui/` (HTML, CSS, 5 JS modules); `Dockerfile`,
`requirements.txt`, `pytest.ini`; all pre-existing docs.

**Evidence runs made for this pass** (no code changes):
1. full SCN-COOL-001 with instrumented property writes (ownership table, demo trace, event catalog);
2. SCN-COOL-001 with maintenance blocked (the trip at 01:54:09);
3. SCN-FAULT-LIBRARY to 8 h without faults (cooling-tower planned maintenance effect);
4. demo trend analysis (wind-up minimum, undershoot, incomplete recovery);
5. work-order and sample timing;
6. PO origin;
7. execution of the documented API examples;
8. lock-usage scan of `SimulatorService`.

## Tests inspected

All 5 test files, 83 tests. Run in this pass: **83 passed**, about 55 s. The catalog of what each test
proves and does not prove is [test_catalog.md](09_validation/test_catalog.md).

## Link and reference check

`python scripts/check_docs.py` checks relative links and anchors, repository paths in backticks,
source-symbol references (symbol defined in the named file) and test references. It was negatively
tested with a probe file, where it reported a bad symbol, a broken link, an unknown test and a missing
path. Results on the final tree are in the last section.

## Unresolved questions

1. Should the operational API stop exposing ground truth (G1–G12)? This is a behaviour change and
   needs an owner decision.
2. Should the `*_deviation` storage properties be tagged `unobservable` (D1 in documentation vs
   implementation)? The intent says yes; it is a behaviour change.
3. Are the quality limits and the demo's near-threshold severity the intended benchmark defaults?
4. Minimum supported Python version; Linux/macOS builds; the Dockerfile (all untested).

## Implementation/documentation discrepancies

The pass found 19 discrepancies, plus 6 more while the learning guide was rewritten. Most were
resolved by correcting the documentation (README ground-truth claim, reproducibility test length,
Python and Docker status, lock usage, incomplete demo recovery) or in the cleanup pass below. Four
remain open, in [documentation_vs_implementation.md](11_limitations/documentation_vs_implementation.md).

## Known documentation gaps

[documentation_gaps.md](11_limitations/documentation_gaps.md):
* 11 unverified behaviours;
* 7 ambiguous semantics;
* descriptive configuration keys that no logic reads;
* naming inconsistencies;
* 17 code paths not covered by tests;
* 4 assumptions needing owner confirmation;
* 2 minor implementation observations (UI event cursor, temp library copies).

## Quality bar review

| Question | Answer and where |
|---|---|
| Could a new engineer understand the simulator without the original developer? | Yes for structure and behaviour: [README](README.md) (mental model and reading order) → [master spec](MASTER_SIMULATOR_SPEC.md) → the per-area pages with Source lists. Remaining unknowns are listed as gaps, not guessed. |
| Could they trace a fault from cause to TEP to enterprise consequence? | Yes: [worked demo](06_scenarios/scenario_examples/SCN-COOL-001.md) (step table with subsystem, variable, event, edge, TEP variable, consequence), [causal chains](06_scenarios/causal_chains.md), [equipment_to_tep](04_coupling/equipment_to_tep.md), and the variable-graph viewer. |
| Could they determine exactly which variables are authoritative? | Yes: [canonical state](05_state_and_events/canonical_state.md) (authority per kind of state) and the [variable reference](05_state_and_events/variable_reference.md), with *observed* writers. |
| Could they understand why TEP measurements are never overwritten? | Yes: [coupling contract](04_coupling/coupling_contract.md) (every write into Fortran memory enumerated, plus the rationale) and [design principles](01_overview/design_principles.md). |
| Could they reproduce a run? | Yes, on the same library: [seeds and reproducibility](10_operation/seeds_and_reproducibility.md) (factor table and procedure). Limits are stated (cross-build, numpy version, interactive commands). |
| Could they add a new equipment fault without guessing? | Yes: [adding faults](12_developer_guide/adding_faults.md) (channel → type → reader → repair → tests) and [adding equipment](12_developer_guide/adding_equipment.md). |
| Could they tell process physics, enterprise logic, control behaviour and benchmark ground truth apart? | Yes: the truth-label taxonomy in [state model](05_state_and_events/state_model.md); the authority columns in [node types](07_variable_graph/node_types.md) and [edge types](07_variable_graph/edge_types.md); [native control](03_tep/native_control.md); [benchmark limitations](11_limitations/benchmark_limitations.md). |

## Recommended next documentation work

1. After any owner decision on G1–G12, document the observable view as its own contract, like the
   TEP boundary contract.
2. Run and document the untested fault paths (U9, U10): steam, power and cooling-tower faults on
   quality; lot-composition faults.
3. Add a sensitivity page for the demo: severity sweep × response time × seed.
4. Build the Dockerfile and document Linux results; test other Python versions.
5. Add tests for the coupling paths listed as untested, and link them from boundary_variables.
6. Run `scripts/generate_docs_tables.py` and `scripts/check_docs.py` in CI so the generated tables and
   links never go stale.

## Cleanup pass

A later cleanup removed outdated pieces. The simulated behaviour is unchanged: the full 3 h demo
produces the same 148 events, the same 166 trend series and the same final TEP states as before. Only
the `run_id` changed, because the configuration and scenario hashes changed.

| Area | Removed or corrected |
|---|---|
| Docs | the seven "moved" stub pages left at the top of `docs/` (architecture, coupling, faults, tep_integration, tep_variables, isa95_and_enterprise, api_scenarios_ui); resolved entries in documentation vs implementation (now four open items); the draft-history appendices of the learning guide |
| Dead code | `EventType.SENSOR_STATE_CHANGED` (never published; 46 event types remain); the `quality_deviation` fault channel (never written); unused helpers `require`, `as_list`, `FaultEffects.faults_on`, `MaterialsModule.consumption_rate_kg_h` / `nominal_attributes` / `specification` / `lots_at`, `ScenarioStore.load_file`, `SimulationEngine.module_summaries`, `LoopDefinition.is_cascade_master`; unused imports in 12 files |
| Configuration | `pm_interval_h` (never read; implied preventive maintenance that does not exist) |
| Stale text | `configs/equipment.yaml` and `configs/materials.yaml` headers; `InventoryModule` docstring; code references to deleted docs; `run.py` docstring (the simulation loads READY and needs Start); the SCN-COOL-001 description (said the process recovers; it is still below setpoint at 3 h) |
| Generated files | reference tables, demo trace and variable graph regenerated |

## Final check results

After the cleanup pass:

* `python -m pytest`: **83 passed**, 1 warning (a starlette/anyio deprecation), 55.2 s.
* `python scripts/check_docs.py`: 99 files; all links, anchors, paths, source symbols and test
  references resolve; **0 problems**.
* `pyflakes` over `simulator/`, `api/`, `scripts/`, `tests/` and `run.py` (excluding the vendored
  Python backend): no warnings.
