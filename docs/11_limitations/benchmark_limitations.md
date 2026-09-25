# Benchmark limitations: ground truth vs observable state

The benchmark relies on this principle: *ground truth must not accidentally become operational or
agent-visible information.* The current implementation guarantees part of it.

## Enforced today

* **Fault control** exists only on `BenchmarkFaultAPI` and `/api/benchmark/*`
  (`test_fault_injection_is_benchmark_only`).
* **FAULT_* events** are excluded from `/api/events` and the UI event list.
* **Sensor faults** change only transmitted values. Operational measurement routes return transmitted
  values.

## Leaks (non-benchmark routes that expose truth)

| # | Route / field | What leaks | Severity |
|---|---|---|---|
| G1 | `/api/events` → `correlation_id`, `causation_id` | the fault id as root cause of alarms, utility, equipment, maintenance and quality events | **high**: this is the answer to a diagnosis task |
| G2 | `/api/entities/{id}`, `/api/equipment/{id}`, `/api/maintenance`, `/api/ui/snapshot` → `health`, `efficiency`, `available_flow`, `capability` | true equipment condition (a hidden `pump_efficiency_loss` is visible as reduced efficiency) | **high** |
| G3 | `/api/process/image`, `/api/coupling`, `/api/ui/snapshot` → `boundary` | reduced VRNG(10), CPFLMX, … reveal the affected subsystem | **high** |
| G4 | `/api/history` → `TRUE:XMEAS(n)` | the difference from transmitted values reveals a sensor fault | **high** for sensor faults |
| G5 | `/api/inventory`, `/api/entities/SU-*` → `*_deviation` | raw-material quality deviation | **high** for that fault; the deviations were meant to be tagged `unobservable` but are not |
| G6 | `/api/export/json`, `/api/export/csv` | everything, including FAULT_* events and fault records | **high** |
| G7 | `/api/scenarios`, `/api/scenarios/{id}`, `/api/scenarios/current` | fault definitions of scenario files and of the running scenario | **high** |
| G8 | `/api/process/internal-states` | TEP states (labelled diagnostic) | medium |
| G9 | `/api/coupling` relation values | capacity fractions and fault-channel inputs | high |
| G10 | UI | shows G2 and G3 and has a Fault Injection tab in the same page | a human operator sees truth |

Metadata tags `model_internal` (assets) and `unobservable` (storage attributes) mark some of these
values. Only the UI details panel and `get_inventory` honour `unobservable`; nothing honours
`model_internal`.

## Consequence

The current REST API is a **benchmark-operator and development interface**. It is not a
system-under-test interface. Connecting an agent to it as-is would invalidate diagnosis benchmarks.

## Recommendation (not implemented)

Define the observable view explicitly, before building UNS, KG, i3X or MCP layers:

* an allow-list of entity properties by kind (e.g. `vibration` yes, `health` no);
* transmitted values only;
* events without `correlation_id` / `causation_id`, or with operational causation only;
* no boundary, no coupling, no exports, no scenario files, no internal states.

Serve it from a separate route tree or process, and test it negatively: assert the absence of every
item above.

Source:
- `api/service.py` — `SimulatorService.get_events`, `SimulatorService.get_process_image`, `SimulatorService.get_inventory`, `SimulatorService.current_scenario_with_faults`
- `api/export.py` — `build_bundle`
- `simulator/inventory/__init__.py` — `InventoryModule.setup`
