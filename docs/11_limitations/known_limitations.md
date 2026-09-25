# Known limitations

Each entry: **what** is limited · **why** · **impact** · **benchmark validity** · **possible
improvement** (a suggestion only, not implemented). Details per area:
[TEP](tep_limitations.md) · [coupling](coupling_limitations.md) · [benchmark](benchmark_limitations.md) ·
[modelling assumptions](modeling_assumptions.md).

| # | What | Why | Impact | Affects benchmark validity? | Possible improvement |
|---|---|---|---|---|---|
| L1 | **The operational boundary is route separation, without authentication** (resolved leaks G1–G16; see the contract audit) | no access control exists | any client that can reach the server can call `/api/benchmark/*` | **yes**, unless a system under test is given the operational routes only | serve operational routes from a separate process or behind access control |
| L2 | **No plant restart after an interlock trip** | TEFUNC freezes the state (ISD); no restart procedure exists in TEP | a scenario ends effectively at the trip | yes for scenarios that trip: only pre-trip behaviour is meaningful | model a restart as re-initialisation with enterprise state carried over |
| L3 | **The native loops wind up** (no anti-windup) | preserved original behaviour | long undershoots after saturation; recovery incomplete within 3 h in the demo | no; it is the reference behaviour, but it must be understood when scoring recovery | none (by design) |
| L4 | **Enterprise models are uncalibrated approximations** (wear rates, vibration curve, response times, utility pressures and temperatures, voltage, steam and power models, density, BOM) | no real plant data | magnitudes and timings are plausible, not realistic for a specific plant | moderate: relative comparisons are fine; absolute timings are scenario choices | calibrate against plant or literature data |
| L5 | **Only 15 of 17 boundary parameters are coupled; the agitator and several equipment types cannot affect TEP** | TEP has no input for them, or it was not modelled | some faults have enterprise-only consequences | low: documented | couple D-feed and stream-4 temperatures to storage or heat tracing |
| L6 | **Random-walk-mean couplings (SZERO) are delayed and smooth** | TEFUNC recomputes these inputs from walks each call | supply temperature or composition changes act over 0.1-1.7 h | low: physically plausible | none needed |
| L7 | **Causal labels are heuristic** (first cause wins, 0.5 % threshold, curated TEP influences) | TEP is opaque; labels are metadata | wrong or missing correlation ids with overlapping faults or TEP-internal propagation | yes if labels are used as scoring ground truth | score from fault specs and timing, and use labels as hints |
| L8 | **Reproducibility is per compiled library** | floating-point differences between builds | runs on other machines or builds may diverge | yes across sites | ship and pin the library; record the numpy version and a code hash |
| L9 | **Python backend is not equivalent** | the port does not reproduce REAL-literal rounding | different trajectories | yes if used for benchmarking | use Fortran only for benchmark runs |
| L10 | **Maintenance isolates non-redundant assets for the full task** | model simplicity | planned work on the cooling tower, boiler, TX, MCC or compressor disturbs the process | scenario design issue | allow online work or maintenance windows |
| L11 | **No automatic preventive maintenance** (only scenario-planned tasks) | not implemented | only scenario-planned maintenance | low | implement PM scheduling |
| L12 | **Single site, single TEP, single simulation per server** | scope | — | no | — |
| L13 | **No persistence or authentication**; in-memory only | scope | state is lost on restart; anyone with network access to the port can control it | operational concern | persistence and auth in a later layer |
| L14 | **Trend buffer at 10 s**; exports have no per-second data | memory bound | fast dynamics are under-sampled in exports | low | configurable per-series rates |
| L15 | **The demo sits near a trip threshold** | chosen to show a meaningful response | small changes flip the outcome | yes, if the demo is used as a fixed benchmark case without pinning everything | provide variants at several severities |
| L16 | **Operator commands issued in real time are not reproducible** unless re-scripted | wall-clock-dependent landing time | interactive runs cannot be replayed exactly | yes for human-in-the-loop runs | record commands with their simulation times and replay them |
| L17 | **Time-step is fixed at 1 s** | TEP's native integrator | cannot be changed | no | — |
| L18 | **Sensor faults cannot express `stuck` (without BAD flag)**, and there is no dedicated sensor-state event | not wired | fewer sensor fault forms; sensor faults are visible only through measurement quality and BADPV alarms | low | add a fault type (and an event type if needed) |

Source:
- `api/service.py`
- `simulator/tep/interface.py` — `BaseTEPAdapter.step`
- `configs/equipment.yaml`
