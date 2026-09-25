"""Operational information boundary.

Every response served under ``/api/*`` (outside ``/api/benchmark/*``) is built through this module, so
that operational interfaces carry only what plant systems and personnel could observe. The canonical
state, the event log and all ground truth stay unchanged inside the simulator; evaluator and console
access to them is under ``/api/benchmark/*``.

The rules follow where information originates (docs/CANONICAL_SIMULATOR_CONTRACT.md §20):

* entity properties that the owning module tags ``model_internal`` or ``unobservable`` (health,
  capability, availability, utility status, lot composition and deviation) are withheld;
* an asset's DEGRADED status is derived from its true health band, so it is reported as RUNNING;
* events that record changes of model-internal state (utility status, equipment health band) are
  withheld, and payload fields carrying health, fault ids, the failure origin or the scenario identity
  are removed;
* correlation and causation are rebuilt from operational causation only (a fault-derived correlation
  would name the fault), and operational event ids are renumbered so that withheld events leave no gap;
* the run manifest keeps no scenario identity, seeds or configuration hash (they allow the scenario, or
  a fault-free twin run, to be reconstructed).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from simulator.events import EventType, Visibility

# Asset status values that encode the true health band. Everything else (RUNNING, STANDBY, STOPPED,
# FAILED, UNDER_MAINTENANCE) is a run state that plant systems record.
ASSET_STATUS_OPERATIONAL = {"DEGRADED": "RUNNING"}

# Event types that record a change of model-internal state.
WITHHELD_EVENT_TYPES = {
    EventType.UTILITY_STATE_CHANGED,   # utility status is classified from model-internal availability/health
    EventType.EQUIPMENT_DEGRADED,      # true health band crossing
}

# Payload fields whose origin is ground truth or model-internal state.
PAYLOAD_REDACTIONS = {
    EventType.SIMULATION_STARTED: ("run_id", "scenario_id", "seed"),
    EventType.SIMULATION_COMPLETED: ("run_id",),
    EventType.SIMULATION_RESET: ("scenario_id",),
    EventType.EQUIPMENT_STATE_CHANGED: ("health",),
    EventType.EQUIPMENT_FAILED: ("health", "reason"),       # reason distinguishes "fault" from "worn out"
    EventType.EQUIPMENT_REPAIRED: ("health_before", "health_after", "cleared_fault_effects"),
}

MANIFEST_FIELDS = ("simulation_start", "duration_seconds", "control_mode", "simulator_version", "tep_backend")
TEP_VERSION_HIDDEN = ("tep_seed",)


# ---------------------------------------------------------------------------- entities
def hidden_properties(rec) -> Set[str]:
    return set(rec.meta.get("model_internal", [])) | set(rec.meta.get("unobservable", []))


def status_value(rec, value: Any) -> Any:
    if rec.meta.get("asset") and isinstance(value, str):
        return ASSET_STATUS_OPERATIONAL.get(value, value)
    return value


def entity_status(state, entity_id: str) -> Any:
    rec = state.entities.get(entity_id)
    if rec is None or "status" in hidden_properties(rec):
        return None
    return status_value(rec, rec.properties.get("status"))


def properties(rec) -> Dict[str, Any]:
    hidden = hidden_properties(rec)
    out = {k: v for k, v in rec.properties.items() if k not in hidden}
    if "status" in out:
        out["status"] = status_value(rec, out["status"])
    return out


def entity_dict(rec, d: dict) -> dict:
    """Apply the boundary to a serialised entity (``EntityRecord.to_dict`` or ``EnterpriseView.entity``)."""
    hidden = hidden_properties(rec)
    d = dict(d)
    d["properties"] = {k: v for k, v in d.get("properties", {}).items() if k not in hidden}
    if "status" in d["properties"]:
        d["properties"]["status"] = status_value(rec, d["properties"]["status"])
    d["units"] = {k: v for k, v in d.get("units", {}).items() if k not in hidden}
    return d


def property_visible(rec, prop: str) -> bool:
    return prop in rec.properties and prop not in hidden_properties(rec)


# ---------------------------------------------------------------------------- history
def series_visible(state, name: str) -> bool:
    if name.startswith("TRUE:"):
        return False
    ent, _, prop = name.partition(".")
    rec = state.entities.get(ent)
    return rec is None or prop not in hidden_properties(rec)


# ---------------------------------------------------------------------------- manifest
def manifest(m: dict) -> dict:
    out = {k: m[k] for k in MANIFEST_FIELDS if k in m}
    out["tep_version"] = {k: v for k, v in (m.get("tep_version") or {}).items() if k not in TEP_VERSION_HIDDEN}
    for k in ("simulated_seconds", "simulation_end", "status", "process_shutdown", "process_shutdown_reason"):
        if k in m:
            out[k] = m[k]
    return out


# ---------------------------------------------------------------------------- events
class OperationalEventView:
    """The operational event stream derived from one engine's event log.

    Events are processed once, in log order, so ids and correlation are stable and deterministic.
    Simulation events get sequential ``OE-`` ids over the operational stream only; lifecycle events
    keep their ``LC-`` ids (every lifecycle event is operational).
    """

    def __init__(self, bus) -> None:
        self.bus = bus
        self._records: List[dict] = []
        self._op_id: Dict[str, str] = {}          # internal event id -> operational id
        self._corr: Dict[str, str] = {}           # operational id -> operational correlation id
        self._seq = 0
        self._processed = 0                       # events processed since the bus was created

    def _visible(self, ev) -> bool:
        if ev.visibility != Visibility.OPERATIONAL or ev.type in WITHHELD_EVENT_TYPES:
            return False
        if ev.type == EventType.EQUIPMENT_STATE_CHANGED:
            old, new = ev.payload.get("old"), ev.payload.get("new")
            return ASSET_STATUS_OPERATIONAL.get(old, old) != ASSET_STATUS_OPERATIONAL.get(new, new)
        return True

    def _convert(self, ev) -> dict:
        if ev.event_id.startswith("LC-"):
            oid = ev.event_id
        else:
            self._seq += 1
            oid = f"OE-{self._seq:07d}"
        cause = self._op_id.get(ev.causation_id) if ev.causation_id else None
        corr = self._corr[cause] if cause else oid
        self._op_id[ev.event_id] = oid
        self._corr[oid] = corr
        payload = {k: v for k, v in ev.payload.items() if k not in PAYLOAD_REDACTIONS.get(ev.type, ())}
        if ev.type == EventType.EQUIPMENT_STATE_CHANGED:
            for k in ("old", "new"):
                if k in payload:
                    payload[k] = ASSET_STATUS_OPERATIONAL.get(payload[k], payload[k])
        return {"event_id": oid, "timestamp": ev.timestamp, "simulation_time": ev.simulation_time,
                "type": ev.type.value, "source": ev.source, "target": ev.target, "payload": payload,
                "correlation_id": corr, "causation_id": cause, "severity": ev.severity}

    def update(self) -> None:
        log = self.bus.log
        start = max(0, self._processed - self.bus.dropped)
        for ev in log[start:]:
            if self._visible(ev):
                self._records.append(self._convert(ev))
        self._processed = self.bus.dropped + len(log)
        excess = len(self._records) - len(log)
        if excess > 0:
            del self._records[:excess]

    def operational_id(self, internal_id: str) -> Optional[str]:
        self.update()
        return self._op_id.get(internal_id)

    def query(self, *, types: Optional[List[str]] = None, since: Optional[int] = None,
              until: Optional[int] = None, target: Optional[str] = None, limit: Optional[int] = None,
              after_id: Optional[str] = None) -> List[dict]:
        self.update()
        tset = {EventType(t).value for t in types} if types else None
        after = None
        if after_id:
            prefix, _, num = after_id.partition("-")
            after = (prefix, int(num))
        out = []
        for r in self._records:
            if after is not None:
                prefix, _, num = r["event_id"].partition("-")
                if prefix == after[0] and int(num) <= after[1]:
                    continue
            if tset and r["type"] not in tset:
                continue
            if since is not None and r["simulation_time"] < since:
                continue
            if until is not None and r["simulation_time"] > until:
                continue
            if target and r["target"] != target:
                continue
            out.append(r)
        if limit is not None:
            out = out[-limit:]
        return out
