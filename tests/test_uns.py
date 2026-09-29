"""The ISA-95-based MQTT Unified Namespace (uns/), tested against a real Eclipse Mosquitto broker.

Every test that needs a broker starts its own mosquitto process on a free port (uns/broker.py).
"""
import copy
import json
import re
import threading
import time
from datetime import timedelta

import paho.mqtt.client as mqtt
import pytest
import yaml

from api.operational import hidden_properties, operational_scope_id
from api.service import SimulatorService
from simulator.common import iso
from simulator.scenarios import ScenarioStore
from uns import DEFAULT_ROOT, SCHEMA
from uns.broker import CONF, LocalBroker, find_mosquitto
from uns.namespace import CONTEXT_MODEL, ROOT_DIR, Namespace, escape, unescape
from uns.publisher import RECORD_CONTINUOUS, UNSPublisher, content_of, encode, wall_clock

from .conftest import requires_fortran

requires_mosquitto = pytest.mark.skipif(find_mosquitto() is None, reason="Eclipse Mosquitto not installed")
CTX = yaml.safe_load(CONTEXT_MODEL.read_text(encoding="utf-8"))
PERIOD = 10
T_FAULT = 3600
T_FIRST_SYMPTOM = 4833          # 01:20:33, vibration alarm VAH-CWP101A
SCOPE_RE = re.compile(r"OS-[0-9a-f]{32}")


# ---------------------------------------------------------------------------- helpers
class Collector:
    """An MQTT subscriber that records (topic, payload, retain, qos) in arrival order."""

    def __init__(self, port, client_id="test-collector", clean=True, topic=f"{DEFAULT_ROOT}/#"):
        self.msgs = []
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=clean)
        self.client.on_message = self._on_message
        self.topic, self.port = topic, port
        self._subscribed = threading.Event()
        self.client.on_subscribe = lambda *a: self._subscribed.set()

    def _on_message(self, c, u, m):
        with self._lock:
            self.msgs.append((m.topic, m.payload.decode(), bool(m.retain), m.qos))
            self._last = time.monotonic()

    def start(self, subscribe=True):
        self.client.connect("127.0.0.1", self.port)
        self.client.loop_start()
        if subscribe:
            self._subscribed.clear()
            self.client.subscribe(self.topic, qos=1)
            assert self._subscribed.wait(5)
        return self

    def quiet(self, settle=0.6, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() - self._last < settle and time.monotonic() < deadline:
            time.sleep(0.05)
        return list(self.msgs)

    def stop(self):
        self.client.disconnect()
        self.client.loop_stop()


def run(port, scenario="SCN-COOL-001", until=600, period=PERIOD, svc=None):
    svc = svc or SimulatorService(start_runner=False)
    if svc.engine is None:
        if isinstance(scenario, dict):
            svc.create_simulation(scenario=scenario)
        else:
            svc.create_simulation(scenario)
    pub = UNSPublisher(svc, port=port, measurement_period_s=period)
    pub.connect()
    pub.sync()
    eng = svc.engine
    while eng.clock.time_s < until and not eng.completed:
        eng.step(1)
        pub.sync()
    pub.flush()
    assert pub.publish_errors == 0, "the MQTT client refused a publish"
    return svc, pub


def loads(msgs, kind=None):
    out = []
    for t, p, r, q in msgs:
        if not p:
            continue
        d = json.loads(p)
        if kind is None or d.get("kind") == kind:
            out.append((t, d, r, q))
    return out


def per_topic(msgs):
    by = {}
    for t, p, r, q in msgs:
        by.setdefault(t, []).append((p, r, q))
    return by


def scopes(msgs):
    """The operational scope ids carried by a list of messages."""
    return {json.loads(p).get("operational_scope_id") for _, p, _, _ in msgs if p} - {None}


def publication(p):
    """A payload without observed_at: everything a republication must leave unchanged, including the
    simulation time."""
    return {k: v for k, v in json.loads(p).items() if k != "observed_at"}


def normalized(msgs):
    """Messages of an independent simulation scope, made comparable byte for byte with another scope:
    the opaque scope id and the wall-clock observed_at differ between scopes by design."""
    out = []
    for t, p, r, q in msgs:
        if p:
            d = json.loads(p)
            if "operational_scope_id" in d:
                d["operational_scope_id"] = "<scope>"
            if "observed_at" in d:
                d["observed_at"] = "<observed>"
            p = encode(d)
        out.append((t, p, r, q))
    return out


def floats_only_differ(a, b):
    """True when a and b have the same structure and equal non-float values."""
    if isinstance(a, float) and isinstance(b, float):
        return True
    if type(a) is not type(b):
        return a is None and isinstance(b, float) or b is None and isinstance(a, float)
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(floats_only_differ(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(floats_only_differ(x, y) for x, y in zip(a, b))
    return a == b


@pytest.fixture(scope="module")
def broker():
    if find_mosquitto() is None:
        pytest.skip("Eclipse Mosquitto not installed")
    with LocalBroker() as b:
        yield b


@pytest.fixture(scope="module")
def stream(broker):
    """SCN-COOL-001 published for 1200 s through a real broker, recorded by a subscriber."""
    col = Collector(broker.port, "test-stream").start()
    wall0 = wall_clock()
    svc, pub = run(broker.port, until=1200)
    msgs = col.quiet()
    wall1 = wall_clock()
    col.stop()
    yield {"svc": svc, "pub": pub, "msgs": msgs, "port": broker.port, "wall": (wall0, wall1)}
    pub.close()
    svc.shutdown()


# ---------------------------------------------------------------------------- namespace (no broker)
def test_topic_escaping_is_reversible_and_canonical_ids_are_preserved():
    for cid in ("measurement:XMEAS(9)", "production_order:PO-2026-0201", "a/b+c#d%e", "$SYS-like"):
        seg = escape(cid)
        assert "/" not in seg and "+" not in seg and "#" not in seg and not seg.startswith("$")
        assert unescape(seg) == cid
    assert escape("measurement:XMEAS(9)") == "measurement:XMEAS(9)"   # nothing to escape in simulator ids


def test_namespace_hierarchy_is_the_context_model_hierarchy(engine):
    """Every hierarchy element is placed under its parent, with the entity type the context model assigns
    to its ISA-95 level; no unrepresented ISA-95 level appears."""
    ns = Namespace(engine)
    level_type = {d["source"]["hierarchy_level"]: t for t, d in CTX["entity_types"].items()
                  if "hierarchy_level" in d["source"]}
    for el in engine.hierarchy.elements.values():
        cid = f"{level_type[el.level.value]}:{el.id}"
        path = ns.path(cid)
        assert path[-1] == cid
        parent = engine.hierarchy.get(el.parent_id) if el.parent_id else None
        assert ns.parent(cid) == (f"{level_type[parent.level.value]}:{parent.id}" if parent else None)
    types = {seg.split(":", 1)[0] for cid in ns.entity_ids() for seg in ns.path(cid)}
    assert types <= set(CTX["entity_types"])
    assert not types & {"unit", "process_cell", "production_line", "work_cell"}
    chains = {tuple(s.split(":", 1)[0] for s in ns.path(c)) for c in ns.entity_ids()}
    assert ("enterprise", "site", "area", "production_unit", "equipment_module", "control_module") in chains
    assert ("enterprise", "site", "area", "work_center", "work_unit") in chains
    assert ("enterprise", "site", "area", "storage_zone", "storage_unit") in chains
    # entities outside the hierarchy are placed by context-model relationships
    assert ns.parent("utility:UT-CW-REACTOR") == "work_center:WC-CW"          # supplied_by
    assert ns.parent("material:MAT-GH") == ns.parent("product:PROD-GH-M1") == "site:SITE-TE"


# ---------------------------------------------------------------------------- broker basics
@requires_mosquitto
def test_broker_accepts_publish_and_subscribe(broker):
    col = Collector(broker.port, "t-basic", topic="probe/#").start()
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="t-basic-pub")
    c.connect("127.0.0.1", broker.port)
    c.loop_start()
    c.publish("probe/x", "hello", qos=1).wait_for_publish(5)
    msgs = col.quiet(0.3)
    c.loop_stop()
    col.stop()
    assert ("probe/x", "hello", False, 1) in msgs


# ---------------------------------------------------------------------------- stream content
@requires_mosquitto
def test_every_published_entity_is_canonical_and_in_its_isa95_place(stream):
    ns = stream["pub"].ns
    types = CTX["entity_types"]
    metas = loads(stream["msgs"], "meta")
    assert {d["entity_id"] for _, d, _, _ in metas} == set(ns.entity_ids())
    for topic, d, retain, qos in metas:
        segs = [unescape(s) for s in topic.split("/")]
        assert qos == 1 and d["schema"] == SCHEMA        # live delivery: the broker clears the retain flag
        assert segs[-2] == d["entity_id"] and d["entity_type"] == d["entity_id"].split(":", 1)[0] in types
        assert d["native_id"] == d["entity_id"].split(":", 1)[1]
        assert d["parent"] == (segs[-3] if d["parent"] else None)
        assert d["isa95"]["mapping_id"] == types[d["entity_type"]]["isa95"]
    for topic, d, _, _ in loads(stream["msgs"]):
        segs = [unescape(s) for s in topic.split("/")]
        assert segs[:2] == DEFAULT_ROOT.split("/")
        entity_segs = [s for s in segs[2:] if ":" in s]
        assert all(s.split(":", 1)[0] in types for s in entity_segs), topic


@requires_mosquitto
def test_state_measurement_and_event_channels_have_distinct_semantics(stream):
    msgs = stream["msgs"]
    start = stream["svc"].engine.clock.start
    for topic, d, retain, qos in loads(msgs):
        kind = d["kind"]
        if kind == "publisher":                 # transport status of the publisher: no manufacturing time
            assert topic.endswith("/uns/publisher") and set(d) == {"kind", "schema", "status"}
            continue
        assert d["simulation_timestamp"] == iso(start + timedelta(seconds=d["simulation_time"]))  # simulator time
        assert SCOPE_RE.fullmatch(d["operational_scope_id"]) and "timestamp" not in d
        if kind == "measurement":
            assert "/measurement/" in topic and qos == 0
            assert d["simulation_time"] % PERIOD == 0
            assert isinstance(d["unit"], str) and d["variable"] and "value" in d
        elif kind == "event":
            assert "/event/" in topic and qos == 1 and topic.endswith("/" + d["event_type"])
            assert re.fullmatch(r"(OE|LC)-\d{7}", d["event_id"])
        elif kind in ("state", "meta", "lifecycle"):
            assert topic.endswith("/" + kind) and qos == 1
    xmeas = [d for _, d, _, _ in loads(msgs, "measurement") if d["variable"] == "measurement:XMEAS(9)"]
    assert xmeas and all(d["unit"] == "degC" and d["entity_id"] == "equipment_module:EM-REACTOR" for d in xmeas)


@requires_mosquitto
def test_events_are_the_operational_stream_in_order(stream):
    svc, msgs = stream["svc"], stream["msgs"]
    got = [d for _, d, _, _ in loads(msgs, "event")]
    expected = svc.get_events(limit=20000)
    assert [e["event_id"] for e in got] == [e["event_id"] for e in expected]
    assert [e["event_type"] for e in got] == [e["type"] for e in expected]
    oe = [int(e["event_id"][3:]) for e in got if e["event_id"].startswith("OE-")]
    assert oe == list(range(1, len(oe) + 1))
    assert got[0]["event_type"] == "SIMULATION_STARTED" and got[0]["payload"] == {"duration_seconds": 10800}
    # per-topic ordering: measurements advance in time on every topic
    for topic, seq in per_topic([m for m in msgs if "/measurement/" in m[0]]).items():
        times = [json.loads(p)["simulation_time"] for p, _, _ in seq]
        assert times == sorted(times), topic


@requires_mosquitto
def test_operational_boundary_holds_on_mqtt(stream):
    svc, msgs = stream["svc"], stream["msgs"]
    st = svc.engine.state
    text = "\n".join(p for _, p, _, _ in msgs)
    for secret in ("F-COOL-001", "cooling_degradation", "SCN-COOL-001", "RUN-SCN", "hidden cause",
                   svc.engine.config_hash[:10], "xmeas_true", "boundary", "EV-0"):
        assert secret not in text, secret
    forbidden = {"seed", "tep_seed", "run_id", "scenario_id", "configuration_hash", "faults", "active_faults",
                 "health", "capacity_fraction", "availability", "available_capacity", "idv"}

    def keys(x):
        if isinstance(x, dict):
            for k, v in x.items():
                yield k
                yield from keys(v)
        elif isinstance(x, list):
            for v in x:
                yield from keys(v)
    for topic, d, _, _ in loads(msgs):
        if d["kind"] != "meta":                 # meta.isa95 and configuration are static descriptions
            assert not set(keys(d)) & forbidden, (topic, set(keys(d)) & forbidden)
    for topic, d, _, _ in loads(msgs, "state"):
        native = d["entity_id"].split(":", 1)[1]
        if st.has_entity(native):
            assert not set(d["state"]) & hidden_properties(st.entity(native)), topic
    for topic, d, _, _ in loads(msgs, "measurement"):
        native = d["entity_id"].split(":", 1)[1]
        if st.has_entity(native):
            assert d["variable"] not in hidden_properties(st.entity(native)), topic
    for _, d, _, _ in loads(msgs, "event"):
        assert d["event_type"] not in ("UTILITY_STATE_CHANGED", "EQUIPMENT_DEGRADED") and \
            not d["event_type"].startswith("FAULT_")
    assert not any("/fault:" in t or "/operator_action:" in t for t, *_ in msgs)


@requires_mosquitto
def test_late_subscriber_recovers_retained_state_and_no_events(stream):
    col = Collector(stream["port"], "t-late").start()
    msgs = col.quiet()
    col.stop()
    got = {t: p for t, p, r, q in msgs}
    assert all(r for _, _, r, _ in msgs)                                   # only retained messages
    assert not any("/event/" in t for t in got)
    assert got == stream["pub"].retained                                  # exactly the current retained state
    kinds = {json.loads(p)["kind"] for p in got.values()}
    assert {"meta", "state", "measurement", "lifecycle", "publisher"} <= kinds
    assert scopes(msgs) == {operational_scope_id(stream["svc"].engine)}  # one scope, named in every payload
    last_live = {t: p for t, p, _, _ in stream["msgs"]}
    assert all(p == last_live[t] for t, p in got.items())                 # retained = last live publication,
                                                                         # simulation time included


# ---------------------------------------------------------------------------- reconnects
@requires_mosquitto
def test_publisher_restart_keeps_scope_content_and_simulation_time():
    """A new publisher instance attached to the same simulation scope (here between two sampling ticks)
    republishes the current state with the same operational scope id, the same content and the same
    simulation time; only observed_at changes. No event is replayed and nothing is deleted."""
    with LocalBroker() as b:
        svc, pub = run(b.port, until=305)
        before = dict(pub.retained)
        col = Collector(b.port, "t-prec").start()
        col.quiet()
        pub.close()
        pub2 = UNSPublisher(svc, port=b.port, measurement_period_s=PERIOD)
        pub2.connect()
        pub2.sync()
        pub2.flush()
        after = col.quiet()
        col.stop()
        status = pub2._status_topic
        assert {t: publication(p) for t, p in before.items() if t != status} == \
            {t: publication(p) for t, p in pub2.retained.items() if t != status}
        new = {t: p for t, p, r, q in after if t != status}
        assert all(new.values())                             # nothing deleted, not even the last samples
        assert not any("/event/" in t for t in new)          # a restart publishes no events
        assert all(publication(before[t]) == publication(p) for t, p in new.items())   # duplicates only
        scope = operational_scope_id(svc.engine)
        assert scopes(after) == {scope} and scopes([(t, p, 1, 1) for t, p in before.items()]) == {scope}
        assert any(json.loads(p)["simulation_time"] < 305 for p in new.values())   # not re-dated
        pub2.close()
        svc.shutdown()


@requires_mosquitto
def test_subscriber_reconnect_with_persistent_session_receives_queued_events():
    with LocalBroker() as b:
        sub = Collector(b.port, "t-persistent", clean=False).start()
        svc, pub = run(b.port, until=880)
        sub.quiet()
        sub.client.disconnect()
        sub.client.loop_stop()
        eng = svc.engine
        while eng.clock.time_s < 1000:                    # QUALITY_SAMPLE_TAKEN, MATERIAL_CONSUMED occur here
            eng.step(1)
            pub.sync()
        pub.flush()
        sub2 = Collector(b.port, "t-persistent", clean=False).start(subscribe=False)
        msgs = sub.msgs + sub2.quiet()
        sub2.stop()
        got = [d["event_id"] for _, d, _, _ in loads(msgs, "event")]
        expected = [e["event_id"] for e in svc.get_events(limit=20000)]
        assert got == expected                             # nothing lost, no gap, no duplicates here
        assert scopes(msgs) == {operational_scope_id(svc.engine)}   # one scope across both sessions
        pub.close()
        svc.shutdown()


@requires_mosquitto
def test_broker_restart_recovers_retained_state_and_buffered_events():
    b = LocalBroker().start()
    try:
        svc, pub = run(b.port, until=880)
        before = {t: json.loads(p) for t, p in pub.retained.items()}
        b.stop()
        eng = svc.engine
        while eng.clock.time_s < 1000:                    # events while the broker is down are buffered
            eng.step(1)
            pub.sync()
        assert not pub.connected and pub._events_buffer
        b.start()
        col = Collector(b.port, "t-restart").start()
        deadline = time.monotonic() + 15
        while not pub.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pub.connected
        pub.sync()
        pub.flush()
        msgs = col.quiet()
        col.stop()
        late = Collector(b.port, "t-restart-late").start()
        retained = {t: p for t, p, r, q in late.quiet() if r}
        late.stop()
        assert retained == pub.retained                    # the broker had lost everything; all is restored
        events = [d["event_id"] for _, d, _, _ in loads(msgs, "event")]
        outage = [e["event_id"] for e in svc.get_events(since=881, limit=20000)]
        assert outage and events[:len(outage)] == outage   # buffered events delivered in order
        assert scopes(msgs) == {operational_scope_id(svc.engine)}   # a broker restart is not a new scope
        # states unchanged during the outage (measurements are new samples every tick and move on)
        kept = [t for t, d in before.items() if t in retained and d["kind"] in ("state", "meta", "lifecycle")
                and content_of(d) == content_of(json.loads(retained[t]))]
        assert kept
        for t in kept:                                     # republished, not re-dated
            after = json.loads(retained[t])
            assert after["simulation_time"] == before[t]["simulation_time"]
            assert after["observed_at"] > before[t]["observed_at"]
        pub.close()
        svc.shutdown()
    finally:
        b.stop()


# ---------------------------------------------------------------------------- lifecycle and reset
@requires_mosquitto
def test_lifecycle_and_reset_begin_a_clean_identity_scope():
    with LocalBroker() as b:
        col = Collector(b.port, "t-reset").start()
        svc, pub = run(b.port, until=1000)                 # QS-00001 exists (sample at 901)
        svc.start_simulation()                             # service run state (pause applies to a running runner)
        svc.pause_simulation()
        pub.sync()
        svc.resume_simulation()
        pub.sync()
        svc.reset_simulation()
        pub.sync()
        pub.flush()
        msgs = col.quiet()
        col.stop()
        events = [d for _, d, _, _ in loads(msgs, "event")]
        types = [e["event_type"] for e in events]
        assert "SIMULATION_PAUSED" in types and "SIMULATION_RESUMED" in types
        reset = types.index("SIMULATION_RESET")
        assert events[reset]["event_id"] == "LC-0000001" and events[reset]["payload"] == {}
        old, new = events[0]["operational_scope_id"], events[reset]["operational_scope_id"]
        assert events[0]["event_type"] == "SIMULATION_STARTED" and old != new == operational_scope_id(svc.engine)
        assert {e["operational_scope_id"] for e in events[:reset]} == {old}
        assert {e["operational_scope_id"] for e in events[reset:]} == {new}
        # retained state of the old scope that does not exist in the new one is deleted
        deleted = {t for t, p, r, q in msgs if not p}
        assert any("quality_sample:QS-00001" in t for t in deleted)
        assert not any("quality_sample:QS-00001" in t for t in pub.retained)
        lifecycle = [d for _, d, _, _ in loads(msgs, "lifecycle")]
        assert [d["status"] for d in lifecycle][-1] == "READY"
        late = Collector(b.port, "t-reset-late").start()
        after = late.quiet()
        late.stop()
        assert not any("QS-00001" in t for t, *_ in after)
        assert scopes(after) == {new}                      # the retained tree holds the new scope only
        pub.close()
        svc.shutdown()


# ---------------------------------------------------------------------------- determinism and non-disclosure
@requires_mosquitto
def test_publication_is_deterministic():
    streams = []
    for _ in range(2):
        with LocalBroker() as b:
            col = Collector(b.port, "t-det").start()
            svc, pub = run(b.port, until=600)
            streams.append(per_topic(normalized(col.quiet())))
            col.stop()
            pub.close()
            svc.shutdown()
    assert streams[0] == streams[1]


@requires_fortran
@requires_mosquitto
def test_hidden_fault_is_not_disclosed_before_the_first_symptom():
    """Faulted SCN-COOL-001 vs the same scenario without its fault, up to 01:20:32. The MQTT streams have
    the same topics, the same message counts per topic, the same retain/QoS flags and identical event
    ids, types, targets, times, discrete state and metadata. Before the fault starts they are identical
    byte for byte. After it, only float values differ: the legitimate physical observations
    (measurements such as vibration or CW header pressure) that a diagnosis must use."""
    faulted = ScenarioStore().load("SCN-COOL-001")
    healthy = copy.deepcopy(faulted)
    healthy["faults"] = []
    streams, ids = [], []
    for scenario in (faulted, healthy):
        with LocalBroker() as b:
            col = Collector(b.port, "t-nd").start()
            svc, pub = run(b.port, scenario=scenario, until=T_FIRST_SYMPTOM - 1)
            raw = col.quiet(timeout=120)
            ids.append(scopes(raw))
            streams.append(per_topic(normalized(raw)))
            col.stop()
            pub.close()
            svc.shutdown()
    a, b = streams
    # one opaque scope id per run, constant across fault onset, different between the two scopes; it
    # is masked in the comparison below, so everything else must be identical as before
    assert all(len(i) == 1 and SCOPE_RE.fullmatch(next(iter(i))) for i in ids) and ids[0] != ids[1]
    assert a.keys() == b.keys()
    differing = set()

    continuous = {f for fields in RECORD_CONTINUOUS.values() for f in fields}

    def discrete(x):
        if isinstance(x, dict):
            return {k: "#" if k in continuous else discrete(v) for k, v in x.items()
                    if k not in ("simulation_time", "simulation_timestamp")}
        if isinstance(x, list):
            return [discrete(v) for v in x]
        return "#" if isinstance(x, float) else x

    for topic in a:
        def when(p):                    # the publisher status carries no simulation time
            return json.loads(p).get("simulation_time", -1) if p else -1
        before_a = [m for m in a[topic] if when(m[0]) < T_FAULT]
        before_b = [m for m in b[topic] if when(m[0]) < T_FAULT]
        assert before_a == before_b, topic                  # identical, byte for byte, before the fault
        if topic.endswith("/state"):
            # state is published when it changes; continuous quantities (lot kilograms) may change on
            # different sampling ticks, so compare the sequence of discrete content
            def seq(msgs):
                out = []
                for p, _, _ in msgs:
                    d = discrete(json.loads(p))
                    if not out or out[-1] != d:
                        out.append(d)
                return out
            assert seq(a[topic]) == seq(b[topic]), topic
            if a[topic] != b[topic]:
                differing.add(topic)
            continue
        assert len(a[topic]) == len(b[topic]), topic
        for (pa, ra, qa), (pb, rb, qb) in zip(a[topic], b[topic]):
            assert (ra, qa) == (rb, qb), topic
            if pa == pb:
                continue
            da, db = json.loads(pa), json.loads(pb)
            assert da["simulation_time"] == db["simulation_time"] >= T_FAULT, topic
            assert floats_only_differ(da, db), (topic, pa, pb)
            differing.add(topic)
    # differences are observed quantities: measurement values, continuous state quantities, and float
    # payload values of events (e.g. kilograms consumed, analyzer results); event identity, type, target,
    # time and causation are identical (checked message by message above)
    kinds = {json.loads(a[t][0][0])["kind"] for t in differing}
    assert differing and kinds <= {"measurement", "state", "event"}
    assert not any(t.endswith("/meta") or t.endswith("/lifecycle") or t.endswith("/uns/publisher") for t in differing)


@requires_fortran
@requires_mosquitto
def test_full_demo_over_mqtt_leaves_the_simulation_unchanged(demo_run):
    with LocalBroker() as b:
        col = Collector(b.port, "t-demo").start()
        svc, pub = run(b.port, until=10800)
        pub.sync()
        pub.flush()
        msgs = col.quiet(timeout=180)
        col.stop()
        eng = svc.engine
        assert eng.completed
        # the simulation is identical to the run without a UNS publisher
        assert [(e.event_id, e.type, e.simulation_time, e.payload) for e in eng.bus.log] == \
            [(e.event_id, e.type, e.simulation_time, e.payload) for e in demo_run.bus.log]
        assert (eng.adapter.get_states() == demo_run.adapter.get_states()).all()
        assert eng.history.to_columns()[1] == demo_run.history.to_columns()[1]
        events = [d for _, d, _, _ in loads(msgs, "event")]
        assert [e["event_id"] for e in events] == [e["event_id"] for e in svc.get_events(limit=50000)]
        alarms = [e for e in events if e["event_type"] == "ALARM_ACTIVATED"]
        assert alarms[0]["payload"]["alarm_id"] == "VAH-CWP101A" and alarms[0]["simulation_time"] == T_FIRST_SYMPTOM
        assert any(e["event_type"] == "EQUIPMENT_REPAIRED" and e["entity_id"] == "work_unit:WU-CWP-101A" for e in events)
        assert any(e["event_type"] == "LOT_STATE_CHANGED" and e["payload"].get("new") == "QUARANTINE" for e in events)
        assert events[-1]["event_type"] == "SIMULATION_COMPLETED"
        assert json.loads(pub.retained[pub.ns.topic(pub.ns.site, "lifecycle")])["status"] == "COMPLETED"
        text = "\n".join(p for _, p, _, _ in msgs)
        assert "F-COOL-001" not in text and "SCN-COOL-001" not in text and "DEGRADED" not in text
        pub.close()
        svc.shutdown()


# ---------------------------------------------------------------------------- operational scope (R-01)
def test_operational_scope_id_belongs_to_the_simulation_scope():
    """Same scope -> same id; reset or a new simulation -> a new id. Reset keeps the run id, so an id
    that changes on reset is not derived from the run id."""
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        first = operational_scope_id(svc.engine)
        assert SCOPE_RE.fullmatch(first)
        svc.engine.step(30)
        assert operational_scope_id(svc.engine) == first               # stable within the scope
        run_id = svc.engine.state.run["run_id"]
        svc.reset_simulation()
        second = operational_scope_id(svc.engine)
        assert second != first and svc.engine.state.run["run_id"] == run_id
        svc.create_simulation("SCN-COOL-001")
        assert operational_scope_id(svc.engine) not in (first, second)
    finally:
        svc.shutdown()


def test_operational_scope_id_discloses_no_benchmark_information():
    """Scopes of the faulted demo (twice, identical run id), its no-fault twin, the baseline and the
    fault library. The id is 'OS-' + 32 hex digits, so it cannot hold a scenario name, id or fault id;
    identical runs get different ids, so it is not a function of scenario, seed, configuration or run
    id; and it is fixed before the first event and never changes, so it carries no time, event count or
    fault onset."""
    store = ScenarioStore()
    faulted = store.load("SCN-COOL-001")
    healthy = copy.deepcopy(faulted)
    healthy["faults"] = []
    cases = [faulted, copy.deepcopy(faulted), healthy, store.load("SCN-BASELINE"), store.load("SCN-FAULT-LIBRARY")]
    ids, run_ids = [], []
    for scenario in cases:
        svc = SimulatorService(start_runner=False)
        try:
            svc.create_simulation(scenario=scenario)
            eng = svc.engine
            sid = operational_scope_id(eng)
            assert SCOPE_RE.fullmatch(sid)
            eng.step(120)
            assert operational_scope_id(eng) == sid                     # independent of time and events
            run = eng.state.run
            for secret in (run["run_id"], scenario["id"], scenario.get("name", ""), eng.config_hash,
                           *[f.get("id", "") for f in scenario.get("faults", [])]):
                assert not secret or secret not in sid
            ids.append(sid)
            run_ids.append(run["run_id"])
        finally:
            svc.shutdown()
    assert run_ids[0] == run_ids[1] and ids[0] != ids[1]                 # same run id, different scopes
    assert len(set(ids)) == len(ids)


@requires_mosquitto
def test_retained_only_subscriber_identifies_the_current_scope():
    """A subscriber that connects after a reset, has no session and never saw SIMULATION_RESET, reads
    only retained messages and can tell which scope they belong to: the lifecycle names it and every
    retained manufacturing payload carries the same id."""
    with LocalBroker() as b:
        svc, pub = run(b.port, until=1000)                 # QS-00001 exists in the first scope
        first = Collector(b.port, "t-first").start()
        old = first.quiet()
        first.stop()
        (old_scope,) = scopes(old)
        svc.reset_simulation()
        pub.sync()
        eng = svc.engine
        while eng.clock.time_s < 60:
            eng.step(1)
            pub.sync()
        pub.flush()
        fresh = Collector(b.port, "t-fresh").start()
        msgs = fresh.quiet()
        fresh.stop()
        assert msgs and all(r for _, _, r, _ in msgs) and not any("/event/" in t for t, *_ in msgs)
        lifecycle = json.loads(next(p for t, p, _, _ in msgs if t.endswith("/lifecycle")))
        current = lifecycle["operational_scope_id"]
        assert current != old_scope and current == operational_scope_id(svc.engine)
        assert scopes(msgs) == {current}                   # nothing stale, nothing mixed
        assert not any("QS-00001" in t for t, *_ in msgs)
        assert all(json.loads(p)["simulation_time"] <= 60 for _, p, _, _ in msgs
                   if json.loads(p)["kind"] != "publisher")
        pub.close()
        svc.shutdown()


# ---------------------------------------------------------------------------- timestamps
@requires_mosquitto
def test_simulation_time_is_manufacturing_time_and_observed_at_is_wall_clock(stream):
    """Events carry the simulator time of the event, measurements the time of their sample, and every
    manufacturing time is on the simulated calendar; observed_at is the wall-clock time of publication
    and is never equal to, or used as, simulation time."""
    svc, msgs = stream["svc"], stream["msgs"]
    wall0, wall1 = stream["wall"]
    by_id = {e["event_id"]: e for e in svc.get_events(limit=20000)}
    seen = set()
    for _, d, _, _ in loads(msgs):
        if d["kind"] == "publisher":
            continue
        seen.add(d["kind"])
        assert wall0 <= d["observed_at"] <= wall1
        assert d["observed_at"] != d["simulation_timestamp"]
        if d["kind"] == "event":
            e = by_id[d["event_id"]]
            assert (d["simulation_time"], d["simulation_timestamp"]) == (e["simulation_time"], e["timestamp"])
        elif d["kind"] == "measurement":
            assert d["simulation_time"] % PERIOD == 0
    assert seen == {"meta", "state", "measurement", "event", "lifecycle"}


# ---------------------------------------------------------------------------- broker security boundary
def test_broker_configuration_is_local_development_only_and_documented():
    conf = CONF.read_text(encoding="utf-8")
    directives = {}
    for line in conf.splitlines():
        words = line.split("#", 1)[0].split()
        if words:
            directives[words[0]] = words[1:]
    assert directives["listener"] == ["1883", "127.0.0.1"]             # loopback only
    assert directives["allow_anonymous"] == ["true"] and directives["persistence"] == ["false"]
    assert not {"password_file", "acl_file", "certfile", "keyfile", "cafile"} & set(directives)
    assert "NOT a production security configuration" in conf
    for doc in ("docs/UNS.md", "docs/UNS_OPERATING_MODEL.md"):
        assert "not a production security configuration" in (ROOT_DIR / doc).read_text(encoding="utf-8").lower()
