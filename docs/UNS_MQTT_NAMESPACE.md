# UNS MQTT namespace

This document describes the topic tree of the Unified Namespace ([UNS.md](UNS.md)). Every example below
is a real topic that the publisher produces for this site. The tree is built by
[uns/namespace.py](../uns/namespace.py). It is derived from the simulator's ISA-95 hierarchy
([configs/site.yaml](../configs/site.yaml)) and the canonical context model
([contract/context_model.yaml](../contract/context_model.yaml)), and no hierarchy is written down in the
UNS code.

## Topic grammar

```
<root>/<entity segment>/.../<entity segment>/<channel>[/<leaf>]

root            uns/v1                               (--root; the version is the topic-tree version)
entity segment  <entity_type>:<native_id>            one per ISA-95 level on the path, escaped for MQTT
channel         meta | state | measurement | event | lifecycle
leaf            measurement: the variable name; event: the event type
```

The path to an entity is the chain of its ISA-95 parents, starting at the enterprise. Every entity
segment contains `:`. Channel and leaf segments never contain `:`, except in measurement leaves such as
`measurement:XMEAS(9)`, which always follow `/measurement/`. A subscriber can therefore always tell
which part of a topic is hierarchy and which part is channel.

## Root, enterprise, site and area

| Level | Topic prefix (+ `/meta`, `/state`, ...) |
|---|---|
| root | `uns/v1` |
| enterprise | `uns/v1/enterprise:ENT-ACME` |
| site | `uns/v1/enterprise:ENT-ACME/site:SITE-TE` |
| area | `uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-REACTION` |

Site-level topics are:

- `.../site:SITE-TE/lifecycle`: the simulation lifecycle, retained;
- `.../site:SITE-TE/event/SIMULATION_STARTED`: site-wide events;
- `.../site:SITE-TE/uns/publisher`: the transport status of the UNS publisher (`online`/`offline`). It
  is not manufacturing information.

## Equipment hierarchy

```
# production unit > equipment module > control module
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-REACTION/production_unit:PU-REACTOR/meta
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-REACTION/production_unit:PU-REACTOR/equipment_module:EM-REACTOR/meta
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-REACTION/production_unit:PU-REACTOR/equipment_module:EM-REACTOR/control_module:CM-TIC-RX/meta

# work center > work unit
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-UTILITIES/work_center:WC-CW/meta
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-UTILITIES/work_center:WC-CW/work_unit:WU-CWP-101A/meta

# storage zone > storage unit
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-RAW/storage_zone:SZ-RAW/meta
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-RAW/storage_zone:SZ-RAW/storage_unit:SU-TK-101/meta
```

**Utilities** are services, not equipment. They are placed under the work center that supplies them
(`supplied_by`):

```
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-UTILITIES/work_center:WC-CW/utility:UT-CW-REACTOR/meta
```

**Materials and products** are site-scoped definitions:

```
uns/v1/enterprise:ENT-ACME/site:SITE-TE/material:MAT-GH/meta
uns/v1/enterprise:ENT-ACME/site:SITE-TE/product:PROD-GH-M1/meta
```

## ISA-95 mapping table

The ISA-95 concept of each entity type is the context model's `isa95_mapping` entry. Every `meta`
payload carries it as `meta.isa95` (`concept`, `mapping`, `mapping_id`).

| MQTT hierarchy level | ISA-95 concept | Canonical entity type | Example entity | Example topic | Notes |
|---|---|---|---|---|---|
| 1 | Enterprise | `enterprise` | `enterprise:ENT-ACME` | `uns/v1/enterprise:ENT-ACME/meta` | M-ENTERPRISE, DIRECT |
| 2 | Site | `site` | `site:SITE-TE` | `.../site:SITE-TE/meta` | M-SITE, DIRECT; carries `lifecycle` and site-wide events |
| 3 | Area | `area` | `area:AREA-REACTION` | `.../site:SITE-TE/area:AREA-REACTION/meta` | M-AREA, DIRECT; 8 areas |
| 4 | Production Unit | `production_unit` | `production_unit:PU-REACTOR` | `.../area:AREA-REACTION/production_unit:PU-REACTOR/state` | M-PRODUCTION-UNIT, DIRECT; continuous production. PU-STRIPPER also carries the line state |
| 5 | Equipment Module (ISA-88 lower-level equipment) | `equipment_module` | `equipment_module:EM-REACTOR` | `.../production_unit:PU-REACTOR/equipment_module:EM-REACTOR/measurement/measurement:XMEAS(9)` | M-EQUIPMENT-MODULE, MODELING_CHOICE; carries the XMEAS measurements bound to it |
| 6 | Control Module (ISA-88 lower-level equipment) | `control_module` | `control_module:CM-TIC-RX` | `.../equipment_module:EM-REACTOR/control_module:CM-TIC-RX/measurement/setpoint` | M-CONTROL-MODULE, DIRECT; loops, valves, analyzers |
| 4 | Work Center | `work_center` | `work_center:WC-CW` | `.../area:AREA-UTILITIES/work_center:WC-CW/meta` | M-WORK-CENTER, MODELING_CHOICE |
| 5 | Work Unit | `work_unit` | `work_unit:WU-CWP-101A` | `.../work_center:WC-CW/work_unit:WU-CWP-101A/measurement/vibration` | M-WORK-UNIT, DIRECT |
| 4 | Storage Zone | `storage_zone` | `storage_zone:SZ-RAW` | `.../area:AREA-RAW/storage_zone:SZ-RAW/meta` | M-STORAGE-ZONE, DIRECT |
| 5 | Storage Unit | `storage_unit` | `storage_unit:SU-TK-101` | `.../storage_zone:SZ-RAW/storage_unit:SU-TK-101/measurement/level_pct` | M-STORAGE-UNIT, DIRECT |
| under supplying work center | no direct object (supply capability of a Work Center) | `utility` | `utility:UT-CW-REACTOR` | `.../work_center:WC-CW/utility:UT-CW-REACTOR/measurement/pressure` | M-UTILITY, MODELING_CHOICE |
| under site | Material Definition | `material` | `material:MAT-GH` | `.../site:SITE-TE/material:MAT-GH/meta` | M-MATERIAL-DEFINITION, DIRECT |
| under site | Product Definition | `product` | `product:PROD-GH-M1` | `.../site:SITE-TE/product:PROD-GH-M1/meta` | M-PRODUCT-DEFINITION, PARTIAL |
| record, under its location | Material Lot | `material_lot` | `material_lot:LOT-A-2601` | `.../storage_unit:SU-SPH-103/material_lot:LOT-A-2601/state` | M-MATERIAL-LOT, DIRECT |
| record, under the production unit | Material Lot (of the product) | `production_lot` | `production_lot:PL-0001` | `.../production_unit:PU-STRIPPER/production_lot:PL-0001/state` | M-PRODUCTION-LOT, MODELING_CHOICE |
| record, under the production unit | Production (Operations) Request / Job Order | `production_order` | `production_order:PO-2026-0201` | `.../production_unit:PU-STRIPPER/production_order:PO-2026-0201/state` | M-PRODUCTION-ORDER |
| record, under its asset | Maintenance Request / Work Order | `work_order` | `work_order:WO-…` | `.../work_unit:WU-CWP-101A/work_order:WO-…/state` | M-WORK-ORDER; exists once maintenance is requested |
| record, under its source | QA Test Request / Response | `quality_sample` | `quality_sample:QS-00001` | `.../control_module:CM-AT-PRODUCT/quality_sample:QS-00001/state` | M-QUALITY-SAMPLE |
| record, under its equipment | (alarm) | `alarm` | `alarm:PAH-07` | `.../equipment_module:EM-REACTOR/alarm:PAH-07/state` | M-ALARM; the alarm's source value is on the source's measurement topic |
| record, under its location | Material Definition (MRO) + inventory | `spare_part` | `spare_part:SP-IMP-101` | `.../storage_unit:SU-WH-MRO/spare_part:SP-IMP-101/state` | M-SPARE-PART, COMPOSITE |
| record, under its location | Level 4 procurement | `purchase_order` | `purchase_order:PO-…` | `.../storage_unit:…/purchase_order:PO-…/state` | M-PURCHASE-ORDER, PARTIAL |
| record, under the dispatch location | Level 4 logistics | `shipment` | `shipment:SH-…` | `.../storage_unit:SU-WH-FG/shipment:SH-…/state` | M-SHIPMENT, PARTIAL |
| record, under site | Person | `technician` | `technician:TECH-01` | `.../site:SITE-TE/technician:TECH-01/state` | M-PERSON, PARTIAL |

**Not fabricated.** The simulator does not use the ISA-95 Unit, Process Cell, Production Line or Work
Cell levels (`M-UNIT-LEVEL` and `M-PROCESS-CELL` are NOT_REPRESENTED in the context model), so they do
not appear in any topic.

**Not published.** These entity types are not operational, so they do not appear as topics:

- `fault`, `operator_action`, `disturbance`, and hidden asset health: evaluator side, see
  [api/operational.py](../api/operational.py) and contract [§20](CANONICAL_SIMULATOR_CONTRACT.md#20-ground-truth);
- `measurement`, `manipulated_variable` and `control_loop`: these are not separate entity nodes. They
  are variables on the equipment they are bound to (see below);
- `event`: events are messages, not nodes.

## Channels

| Channel | Topic | Retained | QoS | Content |
|---|---|---|---|---|
| `meta` | `<entity>/meta` | yes | 1 | static description: native id, ISA-95 concept, parent, name, class, attributes, configuration, and the list of measurement leaves |
| `state` | `<entity>/state` | yes | 1 | current discrete operational state (entities); the current record (records) |
| `measurement` | `<entity>/measurement/<variable>` | yes (last value) | 0 | one sampled value with unit |
| `event` | `<entity>/event/<EVENT_TYPE>` | **no** | 1 | one operational event occurrence |
| `lifecycle` | `<site>/lifecycle` | yes | 1 | simulation status: READY, RUNNING, PAUSED or COMPLETED |

### Measurement leaves

| Leaf | On | Example |
|---|---|---|
| `measurement:XMEAS(n)` | the equipment the TEP measurement is bound to | `.../equipment_module:EM-REACTOR/measurement/measurement:XMEAS(9)` (reactor temperature, degC) |
| `manipulated_variable:XMV(n)` | the valve or drive control module | `.../equipment_module:EM-RX-COOLING/control_module:CM-TV-10/measurement/manipulated_variable:XMV(10)` |
| `setpoint`, `controller_output` | loop control modules | `.../control_module:CM-TIC-RX/measurement/setpoint` |
| operational measurement properties | work units, utilities, storage units, ... | `.../work_unit:WU-CWP-101A/measurement/vibration`, `.../utility:UT-CW-REACTOR/measurement/pressure` |
| production totals and rates | the production unit PU-STRIPPER | `.../production_unit:PU-STRIPPER/measurement/rate_kg_h` |

The XMEAS and XMV leaves are the canonical ids of the context model's `measurement` and
`manipulated_variable` types, so they keep their parentheses.

### Where events go

An event is published under the entity named as its target. If the target is not an entity, it goes
under the record it concerns; if it is neither, it goes under the site. For example:

```
uns/v1/enterprise:ENT-ACME/site:SITE-TE/event/SIMULATION_STARTED
uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-UTILITIES/work_center:WC-CW/work_unit:WU-CWP-101A/event/ALARM_ACTIVATED
```

## Canonical ids in topics

The canonical id `<entity_type>:<native_id>` is the identity. **Payloads always carry it unescaped**
(`entity_id`). In topics, one entity segment is the canonical id with only the characters that MQTT
reserves percent-encoded:

| Character | Why | Encoded as |
|---|---|---|
| `%` | the escape character itself | `%25` |
| `/` | topic level separator | `%2F` |
| `+` | single-level wildcard | `%2B` |
| `#` | multi-level wildcard | `%23` |
| NUL | forbidden in MQTT strings | `%00` |
| leading `$` | reserved for broker topics (`$SYS`) | `%24` |

No current native id contains any of these characters, so today every topic segment is the canonical id
verbatim. The rule exists so that a future id cannot break the tree. The escaping is reversible
(`uns.namespace.unescape`). `:` is legal in MQTT and is kept.

## Subscribing

```bash
mosquitto_sub -t 'uns/v1/#' -v                                           # everything
mosquitto_sub -t 'uns/v1/+/+/area:AREA-REACTION/#' -v                     # one area
mosquitto_sub -t 'uns/v1/+/+/+/+/state' -v                                # state of every production unit, work center and storage zone
mosquitto_sub -t 'uns/v1/+/+/+/+/work_unit:WU-CWP-101A/#' -v              # one work unit
mosquitto_sub -t 'uns/v1/+/site:SITE-TE/lifecycle' -v                     # the simulation lifecycle
```

MQTT wildcards match levels, not types, and `#` may only be the last level. So `uns/v1/#/state` is not a
valid filter. To select every state topic at any depth, subscribe to `uns/v1/#` and filter on the last
segment or on the payload's `kind`.
