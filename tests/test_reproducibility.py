"""Validation 14-15 plus native-fidelity and the demonstration scenario."""
import json

import numpy as np
import pytest

from simulator.common import to_jsonable
from simulator.events import EventType
from simulator.scenarios import ScenarioStore
from simulator.simulation.engine import SimulationEngine
from simulator.tep import create_adapter
from tests.conftest import base_config, make_engine, requires_fortran


def trace(e):
    return [json.dumps(ev.to_dict(), sort_keys=True) for ev in e.bus.log]


def test_same_scenario_same_seed_identical_results():
    sc = ScenarioStore().load("SCN-COOL-001")
    runs = []
    for _ in range(2):
        e = SimulationEngine(sc, base_config(), duration_override=7200)
        e.run()
        t, series = e.history.to_columns()
        runs.append((trace(e), series, e.run_manifest()))
    assert runs[0][0] == runs[1][0]                                     # identical event sequence
    for k in runs[0][1]:
        assert runs[0][1][k] == runs[1][1][k], k                         # identical trajectories
    assert runs[0][2]["run_id"] == runs[1][2]["run_id"]
    assert runs[0][2]["configuration_hash"] == runs[1][2]["configuration_hash"]


def test_different_seed_gives_different_trajectory():
    a, b = make_engine(seed=1), make_engine(seed=2)
    a.run_until(900)
    b.run_until(900)
    assert not np.array_equal(a.state.process.xmeas_true, b.state.process.xmeas_true)
    assert a.manifest()["configuration_hash"] != b.manifest()["configuration_hash"]


@requires_fortran
def test_healthy_enterprise_reproduces_native_tep_exactly():
    """With no faults the enterprise layer must not perturb TEP: the trajectory equals a bare
    native run (TEINIT + native controllers) with the same TEP seed, bit for bit."""
    e = make_engine(seed=3, duration_seconds=3600, production_orders=[])
    raw = create_adapter("fortran")
    raw.initialize(tep_seed=e.tep_seed)
    for _ in range(3600):
        e.step(1)
        raw.step(1)
        assert np.array_equal(e.state.process.xmeas_true, raw.get_measurements())
    assert np.array_equal(e.adapter.get_states(), raw.get_states())


def test_reset_returns_to_initial_state():
    from api.service import SimulatorService
    svc = SimulatorService(start_runner=False)
    svc.create_simulation("SCN-BASELINE")
    fresh = to_jsonable(svc.engine.snapshot())
    svc.step_simulation(1800)
    svc.operator_action("set_setpoint", {"loop_id": 18, "value": 121.0})
    assert svc.engine.clock.time_s == 1800
    svc.reset_simulation()
    after = to_jsonable(svc.engine.snapshot())
    assert after == fresh
    assert svc.engine.history.size == 1                                    # trend buffer cleared (t=0 sample)
    assert [e.type for e in svc.engine.bus.log] == [EventType.SIMULATION_RESET]
    svc.shutdown()


def test_manifest_contents(engine):
    m = engine.run_manifest()
    for k in ("run_id", "scenario_id", "seed", "tep_version", "simulator_version", "configuration_hash",
              "simulation_start", "duration_seconds", "active_faults", "tep_seed"):
        assert k in m
    if m["tep_backend"] == "fortran":
        assert m["tep_version"]["sources"]["teprob.f"] and m["tep_version"]["library_sha256"]


# ------------------------------------------------------------------ demonstration scenario (3 h)
def test_demo_scenario_causal_chain(demo_run):
    e = demo_run
    st = e.state
    log = e.bus.log
    first = {}
    for ev in log:
        first.setdefault((ev.type, ev.target, ev.payload.get("alarm_id")), ev)

    def t(etype, target=None, alarm=None):
        ev = first.get((etype, target, alarm))
        assert ev is not None, (etype, target, alarm)
        return ev

    f = t(EventType.FAULT_STARTED, "WU-CWP-101A")
    assert f.simulation_time == 3600
    degr = t(EventType.EQUIPMENT_DEGRADED, "WU-CWP-101A")
    ut = next(ev for ev in log if ev.type == EventType.UTILITY_STATE_CHANGED and ev.payload["new"] == "CONSTRAINED")
    sat = t(EventType.ALARM_ACTIVATED, "CM-TIC-RCW", "OUTH-L10")
    temp = t(EventType.ALARM_ACTIVATED, "EM-REACTOR", "TAH-09")
    req = t(EventType.MAINTENANCE_REQUESTED, "WU-CWP-101A")
    start = t(EventType.MAINTENANCE_STARTED, "WU-CWP-101A")
    assert f.simulation_time < degr.simulation_time < ut.simulation_time <= sat.simulation_time < temp.simulation_time
    assert req.simulation_time < start.simulation_time
    # every consequence carries the fault as correlation id (ground truth), none was set directly
    for ev in (degr, ut, sat, temp, req, start):
        assert ev.correlation_id == "F-COOL-001", ev.type
    # the process responded through physics: valve saturated, reactor heated, no trip
    t_, s = e.history.to_columns()
    t_ = np.array(t_)
    T9 = np.array(s["TRUE:XMEAS(9)"], dtype=float)
    X10 = np.array(s["XMV(10)"], dtype=float)
    assert T9[t_ < 3600].max() < 121.0
    assert T9.max() > 130.0
    assert X10.max() >= 99.9
    assert not st.process.shutdown
    # recovery: standby pump running, utility normal, P-101A repaired
    assert st.get("WU-CWP-101B", "status") == "RUNNING"
    assert st.get("UT-CW-REACTOR", "status") == "NORMAL"
    wo = [w for w in st.records("work_orders") if w.asset_id == "WU-CWP-101A"][0]
    assert wo.status == "COMPLETED"
    assert st.get("WU-CWP-101A", "health") > 0.9
    # quality impact
    assert any(s.overall == "FAIL" for s in st.records("quality_samples"))
    assert any(l.status in ("QUARANTINE", "REJECTED") for l in st.records("production_lots"))


@pytest.mark.parametrize("sid", [s["id"] for s in ScenarioStore().list()])
def test_every_library_scenario_loads_and_runs(sid):
    sc = ScenarioStore().load(sid)
    e = SimulationEngine(sc, base_config(), duration_override=120)
    e.run()
    assert e.completed and e.clock.time_s == 120


def test_every_library_fault_can_start_and_stop():
    sc = ScenarioStore().load("SCN-FAULT-LIBRARY")
    e = SimulationEngine(sc, base_config(), duration_override=7200)
    e.run_until(10)
    for f in list(e.faults.faults.values()):
        if f.status == "CREATED" and f.trigger_mode == "manual":
            e.faults.start_fault(f.id)
    e.run_until(1200)
    for f in list(e.faults.faults.values()):
        if f.status == "ACTIVE":
            e.faults.stop_fault(f.id)
    e.run_until(1300)
    assert not any(f.status == "ACTIVE" for f in e.faults.faults.values())


def test_python_development_backend_runs_full_engine():
    e = make_engine(backend="python", duration_seconds=600)
    e.run()
    assert e.manifest()["tep_backend"] == "python"
    assert e.state.process.xmeas_true[8] == pytest.approx(120.4, abs=0.5)
    assert not e.state.process.shutdown
