"""The transport-neutral operational projection (projection/operational.py) and the UNS built on it.

The projection is the operational context shared by the UNS and (later) the Historian. These tests fix
its contract directly against the simulator and the canonical context model, and prove that the UNS,
now a consumer of the projection, publishes exactly what it published before the extraction.
"""
import json
import re
import subprocess
import sys

import pytest
import yaml

from api import operational
from api.operational import WITHHELD_EVENT_TYPES, hidden_properties, operational_scope_id
from api.service import SimulatorService
from projection.operational import (ANALYZER_SAMPLE, STEP_STATE, OperationalProjection, StateChangeFilter,
                                    load_context_model)
from simulator.tep import catalog

from .conftest import make_engine, requires_fortran
from .uns_harness import GOLDEN, record, summary

CTX = load_context_model()
CID = re.compile(r"^[a-z][a-z_]*:[^:]+$")
T_FAULT = 3600


def dump(proj):
    """Everything the projection says about the current simulation state, as plain data."""
    ids = proj.entity_ids()
    return {"scope_id": proj.scope_id, "simulation_time": proj.simulation_time(),
            "simulation_timestamp": proj.simulation_timestamp(), "lifecycle": proj.lifecycle(),
            "meta": {c: proj.meta(c) for c in ids}, "measurements": {c: proj.measurements(c) for c in ids},
            "state": {c: proj.entity_state(c) for c in ids},
            "records": [list(r) for r in proj.records()], "events": proj.events_since(0)}


def keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from keys(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from keys(v)


@pytest.fixture(scope="module")
def faulted():
    """SCN-COOL-001 just past the start of its hidden fault (t = 3700 s), with a quality sample."""
    svc = SimulatorService(start_runner=False)
    svc.create_simulation("SCN-COOL-001")
    svc.run_until(T_FAULT + 100)
    proj = OperationalProjection(svc.engine)
    yield svc, proj
    svc.shutdown()


# ---------------------------------------------------------------------------- the UNS is unchanged
@requires_fortran
def test_uns_publication_is_byte_identical_to_the_pre_refactor_golden():
    """Every MQTT publish call (topic, payload bytes, QoS, retain, order) of the scripted scenario in
    tests/uns_harness.py equals the recording made before the projection was extracted."""
    golden = {k: v for k, v in json.loads(GOLDEN.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    assert summary(record()) == golden


def test_projection_has_no_transport_dependency():
    code = ("import sys; sys.path.insert(0, '.'); import projection.operational; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('paho', 'uns', 'sqlite3', 'fastapi')); "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------- entities and ISA-95
def test_entity_metadata_is_canonical_and_isa95_placed(faulted):
    _, proj = faulted
    types = CTX["entity_types"]
    ids = proj.entity_ids()
    assert ids and len(ids) == len(set(ids))
    for cid in ids:
        etype, native = cid.split(":", 1)
        assert CID.match(cid) and etype in types
        meta = proj.meta(cid)
        assert meta["native_id"] == native
        assert meta["isa95"]["mapping_id"] == types[etype]["isa95"]
        assert meta["parent"] == proj.placement.parent(cid)
        path = proj.placement.path(cid)
        assert path[-1] == cid and path[0] == "enterprise:ENT-ACME"
        if "children" in meta:
            assert all(proj.placement.parent(c) == cid for c in meta["children"])
    used = {p.split(":", 1)[0] for c in ids for p in proj.placement.path(c)}
    assert not used & {"unit", "process_cell", "production_line", "work_cell"}      # no fabricated levels


def test_isa95_placement_is_the_simulator_hierarchy(faulted):
    svc, proj = faulted
    eng, pl = svc.engine, proj.placement
    level_type = {d["source"]["hierarchy_level"]: t for t, d in CTX["entity_types"].items()
                  if "hierarchy_level" in d["source"]}
    for el in eng.hierarchy.elements.values():
        cid = f"{level_type[el.level.value]}:{el.id}"
        parent = eng.hierarchy.get(el.parent_id) if el.parent_id else None
        assert pl.parent(cid) == (f"{level_type[parent.level.value]}:{parent.id}" if parent else None)
    assert pl.parent("utility:UT-CW-REACTOR") == "work_center:WC-CW"
    assert pl.parent("material:MAT-GH") == pl.parent("product:PROD-GH-M1") == pl.site


# ---------------------------------------------------------------------------- measurements
def test_measurements_are_the_transmitted_tep_variables_with_units(faulted):
    svc, proj = faulted
    p = svc.engine.state.process
    ms = {(c, v): f for c in proj.entity_ids() for v, f in proj.measurements(c)}
    xmeas = {v: f for (_, v), f in ms.items() if v.startswith("measurement:XMEAS(")}
    xmv = {v: f for (_, v), f in ms.items() if v.startswith("manipulated_variable:XMV(")}
    assert len(xmeas) == 41 and len(xmv) == 12
    assert sum(v == "setpoint" for _, v in ms) == sum(v == "controller_output" for _, v in ms) == 19
    for v, f in xmeas.items():
        i = int(v[len("measurement:XMEAS("):-1])
        assert f["value"] == float(p.xmeas[i - 1])                  # transmitted, never xmeas_true
        assert f["unit"] == catalog.XMEAS[i - 1].unit and f["quality"] in ("GOOD", "BAD")
    assert all(f["source"] == "simulator" and "unit" in f and f["variable"] == v for (_, v), f in ms.items())


def test_measurement_semantics_follow_the_catalog():
    for i, var in enumerate(catalog.XMEAS, start=1):
        expected = ANALYZER_SAMPLE if var.measurement_type == catalog.MeasurementType.SAMPLED else STEP_STATE
        assert OperationalProjection.measurement_semantics(f"measurement:XMEAS({i})") == expected
    assert {i for i in range(1, 42)
            if OperationalProjection.measurement_semantics(f"measurement:XMEAS({i})") == ANALYZER_SAMPLE} \
        == set(range(23, 42))
    for v in ("manipulated_variable:XMV(10)", "setpoint", "controller_output", "vibration", "rate_kg_h"):
        assert OperationalProjection.measurement_semantics(v) == STEP_STATE


# ---------------------------------------------------------------------------- state and records
def test_entity_state_is_operational_state(faulted):
    svc, proj = faulted
    st = svc.engine.state
    for cid in proj.entity_ids():
        s = proj.entity_state(cid)
        if s is None:
            continue
        native = cid.split(":", 1)[1]
        props = operational.properties(st.entity(native))
        for k, v in s["state"].items():
            if k in props:
                assert v == props[k], (cid, k)                      # DEGRADED already reported as RUNNING
    pump = proj.entity_state("work_unit:WU-CWP-101A")["state"]
    assert pump["status"] == operational.status_value(st.entity("WU-CWP-101A"), st.entity("WU-CWP-101A").properties["status"])
    line = proj.entity_state("production_unit:PU-STRIPPER")["state"]
    assert {"line_state", "current_lot", "current_order"} <= set(line)


def test_records_are_the_operational_collections_placed_by_relationship(faulted):
    _, proj = faulted
    observable = {d["source"]["collection"] for d in CTX["entity_types"].values()
                  if "collection" in d["source"] and d["observability"] == "operational"}
    recs = list(proj.records())
    assert {c for c, _, _ in recs} <= observable
    assert not {c for c, _, _ in recs} & {"faults", "operator_actions"}
    byid = {cid: (coll, fields) for coll, cid, fields in recs}
    assert "quality_sample:QS-00001" in byid
    assert proj.placement.parent("quality_sample:QS-00001") == "control_module:CM-AT-PRODUCT"
    assert all("value" not in f["state"] for c, f in byid.values() if c == "alarms")   # alarm value is a measurement
    assert all(CID.match(cid) and fields.keys() == {"state"} for cid, (_, fields) in byid.items())


# ---------------------------------------------------------------------------- events, lifecycle, time, scope
def test_operational_events_in_stream_order_with_their_simulation_time(faulted):
    svc, proj = faulted
    events = proj.events_since(0)
    expected = svc.get_events(limit=50000)
    assert [e["event_id"] for e in events] == [e["event_id"] for e in expected]
    assert [e["simulation_time"] for e in events] == [e["simulation_time"] for e in expected]
    assert all(re.fullmatch(r"(OE|LC)-\d{7}", e["event_id"]) for e in events)
    assert not {e["event_type"] for e in events} & {t.value for t in WITHHELD_EVENT_TYPES}
    assert all(CID.match(e["entity_id"]) for e in events)
    assert proj.event_count() == len(events) and proj.events_since(len(events)) == []
    assert events[0]["event_type"] == "SIMULATION_STARTED" and events[0]["payload"] == {"duration_seconds": 10800}


def test_lifecycle_and_simulation_time():
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        proj = OperationalProjection(svc.engine)
        assert proj.lifecycle()["status"] == "READY" and proj.simulation_time() == 0
        svc.start_simulation()
        assert proj.lifecycle()["status"] == "RUNNING"
        svc.pause_simulation()
        assert proj.lifecycle()["status"] == "PAUSED"
        svc.run_until(30)
        life = proj.lifecycle()
        assert proj.simulation_time() == svc.engine.clock.time_s == 30
        assert proj.simulation_timestamp() == svc.engine.clock.timestamp()
        assert life["duration_seconds"] == 10800 and life["simulation_start"].startswith("2026-01-05T06:00:00")
        types = [e["event_type"] for e in proj.events_since(0)]
        assert types[:2] == ["SIMULATION_STARTED", "SIMULATION_PAUSED"]
        svc.reset_simulation()
        new = OperationalProjection(svc.engine)
        assert new.scope_id != proj.scope_id and new.events_since(0)[0]["event_type"] == "SIMULATION_RESET"
    finally:
        svc.shutdown()


def test_scope_id_is_the_operational_scope(faulted):
    svc, proj = faulted
    assert proj.scope_id == operational_scope_id(svc.engine)
    assert re.fullmatch(r"OS-[0-9a-f]{32}", proj.scope_id)


# ---------------------------------------------------------------------------- the boundary
def test_projection_contains_no_evaluator_only_information(faulted):
    """After the hidden fault has started: no hidden property of any entity, no fault or run identity,
    no truth values; checked against the boundary's own definitions, not a list of names."""
    svc, proj = faulted
    st, e = svc.engine.state, svc.engine
    d = dump(proj)
    for cid, s in d["state"].items():
        native = cid.split(":", 1)[1]
        if s is not None and st.has_entity(native):
            assert not set(s["state"]) & hidden_properties(st.entity(native)), cid
    for cid, ms in d["measurements"].items():
        native = cid.split(":", 1)[1]
        if st.has_entity(native):
            assert not {v for v, _ in ms} & hidden_properties(st.entity(native)), cid
    text = json.dumps(d, default=str)
    run = e.state.run
    for secret in [run["run_id"], e.scenario["id"], e.config_hash] + [f.get("id") for f in e.scenario.get("faults", [])]:
        assert secret and secret not in text, secret
    assert not set(keys(d)) & {"seed", "run_id", "scenario_id", "configuration_hash", "idv", "xmeas_true",
                               "boundary", "faults", "health"}
    assert "EV-" not in text


def test_projection_contains_no_wall_clock_time(faulted):
    _, proj = faulted
    d = dump(proj)
    assert not set(keys(d)) & {"observed_at", "published_at", "received_at", "wall_time", "ingested_at"}
    iso_times = set(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", json.dumps(d, default=str)))
    assert all(t.startswith("2026-01-05") for t in iso_times), iso_times     # the simulated calendar only


# ---------------------------------------------------------------------------- determinism and change rule
def test_projection_is_deterministic_for_the_same_simulation():
    dumps = []
    for _ in range(2):
        e = make_engine()
        e.run_until(400)
        d = dump(OperationalProjection(e))
        d.pop("scope_id")                                           # random per scope, by design
        dumps.append(json.dumps(d, sort_keys=True, default=str))
    assert dumps[0] == dumps[1]


def test_state_change_filter_reports_discrete_changes_at_once_and_continuous_at_ticks():
    f = StateChangeFilter()
    rec = {"state": {"status": "OPEN", "quantity_kg": 10.0}}
    assert f.report("k", rec, tick=False, continuous="material_lots")                       # first observation
    assert not f.report("k", rec, tick=False, continuous="material_lots")                   # unchanged
    assert not f.report("k", {"state": {"status": "OPEN", "quantity_kg": 9.0}}, tick=False,
                        continuous="material_lots")                                          # continuous only: wait
    assert f.report("k", {"state": {"status": "OPEN", "quantity_kg": 9.0}}, tick=True,
                    continuous="material_lots")                                              # reported at the tick
    assert f.report("k", {"state": {"status": "USED", "quantity_kg": 8.0}}, tick=False,
                    continuous="material_lots")                                              # discrete: at once
    e = StateChangeFilter()
    assert e.report("e", {"state": {"x": 1.5}}, tick=False, continuous=False)
    assert e.report("e", {"state": {"x": 1.6}}, tick=False, continuous=False)               # entity: any change
    b = StateChangeFilter()                                                                 # continuing a report
    assert not b.report("r", rec, tick=False, continuous="material_lots", baseline=rec)
    assert b.known("r")


def test_context_model_file_is_the_one_the_projection_reads():
    assert load_context_model() == yaml.safe_load(open("contract/context_model.yaml", encoding="utf-8"))
