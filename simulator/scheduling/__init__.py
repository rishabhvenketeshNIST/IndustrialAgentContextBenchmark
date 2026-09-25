"""Scheduling module: order creation, release, sequencing, blocking and lateness projection."""
from __future__ import annotations

from typing import List

from ..common import ConfigError
from ..events import EventType
from ..production.orders import ProductionOrder, order_from_config, transition
from ..simulation.module import ModuleContext, SimulationModule

_ACTIVE = ("RUNNING", "PAUSED")


class SchedulingModule(SimulationModule):
    name = "scheduling"

    def setup(self, ctx: ModuleContext) -> None:
        super().setup(ctx)
        cfg = ctx.cfg("production")
        self.products = cfg.get("products", {})
        sched = cfg.get("scheduling", {})
        self.single_line = bool(sched.get("single_line", True))
        self.auto_release = bool(sched.get("auto_release", True))
        self.nominal = float(cfg["line"]["nominal_rate_kg_h"])
        storage = ctx.cfg("materials").get("storage", {})
        self.storage_for = {spec["material"]: sid for sid, spec in storage.items()}
        orders = ctx.state.collection("production_orders")
        for spec in ctx.config.get("production_orders", []) or []:
            o = order_from_config(spec, self.products)
            if o.order_id in orders:
                raise ConfigError(f"Duplicate production order {o.order_id}")
            orders[o.order_id] = o
        self._created = False

    # ------------------------------------------------------------------ helpers
    def _orders(self) -> List[ProductionOrder]:
        return sorted(self.ctx.state.collection("production_orders").values(),
                      key=lambda o: (o.priority, o.planned_start, o.order_id))

    def _shortages(self, o: ProductionOrder) -> List[str]:
        out = []
        for mid in sorted(self.products[o.product_id].get("bill_of_materials", {})):
            sid = self.storage_for.get(mid)
            if sid and float(self.ctx.state.get(sid, "supply_availability", 1.0)) < 1.0:
                out.append(mid)
        return out

    def _release_time(self, o: ProductionOrder) -> int:
        delay = self.ctx.state.fault_effects.value(o.order_id, "release_delay_s")
        return o.planned_start + int(delay)

    def _release_blocked(self, o: ProductionOrder) -> bool:
        fe = self.ctx.state.fault_effects
        return fe.value(o.order_id, "blocked") > 0 or fe.value("ERP", "blocked") > 0

    # ------------------------------------------------------------------ simulation
    def pre_step(self, t: int) -> None:
        if not self._created:
            for o in self._orders():
                self.ctx.bus.publish(EventType.PRODUCTION_ORDER_CREATED, self.name, o.order_id,
                                     {"order_id": o.order_id, "product_id": o.product_id, "quantity": o.quantity,
                                      "unit": o.unit, "priority": o.priority, "planned_start": o.planned_start,
                                      "planned_end": o.planned_end})
            self._created = True
        line_down = self.ctx.state.production.get("state") == "DOWN"
        for o in self._orders():
            short = self._shortages(o)
            if o.status == "PLANNED" and self.auto_release and t >= o.planned_start:
                if self._release_blocked(o):
                    transition(self.ctx, o, "BLOCKED", "release blocked by planning system")
                elif short:
                    transition(self.ctx, o, "BLOCKED", f"material shortage: {', '.join(short)}")
                elif t >= self._release_time(o):
                    transition(self.ctx, o, "RELEASED", "planned start reached, materials available")
            elif o.status in ("RELEASED",) + _ACTIVE and (short or self._release_blocked(o)):
                reason = f"material shortage: {', '.join(short)}" if short else "blocked by planning system"
                transition(self.ctx, o, "BLOCKED", reason)
            elif o.status == "BLOCKED" and not short and not self._release_blocked(o):
                if o.actual_start is None:
                    if t >= self._release_time(o):
                        transition(self.ctx, o, "RELEASED", "block cleared")
                else:
                    transition(self.ctx, o, "PAUSED", "block cleared - awaiting line")
        # sequencing on the single TEP line
        active = [o for o in self._orders() if o.status in _ACTIVE]
        if not line_down:
            paused = [o for o in active if o.status == "PAUSED" and o.status_reason != "operator pause"
                      and o.status_reason != "production line down"]
            running = [o for o in active if o.status == "RUNNING"]
            if not running and paused:
                transition(self.ctx, paused[0], "RUNNING", "line available")
            elif not active or not self.single_line:
                for o in self._orders():
                    if o.status == "RELEASED":
                        transition(self.ctx, o, "RUNNING", "line available")
                        if self.single_line:
                            break
        self._project(t)

    def _project(self, t: int) -> None:
        rate = max(float(self.ctx.state.production.get("rate_smoothed_kg_h") or 0.0), 1e-6)
        cursor = t
        for o in sorted(self._orders(), key=lambda o: (o.status not in _ACTIVE, o.priority, o.planned_start)):
            if o.status in ("COMPLETED", "CANCELLED"):
                o.projected_end = o.actual_end
                o.late = o.actual_end is not None and o.actual_end > o.planned_end
                continue
            remaining = max(o.quantity - o.net_kg, 0.0)
            start = max(cursor, o.planned_start if o.status in ("PLANNED", "BLOCKED", "RELEASED") else cursor)
            use_rate = rate if rate > 0.05 * self.nominal else self.nominal
            o.projected_end = int(start + remaining / use_rate * 3600.0)
            o.late = o.projected_end > o.planned_end
            cursor = o.projected_end

    # ------------------------------------------------------------------ operator commands
    def command(self, order_id: str, action: str, actor: str = "operator") -> ProductionOrder:
        orders = self.ctx.state.collection("production_orders")
        if order_id not in orders:
            raise KeyError(f"Unknown production order {order_id}")
        o = orders[order_id]
        if action == "release":
            transition(self.ctx, o, "RELEASED", "released by operator", actor)
        elif action == "pause":
            transition(self.ctx, o, "PAUSED", "operator pause", actor)
        elif action == "resume":
            if o.status != "PAUSED":
                raise ValueError("only PAUSED orders can be resumed")
            if any(x.status == "RUNNING" for x in orders.values()) and self.single_line:
                raise ValueError("another order is running on the line")
            transition(self.ctx, o, "RUNNING", "resumed by operator", actor)
        elif action == "cancel":
            transition(self.ctx, o, "CANCELLED", "cancelled by operator", actor)
        else:
            raise ValueError(f"Unknown order action '{action}'")
        return o

    def create_order(self, spec: dict, actor: str = "operator") -> ProductionOrder:
        o = order_from_config(spec, self.products)
        orders = self.ctx.state.collection("production_orders")
        if o.order_id in orders:
            raise ValueError(f"Order {o.order_id} already exists")
        orders[o.order_id] = o
        self.ctx.bus.publish(EventType.PRODUCTION_ORDER_CREATED, actor, o.order_id,
                             {"order_id": o.order_id, "product_id": o.product_id, "quantity": o.quantity,
                              "unit": o.unit, "priority": o.priority, "planned_start": o.planned_start,
                              "planned_end": o.planned_end})
        return o
