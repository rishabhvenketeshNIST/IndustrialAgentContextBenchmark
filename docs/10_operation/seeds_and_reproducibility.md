# Seeds and reproducibility

## What you need to reproduce a run exactly

| Factor | Where it is recorded | Notes |
|---|---|---|
| Simulator code version | `manifest.simulator_version` (`simulator.__version__` = 1.0.0) | **a version string, not a code hash.** Uncommitted code changes are not detected |
| TEP source | `manifest.tep_version.sources` (SHA-256 of `teprob.f`, `temain_mod.f`) | Fortran backend only |
| TEP compiled library | `manifest.tep_version.library_sha256`, `library` | must match exactly for bit-equality |
| Backend | `manifest.tep_backend` | fortran or python |
| Platform | `manifest.tep_version.platform` | informative |
| Configuration + scenario | `manifest.configuration_hash` (SHA-256 of the canonical JSON of the scenario and all merged configs) | changes with any edit |
| Seed | `manifest.seed` | root of all random streams |
| TEP seed | `manifest.tep_seed` | derived from the seed unless set in the scenario |
| Initial state | implied: TEINIT + configs | there is no custom initial-state support |
| Simulation start | `manifest.simulation_start` | affects timestamps and technician shifts |
| Duration | `manifest.duration_seconds` | a duration override is part of the scenario used |
| Time step | fixed: 1 s (TEP DELTAT = single-precision 1/3600 h) | not configurable |
| Trend sampling | `configs/simulation.yaml` (10 s) | in the configuration hash |
| External commands | the event log (`OPERATOR_ACTION`, lifecycle `LC-` events; benchmark `FAULT_*`) | commands issued interactively must be replayed at the same simulation times |
| numpy version | **not recorded** | PCG64 streams are stable in practice, but this is not guaranteed across versions |

`run_id = RUN-<scenario_id>-<seed>-<first 10 hex of configuration_hash>`. Identical inputs give the
same run id by design.

## Procedure

1. Check out the same code and use the same library file (compare `library_sha256`).
2. Use the scenario file and configs with the same `configuration_hash`.
3. Replay interactive commands as scripted `operator_actions` and scheduled faults at the recorded
   simulation times, or run headless.
4. Compare event logs (excluding `LC-` lifecycle events) and the trend series.

## Cross-platform

**Not guaranteed.** A library built by a different compiler, version, flags or architecture may differ
in the last bits, and differences grow over time in closed loop. The prebuilt Windows DLL was built with
GCC 12 (mingw-w64) via Docker.

Source:
- `simulator/simulation/engine.py` — `SimulationEngine.manifest`, `SimulationEngine.run_manifest`
- `simulator/common/__init__.py` — `RandomStreams`, `config_hash`
- `simulator/scenarios/__init__.py` — `configuration_hash`
