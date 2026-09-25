# UNS MQTT semantics

This document describes what a UNS message means and how MQTT delivers it. The topics are described in
[UNS_MQTT_NAMESPACE.md](UNS_MQTT_NAMESPACE.md) and the overview is in [UNS.md](UNS.md). The code is in
[uns/publisher.py](../uns/publisher.py).

## Payload envelope

Every payload is one JSON object. It is canonical JSON: keys sorted, no whitespace, and NaN written as
`null`. Every manufacturing payload has this envelope:

| Field | Meaning |
|---|---|
| `schema` | payload schema version, `acme-uns/1` |
| `kind` | `meta`, `state`, `measurement`, `event` or `lifecycle` |
| `entity_id` | canonical id `<entity_type>:<native_id>`, unescaped: **the identity** |
| `entity_type` | the context-model entity type (the part of `entity_id` before `:`) |
| `simulation_time` | simulated seconds since the simulation start |
| `timestamp` | the same instant as an ISO-8601 time on the simulated calendar |

Each kind adds its own fields:

| `kind` | Additional fields |
|---|---|
| `meta` | `native_id`, `isa95` {`concept`, `mapping`, `mapping_id`}, `parent` (canonical id or null), `name`, and, where they exist: `level`, `class`, `origin`, `description`, `attributes`, `children`, `supplied_by`/`serves` (utilities), `specification` (materials), `yields_material`/`bill_of_materials` (products), `configuration` (configuration-class properties), `measurements` (the list of measurement leaves) |
| `state` | `state` {property: value}, `units` {property: unit} (entities); `state` = the record's operational fields (records) |
| `measurement` | `variable`, `value`, `unit`, `source` (`"simulator"`). XMEAS measurements also carry `quality` and `tag`; loops carry `loop`, `loop_tag`, `pv` and, for `controller_output`, `output` |
| `event` | `event_id`, `event_type`, `source`, `severity`, `payload`, `causation_id`, `correlation_id`: exactly the operational event of [api/operational.py](../api/operational.py) |
| `lifecycle` | `status` (READY, RUNNING, PAUSED, COMPLETED), `simulation_start`, `duration_seconds` |

Example measurement:

```json
{"entity_id":"equipment_module:EM-REACTOR","entity_type":"equipment_module","kind":"measurement",
 "property":"temperature","quality":"GOOD","schema":"acme-uns/1","simulation_time":600,"source":"simulator",
 "tag":"TI-09","timestamp":"2026-01-05T06:10:00Z","unit":"degC","value":120.37495271223582,
 "variable":"measurement:XMEAS(9)"}
```

The publisher status topic (`<site>/uns/publisher`) has only `{kind: "publisher", schema, status}`. It
is information about the MQTT transport, not about the plant, so it has no manufacturing time.

## Timestamps

- **Simulator time is authoritative.** `simulation_time` and `timestamp` come from the simulator clock.
- **Events** carry the time of the event, not the time the event was published.
- **State, measurement, meta and lifecycle** messages carry the simulation time at which the publisher
  observed the value.
- **No wall-clock time** is published: no `published_at`, and no broker time. Pacing, broker latency and
  reconnects cannot change any payload's time, so there is no side channel.

One consequence: after a publisher restart, a state republished by the new publisher instance carries
the time at which that instance observed it. The content is identical ([Reconnect](#reconnect)).

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
| `meta` | yes | a new subscriber needs the model | its content changes (e.g. a new measurement leaf) | republished for the new scope | republished by the publisher |
| entity `state` (equipment, production unit, storage) | yes | current state of the plant | any discrete property changes | republished | republished |
| record `state` (orders, lots, samples, work orders, spare parts, technicians, purchase orders, shipments, alarms) | yes | current production, maintenance, quality and material state | the record changes | **deleted** if the record does not exist in the new scope | republished |
| `measurement/<variable>` | yes | the latest value is current information | every sampling tick | republished | republished |
| `lifecycle` | yes | whether the simulation is running | the status changes | republished (READY) | republished |
| `<site>/uns/publisher` | yes | whether the retained data is live | the publisher connects or disconnects (last will) | unchanged | republished (`online`) |
| `event/<TYPE>` | **no** | an occurrence is not current state | never | not applicable | not applicable |

**How stale state is prevented:**

1. **At start-up,** the publisher reads the retained topics already under its root. At its first
   snapshot it deletes every topic that is not part of the current simulation, by publishing an empty
   retained message. This removes leftovers from an earlier run.
2. **At a reset** (a new identity scope), every retained topic of the old scope that is not in the new
   snapshot is deleted. An example is a quality sample that no longer exists.
3. **When the publisher dies,** the broker publishes its last will, `uns/publisher = offline`
   (retained). A subscriber then knows that the retained data is no longer being updated.
4. **The broker does not persist** retained messages (`persistence false` in
   [uns/mosquitto.conf](../uns/mosquitto.conf)). A restarted broker is empty until the publisher
   reconnects and republishes, so the broker cannot serve state from a finished run.

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

- **Duplicate state or measurement** (a QoS 1 redelivery, or a republish after a reconnect): the payload
  is the same, and applying it again changes nothing. Only `simulation_time` and `timestamp` may be
  later after a publisher restart.
- **Duplicate event:** identified by `event_id`, which is unique within an identity scope. A subscriber
  that must count events de-duplicates on `event_id` and resets its de-duplication set when a new scope
  begins ([Identity scope](#identity-scope-reset-r-01)).
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
order by `simulation_time` (all kinds) and by `event_id` (events).

## Reconnect

- **Publisher to broker** (network drop, broker restart). The paho client reconnects automatically
  (0.1–2 s backoff). While it is disconnected:
  - events are buffered in order, and none are dropped;
  - state and measurements are not queued: only the latest desired retained value of each topic is
    kept.

  On reconnect, the publisher publishes `online`, sends the buffered events in order, deletes topics
  that became stale in the meantime, and republishes every retained topic. Because the broker is not
  persistent, this fully restores the retained state.
- **Publisher process restart.** A new publisher publishes the full current snapshot. The content is
  identical to what the previous instance had published. It publishes no past events.
- **Subscriber reconnect:**
  - with a clean session, it receives the current retained state (not events);
  - with a persistent session, it also receives the QoS 1 messages queued while it was away.

Reconnects add no information. No payload changes, no counter advances, and no reconnect is used as a
run identifier. The tests cover all of these cases (below).

## Lifecycle

The simulation lifecycle is visible in two forms:

1. **The retained `<site>/lifecycle` topic.** It holds the current status: READY, RUNNING, PAUSED or
   COMPLETED.
2. **Lifecycle events** on `<site>/event/<TYPE>`:

| Event | When | Payload (operational) |
|---|---|---|
| `SIMULATION_STARTED` | the first event of a new simulation | `{duration_seconds}` (run, scenario and seed are removed by the boundary) |
| `SIMULATION_PAUSED` / `SIMULATION_RESUMED` | pause and resume through the service | `{time_s}` |
| `SIMULATION_RESET` | the first event of a reset simulation (`LC-0000001`) | `{}` (the scenario id is removed) |
| `SIMULATION_COMPLETED` | the end of the scenario duration | `{simulated_seconds}` (the run id is removed) |

## Identity scope (reset, R-01)

The rule is that **a new identity scope begins at every simulation start or reset.** Within a scope,
canonical ids and event ids are unique. Across scopes they repeat: `PL-0001` or `OE-0000001` in a new
scope is a different occurrence. The run id is hidden because it can encode the scenario. The UNS does
**not** invent a replacement run key.

A subscriber recognises a new scope using operational information only:

1. **Live subscribers** see a lifecycle event:
   - `SIMULATION_RESET`, whose `event_id` is `LC-0000001`, is always the first event of a reset scope;
   - `SIMULATION_STARTED`, with `OE-0000001` as the first operational event, begins a new simulation.

   A drop in `simulation_time` on any topic, or an event id restarting at 1, confirms it.
2. **Late subscribers** see only the current scope. The publisher deletes the retained topics of the old
   scope and republishes the new snapshot, so the retained tree never mixes two scopes.

**Limitation (for architectural review).** Suppose a subscriber with a persistent session is
disconnected across a reset, or across two runs of equal content. It still receives the queued lifecycle
events and can detect the new scope from them. However, a subscriber that relies only on retained state
cannot distinguish "the same run, later" from "a new run that happens to look the same", because two
scopes have no key that tells them apart. Solving this needs a scope key that carries no scenario
information, for example an opaque counter maintained by the operational boundary. That is a boundary
decision (R-01 in [CONTEXT_MODEL_DECISIONS.md](CONTEXT_MODEL_DECISIONS.md)), and the UNS deliberately
does not make it.

## Determinism

For the same scenario and configuration, the UNS publishes the same topics, and each topic receives the
same sequence of payloads byte for byte. This holds for the same entities, states, events, event ids,
timestamps and payload semantics. It holds because:

- payloads are canonical JSON, and dictionary and topic iteration orders are sorted;
- the client id is fixed, and there are no UUIDs, random ids, wall-clock times or broker metadata in
  payloads;
- measurement sampling follows simulation time, not wall time;
- deferred continuous changes are published at the next tick, whatever the pacing.

Only the interleaving *between* topics can vary with network timing, and MQTT does not guarantee it
anyway. `test_publication_is_deterministic` compares two runs through two brokers, topic by topic.

## Tests

[tests/test_uns.py](../tests/test_uns.py) runs every case against a real Mosquitto broker.

| Required test | Test |
|---|---|
| 1 broker startup, 2 connectivity, 3 publish, 4 subscribe | `test_broker_accepts_publish_and_subscribe` (and every broker test) |
| 5 namespace, 6 ISA-95 hierarchy, 7 canonical ids | `test_namespace_hierarchy_is_the_context_model_hierarchy`, `test_every_published_entity_is_canonical_and_in_its_isa95_place`, `test_topic_escaping_is_reversible_and_canonical_ids_are_preserved` |
| 8 retained state, 25 late-subscriber recovery | `test_late_subscriber_recovers_retained_state_and_no_events` |
| 9 measurements, 10 units, 11 timestamps, 14 state/event separation, 15 QoS | `test_state_measurement_and_event_channels_have_distinct_semantics` |
| 12 events, 17 ordering | `test_events_are_the_operational_stream_in_order` |
| 13 lifecycle, 21 reset identity scope | `test_lifecycle_and_reset_begin_a_clean_identity_scope` |
| 16 duplicates, 18 publisher reconnect | `test_publisher_reconnect_republishes_identical_state_only` |
| 19 subscriber reconnect | `test_subscriber_reconnect_with_persistent_session_receives_queued_events` |
| 20 broker restart | `test_broker_restart_recovers_retained_state_and_buffered_events` |
| 22 operational boundary | `test_operational_boundary_holds_on_mqtt` |
| 23 hidden-fault non-disclosure | `test_hidden_fault_is_not_disclosed_before_the_first_symptom` |
| 24 deterministic publishing | `test_publication_is_deterministic` |
| 26 full 3-hour demo | `test_full_demo_over_mqtt_leaves_the_simulation_unchanged` |
