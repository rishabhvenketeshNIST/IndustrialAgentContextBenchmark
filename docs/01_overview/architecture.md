# Architecture

This page traces how the system is assembled and how a simulation evolves, as implemented. The
meaning of what it produces is defined in the [canonical contract](../CANONICAL_SIMULATOR_CONTRACT.md).

## 1. Overall architecture

```mermaid
flowchart TB
  subgraph PY["Python process (one per server)"]
    subgraph API["api/"]
      SVC["SimulatorService<br/>(operational ops)"]
      BEN["BenchmarkFaultAPI<br/>(fault injection, ground truth)"]
      REST["FastAPI app<br/>/api/* and /api/benchmark/*"]
      RUN["Runner thread<br/>(real-time pacing)"]
    end
    subgraph ENG["SimulationEngine (simulator/simulation/engine.py)"]
      CLK["SimulationClock"]
      BUS{{"EventBus"}}
      ST[("CanonicalState")]
      PI["ProcessInterface"]
      INS["Instrumentation"]
      MODS["Modules: faults, operator, equipment, maintenance,<br/>inventory, scheduling, coupling | utilities, production,<br/>quality, warehouse, alarms, materials"]
      HIST["TrendBuffer"]
    end
    ADP["TEPProcessAdapter<br/>(Fortran or Python backend)"]
  end
  DLL[["tep_fortran.dll / .so<br/>teprob.f + temain_mod.f"]]
  UI["Browser UI (ui/)"]
  UI -- "HTTP polling" --> REST
  REST --> SVC & BEN
  RUN --> ENG
  SVC --> ENG
  BEN --> MODS
  MODS <--> ST
  MODS <--> BUS
  MODS --> PI
  PI --> INS
  PI --> ADP --> DLL
  ENG --> HIST
```

**Layers and what they own:**

| Layer | Owns | Must not |
|---|---|---|
| TEP (Fortran) | process states, measurements, controller internals | — (never modified) |
| `TEPProcessAdapter` | calling TEP correctly: init, stepping, control schedule, boundary access, shutdown detection | contain enterprise logic |
| `ProcessInterface` | the enterprise side's only access to the adapter; syncs the process image | compute physics |
| Modules | their own enterprise state | reach into other modules' internals |
| `CanonicalState` | the in-memory truth for everything | — |
| `SimulatorService` / REST | operational API | fault injection (that is `BenchmarkFaultAPI`) |
| UI | presentation | hold simulation state |

## 2. Startup sequence

`python run.py` runs `run.main`:

```mermaid
sequenceDiagram
  participant U as User
  participant R as run.py
  participant B as scripts/build_fortran.py
  participant A as api/app.py create_app
  participant S as SimulatorService
  participant E as SimulationEngine
  U->>R: python run.py
  R->>R: check_deps (numpy, yaml, fastapi, uvicorn, pydantic)
  R->>R: ensure_fortran: is the library present?
  alt library missing
    R->>B: build (local gfortran, else Docker)
    B-->>R: ok / failed → fallback to python backend (warning)
  end
  R->>A: create_app(autoload=SCN-COOL-001)
  A->>S: SimulatorService() (starts Runner thread, idle)
  A->>S: create_simulation("SCN-COOL-001")
  S->>E: SimulationEngine(scenario)
  Note over E: _build(): see below
  R->>R: uvicorn.run(app, 127.0.0.1:8000), open browser
```

`SimulationEngine._build()` does this, in order:

1. Validate the scenario and merge `config_overrides` over `configs/*.yaml` (`assemble_config`), then
   hash the result (`configuration_hash`).
2. Create the clock from `simulation_start`, load the ISA-95 hierarchy (`Hierarchy.from_config`) and
   the TEP mapping (`TEPMapping.from_config`). Both validate and fail fast.
3. Create `CanonicalState`, `EventBus`, `RandomStreams(seed)`, `Instrumentation` and `ProcessInterface`.
4. Register every ISA-95 element as an entity (`_register_hierarchy`).
5. Derive the TEP seed (`RandomStreams.tep_seed`, unless the scenario sets `tep_seed`) and initialize
   TEP (`ProcessInterface.initialize`). This zeroes the Fortran common blocks, calls TEINIT, sets the
   seed, loads the native controller parameters and records the TEINIT boundary values as "nominal".
6. Construct the modules and register operator commands.
7. Call `setup()` on each module in this order: equipment, utilities, materials, inventory, warehouse,
   production, scheduling, quality, maintenance, alarms, coupling (validates relations and evaluates
   them once to record baselines), faults (creates scenario faults), operator.
8. Build the trend buffer (166 series), run a t = 0 observation pass (utilities, warehouse, alarms) and
   record the first trend sample. Status becomes `READY`.

## 3. Simulation step lifecycle

The step is identical whether driven by the UI runner, `step_simulation`, `run_until` or a test.

```mermaid
flowchart TB
  A["t = clock.time_s"] --> F["FaultEngine.pre_step:<br/>start scheduled/event faults, apply progression,<br/>stop on duration"]
  F --> O["OperatorModule.pre_step:<br/>scripted operator actions due"]
  O --> EQ["EquipmentModule.pre_step:<br/>wear, status, vibration, redundancy changeover"]
  EQ --> MT["MaintenanceModule.pre_step:<br/>planned requests, technician shifts, schedule/start/complete WOs"]
  MT --> IN["InventoryModule.pre_step:<br/>stock loss, deliveries, supply availability, lot attributes"]
  IN --> SC["SchedulingModule.pre_step:<br/>release / block / sequence orders"]
  SC --> CP["CouplingEngine.pre_step:<br/>evaluate 67 relations, write boundary params,<br/>propagate causal labels"]
  CP --> TEP["ProcessInterface.step → adapter.step(1):<br/>due CONTRLn on transmitted PVs → INTGTR → CONSHAND"]
  TEP --> CL["clock.advance(1)"]
  CL --> SY["ProcessInterface.sync:<br/>true + transmitted XMEAS, XMV, setpoints, loops,<br/>analyzer updates, shutdown, equipment properties"]
  SY --> SD{"newly shut down?"}
  SD -- yes --> PSD["publish PROCESS_SHUTDOWN"]
  SD -- no --> POST
  PSD --> POST["post_step(t+1): utilities → inventory (consumption) →<br/>production → quality → warehouse → alarms"]
  POST --> H["TrendBuffer.record (every 10 s)"]
```

Consequences of this order that matter when reading results:

* **One-second lag in coupling observations.** The coupling engine runs before the TEP step and reads
  the process image of the previous step. For example, `UT-CW-REACTOR.flow` is computed from last
  second's XMV(10).
* **Enterprise changes take effect in the same step.** A boundary parameter written in `pre_step(t)` is
  used by TEFUNC in the integration from t to t+1.
* **Events carry the time of the phase that raised them.** Pre-step events get time t and post-step
  events get time t+1.

## 4. End-to-end causal flow (as implemented)

The conceptual flow "fault → equipment → capability → coupling → TEP → measurements → control → XMV →
enterprise interpretation → production/quality/maintenance" is broadly right. The implementation
differs from it in five ways:

```mermaid
flowchart TB
  FLT["Fault (benchmark)"] -->|"cause channel<br/>(FaultEffects)"| ES["Enterprise state<br/>health / capacity_loss / stock / lot deviation"]
  FLT -->|"IDV toggle (TEP-native faults)"| TEPB
  FLT -->|"sensor overlay"| INS["Instrumentation"]
  ES -->|"module logic"| CAP["Status / is_running / supply availability"]
  ES -->|"coupling relations"| CAP2["Capability: efficiency, available flow,<br/>utility capacity fraction, supply temperature"]
  CAP --> CAP2
  CAP2 -->|"tep_boundary relations"| TEPB[["TEP boundary<br/>VRNG, CPFLMX, SZERO, XST (+ IDV)"]]
  TEPB --> PHY["TEP physics (TEFUNC)"]
  PHY --> XT["XMEAS true"]
  XT --> INS
  INS -->|"transmitted PVs, swapped into /PV/ only during CONTRLn"| CTL["Native control (CONTRLn)"]
  CTL --> XMV["XMV"] --> PHY
  INS --> OBS["Transmitted XMEAS<br/>(canonical process image)"]
  OBS --> ENTI["Enterprise interpretation: alarms, quality tests,<br/>production metering, utility observations"]
  XT -->|"true values"| INV["Inventory consumption"]
  ENTI --> EV["Events"]
  ENTI -->|"alarm → work order"| MNT["Maintenance"]
  MNT -->|"repair, changeover"| ES
```

How it differs from the conceptual flow:

1. **Three separate entry points into TEP.** Enterprise faults go through coupling, TEP-native faults
   toggle IDV directly, and sensor faults act through the controllers without changing physics.
2. **Capability comes partly from module logic.** For example, a FAILED status sets `is_running = 0`
   in `EquipmentModule`, and a relation reads `is_running`.
3. **Two views of the measurements.** The enterprise layer reads *transmitted* values for alarms,
   quality, production and utility observations, but uses *true* feed flows for inventory consumption
   (`InventoryModule.post_step`).
4. **Maintenance closes a loop** back into enterprise state. It never acts on TEP directly.
5. **XMV is not interpreted by enterprise modules except in coupling observations.** Examples are the
   utility flow and power-demand relations, and saturation alarms on control-module outputs.

## 5. API and UI relationship

```mermaid
flowchart LR
  UI["ui/js/app.js<br/>poll every 1 s"] -->|"GET /api/ui/snapshot"| APP["api/app.py"]
  UI -->|"GET tab data every 3 s, trends every 2 s"| APP
  UI -->|"POST /api/simulation/*, /api/operator/*"| APP
  UI -->|"Fault Injection tab: /api/benchmark/*"| APP
  APP --> SVC["SimulatorService (lock)"]
  APP --> BEN["BenchmarkFaultAPI (lock)"]
  SVC --> ENG["SimulationEngine"]
  BEN --> ENG
```

The UI holds no simulation state; it renders what the API returns. Service methods that read engine
state take the same re-entrant lock as the runner, so they never see a half-finished step. The exception
is `get_simulation_state` (`GET /api/simulation`), which reads without the lock: a status poll can
observe values from two different steps. `/api/ui/snapshot` calls it inside the lock.

Source:
- `run.py` — `main`, `ensure_fortran`, `check_deps`
- `api/app.py` — `create_app`
- `api/service.py` — `SimulatorService`, `Runner`
- `simulator/simulation/engine.py` — `SimulationEngine._build`, `SimulationEngine._step_once`, `SimulationEngine._register_commands`
- `simulator/simulation/process_interface.py` — `ProcessInterface.initialize`, `ProcessInterface.sync`
- `simulator/tep/interface.py` — `BaseTEPAdapter.step`
