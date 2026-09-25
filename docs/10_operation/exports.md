# Exports

Exports are simulation output, not a historian. They can be taken at any time (in progress or
completed).

| Route / tool | Format | File name |
|---|---|---|
| `GET /api/benchmark/export/json` | one JSON bundle | `<run_id>.json` |
| `GET /api/benchmark/export/csv` | zip of CSV files + manifest | `<run_id>.zip` |
| `python scripts/run_demo.py` | both, into `exports/` | same |

## JSON bundle (`build_bundle`)

| Key | Content | Resolution |
|---|---|---|
| `run_manifest` | `run_manifest()` incl. faults summary, shutdown | — |
| `events` | **all** events incl. benchmark (FAULT_*) | every event |
| `measurements` | `XMEAS(n)` transmitted and `TRUE:XMEAS(n)` | trend buffer, 10 s |
| `measurement_catalog` | catalog entries | — |
| `manipulated_variables`, `setpoints` | XMV, SP:tag series | 10 s |
| `equipment_states` | health and efficiency series + final asset properties | 10 s + final |
| `utilities` | utility series + final properties | 10 s + final |
| `alarms` | alarms that activated at least once (final state, counts) | final |
| `faults` | full fault records | final |
| `maintenance` | work orders, technicians | final |
| `inventory` | storage summary, lots, spare parts, POs, level series | final + 10 s |
| `quality` | samples, production lots | final |
| `production_orders`, `production`, `loops` | — | final |

## CSV zip

`run_manifest.json`, `events.csv`, `measurements.csv`, `manipulated_variables.csv`, `setpoints.csv`,
`equipment_states.csv`, `utilities.csv`, `inventory_levels.csv`, `alarms.csv`, `faults.csv`,
`work_orders.csv`, `material_lots.csv`, `purchase_orders.csv`, `spare_parts.csv`,
`quality_samples.csv`, `production_lots.csv`, `production_orders.csv`.

## Important properties

* **Time series come from the 10-second trend buffer**, not per-second data. Per-second history is not
  kept.
* **Exports contain full ground truth:** fault events, fault records, true measurements, health. They
  are benchmark artefacts. Do not give them to a system under test.
* **Alarm history is summarised.** Only the final state and activation count per alarm are exported;
  individual activations are in `events`.

Source:
- `api/export.py` — `build_bundle`, `build_csv_zip`
- `scripts/run_demo.py` — `main`
- `tests/test_api_ui.py` — `test_export`
