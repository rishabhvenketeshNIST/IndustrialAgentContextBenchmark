"""Production order domain model and state machine (shared by production and scheduling)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..common import ConfigError, fmt_hms
from ..events import Event, EventType

STATUSES = ("PLANNED", "RELEASED", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "BLOCKED")

TRANSITIONS = {
    "PLANNED": {"RELEASED", "BLOCKED", "CANCELLED"},
    "RELEASED": {"RUNNING", "BLOCKED", "CANCELLED"},
    "RUNNING": {"PAUSED", "COMPLETED", "CANCELLED", "BLOCKED"},
    "PAUSED": {"RUNNING", "CANCELLED", "BLOCKED", "COMPLETED"},
    "BLOCKED": {"RELEASED", "RUNNING", "PAUSED", "CANCELLED", "PLANNED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}

EVENT_FOR = {
    "RELEASED": EventType.PRODUCTION_ORDER_RELEASED,
    "RUNNING": EventType.PRODUCTION_ORDER_STARTED,
    "PAUSED": EventType.PRODUCTION_ORDER_PAUSED,
    "BLOCKED": EventType.PRODUCTION_ORDER_BLOCKED,
    "COMPLETED": EventType.PRODUCTION_ORDER_COMPLETED,
    "CANCELLED": EventType.PRODUCTION_ORDER_CANCELLED,
}


@dataclass
class ProductionOrder:
    order_id: str
    product_id: str
    quantity: float
    unit: str
    priority: int
    planned_start: int
    planned_end: int
    status: str = "PLANNED"
    actual_start: Optional[int] = None
    actual_end: Optional[int] = None
    released_at: Optional[int] = None
    produced_kg: float = 0.0
    accepted_kg: float = 0.0
    rejected_kg: float = 0.0
    lots: List[str] = field(default_factory=list)
    status_reason: str = ""
    projected_end: Optional[int] = None
    late: bool = False
    customer: str = ""
    history: List[dict] = field(default_factory=list)
    _started_once: bool = False

    @property
    def net_kg(self) -> float:
        return self.produced_kg - self.rejected_kg

    @property
    def progress(self) -> float:
        return max(0.0, min(1.0, self.net_kg / self.quantity)) if self.quantity > 0 else 0.0

    def to_dict(self) -> dict:
        return {"order_id": self.order_id, "product_id": self.product_id, "quantity": self.quantity,
                "unit": self.unit, "priority": self.priority, "planned_start": self.planned_start,
                "planned_end": self.planned_end, "planned_start_hms": fmt_hms(self.planned_start),
                "planned_end_hms": fmt_hms(self.planned_end), "actual_start": self.actual_start,
                "actual_end": self.actual_end, "status": self.status, "status_reason": self.status_reason,
                "released_at": self.released_at, "produced_kg": round(self.produced_kg, 2),
                "accepted_kg": round(self.accepted_kg, 2), "rejected_kg": round(self.rejected_kg, 2),
                "net_kg": round(self.net_kg, 2), "progress": round(self.progress, 4), "lots": list(self.lots),
                "projected_end": self.projected_end, "late": self.late, "customer": self.customer,
                "history": list(self.history)}


def order_from_config(spec: dict, products: dict) -> ProductionOrder:
    for k in ("order_id", "product_id", "quantity", "planned_start", "planned_end"):
        if k not in spec:
            raise ConfigError(f"Production order missing '{k}': {spec}")
    if spec["product_id"] not in products:
        raise ConfigError(f"Order {spec['order_id']}: unknown product {spec['product_id']}")
    q = float(spec["quantity"])
    ps, pe = int(spec["planned_start"]), int(spec["planned_end"])
    if q <= 0:
        raise ConfigError(f"Order {spec['order_id']}: quantity must be > 0")
    if pe <= ps:
        raise ConfigError(f"Order {spec['order_id']}: planned_end must be after planned_start")
    prio = int(spec.get("priority", 3))
    if not 1 <= prio <= 5:
        raise ConfigError(f"Order {spec['order_id']}: priority must be 1 (highest) .. 5")
    return ProductionOrder(spec["order_id"], spec["product_id"], q, spec.get("unit", "kg"), prio, ps, pe,
                           customer=spec.get("customer", ""))


def transition(ctx, order: ProductionOrder, new: str, reason: str, actor: str = "production",
               cause: Optional[Event] = None) -> Event:
    if new not in STATUSES:
        raise ValueError(f"Unknown order status {new}")
    if new not in TRANSITIONS[order.status]:
        raise ValueError(f"Order {order.order_id}: illegal transition {order.status} -> {new}")
    old = order.status
    t = ctx.clock.time_s
    order.status = new
    order.status_reason = reason
    order.history.append({"t": t, "from": old, "to": new, "reason": reason})
    if new == "RELEASED" and order.released_at is None:
        order.released_at = t
    etype = EVENT_FOR.get(new, EventType.PRODUCTION_ORDER_STARTED)
    if new == "RUNNING":
        if order.actual_start is None:
            order.actual_start = t
        if order._started_once:
            etype = EventType.PRODUCTION_ORDER_RESUMED
        order._started_once = True
    if new in ("COMPLETED", "CANCELLED"):
        order.actual_end = t
    payload = {"order_id": order.order_id, "old": old, "new": new, "reason": reason,
               "product_id": order.product_id, "quantity": order.quantity,
               "net_kg": round(order.net_kg, 1)}
    if new == "RELEASED":
        bom = ctx.cfg("production").get("products", {}).get(order.product_id, {}).get("bill_of_materials", {})
        remaining = max(order.quantity - order.net_kg, 0.0)
        payload["material_requirements"] = {m: round(r * remaining, 3) for m, r in sorted(bom.items())}
    kw = {}
    if cause is None:
        ref = ctx.state.causal.get(order.order_id)
        if ref:
            kw = {"correlation_id": ref.correlation_id, "causation_id": ref.event_id}
    sev = "warning" if new in ("BLOCKED", "PAUSED") else "info"
    return ctx.bus.publish(etype, actor, order.order_id, payload, cause=cause, severity=sev, **kw)
