"""Validation 13: the API and UI reflect simulator state; benchmark functions are separated."""
import io
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.service import SimulatorService


@pytest.fixture
def client():
    svc = SimulatorService(start_runner=False)
    app = create_app(svc, autoload="SCN-COOL-001")
    c = TestClient(app)
    c.svc = svc
    yield c
    svc.shutdown()


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ISA-95 Hierarchy" in r.text and "Fault Injection" in r.text
    for asset in ("/ui/js/app.js", "/ui/js/schematic.js", "/ui/js/charts.js", "/ui/js/panels.js", "/ui/css/app.css"):
        assert client.get(asset).status_code == 200, asset


def test_snapshot_reflects_engine_state(client):
    client.post("/api/simulation/step", json={"n": 900})
    snap = client.get("/api/benchmark/ui/snapshot").json()
    e = client.svc.engine
    assert snap["simulation"]["clock"]["time_s"] == e.clock.time_s == 900
    assert np.allclose(snap["process"]["xmeas"], e.state.process.xmeas)
    assert np.allclose(snap["process"]["xmv"], e.state.process.xmv)
    assert snap["hierarchy"]["id"] == "ENT-ACME"
    assert snap["utilities"]["UT-CW-REACTOR"]["properties"]["status"] == e.state.get("UT-CW-REACTOR", "status")
    eq = client.get("/api/equipment/EM-REACTOR").json()
    temp = next(v for v in eq["variables"] if v["id"] == "XMEAS(9)")
    assert temp["value"]["value"] == pytest.approx(float(e.state.process.xmeas[8]))
    prop = client.get("/api/entities/EM-REACTOR/properties/temperature").json()
    assert prop["value"] == pytest.approx(float(e.state.process.xmeas[8])) and prop["unit"] == "degC"
    h = client.get("/api/history", params={"series": "XMEAS(9)|XMV(10)"}).json()
    assert h["time"][-1] == 900 and len(h["series"]["XMV(10)"]) == len(h["time"])


def test_required_operations_exist(client):
    for path in ("/api/simulation", "/api/enterprise", "/api/site", "/api/areas", "/api/equipment",
                 "/api/process/measurements", "/api/process/setpoints", "/api/process/manipulated-variables",
                 "/api/alarms", "/api/events", "/api/maintenance", "/api/inventory", "/api/quality",
                 "/api/production-orders", "/api/utilities", "/api/benchmark/coupling", "/api/benchmark/scenarios"):
        assert client.get(path).status_code == 200, path
    areas = client.get("/api/areas").json()
    assert {a["id"] for a in areas} >= {"AREA-REACTION", "AREA-UTILITIES"}


def test_simulation_controls(client):
    assert client.post("/api/simulation/speed", json={"speed": 100}).json()["speed"] == 100
    assert client.post("/api/simulation/speed", json={"speed": 1e9}).status_code == 400
    client.post("/api/simulation/run_until", json={"time_s": 120})
    assert client.get("/api/simulation").json()["clock"]["time_s"] == 120
    client.post("/api/simulation/reset", json={"duration_seconds": 3600})
    s = client.get("/api/simulation").json()
    assert s["clock"]["time_s"] == 0 and s["duration_s"] == 3600


def test_operator_actions_and_validation(client):
    r = client.post("/api/operator/set_setpoint", json={"loop_id": 18, "value": 121.0})
    assert r.status_code == 200
    assert client.get("/api/process/setpoints").json()[13]["setpoint"] == 121.0   # TC-RX (order 14th)
    r = client.post("/api/operator/set_setpoint", json={"loop_id": 10, "value": 90.0})
    assert r.status_code == 400 and "cascade" in r.json()["error"]
    assert client.post("/api/operator/no_such_action", json={}).status_code == 400
    assert client.get("/api/equipment/NOPE").status_code == 404
    evs = client.get("/api/events", params={"types": "OPERATOR_ACTION,SETPOINT_CHANGED"}).json()
    assert {e["type"] for e in evs} == {"OPERATOR_ACTION", "SETPOINT_CHANGED"}


def test_fault_injection_is_benchmark_only(client):
    spec = {"id": "F-API", "type": "sensor_dropout", "target": "XMEAS(12)"}
    assert client.post("/api/benchmark/faults", json=spec).status_code == 200
    assert client.post("/api/benchmark/faults/F-API/start").json()["status"] == "ACTIVE"
    client.post("/api/simulation/step", json={"n": 10})
    # operational interfaces never show fault ground truth
    evs = client.get("/api/events", params={"limit": 5000}).json()
    assert not any(e["type"].startswith("FAULT_") for e in evs)
    assert all(not p.startswith("/api/fault") for p in client.app.openapi()["paths"])
    gt = client.get("/api/benchmark/ground-truth").json()
    assert any(e["type"] == "FAULT_STARTED" for e in gt["events"])
    # consequence visible operationally: BAD transmitter + alarm
    m = client.get("/api/process/measurements").json()
    assert m[11]["quality"] == "BAD"
    assert client.post("/api/benchmark/faults/F-API/stop").json()["status"] == "STOPPED"
    assert client.post("/api/benchmark/faults/F-API/reset").json()["status"] == "RESET"


def test_export(client):
    client.post("/api/simulation/step", json={"n": 1200})
    j = client.get("/api/benchmark/export/json").json()
    for k in ("run_manifest", "events", "measurements", "manipulated_variables", "setpoints", "equipment_states",
              "alarms", "faults", "maintenance", "inventory", "quality", "production_orders"):
        assert k in j, k
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/benchmark/export/csv").content))
    names = set(z.namelist())
    assert {"run_manifest.json", "events.csv", "measurements.csv", "manipulated_variables.csv", "setpoints.csv",
            "equipment_states.csv", "alarms.csv", "faults.csv", "work_orders.csv", "production_orders.csv",
            "quality_samples.csv"} <= names
    header = z.read("measurements.csv").decode().splitlines()[0]
    assert header.startswith("time_s,XMEAS(1)")


def test_scenario_save_duplicate(client, tmp_path):
    from simulator.scenarios import ScenarioStore
    client.svc.store = ScenarioStore(tmp_path)
    sc = client.get("/api/benchmark/scenarios/current").json()
    sc["id"] = "SCN-X"
    assert client.post("/api/benchmark/scenarios", json=sc).status_code == 200
    assert client.post("/api/benchmark/scenarios/SCN-X/duplicate", json={"new_id": "SCN-Y"}).status_code == 200
    ids = {s["id"] for s in client.get("/api/benchmark/scenarios").json()}
    assert {"SCN-X", "SCN-Y"} <= ids
    assert client.post("/api/benchmark/scenarios", json=sc).status_code == 400    # no silent overwrite
