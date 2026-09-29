"""The read-only UNS inspector (uns/inspector.py): an ordinary MQTT client of the real UNS.

The inspector is fed by a real Eclipse Mosquitto broker and the real UNS publisher; its HTTP view is
exercised with FastAPI's test client. Nothing here gives the inspector access to the simulator.
"""
import json
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from api.operational import operational_scope_id
from uns.broker import LocalBroker, find_mosquitto
from uns.inspector import UNSInspector, create_app, parse_topic, unescape, wait_for
from uns.namespace import ROOT_DIR, escape
from uns.namespace import unescape as ns_unescape
from uns.publisher import UNSPublisher

from .test_uns import Collector, run

requires_mosquitto = pytest.mark.skipif(find_mosquitto() is None, reason="Eclipse Mosquitto not installed")
# payload keys and strings the UNS must never carry, so the inspector must never show
FORBIDDEN_KEYS = {"seed", "tep_seed", "run_id", "scenario_id", "configuration_hash", "faults", "active_faults",
                  "health", "capacity_fraction", "idv"}
FORBIDDEN_TEXT = ("F-COOL-001", "SCN-COOL-001", "RUN-SCN", "cooling_degradation", "EV-0")


def keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from keys(v)


def step(svc, pub, until):
    eng = svc.engine
    while eng.clock.time_s < until and not eng.completed:
        eng.step(1)
        pub.sync()
    pub.flush()


# ---------------------------------------------------------------------------- no simulator coupling
def test_inspector_imports_nothing_from_the_simulator():
    """Importing the inspector (and its launcher's imports) loads no simulator, API or publisher module:
    it can only know what arrives over MQTT."""
    code = ("import sys; sys.path.insert(0, '.'); import uns.inspector; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('simulator', 'api') "
            "or m in ('uns.namespace', 'uns.publisher')); print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_inspector_topic_parsing_agrees_with_the_namespace():
    for cid in ("measurement:XMEAS(9)", "a/b+c#d%e", "$SYS-like", "production_order:PO-2026-0201"):
        assert unescape(escape(cid)) == ns_unescape(escape(cid)) == cid
    topic = ("uns/v1/enterprise:ENT-ACME/site:SITE-TE/area:AREA-REACTION/production_unit:PU-REACTOR/"
             "equipment_module:EM-REACTOR/measurement/measurement:XMEAS(9)")
    assert parse_topic(topic) == (["enterprise:ENT-ACME", "site:SITE-TE", "area:AREA-REACTION",
                                   "production_unit:PU-REACTOR", "equipment_module:EM-REACTOR"],
                                  "measurement", "measurement:XMEAS(9)")


# ---------------------------------------------------------------------------- live view of the real UNS
@pytest.fixture(scope="module")
def live():
    if find_mosquitto() is None:
        pytest.skip("Eclipse Mosquitto not installed")
    with LocalBroker() as b:
        insp = UNSInspector(port=b.port, client_id="t-inspector").start()
        assert wait_for(lambda: insp.connected)
        col = Collector(b.port, "t-insp-collector").start()
        svc, pub = run(b.port, until=1000)           # SIMULATION_STARTED ... QUALITY_SAMPLE_TAKEN at 901
        col.quiet()
        http = TestClient(create_app(insp))
        assert wait_for(lambda: http.get("/api/status").json()["lifecycle"] is not None)
        yield {"broker": b, "insp": insp, "http": http, "svc": svc, "pub": pub, "col": col}
        col.stop()
        pub.close()
        svc.shutdown()
        insp.stop()


@requires_mosquitto
def test_inspector_serves_its_page(live):
    r = live["http"].get("/")
    assert r.status_code == 200 and "UNS Inspector" in r.text
    assert live["http"].get("/inspector/inspector.js").status_code == 200
    assert live["http"].get("/app.css").status_code == 200
    assert live["http"].post("/api/status").status_code == 405           # read-only: GET routes only


@requires_mosquitto
def test_status_shows_connection_lifecycle_and_scope(live):
    s = live["http"].get("/api/status").json()
    svc, pub = live["svc"], live["pub"]
    assert s["mqtt"]["connected"] and s["mqtt"]["port"] == live["broker"].port
    retained_life = json.loads(pub.retained[pub.ns.topic(pub.ns.site, "lifecycle")])
    assert s["lifecycle"]["status"] == retained_life["status"]
    assert s["operational_scope_id"] == operational_scope_id(svc.engine)
    assert s["publisher"] == "online"
    assert s["latest_simulation_time"] >= 1000                         # manufacturing time, from payloads


@requires_mosquitto
def test_tree_is_the_isa95_hierarchy_received_over_mqtt(live):
    ns = live["pub"].ns
    tree = live["http"].get("/api/tree").json()
    assert {n["entity_id"] for n in tree} == set(ns.entity_ids())
    assert all(n["parent"] == ns.parent(n["entity_id"]) for n in tree)
    e = live["http"].get("/api/entity/equipment_module:EM-REACTOR").json()
    assert [l["entity_id"].split(":")[0] for l in e["lineage"]] == \
        ["enterprise", "site", "area", "production_unit", "equipment_module"]
    assert e["isa95"]["mapping_id"] == "M-EQUIPMENT-MODULE" and e["parent"] == "production_unit:PU-REACTOR"
    assert "control_module:CM-TIC-RX" in e["children"]


@requires_mosquitto
def test_selected_entity_shows_retained_state_measurements_and_topics(live):
    http, col = live["http"], live["col"]
    e = http.get("/api/entity/work_unit:WU-CWP-101A").json()
    assert e["state"] and e["state"]["data"]["kind"] == "state" and e["state"]["data"]["state"]
    reactor = http.get("/api/entity/equipment_module:EM-REACTOR").json()
    xmeas9 = next(m for m in reactor["measurements"] if m["variable"] == "measurement:XMEAS(9)")
    assert xmeas9["unit"] == "degC" and xmeas9["simulation_time"] == 1000 and len(xmeas9["series"]) >= 2
    # every topic shown is an actual topic received, with the payload exactly as received
    last = {t: p for t, p, _, _ in col.msgs}
    for m in reactor["topics"]:
        assert m["topic"] in last and m["payload"] == last[m["topic"]]
    assert all(m["topic"].startswith(reactor["topic_prefix"]) for m in reactor["topics"])


@requires_mosquitto
def test_measurements_update_live(live):
    http, svc, pub = live["http"], live["svc"], live["pub"]

    def xmeas9():
        e = http.get("/api/entity/equipment_module:EM-REACTOR").json()
        return next(m for m in e["measurements"] if m["variable"] == "measurement:XMEAS(9)")
    before = xmeas9()
    step(svc, pub, 1060)
    assert wait_for(lambda: xmeas9()["simulation_time"] == 1060)
    after = xmeas9()
    assert len(after["series"]) > len(before["series"]) and after["series"][-1][0] == 1060


@requires_mosquitto
def test_operational_events_appear_with_their_ids(live):
    http, svc = live["http"], live["svc"]
    events = http.get("/api/events?limit=500").json()
    got = [m["data"]["event_id"] for m in reversed(events)]
    expected = [e["event_id"] for e in svc.get_events(limit=20000)]
    assert got == expected                    # connected before the run: the whole operational stream
    site = http.get("/api/entity/site:SITE-TE").json()
    assert any(m["data"]["event_type"] == "SIMULATION_STARTED" for m in site["events"])
    analyzer = http.get("/api/entity/control_module:CM-AT-PRODUCT").json()
    assert any(r["entity_id"].startswith("quality_sample:") for r in analyzer["records"])


@requires_mosquitto
def test_inspector_shows_no_hidden_benchmark_information(live):
    http = live["http"]
    bodies = [http.get("/api/status").json(), http.get("/api/tree").json(), http.get("/api/events?limit=500").json()]
    bodies += [http.get(f"/api/entity/{n['entity_id']}").json() for n in bodies[1]]
    text = json.dumps(bodies)
    for secret in FORBIDDEN_TEXT:
        assert secret not in text, secret
    shown = [m for e in bodies[3:] for m in e["topics"] + e["events"]] + bodies[2]
    assert shown
    for m in shown:
        d = json.loads(m["payload"])
        assert d == m["data"]                                         # displayed as received
        if d["kind"] != "meta":                                       # meta is a static description
            assert not set(keys(d)) & FORBIDDEN_KEYS, (m["topic"], set(keys(d)) & FORBIDDEN_KEYS)


# ---------------------------------------------------------------------------- scope, restarts, reconnects
@requires_mosquitto
def test_scope_changes_on_reset_and_not_on_publisher_or_broker_restart():
    with LocalBroker() as b:
        insp = UNSInspector(port=b.port, client_id="t-inspector-scope").start()
        http = TestClient(create_app(insp))
        svc, pub = run(b.port, until=950)             # QS-00001 exists
        first = operational_scope_id(svc.engine)
        assert wait_for(lambda: http.get("/api/status").json()["operational_scope_id"] == first)
        assert http.get("/api/status").json()["scope_change"] is None

        # reset: a new scope, shown from the retained lifecycle; old records disappear
        svc.reset_simulation()
        pub.sync()
        step(svc, pub, 30)
        second = operational_scope_id(svc.engine)
        assert wait_for(lambda: http.get("/api/status").json()["operational_scope_id"] == second)
        change = http.get("/api/status").json()["scope_change"]
        assert change["previous"] == first and change["current"] == second
        assert wait_for(lambda: not any(r["entity_id"].startswith("quality_sample:") for r in
                                        http.get("/api/entity/control_module:CM-AT-PRODUCT").json()["records"]))
        e = http.get("/api/entity/work_unit:WU-CWP-101A").json()
        assert e["state"]["data"]["operational_scope_id"] == second

        # publisher restart: same scope, no scope change
        pub.close()
        pub = UNSPublisher(svc, port=b.port)
        pub.connect()
        pub.sync()
        step(svc, pub, 60)
        time.sleep(0.5)
        s = http.get("/api/status").json()
        assert s["operational_scope_id"] == second and s["scope_change"]["current"] == second
        assert s["publisher"] == "online"

        # broker restart: the inspector shows DISCONNECTED, reconnects and recovers the retained state
        b.stop()
        assert wait_for(lambda: not http.get("/api/status").json()["mqtt"]["connected"])
        b.start()
        assert wait_for(lambda: http.get("/api/status").json()["mqtt"]["connected"], timeout=20)
        assert wait_for(lambda: pub.connected, timeout=20)
        pub.sync()
        pub.flush()
        assert wait_for(lambda: len(http.get("/api/tree").json()) == len(pub.ns.entity_ids()), timeout=20)
        s = http.get("/api/status").json()
        assert s["operational_scope_id"] == second and s["mqtt"]["connects"] >= 2
        pub.close()
        svc.shutdown()
        insp.stop()


@requires_mosquitto
def test_inspector_started_after_a_reset_learns_the_scope_from_retained_state_only():
    with LocalBroker() as b:
        svc, pub = run(b.port, until=200)
        svc.reset_simulation()
        pub.sync()
        step(svc, pub, 20)
        insp = UNSInspector(port=b.port, client_id="t-inspector-late").start()   # never saw the RESET event
        http = TestClient(create_app(insp))
        scope = operational_scope_id(svc.engine)
        assert wait_for(lambda: http.get("/api/status").json()["operational_scope_id"] == scope)
        assert http.get("/api/events").json() == []                  # events are not retained
        assert http.get("/api/status").json()["scope_change"] is None
        pub.close()
        svc.shutdown()
        insp.stop()
