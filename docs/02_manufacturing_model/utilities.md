# Utilities

## What they represent

Four supply services the process depends on:

| Service | Supplied by | Serves | Design capacity | TEP coupling |
|---|---|---|---|---|
| `UT-CW-REACTOR` reactor cooling water | P-101A/B + CT-101 | EM-RX-COOLING | 1000 TEP CW flow units (= native VRNG(10)) | VRNG(10), SZERO(5) |
| `UT-CW-CONDENSER` condenser cooling water | P-201A/B + CT-101 | EM-CONDENSER | 1200 (= native VRNG(11)) | VRNG(11), SZERO(6) |
| `UT-STEAM` LP steam | B-301 | EM-STRIPPER-STEAM | 1500 kg/h boiler; 700 kg/h other site use; stripper design 500 kg/h | VRNG(9) |
| `UT-POWER` electrical power | TX-401, MCC-401 | compressor, agitator, pumps, tower fan, boiler auxiliaries | 2400 kW | indirectly (MCC supply fraction, compressor capability) |

"TEP CW flow units" are TEP's internal cooling-water flow units (`FWR = VPOS·VRNG/100`). TEP does not
state their physical unit, and the simulator does not convert them.

## Why they exist

TEP has no utilities. The coupling model gives the process physically meaningful supply limits. A real
cooling-water shortfall shows up in TEP as a reduced hydraulic capacity of the CW line.

## Who computes what

The split is deliberate and easy to miss:

* **Coupling relations** compute the numbers: `available_capacity`, `capacity_fraction`, `flow`,
  `demand`, `utilization`, `temperature`, `return_temperature`, `pressure`, `health` and power's
  `process_demand`, `shed_load`, `process_supply_fraction`, `voltage`.
* **`UtilitiesModule`** (post-step) registers the service entities and owns only `availability` and
  `status`, plus the constants `capacity` and `nominal_*`.

**Status classification** (`UtilitiesModule._classify`, thresholds in `configs/utilities.yaml`), checked
in this order:

1. **UNAVAILABLE:** availability < 0.02.
2. **CONSTRAINED:** utilization ≥ 0.97, meaning the supply is at its limit.
3. **DEGRADED:** availability < 0.999 or health < 0.75.
4. **NORMAL:** otherwise.

`availability` = `capacity_fraction` where a relation defines it (CW, steam), otherwise
`available_capacity / capacity` (power).

## Worked example: reactor cooling water in the demo

P-101A health falls from 0.97 to 0.35. `available_flow = 1100 × 0.35 × 1 × 1 ≈ 382`,
`capacity_fraction ≈ 0.382`, and `VRNG(10) ≈ 382`.

* **DEGRADED at 01:02:57.** Capacity drops below design: at health ≈ 0.909, 1100 × 0.909 = 1000.
* **CONSTRAINED at 01:28:16.** The valve demands the whole supply: utilization = `flow / min(avail, cap)`
  ≈ XMV(10)/100 ≥ 0.97.

The observed timeline is in [SCN-COOL-001_trace.md](../06_scenarios/scenario_examples/SCN-COOL-001_trace.md).

## Enterprise approximations (not TEP outputs)

* **CW `flow`** = `XMV(10)/100 × VRNG(10)`: a header flow-meter model.
* **CW `pressure`** = `p_nom × clip(avail/(1.1·cap), 0, 1.2)²`: a pump-curve approximation.
* **Steam `pressure`** collapses when demand exceeds boiler output; steam `temperature` is the
  saturation temperature from a steam-table interpolation.
* **Power `voltage`** sags 10 % per 100 % overload. Power demand is compressor work XMEAS(20) plus the
  agitator (cubic in TEP speed) plus the rated motor powers of running units.

None of these approximations enter TEP. Only the boundary relations do
([utilities_to_tep](../04_coupling/utilities_to_tep.md)).

## What it does NOT control

Utilities do not decide pump running states (equipment module) or repairs (maintenance). The status
has no feedback effect: it drives alarms and events only.

Events: `UTILITY_STATE_CHANGED` on every status change, carrying the causal correlation if registered.

Source:
- `configs/utilities.yaml`
- `configs/coupling.yaml`
- `simulator/utilities/__init__.py` — `UtilitiesModule`, `UtilitiesModule._classify`, `UtilitiesModule.post_step`
- `tests/test_enterprise.py` — `test_power_loss_cascades_to_pumps_and_compressor`, `test_equipment_degradation_propagates_through_coupling_to_tep`
