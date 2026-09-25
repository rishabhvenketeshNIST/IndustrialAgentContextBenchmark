# API examples

## REST (server started with `python run.py`)

```bash
# create the baseline scenario for 1 h and advance 10 minutes
curl -X POST localhost:8000/api/simulation/create -H "Content-Type: application/json" \
     -d '{"scenario_id": "SCN-BASELINE", "duration_seconds": 3600}'
curl -X POST localhost:8000/api/simulation/step -H "Content-Type: application/json" -d '{"n": 600}'

# generic property access
curl localhost:8000/api/entities/EM-REACTOR/properties/temperature
curl localhost:8000/api/entities/UT-CW-REACTOR

# operator: raise the reactor temperature setpoint (TC-RX, loop 18)
curl -X POST localhost:8000/api/operator/set_setpoint -H "Content-Type: application/json" \
     -d '{"loop_id": 18, "value": 121.0}'

# benchmark: inject a hidden pump efficiency loss now
curl -X POST localhost:8000/api/benchmark/faults -H "Content-Type: application/json" \
     -d '{"id": "F-1", "type": "pump_efficiency_loss", "target": "WU-CWP-101A", "severity": 0.6,
          "progression": {"mode": "gradual", "ramp_s": 1200}}'
curl -X POST localhost:8000/api/benchmark/faults/F-1/start

# watch the consequences (operational view: what the plant can observe)
curl "localhost:8000/api/events?types=ALARM_ACTIVATED,MAINTENANCE_REQUESTED"
curl "localhost:8000/api/history?series=XMV(10)|XMEAS(21)|UT-CW-REACTOR.pressure"

# ground truth for the evaluator (benchmark routes)
curl "localhost:8000/api/benchmark/events?types=UTILITY_STATE_CHANGED,ALARM_ACTIVATED"
curl "localhost:8000/api/benchmark/history?series=UT-CW-REACTOR.capacity_fraction|WU-CWP-101A.health"
```

## Python without HTTP

```python
from api.service import SimulatorService, BenchmarkFaultAPI

svc = SimulatorService(start_runner=False)          # no background thread
svc.create_simulation("SCN-BASELINE", duration_seconds=7200)
bench = BenchmarkFaultAPI(svc)
bench.create_fault({"id": "F-IDV4", "type": "IDV4", "target": "TEP", "start_time": 1800, "duration": 1800})
svc.run_until(7200)
print(svc.get_property("EM-REACTOR", "temperature"))
print([e["type"] for e in svc.get_events(types=["ALARM_ACTIVATED"])])   # [] : the native loops absorb the +5 degC IDV(4) step
svc.shutdown()
```

## Engine directly (fastest, used by tests)

```python
from simulator.scenarios import ScenarioStore
from simulator.simulation.engine import SimulationEngine

e = SimulationEngine(ScenarioStore().load("SCN-COOL-001"))
e.run()                                   # 3 h in ~6 s (Fortran backend)
print(e.run_manifest()["run_id"], e.state.process.shutdown)
```

Source:
- `api/service.py` — `SimulatorService`, `BenchmarkFaultAPI`
- `simulator/simulation/engine.py` — `SimulationEngine.run`
