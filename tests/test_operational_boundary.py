"""The operational information boundary (api/operational.py; docs/CANONICAL_SIMULATOR_INVARIANTS.md I13).

Operational routes (/api/* outside /api/benchmark) must not let a consumer recover the hidden cause of
the SCN-COOL-001 fault, while the evaluator routes (/api/benchmark/*) must still carry it.
"""
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.operational import OperationalEventView, hidden_properties
from api.service import SimulatorService
from simulator.scenarios import ScenarioStore
from simulator.simulation.engine import SimulationEngine

from .conftest import base_config, requires_fortran

pytestmark = requires_fortran

T_DEGRADED = 4500          # 01:15:00 - P-101A DEGRADED internally, UT-CW-REACTOR DEGRADED, no symptom alarm yet
T_FIRST_SYMPTOM = 4833     # 01:20:33 - vibration alarm VAH-CWP101A, the first observable event
FORBIDDEN_KEYS = {"seed", "tep_seed", "run_id", "scenario_id", "configuration_hash", "xmeas_true", "boundary",
                  "boundary_nominal", "health_before", "health_after", "cleared_fault_effects", "faults",
                  "active_faults", "expected_effects"}
HIDDEN_STATUS_VALUES = {"DEGRADED", "CONSTRAINED"}


def _operational_get_routes(app):
    return sorted(r.path for r in app.routes if r.path.startswith("/api/") and not r.path.startswith("/api/benchmark")
                  and "GET" in (getattr(r, "methods", None) or set()))


def _crawl(client):
    """GET every operational route, expanding path parameters over every entity and visible property."""
    svc = client.svc
    ids = sorted(svc.engine.state.entities)
    out = {}
    for path in _operational_get_routes(client.app):
        if "{entity_id}" not in path:
            params = {"series": "XMEAS(9)|XMV(10)"} if path == "/api/history" else None
            r = client.get(path, params=params)
            assert r.status_code == 200, path
            out[path] = r.json()
            continue
        for eid in ids:
            if path.endswith("{prop}"):
                for prop in client.get(f"/api/entities/{eid}").json()["properties"]:
                    out[f"/api/entities/{eid}/properties/{prop}"] = client.get(
                        f"/api/entities/{eid}/properties/{prop}").json()
            else:
                r = client.get(path.replace("{entity_id}", eid))
                if r.status_code == 200:
                    out[path.replace("{entity_id}", eid)] = r.json()
    return out


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path, k, v
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


@pytest.fixture(scope="module")
def demo():
    """SCN-COOL-001 through the API: responses captured at 01:15 (hidden degradation, no symptom yet) and
    at the end of the 3 h run."""
    svc = SimulatorService(start_runner=False)
    client = TestClient(create_app(svc, autoload="SCN-COOL-001"))
    client.svc = svc
    svc.step_simulation(T_DEGRADED)
    early = {"op": _crawl(client),
             "truth_asset": client.get("/api/benchmark/entities/WU-CWP-101A").json(),
             "truth_utility": client.get("/api/benchmark/utilities").json()["UT-CW-REACTOR"],
             "truth_image": client.get("/api/benchmark/process/image").json()}
    svc.run_until(10800)
    final = {"op": _crawl(client)}
    faults = list(svc.engine.state.collection("faults").values())
    secrets = {f.id for f in faults} | {f.type for f in faults} | {
        svc.engine.scenario["id"], svc.engine.scenario["name"], svc.engine.state.run["run_id"],
        svc.engine.config_hash[:10]}
    yield {"client": client, "early": early, "final": final, "secrets": secrets}
    svc.shutdown()


def test_operational_routes_do_not_reveal_fault_or_scenario_identity(demo):
    for stage in ("early", "final"):
        text = json.dumps(demo[stage]["op"])
        for secret in demo["secrets"]:
            assert secret not in text, (stage, secret)
        assert "hidden cause" not in text                      # a phrase of the scenario description
        for route, body in demo[stage]["op"].items():
            for path, key, value in _walk(body):
                assert key not in FORBIDDEN_KEYS, (stage, route, path, key)
                assert not (isinstance(value, str) and value in HIDDEN_STATUS_VALUES), (stage, route, path, key)


def test_model_internal_properties_are_hidden_and_indistinguishable_from_missing(demo):
    client = demo["client"]
    st = client.svc.engine.state
    for eid, rec in st.entities.items():
        hidden = hidden_properties(rec)
        visible = client.get(f"/api/entities/{eid}").json()["properties"]
        assert not hidden & set(visible), eid
        for prop in hidden & set(rec.properties):
            r = client.get(f"/api/entities/{eid}/properties/{prop}")
            missing = client.get(f"/api/entities/{eid}/properties/no_such_property")
            assert r.status_code == missing.status_code == 404, (eid, prop)
            assert r.json()["error"].replace(prop, "X") == missing.json()["error"].replace("no_such_property", "X")
    maint = demo["final"]["op"]["/api/maintenance"]
    assert all("health" not in a and "efficiency" not in a for a in maint["assets"])
    inv = demo["final"]["op"]["/api/inventory"]
    assert not any(k.endswith("_deviation") or k.startswith("feed_") for s in inv["storage"] for k in s)


def test_hidden_degradation_is_not_reported_as_status(demo):
    early = demo["early"]
    assert early["truth_asset"]["properties"]["status"] == "DEGRADED"           # canonical state is intact
    assert early["truth_utility"]["properties"]["status"] == "DEGRADED"
    assert early["op"]["/api/entities/WU-CWP-101A"]["properties"]["status"] == "RUNNING"
    assert "status" not in early["op"]["/api/utilities"]["UT-CW-REACTOR"]["properties"]
    types = {e["type"] for e in early["op"]["/api/events"]}
    assert not types & {"UTILITY_STATE_CHANGED", "EQUIPMENT_DEGRADED"}
    assert not any(e["type"] == "EQUIPMENT_STATE_CHANGED" and e["target"] == "WU-CWP-101A"
                   for e in early["op"]["/api/events"])


def test_operational_event_stream_is_self_contained_and_gap_free(demo):
    client = demo["client"]
    evs = client.get("/api/events", params={"limit": 20000}).json()
    sim = [e for e in evs if e["event_id"].startswith("OE-")]
    assert [int(e["event_id"][3:]) for e in sim] == list(range(1, len(sim) + 1))
    ids = {e["event_id"] for e in evs}
    for e in evs:
        assert not e["type"].startswith("FAULT_") and e["type"] not in ("UTILITY_STATE_CHANGED", "EQUIPMENT_DEGRADED")
        assert e["correlation_id"] in ids, e
        assert e["causation_id"] is None or e["causation_id"] in ids, e
        if e["type"] == "EQUIPMENT_STATE_CHANGED":
            assert e["payload"]["old"] != e["payload"]["new"] and "health" not in e["payload"]
        if e["type"] == "EQUIPMENT_REPAIRED":
            assert set(e["payload"]) == {"work_order"}
    # operational causation is kept: the work order links to the vibration alarm that raised it
    req = next(e for e in evs if e["type"] == "MAINTENANCE_REQUESTED" and e["target"] == "WU-CWP-101A")
    alarm = next(e for e in evs if e["event_id"] == req["causation_id"])
    assert alarm["type"] == "ALARM_ACTIVATED" and alarm["payload"]["alarm_id"] == "VAH-CWP101A"
    assert req["correlation_id"] == alarm["event_id"] == alarm["correlation_id"]


def test_operational_stream_matches_fault_free_run_until_the_first_symptom():
    """Before the first observable symptom, nothing in the operational event stream (ids, types, targets,
    times, correlation) distinguishes the faulted demo from the same scenario without the fault."""
    sc = ScenarioStore().load("SCN-COOL-001")
    healthy = copy.deepcopy(sc)
    healthy["faults"] = []
    streams = []
    for scenario in (sc, healthy):
        e = SimulationEngine(scenario, base_config())
        e.run_until(T_FIRST_SYMPTOM - 1)
        streams.append([(r["event_id"], r["type"], r["target"], r["simulation_time"], r["correlation_id"],
                         r["causation_id"]) for r in OperationalEventView(e.bus).query()])
    assert streams[0] == streams[1]


def test_manifest_and_simulation_state_carry_no_scenario_identity(demo):
    client = demo["client"]
    m = client.get("/api/simulation/manifest").json()
    assert set(m) <= {"simulation_start", "duration_seconds", "control_mode", "simulator_version", "tep_backend",
                      "tep_version", "simulated_seconds", "simulation_end", "status", "process_shutdown",
                      "process_shutdown_reason"}
    assert "tep_seed" not in m["tep_version"]
    s = client.get("/api/simulation").json()
    assert "scenario" not in s and "run_id" not in s["manifest"]


def test_history_and_process_image_exclude_truth(demo):
    client = demo["client"]
    names = {c["name"] for c in client.get("/api/history/catalog").json()}
    assert "XMEAS(9)" in names and not any(n.startswith("TRUE:") for n in names)
    assert not {"WU-CWP-101A.health", "UT-CW-REACTOR.capacity_fraction", "UT-CW-REACTOR.availability"} & names
    for series in ("TRUE:XMEAS(9)", "WU-CWP-101A.health", "UT-CW-REACTOR.available_capacity"):
        assert client.get("/api/history", params={"series": series}).status_code == 404, series
    image = client.get("/api/process/image").json()
    assert not {"boundary", "boundary_nominal", "xmeas_true", "idv"} & set(image)


def test_truth_routes_are_not_operational(demo):
    client = demo["client"]
    for path in ("/api/coupling", "/api/process/internal-states", "/api/scenarios", "/api/scenarios/current",
                 "/api/export/json", "/api/export/csv", "/api/ui/snapshot"):
        assert client.get(path).status_code in (404, 405), path


def test_evaluator_routes_retain_the_true_cause(demo):
    client = demo["client"]
    m = client.get("/api/benchmark/manifest").json()
    assert m["scenario_id"] == "SCN-COOL-001" and any(f["id"] == "F-COOL-001" for f in m["faults"])
    assert client.get("/api/benchmark/simulation").json()["scenario"]["id"] == "SCN-COOL-001"
    evs = client.get("/api/benchmark/events", params={"limit": 20000, "include_benchmark": "true"}).json()
    assert any(e["type"] == "FAULT_STARTED" and e["correlation_id"] == "F-COOL-001" for e in evs)
    util = next(e for e in evs if e["type"] == "UTILITY_STATE_CHANGED")
    assert util["correlation_id"] == "F-COOL-001" and util["operational_id"] is None
    vah = next(e for e in evs if e["type"] == "ALARM_ACTIVATED" and e["payload"]["alarm_id"] == "VAH-CWP101A")
    assert vah["correlation_id"] == "F-COOL-001" and vah["operational_id"].startswith("OE-")
    early = demo["early"]
    assert early["truth_asset"]["properties"]["health"] < 0.75
    assert early["truth_image"]["boundary"]["reactor_cw_max_flow"] < 1000.0
    gt = client.get("/api/benchmark/ground-truth").json()
    assert any(e["type"] == "FAULT_STARTED" for e in gt["events"])


def test_cooling_water_utilization_equals_valve_position():
    """Why CW utilization stays operational: flow / min(available, capacity) equals XMV/100 exactly."""
    e = SimulationEngine(ScenarioStore().load("SCN-COOL-001"), base_config(), duration_override=6000)
    e.run_until(3600)
    for _ in range(6):            # 01:05 .. 01:30, through the loss of capacity and valve saturation
        e.step(299)
        xmv = float(e.state.process.xmv[9])       # the value the flow relation reads in the next pre-step
        e.step(1)
        assert e.state.get("UT-CW-REACTOR", "utilization") == pytest.approx(min(xmv / 100.0, 1.5), rel=1e-9)


def test_alarm_sources_are_operational(demo):
    """Alarms are operational records, so no alarm may be defined on a hidden property."""
    st = demo["client"].svc.engine.state
    for a in st.collection("alarms").values():
        m = re.fullmatch(r"([A-Z0-9-]+)\.(\w+)", a.definition.source)
        if m and m.group(1) in st.entities:
            assert m.group(2) not in hidden_properties(st.entities[m.group(1)]), a.alarm_id
