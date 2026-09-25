# Safety and shutdown

## Native interlock (teprob.f)

TEFUNC sets a local flag `ISD` when any of these limits is exceeded. It then zeroes every derivative,
which freezes the process:

| Condition | Limit | Same limit in XMEAS units |
|---|---|---|
| Reactor pressure | > 3000 kPa gauge | XMEAS(7) > 3000 |
| Reactor temperature | > 175 °C | XMEAS(9) > 175 |
| Reactor liquid volume | > 24 m³ or < 2 m³ | XMEAS(8) > 114.4 % or < −2.1 % |
| Separator liquid volume | > 12 m³ or < 1 m³ | XMEAS(12) > 136.6 % or < 2.7 % |
| Stripper liquid volume | > 8 m³ or < 1 m³ | XMEAS(15) > 130.5 % or < −27.4 % |

The XMEAS-unit conversions use the level equations in TEFUNC (`catalog.level_pct_from_volume`,
`shutdown_limits_in_measurement_units`). These limits are **not configurable**.

## How the simulator detects it

`ISD` is a local variable in the unmodified `teprob.f`, so it cannot be read. After each integration,
`BaseTEPAdapter._evaluate_shutdown` repeats the same comparisons on the noise-free `/TEPROC/` values
(PTR, TCR, VLR, VLS, VLC) that TEFUNC used. The first reason found is latched; the order checked is
pressure, reactor level, reactor temperature, separator level, stripper level. A non-finite reactor
pressure or temperature is reported as "Numerical instability".

## What happens after a trip

```mermaid
sequenceDiagram
  participant T as TEP
  participant A as Adapter
  participant E as Engine
  participant M as Modules
  T->>T: TEFUNC: ISD=1, YP=0 (states frozen)
  A->>A: _update_shutdown latches reason
  E->>E: sync: process.shutdown = True
  E->>M: PROCESS_SHUTDOWN (correlated if a cause is registered)
  M->>M: alarm ESD-TRIP; production rate 0 → DOWN; running order PAUSED;<br/>open lot closed; feed consumption 0; quality stops sampling
  Note over A: controllers are no longer called
```

* **The simulation keeps running.** The clock advances and enterprise modules keep working:
  maintenance continues, deliveries arrive, alarms evaluate. Only the process is frozen.
* **TEP's frozen outputs.** Measurements keep their last values, so flows read non-zero. The
  enterprise layer therefore treats production and consumption as zero by rule, not by reading them.
* **There is no restart.** The only way back is a reset (a new run).

## Pre-trip alarms (configurable)

`configs/alarms.yaml` defines benchmark alarms below the trip limits. Examples: PAH-07 at 2850 kPa and
PAHH-07 at 2950 kPa; TAH-09 at 122.5 °C and TAHH-09 at 150 °C; level HI/LO pairs; PAH-13 below the
purge override. They are observational. Nothing in the simulator acts on them automatically except
alarms that carry a `maintenance:` key.

## Worked example

SCN-COOL-001 with maintenance blocked by an added `maintenance_delay` fault trips on **reactor pressure
> 3000 kPa at 01:54:09** (6849 s), with a peak reactor temperature of about 148 °C. This was observed by
running the scenario. It is not a committed test.

Source:
- `simulator/tep/fortran/src/teprob.f`
- `simulator/tep/catalog.py` — `SHUTDOWN_LIMITS`, `shutdown_limits_in_measurement_units`, `level_pct_from_volume`
- `simulator/tep/interface.py` — `BaseTEPAdapter._evaluate_shutdown`, `BaseTEPAdapter._update_shutdown`
- `simulator/simulation/engine.py` — `SimulationEngine._step_once`
- `tests/test_tep_adapter.py` — `test_shutdown_detected_with_native_limits`
