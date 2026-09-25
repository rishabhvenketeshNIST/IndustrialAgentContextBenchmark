# Goals and scope

## Goals

1. **High-fidelity process behaviour without inventing physics.** The TEP Fortran model (Downs & Vogel)
   and its closed-loop control scheme (Russell, Chiang & Braatz) are used unmodified.
2. **A coherent manufacturing site around the process.** An ISA-95 hierarchy with equipment, utilities,
   materials, maintenance, quality, production and alarms, so that a fault has enterprise-level
   consequences and not just process deviations.
3. **Explicit causality.** Enterprise state influences TEP only through declared relations and
   boundary parameters, so every influence can be traced.
4. **Benchmark support.** Controlled fault injection, ground-truth labels and a separation between
   benchmark information and operational information.
5. **Reproducibility.** The same seed, scenario, configuration and compiled library give an identical
   run.

## In scope (currently implemented)

| Area | Implemented as |
|---|---|
| Process simulation | TEP Fortran via ctypes; optional pure-Python development backend |
| Control | native CONTRLn loops; plant CLOSED_LOOP/MANUAL; per-loop AUTO/CAS/MAN; operator setpoints and outputs |
| Manufacturing hierarchy | ISA-95 elements in `configs/site.yaml` |
| Enterprise modules | equipment, instrumentation, utilities, materials, inventory, warehouse, quality, production, scheduling, maintenance, alarms, operator |
| Coupling | 67 declarative relations, 17 boundary parameters |
| Faults | 20 TEP disturbances + 14 enterprise fault types |
| Events | 46 types, synchronous bus, ordered log |
| Interfaces | Python service, REST API (FastAPI), web UI, JSON/CSV export |
| Scenarios | YAML/JSON files, save/duplicate |
| Validation | 83 pytest tests |

## Explicitly out of scope

Not implemented and not depended on: Unified Namespace, MQTT, historian, knowledge graph, i3X, MCP,
AI agent or agent interface, LLM integration, vector database, semantic search, RAG. The in-memory
trend buffer is for visualisation only and is not a historian ([logging](../10_operation/logging.md)).

Also not implemented (confirmed from the code; details in [limitations](../11_limitations/known_limitations.md)):

* multiple sites or multiple TEP instances in one run;
* a plant restart after an interlock trip;
* persistence of a running simulation;
* authentication or access control on the API;
* automatic preventive-maintenance scheduling (maintenance tasks are planned only through a
  scenario's `planned_maintenance`).
