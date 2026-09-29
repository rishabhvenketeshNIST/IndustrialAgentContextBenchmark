"""Read-only visual inspector for the MQTT Unified Namespace.

The inspector is an ordinary operational MQTT client. It subscribes to ``<root>/#`` on the broker and
shows what arrives: the ISA-95 tree (from the topic paths and the ``meta`` payloads), the current
retained state, live measurements, operational events, the lifecycle and the operational scope. It
never publishes, and it imports nothing from ``simulator/`` or ``api/``: everything it displays is
something any MQTT subscriber receives (tests/test_uns_inspector.py checks both).

It keeps only what a live view needs: the latest message of every topic, the last ``EVENTS_KEPT``
events and the last ``POINTS_KEPT`` samples of each measurement of the current scope, for sparklines.
It is not a historian: nothing is stored, and history before the inspector connected is not
available.

    python scripts/run_inspector.py            # then open http://127.0.0.1:8050

See docs/UNS.md, "Visual inspection".
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt

from . import DEFAULT_ROOT

UI_DIR = Path(__file__).resolve().parents[1] / "ui"
EVENTS_KEPT = 500            # recent operational events, all entities
POINTS_KEPT = 120            # recent samples per measurement topic (sparkline only)
CHANNELS = ("meta", "state", "measurement", "event", "lifecycle", "uns")

# Topic-level escaping of canonical ids (docs/UNS_MQTT_NAMESPACE.md, "Canonical ids in topics"). The
# rule is repeated here, rather than imported from uns.namespace, because that module reads the
# simulator; tests check that both agree.
_UNESCAPE = (("%2F", "/"), ("%2B", "+"), ("%23", "#"), ("%00", "\x00"), ("%24", "$"), ("%25", "%"))


def unescape(segment: str) -> str:
    for enc, plain in _UNESCAPE:
        segment = segment.replace(enc, plain)
    return segment


def parse_topic(topic: str, root: str = DEFAULT_ROOT) -> Tuple[List[str], Optional[str], Optional[str]]:
    """(entity path as canonical ids, channel, leaf) of a UNS topic. Entity segments contain ':' and
    come before the channel segment; the leaf (measurement variable, event type) follows it."""
    if not topic.startswith(root + "/"):
        return [], None, None
    parts = topic[len(root) + 1:].split("/")
    path: List[str] = []
    i = 0
    while i < len(parts) and parts[i] not in CHANNELS and ":" in parts[i]:
        path.append(unescape(parts[i]))
        i += 1
    channel = parts[i] if i < len(parts) else None
    leaf = unescape("/".join(parts[i + 1:])) if i + 1 < len(parts) else None
    return path, channel, leaf


def wall_clock() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UNSInspector:
    """Subscribes to the UNS and keeps the live picture. Thread-safe; all queries return plain dicts."""

    def __init__(self, host: str = "127.0.0.1", port: int = 1883, root: str = DEFAULT_ROOT,
                 client_id: str = "acme-uns-inspector") -> None:
        self.host, self.port, self.root, self.client_id = host, int(port), root, client_id
        self._lock = threading.RLock()
        self._client: Optional[mqtt.Client] = None
        self.connected = False
        self.connects = 0
        self.disconnects = 0
        self.received = 0
        self.messages: Dict[str, dict] = {}                    # topic -> latest message (not events)
        self.events: Deque[dict] = deque(maxlen=EVENTS_KEPT)
        self.series: Dict[str, Deque[Tuple[int, Any]]] = {}    # measurement topic -> (simulation_time, value)
        self.scope: Optional[str] = None
        self.scope_change: Optional[dict] = None
        self.latest_time: Optional[int] = None                 # latest simulation_time seen in the scope

    # ------------------------------------------------------------------ MQTT
    def start(self) -> "UNSInspector":
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id, clean_session=True,
                        protocol=mqtt.MQTTv311)
        c.reconnect_delay_set(min_delay=0.2, max_delay=2)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self._client = c
        c.connect_async(self.host, self.port, keepalive=15)
        c.loop_start()
        return self

    def stop(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client.loop_stop()
            self._client = None
        self.connected = False

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            return
        with self._lock:
            self.connected = True
            self.connects += 1
        client.subscribe(f"{self.root}/#", qos=1)       # the broker resends every retained message

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        with self._lock:
            self.connected = False
            self.disconnects += 1

    def _on_message(self, client, userdata, msg) -> None:
        raw = msg.payload.decode("utf-8", "replace")
        with self._lock:
            self.received += 1
            if not raw:                                  # a deleted retained topic
                self.messages.pop(msg.topic, None)
                self.series.pop(msg.topic, None)
                return
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
            path, channel, leaf = parse_topic(msg.topic, self.root)
            m = {"topic": msg.topic, "qos": msg.qos, "retain": bool(msg.retain), "payload": raw,
                 "data": data if isinstance(data, dict) else None, "received_at": wall_clock(),
                 "path": path, "channel": channel, "leaf": leaf}
            kind = m["data"].get("kind") if m["data"] else None
            if kind == "lifecycle":
                self._lifecycle(m["data"])
            t = (m["data"] or {}).get("simulation_time")
            if isinstance(t, int) and (m["data"] or {}).get("operational_scope_id") == self.scope:
                self.latest_time = t if self.latest_time is None else max(self.latest_time, t)
            if kind == "event":
                self.events.append(m)
                return
            self.messages[msg.topic] = m
            if kind == "measurement":
                self._sample(msg.topic, m["data"])

    def _lifecycle(self, d: dict) -> None:
        scope = d.get("operational_scope_id")
        if scope and scope != self.scope:
            if self.scope is not None:
                self.scope_change = {"previous": self.scope, "current": scope, "detected_at": wall_clock()}
            self.scope = scope
            self.series = {}                            # sparklines belong to one scope
            self.latest_time = None

    def _sample(self, topic: str, d: dict) -> None:
        if self.scope and d.get("operational_scope_id") != self.scope:
            return
        s = self.series.setdefault(topic, deque(maxlen=POINTS_KEPT))
        t = d.get("simulation_time")
        if not s or s[-1][0] != t:                       # a redelivered retained sample is not a new one
            s.append((t, d.get("value")))

    # ------------------------------------------------------------------ queries
    def _nodes(self) -> Dict[str, dict]:
        """Every entity node found in the topic tree, with its channels and meta."""
        nodes: Dict[str, dict] = {}
        for m in self.messages.values():
            path, channel = m["path"], m["channel"]
            for i, cid in enumerate(path):
                n = nodes.setdefault(cid, {"entity_id": cid, "entity_type": cid.split(":", 1)[0],
                                           "parent": path[i - 1] if i else None, "path": path[:i + 1],
                                           "meta": None, "channels": set(), "state": None})
                if i == len(path) - 1 and channel:
                    n["channels"].add(channel)
                    if channel == "meta":
                        n["meta"] = m["data"]
                    elif channel == "state":
                        n["state"] = m
        return nodes

    def status(self) -> dict:
        with self._lock:
            life = next((m for m in self.messages.values() if m["data"] and m["data"].get("kind") == "lifecycle"), None)
            pub = next((m for m in self.messages.values() if m["data"] and m["data"].get("kind") == "publisher"), None)
            d = life["data"] if life else {}
            return {
                "mqtt": {"connected": self.connected, "host": self.host, "port": self.port,
                         "client_id": self.client_id, "connects": self.connects, "disconnects": self.disconnects,
                         "messages_received": self.received},
                "root": self.root,
                "publisher": pub["data"].get("status") if pub else None,
                "lifecycle": {k: d.get(k) for k in ("status", "simulation_time", "simulation_timestamp",
                                                      "simulation_start", "duration_seconds", "observed_at")}
                if life else None,
                "lifecycle_topic": life["topic"] if life else None,
                "operational_scope_id": self.scope,
                "latest_simulation_time": self.latest_time,
                "scope_change": self.scope_change,
                "counts": {"topics": len(self.messages), "events": len(self.events)},
            }

    def tree(self) -> List[dict]:
        """The ISA-95 entity tree: nodes that publish ``meta``, each with its parent. Records (orders,
        lots, samples, ...) are listed with the entity they sit under, not as tree nodes."""
        with self._lock:
            nodes = self._nodes()
        out = []
        for cid, n in nodes.items():
            if n["meta"] is None:
                continue
            meta = n["meta"]
            records = sum(1 for o in nodes.values() if o["parent"] == cid and o["meta"] is None)
            out.append({"entity_id": cid, "entity_type": n["entity_type"], "parent": meta.get("parent"),
                        "name": meta.get("name"), "isa95": (meta.get("isa95") or {}).get("concept"),
                        "records": records})
        return sorted(out, key=lambda n: n["entity_id"])

    def entity(self, cid: str) -> Optional[dict]:
        with self._lock:
            nodes = self._nodes()
            n = nodes.get(cid)
            if n is None:
                return None
            prefix = "/".join([self.root] + [_escape(p) for p in n["path"]]) + "/"
            own = {t: m for t, m in self.messages.items() if m["path"] == n["path"]}
            meta = n["meta"] or {}
            state = n["state"]
            measurements = []
            for t, m in sorted(own.items()):
                if m["channel"] == "measurement" and m["data"]:
                    d = m["data"]
                    measurements.append({"leaf": m["leaf"], "variable": d.get("variable"), "value": d.get("value"),
                                         "unit": d.get("unit"), "simulation_time": d.get("simulation_time"),
                                         "observed_at": d.get("observed_at"), "topic": t,
                                         "series": [list(p) for p in self.series.get(t, ())]})
            events = [m for m in self.events if m["topic"].startswith(prefix)
                      and (not self.scope or (m["data"] or {}).get("operational_scope_id") == self.scope)]
            records = []
            for o in nodes.values():
                if o["parent"] == cid and o["meta"] is None:
                    st = o["state"]
                    records.append({"entity_id": o["entity_id"], "entity_type": o["entity_type"],
                                    "state": (st["data"] or {}).get("state") if st else None,
                                    "simulation_time": (st["data"] or {}).get("simulation_time") if st else None,
                                    "topic": st["topic"] if st else None})
            lineage = []
            for p in n["path"]:
                pm = nodes.get(p, {}).get("meta") or {}
                lineage.append({"entity_id": p, "entity_type": p.split(":", 1)[0],
                                "isa95": (pm.get("isa95") or {}).get("concept"), "name": pm.get("name")})
            return {
                "entity_id": cid, "entity_type": n["entity_type"], "name": meta.get("name"),
                "isa95": meta.get("isa95"), "parent": meta.get("parent", n["parent"]),
                "children": meta.get("children") or sorted(o["entity_id"] for o in nodes.values()
                                                           if o["parent"] == cid and o["meta"] is not None),
                "lineage": lineage, "meta": meta or None,
                "state": _public(state), "measurements": measurements,
                "events": [_public(m) for m in reversed(events)][:50],
                "records": sorted(records, key=lambda r: r["entity_id"]),
                "topics": [_public(m) for _, m in sorted(own.items())],
                "topic_prefix": prefix,
            }

    def recent_events(self, limit: int = 50) -> List[dict]:
        with self._lock:
            return [_public(m) for m in reversed(self.events)
                    if not self.scope or (m["data"] or {}).get("operational_scope_id") == self.scope][:limit]


def _escape(segment: str) -> str:
    out = segment.replace("%", "%25").replace("/", "%2F").replace("+", "%2B").replace("#", "%23").replace("\x00", "%00")
    return "%24" + out[1:] if out.startswith("$") else out


def _public(m: Optional[dict]) -> Optional[dict]:
    """A received message as shown to the browser: the MQTT topic, QoS, retain flag and the payload
    exactly as received (plus its parsed form)."""
    if m is None:
        return None
    return {"topic": m["topic"], "qos": m["qos"], "retain": m["retain"], "payload": m["payload"],
            "data": m["data"], "received_at": m["received_at"]}


# ---------------------------------------------------------------------------- HTTP (read-only)
def create_app(inspector: UNSInspector):
    """A read-only web view of one inspector: GET routes only."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="UNS Inspector", docs_url=None, redoc_url=None)
    app.mount("/inspector", StaticFiles(directory=UI_DIR / "inspector"), name="inspector")

    @app.get("/")
    def index():
        return FileResponse(UI_DIR / "inspector" / "index.html")

    @app.get("/app.css")
    def css():
        return FileResponse(UI_DIR / "css" / "app.css")

    @app.get("/api/status")
    def status():
        return inspector.status()

    @app.get("/api/tree")
    def tree():
        return inspector.tree()

    @app.get("/api/entity/{entity_id:path}")
    def entity(entity_id: str):
        e = inspector.entity(entity_id)
        if e is None:
            raise HTTPException(404, f"{entity_id} is not in the UNS")
        return e

    @app.get("/api/events")
    def events(limit: int = 50):
        return inspector.recent_events(min(max(limit, 1), EVENTS_KEPT))

    return app


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
