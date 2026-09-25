# Production state

## Line summary (`state.production`, owner ProductionModule)

| Field | Unit | Meaning |
|---|---|---|
| `state` | RUNNING / REDUCED_RATE / DOWN | from a 5-min exponential average of the rate |
| `rate_kg_h` | kg/h | instantaneous metered rate (transmitted XMEAS 17 × 613.4) |
| `rate_smoothed_kg_h` | kg/h | the average used for state and projections |
| `total_kg`, `accepted_kg`, `rejected_kg` | kg | cumulative; accepted and rejected only after QC decisions |
| `run_time_s`, `down_time_s` | s | line time |
| `current_lot`, `current_order` | id | — |
| `oee` | dict | availability, performance, quality, oee |

## Production order (`ProductionOrder`)

`order_id, product_id, quantity, unit, priority (1 highest…5), planned_start, planned_end,
actual_start, actual_end, status, status_reason, released_at, produced_kg, accepted_kg, rejected_kg,
net_kg, progress, lots, projected_end, late, customer, history[]`.

Status machine: [production](../02_manufacturing_model/production.md).

## Production lot (`ProductionLot`)

`lot_id, product_id, order_id, start_s, end_s, quantity_kg, status (IN_PROCESS → AWAITING_QC →
RELEASED | QUARANTINE | REJECTED), location, samples[], qc_due_s, disposition_reason`.

Source:
- `simulator/production/__init__.py` — `ProductionModule`, `ProductionLot`
- `simulator/production/orders.py` — `ProductionOrder`
