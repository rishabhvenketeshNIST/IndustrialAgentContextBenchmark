"""The integrated local manufacturing stack (docs/LOCAL_MANUFACTURING_STACK.md).

One simulation (the web server's SimulatorService) is observed by two independent views: the simulator
web UI/API, and the UNS publisher -> MQTT -> UNS inspector. The tests drive the simulation only through
the simulator API, and read the inspector only through its own HTTP view of MQTT.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.operational import operational_scope_id
from api.service import SimulatorService
from uns.broker import LocalBroker, find_mosquitto, free_port, port_open
from uns.inspector import UNSInspector, wait_for
from uns.inspector import create_app as create_inspector_app
from uns.namespace import ROOT_DIR
from uns.publisher import UNSPublisher

from .conftest import requires_fortran

requires_mosquitto = pytest.mark.skipif(find_mosquitto() is None, reason="Eclipse Mosquitto not installed")
FORBIDDEN_TEXT = ("F-COOL-001", "SCN-COOL-001", "RUN-SCN", "cooling_degradation", '"seed"', '"run_id"', "EV-0")
# UNS messages of the full SCN-COOL-001 demo, as published by the standalone runner (scripts/run_uns.py)
DEMO_MESSAGES = {"meta": 101, "state": 7190, "measurement": 188090, "event": 139, "lifecycle": 4, "delete": 0}


class Stack:
    """In-process integrated stack: web app + service + attached UNS publisher, broker, inspector."""

    def __init__(self, broker, speed=200.0):
        self.broker = broker
        self.svc = SimulatorService()                       # with its real-time runner, as in run.py
        self.app = create_app(self.svc, autoload="SCN-COOL-001")
        self.svc.set_speed(speed)
        self.pub = UNSPublisher(self.svc, port=broker.port).attach()
        self.app.state.uns = self.pub
        self.app.state.links["uns_inspector"] = "http://127.0.0.1:8050/"
        self.sim = TestClient(self.app)
        self.inspector = UNSInspector(port=broker.port, client_id=f"t-stack-{broker.port}",
                                      links={"simulator_ui": "http://127.0.0.1:8000/"}).start()
        self.insp = TestClient(create_inspector_app(self.inspector))

    # the three views of the simulation scope
    def scopes(self):
        pub = self.pub
        life = json.loads(pub.retained[pub.ns.topic(pub.ns.site, "lifecycle")])
        return (self.sim.get("/api/uns/status").json()["operational_scope_id"],
                life["operational_scope_id"], self.insp.get("/api/status").json()["operational_scope_id"])

    def lifecycle(self):
        pub = self.pub
        return (self.sim.get("/api/simulation").json()["status"],
                json.loads(pub.retained[pub.ns.topic(pub.ns.site, "lifecycle")])["status"],
                (self.insp.get("/api/status").json()["lifecycle"] or {}).get("status"))

    def close(self):
        self.pub.close()
        self.inspector.stop()
        self.svc.shutdown()


@pytest.fixture()
def stack():
    if find_mosquitto() is None:
        pytest.skip("Eclipse Mosquitto not installed")
    with LocalBroker() as b:
        s = Stack(b)
        assert wait_for(lambda: s.insp.get("/api/status").json()["operational_scope_id"] is not None)
        yield s
        s.close()


# ---------------------------------------------------------------------------- one simulation, one scope
@requires_mosquitto
def test_ui_api_uns_and_inspector_observe_one_simulation(stack):
    svc, pub = stack.svc, stack.pub
    assert pub.service is svc and pub._engine is svc.engine            # the publisher follows the UI's engine
    scope = operational_scope_id(svc.engine)
    assert wait_for(lambda: stack.scopes() == (scope, scope, scope))
    status = stack.sim.get("/api/uns/status").json()
    assert status["uns"]["enabled"] and status["uns"]["connected"] and status["uns"]["port"] == stack.broker.port
    assert status["links"] == {"uns_inspector": "http://127.0.0.1:8050/"}
    assert stack.insp.get("/api/status").json()["links"] == {"simulator_ui": "http://127.0.0.1:8000/"}
    assert not any(x in json.dumps(status) for x in FORBIDDEN_TEXT)     # the scope id only, never the run id


@requires_mosquitto
def test_start_pause_resume_propagate_to_uns_and_inspector(stack):
    sim = stack.sim
    assert wait_for(lambda: stack.lifecycle() == ("READY", "READY", "READY"))
    sim.post("/api/simulation/start")
    assert wait_for(lambda: stack.lifecycle() == ("RUNNING", "RUNNING", "RUNNING"))
    assert wait_for(lambda: (stack.insp.get("/api/status").json()["latest_simulation_time"] or 0) >= 40, timeout=30)
    sim.post("/api/simulation/pause")
    assert wait_for(lambda: stack.lifecycle() == ("PAUSED", "PAUSED", "PAUSED"))
    t = stack.sim.get("/api/simulation").json()["clock"]["time_s"]
    time.sleep(0.5)
    assert stack.sim.get("/api/simulation").json()["clock"]["time_s"] == t          # paused means paused
    events = [m["data"]["event_type"] for m in stack.insp.get("/api/events?limit=500").json()]
    assert "SIMULATION_PAUSED" in events
    sim.post("/api/simulation/resume")
    assert wait_for(lambda: stack.lifecycle() == ("RUNNING", "RUNNING", "RUNNING"))
    assert wait_for(lambda: "SIMULATION_RESUMED" in [m["data"]["event_type"]
                                                     for m in stack.insp.get("/api/events?limit=500").json()])


@requires_mosquitto
def test_simulator_state_and_measurements_are_what_the_inspector_shows(stack):
    sim, insp = stack.sim, stack.insp
    sim.post("/api/simulation/run_until", json={"time_s": 300})           # a sampling tick: t % 10 == 0
    assert wait_for(lambda: insp.get("/api/status").json()["latest_simulation_time"] == 300)
    xmeas9 = next(m for m in sim.get("/api/process/measurements").json() if m["id"] == "XMEAS(9)"
                  or m.get("index") == 9)
    reactor = insp.get("/api/entity/equipment_module:EM-REACTOR").json()
    shown = next(m for m in reactor["measurements"] if m["variable"] == "measurement:XMEAS(9)")
    assert shown["simulation_time"] == 300 and shown["value"] == pytest.approx(xmeas9["value"], abs=0, rel=0)
    pump_api = sim.get("/api/entities/WU-CWP-101A").json()
    pump_uns = insp.get("/api/entity/work_unit:WU-CWP-101A").json()["state"]["data"]["state"]
    assert pump_uns["status"] == pump_api["properties"]["status"]
    # every simulated second was observed: all measurement ticks 0..300 arrived in the sparkline
    assert [p[0] for p in shown["series"]][-5:] == [260, 270, 280, 290, 300]


@requires_mosquitto
def test_reset_starts_a_new_scope_everywhere(stack):
    sim = stack.sim
    sim.post("/api/simulation/run_until", json={"time_s": 950})           # QS-00001 exists
    before = stack.scopes()[0]
    sim.post("/api/simulation/reset")
    new = operational_scope_id(stack.svc.engine)
    assert new != before and stack.pub._engine is stack.svc.engine
    assert wait_for(lambda: stack.scopes() == (new, new, new))
    assert wait_for(lambda: stack.lifecycle() == ("READY", "READY", "READY"))
    change = stack.insp.get("/api/status").json()["scope_change"]
    assert change == {**change, "previous": before, "current": new}
    # old state is not current: the old scope's retained records are deleted (after the new snapshot)
    assert wait_for(lambda: not any(r["entity_id"].startswith("quality_sample:") for r in
                                    stack.insp.get("/api/entity/control_module:CM-AT-PRODUCT").json()["records"]))
    sim.post("/api/simulation/start")
    assert wait_for(lambda: stack.lifecycle() == ("RUNNING", "RUNNING", "RUNNING"))
    assert stack.scopes() == (new, new, new)


@requires_mosquitto
def test_publisher_and_broker_restarts_preserve_the_scope(stack):
    sim = stack.sim
    sim.post("/api/simulation/run_until", json={"time_s": 120})
    scope = operational_scope_id(stack.svc.engine)
    assert wait_for(lambda: stack.scopes() == (scope, scope, scope))
    # publisher restart inside the running server
    stack.pub.close()
    stack.pub = UNSPublisher(stack.svc, port=stack.broker.port).attach()
    stack.app.state.uns = stack.pub
    sim.post("/api/simulation/run_until", json={"time_s": 180})
    assert wait_for(lambda: stack.scopes() == (scope, scope, scope))
    assert stack.insp.get("/api/status").json()["scope_change"] is None
    # broker restart
    stack.broker.stop()
    assert wait_for(lambda: not sim.get("/api/uns/status").json()["uns"]["connected"])
    assert wait_for(lambda: not stack.insp.get("/api/status").json()["mqtt"]["connected"])
    stack.broker.start()
    assert wait_for(lambda: sim.get("/api/uns/status").json()["uns"]["connected"], timeout=20)
    sim.post("/api/simulation/run_until", json={"time_s": 200})          # the next step resynchronises
    assert wait_for(lambda: stack.insp.get("/api/status").json()["mqtt"]["connected"], timeout=20)
    assert wait_for(lambda: stack.scopes() == (scope, scope, scope), timeout=20)
    assert wait_for(lambda: len(stack.insp.get("/api/tree").json()) == len(stack.pub.ns.entity_ids()), timeout=20)


@requires_mosquitto
def test_inspector_shows_no_hidden_information_in_the_integrated_stack(stack):
    stack.sim.post("/api/simulation/run_until", json={"time_s": 600})
    assert wait_for(lambda: stack.insp.get("/api/status").json()["latest_simulation_time"] == 600)
    tree = stack.insp.get("/api/tree").json()
    text = json.dumps([stack.insp.get("/api/status").json(), tree, stack.insp.get("/api/events?limit=500").json()]
                      + [stack.insp.get(f"/api/entity/{n['entity_id']}").json() for n in tree])
    for secret in FORBIDDEN_TEXT:
        assert secret not in text, secret


@requires_fortran
@requires_mosquitto
def test_full_demo_through_the_integrated_stack_is_unchanged(demo_run):
    """The full SCN-COOL-001 run driven through the simulator API with the UNS publisher attached is the
    same simulation as without it, and the UNS publishes the same messages as the standalone runner."""
    with LocalBroker() as b:
        svc = SimulatorService(start_runner=False)
        app = create_app(svc, autoload="SCN-COOL-001")
        pub = UNSPublisher(svc, port=b.port).attach()
        sim = TestClient(app)
        sim.post("/api/simulation/run_until", json={"time_s": 10800})
        pub.flush()
        eng = svc.engine
        assert eng.completed and sim.get("/api/simulation").json()["status"] == "COMPLETED"
        assert [(e.event_id, e.type, e.simulation_time, e.payload) for e in eng.bus.log] == \
            [(e.event_id, e.type, e.simulation_time, e.payload) for e in demo_run.bus.log]
        assert (eng.adapter.get_states() == demo_run.adapter.get_states()).all()
        assert json.loads(pub.retained[pub.ns.topic(pub.ns.site, "lifecycle")])["status"] == "COMPLETED"
        assert pub.sent == DEMO_MESSAGES and pub.publish_errors == 0
        pub.close()
        svc.shutdown()


# ---------------------------------------------------------------------------- the launcher and standalone modes
def test_launcher_and_inspector_import_no_simulator():
    """The orchestrator creates no simulation (only run.py does) and the inspector reads MQTT only."""
    code = ("import sys, importlib.util; sys.path.insert(0, '.'); "
            "spec = importlib.util.spec_from_file_location('stack', 'scripts/run_manufacturing_stack.py'); "
            "spec.loader.exec_module(importlib.util.module_from_spec(spec)); import uns.inspector; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('simulator', 'api') "
            "or m in ('uns.namespace', 'uns.publisher')); print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT_DIR, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_simulator_server_refuses_a_busy_port_before_starting():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=ROOT_DIR,
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "already in use" in r.stdout
    assert "created simulation" not in r.stdout + r.stderr and time.monotonic() - t0 < 30


def test_simulator_server_without_uns_still_reports_its_scope():
    svc = SimulatorService(start_runner=False)
    client = TestClient(create_app(svc, autoload="SCN-COOL-001"))
    status = client.get("/api/uns/status").json()
    assert status == {"operational_scope_id": operational_scope_id(svc.engine), "uns": {"enabled": False}, "links": {}}
    svc.shutdown()


@requires_mosquitto
def test_standalone_uns_runner_still_works():
    port = free_port()
    r = subprocess.run([sys.executable, "scripts/run_uns.py", "--start-broker", "--port", str(port), "--speed", "0",
                        "--duration", "30"], cwd=ROOT_DIR, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0 and "completed at t = 30 s" in r.stdout, r.stdout + r.stderr


@requires_mosquitto
def test_launcher_starts_the_stack_and_ctrl_c_stops_everything():
    ports = {"sim": free_port(), "insp": free_port(), "mqtt": free_port()}
    argv = [sys.executable, "scripts/run_manufacturing_stack.py", "--start-broker", "--no-browser", "--speed", "100",
            "--port", str(ports["sim"]), "--inspector-port", str(ports["insp"]), "--mqtt-port", str(ports["mqtt"])]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(argv, cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            creationflags=flags, start_new_session=os.name != "nt")
    lines = []
    threading.Thread(target=lambda: [lines.append(l) for l in proc.stdout], daemon=True).start()
    try:
        assert wait_for(lambda: any("Ctrl-C stops the stack" in l for l in lines), timeout=120), "".join(lines)
        sim, insp = f"http://127.0.0.1:{ports['sim']}", f"http://127.0.0.1:{ports['insp']}"

        def get(url):
            import urllib.request
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read())

        def post(url):
            import urllib.request
            req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        scope = get(sim + "/api/uns/status")["operational_scope_id"]
        assert get(insp + "/api/status")["operational_scope_id"] == scope
        post(sim + "/api/simulation/start")
        assert wait_for(lambda: get(insp + "/api/status")["lifecycle"]["status"] == "RUNNING")
        assert wait_for(lambda: (get(insp + "/api/status")["latest_simulation_time"] or 0) > 0)
    finally:
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
    assert any("[stack] stopped" in l for l in lines), "".join(lines)
    assert wait_for(lambda: not any(port_open(p) for p in ports.values()), timeout=20), \
        {k: port_open(p) for k, p in ports.items()}


@requires_mosquitto
@pytest.mark.skipif(os.name != "nt", reason="job-object cleanup is Windows-specific")
def test_killing_the_launcher_leaves_no_orphaned_processes():
    """Even a hard kill of the launcher (closed console, TerminateProcess) takes the broker, simulator
    and inspector with it: they belong to the launcher's kill-on-close job object."""
    ports = {"sim": free_port(), "insp": free_port(), "mqtt": free_port()}
    proc = subprocess.Popen([sys.executable, "scripts/run_manufacturing_stack.py", "--start-broker", "--no-browser",
                             "--port", str(ports["sim"]), "--inspector-port", str(ports["insp"]),
                             "--mqtt-port", str(ports["mqtt"])], cwd=ROOT_DIR, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    lines = []
    threading.Thread(target=lambda: [lines.append(l) for l in proc.stdout], daemon=True).start()
    assert wait_for(lambda: any("Ctrl-C stops the stack" in l for l in lines), timeout=120), "".join(lines)
    assert all(port_open(p) for p in ports.values())
    proc.kill()                                                      # TerminateProcess: no cleanup code runs
    proc.wait(30)
    assert wait_for(lambda: not any(port_open(p) for p in ports.values()), timeout=20), \
        {k: port_open(p) for k, p in ports.items()}
