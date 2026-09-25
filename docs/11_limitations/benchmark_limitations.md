# Benchmark limitations: ground truth vs observable state

The benchmark relies on this principle: *ground truth must not become operational or agent-visible
information.* Since contract 0.2.0 the operational routes enforce it through the **operational
boundary** (`api/operational.py`; [canonical contract §20](../CANONICAL_SIMULATOR_CONTRACT.md#20-ground-truth)).

## Enforced

* **Operational routes (`/api/*` outside `/api/benchmark/*`)** serve only operational views:
  * no fault or scenario identity, no seeds, no run id or configuration hash;
  * no fault-derived correlation;
  * no model-internal properties (health, capability, availability, utility status, lot composition);
  * no boundary values, true measurements or TEP states;
  * asset status without the hidden DEGRADED band;
  * an event stream renumbered so that withheld events leave no gap.
* **Fault control, ground truth, evaluator views and the benchmark console (web UI)** are only under
  `/api/benchmark/*`.
* **Sensor faults** change only transmitted values.
* **Tests:**
  * `tests/test_operational_boundary.py` crawls every operational route in the demo, before and after
    the first symptom, and checks for fault, scenario and model-internal information. It also shows that
    the operational event stream equals that of a fault-free run until the first observable symptom.
  * `tests/test_contract.py` keeps the boundary rules and the property classification consistent with
    the contract.

## Former leaks and their resolution

| # | Route / field | What leaked | Resolution |
|---|---|---|---|
| G1 | `/api/events` → `correlation_id`, `causation_id` | the fault id as root cause; causation pointing at the hidden `FAULT_STARTED` | rebuilt from operational causation only |
| G2 | `/api/entities`, `/api/equipment`, `/api/maintenance`, `/api/utilities`, snapshot → `health`, `efficiency`, `available_*`, `capability` | true equipment condition | properties tagged `model_internal` are withheld (404 like a missing property) |
| G3 | `/api/process/image`, `/api/coupling`, snapshot → `boundary` | reduced `VRNG(10)`, `CPFLMX`, … | removed from the operational image; `/api/benchmark/process/image` |
| G4 | `/api/history` → `TRUE:XMEAS(n)` | true vs transmitted reveals sensor faults | not listed and rejected operationally; `/api/benchmark/history` |
| G5 | `/api/inventory`, `/api/entities/SU-*` → `*_deviation` | raw-material quality deviation | tagged `unobservable` and withheld |
| G6 | `/api/export/json`, `/api/export/csv` | everything, including fault records | moved to `/api/benchmark/export/*` |
| G7 | `/api/scenarios`, `/api/scenarios/{id}`, `/api/scenarios/current` | scenario fault definitions | moved to `/api/benchmark/scenarios*` |
| G8 | `/api/process/internal-states` | TEP states | moved to `/api/benchmark/process/internal-states` |
| G9 | `/api/coupling` | capacity fractions and fault-channel inputs | moved to `/api/benchmark/coupling` |
| G10 | UI | showed G2/G3 next to the Fault Injection tab | decision: the UI is the benchmark console and reads `/api/benchmark/*` |
| G11 | `/api/simulation/manifest` | fault list, `active_faults`, scenario id, run id, seeds, configuration hash, event count | operational manifest without them; `/api/benchmark/manifest` |
| G12 | `/api/simulation`, snapshot → `scenario` | scenario name and description naming the fault | removed operationally; `/api/benchmark/simulation` |
| G13 | asset `status` = DEGRADED; `EQUIPMENT_DEGRADED`; RUNNING↔DEGRADED `EQUIPMENT_STATE_CHANGED`; hierarchy roll-ups | the true health band, 10 min before the first symptom in the demo | DEGRADED reported as RUNNING; those events withheld |
| G14 | `EQUIPMENT_FAILED.reason`, health fields in equipment events, `EQUIPMENT_REPAIRED.cleared_fault_effects` | fault-induced vs worn-out failure; fault ids | payload fields removed |
| G15 | utility `status`, `availability`, capacity fields, `UTILITY_STATE_CHANGED` | hidden capacity loss 18 min before the first symptom in the demo (A3) | tagged `model_internal`; the event type is withheld |
| G16 | sequential `EV-` event ids | gaps at 01:00:00 (fault start) and 01:02:57 (utility change) | operational ids `OE-n` over the operational stream |

The full classification, including the items judged legitimate and the decisions D1–D5, is in the
[contract audit](../CANONICAL_CONTRACT_AUDIT.md).

## Remaining limitations

* **No authentication.** The boundary is the route namespace. A system under test must be given the
  operational routes only (for example through a proxy or a future MCP layer that maps only them).
* **Control routes** (`/api/simulation/*` lifecycle, `/api/operator/*`) are operational commands.
  `POST /api/simulation/create` accepts an inline scenario, which can contain faults (decision D5).
* **Legitimate but informative observations:** utility meter readings (header pressure, supply
  temperature, voltage) are noise-free algebraic functions of capability. They model real instruments
  and stay operational, but they are cleaner than real instruments (decision D1).

Source:
- `api/operational.py` — `OperationalEventView`, `hidden_properties`, `manifest`
- `api/service.py` — `SimulatorService.get_events`, `SimulatorService.get_manifest`
- `tests/test_operational_boundary.py`
