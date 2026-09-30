# Context projection principles

Rules that UNS, historian and knowledge-graph implementations must follow. **The UNS is implemented**
as an ISA-95-based MQTT namespace ([UNS.md](UNS.md)), and **the Historian** as deterministic SQLite
history ([HISTORIAN.md](HISTORIAN.md)), both on the shared operational projection (P10). The knowledge
graph does not exist yet. This page constrains them in advance.

```
Enterprise Simulator (source of truth)
        │
        ▼
Canonical Context Model (identity, observations, state, events, relationships)
        │   operational boundary: only operational / derived_operational content
        ▼
   ┌────┼──────────┐
   ▼    ▼          ▼
  UNS  Historian   KG        ← projections
   └────┼──────────┘
        ▼
       i3X
        ▼
       MCP
        ▼
      Agent

Evaluator ──► /api/benchmark/*  (ground truth; never through the projections)
```

## P1. Projections, not sources

UNS, historian and KG are **projections** of the canonical context model. They add no semantics and
no state. The simulator remains the only source of truth; if a projection and the simulator disagree,
the projection is wrong.

## P2. One identity

Every projection identifies things by the canonical identity `<entity_type>:<native_id>`
([context model §4](CONTEXT_MODEL.md#4-entity-model-and-identity)). The rule is frozen (decision
U-05).

* **Lossless mapping:** a projection may encode the identity differently (a topic path, an IRI, a
  tag), but the mapping must be lossless and documented.
* **Aliases:** loop numbers, instrument tags and bound properties are aliases, never separate things.
* **Carry it verbatim:** carry the canonical id itself (e.g. a `canonical_id` attribute on a UNS
  payload, a historian tag or a KG node). Escape characters such as `(` `)` for the transport, never in
  the id.
* **Run scope:** record and event identities are unique only within one run. A projection carries the
  opaque `operational_scope_id` (from `api/operational.py`) with every run-scoped observation, so that
  consumers can attribute it to its simulation. It must not use the evaluator-only run id for this, and
  must not invent its own id per projection process (R-01).

## P3. Same semantics

* **One definition per concept.** A measurement, a setpoint, a controller output and a manipulated
  variable stay distinct in every projection; so do state and event, and capacity and flow.
* **Units** come from the context model.
* **ISA-95 names** are used as documented in the [mapping](ISA95_SIMULATOR_MAPPING.md). Where the
  mapping says MODELING_CHOICE, the projection uses the selected interpretation.

## P4. Operational content only

Projections are fed from the operational routes (`/api/*` outside `/api/benchmark/*`), or from the
same boundary functions (`api/operational.py`).

* **Excluded:** anything `evaluator_only`, `derived_evaluator_only` or `internal`. That covers fault
  records, cause channels and correlation to faults, health and capability, utility status and
  capacity, lot composition, true XMEAS, TEP states, IDV flags, boundary values, and canonical `EV-`
  event ids.
* **No reintroduction:** a projection must not rebuild withheld information. Example: it must not
  derive an asset's DEGRADED band from values it has no access to, re-number events by joining against
  evaluator ids, or attach scenario identity.

## P5. Role of each projection

| Projection | Carries | Must not |
|---|---|---|
| UNS | the current value of operational observations and states, addressed by canonical identity and hierarchy (`contains`); lifecycle events as run-state changes | carry history or evaluator-only values; publish a utility status unless it is a documented derivation from observable inputs only (U-02) |
| Historian | operational observations over time, with the timestamp semantics of the context model (`step_state` sampled at step boundaries; `analyzer_sample` values at their update times; record and event times as stored) | invent change timestamps the simulator does not have; interpolate across analyzer samples as if continuous without saying so |
| KG | canonical entities and operational relationships (hierarchy, process variables, utility topology, material including `yields_material`, production, quality, maintenance, alarms, operational event causation) | contain the evaluator-only causal relationships (fault targets, correlation to faults, coupling equations and values, process influences), or attach health or status to topology edges (U-04); create customer or supplier entities (U-06) |

## P6. Time

* **Time base:** all timestamps are simulation time: seconds and `simulation_start` + seconds, in UTC.
* **Ordering:** events are ordered by `OE-` id; lifecycle `LC-` events are ordered against them by
  `simulation_time`.
* **Wall-clock time** never enters a projection as data.

## P7. Observability conditions are a later, separate layer

Noise, delay, missing, stale or conflicting observations (future benchmark conditions) are applied
**after** projection, as an explicit transformation between operational observation and agent-visible
context ([context model §11](CONTEXT_MODEL.md#11-observability-conditions-future-benchmark-dimension-not-implemented)).
They must never alter simulator truth or the canonical model, and the evaluator always compares
against simulator truth.

## P8. The evaluator does not use projections for truth

The evaluator reads ground truth from `/api/benchmark/*`, including the `EV-` ↔ `OE-` mapping for
scoring references made to operational events. A projection is what the agent sees, never what it is
scored against.

## P10. One shared operational projection

`projection/operational.py` (`OperationalProjection`) is the transport-neutral operational projection
shared by the context consumers, the UNS and the Historian. For one simulation engine (one
operational scope) it decides *what* is operationally observable and how it is structured:

- entity metadata and ISA-95 placement (`Isa95Placement`, derived from the simulator hierarchy and the
  context model);
- measurements, with their timestamp semantics (`step_state` or `analyzer_sample`);
- entity state and operational records;
- operational events and the lifecycle;
- the report-by-exception rule for state (`StateChangeFilter`).

It enforces P4 through `api/operational.py` and the context model's observability; it keeps no list of
field names. It contains no wall-clock time (P6).

Each consumer adds only its own concerns: the UNS adds MQTT topics, envelope, retain, QoS and reconnect,
and the Historian adds storage, coverage and bounded queries. A consumer must not re-derive projection semantics.
`tests/test_projection.py` fixes the projection's contract, and proves that the UNS built on it
publishes byte for byte what it published before the projection was extracted.

## P9. Changes

* **Contract first:** a projection that needs a concept the context model lacks must extend the
  context model (and, if the simulator produces something new, the canonical contract) first.
  `tests/test_context_model.py` and `tests/test_contract.py` must stay green.
* **Operational boundary is fixed:** projections must not change the operational boundary.

Source:
- `contract/context_model.yaml`
- `api/operational.py` — `OperationalEventView`, `hidden_properties`
