# Event model

All 46 event types, their emitters, observed counts and payload keys are generated in
[event_catalog.md](event_catalog.md). Normative event semantics (state vs event, visibility, time,
ordering): [canonical contract §16](../CANONICAL_SIMULATOR_CONTRACT.md#16-events).

## Event fields (`Event`, frozen dataclass)

| Field | Type | Meaning | Origin |
|---|---|---|---|
| `event_id` | string | `EV-nnnnnnn` (simulation events) or `LC-nnnnnnn` (lifecycle: STARTED, PAUSED, RESUMED, RESET), sequential | `EventBus.publish` |
| `timestamp` | ISO-8601 UTC string | `simulation_start + simulation_time`, never the wall clock | `SimulationClock.timestamp` |
| `simulation_time` | int s | clock time when published: t in pre-step, t+1 in post-step | clock |
| `type` | `EventType` | one of 46 | publisher |
| `source` | string | publishing module name (e.g. `equipment`, `alarms`), `benchmark.fault_engine`, `tep`, `simulation`, or the operator name | publisher |
| `target` | string or null | usually an entity id; order id, lot id or sample id for those records | publisher |
| `payload` | dict | type-specific | publisher |
| `correlation_id` | string | incident grouping: the fault id when a registered cause exists, the id of an explicit parent, or the event's own id | see below |
| `causation_id` | string or null | id of the immediate cause event | see below |
| `severity` | info / warning / critical | presentation hint | publisher |
| `visibility` | operational / benchmark | benchmark for FAULT_* types | `BENCHMARK_EVENT_TYPES` |

## How correlation and causation are assigned

There are three mechanisms, in order of precedence:

1. **Explicit `cause=` event.** A handler reacting to an event passes it on, and inherits its
   correlation and causation. Examples: `MaintenanceModule` from an alarm, the equipment module from a
   maintenance event, the warehouse from a lot decision.
2. **Causal registry lookup.** Publishers that are not reacting to a single event look up the
   `CausalRegistry` for their target (and related keys) and copy its `event_id` and `correlation_id`.
   Examples: utility status changes, equipment state changes, alarm activations, material shortages,
   production state changes, `PROCESS_SHUTDOWN`.
3. **Otherwise** the event's own id becomes its correlation id and `causation_id` is null.

**Example from the demo.** `FAULT_STARTED` (EV-…, correlation `F-COOL-001`) registers WU-CWP-101A.
The coupling engine propagates the cause to UT-CW-REACTOR and on to VRNG(10) → XMEAS(9), EM-REACTOR,
CM-TIC-RCW and others. Then:

* `UTILITY_STATE_CHANGED` (UT-CW-REACTOR) carries correlation `F-COOL-001`.
* `ALARM_ACTIVATED` TAH-09 carries `F-COOL-001`.
* `MAINTENANCE_REQUESTED` has cause = the vibration alarm event, so it also carries `F-COOL-001`.

**Correlation ids are ground truth.** A root-cause label on an operational event tells a reader what
caused it. This is intended for benchmark scoring. It is currently returned by the operational
`/api/events` ([benchmark limitations](../11_limitations/benchmark_limitations.md)).

## The bus (`EventBus`)

* **Synchronous:** `publish` appends to the log and calls the subscribers immediately, in subscription
  order. Handlers may publish further events. Depth is limited to 50 to catch loops.
* **Ordered, bounded log:** up to `event_log.max_events` (250,000, `configs/simulation.yaml`). The
  oldest events are dropped beyond that (`dropped` counter).
* **Deterministic:** ids are sequential, and lifecycle events use their own sequence so wall-clock
  pause/resume timing never shifts `EV-` ids.
* `query()` filters by type, time range, target, source, id cursor (`after_id`) and visibility.

## Sensor faults have no event of their own

No event is published when a sensor fault changes a transmitter. Sensor faults are visible only through
measurement quality and `BADPV` alarms.

Source:
- `simulator/events/__init__.py` — `Event`, `EventBus`, `EventBus.publish`, `EventBus.query`, `EventType`, `Visibility`
- `simulator/state/__init__.py` — `CausalRegistry`
- `tests/test_model.py` — `test_event_bus_fields_ids_and_causation`
