# Determinism

## Definition used in this simulator

Given the **same compiled TEP library, the same code, the same configuration files, the same scenario
(including the seed) and the same sequence of external commands at the same simulation times**, two
runs produce:

* **identical `EV-` event sequences**: ids, types, times, sources, targets, payloads and correlations;
* **identical process trajectories**, bit for bit, including all trend series;
* **identical run id and configuration hash**.

## Mechanisms

| Mechanism | Where |
|---|---|
| **Integer-second clock**; no wall-clock value is used in simulation logic | `SimulationClock` |
| **Timestamps** = `simulation_start + t` | `SimulationClock.timestamp` |
| **Fixed module order**, single thread, one lock | `SimulationEngine._step_once`; `SimulatorService.lock` |
| **Synchronous event bus** with sequential ids | `EventBus.publish` |
| **Lifecycle events on a separate `LC-` sequence**, so pause/resume timing does not shift `EV-` ids | `LIFECYCLE_EVENT_TYPES` |
| **One PCG64 stream per module**: `SeedSequence([seed_lo, seed_hi, crc32(name)])` | `RandomStreams.get` |
| **TEP seed** from `SeedSequence([seed, crc32("tep.randsd")])`, odd | `RandomStreams.tep_seed` |
| **All Fortran common blocks zeroed before TEINIT** | `FortranTEPAdapter._native_init` |
| **MAIN constants in single precision** | `control_scheme.r4` |
| **Deterministic iteration order**: sorted keys and ids in modules and faults | throughout |
| **Real-time runner batches only change when steps run**, never what a step does | `Runner.run` |

## What breaks determinism

* **Operator or benchmark commands issued at different simulation times.** For example, pressing a UI
  button while running in real time: the step at which the command lands depends on wall-clock timing.
  Scripted `operator_actions` and scheduled faults are deterministic.
* **A different compiled library** (compiler, version, flags, platform).
* **The Python backend** vs the Fortran backend.
* **Any configuration or scenario change.** This is detected, because the configuration hash changes.
* **numpy's PCG64 implementation** changing across major numpy versions. This is not pinned; the numpy
  version is **not recorded** in the manifest.

## Tests

* `test_fortran_is_deterministic_and_seed_dependent`
* `test_same_scenario_same_seed_identical_results`: 2 h demo, full traces
* `test_fault_injection_is_deterministic`
* `test_different_seed_gives_different_trajectory`

Detailed reproduction procedure: [seeds and reproducibility](../10_operation/seeds_and_reproducibility.md).

Source:
- `simulator/common/__init__.py` — `RandomStreams`, `stable_hash32`
- `simulator/simulation/clock.py` — `SimulationClock`
- `simulator/events/__init__.py` — `EventBus`
