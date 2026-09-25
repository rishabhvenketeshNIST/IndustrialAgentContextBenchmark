# ACME Enterprise Manufacturing Simulator — Tennessee Eastman Site

A deterministic, reproducible enterprise manufacturing simulator built around the **original
Tennessee Eastman Process (TEP) Fortran model**. TEP (Downs & Vogel; closed-loop scheme of Russell,
Chiang & Braatz) provides the continuous process physics. An ISA-95 structured enterprise layer
provides everything around it: utilities, equipment condition, maintenance, materials, inventory,
warehouse, quality, production orders, scheduling, alarms, operator actions and a first-class,
benchmark-only **fault engine**. A web UI visualises and controls it all.

Out of scope by design: no UNS, MQTT, historian, knowledge graph, i3X, MCP, AI agent, LLM, vector
database or RAG. Those can be added later as separate layers on top of the API.

```
Enterprise ─ Site ─ ISA-95 Areas ─ Equipment ─ simulated processes ─ process state ─ events/alarms/faults/maintenance/production
                                                        │
                                           TEP Fortran physics (unmodified)
```

## Quick start

Requirements: Python 3.11 (the version developed and tested with; older versions are untested), and **either** `gfortran` **or** Docker (only to build the Fortran
library once; a prebuilt Windows DLL is included in `simulator/tep/fortran/lib/`).

```bash
# 1. install
python -m pip install -r requirements.txt

# 2. build the authoritative Fortran TEP library (skipped if already built;
#    uses local gfortran, else Docker - cross-compiles a DLL on Windows)
python scripts/build_fortran.py

# 3. run: builds if needed, starts API + UI, opens http://127.0.0.1:8000/
python run.py

# 4. test (~1 minute)
python -m pytest

# 5. demonstration scenario, headless, with timeline + JSON/CSV export to exports/
python scripts/run_demo.py
```

The application starts with one command: `python run.py` (options: `--port`, `--scenario`,
`--backend auto|fortran|python`, `--no-browser`). API docs: `http://127.0.0.1:8000/docs`.

Docker alternative (Linux container, native gfortran build; **not yet built or tested**):
`docker build -t acme-tep . && docker run -p 8000:8000 acme-tep`.

If neither gfortran nor Docker is available, `run.py` falls back to the pure-Python **development**
backend with a warning; the Fortran backend is the authoritative validation backend.

## Running the demonstration in the UI

1. `python run.py` - the demonstration scenario `SCN-COOL-001` is loaded.
2. Choose a speed (e.g. 100x) and press **Start**; or press **Step** with *1 h* twice.
3. Observe (3 h scenario, fault at 1 h):
   * **01:00** hidden fault `F-COOL-001`: reactor cooling-water pump P-101A starts to degrade
     (Fault Injection tab shows it ACTIVE; nothing tells the operator directly).
   * P-101A health/vibration (Maintenance tab, details panel), reactor CW utility becomes DEGRADED.
   * The native TC-RCW/TC-RX cascade opens **XMV(10)** until it saturates at 100 % (schematic valve turns
     amber, OUTH-L10 alarm), UT-CW-REACTOR is CONSTRAINED.
   * **Reactor temperature XMEAS(9)** rises to ~138 °C (TAH-09 alarm) — computed by TEFUNC.
   * Vibration alarm -> corrective work order -> technician dispatch -> planned changeover to standby
     pump P-101B -> capacity restored -> recovery with the native controller's wind-up undershoot
     (still incomplete at 3:00: reactor at 118.7 °C against a 120.4 °C setpoint).
   * Product analyzer composition shifts -> **G_MASS_PCT** out of spec -> lot **QUARANTINE**.
   * P-101A repaired, returns as standby; fault shows as remediated.
4. Export the run with **⭳ JSON / ⭳ CSV**.

`python scripts/run_demo.py` prints the same chain as an event timeline with correlation ids.

## What is where

| Path | Content |
|---|---|
| `simulator/tep/` | `TEPProcessAdapter`, Fortran (ctypes) and Python backends, variable catalog, native control scheme, boundary registry, unmodified Fortran sources |
| `simulator/<module>/` | enterprise modules: isa95, enterprise, equipment, utilities, maintenance, materials, inventory, warehouse, quality, production, scheduling, faults, coupling, alarms, events, operator, simulation, state, scenarios |
| `api/` | `SimulatorService` (operations), `BenchmarkFaultAPI` (fault injection), FastAPI app, export |
| `ui/` | web UI (plain HTML/CSS/JS modules, no build step) |
| `configs/` | site hierarchy, TEP mapping, equipment, utilities, **coupling (causal model)**, materials, production, quality, maintenance, alarms, warehouse, simulation |
| `scenarios/` | `SCN-COOL-001` (demo), `SCN-BASELINE`, `SCN-FAULT-LIBRARY` (example faults of every type) |
| `scripts/` | `build_fortran.py`, `run_demo.py`, `generate_docs_tables.py`, `build_variable_graph.py`, `check_docs.py` |
| `tests/` | pytest suite covering the 15 validation requirements |
| `docs/` | documentation (below) |

## Documentation

* **[docs/README.md - read this first](docs/README.md)**: reading order and the 10-minute mental model
* **[Master simulator specification](docs/MASTER_SIMULATOR_SPEC.md)**
* [Learning guide](docs/LEARNING_GUIDE.md): ten levels, from "what is this" to limitations
* [Worked demo, step by step](docs/06_scenarios/scenario_examples/SCN-COOL-001.md)
* [The TEP boundary contract](docs/04_coupling/coupling_contract.md)
* [Known limitations](docs/11_limitations/known_limitations.md) · [benchmark / ground-truth limitations](docs/11_limitations/benchmark_limitations.md)
* [Documentation audit](docs/DOCUMENTATION_AUDIT.md)

## Key guarantees

* The TEP Fortran code is compiled **unmodified**; enterprise effects reach it only through documented
  boundary parameters (valve hydraulic ranges, compressor capacity, supply-temperature and
  feed-composition means, feed impurities). No measurement or process state is overwritten.
* A healthy enterprise simulation is **bit-identical** to a bare native TEP run with the same seed
  (tested).
* Same code + TEP library + seed + scenario + configuration ⇒ identical trajectories and event trace
  (tested on the first 2 h of the demo). Not guaranteed across compiled libraries or platforms. Every run has a manifest (run id, seeds, TEP source/library hashes,
  simulator version, configuration hash).
* Fault *control* is only reachable via `/api/benchmark/*` (`BenchmarkFaultAPI`), and `/api/events`
  never returns fault events. **Other ground truth is still exposed on operational routes**
  (correlation ids, equipment health, boundary values, true measurements in trends, exports, scenario
  files); see [benchmark limitations](docs/11_limitations/benchmark_limitations.md). Do not connect
  a system under test to the current API.

## Known limitations

* Utility quantities that TEP does not model (CW header pressure, steam pressure/temperature, bus
  voltage) are enterprise-layer approximations; only their documented boundary effects reach TEP.
* Agitator condition is not coupled (TEP's agitator term cannot represent a weakened/stopped drive).
* After an interlock trip TEP freezes; the simulator treats the plant as down until reset (no restart
  sequence).
* The trend buffer is a bounded in-memory visualisation buffer (10 s sampling), not a historian.
* The prebuilt DLL targets Windows x64; on Linux/macOS run `python scripts/build_fortran.py` (untested there).
* Full list: [known limitations](docs/11_limitations/known_limitations.md).

## Licenses / attribution

`simulator/tep/fortran/src/teprob.f` and `temain_mod.f`: J.J. Downs & E.F. Vogel (Tennessee Eastman
Company) and E.L. Russell, L.H. Chiang, R.D. Braatz (University of Illinois); see
`simulator/tep/fortran/src/LICENSE.camaramm`. `simulator/tep/vendor/tep_python_backend.py`: John
Kitchin, BSD-3-Clause (`simulator/tep/vendor/LICENSE.jkitchin`).
