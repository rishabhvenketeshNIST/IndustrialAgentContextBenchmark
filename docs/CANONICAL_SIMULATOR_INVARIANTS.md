# Canonical simulator invariants

Properties that must remain true for the simulator to mean what the
[canonical contract](CANONICAL_SIMULATOR_CONTRACT.md) says it means. Each invariant lists why it
matters, the implementation evidence, the tests that protect it, and what would violate it.

Invariants I1–I12 hold today and are protected by tests. T1–T2 are **target invariants**: the
contract requires them for any agent-facing use, but the current implementation does **not** satisfy
them. They are listed so that nobody assumes otherwise.

| # | Invariant | Status |
|---|---|---|
| I1 | A healthy enterprise reproduces bare TEP exactly | holds, tested |
| I2 | At t = 0 every boundary parameter equals its TEINIT value | holds, tested |
| I3 | Enterprise effects reach TEP only through defined interfaces | holds, tested |
| I4 | Process measurements are never overwritten | holds, tested |
| I5 | The native TEP model and control law are unchanged | holds, tested |
| I6 | Faults write causes, never consequences | holds, partly tested |
| I7 | Same inputs, same run | holds, tested |
| I8 | Reset returns to the initial state | holds, tested |
| I9 | One writer per coupled value; no algebraic cycles | holds, enforced at start-up |
| I10 | Time is integer seconds; event order is deterministic | holds, tested |
| I11 | Fault control and fault events are benchmark-only | holds, tested |
| I12 | Everything produced has contract semantics | holds, tested |
| T1 | Operational interfaces expose no benchmark ground truth | **violated today** (G1–G12) |
| T2 | Model-internal values are not operationally observable | **violated today** |

---

### I1: A healthy enterprise reproduces bare TEP exactly

* **Statement:** with no active faults, the TEP trajectory (every XMEAS every second, and all 50 states)
  is bit-identical to a bare native run (TEINIT plus native controllers) with the same TEP seed.
* **Why it matters:** proves that the enterprise layer adds nothing to the physics unless a cause is
  present, so any deviation in a fault scenario is attributable to that cause.
* **Evidence:** design margins (e.g. pumps rated 1100 against a 1000 design flow) and `min(…, 1)` clips
  keep every boundary value at its TEINIT value; `ProcessInterface.set_boundary` writes only on change.
* **Test:** `tests/test_reproducibility.py::test_healthy_enterprise_reproduces_native_tep_exactly` (1 h,
  no production orders).
* **Violation:** any difference in XMEAS or states between the engine and a bare adapter in a
  fault-free run; for example a new relation whose healthy value is not exactly the TEINIT value.

### I2: At t = 0 every boundary parameter equals its TEINIT value

* **Statement:** the coupling engine's baseline evaluation reproduces the TEINIT boundary values.
* **Why:** the plant starts at the Downs & Vogel base case.
* **Evidence:** `ProcessInterface.initialize` records TEINIT values as nominal; transformations are
  `nominal × fraction` or `nominal + deviation`.
* **Tests:** `test_coupling_baseline_is_native_and_graph_is_acyclic`,
  `test_boundary_parameters_equal_teinit`, `test_initial_state_matches_downs_vogel_base_case`.
* **Violation:** a boundary value at t = 0 that differs from TEINIT.

### I3: Enterprise effects reach TEP only through defined interfaces

* **Statement:** the enterprise layer changes TEP only through:
  * the 15 documented boundary couplings;
  * IDV flags (fault engine only);
  * validated operator setpoints and outputs;
  * bumpless `ERROLDn` initialisation;
  * the temporary transmitted-PV swap during `CONTRLn`.
* **Why:** keeps TEP the single source of process physics; makes every enterprise → process path
  auditable.
* **Evidence:** the adapter is reachable only through `ProcessInterface`; only relations of category
  `tep_boundary` have boundary outputs; `Ref.parse` offers no way to write `process.*`.
* **Tests:**
  * `tests/test_contract.py::test_only_tep_boundary_relations_write_into_tep`;
  * `tests/test_contract.py::test_tep_boundary_couplings_match_contract`;
  * `test_equipment_degradation_propagates_through_coupling_to_tep`;
  * `test_tep_native_fault_only_toggles_idv`.
* **Violation:** any code path that writes TEP memory outside
  [the coupling contract list](04_coupling/coupling_contract.md); a relation writing a process
  variable; a new boundary coupling not declared in the contract.

### I4: Process measurements are never overwritten

* **Statement:** `process.xmeas_true` is exactly what TEFUNC computed. Sensor faults change only the
  transmitted form. The `/PV/` swap for the controllers is restored before integration.
* **Why:** the enterprise layer must not manufacture outcomes by rewriting measurements.
* **Evidence:** `BaseTEPAdapter.step` restores the saved XMEAS in a `finally` block; `Instrumentation`
  returns a new array.
* **Tests:** `test_measurement_filter_does_not_touch_true_process`,
  `test_sensor_bias_changes_transmitted_not_true_value`.
* **Violation:** `xmeas_true` differing from a bare TEP run for reasons other than boundary, IDV or
  control inputs; any enterprise write to XMEAS that persists past `CONTRLn`.

### I5: The native TEP model and control law are unchanged

* **Statement:** `teprob.f` and `temain_mod.f` are compiled unmodified. The controller constants
  loaded by the adapter equal those assigned in `temain_mod.f` MAIN. Native saturation, wind-up and
  undershoot are preserved.
* **Why:** the benchmark's credibility rests on the published TEP model and control scheme.
* **Evidence:** source SHA-256 values in every run manifest
  ([provenance](03_tep/tep_version_and_provenance.md)); `simulator/tep/control_scheme.py`.
* **Tests:** `test_native_control_scheme_transcription`,
  `test_closed_loop_holds_base_case_for_two_hours`, `test_manifest_contents`.
* **Violation:** a changed source hash; added clamping or anti-windup; changed gains, reset times,
  periods or execution order.

### I6: Faults write causes, never consequences

* **Statement:** fault types write only cause channels (`fault_effects`), IDV flags (TEP-native types)
  or sensor overlays (sensor types). Every consequence is computed by modules, relations or TEP.
* **Why:** the benchmark controls causes; the simulator determines effects.
* **Evidence:** `FaultEffects.set` rejects unknown channels; fault types in
  `simulator/faults/types.py` call only `fault_effects.set`, `set_disturbance` or the instrumentation.
* **Tests:** `test_tep_native_fault_only_toggles_idv`,
  `test_equipment_degradation_propagates_through_coupling_to_tep`,
  `test_fault_lifecycle_and_validation`. **Gap:** no test asserts that a fault type never writes an
  entity property directly.
* **Violation:** a fault type that sets a property, a boundary value or a measurement.

### I7: Same inputs, same run

* **Statement:** the same scenario, configuration and compiled library give an identical event log
  (ids, times, payloads) and identical trends; a different seed gives a different trajectory.
* **Why:** controlled experiments and reproducible benchmark scoring.
* **Evidence:** [determinism](09_validation/determinism.md) mechanisms; manifest hashes.
* **Tests:** `test_same_scenario_same_seed_identical_results` (2 h of the demo),
  `test_fault_injection_is_deterministic`, `test_fortran_is_deterministic_and_seed_dependent`,
  `test_different_seed_gives_different_trajectory`.
* **Violation:** any difference between two runs with identical inputs on the same library build.

### I8: Reset returns to the initial state

* **Statement:** resetting a simulation yields a snapshot identical to a freshly created one.
* **Test:** `test_reset_returns_to_initial_state`.
* **Violation:** state that survives a reset (a cached value, an un-reset random stream).

### I9: One writer per coupled value; no algebraic cycles

* **Statement:** every relation output has exactly one writing relation, and the relation graph is
  acyclic. Dynamic loops close only through TEP time steps.
* **Evidence:** `CouplingEngine.setup` rejects double writers and cycles (`_toposort`).
* **Test:** `test_coupling_baseline_is_native_and_graph_is_acyclic`.
* **Violation:** start-up accepting a configuration with a cycle or two writers.

### I10: Time is integer seconds; event order is deterministic

* **Statement:** simulation time advances in whole seconds; timestamps derive from
  `simulation_start + t`, never from the wall clock; `EV-` ids increase strictly; lifecycle events use
  `LC-` ids and never shift `EV-` ids.
* **Tests:** `test_clock_is_deterministic`, `test_event_bus_fields_ids_and_causation`,
  `test_same_scenario_same_seed_identical_results`.
* **Violation:** a wall-clock value in simulation data; pause or resume changing `EV-` ids.

### I11: Fault control and fault events are benchmark-only

* **Statement:** fault creation, scheduling, start, stop and reset exist only on `BenchmarkFaultAPI`
  (`/api/benchmark/*`); FAULT_* events have `benchmark` visibility and are excluded from
  `/api/events`.
* **Tests:** `test_fault_injection_is_benchmark_only`,
  `tests/test_contract.py::test_event_types_and_visibility_match_contract`,
  `tests/test_contract.py::test_every_api_route_is_classified`.
* **Violation:** a fault operation on an operational route; a FAULT_* event in `/api/events`.

### I12: Everything produced has contract semantics

* **Statement:** every event type, entity property, record collection, TEP boundary coupling and API
  route that the simulator produces is described in `contract/canonical_contract.yaml`, and vice
  versa.
* **Why:** downstream layers can rely on documented semantics; nothing new appears unclassified.
* **Tests:** `tests/test_contract.py` (all tests).
* **Violation:** a new property, event, collection, coupling or route without a contract entry.

---

### T1: Operational interfaces expose no benchmark ground truth (target, violated today)

* **Statement:** nothing reachable without benchmark authority reveals fault identity, cause
  channels, the causal registry, scenario fault definitions or fault-derived correlation.
* **Status:** violated through leaks G1–G12 ([benchmark limitations](11_limitations/benchmark_limitations.md));
  `contract/canonical_contract.yaml` → `api_routes` lists them per route.
* **Required before any agent-facing layer:** an allow-listed observable view (contract §25).
* **Would be protected by:** a negative test over the observable view asserting the absence of every
  benchmark-class field.

### T2: Model-internal values are not operationally observable (target, violated today)

* **Statement:** properties with `observability: model_internal` (health, capability, availability,
  lot composition) are not served to a system under test.
* **Status:** `model_internal` and `unobservable` tags exist but nothing filters on them; the
  `*_deviation` properties are not even tagged (ambiguity A1).
* **Would be protected by:** the same observable-view test as T1.

Source:
- `tests/test_contract.py`
- `tests/test_reproducibility.py` — `test_healthy_enterprise_reproduces_native_tep_exactly`
- `simulator/tep/interface.py` — `BaseTEPAdapter.step`
- `simulator/coupling/__init__.py` — `CouplingEngine.setup`, `Ref.parse`
- `simulator/faults/effects.py` — `FaultEffects.set`
