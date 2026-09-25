# Intervention and recovery

Nothing in the simulator repairs itself. Recovery comes from one of four mechanisms, all implemented
as enterprise logic acting on enterprise state:

| Mechanism | Trigger | What it changes | Code |
|---|---|---|---|
| Automatic changeover | a redundancy group has no running unit (failure or stop) | starts a STANDBY unit after 30 s | `EquipmentModule._redundancy` |
| Maintenance (corrective, inspection, planned) | alarms with `maintenance:`, failures, plan, operator | isolates, changes over to standby, repairs (health, clears fault damage) | `MaintenanceModule`, `EquipmentModule._on_maintenance_*` |
| Operator actions | UI/API or scripted `operator_actions` | setpoints, modes, manual XMV, run/standby/stop assets, order commands, purchasing, work orders | `OperatorModule`, `SimulationEngine._register_commands` |
| Benchmark fault control | stop / reset | ends a cause; reset removes all contributions | `FaultEngine` |

The native control scheme is not a recovery mechanism in this sense. It compensates within its limits
and, with wind-up, can make recovery overshoot.

## Worked comparison: SCN-COOL-001 with and without maintenance

| | Default (maintenance responds) | With a `maintenance_delay` fault blocking dispatch |
|---|---|---|
| Work order start | 01:45:33 (P2 response 1500 s after the 01:20:33 request) | never within the run |
| Valve saturation | 01:28:50 → 01:45:40 | from 01:28:50 on |
| Peak reactor T | 138.21 °C at 01:45:40 | ≈ 148 °C |
| Outcome | recovery with undershoot (116.0 °C at 02:20:40), incomplete at 3 h; lot quarantined | **trip at 01:54:09, reactor pressure > 3000 kPa** |

The comparison was run during this documentation pass by adding
`{type: maintenance_delay, target: MAINTENANCE, severity: 1, parameters: {max_delay_s: 20000}, trigger: {mode: simulation_time, time: 0}}`
to the scenario. It is not a committed test.

**Margin:** without the standby start, the trip would have occurred about 8.6 minutes later. Benchmark
designers should treat SCN-COOL-001 as sitting close to a cliff. Changes to severity, response time,
seed or technician availability can flip the outcome.

## An operator could have intervened earlier

These actions are all available through the UI and API. None is scripted in the demo.

* **Start the standby pump** before the work order: `equipment_command {asset_id: WU-CWP-101B, state:
  RUN}`. Both pumps would then run, capacity = (385 + 1045)/1000 → clipped to 1.0.
* **Put TC-RX in MAN** to stop wind-up. TC-RCW then runs on a local setpoint.
* **Raise the priority** of the work order by requesting a P1 corrective order. This is not possible
  while one is already open for the asset, because requests are deduplicated per asset.

## After a trip

There is no restart. See [safety and shutdown](../03_tep/safety_and_shutdown.md).

Source:
- `simulator/equipment/__init__.py` — `EquipmentModule._redundancy`, `EquipmentModule.command`
- `simulator/maintenance/__init__.py` — `MaintenanceModule.request`
- `simulator/operator/__init__.py` — `OperatorModule.execute`
