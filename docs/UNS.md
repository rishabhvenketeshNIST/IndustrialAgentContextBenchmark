# Unified Namespace (UNS)

**The UNS is an ISA-95-based manufacturing information space implemented over MQTT.**

The simulator is the source of reality. The canonical context model
([contract/context_model.yaml](../contract/context_model.yaml)) defines what that reality means. ISA-95
provides the structure of the information. The UNS makes the live operational context of the plant
available to any MQTT client, and MQTT is its transport.

| Document | What it covers |
|---|---|
| this file | what the UNS is, why MQTT, why ISA-95, and how it relates to the simulator and to future systems |
| [UNS_MQTT_NAMESPACE.md](UNS_MQTT_NAMESPACE.md) | the topic tree, the ISA-95 mapping table, canonical-id escaping, and real topic examples |
| [UNS_MQTT_SEMANTICS.md](UNS_MQTT_SEMANTICS.md) | payloads, timestamps, units, retained state, QoS, duplicates, ordering, reconnect, lifecycle, operational scope, determinism |
| [UNS_OPERATING_MODEL.md](UNS_OPERATING_MODEL.md) | how simulator state becomes MQTT messages, the roles of broker, publisher and subscriber, reset, the boundary, and what belongs in the UNS |

The code is in [uns/](../uns/):

- [namespace.py](../uns/namespace.py): the topic tree;
- [publisher.py](../uns/publisher.py): simulator to MQTT;
- [broker.py](../uns/broker.py) and [mosquitto.conf](../uns/mosquitto.conf): the local broker;
- [scripts/run_uns.py](../scripts/run_uns.py): the runner;
- [inspector.py](../uns/inspector.py), [ui/inspector/](../ui/inspector/) and
  [scripts/run_inspector.py](../scripts/run_inspector.py): the read-only visual inspector
  ([Visual inspection](#visual-inspection));
- [tests/test_uns.py](../tests/test_uns.py) and [tests/test_uns_inspector.py](../tests/test_uns_inspector.py):
  the tests.

## Quick start

```bash
# 1. a broker (any MQTT 3.1.1 broker works; the repository is tested with Eclipse Mosquitto 2.x)
mosquitto -c uns/mosquitto.conf                    # or: docker run -p 1883:1883 eclipse-mosquitto
# 2. the simulator publishing into the UNS (10 simulated seconds per wall second; --speed 0 = max)
python scripts/run_uns.py --scenario SCN-COOL-001
#    or both at once:  python scripts/run_uns.py --start-broker --speed 0
# 3. any subscriber
mosquitto_sub -h 127.0.0.1 -t 'uns/v1/#' -v
#    or the visual inspector (read-only MQTT client with a web view):
python scripts/run_inspector.py                    # opens http://127.0.0.1:8050
```

**Security.** [uns/mosquitto.conf](../uns/mosquitto.conf) is a local-development and test
configuration. It is **not a production security configuration**:

- anonymous access is enabled;
- authentication, authorization and TLS are not configured;
- persistence is disabled;
- the listener is bound to the loopback interface.

A production deployment would need authentication, authorization and TLS. That is outside the scope of
this benchmark ([UNS_OPERATING_MODEL.md](UNS_OPERATING_MODEL.md#broker-security)).

`pip install paho-mqtt` (listed in [requirements.txt](../requirements.txt)) is the only new Python
dependency. The runner drives the simulator itself (`SimulatorService(start_runner=False)`, one simulated
second per step). The web UI and REST API are unchanged and independent of the UNS.

## Visual inspection

> To run the simulator web UI, the UNS and the inspector together, as one simulation, use
> `python scripts/run_manufacturing_stack.py --start-broker`
> ([LOCAL_MANUFACTURING_STACK.md](LOCAL_MANUFACTURING_STACK.md)).

The **UNS inspector** is a small, read-only web view of the real UNS. It lets a researcher or developer
see the ISA-95 structure and the live MQTT information while the simulator publishes.

```bash
mosquitto -c uns/mosquitto.conf                                 # 1. broker (local-development config)
python scripts/run_uns.py --scenario SCN-COOL-001 --speed 10    # 2. simulator -> UNS (another terminal)
python scripts/run_inspector.py                                 # 3. inspector -> http://127.0.0.1:8050
```

Options:

- `--mqtt-host` and `--mqtt-port` choose the broker (default `127.0.0.1:1883`);
- `--port` sets the web view port (default 8050);
- `--no-browser` stops it opening a browser;
- `#<canonical id>` in the URL selects an entity, for example
  `http://127.0.0.1:8050/#work_unit:WU-CWP-101A`.

**It is an ordinary MQTT client.** The inspector:

- subscribes to `uns/v1/#` and shows only what the broker delivers;
- imports nothing from `simulator/` or `api/`, calls no simulator or benchmark route, and never
  publishes;
- adds nothing to a payload.

Hidden information cannot appear in it, because it never reaches MQTT.
[tests/test_uns_inspector.py](../tests/test_uns_inspector.py) checks this: its imports, the payloads it
shows against those received, and a search for hidden keys and ids.

**What it shows:**

- **Header.**
  - The inspector's MQTT connection (CONNECTED or DISCONNECTED, with host and port).
  - The retained publisher status.
  - The retained **lifecycle** (READY, RUNNING, PAUSED, COMPLETED).
  - `t`, the latest `simulation_time` received.
  - The current **operational scope** (`OS-…`), taken from the retained lifecycle.

  When the scope changes, a *NEW SIMULATION SCOPE* banner shows the new and previous ids. It is
  detected from retained state, so it also works for an inspector that never saw `SIMULATION_RESET`.
- **ISA-95 tree.** The tree is built from the topic paths and `meta` payloads actually received, so it
  is the simulator hierarchy exactly as the UNS publishes it: Enterprise › Site › Area ›
  {Production Unit › Equipment Module › Control Module | Work Center › Work Unit | Storage Zone ›
  Storage Unit}, plus utilities, materials and products. Nothing is hard-coded, and no second
  hierarchy exists. The badge on a node counts the records (orders, lots, samples, alarms, work orders)
  published under it.
- **Selected entity.**
  - Its canonical `entity_id`, `entity_type` and ISA-95 concept (from `meta.isa95`), with parent and
    children.
  - Its **ISA-95 position**: the lineage from the enterprise down, clickable.
  - **Current retained state**, marked as such. It is the latest message of the retained state topic,
    with its `simulation_time` and scope, and it is not history.
  - **Live measurements**: variable, value, unit and `simulation_time`, with a small sparkline of the
    samples received since the inspector connected (current scope only).
  - **Recent operational events** for the entity and everything below it: `event_id` (`OE-`/`LC-`),
    type, simulation time, operational payload, and causation and correlation ids as the UNS carries
    them.
  - The **records** under the entity, with their current state.
  - Its configuration, from `meta`.
  - Every **MQTT topic** received for it. Each expands to the raw MQTT message: topic, QoS, retain
    flag, and the payload byte-for-byte as received.

**How to read the times:**

- `simulation_time` (and `simulation_timestamp`) is manufacturing time. The simulator clock is the only
  timeline the inspector uses: for the header, the measurement tables, the sparklines and the events.
- `observed_at` is shown only in a column labelled *observed_at (transport)*. It is the wall-clock time
  at which the publisher sent that copy. It is not manufacturing time.

**Retained versus live:**

- *Retained* state is the latest value of a retained topic. A new or reconnected inspector receives it
  immediately from the broker. The raw-message retain flag says whether a message came from the
  broker's retained store (`true`) or live (`false`).
- Events are not retained. The inspector lists only events that arrived while it was connected, and it
  never reconstructs or replays older ones.

**Reconnects.** If the broker goes away, the header shows *MQTT: DISCONNECTED*. The client reconnects
automatically, resubscribes, and the retained state returns. Past events are not fabricated.

**What it is not:**

- It is not a historian. It keeps only the latest message per topic, the last 500 events and the last
  120 samples per measurement, in memory, for the current scope. Nothing is stored, and there is no
  query, replay or retention.
- It is not an i3X layer or an operator HMI. Its web routes are GET-only, and it cannot send anything to
  the broker or the simulator.
- Its web view is served on the loopback interface, without authentication (local development only;
  see *Security* above).

## What the UNS is

The UNS is a single MQTT topic tree, rooted at `uns/v1`. In it every operational entity of the plant has
one place, and under that place it publishes:

- its current **state**;
- its **measurements**;
- its operational **events**;
- its static **metadata**.

A subscriber that knows nothing about the simulator can connect and find:

- **the current state of the plant:** from retained messages, as soon as it subscribes;
- **what is happening now:** from live events and measurements;
- **what each thing is and where it sits in the plant:** from the topic path and the `meta` topic.

The UNS is a *projection*. It adds no manufacturing behaviour, holds no state of its own beyond what
MQTT needs, and changes nothing in the simulator. Removing it leaves the benchmark bit-for-bit identical.
A test checks this: [tests/test_uns.py](../tests/test_uns.py) runs the full 3-hour demo through the UNS
and compares it with the demo run without it.

## Why MQTT

MQTT is the de-facto transport of Unified Namespace architectures in manufacturing. It provides exactly
the three things a UNS needs, and nothing else:

| MQTT feature | UNS use |
|---|---|
| hierarchical topics with `+` and `#` wildcards | the ISA-95 hierarchy becomes the topic tree; a subscriber selects an area, a unit or one module by topic filter |
| retained messages | "current state": a new subscriber immediately receives the last state of every entity |
| publish/subscribe with a broker | producers and consumers are decoupled; the simulator does not know who listens |

The UNS uses MQTT 3.1.1 (the most widely supported version) and a real broker, Eclipse Mosquitto. It
uses no broker-specific extensions, so any compliant broker can be substituted.

## ISA-95 Foundation

The UNS is explicitly based on ISA-95 (IEC 62264). The ISA-95 role-based equipment hierarchy *is* the
topic hierarchy: every level of the topic path is an ISA-95 level. The UNS does not create this
hierarchy. It reuses the one the simulator already has ([configs/site.yaml](../configs/site.yaml)), as
the canonical context model describes it
([docs/ISA95_SIMULATOR_MAPPING.md](ISA95_SIMULATOR_MAPPING.md)). There is no second hierarchy.

```
Enterprise                     enterprise:ENT-ACME
└─ Site                        site:SITE-TE
   └─ Area                     area:AREA-REACTION, area:AREA-UTILITIES, area:AREA-RAW, ...
      ├─ Production Unit       production_unit:PU-REACTOR          (continuous production)
      │  └─ Equipment Module   equipment_module:EM-REACTOR
      │     └─ Control Module  control_module:CM-TIC-RX
      ├─ Work Center           work_center:WC-CW                   (utilities, maintenance, lab)
      │  └─ Work Unit          work_unit:WU-CWP-101A
      └─ Storage Zone          storage_zone:SZ-RAW                 (inventory)
         └─ Storage Unit       storage_unit:SU-TK-101
```

The three branches are the three kinds of ISA-95 work center found at this site:

- production units, for continuous production;
- work centers, for utilities, maintenance and the laboratory;
- storage zones, for inventory.

ISA-95 levels that the simulator does not model are **not** fabricated. There is no Process Cell, Unit,
Production Line or Work Cell level in the topics.

ISA-95 also shapes what is published under each entity:

- **Resources** (materials, products, material lots, spare parts, technicians) have their own ISA-95
  concepts. These concepts are recorded in `meta.isa95`.
- **Operations records** (production orders and lots, work orders, quality samples, purchase orders)
  are placed under the equipment they concern.

The ISA-95 concept of every entity type comes from the context model's `isa95_mapping`. The UNS does not
restate it.

## Canonical context model

The canonical context model ([contract/context_model.yaml](../contract/context_model.yaml),
[docs/CONTEXT_MODEL.md](CONTEXT_MODEL.md)) is the semantic authority. The UNS reads it at start-up and
takes the following from it:

- **Entity types and identity.** Every entity is named by its canonical id `<entity_type>:<native_id>`,
  for example `work_unit:WU-CWP-101A`. The id is carried unchanged in every payload (`entity_id`).
- **Operational collections.** Only the collections the model marks as observable become record topics.
- **ISA-95 mappings.** These give each entity's ISA-95 concept (`meta.isa95`).
- **Property classes.** These decide whether a property is published as state, as a measurement or as
  metadata.

MQTT is only the transport. The topic string is not the identity: a subscriber identifies an entity by
the `entity_id` in the payload. Canonical ids are escaped only where MQTT forbids a character
([UNS_MQTT_NAMESPACE.md](UNS_MQTT_NAMESPACE.md#canonical-ids-in-topics)).

## Relationship to the simulator

The simulator is authoritative. After every simulated second, the UNS publisher reads the simulator
through the operational boundary and publishes what changed. The publisher never alters simulator
state, timing, physics or the event stream.

**Timestamps.** Every payload keeps two clocks apart:

- `simulation_time`, with `simulation_timestamp` as an ISO time on the simulated calendar. This is the
  simulator's own clock and the **only manufacturing time**. An event carries the time it happened, a
  measurement the time it was sampled, and a state the time its content was first observed.
  Republication after a reconnect or a publisher restart never changes it.
- `observed_at`. This is the wall-clock time at which the publisher sent this copy. It is transport
  metadata, never used for manufacturing reasoning.

Pacing (`--speed`) changes when messages are sent and their `observed_at`, never their content or
simulation time ([semantics](UNS_MQTT_SEMANTICS.md#timestamps)).

## Operational scope

Every simulation (from its creation or reset until the next one) is one **operational scope**. Record
and event ids are unique only within a scope. Every payload names its scope with an
`operational_scope_id`: an opaque `OS-` + 128-bit random token issued by the operational boundary
(`api.operational.operational_scope_id`).

- It is **not** the simulator's run id, which stays hidden because it names the scenario.
- It is not derived from the scenario, seed, configuration, faults, time or events, so it identifies a
  simulation without saying anything about it.
- It stays the same across publisher restarts, broker restarts and subscriber reconnects, and changes
  on every reset or new simulation.
- The retained `<site>/lifecycle` topic states the current scope. A subscriber that connects at any
  time can therefore tell, from retained messages alone, which simulation the retained state belongs
  to.

This resolves R-01 ([semantics](UNS_MQTT_SEMANTICS.md#operational-scope-r-01)).

## Operational boundary

The UNS is on the operational side of the boundary defined in [api/operational.py](../api/operational.py)
(contract [§20](CANONICAL_SIMULATOR_CONTRACT.md#20-ground-truth)). It publishes only what an operator could
observe. It does not publish:

- scenario, run or seed identifiers;
- fault ids, fault events or hidden health and capacity values;
- evaluator events or evaluator-only properties.

Filtering is done by the boundary functions (`hidden_properties`, `properties`, `OperationalEventView`),
not by the UNS matching names. A regression test publishes the cooling-fault scenario and the same
scenario without its fault. It shows that the two MQTT streams cannot be told apart by anything except
physical measurement values until the first operator-visible symptom at 01:20:33
([UNS_OPERATING_MODEL.md](UNS_OPERATING_MODEL.md#hidden-fault-non-disclosure)).

## State versus event

| | state | event |
|---|---|---|
| meaning | what is true now | something that happened |
| topic | `<entity>/state` (and `/measurement/<variable>`) | `<entity>/event/<EVENT_TYPE>` |
| retained | yes: the last value is the current value | no: an occurrence is never "current" |
| replayed to a new subscriber | yes | no |
| repeated delivery | harmless (idempotent) | recognised by `event_id` |

State and events come from different sources:

- **State** is the current value of an entity's operational properties.
- **Events** are the simulator's operational event stream, with the `OE-`/`LC-` ids of
  [api/operational.py](../api/operational.py).

A subscriber that wants to know whether a pump is running reads the state. A subscriber that wants to
know when it tripped reads the events.

## Retained state

These topics are retained:

- entity metadata, entity and record state, and the latest measurement values;
- the simulation lifecycle;
- the publisher status.

Events are never retained. When an entity or record disappears (for example after a reset), the
publisher deletes its retained topic. The broker therefore always holds exactly the current state of
the current simulation. Details, including broker restarts and stale-state prevention, are in
[UNS_MQTT_SEMANTICS.md](UNS_MQTT_SEMANTICS.md#retained-state).

## Relationship to the future Historian and Knowledge Graph

Neither is implemented. They are **peer** context systems, grounded in the same canonical context model
as the UNS, not downstream consumers of it:

```
                 Canonical Context Model
                /           |           \
             UNS        Historian        KG
            (MQTT)    (time-series)   (semantic)
       live state      historical      entities, relationships,
       and events      observations    topology
```

The Historian does not need to consume the UNS, and the KG does not need to consume the UNS. All three
use the same canonical ids and the same entity types. That is what lets them be combined later.

## Future i3X boundary

i3X is not implemented. It is the future interoperability and access layer across the three peers:

```
UNS ─────────┐
Historian ───┼──→ i3X ──→ MCP ──→ agent
KG ──────────┘
```

i3X is not a database. It is not the parent of the UNS, the Historian or the KG, it does not replace
MQTT, and it does not hold a copy of their data. From the UNS it would read live state and events,
addressed by the same canonical ids the UNS carries in its payloads. The UNS already exposes everything
i3X would need for this, so i3X requires no UNS changes.
