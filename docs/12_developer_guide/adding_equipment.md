# Adding equipment

## 1. Add the ISA-95 element

In `configs/site.yaml`, under a parent that is allowed by `ALLOWED_CHILDREN` (e.g. a WorkUnit under a
WorkCenter, or an EquipmentModule under a ProductionUnit):

```yaml
- {id: WU-CWP-101C, name: Reactor CW Pump P-101C (spare), level: WorkUnit, origin: ENTERPRISE, class: centrifugal_pump}
```

`origin: TEP` only for equipment that exists in the Downs & Vogel flowsheet. Validation runs at engine
start (`Hierarchy.validate`).

## 2. Make it a maintainable asset (optional)

In `configs/equipment.yaml → assets`:

```yaml
WU-CWP-101C:
  asset_type: centrifugal_pump          # used by fault target lists (e.g. pump_efficiency_loss)
  redundancy_group: RG-CW-REACTOR       # joins auto-changeover
  rated: {flow: 1100.0, power_kw: 90.0} # exposed as rated_flow, rated_power_kw properties
  initial: {health: 1.0, state: STANDBY, run_hours: 0}
  maintenance: {task: ..., duration_min: 60, skill: mechanical, parts: {SP-IMP-101: 1}, restore_health: 0.98}
```

The equipment module then maintains `health`, `status`, `is_running`, `vibration`, `run_hours`, `role`.
The asset automatically becomes a valid target of `equipment_degradation`, `equipment_failure` and
(pumps, compressor, boiler) `pump_efficiency_loss`.

## 3. Give it an effect (coupling)

Equipment affects nothing until a relation reads it. For the pump above, extend the reactor CW relations
in `configs/coupling.yaml`:

* add `WU-CWP-101C` to the `for_each` lists of `CR-PUMP-EFFICIENCY` and `CR-PUMP-FLOW`;
* add an input `c: WU-CWP-101C.available_flow` to `CR-CW-RX-CAPACITY` and use `(a + b + c)`;
* update `CR-POWER-PROCESS-DEMAND` if it draws power.

## 4. Observability (optional)

Add alarms in `configs/alarms.yaml → equipment`, for example a vibration alarm with
`maintenance: corrective`.

## 5. Contract

New property names need a semantic class in `contract/canonical_contract.yaml` (`properties`), and
`tests/test_contract.py` fails until they have one. See [contract §23](../CANONICAL_SIMULATOR_CONTRACT.md#23-versioning)
for how to version the change.

## 6. Check

```bash
python -m pytest
python scripts/build_variable_graph.py        # the new nodes and edges appear
python scripts/generate_docs_tables.py        # the entity table and variable reference update
```

Verify that a healthy run is still native: `test_coupling_baseline_is_native_and_graph_is_acyclic` must
pass. If the new equipment lowers a boundary value at t = 0, adjust margins.

**Do not** bind new equipment to TEP variables that already have a binding.
`configs/tep_mapping.yaml` maps each XMEAS and XMV exactly once.

Source:
- `configs/site.yaml`
- `configs/equipment.yaml`
- `simulator/isa95/__init__.py` — `ALLOWED_CHILDREN`
- `simulator/equipment/__init__.py` — `EquipmentModule.setup`
