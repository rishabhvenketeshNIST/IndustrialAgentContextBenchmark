# Equipment state

| Variable | Type | Unit | Kind | Written by | Update | Influences TEP |
|---|---|---|---|---|---|---|
| `health` | float 0..1 | fraction | simulated truth | EquipmentModule | every step (wear); repair; fault damage | yes (via relations / failure) |
| `status` | string | — | interpretation | EquipmentModule | every step (`_resolve`) | yes (via `is_running`) |
| `is_running` | float 0/1 | — | simulated | EquipmentModule | every step | yes |
| `desired_state` | string RUN/STANDBY/STOP | — | commanded | operator, changeover, maintenance | on command/event | yes |
| `role` | string duty/standby/single | — | interpretation | EquipmentModule | every step | no |
| `vibration` | float | mm/s | observable indicator | EquipmentModule | every step | no |
| `run_hours` | float | h | simulated | EquipmentModule | every running step | no |
| `efficiency` | float | fraction | derived (pumps, tower, boiler, compressor) / constant 1.0 (TX, MCC, agitator) | CouplingEngine / setup | every step | yes where derived |
| `available_flow`, `available_steam`, `available_kw`, `capability`, `supply_fraction`, `supply_temperature_rise` | float | various | derived | CouplingEngine | every step | yes |
| `rated_*` | float | various | constant | setup | never | — |
| TEP-bound properties on EMs/CMs (`temperature`, `position`, …) | float | from catalog | measured / commanded copy | ProcessInterface.sync | every step | no (copies) |
| loop CM properties `process_value`, `setpoint`, `output`, `mode`, `saturated` | float/str/bool | — | copy of native control | ProcessInterface.sync | every step | no (copies) |

Status state machine and redundancy: [equipment](../02_manufacturing_model/equipment.md).
Events: EQUIPMENT_STATE_CHANGED, EQUIPMENT_DEGRADED, EQUIPMENT_FAILED, EQUIPMENT_REPAIRED
([event catalog](event_catalog.md)).

Source:
- `simulator/equipment/__init__.py` — `Asset`, `EquipmentModule._resolve`
- `simulator/simulation/process_interface.py` — `ProcessInterface.sync`
