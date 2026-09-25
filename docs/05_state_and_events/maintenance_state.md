# Maintenance state

## Work order (`WorkOrder`)

| Field | Meaning |
|---|---|
| `wo_id` | WO-nnnnn |
| `asset_id` | asset, or work center for inspections |
| `kind` | corrective / inspection / planned; corrective and planned isolate the asset |
| `priority` | 1..4 → response time |
| `status` | REQUESTED, WAITING_PARTS, WAITING_TECHNICIAN, SCHEDULED, IN_PROGRESS, COMPLETED, CANCELLED |
| `technician`, `skill`, `parts{}` | assignment and requirements |
| `requested_s`, `scheduled_start_s`, `actual_start_s`, `completed_s`, `duration_s` | timing (duration includes ±10 % seeded jitter) |
| `wait_reason`, `findings[]` | why waiting; inspection findings |
| `requested_by`, `description`, `restore_health` | — |

Internally it also keeps `correlation_id` and `request_event`, which link all later maintenance events
of the order to the request event.

## Technician (`Technician`)

`id, name, skills[], shift (HH:00-HH:00), status (AVAILABLE / BUSY / OFF_SHIFT), work_order`.

## Spare part (`SparePart`)

`part_id, name, location, on_hand, reserved, available (= 0 if blocked), reorder_point, order_qty,
lead_time_h, blocked`.

Events: MAINTENANCE_REQUESTED / SCHEDULED / WAITING / STARTED / COMPLETED. Cancellation is published
as MAINTENANCE_WAITING with `status: CANCELLED`, because there is no dedicated event type.

Source:
- `simulator/maintenance/__init__.py` — `WorkOrder`, `Technician`, `MaintenanceModule.cancel`
- `simulator/inventory/__init__.py` — `SparePart`
