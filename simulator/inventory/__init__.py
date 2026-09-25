"""Inventory module: stock in storage units, FIFO lot consumption, reservations,
replenishment (reorder point), shortages, spare parts and purchase orders.

Physical consumption uses the *true* TEP feed flows (the tank physically drains
at the true rate even if a flow transmitter is biased). The storage unit's
supply availability and the deviations of the consumed lot's attributes from their
nominal values (``*_deviation`` properties) are exposed as properties that the
coupling model feeds back to TEP boundary parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..common import ConfigError
from ..events import Event, EventType
from ..materials import MaterialLot
from ..simulation.module import ModuleContext, SimulationModule
from ..tep import catalog


@dataclass
class PurchaseOrder:
    po_id: str
    item_id: str
    item_type: str            # material | spare_part
    location: str
    quantity: float
    supplier: Optional[str]
    ordered_s: int
    due_s: int
    status: str = "OPEN"      # OPEN | DELAYED | RECEIVED | CANCELLED
    received_s: Optional[int] = None

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass
class SparePart:
    part_id: str
    name: str
    location: str
    on_hand: int
    reserved: int
    reorder_point: int
    order_qty: int
    lead_time_h: float
    blocked: bool = False

    @property
    def available(self) -> int:
        return 0 if self.blocked else max(0, self.on_hand - self.reserved)

    def to_dict(self) -> dict:
        d = dict(vars(self))
        d["available"] = self.available
        return d


class InventoryModule(SimulationModule):
    name = "inventory"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("materials")
        self.storage: Dict[str, dict] = cfg.get("storage", {}) or {}
        self.replen: Dict[str, dict] = cfg.get("replenishment", {}) or {}
        sa = cfg.get("supply_availability", {})
        self.heel = float(sa.get("heel_fraction", 0.02))
        self.ramp = float(sa.get("ramp_fraction", 0.01))
        self.low = float(cfg.get("low_level_fraction", 0.2))
        self.posting_interval = int(cfg.get("posting_interval_s", 900))
        self.materials_cfg = cfg.get("materials", {}) or {}
        self._rng = ctx.rng.get("inventory")
        self._posted: Dict[str, float] = {}
        self._consumed_by_lot: Dict[str, Dict[str, float]] = {}
        self._shortage: Dict[str, bool] = {}
        self._status: Dict[str, str] = {}
        self._stock_loss_applied: Dict[tuple, float] = {}
        self._reservations: Dict[str, Dict[str, float]] = {}
        self._po_seq = 0
        self._lot_seq = 0
        lots = ctx.state.collection("material_lots")
        for sid, spec in self.storage.items():
            if not ctx.state.has_entity(sid):
                raise ConfigError(f"Storage '{sid}' is not an equipment element")
            mid = spec["material"]
            if mid not in self.materials_cfg:
                raise ConfigError(f"Storage {sid} holds unknown material {mid}")
            total = sum(l.quantity_kg for l in lots.values() if l.location == sid)
            if abs(total - float(spec["initial_kg"])) > 1e-6:
                raise ConfigError(f"Initial lots in {sid} sum to {total} kg, expected {spec['initial_kg']}")
            rec = ctx.state.entity(sid)
            rec.properties.update({"material": mid, "capacity_kg": float(spec["capacity_kg"]), "reserved_kg": 0.0})
            rec.units.update({"quantity_kg": "kg", "capacity_kg": "kg", "reserved_kg": "kg", "available_kg": "kg",
                              "level_pct": "%", "consumption_kg_h": "kg/h"})
            rec.meta["unobservable"] = sorted(self.materials_cfg[mid].get("attributes", {}).keys()) + \
                ["supply_availability"]
            self._posted[sid] = 0.0
            self._shortage[sid] = False
            self._status[sid] = "NORMAL"
            self._refresh_storage(sid, publish=False)
        parts = ctx.state.collection("spare_parts")
        for pid, p in (cfg.get("spare_parts") or {}).items():
            parts[pid] = SparePart(pid, p["name"], p.get("location", "SU-WH-MRO"), int(p["on_hand"]), 0,
                                   int(p.get("reorder_point", 0)), int(p.get("order_qty", 1)),
                                   float(p.get("lead_time_h", 72)))
        ctx.state.collection("purchase_orders")
        bus = ctx.bus
        bus.subscribe(self._on_order_released, [EventType.PRODUCTION_ORDER_RELEASED])
        bus.subscribe(self._on_order_closed, [EventType.PRODUCTION_ORDER_COMPLETED,
                                              EventType.PRODUCTION_ORDER_CANCELLED])
        bus.subscribe(self._on_maintenance_scheduled, [EventType.MAINTENANCE_SCHEDULED])
        bus.subscribe(self._on_maintenance_started, [EventType.MAINTENANCE_STARTED])
        bus.subscribe(self._on_lot_state, [EventType.LOT_STATE_CHANGED])

    # ------------------------------------------------------------------ helpers
    def _lots(self, sid: str, usable_only: bool = True) -> List[MaterialLot]:
        lots = [l for l in self.ctx.state.collection("material_lots").values()
                if l.location == sid and l.quantity_kg > 1e-9
                and (not usable_only or l.quality_status == "RELEASED")]
        return sorted(lots, key=lambda l: (l.received_s, l.lot_id))

    def _current_lot(self, sid: str) -> Optional[MaterialLot]:
        lots = self._lots(sid)
        return lots[0] if lots else None

    def _refresh_storage(self, sid: str, publish: bool = True) -> None:
        st = self.ctx.state
        spec = self.storage[sid]
        mid = spec["material"]
        cap = float(spec["capacity_kg"])
        usable = sum(l.quantity_kg for l in self._lots(sid))
        total = sum(l.quantity_kg for l in self._lots(sid, usable_only=False))
        reserved = min(sum(r.get(sid, 0.0) for r in self._reservations.values()), usable)
        heel_kg, ramp_kg = self.heel * cap, max(self.ramp * cap, 1e-6)
        availability = max(0.0, min(1.0, (usable - heel_kg) / ramp_kg))
        lot = self._current_lot(sid)
        nominal = self.materials_cfg[mid].get("attributes", {})
        attrs = dict(nominal)
        if lot is not None:
            attrs.update(lot.attributes)
        fe = st.fault_effects
        for k in list(attrs):
            attrs[k] = attrs[k] + fe.value(f"{mid}.{k}", "value") + fe.value(f"{sid}.{k}", "value")
        # deviation from the material's nominal (certificate) composition; the coupling model adds it
        # to the TEINIT value so nominal lots reproduce the native boundary exactly
        attrs.update({f"{k}_deviation": attrs[k] - float(nominal.get(k, attrs[k])) for k in list(attrs)})
        level = 100.0 * total / cap
        props = st.entity(sid).properties
        props.update({"quantity_kg": round(total, 3), "usable_kg": round(usable, 3),
                      "reserved_kg": round(reserved, 3), "available_kg": round(max(usable - reserved, 0.0), 3),
                      "level_pct": round(level, 3), "supply_availability": availability,
                      "current_lot": lot.lot_id if lot else None})
        props.update(attrs)
        if availability < 1.0:
            status = "EMPTY" if availability <= 0.0 else "CRITICAL"
        elif usable < self.low * cap * 0.5:
            status = "CRITICAL"
        elif usable < self.low * cap:
            status = "LOW"
        else:
            status = "NORMAL"
        props["status"] = status
        if publish:
            short = availability < 1.0
            if short != self._shortage[sid]:
                self._shortage[sid] = short
                ref = st.causal.get(sid) or st.causal.get(mid)
                kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
                self.ctx.bus.publish(EventType.MATERIAL_SHORTAGE if short else EventType.MATERIAL_SHORTAGE_CLEARED,
                                     self.name, sid, {"material": mid, "usable_kg": round(usable, 1),
                                                      "supply_availability": round(availability, 4)},
                                     severity="critical" if short else "info", **kw)
        self._status[sid] = status

    # ------------------------------------------------------------------ simulation
    def pre_step(self, t: int) -> None:
        self._apply_stock_loss(t)
        self._receive_deliveries(t)
        self._update_parts_blocking()
        for sid in self.storage:
            self._refresh_storage(sid)

    def post_step(self, t: int) -> None:
        st = self.ctx.state
        xtrue = st.process.xmeas_true
        for sid, spec in self.storage.items():
            mid = spec["material"]
            tep = self.materials_cfg[mid].get("tep")
            if not tep:
                continue
            idx = int(catalog.normalize_id(tep["measurement"])[6:-1])
            factor = float(st.entity(mid).meta["kg_per_h_per_measurement_unit"])
            # after an interlock trip TEFUNC freezes (flows keep their last value); feeds are stopped
            rate = 0.0 if st.process.shutdown else max(0.0, float(xtrue[idx - 1]) * factor)
            st.set(sid, "consumption_kg_h", round(rate, 4), "kg/h")
            self._consume(sid, rate / 3600.0)
            self._refresh_storage(sid)
            self._check_reorder(sid, t)
        if t > 0 and t % self.posting_interval == 0:
            self._post_consumption()
        self._check_parts_reorder(t)

    def _consume(self, sid: str, kg: float) -> None:
        remaining = kg
        for lot in self._lots(sid):
            if remaining <= 0:
                break
            take = min(lot.quantity_kg, remaining)
            lot.quantity_kg -= take
            lot.consumed_kg += take
            remaining -= take
            self._consumed_by_lot.setdefault(sid, {})
            self._consumed_by_lot[sid][lot.lot_id] = self._consumed_by_lot[sid].get(lot.lot_id, 0.0) + take
            if lot.quantity_kg <= 1e-9:
                lot.quantity_kg = 0.0
                lot.quality_status = "CONSUMED"
        # consumption draws down reservations of the running order(s) first
        used = kg - max(remaining, 0.0)
        for oid in sorted(self._reservations):
            r = self._reservations[oid]
            if sid in r and used > 0:
                d = min(r[sid], used)
                r[sid] -= d
                used -= d

    def _post_consumption(self) -> None:
        for sid, by_lot in sorted(self._consumed_by_lot.items()):
            total = sum(by_lot.values())
            if total <= 0:
                continue
            mid = self.storage[sid]["material"]
            self.ctx.bus.publish(EventType.MATERIAL_CONSUMED, self.name, sid,
                                 {"material": mid, "quantity_kg": round(total, 2),
                                  "lots": {k: round(v, 2) for k, v in sorted(by_lot.items())},
                                  "period_s": self.posting_interval})
        self._consumed_by_lot = {}

    # ------------------------------------------------------------------ faults (causes -> consequences)
    def _apply_stock_loss(self, t: int) -> None:
        fe = self.ctx.state.fault_effects
        for sid, spec in self.storage.items():
            mid = spec["material"]
            for target in (sid, mid):
                for fid, frac in sorted(fe.contributions(target, "stock_loss").items()):
                    done = self._stock_loss_applied.get((fid, sid), 0.0)
                    if frac <= done + 1e-9:
                        continue
                    # remove the additional fraction of the stock present when the loss started
                    usable = sum(l.quantity_kg for l in self._lots(sid))
                    add = (frac - done) / max(1e-9, 1.0 - done)
                    lost = 0.0
                    for lot in self._lots(sid):
                        d = lot.quantity_kg * min(1.0, add)
                        lot.quantity_kg -= d
                        lost += d
                    self._stock_loss_applied[(fid, sid)] = frac
                    if lost > 1.0 and (frac >= 1.0 or int(frac * 20) != int(done * 20)):
                        ref = self.ctx.state.causal.get(target)
                        kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
                        self.ctx.bus.publish(EventType.INVENTORY_MOVED, self.name, sid,
                                             {"material": mid, "quantity_kg": round(lost, 1), "to": "WRITE-OFF",
                                              "reason": "stock rejected / unavailable", "usable_before_kg":
                                                  round(usable, 1)}, severity="warning", **kw)

    def _supply_disrupted(self, item_id: str, supplier: Optional[str]) -> bool:
        fe = self.ctx.state.fault_effects
        return any(fe.value(k, "supply_disruption") > 0 for k in (item_id, supplier or "") if k)

    def _update_parts_blocking(self) -> None:
        fe = self.ctx.state.fault_effects
        for p in self.ctx.state.collection("spare_parts").values():
            p.blocked = fe.value(p.part_id, "stock_blocked") > 0

    # ------------------------------------------------------------------ replenishment
    def _open_po(self, item_id: str) -> Optional[PurchaseOrder]:
        for po in self.ctx.state.collection("purchase_orders").values():
            if po.item_id == item_id and po.status in ("OPEN", "DELAYED"):
                return po
        return None

    def _create_po(self, item_id: str, item_type: str, location: str, qty: float, supplier: Optional[str],
                   lead_h: float, jitter_h: float, t: int, cause: Optional[Event] = None) -> PurchaseOrder:
        self._po_seq += 1
        lead = lead_h + (float(self._rng.uniform(-jitter_h, jitter_h)) if jitter_h > 0 else 0.0)
        po = PurchaseOrder(f"PO-{self._po_seq:05d}", item_id, item_type, location, qty, supplier, t,
                           t + int(round(max(lead, 0.05) * 3600)))
        self.ctx.state.collection("purchase_orders")[po.po_id] = po
        self.ctx.bus.publish(EventType.MATERIAL_ORDERED, self.name, location,
                             {"po_id": po.po_id, "item": item_id, "item_type": item_type, "quantity": qty,
                              "supplier": supplier, "due_s": po.due_s}, cause=cause)
        return po

    def _check_reorder(self, sid: str, t: int) -> None:
        pol = self.replen.get(sid)
        if not pol:
            return
        mid = self.storage[sid]["material"]
        if self._open_po(mid):
            return
        if self.ctx.state.get(sid, "quantity_kg", 0.0) <= float(pol["reorder_point_kg"]):
            self._create_po(mid, "material", sid, float(pol["order_qty_kg"]), pol.get("supplier"),
                            float(pol.get("lead_time_h", 4)), float(pol.get("lead_time_jitter_h", 0)), t)

    def _check_parts_reorder(self, t: int) -> None:
        for p in sorted(self.ctx.state.collection("spare_parts").values(), key=lambda x: x.part_id):
            if p.on_hand - p.reserved <= p.reorder_point and not self._open_po(p.part_id):
                self._create_po(p.part_id, "spare_part", p.location, p.order_qty, None, p.lead_time_h, 0.0, t)

    def _receive_deliveries(self, t: int) -> None:
        for po in sorted(self.ctx.state.collection("purchase_orders").values(), key=lambda p: p.po_id):
            if po.status not in ("OPEN", "DELAYED") or t < po.due_s:
                continue
            if self._supply_disrupted(po.item_id, po.supplier):
                if po.status != "DELAYED":
                    po.status = "DELAYED"
                    ref = self.ctx.state.causal.get(po.item_id) or self.ctx.state.causal.get(po.supplier or "")
                    kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id} if ref else {}
                    self.ctx.bus.publish(EventType.MATERIAL_ORDERED, self.name, po.location,
                                         {"po_id": po.po_id, "item": po.item_id, "status": "DELAYED",
                                          "reason": "supplier unable to deliver"}, severity="warning", **kw)
                continue
            po.status = "RECEIVED"
            po.received_s = t
            if po.item_type == "spare_part":
                part = self.ctx.state.collection("spare_parts")[po.item_id]
                part.on_hand += int(po.quantity)
                self.ctx.bus.publish(EventType.MATERIAL_RECEIVED, self.name, po.location,
                                     {"po_id": po.po_id, "item": po.item_id, "item_type": "spare_part",
                                      "quantity": po.quantity})
                continue
            self._lot_seq += 1
            mid = po.item_id
            lot_id = f"LOT-{mid.split('-', 1)[1]}-R{self._lot_seq:03d}"
            attrs = dict(self.materials_cfg[mid].get("attributes", {}))
            lot = MaterialLot(lot_id, mid, po.location, po.quantity, po.quantity, t, po.supplier, attrs,
                              quality_status="QUARANTINE")
            self.ctx.state.collection("material_lots")[lot_id] = lot
            self.ctx.bus.publish(EventType.MATERIAL_RECEIVED, self.name, po.location,
                                 {"po_id": po.po_id, "item": mid, "item_type": "material", "lot_id": lot_id,
                                  "quantity": po.quantity, "attributes": attrs, "supplier": po.supplier})

    # ------------------------------------------------------------------ event handlers
    def _on_order_released(self, ev: Event) -> None:
        req: Dict[str, float] = ev.payload.get("material_requirements", {})
        res = {}
        for sid, spec in self.storage.items():
            if spec["material"] in req:
                res[sid] = float(req[spec["material"]])
        self._reservations[ev.payload["order_id"]] = res

    def _on_order_closed(self, ev: Event) -> None:
        self._reservations.pop(ev.payload.get("order_id"), None)

    def _on_maintenance_scheduled(self, ev: Event) -> None:
        parts = self.ctx.state.collection("spare_parts")
        for pid, qty in (ev.payload.get("parts") or {}).items():
            if pid in parts:
                parts[pid].reserved += int(qty)

    def _on_maintenance_started(self, ev: Event) -> None:
        parts = self.ctx.state.collection("spare_parts")
        issued = {}
        for pid, qty in (ev.payload.get("parts") or {}).items():
            if pid in parts:
                p = parts[pid]
                q = min(int(qty), p.on_hand)
                p.on_hand -= q
                p.reserved = max(0, p.reserved - int(qty))
                issued[pid] = q
        if issued:
            self.ctx.bus.publish(EventType.MATERIAL_CONSUMED, self.name, "SU-WH-MRO",
                                 {"item_type": "spare_part", "parts": issued,
                                  "work_order_id": ev.payload.get("work_order_id")}, cause=ev)

    def _on_lot_state(self, ev: Event) -> None:
        if ev.payload.get("lot_type") != "material":
            return
        lot = self.ctx.state.collection("material_lots").get(ev.payload.get("lot_id"))
        if lot is not None:
            lot.quality_status = ev.payload["new"]
            self._refresh_storage(lot.location)

    # ------------------------------------------------------------------ operator / API
    def order_material(self, storage_id: str, quantity_kg: float, actor: str = "operator") -> PurchaseOrder:
        if storage_id not in self.storage:
            raise KeyError(f"Unknown storage unit {storage_id}")
        if quantity_kg <= 0:
            raise ValueError("quantity must be positive")
        pol = self.replen.get(storage_id, {})
        return self._create_po(self.storage[storage_id]["material"], "material", storage_id, float(quantity_kg),
                               pol.get("supplier"), float(pol.get("lead_time_h", 4)),
                               float(pol.get("lead_time_jitter_h", 0)), self.ctx.clock.time_s)

    def summary(self) -> dict:
        st = self.ctx.state
        keys = ("material", "quantity_kg", "usable_kg", "reserved_kg", "available_kg", "capacity_kg", "level_pct",
                "consumption_kg_h", "status", "current_lot")
        return {"storage": {sid: {k: st.get(sid, k) for k in keys} for sid in self.storage}}
