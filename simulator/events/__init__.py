"""Canonical event model and synchronous, deterministic event bus."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional


class EventType(str, Enum):
    # simulation lifecycle
    SIMULATION_STARTED = "SIMULATION_STARTED"
    SIMULATION_PAUSED = "SIMULATION_PAUSED"
    SIMULATION_RESUMED = "SIMULATION_RESUMED"
    SIMULATION_RESET = "SIMULATION_RESET"
    SIMULATION_COMPLETED = "SIMULATION_COMPLETED"
    # faults (benchmark visibility)
    FAULT_CREATED = "FAULT_CREATED"
    FAULT_SCHEDULED = "FAULT_SCHEDULED"
    FAULT_STARTED = "FAULT_STARTED"
    FAULT_STOPPED = "FAULT_STOPPED"
    FAULT_RESET = "FAULT_RESET"
    # equipment
    EQUIPMENT_STATE_CHANGED = "EQUIPMENT_STATE_CHANGED"
    EQUIPMENT_DEGRADED = "EQUIPMENT_DEGRADED"
    EQUIPMENT_FAILED = "EQUIPMENT_FAILED"
    EQUIPMENT_REPAIRED = "EQUIPMENT_REPAIRED"
    # process
    PROCESS_SHUTDOWN = "PROCESS_SHUTDOWN"
    CONTROL_MODE_CHANGED = "CONTROL_MODE_CHANGED"
    SETPOINT_CHANGED = "SETPOINT_CHANGED"
    MANIPULATED_VARIABLE_CHANGED = "MANIPULATED_VARIABLE_CHANGED"
    # utilities
    UTILITY_STATE_CHANGED = "UTILITY_STATE_CHANGED"
    # alarms
    ALARM_ACTIVATED = "ALARM_ACTIVATED"
    ALARM_ACKNOWLEDGED = "ALARM_ACKNOWLEDGED"
    ALARM_CLEARED = "ALARM_CLEARED"
    # production
    PRODUCTION_ORDER_CREATED = "PRODUCTION_ORDER_CREATED"
    PRODUCTION_ORDER_RELEASED = "PRODUCTION_ORDER_RELEASED"
    PRODUCTION_ORDER_STARTED = "PRODUCTION_ORDER_STARTED"
    PRODUCTION_ORDER_PAUSED = "PRODUCTION_ORDER_PAUSED"
    PRODUCTION_ORDER_RESUMED = "PRODUCTION_ORDER_RESUMED"
    PRODUCTION_ORDER_BLOCKED = "PRODUCTION_ORDER_BLOCKED"
    PRODUCTION_ORDER_COMPLETED = "PRODUCTION_ORDER_COMPLETED"
    PRODUCTION_ORDER_CANCELLED = "PRODUCTION_ORDER_CANCELLED"
    PRODUCTION_STATE_CHANGED = "PRODUCTION_STATE_CHANGED"
    # maintenance
    MAINTENANCE_REQUESTED = "MAINTENANCE_REQUESTED"
    MAINTENANCE_SCHEDULED = "MAINTENANCE_SCHEDULED"
    MAINTENANCE_WAITING = "MAINTENANCE_WAITING"
    MAINTENANCE_STARTED = "MAINTENANCE_STARTED"
    MAINTENANCE_COMPLETED = "MAINTENANCE_COMPLETED"
    # quality
    QUALITY_SAMPLE_TAKEN = "QUALITY_SAMPLE_TAKEN"
    QUALITY_RESULT_CREATED = "QUALITY_RESULT_CREATED"
    LOT_STATE_CHANGED = "LOT_STATE_CHANGED"
    # materials / inventory
    MATERIAL_CONSUMED = "MATERIAL_CONSUMED"
    MATERIAL_RECEIVED = "MATERIAL_RECEIVED"
    MATERIAL_ORDERED = "MATERIAL_ORDERED"
    MATERIAL_SHORTAGE = "MATERIAL_SHORTAGE"
    MATERIAL_SHORTAGE_CLEARED = "MATERIAL_SHORTAGE_CLEARED"
    INVENTORY_MOVED = "INVENTORY_MOVED"
    # people
    OPERATOR_ACTION = "OPERATOR_ACTION"


class Visibility(str, Enum):
    OPERATIONAL = "operational"   # what plant personnel / plant systems would see
    BENCHMARK = "benchmark"       # ground truth; must never reach an agent-facing interface


# Lifecycle events depend on wall-clock user interaction (pause/resume timing), so they
# use their own id sequence ("LC-") and never shift the ids of simulation events.
LIFECYCLE_EVENT_TYPES = {EventType.SIMULATION_STARTED, EventType.SIMULATION_PAUSED, EventType.SIMULATION_RESUMED,
                         EventType.SIMULATION_RESET}

BENCHMARK_EVENT_TYPES = {EventType.FAULT_CREATED, EventType.FAULT_SCHEDULED, EventType.FAULT_STARTED,
                         EventType.FAULT_STOPPED, EventType.FAULT_RESET}


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: str
    simulation_time: int
    type: EventType
    source: str
    target: Optional[str]
    payload: Dict[str, Any]
    correlation_id: str
    causation_id: Optional[str]
    severity: str = "info"
    visibility: Visibility = Visibility.OPERATIONAL

    @property
    def seq(self) -> int:
        return int(self.event_id.split("-")[1])

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "timestamp": self.timestamp, "simulation_time": self.simulation_time,
            "type": self.type.value, "source": self.source, "target": self.target, "payload": self.payload,
            "correlation_id": self.correlation_id, "causation_id": self.causation_id,
            "severity": self.severity, "visibility": self.visibility.value,
        }


Handler = Callable[[Event], None]


class EventBus:
    """Synchronous publish/subscribe with an ordered event log.

    Handlers run immediately, in subscription order, so the event sequence is a
    deterministic function of the simulation. Event ids are sequential.
    """

    def __init__(self, clock, max_log: int = 250_000) -> None:
        self._clock = clock
        self._max_log = max_log
        self._seq = 0
        self._lc_seq = 0
        self._subs: Dict[Optional[EventType], List[Handler]] = defaultdict(list)
        self.log: List[Event] = []
        self.dropped = 0
        self._depth = 0

    def reset(self) -> None:
        self._seq = 0
        self._lc_seq = 0
        self.log.clear()
        self.dropped = 0

    def subscribe(self, handler: Handler, types: Optional[Iterable[EventType]] = None) -> None:
        if types is None:
            self._subs[None].append(handler)
        else:
            for t in types:
                self._subs[EventType(t)].append(handler)

    def publish(self, type: EventType, source: str, target: Optional[str] = None,
                payload: Optional[Dict[str, Any]] = None, *, cause: Optional[Event] = None,
                correlation_id: Optional[str] = None, causation_id: Optional[str] = None,
                severity: str = "info", visibility: Optional[Visibility] = None) -> Event:
        type = EventType(type)
        if type in LIFECYCLE_EVENT_TYPES:
            self._lc_seq += 1
            event_id = f"LC-{self._lc_seq:07d}"
        else:
            self._seq += 1
            event_id = f"EV-{self._seq:07d}"
        if cause is not None:
            causation_id = causation_id or cause.event_id
            correlation_id = correlation_id or cause.correlation_id
        if visibility is None:
            visibility = Visibility.BENCHMARK if type in BENCHMARK_EVENT_TYPES else Visibility.OPERATIONAL
        ev = Event(event_id=event_id, timestamp=self._clock.timestamp(), simulation_time=self._clock.time_s,
                   type=type, source=source, target=target, payload=dict(payload or {}),
                   correlation_id=correlation_id or event_id, causation_id=causation_id,
                   severity=severity, visibility=visibility)
        self.log.append(ev)
        if len(self.log) > self._max_log:
            del self.log[0]
            self.dropped += 1
        if self._depth > 50:
            raise RuntimeError("Event cascade too deep - check for handler loops")
        self._depth += 1
        try:
            for h in list(self._subs.get(type, ())) + list(self._subs.get(None, ())):
                h(ev)
        finally:
            self._depth -= 1
        return ev

    def query(self, *, types: Optional[Iterable[str]] = None, since: Optional[int] = None,
              until: Optional[int] = None, target: Optional[str] = None, source: Optional[str] = None,
              include_benchmark: bool = True, limit: Optional[int] = None,
              after_id: Optional[str] = None) -> List[Event]:
        tset = {EventType(t) for t in types} if types else None
        after_seq = int(after_id.split("-")[1]) if after_id else None
        out = []
        for ev in self.log:
            if after_seq is not None and ev.event_id.startswith("EV-") and ev.seq <= after_seq:
                continue
            if tset and ev.type not in tset:
                continue
            if since is not None and ev.simulation_time < since:
                continue
            if until is not None and ev.simulation_time > until:
                continue
            if target and ev.target != target:
                continue
            if source and ev.source != source:
                continue
            if not include_benchmark and ev.visibility == Visibility.BENCHMARK:
                continue
            out.append(ev)
        if limit is not None:
            out = out[-limit:]
        return out
