# Node types

Every node has `id, layer, cat, label, sub, detail, authority`. `cat` is the category (colour in the
viewer); `authority` states where the node's value is authoritatively determined.

| Category (`cat`) | Count | Column | Id pattern | What it is | Authority |
|---|---|---|---|---|---|
| `fault` | 20 | 0 | `fault.<TARGET>.<channel>` | a fault cause channel (efficiency_loss ×6, capacity_loss ×4, thermal_degradation ×1, damage ×9) | benchmark ground truth, `FaultEffects` |
| `equipment` | 35 | 1 | `<ASSET>.<property>` | asset condition and capability: health, is_running, efficiency, available_flow, capability, supply_fraction, vibration … | EquipmentModule or a coupling relation |
| `utility` | 36 | 2 (supply) / 7 (observations) | `UT-*.<property>` | utility capacity, fraction, supply temperature (column 2); flow, demand, utilization, pressure, return temperature, voltage (column 7) | coupling relations; status by UtilitiesModule |
| `material` | 17 | 2 / 7 | `SU-*.<property>` | storage supply availability and lot deviations (column 2); consumption and quantity (column 7) | InventoryModule |
| `boundary` | 15 | 3 | `tep.boundary.<name>` | the 15 coupled TEP boundary parameters | TEP memory, written only by coupling |
| `xmv` | 12 | 4 | `XMV(n)` | manipulated variables | TEP `/PV/`, written by native control or operator |
| `xmeas` | 33 | 5 | `XMEAS(n)` | process measurements with at least one edge | TEP TEFUNC |
| `control` | 19 | 6 | `LOOP:<tag>` | native control loops (CONTRLn) | Fortran subroutine |
| `enterprise` | 8 | 7 | `QT:<test>`, `LOT.disposition`, `PROD.rate_kg_h`, `PROD.order_progress`, `MAINT.work_order` | derived enterprise quantities that are not entity properties | Quality, Production, Maintenance modules |

**Nodes that are not canonical-state properties:** `LOOP:*`, `QT:*`, `LOT.disposition`, `PROD.*`,
`MAINT.work_order`. They stand for records or computations (sample results, lot status, the production
summary, work orders). `SU-*.consumption_kg_h` and `SU-*.quantity_kg` are real storage properties.

Source:
- `scripts/build_variable_graph.py` — `build`
