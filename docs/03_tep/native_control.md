# Native TEP control

The complete loop table (tags, PVs, setpoints, gains, reset times, periods, cascade structure,
control modules) is generated in [native_control_loops.md](native_control_loops.md).

## Five different things, never mixed

| Concept | Where it lives | Example (reactor temperature) | Who writes it |
|---|---|---|---|
| **Process measurement** | `/PV/ XMEAS(n)` (true), `process.xmeas` (transmitted) | XMEAS(9) = 120.40 °C | TEFUNC |
| **Controller setpoint** | `/CTRLALL/ SETPT(k)` | SETPT(18) = 120.40 °C (TC-RX) | initial value from MAIN; operator; or a cascade master |
| **Controller output** | what CONTRLn writes | TC-RX writes SETPT(10), which is TC-RCW's setpoint | the native subroutine |
| **Manipulated variable** | `/PV/ XMV(n)` | XMV(10) reactor CW valve command, written by TC-RCW | native subroutine, or operator in MAN |
| **Process response** | TEP states → next XMEAS | valve position VPOS(10) follows XMV(10) (τ = 5 s); cooling flow; TWR; TCR | TEFUNC |

`BaseTEPAdapter.get_loop_states()` reports them separately for every loop: `pv`, `setpoint`, `output`,
`mode`, `error` and `saturated`. The UI details panel shows them in separate rows
(`test_setpoint_measurement_and_output_are_distinct`).

## How the native loops are executed

`temain_mod.f` MAIN is never run. Instead, every simulated second the adapter does the following
(`BaseTEPAdapter.step`):

1. Counts the step `I`. A loop is due when `I mod period == 0` (period 3, 360 or 900) and it is not in
   MAN.
2. If any loop is due, it copies the true XMEAS vector and writes the **transmitted** values (from the
   instrumentation layer) into `/PV/ XMEAS`.
3. Calls the due `CONTRLn` subroutines in native order: 1-11, 16, 17, 18, then 13, 14, 15, 19, then
   20.
4. Restores the true XMEAS values.
5. Calls `INTGTR` (TEFUNC + explicit Euler, DELTAT = 1/3600 h in single precision) and then
   `CONSHAND` (clamps XMV 1-11 to 0-100).

This is the same order as one iteration of the native main loop. Controller parameters are loaded
into `/CTRLALL/` and `/CTRLn/` from `control_scheme.py` before the first step. Constants like
`GAIN10 = -0.156*10.` are evaluated as single-precision REAL exactly as gfortran folds them
(`control_scheme.r4`).

## The loops (summary)

* **14 fast loops, every 3 s:** flow loops FC-D/E/A/AC/RCY/PRG/STM/PRD, level loops LC-SEP/LC-STR, and
  temperature loops TC-RCW, TC-STR, LC-RX, TC-RX.
* **4 analyzer loops, every 360 s:** reactor feed composition A, D, E → flow setpoints, and purge B →
  purge setpoint.
* **1 slow loop, every 900 s:** product E composition → stripper temperature setpoint.

All are **velocity form**: `ΔOUT = K·[(e − e_old) + e·Δt·period/Ti]`. The P-only loops omit the
integral term.

Cascades (master → slave):
* TC-RX → TC-RCW;
* TC-STR → FC-STM;
* LC-RX → FC-AC;
* AC-RFA → FC-A, AC-RFD → FC-D, AC-RFE → FC-E;
* AC-PRGB → FC-PRG;
* AC-PRDE → TC-STR.

**Special case: loop 6 (purge).** A separator-pressure override: at ≥ 2950 kPa it forces XMV(6) to
100 %, and at ≤ 2300 kPa to 0 %, with hysteresis at 2633.7 kPa (`FLAG6`). `get_loop_states` reports
`override` while it is active.

**XMV(12)** (agitator speed) has no loop. It stays at the TEINIT value of 50 % unless the operator
changes it.

## Modes (enterprise-layer addition)

* Plant **CLOSED_LOOP**: every loop runs. **MANUAL**: none run; XMVs are operator-set, and the test
  `test_manual_mode_freezes_controller_outputs` checks that they stay frozen.
* Per loop **AUTO**, **CAS** (setpoint written by the master; the operator cannot change it) or **MAN**
  (the subroutine is not called).
* **MAN → AUTO:** the adapter sets `ERROLDn` to the current error so there is no proportional kick
  (`_bumpless_init`). This is the only enterprise write to controller state.

## Native behaviour you will see: saturation and wind-up

The loops have **no anti-windup**. This is preserved.

Observed in SCN-COOL-001 (trend buffer, true values):

| Time | Reactor T | CW outlet T (XMEAS 21) | TC-RCW setpoint (= TC-RX output) | XMV(10) | What is happening |
|---|---|---|---|---|---|
| 00:58 | 120.41 | 94.4 | 94.58 | 41.5 % | normal |
| 01:23 | 120.93 | 95.1 | 70.41 | 76.5 % | CW capacity falling; TC-RX lowers the CW-outlet setpoint, TC-RCW opens the valve |
| 01:28 | 121.21 | 95.2 | 56.35 | 97.8 % | valve about to saturate |
| 01:40 | 131.99 | 103.6 | −354.1 | 100 % | saturated; reactor heats; TC-RX keeps integrating |
| 01:45 | 137.57 | 107.6 | −636.8 | 100 % | minimum setpoint −675.7 at 01:45:40; standby pump starts at 01:45:33 |
| 01:47 | 133.33 | 99.8 | −565.5 | 14.1 % | capacity back; the sharp fall of XMEAS(21) makes the velocity-form proportional term close the valve |
| 02:13 | 116.48 | 92.2 | −141.2 | 43.5 % | undershoot below 120.4 °C while the setpoint unwinds |
| 03:00 | 118.68 | 93.7 | 60.2 | 41.1 % | still recovering: setpoint not back to ≈ 94.6, T 1.7 °C low |

The sequence: cooling constraint → controller increases output → valve saturates → process deviation
continues → capacity restored → wind-up drives an **undershoot** → slow unwinding. The undershoot is
what fails product quality at 02:15 ([quality](../02_manufacturing_model/quality.md)). **Recovery
is incomplete when the 3 h demo ends.**

## After a trip

Once TEP's interlock trips, the adapter stops calling the controllers (`if not self.is_shutdown()` in
`BaseTEPAdapter.step`). TEFUNC has zeroed all derivatives, so nothing would move anyway.

Source:
- `simulator/tep/control_scheme.py` — `LOOPS`, `EXECUTION_ORDER`, `INITIAL_XMV`, `PURGE_OVERRIDE`, `r4`, `cascade_parent`
- `simulator/tep/interface.py` — `BaseTEPAdapter.step`, `BaseTEPAdapter._load_native_controller_configuration`, `BaseTEPAdapter.set_loop_mode`, `BaseTEPAdapter.set_setpoint`, `BaseTEPAdapter._bumpless_init`, `BaseTEPAdapter.get_loop_states`
- `simulator/tep/fortran_backend.py` — `FortranTEPAdapter._run_native_controller`
- `simulator/tep/python_backend.py` — `PythonTEPAdapter._run_native_controller`, `PythonTEPAdapter._contrl6`
- `tests/test_tep_adapter.py` — `test_native_control_scheme_transcription`, `test_manual_mode_freezes_controller_outputs`, `test_setpoint_measurement_and_output_are_distinct`
