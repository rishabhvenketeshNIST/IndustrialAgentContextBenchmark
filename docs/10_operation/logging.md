# Logging

**Current implementation:** the simulator's record of what happened is the **event log**, not text
logs.

| Channel | What | Where |
|---|---|---|
| Event log | every simulation event (46 types), in order | `EventBus.log`, in memory; `/api/events`; exports |
| Python logging | `acme.sim`: "created simulation <run_id>" (INFO), step exceptions in the runner (with traceback) | `api/service.py`; configured by `run.py` at INFO |
| Uvicorn access logs | set to `warning` in `run.py`, so request logs are suppressed | `run.py` |
| Console | `run.py`, `scripts/run_demo.py` and `scripts/build_fortran.py` print progress | stdout |
| Trend buffer | 166 series every 10 s, ring buffer of 20,000 samples | in memory; `/api/history`; exports |

No log files are written, nothing is persisted automatically, and there is no historian. When the
runner catches a step exception it stops, and `get_simulation_state().error` reports it.

**Implementation status:** logging of important events to Python logging (as opposed to the event bus)
is minimal. Operators and tools should use the event API.

Source:
- `api/service.py` — `Runner.run`, `SimulatorService.create_simulation`
- `run.py` — `main`
- `simulator/simulation/history.py` — `TrendBuffer`
