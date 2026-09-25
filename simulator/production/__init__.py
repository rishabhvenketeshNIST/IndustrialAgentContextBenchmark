"""Production module: line state, metered production, production lots, order progress, KPIs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..common import fmt_hms
from ..events import Event, EventType
from ..simulation.module import ModuleContext, SimulationModule
from ..tep import catalog
from .orders import ProductionOrder, transition

LINE_STATES = ("RUNNING", "REDUCED_RATE", "DOWN")


@dataclass
class ProductionLot:
    lot_id: str
    product_id: str
    order_id: Optional[str]
    start_s: int
    end_s: Optional[int] = None
    quantity_kg: float = 0.0
    status: str = "IN_PROCESS"        # IN_PROCESS | AWAITING_QC | RELEASED | QUARANTINE | REJECTED
    location: str = "SU-TK-501"
    samples: List[str] = field(default_factory=list)
    qc_due_s: Optional[int] = None
    disposition_reason: str = ""

    def to_dict(self) -> dict:
        return {"lot_id": self.lot_id, "product_id": self.product_id, "order_id": self.order_id,
                "start_s": self.start_s, "end_s": self.end_s, "start_hms": fmt_hms(self.start_s),
                "end_hms": fmt_hms(self.end_s) if self.end_s is not None else None,
                "quantity_kg": round(self.quantity_kg, 2), "status": self.status, "location": self.location,
                "samples": list(self.samples), "qc_due_s": self.qc_due_s,
                "disposition_reason": self.disposition_reason}


class ProductionModule(SimulationModule):
    name = "production"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("production")
        line = cfg["line"]
        self.line_id = line["id"]
        self.flow_idx = int(catalog.normalize_id(line["product_flow_measurement"])[6:-1])
        self.density = float(line["product_density_kg_m3"])
        self.nominal = float(line["nominal_rate_kg_h"])
        self.reduced_frac = float(line.get("reduced_rate_fraction", 0.9))
        self.down_frac = float(line.get("down_rate_fraction", 0.05))
        self.rundown = line.get("rundown_tank", "SU-TK-501")
        self.products = cfg.get("products", {})
        self.default_product = next(iter(self.products))
        lots_cfg = cfg.get("lots", {})
        self.lot_duration = int(lots_cfg.get("duration_s", 3600))
        self.lot_prefix = lots_cfg.get("prefix", "PL")
        self.qc_delay = int(lots_cfg.get("qc_decision_delay_s", 900))
        self.pause_when_down = bool(cfg.get("scheduling", {}).get("pause_when_down", True))
        self._lot_seq = 0
        self._current_lot: Optional[ProductionLot] = None
        self._lot_run_s = 0
        self._rate_ema: Optional[float] = None
        self._state = "RUNNING"
        ctx.state.collection("production_lots")
        ctx.state.production.update({
            "line_id": self.line_id, "state": "RUNNING", "rate_kg_h": 0.0, "rate_smoothed_kg_h": 0.0,
            "nominal_rate_kg_h": self.nominal, "total_kg": 0.0, "accepted_kg": 0.0, "rejected_kg": 0.0,
            "run_time_s": 0, "down_time_s": 0, "current_lot": None, "current_order": None,
            "oee": {"availability": 1.0, "performance": 1.0, "quality": 1.0, "oee": 1.0}})
        ctx.bus.subscribe(self._on_lot_state, [EventType.LOT_STATE_CHANGED])

    # ------------------------------------------------------------------ helpers
    def running_order(self) -> Optional[ProductionOrder]:
        for o in sorted(self.ctx.state.collection("production_orders").values(), key=lambda o: o.order_id):
            if o.status == "RUNNING":
                return o
        return None

    def _open_lot(self, t: int, order: Optional[ProductionOrder]) -> None:
        self._lot_seq += 1
        lot = ProductionLot(f"{self.lot_prefix}-{self._lot_seq:04d}", order.product_id if order else
                            self.default_product, order.order_id if order else None, t, location=self.rundown)
        self.ctx.state.collection("production_lots")[lot.lot_id] = lot
        self._current_lot = lot
        self._lot_run_s = 0
        if order is not None:
            order.lots.append(lot.lot_id)
        self.ctx.bus.publish(EventType.LOT_STATE_CHANGED, self.name, lot.lot_id,
                             {"lot_id": lot.lot_id, "lot_type": "product", "old": None, "new": "IN_PROCESS",
                              "order_id": lot.order_id})

    def _close_lot(self, t: int, reason: str) -> None:
        lot = self._current_lot
        if lot is None:
            return
        lot.end_s = t
        lot.status = "AWAITING_QC"
        lot.qc_due_s = t + self.qc_delay
        self._current_lot = None
        self.ctx.bus.publish(EventType.LOT_STATE_CHANGED, self.name, lot.lot_id,
                             {"lot_id": lot.lot_id, "lot_type": "product", "old": "IN_PROCESS", "new": "AWAITING_QC",
                              "quantity_kg": round(lot.quantity_kg, 1), "reason": reason, "order_id": lot.order_id,
                              "qc_due_s": lot.qc_due_s})

    # ------------------------------------------------------------------ simulation
    def post_step(self, t: int) -> None:
        st = self.ctx.state
        p = st.process
        rate = 0.0 if p.shutdown else max(0.0, float(p.xmeas[self.flow_idx - 1])) * self.density
        a = 1.0 / 300.0   # 5-minute smoothing for state classification
        self._rate_ema = rate if self._rate_ema is None else self._rate_ema + a * (rate - self._rate_ema)
        if p.shutdown or self._rate_ema < self.down_frac * self.nominal:
            state = "DOWN"
        elif self._rate_ema < self.reduced_frac * self.nominal:
            state = "REDUCED_RATE"
        else:
            state = "RUNNING"
        prod = st.production
        if state != self._state:
            ref = st.causal.get(self.line_id) or st.causal.get("XMEAS(17)")
            kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
            self.ctx.bus.publish(EventType.PRODUCTION_STATE_CHANGED, self.name, self.line_id,
                                 {"old": self._state, "new": state, "rate_kg_h": round(self._rate_ema, 1),
                                  "reason": p.shutdown_reason if p.shutdown else ""},
                                 severity="critical" if state == "DOWN" else "warning" if state != "RUNNING"
                                 else "info", **kw)
            self._state = state
            if state == "DOWN":
                self._handle_down(t)
            else:
                self._handle_up(t)
        kg = rate / 3600.0
        order = self.running_order()
        if state != "DOWN":
            prod["run_time_s"] += 1
            if self._current_lot is None:
                self._open_lot(t, order)
            elif (self._current_lot.order_id != (order.order_id if order else None)):
                self._close_lot(t, "order changed")
                self._open_lot(t, order)
            self._current_lot.quantity_kg += kg
            self._lot_run_s += 1
            if self._lot_run_s >= self.lot_duration:
                self._close_lot(t, "lot duration reached")
        else:
            prod["down_time_s"] += 1
        prod["total_kg"] += kg
        if order is not None and state != "DOWN":
            order.produced_kg += kg
            if order.net_kg >= order.quantity:
                transition(self.ctx, order, "COMPLETED", "ordered quantity produced")
                if self._current_lot is not None and self._current_lot.order_id == order.order_id:
                    self._close_lot(t, "order completed")
        prod.update({"state": state, "rate_kg_h": round(rate, 3), "rate_smoothed_kg_h": round(self._rate_ema, 3),
                     "current_lot": self._current_lot.lot_id if self._current_lot else None,
                     "current_order": order.order_id if order else None})
        self._update_kpis(t)

    def _handle_down(self, t: int) -> None:
        if self._current_lot is not None:
            self._close_lot(t, "production down")
        if self.pause_when_down:
            o = self.running_order()
            if o is not None:
                transition(self.ctx, o, "PAUSED", "production line down")

    def _handle_up(self, t: int) -> None:
        if not self.pause_when_down:
            return
        for o in sorted(self.ctx.state.collection("production_orders").values(), key=lambda o: o.order_id):
            if o.status == "PAUSED" and o.status_reason == "production line down":
                transition(self.ctx, o, "RUNNING", "production line restored")
                break

    def _update_kpis(self, t: int) -> None:
        prod = self.ctx.state.production
        elapsed = max(t + 1, 1)
        run = max(prod["run_time_s"], 1)
        availability = prod["run_time_s"] / elapsed
        performance = min(1.5, prod["total_kg"] / (self.nominal * run / 3600.0))
        decided = prod["accepted_kg"] + prod["rejected_kg"]
        quality = prod["accepted_kg"] / decided if decided > 0 else 1.0
        prod["oee"] = {"availability": round(availability, 4), "performance": round(performance, 4),
                       "quality": round(quality, 4), "oee": round(availability * min(performance, 1.0) * quality, 4)}

    # ------------------------------------------------------------------ events
    def _on_lot_state(self, ev: Event) -> None:
        if ev.payload.get("lot_type") != "product" or ev.source == self.name:
            return
        lot = self.ctx.state.collection("production_lots").get(ev.payload.get("lot_id"))
        if lot is None:
            return
        new = ev.payload["new"]
        lot.status = new
        lot.disposition_reason = ev.payload.get("reason", "")
        prod = self.ctx.state.production
        order = self.ctx.state.collection("production_orders").get(lot.order_id) if lot.order_id else None
        if new == "RELEASED":
            prod["accepted_kg"] += lot.quantity_kg
            if order:
                order.accepted_kg += lot.quantity_kg
        elif new == "REJECTED":
            prod["rejected_kg"] += lot.quantity_kg
            if order:
                order.rejected_kg += lot.quantity_kg
                if order.status == "COMPLETED":
                    order.status_reason = "completed; later lot rejection reduced net quantity"

    def summary(self) -> dict:
        return dict(self.ctx.state.production)
