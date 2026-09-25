# Edge types

Each edge has a coarse **display kind** (the five chips in the viewer) and a finer **semantics**
derived from the implementation. Relationships are not all "causal" in the same sense.

## Semantic types

| Semantics | Count | Meaning | Determined from | Executed by the simulator? |
|---|---|---|---|---|
| `cause_injection` | 20 | a benchmark cause channel enters simulated state | coupling input `fault.*` (11); equipment module `health = intrinsic − damage` (9) | yes |
| `capability_constraint` | 29 | condition limits what equipment can deliver | relations of category `equipment_performance` | yes |
| `supply_dependency` | 18 | a supply or capacity aggregates or depends on another supply | categories `utility_supply`, `power_distribution` | yes |
| `tep_boundary_interface` | 15 | enterprise value written into a TEP boundary parameter | category `tep_boundary` | yes (the only edges into TEP) |
| `process_response` | 57 | consequence inside TEP | `process_influences` (41, hand-curated); direct valve equations (13); extra TEFUNC relations (3) | **no**: TEP computes it; the edge only documents it |
| `control_measurement` | 19 | a measurement is a loop's PV | `control_scheme.LOOPS` | yes (native) |
| `control_actuation` | 11 | a loop writes an XMV | same | yes (native) |
| `control_cascade` | 8 | a master loop writes a slave's setpoint | same | yes (native) |
| `derivation` | 67 | a value is computed from others without being a physical cause: observations, metering, tests | category `utility_observation` (51); module code (16) | yes |
| `state_transition` | 12 | a discrete state change driven by another state | module code: FIFO consumption, lot disposition, order progress, repair, changeover | yes |
| `event_trigger` | 2 | a threshold crossing raises an event that creates a record | alarm → work order | yes |

**Not present as edges:** *correlation* (the causal registry labels events at runtime, not variables)
and *benchmark-only relationships* beyond `cause_injection`.

## Display kinds (viewer chips) → semantics

| Chip | Edges | Contains |
|---|---|---|
| Causal relation (`coupling`) | 124 | derivation 51, capability_constraint 29, supply_dependency 18, tep_boundary_interface 15, cause_injection 11 |
| TEP physics (`physics`) | 57 | process_response 57 |
| Native control (`control`) | 38 | control_measurement 19, control_actuation 11, control_cascade 8 |
| Module logic (`module`) | 35 | derivation 16, state_transition 10, cause_injection 9 |
| Discrete event (`event`) | 4 | event_trigger 2, state_transition 2 |

The chip name "Causal relation" means "a relation from `coupling.yaml`". It includes derivations and
observations that are not physical causes. Use `semantics` for rigorous work.

## Edge fields

`s` (source id), `t` (target id), `kind`, `semantics`, `why` (the relation id, equation or rule), `source`
(the file the edge was derived from).

Source:
- `scripts/build_variable_graph.py` — `SEM_BY_RELATION_CATEGORY`, `DIRECT_VALVE_EFFECTS`, `EXTRA_PHYSICS`
- `docs/07_variable_graph/variable_graph.json`
