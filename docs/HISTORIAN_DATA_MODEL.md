# Historian data model

The storage and time semantics of the Historian ([HISTORIAN.md](HISTORIAN.md)). The schema is
[historian/schema.sql](../historian/schema.sql), version **`acme-historian/1`**, recorded in
`historian_meta`. The reader and the writer refuse a file with any other version
(`HistorianSchemaError`).

## Tables

| Table | One row per | Key |
|---|---|---|
| `historian_meta` | setting (`schema`) | `key` |
| `scopes` | operational scope: `simulation_start` (simulated calendar), `duration_seconds`, `sample_period_s` of the recording, `schema` | `operational_scope_id` |
| `coverage` | committed, continuously observed interval `[from_t, to_t]` (both inclusive) | — |
| `entities` | entity node or record of a scope: canonical `entity_id`, `entity_type`, `parent_id`, `isa95_path` (JSON, enterprise first), `isa95_mapping_id`, `name`, `first_t` | scope + `entity_id` |
| `series` | measurement of an entity: `variable`, `kind`, `unit`, `semantics`, `sample_period_s`, `dead_time_s`, `source` | `series_id`; unique scope + entity + variable |
| `samples` | observation of a series: `t`, `value` (NULL if not a number), `quality` (XMEAS) | `series_id` + `t` |
| `state_changes` | change of an operational property: entity, `property`, `t`, `seq`, `value` (canonical JSON), `unit`, `origin` | scope + `seq` |
| `events` | operational event: `event_id` (OE-/LC-), `t`, `seq`, `event_type`, entity, `source`, `severity`, `payload` (canonical JSON), `causation_id`, `correlation_id` | scope + `event_id`; unique scope + `seq` |

- **Identity.** Canonical ids are stored exactly as the operational projection gives them
  (`<entity_type>:<native_id>`). ISA-95 placement is the projection's (`Isa95Placement`); the
  Historian keeps no hierarchy of its own. Records such as `quality_sample:QS-00001` are entities of
  their scope, placed under the entity their context-model relationship names.
- **Integrity.** Foreign keys tie series, state changes and events to their entity, and entities to
  their scope.
- **Indexes.** They serve the reader's queries: by scope, entity, variable, time, and `(t, seq)`.

## Series

| `kind` | Variables | Semantics |
|---|---|---|
| `xmeas` | `measurement:XMEAS(1..41)`, transmitted, with `quality` | XMEAS 1–22 `step_state`; XMEAS 23–41 `analyzer_sample` |
| `xmv` | `manipulated_variable:XMV(1..12)` (%) | `step_state` |
| `setpoint`, `controller_output` | one of each per native control loop (19) | `step_state` |
| `production` | the production line's rates and totals (on `production_unit:PU-STRIPPER`) | `step_state` |
| `property` | the operational measurement properties of entities (vibration, meters, inventory, ...) | `step_state` |

A property that appears only during the run is a series from its first observation on. For example, a
storage unit's `consumption_kg_h` exists from t = 1.

## Temporal semantics

`t` is **simulation time in seconds**, the only timeline. Nothing wall-clock is stored
([CONTEXT_PROJECTION_PRINCIPLES.md](CONTEXT_PROJECTION_PRINCIPLES.md) P6).

| Observation | Recorded when | `t` is |
|---|---|---|
| **step_state sample** | on the grid `t % sample_period_s == 0` (default 1 s: every simulator step) | the step boundary at which the value holds |
| **analyzer_sample** | at the catalog schedule (see below) | the time the result becomes observable |
| **state change** | when the projection's report-by-exception rule reports a change (below) | the step boundary at which it was first observed |
| **event** | as produced | the event's own simulation time |
| **lifecycle** | as a state change of the site, property `lifecycle_status` (READY, RUNNING, PAUSED, COMPLETED) | the boundary at which the status changed |

- **Samples are never interpolated.**
- **State changes and events.** An event stamped t happened during the step that begins at t. A state
  change it causes is observed at that step's end boundary: t + 1, or t itself for events raised at
  the end of a step. The simulator has no finer change time, so none is invented. For example, the
  pump's `MAINTENANCE_STARTED` and `EQUIPMENT_STATE_CHANGED` events are at 6333, and its `status`
  becomes UNDER_MAINTENANCE at 6334.
- **State rule.** State follows the projection's `StateChangeFilter`, with this recording's grid as
  the tick. A discrete change is recorded at once. A change of continuous record quantities alone
  (lot or order kilograms, projected end) is recorded at the next grid point, which at the default
  1-second grid is the same second.
- **Property-level rows.** Only properties whose value changed get a row. `origin = baseline` marks
  the first observation of a property in a recording (at a scope's start or when a writer attaches);
  `change` marks everything after.

## Analyzer semantics

The analyzers (XMEAS 23–41) are recorded by their **catalog schedule**, never by a change in value:

- **Schedule.** Each analyzer has a sample period P (`sample_period_h`: 0.1 h = 360 s for the reactor
  feed and purge, 0.25 h = 900 s for the product) and a dead time D (`dead_time_h`, the same values).
  The result of the sample at k·P is observable at the end of the one-second step that begins at k·P,
  so it is recorded at **t = k·P + 1** for k ≥ 1. A test checks, over a whole run, that this schedule
  is exactly when the simulator updates its analyzers.
- **Equal results are still observations.** Two equal results at two scheduled times are two rows.
- **No result before the first sample.** There is no analyzer row before the first sample at P + 1,
  and none is invented.
- **Series metadata.** The series stores `sample_period_s` = P and `dead_time_s` = D.
  `value_at` returns `represents_time = sample_time − dead_time_s`: the process time the result
  describes.
- **No interpolation.** A value between two analyzer results is the earlier result, returned with its
  sample time and age.

## Queries and ordering

- **History order** within a scope is `(t, seq)`. `seq` is one counter per scope, shared by events
  and state changes, and assigned in the projection's order: events, then the lifecycle, then
  entities, then records. It is deterministic because the projection is. Database insertion order
  carries no meaning.
- **Ranges** are half-open `[start, end)`.
- **Limits.** A range spans at most 86 400 simulated seconds. Results default to 1 000 rows and allow
  at most 10 000. A result that hit its limit says `truncated` and, for samples, gives `next_start`.
- **`value_at(t)`** returns:
  - the last sample at or before t, with `sample_time`, `age`, `semantics`, `dead_time_s` and
    `represents_time`;
  - `covered`: whether t is inside a recorded interval;
  - `continuous_since_sample`: whether coverage is unbroken from the sample to t.

  So a value is never silently carried across a recording gap.
- **`state_at(t)`** returns, for each property, the last recorded value at or before t and the time it
  was recorded (`since`).

## Duplicates

Re-ingesting the same observation is a no-op. Conflicting data for the same key raises
`HistorianIntegrityError`:

| Key | Same content | Different content |
|---|---|---|
| sample: series + `t` | no-op | error |
| event: scope + `event_id` | no-op (`seq` is not consumed) | error |
| series: scope + entity + variable | the existing series | error (different metadata) |
| scope | no-op | error (different settings, e.g. another sample period) |

State changes are recorded only when a property's value differs from its last recorded value, so
repeated observations add nothing.

## Coverage and commits

- **Batched commits.** Rows are committed every `commit_interval_s` simulated seconds (default 60) and
  at every lifecycle change (start, pause, resume, completion), at a reset (the previous scope is
  committed first), and on `flush()` and `close()`.
- **Coverage is only durable history.** Coverage is updated in the same transaction as the data it
  covers.
- **Gaps are explicit.** A writer that attaches to a running simulation, or re-attaches after being
  closed, starts a new interval:
  - earlier events and samples are **not backfilled**;
  - the current state is recorded as a `baseline`;
  - the unobserved interval is simply absent from `coverage`.
- **Missing seconds.** If a writer ever misses seconds while attached, which the service's
  one-second observer stepping prevents, it starts a new interval rather than claim continuity.

## Scope isolation

Every row has its scope, and every query names exactly one scope. The same native ids (`PL-0001`,
`OE-0000001`) recur in different scopes as different rows. The default reader access is the current
scope only; evaluator access reads any scope ([HISTORIAN.md](HISTORIAN.md#scopes)).

## Evaluator boundary

The writer reads only `OperationalProjection`, which applies `api/operational.py` (the owning modules'
`model_internal`/`unobservable` tags, the operational event view) and the context model's
observability. There is no field-name filtering in the Historian. What the projection withholds never
reaches SQLite. The invariant:

> For every scope and every time t, the Historian's contents up to t are a function of the operational
> projection at times up to t. They are written once, in simulation order, and never revised; nothing
> in them is later information or evaluator-only information.

## Size

For SCN-COOL-001 at the default 1-second grid, a 3-hour recording holds:

- 1 674 612 samples, mostly about 155 step-state series × 10 801 seconds, plus the analyzer results;
- 136 049 state changes, mostly lot and order quantities that change every second;
- 139 events.

The file is 78 MB. A 10-second grid reduces the step-state samples tenfold.
