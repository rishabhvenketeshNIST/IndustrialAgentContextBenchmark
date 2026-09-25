# Graph generation, viewer and counts

## How the graph is generated

```bash
python scripts/build_variable_graph.py
# writes docs/07_variable_graph/variable_graph.json and variable_graph.html, prints stats
```

It builds an engine for `SCN-BASELINE` (1 s duration, not run) and assembles the graph from four
sources of **different authority**:

| Part | Source | Authority |
|---|---|---|
| 1. Coupling edges | `CouplingEngine.graph()`: relations after `for_each` expansion, constants removed | **authoritative**: what the simulator executes |
| 1b. TEP influence edges | `coupling.yaml → process_influences` | **curated documentation**: not executed |
| 2. Native control edges | `control_scheme.LOOPS` (transcribed from `temain_mod.f`) | **authoritative** for loop wiring |
| 3. Direct valve and TEFUNC edges | `DIRECT_VALVE_EFFECTS`, `EXTRA_PHYSICS` in the script, read from `teprob.f` equations | **hand-transcribed** from the Fortran |
| 4. Module-logic and event edges | written in the script from reading the module code | **hand-written summary**: not generated from code; will not follow code changes automatically |

Edges are de-duplicated, and nodes without edges are dropped.

**History.** The first version of this graph (published as the artifact "TEP Site Variable Graph")
was produced by an equivalent script in a scratch folder during development. During this documentation
pass the script was moved into the repository and extended with `semantics`, `authority` and `source`
fields. It produces the same 195 nodes and 258 edges.

## The viewer

`variable_graph.html` is a standalone page built from `scripts/variable_graph_template.html`. Open it
in a browser; it needs no server. Fonts load from Google Fonts when online.

* **Layout:** 8 fixed columns. Rows are ordered by 12 barycentre sweeps to reduce crossings. Edges
  pointing back left are drawn as loops.
* **Tracing:** clicking a node runs a breadth-first search downstream (orange) and upstream (blue),
  skipping edge kinds hidden by the chips. The details panel lists direct drivers and dependents with
  each edge's kind, semantics and reason.
* **Presets:** demo fault (P-101A damage), XMEAS(9), VRNG(10), TC-RX, power capacity loss, D tank stock,
  lot disposition.
* **Chips** toggle the five display kinds. **Search** matches id, label and entity name.

**Counts shown in the viewer:**

* The header shows "195 variables · 258 connections".
* The chips show edges per display kind: 124 / 57 / 38 / 35 / 4.
* The detail panel's "N upstream / N downstream" counts every node reachable in that direction through
  visible edges. It is transitive, which is why the demo fault reaches 84 nodes downstream: once the
  path reaches the XMEAS and control-loop columns, the feedback edges connect most of the plant.

The simulator's own UI does **not** embed this graph. Its "Causal Model" tab lists the executed
relations and boundary values.

## When to regenerate

After changing `configs/coupling.yaml`, `configs/tep_mapping.yaml`, `control_scheme.py`, or any module
logic summarised in part 4. For part 4 changes, the script must be edited by hand.

Source:
- `scripts/build_variable_graph.py` — `build`, `main`
- `scripts/variable_graph_template.html`
- `docs/07_variable_graph/variable_graph.html`
