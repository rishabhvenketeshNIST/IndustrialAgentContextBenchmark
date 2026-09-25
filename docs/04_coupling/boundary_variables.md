# Boundary variables: every enterprise → TEP coupling

17 boundary parameters are declared in `simulator/tep/boundary.py`. **15 are driven by coupling
relations. Two (`d_feed_temperature_mean`, `stream4_temperature_mean`) are declared but not coupled;**
they always keep their TEINIT value.

What every coupling in this table has in common:
* It changes a **boundary condition** of TEFUNC. None changes an XMV, IDV or measurement.
* Values are clamped to the declared `[min, max]` by `set_boundary_parameter`.
* The write happens in the pre-step of second t and affects the integration from t to t+1, unless
  noted otherwise.
* In a healthy plant, the value equals the TEINIT value exactly (design margins or deviation form).

## Hydraulic and capacity limits (effect: immediate, next TEFUNC call)

| Boundary (Fortran) | TEINIT | Source variable → transformation | Physical interpretation | Limits | Test |
|---|---|---|---|---|---|
| `reactor_cw_max_flow` (VRNG(10)) | 1000 | `UT-CW-REACTOR.capacity_fraction`; `nominal × frac`; frac = clip(Σ pump `available_flow` × (1 − utility capacity_loss) / 1000, 0, 1); pump flow = 1100 × efficiency × is_running × MCC supply | pump head/flow capability: CW flow at a given valve opening (`FWR = VPOS(10)·VRNG(10)/100`) falls | ≥ 0 | `test_equipment_degradation_propagates_through_coupling_to_tep` |
| `condenser_cw_max_flow` (VRNG(11)) | 1200 | same with P-201A/B (rated 1320) and design 1200 | condenser CW flow capability (`FWS`) | ≥ 0 | `test_failure_changeover_and_maintenance_recovery` (checks capacity fraction 0 → 1) |
| `stripper_steam_valve_range` (VRNG(9)) | 0.03 | `UT-STEAM.capacity_fraction` = clip((boiler steam × (1 − loss) − 700) / 500, 0, 1); boiler steam = 1500 × efficiency × running × MCC supply | heat input to the stripper at a given steam valve opening (`UAC`, `QUC`) | ≥ 0 | none specific (gap) |
| `compressor_max_flow` (CPFLMX) | 280275 | `EM-COMPRESSOR.capability` = clip(efficiency × 1.10, 0, 1) × running × `UT-POWER.process_supply_fraction` | compressor curve capacity | ≥ 0 | `test_power_loss_cascades_to_pumps_and_compressor` |
| `a_feed_max_flow` (VRNG(3)) | 100 | `SU-SPH-103.supply_availability` | no A gas when storage is empty | ≥ 0 | none specific |
| `d_feed_max_flow` (VRNG(1)) | 400 | `SU-TK-101.supply_availability` | no D feed when the tank is at its heel | ≥ 0 | `test_order_blocked_by_material_shortage` (asserts 0) |
| `e_feed_max_flow` (VRNG(2)) | 400 | `SU-TK-102.supply_availability` | E feed | ≥ 0 | none specific |
| `ac_feed_max_flow` (VRNG(4)) | 1500 | `SU-SPH-104.supply_availability` | A/C feed | ≥ 0 | none specific |

`supply_availability = clip((usable_kg − 2 % capacity) / (1 % capacity), 0, 1)`: it falls from 1 to 0
over the last 1 % of capacity above the heel.

## Composition of the lot being consumed (effect: immediate)

| Boundary | TEINIT | Source → transformation | Interpretation | Limits | Test |
|---|---|---|---|---|---|
| `d_feed_b_impurity` (XST(2,1); XST(4,1) = complement) | 9.9999997e-05 (REAL literal 0.0001) | `nominal + SU-TK-101.feed_impurity_deviation` | B inert in the D feed | 0-0.2 | none specific |
| `e_feed_f_impurity` (XST(6,2); XST(5,2) = complement) | same | `nominal + SU-TK-102.feed_impurity_deviation` | F in the E feed | 0-0.2 | none specific |
| `a_feed_b_impurity` (XST(2,3); XST(1,3) = complement) | same | `nominal + SU-SPH-103.feed_impurity_deviation` | B in the A feed | 0-0.2 | none specific |

The deviation is lot attribute minus certificate nominal, plus any `raw_material_quality_deviation`
contribution. The *deviation* form is used because TEINIT assigns these values as single-precision
REAL literals; writing the double 0.0001 back would perturb a healthy run.

## Random-walk means (effect: delayed, at the next walk knot)

TEFUNC recomputes these inputs every call from cubic random walks (`TESUB8`). The simulator changes only
the walk **mean** `SZERO(i)`. `TESUB5` uses the mean as the target of the *next* walk segment, so a
change is picked up at the next knot and fully reached by the following one. Segment lengths are
`HSPAN·U(−1,1) + HZERO`: 0.1–0.4 h for walks 5-6 (CW temperatures), 0.3–0.7 h for walk 1, 0.3–1.7 h for
walk 2.

| Boundary | TEINIT | Source → transformation | Interpretation | Limits | Test |
|---|---|---|---|---|---|
| `reactor_cw_inlet_temperature_mean` (SZERO(5)) | 35 °C | `UT-CW-REACTOR.temperature` = 35 + CT-101 rise; rise = 12 × clip((0.9 − tower performance)/0.9, 0, 1); performance = health × running × MCC supply × (1 − thermal_degradation) | cooling-tower supply temperature (`TCWR = TESUB8(5) + IDV(4)·5`) | 5-80 | none specific |
| `condenser_cw_inlet_temperature_mean` (SZERO(6)) | 40 °C | `UT-CW-CONDENSER.temperature` = 40 + same rise | condenser CW supply (`TCWS`) | 5-80 | none specific |
| `stream4_a_fraction_mean` (SZERO(1)) | 0.485 | `nominal + SU-SPH-104.feed_a_fraction_deviation` | A content of the A/C feed | 0.3-0.7 | none specific |
| `stream4_b_fraction_mean` (SZERO(2)) | 0.005 | `nominal + SU-SPH-104.feed_b_fraction_deviation` | B inert content of the A/C feed | 0-0.1 | none specific |
| `d_feed_temperature_mean` (SZERO(3)) | 45 °C | **not coupled** | — | 0-120 | — |
| `stream4_temperature_mean` (SZERO(4)) | 45 °C | **not coupled** | — | 0-120 | — |

Observed example of the delay: during the cooling-tower planned maintenance in SCN-FAULT-LIBRARY, the
supply temperature jumped 35 → 47 °C at 25,200 s, and XMV(10) rose gradually from 41 % to 52 % over the
following ~25 min ([maintenance_to_tep](maintenance_to_tep.md)).

## Assumptions shared by all couplings

* A utility or equipment limitation acts as a scaling of TEP's existing capacity term (the valve range
  or the compressor limit). A real pump curve and valve interaction is more complex.
* **CW pumps scale both** the reactor and condenser circuits independently. The cooling tower affects
  both supply temperatures by the same rise.
* Relations use the previous second's process values, a one-step lag, where they read `process.*`. Only
  observation relations do; no boundary relation reads process values.

Source:
- `simulator/tep/boundary.py` — `BOUNDARY_PARAMETERS`, `BoundaryParameter`
- `configs/coupling.yaml`
- `simulator/inventory/__init__.py` — `InventoryModule._refresh_storage`
- `simulator/tep/fortran/src/teprob.f`
