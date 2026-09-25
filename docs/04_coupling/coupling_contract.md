# The TEP boundary contract

This is the most important architectural boundary in the simulator. Every write into Fortran memory
made by Python is listed here, with its code path. Anything not listed does not happen. This page is
the implementation detail behind [canonical contract §17](../CANONICAL_SIMULATOR_CONTRACT.md#17-tep-boundary).

## What can enter TEP?

| # | What | Fortran target | Writer (only code path) | When | Test |
|---|---|---|---|---|---|
| 1 | Initialisation | all common blocks zeroed; TEINIT; `/RANDSD/ G`; `/CTRLALL/`, `/CTRLn/`, `/FLAG6/`, initial XMV | `BaseTEPAdapter.initialize` → `_native_init`, `_set_seed`, `_load_native_controller_configuration` | run start / reset | `test_initial_state_matches_downs_vogel_base_case` |
| 2 | **Boundary parameters** (17) | `/TEPROC/ VRNG(1,2,3,4,9,10,11)`, `CPFLMX`, `XST(2,1),(4,1),(6,2),(5,2),(2,3),(1,3)`; `/WLK/ SZERO(1..6)` | `CouplingEngine.evaluate` → `ProcessInterface.set_boundary` → `set_boundary_parameter` | pre-step, only when the value changes | `test_equipment_degradation_propagates_through_coupling_to_tep`, `test_coupling_baseline_is_native_and_graph_is_acyclic` |
| 3 | TEP-native disturbances | `/DVEC/ IDV(1..20)` | `FaultEngine` → `_IDVFault.apply` → `ProcessInterface.set_disturbance` | pre-step while the fault is active | `test_tep_native_fault_only_toggles_idv` |
| 4 | Operator setpoint | `/CTRLALL/ SETPT(k)` | `ProcessInterface.set_setpoint` (refused for CAS loops) | on command | `test_setpoint_measurement_and_output_are_distinct` |
| 5 | Operator manual output | `/PV/ XMV(n)` | `ProcessInterface.set_manipulated_variable` (refused while the owning loop runs) | on command | `test_manual_mode_freezes_controller_outputs` |
| 6 | Loop / plant mode | no Fortran write, except `ERROLDn` on MAN→AUTO (bumpless) | `BaseTEPAdapter.set_loop_mode`, `set_control_mode` | on command | same |
| 7 | Transmitted measurements for the controllers | `/PV/ XMEAS` **temporarily**, restored right after the CONTRLn calls | `BaseTEPAdapter.step(measurement_filter=Instrumentation.filter)` | each controller execution | `test_measurement_filter_does_not_touch_true_process`, `test_sensor_bias_changes_transmitted_not_true_value` |

## What can leave TEP?

| What | Read by | Notes |
|---|---|---|
| XMEAS(1..41) (true) | `ProcessInterface.sync` → `process.xmeas_true`; filtered → `process.xmeas`; copied to equipment properties | every step |
| XMV(1..12) | `process.xmv`, control-module properties | every step |
| SETPT, loop outputs and modes | `process.setpoints`, `process.loops` | every step |
| IDV flags | `process.idv` | every step |
| Shutdown condition | `/TEPROC/ PTR, TCR, VLR, VLS, VLC` via `_internals` | every step |
| Boundary values (after clamping) | `get_boundary_parameters` | on write |
| 50 states | `get_states`: diagnostic API only (`/api/benchmark/process/internal-states`) and the reproducibility tests | on request |
| TEP TIME | `get_time` → `process.tep_time_h` | every step |

## What cannot be directly modified?

* **XMEAS**: never written except item 7, a temporary swap that is always restored. No fault, module
  or relation writes a measurement.
* **The 50 states YY**: never written after TEINIT.
* **Physics constants** (`/CONST/`), reaction parameters, heat-transfer coefficients, vessel volumes and
  noise levels: never written.
* **Controller gains and reset times** after initialisation: never written.
* **XMV by the enterprise layer or faults**: never. Only an operator can, and only in MAN.

## "Enterprise faults do not overwrite TEP measurements": how this is guaranteed

1. Fault types can only write cause channels, instrumentation overlays or IDV (`simulator/faults/types.py`).
2. Cause channels are read by modules and relations whose TEP targets are only boundary parameters.
3. A sensor fault changes the *transmitted* copy. TEP's own XMEAS array is restored after each
   controller call. The test `test_sensor_bias_changes_transmitted_not_true_value` shows `xmeas −
   xmeas_true = bias`, and that after 30 min the **true** CW outlet temperature has moved through
   closed-loop action.
4. With no faults, the trajectory is bit-identical to a bare native run
   (`test_healthy_enterprise_reproduces_native_tep_exactly`), so the layer adds nothing when healthy.

## Why measurements are never overwritten

Overwriting XMEAS(9) with "the temperature it would have had" would bypass the process physics. The
consequence would no longer follow from the cause, other measurements (pressure, composition) would
be inconsistent with it, the native controllers would respond to a fiction, and the benchmark ground
truth would stop being physically coherent. Changing a *boundary condition* lets TEP compute a
self-consistent response.

Source:
- `simulator/simulation/process_interface.py` — `ProcessInterface.set_boundary`, `ProcessInterface.set_disturbance`, `ProcessInterface.set_setpoint`, `ProcessInterface.set_manipulated_variable`, `ProcessInterface.sync`
- `simulator/tep/interface.py` — `BaseTEPAdapter.step`, `BaseTEPAdapter.set_boundary_parameter`, `BaseTEPAdapter.initialize`
- `simulator/tep/fortran_backend.py` — `FortranTEPAdapter._set_boundary`, `FortranTEPAdapter._internals`
- `simulator/tep/boundary.py` — `BOUNDARY_PARAMETERS`
