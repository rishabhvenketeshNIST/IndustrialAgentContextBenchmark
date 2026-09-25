"""Warehouse module: product tank farm inventory, QC-driven lot movements and dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ..events import Event, EventType
from ..simulation.module import ModuleContext, SimulationModule


@dataclass
class Shipment:
    shipment_id: str
    due_s: int
    quantity_kg: float
    status: str = "SCHEDULED"      # SCHEDULED | SHIPPED | DELAYED
    shipped_s: Optional[int] = None
    shipped_kg: float = 0.0

    def to_dict(self) -> dict:
        return dict(vars(self))


class WarehouseModule(SimulationModule):
    name = "warehouse"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("warehouse")
        self.loc = cfg.get("locations", {})
        d = cfg.get("dispatch", {})
        self.first = int(d.get("first_at_s", 5400))
        self.interval = int(d.get("interval_s", 3600))
        self.qty = float(d.get("quantity_kg", 12000))
        self.shipped_total = 0.0
        self._seq = 0
        self._released_stock: Dict[str, float] = {}   # lot -> remaining kg in released tank
        ctx.state.collection("shipments")
        for key in ("rundown", "released", "rejected"):
            sid = self.loc.get(key)
            if sid and ctx.state.has_entity(sid):
                ctx.state.entity(sid).properties.update({"quantity_kg": 0.0, "material": "MAT-GH", "lots": []})
                ctx.state.entity(sid).units["quantity_kg"] = "kg"
        ctx.bus.subscribe(self._on_lot_state, [EventType.LOT_STATE_CHANGED])

    def _on_lot_state(self, ev: Event) -> None:
        if ev.payload.get("lot_type") != "product":
            return
        lot = self.ctx.state.collection("production_lots").get(ev.payload.get("lot_id"))
        if lot is None:
            return
        new = ev.payload.get("new")
        dest = {"RELEASED": self.loc.get("released"), "REJECTED": self.loc.get("rejected")}.get(new)
        if dest and lot.location != dest:
            src = lot.location
            lot.location = dest
            if new == "RELEASED":
                self._released_stock[lot.lot_id] = lot.quantity_kg
            self.ctx.bus.publish(EventType.INVENTORY_MOVED, self.name, dest,
                                 {"lot_id": lot.lot_id, "from": src, "to": dest,
                                  "quantity_kg": round(lot.quantity_kg, 1), "reason": f"QC disposition {new}"},
                                 cause=ev)

    def post_step(self, t: int) -> None:
        st = self.ctx.state
        lots = st.collection("production_lots").values()
        for key in ("rundown", "released", "rejected"):
            sid = self.loc.get(key)
            if not sid or not st.has_entity(sid):
                continue
            if key == "released":
                qty = sum(self._released_stock.values())
                here = sorted(k for k, v in self._released_stock.items() if v > 1e-6)
            else:
                here = sorted(l.lot_id for l in lots if l.location == sid)
                qty = sum(l.quantity_kg for l in lots if l.location == sid)
            cap = float(st.hierarchy.get(sid).attributes.get("capacity_kg", 0) or 0)
            st.entity(sid).properties.update({"quantity_kg": round(qty, 2), "lots": here,
                                              "level_pct": round(100 * qty / cap, 3) if cap else None})
        if t >= self.first and (t - self.first) % self.interval == 0:
            self._dispatch(t)
        for sh in st.collection("shipments").values():
            if sh.status == "DELAYED" and self._available() >= sh.quantity_kg:
                self._ship(sh, t)

    def _available(self) -> float:
        return sum(self._released_stock.values())

    def _dispatch(self, t: int) -> None:
        self._seq += 1
        sh = Shipment(f"SH-{self._seq:04d}", t, self.qty)
        self.ctx.state.collection("shipments")[sh.shipment_id] = sh
        if self._available() >= self.qty:
            self._ship(sh, t)
        else:
            sh.status = "DELAYED"
            self.ctx.bus.publish(EventType.INVENTORY_MOVED, self.name, self.loc.get("dispatch"),
                                 {"shipment_id": sh.shipment_id, "status": "DELAYED",
                                  "required_kg": self.qty, "released_stock_kg": round(self._available(), 1)},
                                 severity="warning")

    def _ship(self, sh: Shipment, t: int) -> None:
        need = sh.quantity_kg
        taken = {}
        for lot_id in sorted(self._released_stock):
            if need <= 0:
                break
            d = min(self._released_stock[lot_id], need)
            self._released_stock[lot_id] -= d
            need -= d
            taken[lot_id] = round(d, 1)
        sh.status, sh.shipped_s, sh.shipped_kg = "SHIPPED", t, sh.quantity_kg - need
        self.shipped_total += sh.shipped_kg
        self.ctx.bus.publish(EventType.INVENTORY_MOVED, self.name, self.loc.get("dispatch"),
                             {"shipment_id": sh.shipment_id, "status": "SHIPPED", "quantity_kg": sh.shipped_kg,
                              "lots": taken, "from": self.loc.get("released")})

    def summary(self) -> dict:
        st = self.ctx.state
        return {"tanks": {self.loc[k]: {"quantity_kg": st.get(self.loc[k], "quantity_kg"),
                                        "lots": st.get(self.loc[k], "lots")}
                          for k in ("rundown", "released", "rejected") if self.loc.get(k)},
                "shipped_total_kg": round(self.shipped_total, 1)}
