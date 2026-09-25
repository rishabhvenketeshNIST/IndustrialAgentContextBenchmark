# Coupling validation

## Validated

| Property | Test |
|---|---|
| Healthy boundary values equal TEINIT exactly | `test_coupling_baseline_is_native_and_graph_is_acyclic`, `test_healthy_enterprise_reproduces_native_tep_exactly` |
| Relations run in dependency order | `test_coupling_baseline_is_native_and_graph_is_acyclic` |
| Pump degradation → VRNG(10) = 1000 × capacity fraction → controller response | `test_equipment_degradation_propagates_through_coupling_to_tep` |
| Pump failure → condenser capacity 0 → changeover restores 1.0 | `test_failure_changeover_and_maintenance_recovery` |
| Power loss → MCC supply, CPFLMX | `test_power_loss_cascades_to_pumps_and_compressor` |
| Stock loss → supply availability 0 → VRNG(1) = 0 | `test_order_blocked_by_material_shortage` |
| IDV path does not touch boundaries | `test_tep_native_fault_only_toggles_idv` |
| Sensor layer does not touch physics | `test_measurement_filter_does_not_touch_true_process` |
| Start-up rejects cycles, double writers, unknown references | enforced in code (`_toposort`, `Ref.parse`, `_validate_entities`); **no negative test** |

## Not validated by tests (implemented, observed, untested)

* **SZERO(5)/(6) supply-temperature coupling.** Observed in the planned cooling-tower maintenance
  ([maintenance_to_tep](../04_coupling/maintenance_to_tep.md)).
* SZERO(1)/(2) and the XST lot-composition couplings.
* VRNG(9) steam, and VRNG(2), (3), (4) feed availability.
* The power shed logic arithmetic.
* The causal-label propagation rules beyond the demo chain.

**Recommendation:** add one assertion per boundary parameter that a single upstream change moves it
by the expected amount.

Source:
- `tests/test_enterprise.py`
- `simulator/coupling/__init__.py` — `CouplingEngine._toposort`, `Ref.parse`
