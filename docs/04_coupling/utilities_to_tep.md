# Utilities → TEP

| Utility | Capacity chain | Boundary | Physical effect in TEFUNC |
|---|---|---|---|
| Reactor CW | pumps' available flow (+ `capacity_loss` fault) → capacity fraction | VRNG(10) | `FWR = VPOS(10)·VRNG(10)/100` → CW energy balance YP(37) → TWR → `QUR = UAR·(TWR − TCR)` |
| Reactor CW supply temperature | cooling tower rise | SZERO(5) | mean of TCWR walk |
| Condenser CW | P-201A/B flow → capacity fraction | VRNG(11) | `FWS` → YP(38) → TWS → `QUS` |
| Condenser CW supply temperature | cooling tower rise | SZERO(6) | mean of TCWS walk |
| LP steam | boiler steam − 700 kg/h other users, over a 500 kg/h design | VRNG(9) | `UAC = VPOS(9)·VRNG(9)·(1+walk)/100`, `QUC = UAC·(100 − TCC)` |
| Electrical power | TX capacity (+ `capacity_loss`); shed up to 60 % of 900 kW other site load first; remaining share of process load → `process_supply_fraction` | via MCC supply fraction (pumps, tower, boiler) and compressor capability (CPFLMX) | derived from the above |

## Worked example: power capacity loss

`utility_capacity_loss` on UT-POWER with severity 0.75 gives available 2400 × 0.25 = 600 kW.

* Process demand ≈ 666 kW: compressor ≈ 341, pumps 200, fan 45, boiler auxiliary 25, agitator ≈ 55.
* Essential other load is 900 × 0.4 = 360 kW.
* `process_supply_fraction` = (600 − 360)/666 ≈ 0.36.

The consequences:

* **MCC supply 0.36:** every pump's available flow drops to about 36 %. Both CW circuits lose capacity
  (VRNG(10), VRNG(11) fall), and the boiler and tower lose output.
* **Compressor:** capability × 0.36, so CPFLMX falls.

`test_power_loss_cascades_to_pumps_and_compressor` checks the process fraction, the MCC fraction and
the compressor boundary.

## What is approximated

* **Header pressures, steam temperature, voltage.** They are computed for observation only and do not
  enter TEP.
* **Cooling-water flow units.** These are TEP's internal flow units; they are not converted to m³/h.
* **Steam.** Only the deliverable heat input is coupled. Steam pressure and temperature do not appear in
  TEFUNC.
* **Power.** Shortage is modelled as proportional derating of process motors. There is no motor
  tripping, restart or protection logic.

Source:
- `configs/coupling.yaml`
- `configs/utilities.yaml`
- `simulator/utilities/__init__.py` — `UtilitiesModule`
- `tests/test_enterprise.py` — `test_power_loss_cascades_to_pumps_and_compressor`
