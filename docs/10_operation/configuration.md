# Configuration

Twelve YAML files in `configs/`, one per section. `assemble_config` loads all of them for every run, and
the scenario's `config_overrides` are deep-merged per section. Everything that shapes behaviour is here
except the TEP physics, the native control constants (`control_scheme.py`) and the boundary parameter
declarations (`boundary.py`).

| File | Section | Read by | What it controls |
|---|---|---|---|
| `site.yaml` | site | `Hierarchy.from_config` | ISA-95 elements, origin, class, attributes (`xmv`, `loop_id`, `capacity_kg`, …) |
| `tep_mapping.yaml` | tep_mapping | `TEPMapping.from_config` | XMEAS/XMV/IDV → equipment/property; instrument tags |
| `equipment.yaml` | equipment | `EquipmentModule` | assets: ratings, initial health/state/run hours, wear, thresholds, vibration model, maintenance task/duration/skill/parts/restore health; changeover delay |
| `utilities.yaml` | utilities | `UtilitiesModule` | services, capacities, nominal values, status thresholds |
| `coupling.yaml` | coupling | `CouplingEngine` | causal relations; `process_influences` |
| `materials.yaml` | materials | `MaterialsModule`, `InventoryModule`, fault types | materials and TEP conversions, attributes and specs, storage, replenishment, initial lots, spare parts, heel/ramp, posting interval |
| `production.yaml` | production | `ProductionModule`, `SchedulingModule` | line metering, density, nominal rate, state thresholds, products and BOM, lot duration, QC delay, scheduling flags |
| `quality.yaml` | quality | `QualityModule`, `QualityFailure` | laboratory, sample sources, product tests, lot disposition, incoming inspection delay |
| `maintenance.yaml` | maintenance | `MaintenanceModule` | technicians and shifts, response times, jitter, request policies, inspection thresholds |
| `alarms.yaml` | alarms | `AlarmModule` | alarm definitions by category, defaults, generated saturation and BADPV alarms, maintenance links |
| `warehouse.yaml` | warehouse | `WarehouseModule` | tank roles, dispatch schedule |
| `simulation.yaml` | simulation | engine, service | trend interval and capacity, event log size, runner default speed and max steps per tick |

## Descriptive keys not used by simulation logic

These keys are reference data: they are loaded, and some are shown in the UI, but no behaviour
depends on them.

| Key | File | Note |
|---|---|---|
| `suppliers` | materials.yaml | supplier catalog; purchase orders use the supplier *ids* from `replenishment` |
| `scenario.tags` | scenario files | accepted, ignored |
| `products.*.material`, `products.*.name` | production.yaml | not used by logic |
| `utilities.services.*.serves` | utilities.yaml | stored in metadata only |

Any change to a config file changes the configuration hash and therefore the run id.

Source:
- `simulator/scenarios/__init__.py` — `load_base_config`, `assemble_config`, `CONFIG_SECTIONS`
- `simulator/common/__init__.py` — `deep_merge`
