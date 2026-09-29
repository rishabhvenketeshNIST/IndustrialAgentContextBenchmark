# UNS MQTT semantics

This document describes what a UNS message means and how MQTT delivers it. The topics are described in
[UNS_MQTT_NAMESPACE.md](UNS_MQTT_NAMESPACE.md) and the overview is in [UNS.md](UNS.md). The code is in
[uns/publisher.py](../uns/publisher.py).

## Payload envelope

Every payload is one JSON object. It is canonical JSON: keys sorted, no whitespace, and NaN written as
`null`. Every manufacturing payload has this envelope:

| Field | Meaning |
|---|---|
| `schema` | payload schema version, `acme-uns/2` |
| `kind` | `meta`, `state`, `measurement`, `event` or `lifecycle` |
| `entity_id` | canonical id `<entity_type>:<native_id>`, unescaped: **the identity** |
| `entity_type` | the context-model entity type (the part of `entity_id` before `:`) |
| `operational_scope_id` | the simulation scope the message belongs to (`OS-` + 32 hex digits; see [Operational scope](#operational-scope-r-01)) |
| `simulation_time` | **manufacturing time:** simulated seconds since the simulation start |
| `simulation_timestamp` | the same instant as an ISO-8601 time on the simulated calendar |
| `observed_at` | **transport time:** wall-clock UTC time at which the UNS publisher published this copy |

Each kind adds its own fields:

| `kind` | Additional fields |
|---|---|
| `meta` | `native_id`, `isa95` {`concept`, `mapping`, `mapping_id`}, `parent` (canonical id or null), `name`, and, where they exist: `level`, `class`, `origin`, `description`, `attributes`, `children`, `supplied_by`/`serves` (utilities), `specification` (materials), `yields_material`/`bill_of_materials` (products), `configuration` (configuration-class properties), `measurements` (the list of measurement leaves) |
| `state` | `state` {property: value}, `units` {property: unit} (entities); `state` = the record's operational fields (records) |
| `measurement` | `variable`, `value`, `unit`, `source` (`"simulator"`). XMEAS measurements also carry `quality` and `tag`; loops carry `loop`, `loop_tag`, `pv` and, for `controller_output`, `output` |
| `event` | `event_id`, `event_type`, `source`, `severity`, `payload`, `causation_id`, `correlation_id`: exactly the operational event of [api/operational.py](../api/operational.py) |
| `lifecycle` | `status` (READY, RUNNING, PAUSED, COMPLETED), `simulation_start`, `duration_seconds` |

Example measurement (`operational_scope_id` and `observed_at` differ in every run):

```json
{"entity_id":"equipment_module:EM-REACTOR","entity_type":"equipment_module","kind":"measurement",
 "observed_at":"2026-09-29T14:03:12.418Z","operational_scope_id":"OS-9f2c41d07be35a6e8c1d24f09a7b3e51",
 "property":"temperature","quality":"GOOD","schema":"acme-uns/2","simulation_time":600,
 "simulation_timestamp":"2026-01-05T06:10:00Z","source":"simulator","tag":"TI-09","unit":"degC",
 "value":120.37495271223582,"variable":"measurement:XMEAS(9)"}
```

The publisher status topic (`<site>/uns/publisher`) has only `{kind: "publisher", schema, status}`. It
is information about the MQTT transport, not about the plant, so it has no manufacturing time and no
scope. One publisher can outlive several scopes.

**Schema history.** `acme-uns/1` (the first commit on this branch) had a field called `timestamp`. It
held the simulated-calendar time, but its name suggested a publication time. `acme-uns/2`:

- renames it `simulation_timestamp`;
- adds `observed_at` for the publication time;
- adds `operational_scope_id`.

The version is bumped because a field was renamed and two fields were added, which a consumer of
`acme-uns/1` must know about.

## Timestamps

There are two clocks, and only one of them is manufacturing time.

| Field | Clock | Authoritative for | May change on republication? |
|---|---|---|---|
| `simulation_time`, `simulation_timestamp` | the simulator clock | **all manufacturing reasoning**: when a value held, when an event happened, ordering | **No** |
| `observed_at` | the wall clock of the publisher host | transport diagnostics only: when this copy was sent | Yes |

The rules for `simulation_time` depend on the kind of message:

| Message | `simulation_time` is |
|---|---|
| event | the simulator time of the event (from the event log), never its publication time |
| measurement | the sampling tick at which the value was read (a multiple of the sampling period) |
| state | the simulation time at which the publisher first observed this state content in this scope |
| meta, lifecycle | the simulation time at which the publisher first published this content in this scope |

The simulator keeps no per-value change timestamps, so "first observed" is the most precise time
available for state. Because the publisher synchronises after every simulated second, it is exact to
the second, except for continuous-only record changes, which are sampled at the tick.

**Republication does not re-date.** The same message can be published again:

- after a broker restart (resync);
- by a new publisher instance attached to the same scope;
- as a QoS 1 redelivery.

In every case it keeps its `simulation_time` and `simulation_timestamp`; only `observed_at` changes.

A restarted publisher reads the retained messages already on the broker. For each retained message of
the *same* operational scope whose content is unchanged, it reuses the message's simulation time. It
also keeps continuing under the same report-by-exception rules:

- a measurement sampled at the last tick stays retained until the next tick;
- a continuous-only record change waits for the next tick.

**Wall-clock time never stands in for manufacturing time.** `observed_at` is not a manufacturing
fact:

- it is not deterministic;
- it depends on pacing and the host;
- a consumer must not order, correlate or reason about the plant with it.

When `run_uns.py` paces the run (`--speed > 0`), it publishes after the pacing sleep. `observed_at`
therefore follows the pacing schedule, not how long each step took to compute. At `--speed 0` it does
reflect compute throughput, which is a potential timing side channel. For that reason, an agent-facing
consumer of an unpaced run must ignore `observed_at`
([UNS_OPERATING_MODEL.md](UNS_OPERATING_MODEL.md#hidden-fault-non-disclosure)).

## Units

- Every measurement has a `unit`. XMEAS units come from the TEP catalog, XMV and controller outputs are
  `%`, and loop setpoints use the unit of the loop's process variable.
- Entity properties use the simulator's declared units. Entity state carries them in `units`.
- Units are strings exactly as the simulator declares them (`degC`, `kPa`, `kg/h`, `mm/s`, `%`, ...).
  An empty string means dimensionless.

## State semantics

- **Entity state** is the entity's *discrete* operational properties: status, mode, running flag, alarm
  state, and similar. It is published when any of them changes. The property classes of the canonical
  contract decide what counts as state, what counts as a measurement and what counts as meta.
- **Control-loop modules** also carry `mode` and `saturated`.
- **The production unit PU-STRIPPER** also carries `line_state`, `current_lot` and `current_order`.
- **Record state** is the full operational record: a production order, a lot, a work order, and so on.
  Changes to discrete fields (status, disposition, assignment) are published immediately. Records also
  hold continuously changing quantities (lot kilograms, stock, an order's projected end). A change to
  those alone is published at the next sampling tick (every `--measurement-period`, 10 s by default),
  so the record is not republished every second.
- **Report by exception:** a state topic is republished only when its content changes.
- Each state message is complete, not a delta. A subscriber replaces its view with it.

## Measurement semantics

- All measurements are sampled together at every sampling tick (`simulation_time % period == 0`,
  default 10 s) and published whether or not the value changed. The sample grid does not depend on the
  values, so the number of measurement messages carries no information.
- A measurement topic is retained, so it always holds the latest sample.
- A measurement's `value` is exactly what the simulator reports at that instant. Where a TEP
  measurement is sampled (analyzers), its `quality` field reports it.

## Event semantics

- Events are the simulator's **operational** event stream ([api/operational.py](../api/operational.py)):
  - `OE-nnnnnnn` ids for operational events;
  - `LC-nnnnnnn` for lifecycle events after a reset;
  - `causation_id` and `correlation_id` expressed in the same id space.
- Faults, evaluator events and hidden payload fields are excluded by the boundary, not by the UNS.
- One MQTT message per event, published under the event's target entity at `.../event/<EVENT_TYPE>`.
- Events are published in the order of the event stream, and the ids increase in that order.
- Events are **not retained**. A subscriber that was not connected does not receive past events (see
  persistent sessions under [Reconnect](#reconnect)). The event history belongs to a future Historian,
  not to the UNS.

## Retained state

| Topic | Retained | Why | Replaced when | At reset | At broker restart |
|---|---|---|---|---|---|
| `meta` | yes | a new subscriber needs the model | its content changes (e.g. a new measurement leaf) | republished for the new scope | republished by the publisher, same simulation time |
| entity `state` (equipment, production unit, storage) | yes | current state of the plant | any discrete property changes | republished | republished, same simulation time |
| record `state` (orders, lots, samples, work orders, spare parts, technicians, purchase orders, shipments, alarms) | yes | current production, maintenance, quality and material state | the record changes | **deleted** if the record does not exist in the new scope | republished, same simulation time |
| `measurement/<variable>` | yes | the latest value is current information | every sampling tick | republished | republished, same simulation time |
| `lifecycle` | yes | whether the simulation is running, and **which scope is current** | the status changes | republished (READY, new scope id) | republished, same simulation time |
| `<site>/uns/publisher` | yes | whether the retained data is live | the publisher connects or disconnects (last will) | unchanged | republished (`online`) |
| `event/<TYPE>` | **no** | an occurrence is not current state | never | not applicable | not applicable |

**How stale state is prevented:**

1. **At start-up,** the publisher reads the retained topics already under its root:
   - it reuses a topic of the current operational scope whose content is unchanged (a publisher
     restart);
   - at its first snapshot, it deletes every other topic that is not part of the current simulation,
     by publishing an empty retained message. This removes leftovers from an earlier run.
2. **At a reset** (a new scope), every retained topic of the old scope that is not in the new snapshot
   is deleted. An example is a quality sample that no longer exists. Every remaining topic is
   republished with the new scope id.
3. **When the publisher dies,** the broker publishes its last will, `uns/publisher = offline`
   (retained). A subscriber then knows that the retained data is no longer being updated.
4. **The broker does not persist** retained messages (`persistence false` in
   [uns/mosquitto.conf](../uns/mosquitto.conf)). A restarted broker is empty until the publisher
   reconnects and republishes, so the broker cannot serve state from a finished run.
5. **Each retained message names its scope.** Even if a stale message survived, a subscriber would
   recognise it: its `operational_scope_id` differs from the one in the retained `lifecycle`.

## MQTT QoS

| Traffic | QoS | Retain | Reason |
|---|---|---|---|
| measurements | 0 | yes | Periodic and superseded every tick. A lost sample is replaced by the next one. QoS 0 keeps the high-volume stream cheap. |
| entity and record state (equipment, production, storage/material, quality, maintenance) | 1 | yes | Must arrive. Duplicates are harmless because state is idempotent. |
| meta | 1 | yes | Same as state. |
| lifecycle | 1 | yes | Same as state. |
| events: alarms, lifecycle, production, maintenance, quality, material | 1 | no | Must arrive. Duplicates are recognisable by `event_id`. |
| publisher status and last will | 1 | yes | Must arrive. |

QoS 2 is not used. Every message is either idempotent (state) or carries its own identity (events), so
exactly-once delivery would cost a four-step handshake and would buy nothing. No MQTT 5 message expiry
is used. Stale retained state is handled explicitly, as described above.

**Sessions:**

- **The publisher** connects with a clean session and a fixed client id (`acme-uns-publisher`). It
  republishes everything after a reconnect ([Reconnect](#reconnect)).
- **Subscribers** choose for themselves. A subscriber that must not miss events uses a fixed client id,
  `clean_session=False` and a QoS 1 subscription. The broker then queues its QoS 1 messages while it is
  disconnected. The repository broker queues without limit (`max_queued_messages 0`).

## Duplicates

- **Duplicate state or measurement** (a QoS 1 redelivery, or a republish after a reconnect or a
  publisher restart): the content and the simulation time are the same, and applying the message again
  changes nothing. Only `observed_at` may differ.
- **Duplicate event:** `event_id` is unique within a scope, so `(operational_scope_id, event_id)` is
  unique across scopes. A subscriber that counts events de-duplicates on that pair and needs no
  reset-detection logic.
- **Repeated state with the same content** is never published by one publisher instance
  (report-by-exception).

## Ordering

| Scope | Guaranteed? |
|---|---|
| per topic | **Yes.** MQTT delivers the messages of one topic in order at one QoS level. The publisher emits each topic in simulation-time order. |
| events | **Yes, by content.** `event_id` increases in stream order and events share QoS 1, so a subscriber can also re-sort by `event_id`. |
| per entity (across its channels) | **No.** Its measurements (QoS 0) and state (QoS 1) may interleave differently from publication order. |
| per publisher / globally | **No.** Order across QoS levels, and the arrival order of messages from different topics, are not guaranteed by MQTT. |

The UNS does **not** add a global sequence number. It would not be MQTT-native, and a counter over all
messages would reveal how much the plant publishes, which is a possible disclosure channel. Subscribers
order by `simulation_time` (all kinds) and by `event_id` (events), within one `operational_scope_id`.

## Reconnect

- **Publisher to broker** (network drop, broker restart). The paho client reconnects automatically
  (0.1–2 s backoff). While it is disconnected:
  - events are buffered in order, and none are dropped;
  - state and measurements are not queued: only the latest desired retained value of each topic is
    kept.

  On reconnect, the publisher:
  1. publishes `online`;
  2. sends the buffered events in order;
  3. deletes topics that became stale in the meantime;
  4. republishes every retained topic, with the same content, scope and simulation time and a new
     `observed_at`.

  Because the broker is not persistent, this fully restores the retained state.
- **Publisher restart** (a new publisher instance for the same simulation). The scope id is the same,
  because it belongs to the simulation, not to the publisher. The new instance adopts the same-scope
  retained messages it finds:
  - it republishes the current snapshot with the same content and simulation time;
  - it deletes nothing that is still current;
  - it replays no past events.
- **Subscriber reconnect:**
  - with a clean session, it receives the current retained state (not events);
  - with a persistent session, it also receives the QoS 1 messages queued while it was away.

  The scope id is unchanged in both cases.

Reconnects add no manufacturing information:

- no content or simulation time changes;
- no counter advances;
- no scope changes;
- no reconnect is used as a run identifier.

The tests cover all of these cases (below).

## Lifecycle

The simulation lifecycle is visible in two forms:

1. **The retained `<site>/lifecycle` topic.** It holds the current status (READY, RUNNING, PAUSED or
   COMPLETED) and the current `operational_scope_id`.
2. **Lifecycle events** on `<site>/event/<TYPE>`:

| Event | When | Payload (operational) |
|---|---|---|
| `SIMULATION_STARTED` | the first event of a new simulation | `{duration_seconds}` (run, scenario and seed are removed by the boundary) |
| `SIMULATION_PAUSED` / `SIMULATION_RESUMED` | pause and resume through the service | `{time_s}` |
| `SIMULATION_RESET` | the first event of a reset simulation (`LC-0000001`) | `{}` (the scenario id is removed) |
| `SIMULATION_COMPLETED` | the end of the scenario duration | `{simulated_seconds}` (the run id is removed) |

## Operational scope (R-01)

**What it is.** An *operational scope* is one simulation, from its creation or reset until the next
one. It is exactly one simulator engine: the service builds a new engine on every create and every
reset. Record ids (`PL-0001`, `QS-00001`, ...) and event ids (`OE-`, `LC-`) are unique only within a
scope, and restart in the next one. The `operational_scope_id` names the scope, so that any message,
and in particular any retained message, can be attributed to its simulation.

**Where it comes from.** The id comes from the operational boundary,
`api.operational.operational_scope_id(engine)`, not from the MQTT publisher. It is `OS-` followed by
128 random bits (Python `secrets`). It is drawn when the scope is first observed and kept for the
lifetime of the engine. The simulator itself is unchanged: the id is held outside the engine, in a
weak-keyed map.

**What it does not represent.** It is deliberately **not** the simulator run id.

- **The run id is not used.** The run id (`RUN-<scenario>-<seed>-<config hash>`) names the benchmark
  case, and it stays hidden behind the boundary.
- **Nothing is derived from benchmark or run data.** The scope id is not derived from the run id, the
  scenario, the seed, the configuration, faults, evaluator data, time, or event counts. It is random, so
  there is nothing to reverse-engineer. Two runs of the *same* scenario, with the same run id, get
  different scope ids, and a scope id says nothing about what happens inside the scope. It identifies a
  simulation, not a benchmark case.
- **It is constant within a scope.** It is fixed before the first event and never changes. It
  therefore cannot encode fault onset, elapsed time or how many events occurred.

**How it changes:**

| Situation | Scope id |
|---|---|
| steps, pause, resume, completion | unchanged |
| publisher reconnect or publisher restart | unchanged (it belongs to the simulation, not the publisher) |
| broker restart | unchanged |
| subscriber reconnect | unchanged |
| simulation reset | **new** |
| new simulation created | **new** |
| simulator service restarted | **new** (the new process builds a new engine) |

**Retained-state semantics.** The id is in the envelope of every manufacturing message, retained or
not. The retained `lifecycle` topic states the current scope. A subscriber that connects at any time,
with no session and no history (for example, just after a reset it never saw), therefore:

1. reads the retained `<site>/lifecycle`, whose `operational_scope_id` is the current scope;
2. accepts retained messages that carry the same id.

The publisher guarantees that there are no others: old-scope topics are deleted or republished at a
reset. The id is carried per message rather than only in the lifecycle topic because MQTT retained
topics are independent, with no atomic snapshot. A per-message id makes every message self-describing,
even while a reset is being published.

**Live subscribers** see a scope change as a new `operational_scope_id`, together with
`SIMULATION_RESET` (`LC-0000001`) or `SIMULATION_STARTED`.

**Decision record.** This resolves R-01 ([CONTEXT_MODEL_DECISIONS.md](CONTEXT_MODEL_DECISIONS.md)).
The REST operational routes do not expose the scope id yet. Adding it there would be an additive API
change, left for when an operational REST consumer needs it.

## Determinism

For the same scenario and configuration, the UNS publishes the same topics. Each topic receives the
same sequence of payloads, byte for byte, **apart from two fields that differ by design**:

- `operational_scope_id`: a new random id per scope;
- `observed_at`: wall-clock time.

Everything else is identical: entities, states, events, event ids, simulation times and payload
semantics. It holds because:

- payloads are canonical JSON, and dictionary and topic iteration orders are sorted;
- the client id is fixed, and there are no other random ids, and no broker metadata, in payloads;
- measurement sampling follows simulation time, not wall time;
- deferred continuous changes are published at the next tick, whatever the pacing.

Only the interleaving *between* topics can vary with network timing, and MQTT does not guarantee it
anyway. `test_publication_is_deterministic` compares two runs through two brokers, topic by topic, with
the two by-design fields masked.

## Tests

[tests/test_uns.py](../tests/test_uns.py) runs every case against a real Mosquitto broker.

| Required test | Test |
|---|---|
| 1 broker startup, 2 connectivity, 3 publish, 4 subscribe | `test_broker_accepts_publish_and_subscribe` (and every broker test) |
| 5 namespace, 6 ISA-95 hierarchy, 7 canonical ids | `test_namespace_hierarchy_is_the_context_model_hierarchy`, `test_every_published_entity_is_canonical_and_in_its_isa95_place`, `test_topic_escaping_is_reversible_and_canonical_ids_are_preserved` |
| 8 retained state, 25 late-subscriber recovery | `test_late_subscriber_recovers_retained_state_and_no_events` |
| 9 measurements, 10 units, 11 timestamps, 14 state/event separation, 15 QoS | `test_state_measurement_and_event_channels_have_distinct_semantics`, `test_simulation_time_is_manufacturing_time_and_observed_at_is_wall_clock` |
| 12 events, 17 ordering | `test_events_are_the_operational_stream_in_order` |
| 13 lifecycle, 21 reset identity scope | `test_lifecycle_and_reset_begin_a_clean_identity_scope` |
| 16 duplicates, 18 publisher reconnect | `test_publisher_restart_keeps_scope_content_and_simulation_time` |
| 19 subscriber reconnect | `test_subscriber_reconnect_with_persistent_session_receives_queued_events` |
| 20 broker restart | `test_broker_restart_recovers_retained_state_and_buffered_events` |
| 22 operational boundary | `test_operational_boundary_holds_on_mqtt` |
| 23 hidden-fault non-disclosure | `test_hidden_fault_is_not_disclosed_before_the_first_symptom` |
| 24 deterministic publishing | `test_publication_is_deterministic` |
| 26 full 3-hour demo | `test_full_demo_over_mqtt_leaves_the_simulation_unchanged` |
| operational scope (R-01) | `test_operational_scope_id_belongs_to_the_simulation_scope`, `test_operational_scope_id_discloses_no_benchmark_information`, `test_retained_only_subscriber_identifies_the_current_scope`; scope assertions in the reconnect, broker-restart, reset and non-disclosure tests |
| broker security boundary | `test_broker_configuration_is_local_development_only_and_documented` |
