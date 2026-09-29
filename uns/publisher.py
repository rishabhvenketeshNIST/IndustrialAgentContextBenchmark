"""Simulator -> UNS (MQTT) synchronisation.

``UNSPublisher.sync()`` is called after every simulation step (and after lifecycle commands). It
reads the simulator only through the operational boundary (``api/operational.py``) and publishes:

    meta          retained, QoS 1   static description of an entity (identity, ISA-95 concept, parent,
                                    configuration, measurement leaves)
    state         retained, QoS 1   current discrete condition of an entity or record; published at
                                    once when a discrete field changes, and at the sampling period
                                    when only continuous quantities changed
    measurement   retained, QoS 0   last value of a continuous observation, sampled every
                                    ``measurement_period_s`` simulated seconds (all of them, every
                                    period, so message counts carry no information)
    event         not retained, QoS 1   operational events (OE-/LC- ids) in stream order
    lifecycle     retained, QoS 1   simulation status (READY | RUNNING | PAUSED | COMPLETED)
    uns/publisher retained, QoS 1   publisher online/offline (MQTT last will)

Every manufacturing payload names its simulation scope (``operational_scope_id``, from the operational
boundary) and separates two clocks: ``simulation_time``/``simulation_timestamp`` (simulator time, the
only manufacturing time) and ``observed_at`` (wall-clock time of this publication, transport metadata).

The publisher never changes the simulator; it only reads it under the service lock.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np
import paho.mqtt.client as mqtt

from api import operational
from simulator.common import iso
from simulator.tep import catalog
from simulator.tep import control_scheme as cs

from . import DEFAULT_ROOT, SCHEMA
from .namespace import ROOT_DIR, Namespace, load_context_model

import yaml

QOS_MEASUREMENT = 0
QOS_STATE = 1
QOS_EVENT = 1
MAX_INFLIGHT = 1000          # client-side QoS 1 window
MAX_PENDING = 500            # backpressure: unacknowledged QoS 1 messages before publishing waits

# Channel of an entity property, from its semantic class in contract/canonical_contract.yaml.
MEASUREMENT_CLASSES = {"condition_indicator", "utility_observation", "inventory_quantity", "counter", "availability"}
META_CLASSES = {"configuration"}
# Production line (state.production of the production unit)
PRODUCTION_STATE = ("state", "current_lot", "current_order")
PRODUCTION_MEASUREMENTS = {"rate_kg_h": "kg/h", "rate_smoothed_kg_h": "kg/h", "total_kg": "kg", "accepted_kg": "kg",
                           "rejected_kg": "kg", "run_time_s": "s", "down_time_s": "s"}
RECORD_EXCLUDE = {"alarms": ("value",)}      # the alarm source's current value is its measurement topic
# Record fields that are continuous quantities although stored as integers (a forecast in seconds).
# Floats are always continuous.
RECORD_CONTINUOUS = {"production_orders": ("projected_end",)}
# Envelope fields that describe a publication rather than its content.
PUBLICATION_FIELDS = ("simulation_time", "simulation_timestamp", "observed_at")


def content_of(payload: dict) -> dict:
    """A payload without its publication fields: what a republication must leave unchanged."""
    return {k: v for k, v in payload.items() if k not in PUBLICATION_FIELDS}


def wall_clock() -> str:
    """Wall-clock UTC time for observed_at (transport metadata, never manufacturing time)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    return x


def encode(payload: dict) -> str:
    """Canonical JSON: sorted keys, no whitespace, NaN -> null. Identical inputs give identical bytes."""
    return json.dumps(_clean(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _snap(x: Any) -> Any:
    """Structural copy of containers, so a later in-place mutation cannot hide a change."""
    if isinstance(x, dict):
        return {k: _snap(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_snap(v) for v in x]
    return x


def discrete_projection(body: dict, collection: Optional[str] = None) -> Any:
    """The discrete content of a state body: floats and declared continuous record fields replaced by
    a marker. Used to decide when a record's state must be published at once."""
    d = _discrete(body)
    for k in RECORD_CONTINUOUS.get(collection, ()):
        if k in d.get("state", {}):
            d["state"][k] = "#"
    return d


def _discrete(x: Any) -> Any:
    """The payload with every float replaced by a marker."""
    if isinstance(x, dict):
        return {k: _discrete(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_discrete(v) for v in x]
    return "#" if isinstance(x, float) else x


class UNSPublisher:
    def __init__(self, service, host: str = "127.0.0.1", port: int = 1883, root: str = DEFAULT_ROOT,
                 measurement_period_s: int = 10, client_id: str = "acme-uns-publisher",
                 keepalive: int = 30) -> None:
        self.service = service
        self.host, self.port, self.root = host, int(port), root
        self.period = int(measurement_period_s)
        if self.period < 1:
            raise ValueError("measurement_period_s must be >= 1")
        self.client_id = client_id
        self.keepalive = keepalive
        self.cm = load_context_model()
        self.contract = yaml.safe_load((ROOT_DIR / "contract" / "canonical_contract.yaml").read_text(encoding="utf-8"))
        self._client: Optional[mqtt.Client] = None
        self._connected = threading.Event()
        self._resync = False
        self._engine = None
        self.ns: Optional[Namespace] = None
        self._ops: Optional[operational.OperationalEventView] = None
        self._events_done = 0
        self.retained: Dict[str, str] = {}        # topic -> payload currently desired as retained
        self._sent_retained: Dict[str, str] = {}  # topic -> payload last sent as retained
        self._last_state: Dict[str, Tuple[Any, str]] = {}
        self._pending_deletes: set = set()
        self._stale: set = set()
        self._found: Dict[str, str] = {}          # retained topic -> payload found on the broker at connect
        self._adopt: Dict[str, dict] = {}         # same-scope retained payloads a (re)started publisher reuses
        self._scope_id: Optional[str] = None
        self._keep: set = set()
        self._observed_at = wall_clock()
        self._events_buffer: Deque[Tuple[str, str]] = deque()
        self._last_tick: Optional[int] = None
        self._lifecycle: Optional[str] = None
        self._last_info: Dict[int, mqtt.MQTTMessageInfo] = {}    # QoS -> last message published
        self.sent = {"meta": 0, "state": 0, "measurement": 0, "event": 0, "lifecycle": 0, "delete": 0}
        self.publish_errors = 0          # publish calls the client refused after retrying (diagnostics)
        self.disconnects = 0
        self._q1_pending: set = set()    # packet ids of QoS 1 messages awaiting PUBACK

    # ------------------------------------------------------------------ connection
    def connect(self, timeout: float = 10.0) -> None:
        with self.service.lock:
            eng = self.service._eng()
            ns = Namespace(eng, self.cm, self.root)
        status_topic = ns.publisher_status_topic()
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id, clean_session=True,
                        protocol=mqtt.MQTTv311)
        c.will_set(status_topic, encode({"kind": "publisher", "schema": SCHEMA, "status": "offline"}),
                   qos=QOS_STATE, retain=True)
        c.reconnect_delay_set(min_delay=0.1, max_delay=2)
        c.max_queued_messages_set(0)
        c.max_inflight_messages_set(MAX_INFLIGHT)
        c.on_connect = self._on_connect
        c.on_publish = self._on_publish
        c.on_disconnect = self._on_disconnect
        self._client = c
        self._status_topic = status_topic
        c.connect(self.host, self.port, keepalive=self.keepalive)
        c.loop_start()
        if not self._connected.wait(timeout):
            raise ConnectionError(f"MQTT broker {self.host}:{self.port} not reachable")
        self._discover_stale_retained()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if not reason_code.is_failure:
            self._resync = True
            self._connected.set()

    def _on_publish(self, client, userdata, mid, reason_code, properties) -> None:
        self._q1_pending.discard(mid)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._connected.clear()
        self.disconnects += 1

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def _discover_stale_retained(self, settle: float = 0.3) -> None:
        """Read the retained topics already under the root. At the first sync, those of the current
        operational scope are adopted (a publisher restart: same content keeps its simulation time);
        every other one is purged unless the current snapshot republishes it (stale-state prevention)."""
        found: Dict[str, str] = {}
        last = [time.monotonic()]

        def on_message(client, userdata, msg):
            if msg.retain and msg.topic != self._status_topic and msg.payload:
                found[msg.topic] = msg.payload.decode("utf-8", "replace")
            last[0] = time.monotonic()
        self._client.on_message = on_message
        self._client.subscribe(f"{self.root}/#", qos=0)
        deadline = time.monotonic() + 5.0
        while time.monotonic() - last[0] < settle and time.monotonic() < deadline:
            time.sleep(0.05)
        self._client.unsubscribe(f"{self.root}/#")
        self._client.on_message = None
        self._found = found
        self._stale = set(found)

    def attach(self) -> "UNSPublisher":
        """Integrated mode: connect and follow the host service's own simulation. The publisher is
        registered as an observer (SimulatorService.add_observer), so it synchronises after every
        simulated second and every lifecycle command of the simulation the web UI/API serves; it never
        creates or steps a simulation itself."""
        self.connect()
        self.service.add_observer(self.sync)
        self.sync()
        return self

    def status(self) -> dict:
        """Transport status for operator displays (no manufacturing content)."""
        return {"connected": self.connected, "host": self.host, "port": self.port, "root": self.root,
                "client_id": self.client_id, "publish_errors": self.publish_errors}

    def close(self) -> None:
        self.service.remove_observer(self.sync)
        if self._client is None:
            return
        if self.connected:
            self._publish(self._status_topic, encode({"kind": "publisher", "schema": SCHEMA, "status": "offline"}),
                          QOS_STATE, True, "lifecycle")
            self.flush()
            self._client.disconnect()
        self._client.loop_stop()
        self._client = None

    def flush(self, timeout: float = 60.0) -> None:
        """Wait until everything published so far has left the client. QoS 1 messages wait for broker
        acknowledgement in order, so waiting for the last one of each QoS covers all earlier ones."""
        if self.connected:
            for info in list(self._last_info.values()):
                info.wait_for_publish(timeout)

    # ------------------------------------------------------------------ low-level publish
    def _publish(self, topic: str, payload: str, qos: int, retain: bool, kind: str) -> None:
        if retain:
            if payload:
                self.retained[topic] = payload
            else:
                self.retained.pop(topic, None)
        if not self.connected:
            if kind == "event":
                self._events_buffer.append((topic, payload))
            return
        if qos:
            # backpressure keeps the QoS 1 backlog small, so state and events are not delayed behind
            # measurements and 16-bit packet ids cannot wrap onto a message still awaiting its PUBACK
            deadline = time.monotonic() + 30
            while len(self._q1_pending) > MAX_PENDING and self.connected and time.monotonic() < deadline:
                time.sleep(0.001)
        for _ in range(100):
            info = self._client.publish(topic, payload, qos=qos, retain=retain)
            if info.rc != mqtt.MQTT_ERR_QUEUE_SIZE:
                break
            time.sleep(0.001)            # packet-id collision: retry with the next id
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self.publish_errors += 1
        elif qos:
            self._q1_pending.add(info.mid)
        self._last_info[qos] = info
        if retain:
            if payload:
                self._sent_retained[topic] = payload
            else:
                self._sent_retained.pop(topic, None)
        self.sent[kind] += 1

    def _retain(self, topic: str, payload: dict, qos: int, kind: str) -> None:
        """Publish a retained payload. A publisher that attached to a running scope first reuses the
        simulation time of the same-scope retained message it found, if the content is unchanged: a
        republication is not a new manufacturing observation."""
        adopted = self._adopt.pop(topic, None)
        if adopted is not None and content_of(adopted) == content_of(_clean(payload)):
            payload = dict(payload, simulation_time=adopted["simulation_time"],
                           simulation_timestamp=adopted["simulation_timestamp"])
        self._publish(topic, self._stamp(payload), qos, True, kind)

    def _stamp(self, payload: dict) -> str:
        payload["observed_at"] = self._observed_at
        return encode(payload)

    def _envelope(self, cid: str, kind: str, **fields) -> dict:
        eng = self._engine
        d = {"schema": SCHEMA, "kind": kind, "entity_id": cid, "entity_type": cid.split(":", 1)[0],
             "operational_scope_id": self._scope_id,
             "simulation_time": eng.clock.time_s, "simulation_timestamp": eng.clock.timestamp()}
        d.update(fields)
        return d

    # ------------------------------------------------------------------ synchronisation
    def sync(self) -> None:
        """Publish everything that changed since the last call. Call after every simulation step."""
        with self.service.lock:
            eng = self.service._eng()
            self._observed_at = wall_clock()
            if self._resync and self.connected:
                self._do_resync()
            if eng is not self._engine:
                self._begin_scope(eng)
            t = eng.clock.time_s
            tick = t % self.period == 0 and t != self._last_tick
            self._publish_events()
            self._publish_lifecycle()
            self._publish_states(tick)
            if tick:
                self._publish_meta()
                self._publish_measurements()
                self._last_tick = t

    def _begin_scope(self, eng) -> None:
        """A new identity scope (simulation created or reset): publish the scope's first events, then
        replace the retained snapshot; retained topics of the previous scope that no longer exist are
        deleted."""
        old = set(self.retained) - {getattr(self, "_status_topic", None)}
        attach_mid_run = self._engine is None and eng.clock.time_s > 0
        self._engine = eng
        self._scope_id = operational.operational_scope_id(eng)
        # retained messages of this very scope (a publisher restart): reuse, never re-date
        self._adopt = {}
        for topic, raw in self._found.items():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if isinstance(d, dict) and d.get("operational_scope_id") == self._scope_id:
                self._adopt[topic] = d
        self._found = {}
        self._keep: set = set()
        self.ns = Namespace(eng, self.cm, self.root)
        self._ops = operational.OperationalEventView(eng.bus)
        self._events_done = 0
        self._last_state = {}
        self._last_raw: Dict[str, dict] = {}
        self._meta_content: Dict[str, Any] = {}
        self._chan_cache: Dict[Tuple[str, str], str] = {}
        self._last_tick = None
        self._lifecycle = None
        if attach_mid_run:
            # a publisher (re)started while the simulation runs publishes the current state, not a replay
            # of past events: history belongs to the historian, not to the live namespace
            self._ops.update()
            self._events_done = len(self._ops._records)
        self._publish_events()
        self.retained = {k: v for k, v in self.retained.items() if k == getattr(self, "_status_topic", None)}
        self._publish_meta()
        self._publish_lifecycle()
        t = eng.clock.time_s
        self._publish_states(tick=t % self.period == 0)
        if t % self.period == 0:
            self._publish_measurements()
            self._last_tick = t
        for topic, d in sorted(self._adopt.items()):
            # not republished: between sampling ticks the last same-scope samples, and states whose
            # only change waits for the next tick, stay retained as they are
            if d.get("kind") == "measurement" or topic in self._keep:
                self.retained[topic] = self._sent_retained[topic] = encode(d)
        self._adopt, self._keep = {}, set()
        for topic in sorted((old | self._stale) - set(self.retained)):
            self._publish(topic, "", QOS_STATE, True, "delete")
        self._stale = set()

    def _do_resync(self) -> None:
        """After a (re)connect: deliver events buffered while offline, then republish every retained
        topic (the broker may have lost them). Payloads are identical to the last ones, so this only
        produces duplicates, never new information."""
        self._resync = False
        self._publish(self._status_topic, encode({"kind": "publisher", "schema": SCHEMA, "status": "online"}),
                      QOS_STATE, True, "lifecycle")
        while self._events_buffer:
            topic, payload = self._events_buffer.popleft()
            self._publish(topic, payload, QOS_EVENT, False, "event")
        for topic in sorted(set(self._sent_retained) - set(self.retained)):
            self._publish(topic, "", QOS_STATE, True, "delete")
        for topic, payload in sorted(self.retained.items()):
            if topic == self._status_topic:
                continue
            d = json.loads(payload)                 # same content and simulation time, new observed_at
            kind = d["kind"]
            self._publish(topic, self._stamp(d), QOS_MEASUREMENT if kind == "measurement" else QOS_STATE, True,
                          kind if kind in self.sent else "state")

    # ------------------------------------------------------------------ meta
    def _publish_meta(self) -> None:
        ns, eng, st = self.ns, self._engine, self._engine.state
        for cid in ns.entity_ids():
            etype, native = cid.split(":", 1)
            meta = {"native_id": native, "isa95": ns.isa95_of(cid), "parent": ns.parent(cid)}
            if etype == "product":
                p = eng.config["production"]["products"][native]
                meta.update({"name": p.get("name"), "unit": p.get("unit"), "yields_material": f"material:{p['material']}",
                             "bill_of_materials": {f"material:{m}": q for m, q in p.get("bill_of_materials", {}).items()}})
            else:
                rec = st.entity(native)
                meta["name"] = rec.name
                if native in eng.hierarchy:
                    el = eng.hierarchy.get(native)
                    meta.update({"level": el.level.value, "class": el.equipment_class, "origin": el.origin.value,
                                 "description": el.description, "attributes": dict(el.attributes),
                                 "children": [ns.entity_canonical_id(c) for c in el.children]})
                if etype == "utility":
                    meta.update({"utility_type": rec.meta.get("utility_type"),
                                 "supplied_by": ns.entity_canonical_id(rec.meta["supplied_by"]),
                                 "serves": [ns.entity_canonical_id(x) for x in rec.meta.get("serves", [])]})
                if etype == "material":
                    meta.update({"attributes": rec.meta.get("attributes", {}), "specification": rec.meta.get("specification", {}),
                                 "attribute_units": rec.meta.get("attribute_units", {})})
                meta["configuration"] = {k: v for k, v in operational.properties(rec).items()
                                         if self._channel(rec, k) == "meta"}
                meta["measurements"] = sorted(self._measurements_of(cid))
            topic = ns.topic(cid, "meta")
            content = _clean(meta)
            if self._meta_content.get(topic) != content:     # static, but measurement leaves can appear later
                self._meta_content[topic] = content
                self._retain(topic, self._envelope(cid, "meta", **meta), QOS_STATE, "meta")

    # ------------------------------------------------------------------ property routing
    def _scope(self, rec) -> Optional[str]:
        if rec.kind == "utility":
            return "utility"
        if rec.id == self._engine.config["quality"].get("laboratory"):
            return "laboratory"
        if rec.kind == "material":
            return "material"
        if rec.meta.get("level") == "StorageUnit":
            return "storage"
        if rec.meta.get("asset"):
            return "asset"
        return None

    def _channel(self, rec, prop: str) -> str:
        """meta | state | measurement | bound (published as a process variable) | loop"""
        key = (rec.id, prop)
        if key in self._chan_cache:
            return self._chan_cache[key]
        self._chan_cache[key] = ch = self._route(rec, prop)
        return ch

    def _route(self, rec, prop: str) -> str:
        m = self._engine.mapping
        if (rec.id, prop) in {(b.equipment_id, b.property) for b in list(m.xmeas.values()) + list(m.xmv.values())}:
            return "bound"
        if rec.id in m.loops.values():
            return "loop"
        scope = self._scope(rec)
        if scope:
            table = self.contract["properties"][scope]
            spec = table.get(prop)
            if spec is None:
                spec = next((v for k, v in table.items() if "*" in k and prop.startswith(k.rstrip("*"))), None)
            if spec is None and scope == "laboratory":
                spec = {"class": "quality_result"}
            cls = (spec or {}).get("class")
            if cls in META_CLASSES:
                return "meta"
            if cls in MEASUREMENT_CLASSES:
                return "measurement"
        return "state"

    def _measurements_of(self, cid: str) -> List[str]:
        return [leaf for leaf, _ in self._entity_measurements(cid)]

    def _entity_measurements(self, cid: str) -> List[Tuple[str, dict]]:
        """(leaf, fields) for every measurement published under an entity node."""
        st, m = self._engine.state, self._engine.mapping
        etype, native = cid.split(":", 1)
        out: List[Tuple[str, dict]] = []
        p = st.process
        for idx, b in sorted(m.xmeas.items()):
            if b.equipment_id == native:
                v = f"measurement:XMEAS({idx})"
                out.append((v, {"variable": v, "value": float(p.xmeas[idx - 1]), "unit": catalog.XMEAS[idx - 1].unit,
                                "quality": p.quality[idx - 1], "property": b.property, "tag": b.tag}))
        for idx, b in sorted(m.xmv.items()):
            if b.equipment_id == native:
                v = f"manipulated_variable:XMV({idx})"
                out.append((v, {"variable": v, "value": float(p.xmv[idx - 1]), "unit": "%", "property": b.property}))
        for lid, cm in sorted(m.loops.items()):
            if cm != native:
                continue
            loop = cs.LOOPS[lid]
            row = next(r for r in p.loops if r["loop_id"] == lid)
            pv_unit = catalog.XMEAS[loop.pv - 1].unit
            if loop.output_kind == "XMV":
                out_unit, target = "%", f"manipulated_variable:XMV({loop.output_index})"
            else:
                slave = cs.loop_for_setpoint(loop.output_index)
                out_unit = catalog.XMEAS[cs.LOOPS[slave].pv - 1].unit
                target = f"setpoint of control_module:{m.loops[slave]}"
            common = {"loop": lid, "loop_tag": loop.tag, "pv": f"measurement:XMEAS({loop.pv})"}
            out.append(("setpoint", dict(common, variable="setpoint", value=row["setpoint"], unit=pv_unit)))
            out.append(("controller_output", dict(common, variable="controller_output", value=row["output"],
                                                  unit=out_unit, output=target)))
        if etype != "product" and st.has_entity(native):
            rec = st.entity(native)
            for prop, val in sorted(operational.properties(rec).items()):
                if self._channel(rec, prop) == "measurement" and isinstance(val, (int, float, np.number)) \
                        and not isinstance(val, bool):
                    out.append((prop, {"variable": prop, "value": val, "unit": rec.units.get(prop, "")}))
        if cid == self.ns.production_unit:
            for k, unit in PRODUCTION_MEASUREMENTS.items():
                out.append((k, {"variable": k, "value": st.production.get(k), "unit": unit}))
        return out

    # ------------------------------------------------------------------ states
    def _entity_state(self, cid: str) -> Optional[dict]:
        etype, native = cid.split(":", 1)
        st = self._engine.state
        if etype == "product":
            return None
        rec = st.entity(native)
        props = operational.properties(rec)
        state = {k: v for k, v in props.items() if self._channel(rec, k) in ("state",)}
        if native in self._engine.mapping.loops.values():
            state.update({k: props[k] for k in ("mode", "saturated") if k in props})
        if cid == self.ns.production_unit:
            state.update({f"line_{k}" if k == "state" else k: st.production.get(k) for k in PRODUCTION_STATE})
        if not state:
            return None
        units = {k: rec.units[k] for k in state if k in rec.units}
        return {"state": state, "units": units}

    def _publish_states(self, tick: bool) -> None:
        ns, st = self.ns, self._engine.state
        for cid in ns.entity_ids():
            s = self._entity_state(cid)
            if s is not None:
                self._state(cid, ns.topic(cid, "state"), s, tick, continuous=False)
        for coll in sorted(ns.collection_type):
            for rid, rec in list(st.collection(coll).items()):
                cid = ns.register_record(coll, rec, rid)
                d = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
                for k in RECORD_EXCLUDE.get(coll, ()):
                    d.pop(k, None)
                self._state(cid, ns.topic(cid, "state"), {"state": d}, tick, continuous=coll)

    def _state(self, cid: str, topic: str, fields: dict, tick: bool, continuous) -> None:
        """Entity state is discrete: published on any change. Record state also holds continuous
        quantities (lot and stock kilograms): a change of those alone waits for the sampling period."""
        if self._last_raw.get(topic) == fields:
            return                          # fast path: identical to the last handled observation
        body = _clean(fields)
        disc = discrete_projection(body, continuous) if continuous else body
        last = self._last_state.get(topic)
        if last is None and topic in self._adopt:
            # a restarted publisher continues from the same-scope retained state, under the same rules
            prev = {k: self._adopt[topic].get(k) for k in fields}
            last = self._last_state[topic] = (discrete_projection(prev, continuous) if continuous else prev, prev)
            self._keep.add(topic)           # stays retained as it is unless republished below
        if last is not None:
            if last[0] == disc and last[1] != body and not tick:
                return                      # only continuous quantities changed: wait for the sampling period
                                            # (not recorded in _last_raw, so the next tick publishes it)
            if last[1] == body:
                self._last_raw[topic] = _snap(fields)
                return                      # nothing changed
        self._last_raw[topic] = _snap(fields)
        self._last_state[topic] = (disc, body)
        self._retain(topic, self._envelope(cid, "state", **fields), QOS_STATE, "state")

    # ------------------------------------------------------------------ measurements
    def _publish_measurements(self) -> None:
        ns = self.ns
        for cid in ns.entity_ids():
            for leaf, fields in self._entity_measurements(cid):
                self._retain(ns.topic(cid, "measurement", leaf),
                             self._envelope(cid, "measurement", source="simulator", **fields),
                             QOS_MEASUREMENT, "measurement")

    # ------------------------------------------------------------------ lifecycle and events
    def _publish_lifecycle(self) -> None:
        status = self._engine.status
        if status != self._lifecycle:
            self._lifecycle = status
            eng = self._engine
            self._retain(self.ns.topic(self.ns.site, "lifecycle"),
                         self._envelope(self.ns.site, "lifecycle", status=status,
                                        simulation_start=iso(eng.clock.start), duration_seconds=eng.duration_s),
                         QOS_STATE, "lifecycle")

    def _event_entity(self, target: Optional[str]) -> str:
        ns = self.ns
        if target:
            cid = ns.entity_canonical_id(target)
            if cid:
                return cid
            st = self._engine.state
            for coll in sorted(ns.collection_type):
                if target in st.collection(coll):
                    return ns.register_record(coll, st.collection(coll)[target], target)
        return ns.site

    def _publish_events(self) -> None:
        self._ops.update()
        records = self._ops._records
        for r in records[self._events_done:]:
            cid = self._event_entity(r["target"])
            payload = self._envelope(cid, "event", event_id=r["event_id"], event_type=r["type"], source=r["source"],
                                     severity=r["severity"], payload=r["payload"], causation_id=r["causation_id"],
                                     correlation_id=r["correlation_id"])
            # an event carries the simulator time at which it happened, not the time it was published
            payload["simulation_time"], payload["simulation_timestamp"] = r["simulation_time"], r["timestamp"]
            self._publish(self.ns.topic(cid, "event", r["type"]), self._stamp(payload), QOS_EVENT, False, "event")
        self._events_done = len(records)
