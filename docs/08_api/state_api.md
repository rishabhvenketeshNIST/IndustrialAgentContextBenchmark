# State API

All `GET`. "Truth content" flags whether a response contains simulation or benchmark truth beyond what
plant instruments and business systems would show ([benchmark limitations](../11_limitations/benchmark_limitations.md)).

| Route | Service method | Returns | Truth content |
|---|---|---|---|
| `/api/enterprise`, `/api/site` | `get_enterprise`, `get_site` | root and site elements + status roll-up (+ production summary for the site) | no |
| `/api/areas` | `get_areas` | the 8 areas with roll-ups | no |
| `/api/hierarchy` | `get_hierarchy` | full tree with status and alarm counts | no |
| `/api/equipment?level=&parent=` | `get_equipment` | filtered elements | no |
| `/api/equipment/{id}`, `/api/entities/{id}` | `get_equipment_state`, `get_entity` | all properties, units, meta, bound TEP variables with transmitted values, loops, active alarms, children | **yes**: health, efficiency, lot deviations |
| `/api/entities?kind=` | `list_entities` | id, kind, name | no |
| `/api/entities/{id}/properties/{prop}` | `get_property` | value, unit, timestamp, simulation_time | whatever the property is |
| `/api/process/measurements` | `get_measurements` | 41 × (catalog + mapping + transmitted value + quality) | no |
| `/api/process/setpoints` | `get_setpoints` | loop setpoints with PV, mode, cascade parent | no |
| `/api/process/manipulated-variables` | `get_manipulated_variables` | 12 XMV with mapping | no |
| `/api/process/loops` | `get_loops` | full loop table | no |
| `/api/process/image` | `get_process_image` | transmitted XMEAS, quality, XMV, loops, mode, shutdown, **boundary and nominal boundary** | **yes**: boundary |
| `/api/process/catalog` | `get_catalog` | static catalog: XMEAS/XMV/IDV/states/loops/trip limits | no |
| `/api/process/internal-states` | `get_internal_states` | the 50 TEP states, `diagnostic_only: true` | **yes** |
| `/api/utilities` | `get_utilities` | the 4 service entities | partly (capacity fractions are not plant measurements) |
| `/api/maintenance` | `get_maintenance` | work orders, technicians, asset summary (**incl. health, efficiency**), spare parts | **yes**: health |
| `/api/inventory` | `get_inventory` | storage (hides `unobservable` attributes, **but not `*_deviation`**), lots, POs, parts, materials, warehouse, shipments | **yes**: deviations |
| `/api/quality` | `get_quality` | specs, samples, lots, lab properties | no |
| `/api/production-orders`, `/api/production` | `get_production_orders`, `get_production` | orders; line summary | no |
| `/api/coupling` | `get_coupling` | relations with baseline and current values, graph, boundary + definitions | **yes** |
| `/api/history?series=a\|b&since=&max_points=` | `get_history` | trend-buffer series (10 s samples, max 20,000) | **yes** for `TRUE:XMEAS(n)` and model-internal series |
| `/api/history/catalog` | `get_history_catalog` | 166 series names, groups, labels, units | — |
| `/api/scenarios`, `/api/scenarios/{id}` | `list_scenarios`, `get_scenario` | scenario files (**incl. their fault definitions**) | **yes** |
| `/api/scenarios/current` | `current_scenario_with_faults` | the running scenario with the faults created in this run | **yes** |
| `/api/export/json`, `/api/export/csv` | `export_json`, `export_csv_zip` | full bundle incl. all events and faults | **yes** |
| `/api/ui/snapshot?after_event=` | (composite) | simulation state, hierarchy, process image, alarms, utilities, asset properties, production, new operational events | **yes** (boundary, health) |

Source:
- `api/service.py` — `SimulatorService`
- `simulator/enterprise/__init__.py` — `EnterpriseView`
- `tests/test_api_ui.py` — `test_snapshot_reflects_engine_state`
