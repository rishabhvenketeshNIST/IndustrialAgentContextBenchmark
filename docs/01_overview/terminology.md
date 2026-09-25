# Terminology (glossary)

Definitions are specific to this project. "Source" points to where the concept lives in the code.

| Term | Meaning in this simulator | Source |
|---|---|---|
| **TEP** | Tennessee Eastman Process: the chemical process benchmark by Downs & Vogel (1993). Here, specifically the Fortran `teprob.f` process model plus the `temain_mod.f` control scheme, both unmodified. | `simulator/tep/fortran/src/` |
| **TEFUNC / TEINIT** | TEP's derivative function and its initialisation routine. TEFUNC computes all measurements and derivatives from the 50 states. | `teprob.f` |
| **XMEAS(n)** | One of 41 process measurements computed by TEFUNC (22 continuous, 19 sampled analyzers). | `simulator/tep/catalog.py` |
| **XMV(n)** | One of 12 manipulated variables: valve or drive position commands in %. | `simulator/tep/catalog.py` |
| **IDV(n)** | One of 20 binary process disturbances built into TEFUNC. | `simulator/tep/catalog.py` |
| **State (YY)** | One of TEP's 50 internal ODE states. Diagnostic only here. | `catalog.STATES` |
| **SETPT(k)** | A controller setpoint held in the `/CTRLALL/` common block. | `control_scheme.py` |
| **Native control** | The 19 CONTRLn subroutines in `temain_mod.f`, executed as compiled Fortran. | `control_scheme.LOOPS` |
| **Loop mode** | AUTO (runs, local setpoint), CAS (runs, setpoint written by a master loop), MAN (not executed; output operator-set). An enterprise-layer concept around native control. | `LoopMode` |
| **Control mode** | Plant-wide CLOSED_LOOP (all loops run) or MANUAL (none run). | `ControlMode` |
| **Measurement** | Here: a TEP XMEAS value. *True* = what TEP computed (including TEP's own noise). *Transmitted* = after the instrumentation layer. | `ProcessState.xmeas_true`, `ProcessState.xmeas` |
| **Setpoint** | The target a loop drives its measurement to (`SETPT`). Distinct from the measurement and from the output. | `get_loop_states` |
| **Controller output** | What a loop writes: an XMV for inner loops, another loop's SETPT for cascade masters. | `LoopDefinition.output_kind` |
| **Manipulated variable** | The XMV command sent to a valve or drive. The physical valve position (VPOS) is a TEP state that follows it. | `teprob.f` |
| **Boundary parameter** | A TEP Fortran variable that TEFUNC reads as an input condition (valve hydraulic range, compressor capacity, random-walk mean, feed composition). The *only* channel through which enterprise state affects TEP physics. | `simulator/tep/boundary.py` |
| **Coupling** | The declarative causal relations that compute enterprise variables and boundary parameters. | `configs/coupling.yaml` |
| **Relation** | One coupling rule: `output = expression(inputs)`. | `CouplingEngine` |
| **ISA-95** | The IEC 62264 role-based equipment hierarchy used to structure the site (Enterprise > Site > Area > …). | `simulator/isa95` |
| **Equipment element** | Any node of the ISA-95 hierarchy. Has an `origin`: TEP or ENTERPRISE. | `configs/site.yaml` |
| **Asset** | A maintainable equipment element with a condition model (10 of them). | `configs/equipment.yaml` |
| **Condition / health** | Asset health, 0..1: intrinsic health minus fault damage. Simulation truth; not a plant measurement. | `EquipmentModule.health` |
| **Vibration** | The observable condition indicator derived from health. | `EquipmentModule._resolve` |
| **Capability** | What equipment can deliver given its condition, running state and power, e.g. `available_flow`, compressor `capability`. Computed by relations. | `configs/coupling.yaml` |
| **Availability (utility)** | Fraction of design capacity a utility can currently deliver (`capacity_fraction` where defined). | `UtilitiesModule._classify` |
| **Utility** | A supply service to the process: reactor CW, condenser CW, LP steam, electrical power. | `configs/utilities.yaml` |
| **Storage unit** | A raw-material tank or gas storage feeding a TEP stream; also product tanks and warehouses. | `configs/materials.yaml` |
| **Material lot** | A quantity of material with certificate attributes and a quality status; consumed FIFO. | `MaterialLot` |
| **Production lot** | One hour of produced product; disposed by quality. | `ProductionLot` |
| **Production order** | Customer order with a quantity and a status machine (PLANNED … COMPLETED). | `ProductionOrder` |
| **Work order** | A maintenance job: corrective, inspection or planned. | `WorkOrder` |
| **Alarm** | A configured condition with ISA-18.2-style states and acknowledgement. | `simulator/alarms` |
| **Event** | An immutable record on the event bus with ids, times, source, target, payload and correlation. | `simulator/events` |
| **Fault** | A benchmark-defined cause with trigger, severity, progression and duration. | `simulator/faults/engine.py` |
| **Fault type** | TEP_NATIVE_FAULT (IDV toggle) or ENTERPRISE_FAULT (cause channel or sensor overlay). | `simulator/faults/types.py` |
| **Cause channel / fault effect** | A named numeric contribution written only by the fault engine (e.g. `damage` on a pump). | `FaultEffects` |
| **Persistent fault** | A fault whose effect stays after it stops, until repair or reset. | `FaultType.persistent` |
| **Scenario** | A YAML/JSON file: seed, duration, start time, backend, faults, orders, planned maintenance, operator actions, config overrides. | `simulator/scenarios` |
| **Canonical state** | The in-memory source of truth for the whole simulation (not a UNS). | `CanonicalState` |
| **Ground truth** | Information about *causes* that plant personnel would not have: fault specs and status, cause channels, causal labels, true values that differ from transmitted ones. | [fault injection](../06_scenarios/fault_injection.md#ground-truth) |
| **Causal registry** | Map from entity key to the event that currently explains its abnormal state; source of `correlation_id`/`causation_id`. | `CausalRegistry` |
| **Correlation id** | Groups events of one incident; equals the fault id when a fault is the registered root cause. | `Event` |
| **Causation id** | Id of the immediate upstream event. | `Event` |
| **Variable graph** | The explanatory graph of variables and their connections generated by `scripts/build_variable_graph.py`. Documentation, not executed. | [graph model](../07_variable_graph/graph_model.md) |
| **Backend** | `fortran` (authoritative) or `python` (development port) implementation of the adapter. | `create_adapter` |
| **Run manifest** | The identity record of a run: ids, seeds, hashes, versions. | `SimulationEngine.run_manifest` |
| **Trend buffer** | A bounded in-memory ring buffer sampled every 10 s, for charts and exports. Not a historian. | `TrendBuffer` |
