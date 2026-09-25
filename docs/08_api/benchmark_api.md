# Benchmark API (fault injection and ground truth)

Benchmark-operator only. It must never be handed to an agent interface. In Python it is a separate
object, `BenchmarkFaultAPI(service)`; over REST it lives under `/api/benchmark/`.

| Operation | REST | Notes |
|---|---|---|
| `catalog()` | `GET /api/benchmark/faults/catalog` | 34 types: category, description, persistence, gradual support, parameters, default observability and expected effects, **valid targets** |
| `list_faults()` | `GET /api/benchmark/faults` | every fault with status, intensity, history |
| `create_fault(spec)` | `POST /api/benchmark/faults` | validated; `simulation_time` triggers are scheduled at once |
| `get_fault_status(id)` | `GET /api/benchmark/faults/{id}` | — |
| `schedule_fault(id, start_time)` | `POST /api/benchmark/faults/{id}/schedule` | `{start_time}` ≥ now |
| `start_fault(id)` | `POST /api/benchmark/faults/{id}/start` | from CREATED, SCHEDULED or STOPPED |
| `stop_fault(id)` | `POST /api/benchmark/faults/{id}/stop` | ACTIVE only |
| `reset_fault(id)` | `POST /api/benchmark/faults/{id}/reset` | any status except RESET |
| `ground_truth_events(limit)` + `fault_effects()` | `GET /api/benchmark/ground-truth` | `{events: [benchmark events], effects: {channels, causal, sensor_overlays}}` |

Validation failures return 400, for example:
* an unknown type or target;
* severity outside [0, 1], or ≠ 1 for IDV;
* gradual progression on a binary type;
* a missing start time;
* a fault triggered by a fault event;
* a duplicate id.

## Separation: what it does and does not guarantee

**Guaranteed** (`test_fault_injection_is_benchmark_only`):
* No non-benchmark route can create, start, stop or reset a fault. The OpenAPI schema has no
  `/api/fault*` paths.
* `/api/events` never returns FAULT_* events.

**Also guaranteed** since contract 0.2.0 (`tests/test_operational_boundary.py`): no operational
route carries ground truth. Truth-bearing views, used by an evaluator and by the web UI, are here:

* `GET /api/benchmark/simulation` and `/manifest`: scenario identity, faults, seeds;
* `/events`: canonical events with true correlation and `operational_id`;
* `/entities/{id}`, `/utilities`, `/maintenance`, `/inventory`: all properties;
* `/process/image`: true XMEAS, IDV, boundary;
* `/process/internal-states`, `/coupling`, `/history` and `/history/catalog`;
* `/scenarios*`, `/export/*` and `/ui/snapshot`.

See [benchmark limitations](../11_limitations/benchmark_limitations.md). There is no authentication:
a system under test must be given the operational routes only.

Source:
- `api/service.py` — `BenchmarkFaultAPI`
- `api/app.py` — `create_app`
- `simulator/faults/engine.py` — `FaultEngine.catalog`
