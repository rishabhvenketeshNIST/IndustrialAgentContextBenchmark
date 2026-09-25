# Read this first

This documentation describes the enterprise manufacturing simulator **as implemented**. Where the
implementation and earlier text disagreed, the implementation won, and the difference is recorded in
[documentation vs implementation](11_limitations/documentation_vs_implementation.md).

Main specification: **[MASTER_SIMULATOR_SPEC.md](MASTER_SIMULATOR_SPEC.md)**. Exact semantics of
everything the simulator produces: **[CANONICAL_SIMULATOR_CONTRACT.md](CANONICAL_SIMULATOR_CONTRACT.md)**
(with its [invariants](CANONICAL_SIMULATOR_INVARIANTS.md)). A tutorial-style walk through 17 levels:
[LEARNING_GUIDE.md](LEARNING_GUIDE.md).

Documentation hierarchy, from overview to authority on semantics:

```
README.md → MASTER_SIMULATOR_SPEC → CANONICAL_SIMULATOR_CONTRACT → subsystem and reference pages → source code and tests
```

The implementation is authoritative for behaviour; the contract is authoritative for documented
meaning. Where the two disagree, the disagreement is a recorded defect or ambiguity
([contract audit](CANONICAL_CONTRACT_AUDIT.md)), never a silent reinterpretation.

## The 10-minute mental model

1. **There is one simulated site** (ACME Manufacturing, Tennessee Eastman Manufacturing Site). Its
   chemical process is the **Tennessee Eastman Process**, computed by the **original Fortran code**,
   unmodified. Python never computes a temperature, pressure, level or composition.
2. **Around the process, Python simulates the rest of the plant.** That covers pumps and other
   equipment with a health that wears; cooling water, steam and power; feed tanks and raw-material
   lots; a warehouse; quality control; production orders and lots; maintenance with technicians and
   spare parts; alarms; and operator actions.
3. **Everything happens in 1-second steps, always in the same order.** Enterprise modules update, then
   the **coupling engine** evaluates a declarative cause-and-effect model (`configs/coupling.yaml`),
   then TEP integrates one second with its **native controllers**, then enterprise modules observe the
   result.
4. **The enterprise layer can influence TEP only by changing named "boundary parameters"** (17 defined,
   15 driven by coupling relations). These
   are physical inputs of the Fortran model, such as the cooling-water line's capacity `VRNG(10)`, the
   compressor limit `CPFLMX`, supply-temperature means and feed compositions. It never overwrites a
   measurement. With a healthy plant these parameters equal their original values, and the simulation
   is bit-identical to bare TEP.
5. **A benchmark operator injects faults.** A fault only writes a *cause*, for example "pump P-101A
   has damage 0.35". The simulator works out the consequences: health falls, the pump delivers less
   flow, cooling capacity drops, `VRNG(10)` falls, TEP's reactor gets less cooling, the native
   controller opens the valve until it saturates, the reactor heats up, alarms fire, maintenance sends
   a technician, the standby pump starts, and the process begins to recover, with the original controller's
   wind-up undershoot, and is still below setpoint at 3 h. Product quality and lot disposition
   reflect it.
6. **Everything is recorded as events** (46 types, ordered, deterministic ids) and is visible through a
   REST API and a web UI. The in-memory **canonical state** is the source of truth.
7. **It is reproducible.** The same library, code, configuration, scenario and seed give the identical
   event log and trajectory.
8. **Ground truth is not yet fully separated from what an operator or agent should see.** Fault control
   is benchmark-only, but causes can be inferred from several operational routes.

## Reading order

| # | Topic | Start with |
|---|---|---|
| 1 | Simulator overview | [01_overview/simulator_overview.md](01_overview/simulator_overview.md), [goals and scope](01_overview/goals_and_scope.md), [design principles](01_overview/design_principles.md) |
| 2 | Architecture | [01_overview/architecture.md](01_overview/architecture.md), [terminology](01_overview/terminology.md) |
| 3 | Manufacturing model | [ISA-95 model](02_manufacturing_model/isa95_model.md), then [equipment](02_manufacturing_model/equipment.md), [utilities](02_manufacturing_model/utilities.md), [materials](02_manufacturing_model/materials_and_storage.md), [production](02_manufacturing_model/production.md), [maintenance](02_manufacturing_model/maintenance.md), [quality](02_manufacturing_model/quality.md) |
| 4 | TEP integration | [TEP overview](03_tep/tep_overview.md), [provenance](03_tep/tep_version_and_provenance.md), [integration](03_tep/tep_integration.md), [native control](03_tep/native_control.md), [shutdown](03_tep/safety_and_shutdown.md) |
| 5 | Coupling | [coupling architecture](04_coupling/coupling_architecture.md), **[the TEP boundary contract](04_coupling/coupling_contract.md)**, [boundary variables](04_coupling/boundary_variables.md) |
| 6 | State and events | **[canonical contract](CANONICAL_SIMULATOR_CONTRACT.md)**, [canonical state](05_state_and_events/canonical_state.md), [event model](05_state_and_events/event_model.md), [variable reference](05_state_and_events/variable_reference.md) |
| 7 | Variable graph | [graph model](07_variable_graph/graph_model.md), [edge types](07_variable_graph/edge_types.md) |
| 8 | Faults and scenarios | [fault injection](06_scenarios/fault_injection.md), [taxonomy](06_scenarios/fault_taxonomy.md), [causal chains](06_scenarios/causal_chains.md) |
| 9 | Worked demo | **[SCN-COOL-001 step by step](06_scenarios/scenario_examples/SCN-COOL-001.md)** |
| 10 | Validation | [strategy](09_validation/validation_strategy.md), [test catalog](09_validation/test_catalog.md), [determinism](09_validation/determinism.md) |
| 11 | Limitations | [known limitations](11_limitations/known_limitations.md), **[benchmark limitations](11_limitations/benchmark_limitations.md)**, [gaps](11_limitations/documentation_gaps.md) |
| 12 | Developer guide | [12_developer_guide/](12_developer_guide/adding_faults.md) |

Operation: [installation](10_operation/installation.md) · [running](10_operation/running.md) ·
[configuration](10_operation/configuration.md) · [seeds and reproducibility](10_operation/seeds_and_reproducibility.md) ·
[exports](10_operation/exports.md) · [troubleshooting](10_operation/troubleshooting.md).

## Generated files (do not edit by hand)

Produced from the implementation by `scripts/generate_docs_tables.py`, which runs the demo with
instrumented state, and by `scripts/build_variable_graph.py`:

* reference tables: [measurements](03_tep/measurements.md), [manipulated variables](03_tep/manipulated_variables.md),
  [disturbances](03_tep/disturbances.md), [native control loops](03_tep/native_control_loops.md),
  [coupling relations](04_coupling/coupling_relations.md), [variable reference](05_state_and_events/variable_reference.md),
  [event catalog](05_state_and_events/event_catalog.md), [ISA-95 entity table](02_manufacturing_model/isa95_entity_table.md);
* the demo trace: [SCN-COOL-001 trace](06_scenarios/scenario_examples/SCN-COOL-001_trace.md);
* the variable graph: `07_variable_graph/variable_graph.json`, `07_variable_graph/variable_graph.html`.

`scripts/check_docs.py` verifies that every relative link and every `path` · `Symbol` source reference
in these docs resolves.

## Source-reference convention

Pages end with a **Source** list: `` `path` — `Class.method`, `function` ``. Each path exists, and each
symbol is defined in that file (checked by `scripts/check_docs.py`). Tests are referenced the same way.

Audit of this documentation pass: [DOCUMENTATION_AUDIT.md](DOCUMENTATION_AUDIT.md).
