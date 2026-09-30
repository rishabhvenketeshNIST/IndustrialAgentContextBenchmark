# Local manufacturing stack

This page explains how to run the enterprise simulator, its web UI, the MQTT Unified Namespace (UNS)
and the UNS inspector, and optionally the Historian and its read-only API, together as **one simulation
seen through independent views**. It is a local benchmark and development stack; nothing here is a
production deployment.

```
                    ENTERPRISE SIMULATOR          run.py --uns [--historian PATH]: the only simulation
                           │
          ┌────────────────┼──────────────────┐
          ▼                ▼                  ▼
   SIMULATOR WEB UI   UNS PUBLISHER     HISTORIAN WRITER    all follow the same SimulatorService
                           │                  │            (the Historian only with --historian)
                           ▼                  ▼
                      MQTT BROKER        SQLite (WAL)       Eclipse Mosquitto (uns/mosquitto.conf)
                           │                  │
                           ▼                  ▼
                      UNS INSPECTOR     HISTORIAN API       an MQTT client only; a read-only reader
```

- **The simulator is the source of truth.** The web server (`run.py`) owns one `SimulatorService` and
  therefore one simulation engine.
- **The simulator web UI** is that server's own interface, the benchmark operator's console. It reads
  the simulator API, and it also shows benchmark-console information such as the scenario, the seed
  and the fault panel.
- **The UNS publisher** runs inside the same server and follows the same service. It never creates or
  steps a simulation. It publishes the operational projection to MQTT
  ([UNS.md](UNS.md)).
- **The UNS inspector** is a separate process and an ordinary MQTT client. It shows only what the
  broker delivers, and never calls the simulator API. It shows what an operational consumer, such as
  a future agent, could see.
- **The Historian writer** (with `--historian PATH`) is a second observer of the same service. It
  records the operational projection into the SQLite file, never creates or steps a simulation, and
  does not use MQTT ([HISTORIAN.md](HISTORIAN.md)).
- **The Historian API** is a separate, read-only process over that same file
  ([HISTORIAN_QUERY_API.md](HISTORIAN_QUERY_API.md)). It serves the current scope only; evaluator
  access is never started by the stack.

The two views are deliberately different. Comparing them shows what the operational boundary removes.

## Start the stack

```bash
python scripts/run_manufacturing_stack.py --scenario SCN-COOL-001 --speed 10 --start-broker
```

| Service | Address |
|---|---|
| simulator web UI and API | http://127.0.0.1:8000 (API docs at `/docs`) |
| UNS inspector | http://127.0.0.1:8050 |
| MQTT broker | `mqtt://127.0.0.1:1883`, topics `uns/v1/#` |
| Historian API (with `--historian PATH`) | http://127.0.0.1:8060/status (read-only, current scope) |

**With the Historian:**

```bash
python scripts/run_manufacturing_stack.py --scenario SCN-COOL-001 --speed 10 --start-broker \
    --historian exports/session.sqlite
```

The simulator records into `exports/session.sqlite`, and the Historian API serves that same file.
The file is created, or appended to with a new scope. `exports/` and `*.sqlite` are ignored by git.
Without `--historian`, the stack is exactly as before.

The launcher starts, in order:

1. **the broker** (`--start-broker`, the repository configuration). Without that flag it uses a broker
   already listening on `--mqtt-host`/`--mqtt-port`, and stops with a clear message if there is none.
2. **the simulator server** with its UNS publisher, and with `--historian` its Historian writer:
   `python run.py --uns [--historian PATH] ...`;
3. with `--historian`, **the Historian API** over the same file:
   `python scripts/run_historian_api.py --database PATH ...`;
4. **the inspector**: `python scripts/run_inspector.py ...`.

It waits for each one to be ready, not for a fixed time:

- the simulator is ready when `/api/uns/status` reports the publisher connected;
- the Historian API is ready when its `/status` reports the simulator's operational scope as current;
- the inspector is ready when it reports the same operational scope as the simulator.

It then prints the URLs and the scope id and opens both pages (unless `--no-browser`). The scenario is
loaded in READY state: press **▶ Start** in the simulator UI.

**Ctrl-C stops everything it started:** the inspector, then the Historian API, then the simulator,
then the broker. When the simulator stops, the publisher's MQTT last will marks the UNS offline, and
the Historian writer commits and closes the file, so nothing recorded is lost. If any part exits unexpectedly, the launcher reports it
and stops the rest.

**No orphaned processes.**

- **Windows:** the launcher places itself and its children in a kill-on-close job object. Even if the
  launcher itself is killed, or its console window is closed, Windows terminates the broker, simulator
  and inspector with it.
- **POSIX:** the children share the launcher's process group, so Ctrl-C or a terminal hang-up reaches
  all of them.

Options:

| Option | Default | Purpose |
|---|---|---|
| `--scenario` | `SCN-COOL-001` | scenario loaded by the simulator |
| `--speed` | scenario default (10) | simulated seconds per wall-clock second |
| `--port` | 8000 | simulator web UI port (the existing `run.py` default) |
| `--inspector-port` | 8050 | inspector port |
| `--mqtt-host`, `--mqtt-port` | `127.0.0.1`, 1883 | broker address |
| `--start-broker` | off | start a local Mosquitto from `uns/mosquitto.conf` |
| `--historian PATH` | off | also record into this Historian SQLite file and serve it read-only |
| `--historian-port` | 8060 | Historian API port |
| `--backend` | auto | TEP backend, passed to `run.py` |
| `--no-browser` | off | do not open the pages |

If another broker already holds port 1883 (for example a Mosquitto Windows service), either use it (omit
`--start-broker`) or choose another port, such as `--start-broker --mqtt-port 1884`.

## One simulation, one scope

The simulator server builds a new engine on every create and reset, and the UNS publisher follows
whichever engine the service holds. It is attached as an **observer** of the service
(`SimulatorService.add_observer`, [08_api/simulation_api.md](08_api/simulation_api.md)):

- **Real-time runner.** While the runner advances the simulation, it steps one simulated second at a
  time and calls the publisher after each second. `SimulationEngine.step(n)` is n one-second steps, so
  the simulation is identical.
- **Commands.** Every lifecycle or operator command notifies the publisher: create, start, pause,
  resume, reset, step, run-until, and operator actions. So PAUSED, RESUMED, a reset or an operator
  action reaches MQTT at once, even while the simulation is paused.

**The same simulation is shown by its operational scope id** (`OS-…`, R-01 in
[UNS_MQTT_SEMANTICS.md](UNS_MQTT_SEMANTICS.md#operational-scope-r-01)):

- the simulator UI header shows it (from `GET /api/uns/status`);
- the inspector header shows it (from the retained lifecycle on MQTT).

They are equal because both come from `api.operational.operational_scope_id` of the same engine. The id
names the simulation without revealing it: it is not the run id, scenario or seed.

| Action in the simulator UI | Simulator | UNS (retained lifecycle) | Inspector |
|---|---|---|---|
| ▶ Start | RUNNING | RUNNING | RUNNING |
| ❚❚ Pause | PAUSED | PAUSED (+ `SIMULATION_PAUSED`) | PAUSED |
| ▶ Resume | RUNNING | RUNNING (+ `SIMULATION_RESUMED`) | RUNNING |
| ⟲ Reset | READY, new engine | new scope: `SIMULATION_RESET`, old retained topics deleted | *NEW SIMULATION SCOPE* banner, new `OS-…` |
| Load (another scenario) | READY, new engine | new scope | new scope |
| completion | COMPLETED | COMPLETED | COMPLETED |
| publisher or broker restart | unchanged | same scope, retained state republished | reconnects, same scope |

## Moving between the views

- The simulator UI header shows:
  - **UNS CONNECTED · host:port** (or *UNS off* when `run.py` runs without `--uns`);
  - the **operational scope**;
  - an **UNS Inspector ↗** link. The link opens the inspector on the entity selected in the simulator
    UI, for example `…:8050/#equipment_module:EM-REACTOR`.
- The inspector header has a **Simulator UI ↗** link.

These are links only. The inspector never reads the simulator API, and the simulator UI never reads
MQTT.

## Side-by-side demonstration

1. Start the stack as above, open both pages, and press **▶ Start** in the simulator UI.
2. Select the reactor in the simulator UI (tree: `EM-REACTOR`) and follow **UNS Inspector ↗**. The
   inspector opens on `equipment_module:EM-REACTOR`.
   - The simulator shows its process view.
   - The inspector shows the same entity as ISA-95/MQTT: its lineage (Enterprise › Site › Area ›
     Production Unit › Equipment Module), measurements such as `measurement:XMEAS(9)` with unit and
     simulation time, records, and the actual topics.
3. Check that it is the same simulation:
   - the **scope ids match**;
   - the **lifecycles match**;
   - the measurement values agree at the sampling ticks (every 10 simulated seconds).
4. Let SCN-COOL-001 run.
   - **Before 01:20:33** the inspector shows normal operation. The drift of pump vibration and reactor
     temperature is visible only as measurement values: no fault, scenario, seed, cause or utility
     health appears.
   - The simulator UI, being the benchmark console, can show the scenario and its fault panel. That is
     the difference between the two views.
5. **At 01:20:33** both show the vibration alarm on `WU-CWP-101A`, then the maintenance request and
   work order. Later they show the failed quality sample and the quarantined lot.
6. **Pause** in the simulator UI: the inspector shows PAUSED. **Resume**: RUNNING.
7. **Reset** in the simulator UI:
   - the simulator returns to READY;
   - the inspector shows the *NEW SIMULATION SCOPE* banner with the new `OS-…`, which matches the
     simulator UI;
   - the old records are gone from the inspector.

   Press **Start**, and all three views are RUNNING in the new scope.

## The Historian in the stack

**Every view shows the same scope.** The simulator UI (`/api/uns/status`), the UNS inspector (from
the retained lifecycle) and the Historian API (`/status` → `current_scope`) all report the same
`operational_scope_id`.

| Action | Historian |
|---|---|
| ▶ Start, ❚❚ Pause, ▶ Resume, completion | recorded as `lifecycle_status` (RUNNING, PAUSED, RUNNING, COMPLETED) and the lifecycle events |
| ⟲ Reset, or Load | a new scope in the same file. The API's current scope follows it, and the previous scope is kept, readable with evaluator access only |
| MQTT broker stopped or restarted | nothing: the Historian does not use MQTT |

- **Current versus evaluator access.** The stack's Historian API always runs with the default
  `current` access. For the evaluator, start a separate service on the file:

  ```bash
  python scripts/run_historian_api.py --database exports/session.sqlite --access evaluator --port 8061
  ```

  Do not give that service to an agent.
- **One simulation.** A test counts the simulation engines created while the stack runs: one per
  scope. The UNS publisher and the Historian writer both follow that engine.
- **Observational only.** A full 3-hour run with the Historian writer and API attached has the same
  event log, the same final TEP states and exactly the same UNS publish calls as a run without them.

## Standalone operation

Every part still runs on its own:

```bash
mosquitto -c uns/mosquitto.conf                                  # broker
python run.py                                                    # simulator UI without UNS (as before)
python run.py --uns --mqtt-port 1883                             # simulator UI publishing to the UNS
python scripts/run_uns.py --scenario SCN-COOL-001 --speed 10     # UNS without the web server
python scripts/run_inspector.py --mqtt-port 1883                 # inspector against any UNS broker
python run.py --historian exports/session.sqlite                 # simulator UI recording a Historian file
python scripts/record_history.py --db exports/run.sqlite         # headless recording (its own simulation)
python scripts/run_historian_api.py --database exports/run.sqlite  # read-only API over any Historian file
```

`scripts/run_uns.py` creates and steps its own simulation, with no web server. **Do not combine it with
`run.py --uns` on the same broker:** two simulations would then publish into one namespace. Use one
publisher per broker and topic root.

## Security

This is a local benchmark and development stack:

- the broker listens on `127.0.0.1` with anonymous access, and has no authentication, authorization or
  TLS ([UNS_OPERATING_MODEL.md](UNS_OPERATING_MODEL.md#broker-security));
- the simulator UI and the inspector are served on `127.0.0.1` without authentication.

Do not expose these ports on a network. Production security is outside the scope of the benchmark.

## Notes

- **`run.py` start-up checks.** `run.py` now checks that its web port is free *before* building the
  simulation, and exits with code 3 and a clear message if it is not. Before, the scenario was loaded
  and the URLs printed, and only then did uvicorn fail to bind.
- **The earlier exit code 4.** One earlier `run.py` session ended with exit code 4 after running for
  days. This was not a `run.py` failure: no code in the repository exits with 4, and a port conflict
  exits with 3. It coincided with the host session that ran it being torn down.
- **Throughput.** With the publisher attached, each simulated second is published, which costs a few
  milliseconds. Very high speeds (for example 2000×) are therefore capped by publishing throughput. The
  results are unchanged; the run just takes longer.
