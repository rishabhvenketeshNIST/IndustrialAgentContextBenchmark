# Historian

The Historian records the **historical operational context** of a simulation: what an operational
consumer could have observed, over simulation time, in one SQLite file per benchmark session. It is a
peer of the UNS, which carries the live operational context: both consume the same operational
projection, and neither depends on the other. The data model is in
[HISTORIAN_DATA_MODEL.md](HISTORIAN_DATA_MODEL.md).

It answers questions such as:

- What was the reactor temperature at simulated time T?
- How did a measurement trend over an interval?
- Which operational events occurred in an interval?
- What state was an entity in at time T, and when did it change?
- What happened during one operational scope?

## Architecture

```
          SimulatorService (one simulation, the source of truth)
                  │   observers: called after every simulated second and every command
        ┌─────────┴─────────┐
        ▼                   ▼
OperationalProjection   OperationalProjection      projection/operational.py (one per consumer)
        │                   │
        ▼                   ▼
  UNS publisher        HistorianWriter              historian/writer.py
        │                   │
        ▼                   ▼
   MQTT broker         SQLite (WAL)                 one file per benchmark session
   live context        historical context ◄──── HistorianReader (historian/reader.py)
```

- **The writer is an observer of the simulator service,** exactly like the UNS publisher. It never
  creates or steps a simulation, and it reads the simulation only through `OperationalProjection`,
  which enforces the operational boundary
  ([CONTEXT_PROJECTION_PRINCIPLES.md](CONTEXT_PROJECTION_PRINCIPLES.md) P4, P10).
- **It does not use MQTT.** The Historian is correct whether the UNS runs or not, and whether the
  broker is up, down or reconnecting. A test stops the broker mid-run and shows the database is
  identical to a run without any UNS.
- **The UNS and the Historian keep their own cursors and change filters.** Neither changes what the
  other records. The UNS still publishes byte for byte what it published before; this is tested.

The **reader** is Python-only for now. An HTTP query API and a UI will follow once the storage and
query contract is stable.

## Quick start

```bash
# record SCN-COOL-001 (3 simulated hours) as fast as possible, sampling every simulated second
python scripts/record_history.py --scenario SCN-COOL-001 --db exports/SCN-COOL-001.sqlite

# options: --speed N (simulated s per wall s; 0 = max), --sample-period S (default 1),
#          --commit-interval S (default 60)
```

```python
from historian.reader import HistorianReader

r = HistorianReader("exports/SCN-COOL-001.sqlite")          # agent-facing: the current scope only
scope = r.current_scope()
r.value_at(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 4833)
r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 3600, 4900)
r.events(scope, 4800, 4900)                                   # [start, end), in (t, seq) order
r.state_at(scope, "work_unit:WU-CWP-101A", 6400)
r.coverage(scope)
```

The file is ordinary SQLite: `sqlite3 exports/SCN-COOL-001.sqlite ".tables"`.

In code, attach a writer to any `SimulatorService` with
`HistorianWriter(service, path).attach()` and close it with `.close()`.

## Scopes

Every row carries the `operational_scope_id` (`OS-…`), the opaque scope identity of the operational
boundary. It is never the run id, scenario or seed.

- A **reset** or a new simulation starts a new scope. The previous scope's history stays in the file.
- **Pause and resume** keep the scope. So do UNS publisher and broker restarts, which do not concern
  the Historian at all.
- **Every query names one scope; there are no cross-scope queries.**
- `HistorianReader(path)` uses the default access `"current"`, meant for agent-facing use: only the
  scope most recently begun in the file is readable, and older scopes raise `HistorianAccessError`.
  Otherwise a file holding, for example, a faulted run and its fault-free twin would let a consumer
  compare them.
- `HistorianReader(path, access="evaluator")` reads every scope, for the benchmark evaluator.

## Operational boundary

The Historian stores only what the operational projection shows:

- transmitted measurements with quality;
- manipulated variables, setpoints and controller outputs;
- operational properties;
- operational records;
- OE-/LC- events;
- the lifecycle;
- simulated-calendar times.

It stores **no** evaluator-only information:

- true XMEAS, TEP states, IDV flags or boundary values;
- faults, the causal registry, health, capability, hidden utility status or capacity, or lot
  composition;
- EV- events;
- the run id, scenario, seed or configuration hash;
- the `TrendBuffer` (the simulator's in-memory trend buffer, which is not a historian).

It also stores **no wall-clock time**: no `observed_at`, ingestion time or insertion time. Tests check
the database contents against the boundary's own definitions (`hidden_properties`, the event view)
after the hidden fault has started. Aggregates such as interval minimum and maximum are derived
operational data. They are not implemented yet, and would be computed at query time from the stored
samples.

## Performance and size

A simulated second costs about 3–4 ms with the Historian attached, including the simulation. A
3-hour SCN-COOL-001 recording (`record_history.py --speed 0`) takes about 40 s and produces a 78 MB
file ([HISTORIAN_DATA_MODEL.md](HISTORIAN_DATA_MODEL.md#size)).

Source:
- `historian/writer.py`, `historian/reader.py`, `historian/schema.sql`
- `scripts/record_history.py`
- `tests/test_historian.py`
