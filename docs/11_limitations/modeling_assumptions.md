# Modelling assumptions

Assumptions made by the enterprise layer. None of them modifies TEP physics.

| Area | Assumption | Where |
|---|---|---|
| Pumps | deliverable flow = rated × efficiency × running × power supply; efficiency = health × (1 − efficiency loss); no pump curve | `coupling.yaml` |
| Pumps | rated at 110 % of circuit design, so wear below ≈ 9 % has no process effect | `equipment.yaml` |
| CW circuits | the reactor and condenser circuits are independent pump sets sharing one cooling tower | `coupling.yaml`, `site.yaml` |
| Cooling tower | supply temperature rises linearly by up to 12 °C as performance drops below 0.9 | `CR-CT-TEMPERATURE-RISE` |
| Steam | the boiler has a fixed 700 kg/h of other site demand; the stripper design is 500 kg/h | `utilities.yaml` |
| Power | 900 kW other site load, 60 % sheddable; process loads share the rest proportionally | `CR-POWER-*` |
| Compressor | 10 % capacity margin; power shortage derates it proportionally | `CR-COMPRESSOR-CAPABILITY` |
| Wear | constant rate per running hour plus small noise; never self-heals | `EquipmentModule.pre_step` |
| Condition monitoring | vibration = 1.8 + 10 (1 − h)² mm/s; alarm at 3.8 mm/s ≈ health 0.55 | `EquipmentModule._resolve`, `alarms.yaml` |
| Redundancy | failure → 30 s auto-start of the healthiest standby; degradation → no auto action | `EquipmentModule._redundancy` |
| Maintenance | fixed response time per priority; one technician per job; parts issued at start; repair restores health to a fixed value | `maintenance.yaml` |
| Inventory | segregated lots consumed FIFO; supply lost over the last 1 % of capacity above a 2 % heel; tanks drain at the **true** feed rate | `InventoryModule` |
| Materials | gas feeds converted with TEFUNC's kscmh equation and TEINIT compositions | `kg_per_h_factor` |
| Production | product mass = transmitted product flow × 613.4 kg/m³ (constant); production reported from the metered value | `production.yaml` |
| Quality | tests on transmitted analyzer values; sample assigned to the lot running 900 s earlier; decision 900 s after lot close; limits are benchmark defaults for Mode 1 | `quality.yaml` |
| Lots | one production lot per hour of running | `production.yaml` |
| Orders | single line, one order at a time, priority then planned start | `production.yaml` |
| Alarms | fixed thresholds, deadband 1 %, on-delay 10 s, off-delay 30 s (defaults) | `alarms.yaml` |
| Time | UTC clock for shifts; no calendar effects | `MaintenanceModule._hour` |

Source:
- `configs/`
