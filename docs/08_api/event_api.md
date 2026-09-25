# Event API

## `GET /api/events` (operational stream)

| Query | Meaning |
|---|---|
| `types=A,B` | filter by event type names |
| `since=<s>` | simulation_time ≥ s |
| `target=<id>` | exact target |
| `after_id=OE-…` | cursor: only `OE-` events after this id (lifecycle `LC-` events are always included) |
| `limit` (≤ 20000, default 500) | keep the **last** `limit` matches |

The operational stream is derived from the canonical event log by `api/operational.py`
([canonical contract §16](../CANONICAL_SIMULATOR_CONTRACT.md#16-events)):

* **Withheld:**
  * `FAULT_*`;
  * `UTILITY_STATE_CHANGED` and `EQUIPMENT_DEGRADED`, which record changes of model-internal state;
  * `EQUIPMENT_STATE_CHANGED` between RUNNING and DEGRADED.
* **Ids:** simulation events are renumbered `OE-nnnnnnn` over the operational stream, so there are
  no gaps. `LC-` ids are unchanged.
* **Correlation:** `causation_id` and `correlation_id` come from operational causation only. For
  example, a work order points to the alarm that raised it; nothing points to a fault.
* **Payloads:** health values, fault ids, the fault-vs-wear failure reason and scenario identity are
  removed.

Response: a list of `event_id, timestamp, simulation_time, type, source, target, payload,
correlation_id, causation_id, severity`.

## `GET /api/benchmark/events` (evaluator)

The canonical events (`Event.to_dict()`, with `EV-` ids and true correlation, e.g. `F-COOL-001`).
Each carries `operational_id`: its `OE-`/`LC-` id in the operational stream, or `null` if that
stream withholds it. Same filters; `include_benchmark=true` adds `FAULT_*` events. The web UI
(benchmark console) uses this route and `GET /api/benchmark/ui/snapshot?after_event=<last EV id>`.

## Benchmark events and exports

* **Ground truth:** `GET /api/benchmark/ground-truth?limit=` returns the `visibility=benchmark`
  events, plus cause channels, the causal registry and sensor overlays ([benchmark API](benchmark_api.md)).
* **Exports:** `/api/benchmark/export/json` and `/api/benchmark/export/csv` (`events.csv`) include all
  canonical events ([exports](../10_operation/exports.md)).

Source:
- `api/operational.py` — `OperationalEventView`
- `api/service.py` — `SimulatorService.get_events`
- `simulator/events/__init__.py` — `EventBus.query`
- `tests/test_operational_boundary.py`
