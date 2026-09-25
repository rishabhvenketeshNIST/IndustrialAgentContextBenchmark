# Design principles

Each principle is stated with the mechanism that enforces it in the code and the test that checks it.
Where enforcement is partial, the page says so.

## 1. TEP is the only source of process physics

Python computes no reactor temperature, pressure, level, flow or composition.
**Mechanism:** `teprob.f` and `temain_mod.f` are compiled unmodified (`scripts/build_fortran.py`) and
called through ctypes (`FortranTEPAdapter`). **Test:** `test_healthy_enterprise_reproduces_native_tep_exactly`
compares the full simulator (no faults) with a bare native run for one hour, bit for bit.

## 2. The enterprise layer changes TEP only through boundary parameters

**Mechanism:** the only enterprise code path that writes TEP memory is
`ProcessInterface.set_boundary`, called by `CouplingEngine.evaluate` for relations whose output is
`tep.boundary.*`. The other writes to TEP are:

* operator control actions (setpoint, loop mode, plant mode, manual XMV), through `ProcessInterface`;
* TEP-native faults, which toggle IDV through `ProcessInterface.set_disturbance`;
* the sensor layer, which swaps transmitted values into `/PV/` only while the native controllers
  execute and then restores them (`BaseTEPAdapter.step`).

**Tests:** `test_tep_native_fault_only_toggles_idv`, `test_measurement_filter_does_not_touch_true_process`,
`test_equipment_degradation_propagates_through_coupling_to_tep`.

## 3. Faults define causes; the simulator determines consequences

The fault engine writes only to *cause channels* (`FaultEffects`) or to instrumentation overlays. The
module that owns the target decides what happens. **Mechanism:** each fault type's `apply` method in
`simulator/faults/types.py`. **Test:** `test_fault_lifecycle_and_validation`.

## 4. Causal relationships are data, not scattered code

Cross-entity influences live in `configs/coupling.yaml`; the engine sorts and evaluates them.
**Partial:** some influences are module logic, not relations. Examples are health → status → is_running
in `EquipmentModule`, stock → supply availability in `InventoryModule`, and alarm → work order in
`MaintenanceModule`. These are documented in [coupling architecture](../04_coupling/coupling_architecture.md).

## 5. Modules do not call each other

Modules share the canonical state and the synchronous event bus. Operator commands go through a
command registry. **Mechanism:** `SimulationModule` receives only a `ModuleContext`. **Partial:**
`SimulationEngine._register_commands` wires operator commands to specific module methods, for example
`EquipmentModule.command`. That is the one place with direct module references.

## 6. Deterministic by construction

There is an integer-second clock, a fixed module order, a synchronous event bus, one seeded random
stream per module, and timestamps derived from simulation time. **Tests:**
`test_same_scenario_same_seed_identical_results`, `test_fault_injection_is_deterministic`
([determinism](../09_validation/determinism.md)).

## 7. Preserve native behaviour, including its flaws

The native controllers have no anti-windup and they wind up when a valve saturates. This is kept. The
only enterprise-layer addition to controller state is bumpless transfer on MAN→AUTO
(`BaseTEPAdapter._bumpless_init`), which is documented.

## 8. A healthy plant must be native

Design margins make a healthy enterprise reproduce the exact TEINIT boundary values:

* CW pumps are rated 1100 against a 1000 design;
* the compressor has a 10 % margin;
* the cooling tower has a 0.9 performance threshold;
* lot couplings add a *deviation* to the TEINIT value.

**Test:** `test_coupling_baseline_is_native_and_graph_is_acyclic`.

## 9. Benchmark information is separated from operational information

**Partial.** Fault control and fault events are strictly separated (`/api/benchmark/*`,
`Visibility.BENCHMARK`). Other ground truth is labelled but still returned by operational routes. See
[benchmark limitations](../11_limitations/benchmark_limitations.md).
