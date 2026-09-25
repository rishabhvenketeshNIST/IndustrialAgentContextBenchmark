# Fault taxonomy

34 registered fault types (`simulator/faults/types.py`, `REGISTRY`). `/api/benchmark/faults/catalog`
returns each type's parameters, defaults and valid targets.

## TEP_NATIVE_FAULT (20)

`IDV1` … `IDV20`. Each toggles `IDV(k)` and nothing else. TEFUNC treats IDV as binary, so:
* severity must be exactly 1.0;
* `gradual` progression is rejected;
* `intermittent` toggles the flag on and off.

Valid targets are `TEP` or any equipment listed for that IDV in the mapping. The target is only a
label: the effect is the same. IDV16-20 are flagged `documented: false`, because Downs & Vogel list
them as "Unknown"; their code effects are in [disturbances.md](../03_tep/disturbances.md). Stopping the
fault clears the flag unless another active fault of the same type still needs it.

## ENTERPRISE_FAULT (14)

| Type | Valid targets | Cause written | Consequence path | Persistent | Default observability |
|---|---|---|---|---|---|
| `equipment_degradation` | any asset | `damage = severity × intensity` (absolute health loss) | health ↓, vibration ↑ → capability → boundary | yes | no direct indication; via vibration and process |
| `cooling_degradation` | CW pumps, cooling tower, CW utility services | pump: `damage`; tower: `thermal_degradation`; utility: `capacity_loss` | CW capacity or supply temperature → VRNG(10/11) or SZERO(5/6) | yes | — |
| `pump_efficiency_loss` | pumps, compressor, boiler | `efficiency_loss` | efficiency × (1 − loss); health unchanged, so no vibration | yes | hidden; via flow / valve position |
| `equipment_failure` | any asset | `failed = 1` (step or intermittent only) | FAILED, `is_running = 0`, P1 work order, auto-changeover if there is a standby | yes | direct |
| `sensor_bias` | `XMEAS(n)` or a bound `EQUIPMENT.property` | overlay `bias` (param `bias`, default severity × 10 % of the base value) | transmitted offset; controllers act on it | no | — |
| `sensor_drift` | same | overlay `drift` (param `rate_per_h`, default severity × 5 % of base per hour) | growing transmitted error | no | — |
| `sensor_dropout` | same | overlay `dropout` | hold last value, quality BAD, BADPV alarm | no | direct |
| `utility_capacity_loss` | UT-* | `capacity_loss` | available capacity × (1 − loss) | no | — |
| `raw_material_quality_deviation` | materials with attributes | `value` on `MAT.attribute` (param `attribute`; `max_deviation`, default 4 × spec margin) | lot composition deviation → XST or SZERO(1/2); incoming inspection sees it | no | — |
| `raw_material_shortage` | raw materials | `supply_disruption` (POs delayed) + `stock_loss = severity × intensity` (irreversible write-off) | storage empties → supply availability → VRNG(1-4); orders BLOCKED | stock loss is permanent | direct |
| `maintenance_delay` | `MAINTENANCE` | `response_delay_s = severity × max_delay_s` (default 7200) | work orders start later | no | — |
| `spare_part_shortage` | spare part ids | `stock_blocked` | work orders WAITING_PARTS | no | — |
| `production_order_delay` | `ERP` or an order id | `release_delay_s` (default max 3600) or `blocked` (param `block`) | order release delayed or BLOCKED | no | — |
| `quality_failure` | quality test ids | `test_offset` (default 2 × spec width) | reported value offset → false FAIL → lot QUARANTINE/REJECTED | no | — |

**How simultaneous faults combine** on one channel (`COMBINE`):
* `damage`, `test_offset` and `value` add up;
* `efficiency_loss`, `capacity_loss` and `thermal_degradation` combine as 1 − Π(1 − x);
* `failed`, `supply_disruption`, `stock_blocked` and `blocked` are "any";
* `stock_loss`, `response_delay_s` and `release_delay_s` take the maximum.

Raw-material deviations use the `value` channel on `MAT.attribute`.

## Adding a type

See [adding faults](../12_developer_guide/adding_faults.md).

Source:
- `simulator/faults/types.py` — `REGISTRY`, `FaultType`, `_IDVFault`, `EquipmentDegradation`, `CoolingDegradation`, `PumpEfficiencyLoss`, `EquipmentFailure`, `SensorBias`, `SensorDrift`, `SensorDropout`, `UtilityCapacityLoss`, `RawMaterialQualityDeviation`, `RawMaterialShortage`, `MaintenanceDelay`, `SparePartShortage`, `ProductionOrderDelay`, `QualityFailure`, `catalog_list`
- `simulator/faults/effects.py` — `COMBINE`
- `tests/test_reproducibility.py` — `test_every_library_fault_can_start_and_stop`
