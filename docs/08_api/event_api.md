# Event API

## `GET /api/events`

| Query | Meaning |
|---|---|
| `types=A,B` | filter by event type names |
| `since=<s>` | simulation_time ≥ s |
| `target=<id>` | exact target |
| `after_id=EV-…` | cursor: only `EV-` events after this id (lifecycle `LC-` events are always included) |
| `limit` (≤ 20000, default 500) | keep the **last** `limit` matches |

Always `include_benchmark=False`: `FAULT_*` events are never returned here. Operational events are
returned **with their `correlation_id` and `causation_id`**, which may name a fault
([event model](../05_state_and_events/event_model.md)).

Response: a list of `Event.to_dict()`: `event_id, timestamp, simulation_time, type, source, target,
payload, correlation_id, causation_id, severity, visibility`.

## Incremental polling (as the UI does)

`GET /api/ui/snapshot?after_event=<last EV id>` returns the new operational events (up to 300) together
with the rest of the snapshot.

## Benchmark events

`GET /api/benchmark/ground-truth?limit=` returns only `visibility=benchmark` events, plus cause
channels, the causal registry and sensor overlays ([benchmark API](benchmark_api.md)).

## Exports

`/api/export/json` and `/api/export/csv` (`events.csv`) include **all** events, benchmark ones included
([exports](../10_operation/exports.md)).

Source:
- `api/service.py` — `SimulatorService.get_events`
- `simulator/events/__init__.py` — `EventBus.query`
- `tests/test_api_ui.py` — `test_fault_injection_is_benchmark_only`
