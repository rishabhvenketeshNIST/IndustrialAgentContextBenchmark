# Simulator overview

## What it is

A deterministic simulator of one manufacturing site, "Tennessee Eastman Manufacturing Site" of "ACME
Manufacturing". The site's production process is the **Tennessee Eastman Process (TEP)**, simulated by
the original Fortran code. Around it, Python models the rest of the site: equipment condition,
utilities, raw-material storage, maintenance, quality control, production orders, a product warehouse,
alarms and operator actions. A **fault engine** lets a benchmark operator inject causes such as a pump
wearing out, and the simulator works out the consequences.

It exists to produce a realistic, reproducible, fully known "manufacturing reality". Future layers such
as a UNS, a historian, a knowledge graph, i3X, MCP or an agent can observe it and be evaluated against
its ground truth. None of those layers exist in this repository ([future architecture](../11_limitations/future_extensions.md)).

## The principle everything follows

> **The enterprise simulator defines simulated manufacturing reality. Downstream information
> technologies may later expose views of that reality, but they do not define or modify it.**

Inside the simulator, authority is split cleanly:

| Kind of state | Authoritative source | Evidence |
|---|---|---|
| Process physics: temperatures, pressures, levels, flows, compositions | TEP Fortran `TEFUNC` | [TEP integration](../03_tep/tep_integration.md) |
| Manipulated variables | native controllers (`CONTRLn`) or operator writes | [native control](../03_tep/native_control.md) |
| Everything the enterprise layer can change about TEP | 17 named boundary parameters, written only by the coupling engine | [coupling contract](../04_coupling/coupling_contract.md) |
| Equipment condition, utilities, stock, orders, lots, work orders, alarms | the owning enterprise module | [canonical state](../05_state_and_events/canonical_state.md) |
| Why something happened (fault causes, causal labels) | fault engine and causal registry: benchmark ground truth | [ground truth](../06_scenarios/fault_injection.md#ground-truth) |

## Ten-second mental model

```
benchmark fault ─► cause channel ─► enterprise module state (e.g. pump health)
       ─► causal relation (coupling.yaml) ─► TEP boundary parameter (e.g. VRNG(10))
       ─► TEP physics + native control ─► measurements
       ─► enterprise observation (alarms, quality, production, maintenance) ─► events
       ─► maintenance may change enterprise state again (repair, standby pump) ─► ...
```

## What you can do with it

* Run it interactively: `python run.py` opens a web UI with the ISA-95 tree, a process schematic, trends,
  module tabs and a fault-injection panel ([running](../10_operation/running.md)).
* Run headless and export: `python scripts/run_demo.py` ([exports](../10_operation/exports.md)).
* Drive it from Python or REST ([API overview](../08_api/api_overview.md)).
* Define scenarios in YAML: seed, duration, faults, orders, planned maintenance, scripted operator
  actions ([scenario model](../06_scenarios/scenario_model.md)).

## Size of the model (from the implementation)

| Item | Count | Source |
|---|---|---|
| ISA-95 equipment elements | 87 (57 from TEP, 30 enterprise) | `configs/site.yaml` |
| Maintainable assets | 10 | `configs/equipment.yaml` |
| Utility services | 4 | `configs/utilities.yaml` |
| Causal relations (after expansion) | 67 | `configs/coupling.yaml` |
| TEP boundary parameters | 17 (15 currently coupled) | `simulator/tep/boundary.py` |
| XMEAS / XMV / IDV / states | 41 / 12 / 20 / 50 | `simulator/tep/catalog.py` |
| Native control loops | 19 | `simulator/tep/control_scheme.py` |
| Alarm definitions | 105 | `configs/alarms.yaml` (plus generated) |
| Fault types | 34 (20 TEP-native, 14 enterprise) | `simulator/faults/types.py` |
| Event types | 47 | `simulator/events/__init__.py` |
| Tests | 83 | `tests/` |

Source:
- `simulator/simulation/engine.py` — `SimulationEngine`
- `api/service.py` — `SimulatorService`, `BenchmarkFaultAPI`
- `run.py` — `main`
