# Utility state

Per service (`UT-CW-REACTOR`, `UT-CW-CONDENSER`, `UT-STEAM`, `UT-POWER`):

| Variable | Unit | Kind | Written by | Influences TEP |
|---|---|---|---|---|
| `capacity` | per service (TEP CW flow units, kg/h, kW) | constant | UtilitiesModule (setup) | in the fraction |
| `available_capacity` | same | derived | CouplingEngine | yes |
| `capacity_fraction` (CW, steam) | fraction | derived | CouplingEngine | yes |
| `availability` | fraction | interpretation | UtilitiesModule | no |
| `status` | NORMAL / DEGRADED / CONSTRAINED / UNAVAILABLE | interpretation | UtilitiesModule | no |
| `flow`, `demand` | same as capacity | derived observation | CouplingEngine | no |
| `utilization` | fraction (may exceed 1, capped at 1.5) | derived observation | CouplingEngine | no (drives CONSTRAINED and the inspection alarm) |
| `temperature` | °C (CW supply, steam saturation) | derived | CouplingEngine | CW: yes (SZERO); steam: no |
| `return_temperature` (CW) | °C | observation (= transmitted XMEAS 21/22) | CouplingEngine | no |
| `pressure` | barg (CW, steam) | derived approximation | CouplingEngine | no |
| `health` | fraction | derived | CouplingEngine | no |
| `voltage`, `shed_load`, `process_demand`, `process_supply_fraction` (power) | kV, kW | derived | CouplingEngine | `process_supply_fraction`: yes |
| `nominal_*` | — | constant | UtilitiesModule | some |

For power, `temperature` and `pressure` are `null` (not applicable).

Status classification and examples: [utilities](../02_manufacturing_model/utilities.md). Event:
UTILITY_STATE_CHANGED.

Source:
- `simulator/utilities/__init__.py` — `UtilitiesModule`
- `configs/coupling.yaml`
