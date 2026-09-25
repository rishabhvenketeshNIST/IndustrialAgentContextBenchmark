# Causal chains

The main cause-to-consequence chains the implementation supports. Each is
**cause → enterprise state → (coupling) → TEP → observation → enterprise consequence**. Each row names
the boundary parameter, if any, and where the chain ends. The worked end-to-end example is
[SCN-COOL-001](scenario_examples/SCN-COOL-001.md).

| # | Cause (fault type → target) | Enterprise state | Boundary | TEP response | Observable signs | Enterprise consequence | Test |
|---|---|---|---|---|---|---|---|
| 1 | degradation / cooling → CW pump | health, vibration, efficiency, capacity fraction | VRNG(10) or (11) | CW flow ↓; valve opens; saturation → reactor T or condenser/product flow | vibration alarm, utility DEGRADED/CONSTRAINED, saturation alarm, TAH-09 | work orders, standby changeover, quality failure after wind-up | `test_demo_scenario_causal_chain` |
| 2 | pump_efficiency_loss → CW pump | efficiency only (hidden) | VRNG(10/11) | same as 1 | **no vibration**; utility status; capacity alarm | inspection finds the pump (eff < 0.85) → corrective WO | — (no test) |
| 3 | equipment_failure → duty pump | FAILED, is_running 0 | VRNG = 0 for ≈ 30 s | brief cooling loss | status alarm (P-101A/P-201A), EQUIPMENT_FAILED | auto-changeover, P1 WO, repair to STANDBY | `test_failure_changeover_and_maintenance_recovery` |
| 4 | cooling_degradation → cooling tower | thermal_degradation → supply temperature rise | SZERO(5), SZERO(6) | CW inlet temperature rises at the next walk knot; valves open more | UA-CWRX-T alarm (> 38 °C), XMEAS 21/22 | usually absorbed by control | — |
| 5 | utility_capacity_loss → UT-STEAM | steam capacity fraction | VRNG(9) | stripper heat ↓ → stripper T, product E | FAL-19, TAL-18, product E | quality risk | — |
| 6 | utility_capacity_loss → UT-POWER | process supply fraction, MCC supply | VRNG(10/11), VRNG(9), SZERO(5/6), CPFLMX | cooling, steam and compressor all derated | many | broad upset, possible trip | `test_power_loss_cascades_to_pumps_and_compressor` |
| 7 | equipment_degradation → compressor | efficiency → capability | CPFLMX | recycle flow ↓, pressure ↑ | FI-05, PAH-07 | vibration WO | — |
| 8 | raw_material_shortage → material | stock written off → supply availability | VRNG(1-4) | feed lost at any valve opening | MATERIAL_SHORTAGE, storage alarm, feed flow ↓ | orders BLOCKED; reorder delayed while disrupted | `test_order_blocked_by_material_shortage` |
| 9 | raw_material_quality_deviation → material | lot composition deviation | XST or SZERO(1,2) | inert/impurity accumulates; purge and composition loops react | XMEAS 24, 29-36; purge flow | incoming inspection REJECTS new lots | — |
| 10 | sensor bias / drift / dropout → XMEAS | instrumentation overlay | none | **true** process moves through closed-loop action on a false PV | transmitted value, BAD quality, BADPV alarm | alarms, quality, production computed on the false value | `test_sensor_bias_changes_transmitted_not_true_value` |
| 11 | IDV k | IDV flag | IDV (native input) | native disturbance | process measurements | depends | `test_tep_native_fault_only_toggles_idv` |
| 12 | maintenance_delay / spare_part_shortage | work-order timing | none directly | lets chains 1-7 run longer | WO waiting reasons | recovery later, trip more likely | `test_spare_part_shortage_blocks_work_order` |
| 13 | production_order_delay / quality_failure | order release / reported test value | none | none | order status / FAIL results | late or blocked orders; false quarantines and rejections | `test_quality_failure_fault_rejects_lot` |

Rows without a test are implemented paths whose end-to-end behaviour is **not covered by an automated
test** ([documentation gaps](../11_limitations/documentation_gaps.md)).

## Chain properties you should keep in mind

* **Absorption.** The native control scheme absorbs moderate constraints. Enterprise faults below a
  threshold show up only as changed valve positions (XMV). In the demo, CW capacity losses up to ≈ 58 %
  are absorbed and ≈ 62 % trips without intervention (probing during development). The threshold is
  sharp.
* **Delay.** SZERO couplings act at the next walk knot. Analyzer-based consequences lag by 0.1–0.25 h
  of dead time.
* **Hidden vs observable causes.** Health-based faults produce vibration; efficiency-loss faults do not.
* **Correlation labels** propagate along relations only when the output deviates by > 0.5 % from its
  baseline. A fault fully absorbed below that threshold labels nothing downstream.

Source:
- `configs/coupling.yaml`
- `simulator/faults/types.py`
- `simulator/coupling/__init__.py` — `CouplingEngine._propagate_cause`
