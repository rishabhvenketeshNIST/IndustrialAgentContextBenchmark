# Documentation vs implementation

Open places where the stated intent (documentation, comments, scenario text, design notes) and the
implementation still differ. The implementation is the source of truth. Items that were resolved,
by correcting the documentation or by removing dead code, have been removed from this list. The
[documentation audit](../DOCUMENTATION_AUDIT.md) records what was fixed.

| # | Intent says | Implementation does | Evidence | Status |
|---|---|---|---|---|
| D1 | The raw-material `*_deviation` storage properties should be tagged `unobservable`, like the other lot-composition attributes | they are not tagged, so `/api/inventory` and the UI show them; they reveal an active `raw_material_quality_deviation` fault | `InventoryModule.setup` | behaviour change: owner decision (leak G5 in [benchmark limitations](benchmark_limitations.md)) |
| D2 | `CanonicalState.snapshot(include_truth=False)` suggests a truth-free view is available | no route or caller uses `include_truth=False` | `simulator/state/__init__.py`, `api/service.py` | documented; an observable view is future work ([future extensions](future_extensions.md)) |
| D3 | Consequence events carry the fault's correlation id | most do, but alarms on entities without a causal-registry entry do not. In the demo, QUALITY_RESULT_CREATED (QS-00009) carries `F-COOL-001`, while the QA-OFFSPEC alarm raised in the same second, from `WU-QC-LAB.last_result_fail`, has its own id | `AlarmModule._cause_keys`; [demo trace](../06_scenarios/scenario_examples/SCN-COOL-001_trace.md) | documented; labels are heuristic ([causal semantics](../07_variable_graph/causal_semantics.md)) |
| D4 | The instrumentation layer supports `stuck` sensors | `Instrumentation` implements the overlay, but no fault type creates it | `simulator/equipment/instrumentation.py`, `simulator/faults/types.py` | documented (L18 in [known limitations](known_limitations.md)) |

Source:
- `simulator/inventory/__init__.py` — `InventoryModule.setup`
- `simulator/state/__init__.py` — `CanonicalState.snapshot`
- `simulator/alarms/__init__.py` — `AlarmModule._cause_keys`
- `simulator/equipment/instrumentation.py` — `SensorOverlay`, `Instrumentation`
