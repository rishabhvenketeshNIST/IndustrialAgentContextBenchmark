# UNS operating model

This document describes how simulator information becomes MQTT information, who does what, and what
does and does not belong in the UNS. See [UNS.md](UNS.md) for the overview,
[UNS_MQTT_NAMESPACE.md](UNS_MQTT_NAMESPACE.md) for topics and
[UNS_MQTT_SEMANTICS.md](UNS_MQTT_SEMANTICS.md) for message semantics.

## Flow: simulator, canonical context, UNS

```
simulator (engine, TEP, enterprise state, event bus)          source of reality
      │  read after every step, never written
      ▼
operational boundary (api/operational.py)                      what an operator may observe
      │  hidden_properties, properties(), OperationalEventView (OE-/LC- ids)
      ▼
canonical context model (contract/context_model.yaml)          what it means
      │  entity types, canonical ids, ISA-95 mappings, property classes, operational collections
      ▼
UNS publisher (uns/publisher.py + uns/namespace.py)            MQTT projection
      │  topics from the ISA-95 hierarchy, canonical JSON payloads
      ▼
MQTT broker (Eclipse Mosquitto)                                 transport, retained state
      │
      ▼
subscribers                                                     any MQTT client
```

## Synchronisation

The simulator is stepped one simulated second at a time. After each step, `UNSPublisher.sync()` runs
under the service lock and publishes what changed:

| Moment | What is published |
|---|---|
| **start-up** (`connect`) | Retained `uns/publisher = online`, with the last will set to `offline`. The retained topics already under the root are read, to find stale candidates. |
| **first `sync`** (new identity scope) | The scope's events so far (`SIMULATION_STARTED`), every `meta`, the `lifecycle`, every entity and record `state`, and all measurements if the time is a sampling tick. Stale retained topics are then deleted. |
| **every step** | New operational events, in stream order. Every entity state or record discrete change. The lifecycle, if it changed. |
| **every sampling tick** (`simulation_time % period == 0`, default 10 s) | Every measurement. Deferred continuous record changes. `meta` whose content changed (for example, a newly observed measurement leaf). |
| **pause / resume** | The `SIMULATION_PAUSED` / `SIMULATION_RESUMED` events, and lifecycle `PAUSED` / `RUNNING`. |
| **reset** | A new identity scope ([Reset](#reset-behaviour)). |
| **completion** | `SIMULATION_COMPLETED` and lifecycle `COMPLETED`. The retained tree keeps the final state. |
| **shutdown** (`close`) | Retained `uns/publisher = offline`, then a clean disconnect. |
| **broker lost / restored** | Events are buffered; after reconnect everything is resynchronised ([semantics](UNS_MQTT_SEMANTICS.md#reconnect)). |

The publisher changes nothing in the simulator. It does not alter physics, controllers, timing, fault
behaviour, scenarios, the boundary or the API. `scripts/run_uns.py` owns the stepping loop. Pacing
(`--speed`) only sleeps between steps. The web UI and REST API ([run.py](../run.py)) are unchanged and
do not publish to MQTT.

## Broker role

The broker is Eclipse Mosquitto (MQTT 3.1.1), configured by [uns/mosquitto.conf](../uns/mosquitto.conf):
loopback listener, anonymous access (local development only), no persistence, and no queue limit. The
broker's jobs are to:

- route messages by topic filter;
- hold the retained current state for new subscribers;
- queue QoS 1 messages for persistent subscriber sessions;
- publish the publisher's last will.

It holds no manufacturing logic. [uns/broker.py](../uns/broker.py) starts it as a child process for
tests and for `run_uns.py --start-broker`.

## Publisher role

There is one publisher per simulation. Its client id is fixed, so a second instance replaces the first
at the broker. The publisher:

- derives topics from the hierarchy (it never invents entities);
- routes properties to state, measurement or meta by their contract property class;
- publishes only operational information, through the boundary functions;
- keeps the retained tree equal to the current simulation, deleting what no longer exists;
- counts what it sends (`sent`) and any refused publish (`publish_errors`; the tests require 0).

## Subscriber behaviour

A subscriber should:

1. subscribe to `uns/v1/#`, or a narrower filter, at QoS 1. Retained messages give it the current state
   at once;
2. identify entities by the payload's `entity_id`, not by parsing the topic;
3. check `<site>/uns/publisher`. If it is `offline`, the retained data is not being updated;
4. treat state as replace-on-receive and events as occurrences, de-duplicating on `event_id` if it
   counts them;
5. order by `simulation_time` and by `event_id`, never by arrival order across topics;
6. watch for `SIMULATION_RESET` / `SIMULATION_STARTED` and clear its per-scope state when either
   arrives;
7. use a persistent session (fixed client id, `clean_session=False`) if it must not miss events while
   disconnected.

## Reset behaviour

`reset_simulation()` creates a new engine, and therefore a new identity scope. On the next `sync`, the
publisher:

1. publishes the new scope's events, starting with `SIMULATION_RESET` (`LC-0000001`);
2. publishes the new snapshot: meta, lifecycle `READY`, and every state and measurement;
3. deletes, with an empty retained message, every retained topic of the old scope that does not exist
   in the new one (for example a quality sample or a lot created during the old run).

The retained tree therefore never mixes two scopes. How subscribers detect the new scope, and the
limitation that no run key exists, are described in
[UNS_MQTT_SEMANTICS.md](UNS_MQTT_SEMANTICS.md#identity-scope-reset-r-01).

## Operational boundary

The UNS follows [api/operational.py](../api/operational.py). It does not filter by name.

| Boundary function | UNS use |
|---|---|
| `properties(rec)` | the entity properties that may appear in state, measurement and meta: hidden properties removed, and an asset status in its operational form (`ASSET_STATUS_OPERATIONAL`, so `DEGRADED` is shown as `RUNNING`) |
| `OperationalEventView` | the event stream: faults and evaluator events removed, hidden payload fields removed, `OE-`/`LC-` ids |
| context-model `observability` | only collections the model marks `operational` become record topics (faults, operator actions and disturbances do not) |

Evaluator-only information never reaches the broker:

- scenario id, run id, seed, configuration hash;
- faults and disturbances;
- asset health and capacity;
- operator actions;
- the evaluator event ids (`EV-`);
- the TEP `xmeas_true` values.

`test_operational_boundary_holds_on_mqtt` checks every published payload: the payload keys, the hidden
properties of every entity, and the event types.

## Hidden-fault non-disclosure

`test_hidden_fault_is_not_disclosed_before_the_first_symptom` publishes two runs through two real
brokers:

- the cooling-degradation scenario SCN-COOL-001, whose hidden fault starts at 01:00:00;
- the same scenario with its fault removed.

Both are recorded up to 01:20:32, one second before the first operator-visible symptom (the vibration
alarm VAH-CWP101A at 01:20:33). The two MQTT streams have:

- the same topics, and the same number of messages on every non-state topic;
- the same retain and QoS flags;
- identical meta, lifecycle and publisher-status messages;
- identical event ids, event types, targets, times and causation;
- the same sequence of discrete state (statuses, dispositions, modes, assignments);
- **byte-identical content before the fault starts** (t < 3600 s).

The fault starts at 3600 s. From then until the first symptom, the only differences are **float
values**:

- measurement values, such as pump vibration and the cooling-water header pressure;
- the continuous quantities of records;
- float fields in event payloads (kilograms consumed, analyzer results).

These are the legitimate physical observations that a diagnosis must use. The disclosure channels
listed in the requirement show no difference: topic names, payload fields, retained messages, event
numbering, timestamps, ordering, connection behaviour and message counts. The UNS has no sequence
counter, so there is none to compare.

## What belongs in the UNS

- the current operational state of every ISA-95 entity and every operational record;
- the latest measurement values, with units;
- operational events, as they happen;
- the simulation lifecycle;
- static metadata about each entity: identity, ISA-95 concept, parent, class, configuration.

## What does not belong in the UNS

- **evaluator truth:** faults, root causes, hidden health, scenario, run and seed identifiers;
- **history:** past events for late subscribers, time series, aggregates. This belongs to a future
  Historian;
- **the full semantic graph:** relationships beyond the parent path and the few relationships in
  `meta`, topology and reasoning. This belongs to a future Knowledge Graph;
- **commands:** writes into the simulator. The UNS is read-only. Operator actions go through the REST
  API ([docs/08_api](08_api/)), not MQTT;
- **transport artefacts presented as manufacturing facts:** broker time, message ids, global sequence
  numbers.

## Relationship to the Historian

The Historian is future work and is not implemented. It will be a **peer** of the UNS, grounded in the
same canonical context model and using the same canonical ids. It will store historical observations
and time series. It may read from the simulator or boundary directly, and it does not need to consume
the UNS. The UNS does not become a historian: it keeps only the current state.

## Relationship to the Knowledge Graph

The KG is future work and is not implemented. It will be a peer holding entities, relationships,
topology and semantic context from the same canonical context model. The UNS parent path and `meta`
express only the ISA-95 containment and a few direct relationships. The KG owns the rest. It does not
need to consume the UNS.

## Relationship to i3X

i3X is future work and is not implemented. It will be the interoperability and access layer over the
UNS (live state and events), the Historian (history) and the KG (semantics), all addressed by the same
canonical ids and definitions. i3X is not a database, not a parent of the three systems, not a
replacement for MQTT, and not a copy of their data. MCP and agents come after i3X. None of them is part
of this work, and the UNS needs no changes to be read by them.
