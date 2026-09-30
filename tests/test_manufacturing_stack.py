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


# ---------------------------------------------------------------------------- the Historian in the stack
class HistorianStack(Stack):
    """The in-process stack with the Historian: its writer observes the same service as the UNS
    publisher, and a read-only Historian API serves the file being written (current access)."""

    def __init__(self, broker, path):
        super().__init__(broker)
        from historian.api import create_app as create_historian_app
        from historian.writer import HistorianWriter
        self.path = path
        self.writer = HistorianWriter(self.svc, path).attach()
        self.hist = TestClient(create_historian_app(path))

    def scopes(self):
        return super().scopes() + (self.hist.get("/status").json()["current_scope"],)

    def historian_lifecycle(self):
        scope = self.hist.get("/status").json()["current_scope"]
        t = self.sim.get("/api/simulation").json()["clock"]["time_s"]
        state = self.hist.get("/state_at", params={"scope": scope, "entity": "site:SITE-TE", "t": t}).json()["state"]
        return state.get("lifecycle_status", {}).get("value")

    def close(self):
        self.writer.close()
        super().close()


@pytest.fixture()
def hstack(tmp_path, monkeypatch):
    if find_mosquitto() is None:
        pytest.skip("Eclipse Mosquitto not installed")
    import api.service as service_module
    built = []

    class CountingEngine(service_module.SimulationEngine):      # counts every simulation engine created
        def __init__(self, *a, **kw):
            built.append(self)
            super().__init__(*a, **kw)
    monkeypatch.setattr(service_module, "SimulationEngine", CountingEngine)
    with LocalBroker() as b:
        s = HistorianStack(b, tmp_path / "history.sqlite")
        s.engines = built
        assert wait_for(lambda: len(set(s.scopes())) == 1 and s.scopes()[0] is not None)
        yield s
        s.close()


@requires_mosquitto
def test_one_simulation_is_observed_by_ui_uns_historian_and_inspector(hstack):
    svc = hstack.svc
    assert len(hstack.engines) == 1                                               # exactly one simulator
    assert hstack.writer.service is svc and hstack.pub.service is svc
    assert hstack.writer._engine is svc.engine is hstack.pub._engine
    scope = operational_scope_id(svc.engine)
    assert wait_for(lambda: hstack.scopes() == (scope,) * 4)                   # UI, UNS, inspector, Historian API
    assert hstack.hist.get("/status").json()["access"] == "current"
    assert hstack.app.state.service is svc


@requires_mosquitto
def test_lifecycle_and_reset_propagate_to_the_historian(hstack):
    from historian.reader import HistorianReader
    sim, hist = hstack.sim, hstack.hist
    first = hstack.scopes()[0]
    assert wait_for(lambda: hstack.historian_lifecycle() == "READY")
    sim.post("/api/simulation/start")
    assert wait_for(lambda: hstack.lifecycle() == ("RUNNING",) * 3 and hstack.historian_lifecycle() == "RUNNING")
    assert wait_for(lambda: sim.get("/api/simulation").json()["clock"]["time_s"] >= 60, timeout=30)
    sim.post("/api/simulation/pause")
    assert wait_for(lambda: hstack.lifecycle() == ("PAUSED",) * 3 and hstack.historian_lifecycle() == "PAUSED")
    sim.post("/api/simulation/resume")
    assert wait_for(lambda: hstack.lifecycle() == ("RUNNING",) * 3 and hstack.historian_lifecycle() == "RUNNING")
    sim.post("/api/simulation/pause")
    types = [e["event_type"] for e in hist.get("/events", params={"scope": first, "start": 0, "end": 86400}).json()["rows"]]
    assert {"SIMULATION_STARTED", "SIMULATION_PAUSED", "SIMULATION_RESUMED"} <= set(types)
    # reset: a new scope everywhere; the old one stays recorded, readable only with evaluator access
    sim.post("/api/simulation/reset")
    second = operational_scope_id(hstack.svc.engine)
    assert second != first and len(hstack.engines) == 2
    assert wait_for(lambda: hstack.scopes() == (second,) * 4)
    assert hist.get("/entities", params={"scope": first}).status_code == 403
    # completion: a short simulation (a new scope again) run to its end
    sim.post("/api/simulation/create", json={"scenario_id": "SCN-COOL-001", "duration_seconds": 120})
    third = operational_scope_id(hstack.svc.engine)
    sim.post("/api/simulation/run_until", json={"time_s": 120})
    assert wait_for(lambda: hstack.lifecycle() == ("COMPLETED",) * 3 and hstack.historian_lifecycle() == "COMPLETED")
    assert wait_for(lambda: hstack.scopes() == (third,) * 4)
    ev = HistorianReader(hstack.path, access="evaluator")
    assert [s["operational_scope_id"] for s in ev.scopes()] == [first, second, third]
    assert ev.coverage(third) == [{"from_t": 0, "to_t": 120}]
    for scope in (first, second, third):                                          # never mixed
        n = ev.db.execute("SELECT COUNT(*) FROM events e JOIN entities n ON n.entity_id = e.entity_id AND "
                          "n.operational_scope_id = e.operational_scope_id WHERE e.operational_scope_id = ?",
                          (scope,)).fetchone()[0]
        assert n == len(ev.events(scope, 0, 86400, limit=10000)["rows"])
    ev.close()
    assert len(hstack.engines) == 3                                               # one per scope, nothing else


@requires_mosquitto
def test_the_historian_api_reads_the_file_being_written(hstack):
    from historian.reader import HistorianReader
    sim, hist = hstack.sim, hstack.hist
    sim.post("/api/simulation/run_until", json={"time_s": 90})                   # ends PAUSED: committed
    scope = hstack.scopes()[0]
    got = hist.get("/samples", params={"scope": scope, "entity": "equipment_module:EM-REACTOR",
                                       "variable": "measurement:XMEAS(9)", "start": 0, "end": 91}).json()
    direct = HistorianReader(hstack.path)
    assert got["rows"] == direct.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 91)["rows"]
    assert [r["t"] for r in got["rows"]] == list(range(91))
    sim.post("/api/simulation/run_until", json={"time_s": 150})
    assert wait_for(lambda: hist.get("/status").json()["covered_range"]["to_t"] == 150)
    direct.close()


@requires_mosquitto
def test_stopping_mqtt_does_not_invalidate_the_historian_recording(hstack):
    sim, hist = hstack.sim, hstack.hist
    sim.post("/api/simulation/run_until", json={"time_s": 60})
    hstack.broker.stop()
    assert wait_for(lambda: not sim.get("/api/uns/status").json()["uns"]["connected"])
    sim.post("/api/simulation/run_until", json={"time_s": 180})                  # recorded while MQTT is down
    scope = hist.get("/status").json()["current_scope"]
    assert hist.get("/status").json()["coverage"] == [{"from_t": 0, "to_t": 180}]
    rows = hist.get("/samples", params={"scope": scope, "entity": "equipment_module:EM-REACTOR",
                                        "variable": "measurement:XMEAS(9)", "start": 0, "end": 181}).json()["rows"]
    assert [r["t"] for r in rows] == list(range(181))
    hstack.broker.start()


@requires_fortran
def test_full_demo_is_unchanged_by_the_historian_and_its_api(demo_run, tmp_path, monkeypatch):
    """Baseline (simulator + UNS) against simulator + UNS + Historian writer + Historian API queried during
    the run: same event log, same final TEP states, the exact same UNS publish calls and counts."""
    import uns.publisher as up
    from historian.api import create_app as create_historian_app
    from historian.writer import HistorianWriter

    from .uns_harness import attach_recording_publisher, normalise
    monkeypatch.setattr(up, "wall_clock", lambda: "2000-01-01T00:00:00.000Z")  # observed_at: the only wall time
    runs = []
    for with_historian in (False, True):
        svc = SimulatorService(start_runner=False)
        try:
            svc.create_simulation("SCN-COOL-001")
            pub, client = attach_recording_publisher(svc)
            queried = []
            if with_historian:
                writer = HistorianWriter(svc, tmp_path / "demo.sqlite").attach()
                api = TestClient(create_historian_app(tmp_path / "demo.sqlite"))

                def query():                                                         # the API reads during the run
                    if svc.engine.clock.time_s % 1800 == 0:
                        queried.append(api.get("/status").json()["covered_range"])
                svc.add_observer(query)
            svc.run_until(10800)
            sent = dict(pub.sent)
            pub.close()
            if with_historian:
                writer.close()
                assert queried and api.get("/status").json()["covered_range"] == {"from_t": 0, "to_t": 10800}
            eng = svc.engine
            runs.append({"log": [(e.event_id, e.type, e.simulation_time, e.payload) for e in eng.bus.log],
                         "states": eng.adapter.get_states().copy(), "calls": normalise(client.calls), "sent": sent})
        finally:
            svc.shutdown()
    base, full = runs
    assert base["log"] == full["log"] == [(e.event_id, e.type, e.simulation_time, e.payload) for e in demo_run.bus.log]
    assert (base["states"] == full["states"]).all() and (full["states"] == demo_run.adapter.get_states()).all()
    assert base["calls"] == full["calls"]                                         # every UNS publish, byte for byte
    assert base["sent"] == full["sent"] == DEMO_MESSAGES


@requires_mosquitto
def test_launcher_with_historian_runs_one_simulation_and_stops_cleanly(tmp_path):
    from historian.reader import HistorianReader
    db = tmp_path / "stack.sqlite"
    ports = {"sim": free_port(), "insp": free_port(), "mqtt": free_port(), "hist": free_port()}
    argv = [sys.executable, "scripts/run_manufacturing_stack.py", "--start-broker", "--no-browser", "--speed", "100",
            "--port", str(ports["sim"]), "--inspector-port", str(ports["insp"]), "--mqtt-port", str(ports["mqtt"]),
            "--historian", str(db), "--historian-port", str(ports["hist"])]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(argv, cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            creationflags=flags, start_new_session=os.name != "nt")
    lines = []
    threading.Thread(target=lambda: [lines.append(l) for l in proc.stdout], daemon=True).start()

    def get(port, path):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return json.loads(r.read())

    def post(port, path):
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    try:
        assert wait_for(lambda: any("Ctrl-C stops the stack" in l for l in lines), timeout=120), "".join(lines)
        assert any("Historian API" in l and f":{ports['hist']}/" in l for l in lines)
        scope = get(ports["sim"], "/api/uns/status")["operational_scope_id"]
        assert get(ports["insp"], "/api/status")["operational_scope_id"] == scope
        assert get(ports["hist"], "/status")["current_scope"] == scope
        post(ports["sim"], "/api/simulation/start")
        assert wait_for(lambda: get(ports["sim"], "/api/simulation")["clock"]["time_s"] >= 100, timeout=60)
        post(ports["sim"], "/api/simulation/pause")
        assert wait_for(lambda: get(ports["hist"], "/status")["covered_range"]["to_t"] >= 100)
        final_t = get(ports["sim"], "/api/simulation")["clock"]["time_s"]
    finally:
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
        proc.wait(90)
    assert any("[stack] stopped" in l for l in lines), "".join(lines)
    assert wait_for(lambda: not any(port_open(p) for p in ports.values()), timeout=20), \
        {k: port_open(p) for k, p in ports.items()}
    r = HistorianReader(db)                                                         # the writer closed gracefully
    assert r.current_scope() == scope and r.coverage(scope)[-1]["to_t"] >= final_t
    r.close()


def test_simulator_server_commits_the_historian_when_stopped_while_running(tmp_path):
    """Ctrl-Break (Windows; SIGTERM elsewhere) stops run.py gracefully even while the simulation runs:
    the process leaves through its cleanup, so the Historian writer commits everything recorded (no
    lifecycle change or periodic commit is relied on)."""
    from historian.reader import HistorianReader
    db, port = tmp_path / "running.sqlite", free_port()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port), "--historian", str(db),
                             "--speed", "50"], cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, creationflags=flags)

    def call(path, body=None):
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    try:
        assert wait_for(lambda: call("/api/simulation")["status"] == "READY", timeout=120)
        call("/api/simulation/start", b"{}")
        assert wait_for(lambda: call("/api/simulation")["clock"]["time_s"] > 90, timeout=60)
        t_seen = call("/api/simulation")["clock"]["time_s"]
    finally:
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        proc.wait(60)
    assert proc.returncode == 0, proc.stdout.read()[-500:]
    r = HistorianReader(db)
    assert r.coverage(r.current_scope())[-1]["to_t"] >= t_seen
    r.close()
