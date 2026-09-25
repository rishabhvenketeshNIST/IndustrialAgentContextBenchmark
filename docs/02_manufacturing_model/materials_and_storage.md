# Materials, storage, inventory and warehouse

## What they represent

* **Materials** (`MaterialsModule`, `configs/materials.yaml`): four raw materials matching TEP feed
  streams, plus the finished product.

  | Material | TEP stream | Measurement | Conversion |
  |---|---|---|---|
  | MAT-A | 1 | XMEAS(1) | kscmh → kg/h |
  | MAT-D | 2 | XMEAS(2) | already kg/h |
  | MAT-E | 3 | XMEAS(3) | already kg/h |
  | MAT-AC | 4 | XMEAS(4) | kscmh → kg/h |
  | MAT-GH | product (finished good) | — | — |

  The kscmh conversion follows TEFUNC's unit equations (`0.359/35.3145`, lb→kg `0.454`) and the TEINIT
  stream compositions (`kg_per_h_factor`): 89.42 kg/h per kscmh for stream 1, 686.73 for stream 4.
* **Storage units** (`InventoryModule`): SU-SPH-103 (A gas), SU-TK-101 (D), SU-TK-102 (E), SU-SPH-104
  (A/C gas). These are enterprise-invented equipment feeding the TEP streams.
* **Material lots:** quantity, certificate attributes (e.g. `feed_impurity` 0.0001), and quality
  status RELEASED / QUARANTINE / REJECTED / CONSUMED.
* **Spare parts** in the MRO store, and **purchase orders**.
* **Warehouse** (`WarehouseModule`): product tanks TK-501 (rundown), TK-502 (released), TK-503
  (off-spec), and hourly dispatch shipments.

## Why they exist

Feed availability and feed quality are real constraints on a chemical plant. Stock-outs and
off-spec deliveries are also realistic benchmark faults.

## Inventory behaviour

Every second:

* **Pre-step:**
  1. Apply stock-loss faults.
  2. Receive due purchase orders. Delivered lots start in QUARANTINE until incoming inspection.
  3. Compute per storage: `usable_kg` (RELEASED lots), `reserved_kg`, `available_kg`, `level_pct`,
     `status`, and `supply_availability = clip((usable − heel)/ramp, 0, 1)` (heel 2 %, ramp 1 % of
     capacity).
  4. Expose the current FIFO lot's attributes, and their deviation from the certificate nominal, as
     properties.
* **Post-step:**
  1. Consume `true feed flow × conversion` FIFO. This uses the **true** XMEAS, because a tank drains
     at the physical rate even if a transmitter is biased. Consumption is zero after a trip.
  2. Reorder when on-hand quantity ≤ reorder point, if no PO is open.
  3. Post a `MATERIAL_CONSUMED` event every 900 s.
  4. Emit `MATERIAL_SHORTAGE` / `MATERIAL_SHORTAGE_CLEARED` when supply availability crosses 1.

**Couplings to TEP** ([equipment_to_tep](../04_coupling/equipment_to_tep.md)):

* `supply_availability` → `*_feed_max_flow` (VRNG 1-4). An empty tank means no feed at any valve
  opening.
* Lot attribute *deviation* → `d_feed_b_impurity`, `e_feed_f_impurity`, `a_feed_b_impurity` (XST) and
  `stream4_a/b_fraction_mean` (SZERO 1/2).

**Reservations:**

* `PRODUCTION_ORDER_RELEASED` → reserve BOM × remaining quantity per storage unit.
* Consumption draws reservations down.
* Order completion or cancellation releases them.
* `MAINTENANCE_SCHEDULED` reserves spare parts, and `MAINTENANCE_STARTED` issues them.

## Warehouse behaviour

* `LOT_STATE_CHANGED` RELEASED → the lot moves to TK-502. REJECTED → TK-503. QUARANTINE stays in
  TK-501.
* Dispatch every 3600 s from 5400 s ships 12 t from released stock (FIFO by lot id), or marks the
  shipment DELAYED and retries.

Product quantity in the tanks is the lot quantity metered by production. Product is **not** a material
lot.

## Worked example: D feed replenishment (baseline and demo)

TK-101 starts at 30 t with reorder point 24 t. D consumption is ≈ 3660 kg/h, so the reorder happens at
about 1.6 h (demo: `MATERIAL_ORDERED` at 5895 s). Delivery comes after 1.0 ± 0.25 h (seeded jitter) as
a QUARANTINE lot, followed by incoming inspection 600 s later (`LOT_STATE_CHANGED` → RELEASED).

## What they do NOT control

Inventory does not change feed flow directly. It only removes *availability*, and TEP's own flow
equation does the rest. It does not model tank temperature, pressure or mixing: lots are segregated
FIFO, and a new lot's composition applies as a step (for XST streams) or at the next walk knot (SZERO).

## Configuration notes

`configs/materials.yaml → suppliers` (names) is **not read** by the code. Supplier ids appear only as
strings in `replenishment` and lots.

Source:
- `configs/materials.yaml`
- `configs/warehouse.yaml`
- `simulator/materials/__init__.py` — `kg_per_h_factor`, `MaterialsModule`, `MaterialLot`
- `simulator/inventory/__init__.py` — `InventoryModule._refresh_storage`, `InventoryModule.post_step`, `InventoryModule._consume`, `InventoryModule._receive_deliveries`, `InventoryModule._apply_stock_loss`
- `simulator/warehouse/__init__.py` — `WarehouseModule._on_lot_state`, `WarehouseModule._dispatch`
- `tests/test_enterprise.py` — `test_inventory_consumption_matches_metered_feed`, `test_replenishment_orders_and_receives_material`, `test_order_blocked_by_material_shortage`
