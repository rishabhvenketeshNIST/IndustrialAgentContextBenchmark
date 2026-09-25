# State API

All `GET`.

* **Operational routes** return the operational view built by `api/operational.py`
  ([canonical contract §20](../CANONICAL_SIMULATOR_CONTRACT.md#20-ground-truth)).
* **Evaluator views** under `/api/benchmark/*` return the canonical data with the truth intact.

| Route | Service method | Returns (operational view) | Evaluator view |
|---|---|---|---|
| `/api/enterprise`, `/api/site` | `get_enterprise`, `get_site` | root and site elements + status roll-up (+ production summary for the site) | — |
| `/api/areas` | `get_areas` | the 8 areas with roll-ups | — |
| `/api/hierarchy` | `get_hierarchy` | full tree with status and alarm counts; roll-ups use the operational status (no DEGRADED) | — |
| `/api/equipment?level=&parent=` | `get_equipment` | filtered elements | — |
| `/api/equipment/{id}`, `/api/entities/{id}` | `get_equipment_state`, `get_entity` | properties **without** `model_internal`/`unobservable` ones, asset DEGRADED reported as RUNNING, units, meta, bound TEP variables (transmitted), loops, active alarms, children | `/api/benchmark/entities/{id}` |
| `/api/entities?kind=` | `list_entities` | id, kind, name | — |
| `/api/entities/{id}/properties/{prop}` | `get_property` | value, unit, timestamp, simulation_time; 404 for a hidden property, as for a missing one | — |
| `/api/process/measurements` | `get_measurements` | 41 × (catalog + mapping + transmitted value + quality) | — |
| `/api/process/setpoints` | `get_setpoints` | loop setpoints with PV, mode, cascade parent | — |
| `/api/process/manipulated-variables` | `get_manipulated_variables` | 12 XMV with mapping | — |
| `/api/process/loops` | `get_loops` | full loop table | — |
| `/api/process/image` | `get_process_image` | transmitted XMEAS, quality, XMV, loops, mode, shutdown | `/api/benchmark/process/image` (+ true XMEAS, IDV, boundary and nominal boundary) |
| `/api/process/catalog` | `get_catalog` | static catalog: XMEAS/XMV/IDV/states/loops/trip limits | — |
| `/api/utilities` | `get_utilities` | the 4 service entities without model-internal properties (no status, availability, capacity fields, health; no utilization for steam) | `/api/benchmark/utilities` |
| `/api/maintenance` | `get_maintenance` | work orders, technicians, asset summary (status, vibration, role, run hours, running), spare parts | `/api/benchmark/maintenance` (+ health, efficiency) |
| `/api/inventory` | `get_inventory` | storage without lot composition or deviations, lots, POs, parts, materials, warehouse, shipments | `/api/benchmark/inventory` |
| `/api/quality` | `get_quality` | specs, samples, lots, lab properties | — |
| `/api/production-orders`, `/api/production` | `get_production_orders`, `get_production` | orders; line summary | — |
| `/api/history?series=a\|b&since=&max_points=` | `get_history` | trend-buffer series (10 s samples, max 20,000); `TRUE:` and hidden-property series answer 404 | `/api/benchmark/history` |
| `/api/history/catalog` | `get_history_catalog` | the operational series | `/api/benchmark/history/catalog` (all 166) |

**Benchmark-only data** (no operational equivalent):

| Route | Service method | Returns |
|---|---|---|
| `/api/benchmark/process/internal-states` | `get_internal_states` | the 50 TEP states, `diagnostic_only: true` |
| `/api/benchmark/coupling` | `get_coupling` | relations with baseline and current values, graph, boundary + definitions |
| `/api/benchmark/scenarios`, `/api/benchmark/scenarios/{id}` | `list_scenarios`, `get_scenario` | scenario files, including their fault definitions |
| `/api/benchmark/scenarios/current` | `current_scenario_with_faults` | the running scenario with the faults created in this run |
| `/api/benchmark/export/json`, `/api/benchmark/export/csv` | `export_json`, `export_csv_zip` | full bundle incl. all events and faults |
| `/api/benchmark/ui/snapshot?after_event=` | (composite) | the web UI (benchmark console) snapshot: simulation, hierarchy, process image, alarms, utilities, asset properties, production, new events |

Source:
- `api/service.py` — `SimulatorService`
- `api/operational.py` — `entity_dict`, `series_visible`
- `simulator/enterprise/__init__.py` — `EnterpriseView`
- `tests/test_operational_boundary.py`
