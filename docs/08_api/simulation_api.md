# Simulation API

| Operation (`SimulatorService`) | REST | Body | Behaviour |
|---|---|---|---|
| `create_simulation(scenario_id, scenario, duration_seconds, seed, backend)` | `POST /api/simulation/create` | `{scenario_id? , scenario?, duration_seconds?, seed?, backend?}` | stops the runner and builds a new engine at t = 0 (status READY). Default scenario SCN-COOL-001. `TEP_BACKEND` env / `--backend` overrides the backend |
| `start_simulation()` | `POST /api/simulation/start` | — | READY → RUNNING, publishes SIMULATION_STARTED, runner starts. 409 if completed |
| `pause_simulation()` | `POST /api/simulation/pause` | — | stops the runner; status PAUSED; SIMULATION_PAUSED |
| `resume_simulation()` | `POST /api/simulation/resume` | — | starts if READY, otherwise SIMULATION_RESUMED; runner on |
| `reset_simulation(duration_seconds)` | `POST /api/simulation/reset` | `{duration_seconds?}` | new engine from the same scenario (same seed); SIMULATION_RESET is the first event of the new log |
| `step_simulation(n)` | `POST /api/simulation/step` | `{n: 1..86400}` | synchronous n seconds; runner off; status PAUSED unless completed |
| `run_until(time_s)` | `POST /api/simulation/run_until` | `{time_s}` | synchronous to min(time_s, duration) |
| `set_speed(speed)` | `POST /api/simulation/speed` | `{speed: 0.01..5000}` | real-time multiplier |
| `get_simulation_state()` | `GET /api/simulation` | — | status, running, speed, clock, duration, progress, manifest, shutdown, production summary, alarm counts, backends, scenario |
| run manifest | `GET /api/simulation/manifest` | — | `SimulationEngine.run_manifest()` |

## Real-time runner

`Runner` is a daemon thread that loops every ~20 ms. It computes `steps = elapsed_wall × speed + carry`,
caps them at `runner.max_steps_per_tick` (2000), and calls `engine.step(steps)` under the lock. It
stops at completion. Speed never changes results; it only changes how fast they arrive. At very high
speeds the requested rate may not be reached, and the run simply takes longer.

## Status values

`READY` (built, not started), `RUNNING`, `PAUSED`, `COMPLETED` (clock reached `duration_s`; further steps
do nothing). A process trip does **not** change the simulation status (see `process.shutdown`).

## Operator actions

`POST /api/operator/{action}` with a JSON body of parameters; `GET /api/operator/actions` lists them.
Each action publishes its specific event and then `OPERATOR_ACTION`. The same actions can be scripted in
a scenario's `operator_actions`.

| Action | Parameters | Effect |
|---|---|---|
| `set_setpoint` | `loop_id, value` | SETPT (refused for CAS loops) |
| `set_loop_mode` | `loop_id, mode (AUTO/CAS/MAN)` | loop mode; AUTO on a slave with an active master becomes CAS |
| `set_control_mode` | `mode (CLOSED_LOOP/MANUAL)` | plant mode |
| `set_xmv` | `index, value` | manual output (only when the owning loop is in MAN, or XMV 12) |
| `equipment_command` | `asset_id, state (RUN/STANDBY/STOP)` | asset desired state |
| `acknowledge_alarm` / `acknowledge_all` | `alarm_id` / — | alarm acknowledgement |
| `request_maintenance` | `asset_id, kind, priority, description` | work order |
| `cancel_work_order` | `wo_id` | cancel (not when IN_PROGRESS) |
| `order_command` | `order_id, command (release/pause/resume/cancel)` | order transition |
| `create_order` | `order{...}` | new production order |
| `order_material` | `storage_id, quantity_kg` | purchase order |

Source:
- `api/service.py` — `SimulatorService.create_simulation`, `SimulatorService.start_simulation`, `SimulatorService.pause_simulation`, `SimulatorService.resume_simulation`, `SimulatorService.reset_simulation`, `SimulatorService.step_simulation`, `SimulatorService.run_until`, `SimulatorService.set_speed`, `SimulatorService.operator_action`, `Runner.run`
- `simulator/simulation/engine.py` — `SimulationEngine._register_commands`
- `tests/test_api_ui.py` — `test_simulation_controls`, `test_operator_actions_and_validation`
