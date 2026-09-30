"""The Historian (historian/): deterministic, operational-only history of the operational projection.

The Historian is a peer of the UNS: both observe the same SimulatorService through their own
OperationalProjection. These tests record real simulations (no MQTT unless a test is about MQTT) and
read them back through HistorianReader and plain sqlite3.
"""
import bisect
import json
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

from api.operational import hidden_properties, operational_scope_id
from api.service import SimulatorService
from historian import SCHEMA, HistorianIntegrityError, HistorianSchemaError
from historian.reader import MAX_ROWS, MAX_SPAN_S, HistorianAccessError, HistorianReader
from historian.writer import ANALYZER_VARIABLES, HistorianStore, HistorianWriter, analyzer_due
from projection.operational import OperationalProjection, load_context_model
from uns.broker import LocalBroker, find_mosquitto

from .conftest import requires_fortran
from .uns_harness import GOLDEN, attach_recording_publisher, record, summary

requires_mosquitto = pytest.mark.skipif(find_mosquitto() is None, reason="Eclipse Mosquitto not installed")
CTX = load_context_model()
CID = re.compile(r"^[a-z][a-z_]*:[^:]+$")
T_END = 6400                     # past the hidden fault (3600), the first symptom (4833) and maintenance (6333)
DEMO_MESSAGES = {"meta": 101, "state": 7190, "measurement": 188090, "event": 139, "lifecycle": 4, "delete": 0}


def dump(path, mask_scope=True):
    """Every row of every table, deterministically ordered; scope ids masked (random by design)."""
    db = sqlite3.connect(path)
    out, names = {}, {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        rows = db.execute(f"SELECT * FROM {table}").fetchall()
        out[table] = sorted(json.dumps(r, default=str) for r in rows)
    db.close()
    text = json.dumps(out, sort_keys=True)
    if mask_scope:
        text = re.sub(r"OS-[0-9a-f]{32}", lambda m: names.setdefault(m.group(0), f"<scope-{len(names) + 1}>"), text)
    return text


def run(svc, until):
    svc.run_until(until)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    """SCN-COOL-001 to T_END, recorded by a Historian writer, with a broker-less UNS publisher attached
    alongside (peer projections) and a verification probe of the simulator's own analyzer updates."""
    path = tmp_path_factory.mktemp("hist") / "history.sqlite"
    svc = SimulatorService(start_runner=False)
    svc.create_simulation("SCN-COOL-001")
    writer = HistorianWriter(svc, path).attach()
    pub, client = attach_recording_publisher(svc)
    analyzer_updates = {}

    def probe():                                      # verification only; the writer never reads this
        e = svc.engine
        for group, updated in e.state.process.analyzer_updates.items():
            if updated and e.clock.time_s > 0:
                analyzer_updates.setdefault(group, set()).add(e.clock.time_s)
    svc.add_observer(probe)
    svc.run_until(T_END)
    process = svc.engine.state.process
    final = {"xmeas": [float(x) for x in process.xmeas], "xmeas_true": [float(x) for x in process.xmeas_true]}
    writer.close()
    pub.close()
    reader = HistorianReader(path)
    yield {"svc": svc, "path": path, "reader": reader, "scope": operational_scope_id(svc.engine),
           "calls": client.calls, "analyzer_updates": analyzer_updates, "final": final}
    reader.close()
    svc.shutdown()


# ---------------------------------------------------------------------------- 1-6 database, schema, identity
def test_database_initialises_with_wal_and_all_tables(tmp_path):
    store = HistorianStore(tmp_path / "h.sqlite")
    tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"historian_meta", "scopes", "coverage", "entities", "series", "samples", "state_changes", "events"} <= tables
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    store.close()


def test_schema_version_is_explicit_and_checked(tmp_path, recorded):
    db = sqlite3.connect(recorded["path"])
    assert db.execute("SELECT value FROM historian_meta WHERE key = 'schema'").fetchone()[0] == SCHEMA == "acme-historian/1"
    db.close()
    copy = tmp_path / "old.sqlite"
    shutil.copy(recorded["path"], copy)
    db = sqlite3.connect(copy)
    db.execute("UPDATE historian_meta SET value = 'acme-historian/0' WHERE key = 'schema'")
    db.commit()
    db.close()
    with pytest.raises(HistorianSchemaError):
        HistorianReader(copy)
    with pytest.raises(HistorianSchemaError):
        HistorianStore(copy)
    other = tmp_path / "other.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE x (y)").connection.close()
    with pytest.raises(HistorianSchemaError):
        HistorianReader(other)


def test_scope_row_is_the_operational_scope(recorded):
    r = recorded["reader"]
    assert r.current_scope() == recorded["scope"] and re.fullmatch(r"OS-[0-9a-f]{32}", recorded["scope"])
    (s,) = r.scopes()
    assert s == {"operational_scope_id": recorded["scope"], "simulation_start": "2026-01-05T06:00:00Z",
                 "duration_seconds": 10800, "sample_period_s": 1, "schema": SCHEMA}


def test_canonical_ids_are_stored_as_projected(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    ents = r.entities(scope, limit=MAX_ROWS)["rows"]
    ids = {e["entity_id"] for e in ents}
    proj = OperationalProjection(recorded["svc"].engine)
    assert set(proj.entity_ids()) <= ids and all(CID.match(i) for i in ids)
    assert {e["entity_type"] for e in ents} <= set(CTX["entity_types"])
    assert {s["entity_id"] for s in r.series(scope)} <= ids
    assert "quality_sample:QS-00001" in ids and "work_order:WO-00001" in ids          # records, as projected


def test_isa95_entity_metadata_comes_from_the_projection(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    pl = OperationalProjection(recorded["svc"].engine).placement
    for e in r.entities(scope, limit=MAX_ROWS)["rows"]:
        if e["entity_id"] in pl.entity_ids():
            assert e["isa95_path"] == list(pl.path(e["entity_id"]))
            assert e["parent_id"] == pl.parent(e["entity_id"])
            assert e["isa95_mapping_id"] == CTX["entity_types"][e["entity_type"]]["isa95"]
    reactor = r.entities(scope, parent_id="production_unit:PU-REACTOR")["rows"]
    assert "equipment_module:EM-REACTOR" in {e["entity_id"] for e in reactor}
    assert not {e["entity_type"] for e in r.entities(scope, limit=MAX_ROWS)["rows"]} & \
        {"fault", "operator_action", "unit", "process_cell"}


def test_series_metadata(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    s = {(x["entity_id"], x["variable"]): x for x in r.series(scope)}
    x9 = s[("equipment_module:EM-REACTOR", "measurement:XMEAS(9)")]
    assert (x9["kind"], x9["unit"], x9["semantics"], x9["sample_period_s"], x9["dead_time_s"], x9["source"]) == \
        ("xmeas", "degC", "step_state", 1, None, "simulator")
    kinds = [x["kind"] for x in s.values()]
    assert kinds.count("xmeas") == 41 and kinds.count("xmv") == 12
    assert kinds.count("setpoint") == kinds.count("controller_output") == 19
    assert {x["variable"] for x in s.values() if x["kind"] == "production"} >= {"rate_kg_h", "total_kg"}
    analyzers = [x for x in s.values() if x["semantics"] == "analyzer_sample"]
    assert sorted(x["variable"] for x in analyzers) == sorted(ANALYZER_VARIABLES)
    assert all(x["kind"] == "xmeas" for x in analyzers)


# ---------------------------------------------------------------------------- 7-10 sampling
def test_step_state_series_are_recorded_every_second(recorded):
    """Every step-state series has a sample at every second from its first observation to the end, with
    no hole; the TEP variables exist from t = 0 (a property that appears later, such as a storage unit's
    consumption rate from the first step, starts when it first exists: nothing is invented before)."""
    db = sqlite3.connect(recorded["path"])
    counts = db.execute("SELECT s.kind, s.variable, COUNT(*), MIN(t), MAX(t) FROM samples JOIN series s USING (series_id) "
                        "WHERE s.semantics = 'step_state' GROUP BY series_id").fetchall()
    db.close()
    assert counts and all(n == hi - lo + 1 and hi == T_END for _, _, n, lo, hi in counts)
    assert all(lo == 0 for kind, _, _, lo, _ in counts if kind in ("xmeas", "xmv", "setpoint", "controller_output"))
    late = {(v, lo) for kind, v, _, lo, _ in counts if lo != 0}
    assert late <= {("consumption_kg_h", 1)}, late


def test_analyzer_samples_follow_the_catalog_schedule(recorded):
    r, scope = recorded["reader"], recorded["scope"]

    def times(entity, var):
        return [x["t"] for x in r.samples(scope, entity, var, 0, T_END + 1, limit=MAX_ROWS)["rows"]]
    feed = times("control_module:CM-AT-RXFEED", "measurement:XMEAS(23)")
    product = times("control_module:CM-AT-PRODUCT", "measurement:XMEAS(38)")
    assert feed == [k * 360 + 1 for k in range(1, T_END // 360 + 1) if k * 360 + 1 <= T_END]
    assert product == [k * 900 + 1 for k in range(1, T_END // 900 + 1) if k * 900 + 1 <= T_END]
    # the catalog schedule is exactly when the simulator updates its analyzers (verification only)
    upd = recorded["analyzer_updates"]
    assert sorted(upd["reactor_feed"]) == feed and sorted(upd["product"]) == product
    assert sorted(upd["purge"]) == [t for t in range(1, T_END + 1) if analyzer_due("measurement:XMEAS(29)", t)]


def test_identical_consecutive_analyzer_results_are_each_recorded(tmp_path):
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, tmp_path / "h.sqlite").attach()
        real = w.projection.measurements

        def constant_analyzer(cid):          # the analyzer reports the same result at every sample
            return [(v, dict(f, value=42.0)) if v == "measurement:XMEAS(23)" else (v, f) for v, f in real(cid)]
        w.projection.measurements = constant_analyzer
        svc.run_until(1100)
        w.close()
        r = HistorianReader(tmp_path / "h.sqlite")
        rows = r.samples(r.current_scope(), "control_module:CM-AT-RXFEED", "measurement:XMEAS(23)", 0, 1101)["rows"]
        assert [(x["t"], x["value"]) for x in rows] == [(361, 42.0), (721, 42.0), (1081, 42.0)]
        r.close()
    finally:
        svc.shutdown()


def test_analyzer_dead_time_and_value_at(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    v = r.value_at(scope, "control_module:CM-AT-RXFEED", "measurement:XMEAS(23)", 700)
    assert (v["semantics"], v["sample_time"], v["age"], v["dead_time_s"], v["represents_time"]) == \
        ("analyzer_sample", 361, 339, 360, 1)
    assert v["covered"] and v["continuous_since_sample"]
    first = r.value_at(scope, "control_module:CM-AT-RXFEED", "measurement:XMEAS(23)", 360)
    assert first["value"] is None and first["sample_time"] is None           # no result yet: none invented
    p = r.value_at(scope, "control_module:CM-AT-PRODUCT", "measurement:XMEAS(38)", 1000)
    assert (p["sample_time"], p["dead_time_s"], p["represents_time"]) == (901, 900, 1)


# ---------------------------------------------------------------------------- 11-13 state and events
def test_state_changes_are_recorded_when_they_happen(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    ev = [e for e in r.events(scope, 0, T_END + 1, entity_id="work_unit:WU-CWP-101A",
                              event_type="EQUIPMENT_STATE_CHANGED", limit=MAX_ROWS)["rows"]
          if e["payload"].get("new") == "UNDER_MAINTENANCE"]
    assert len(ev) == 1
    # the event happened during the step that begins at its time; the state it changed is observed at
    # that step's end boundary (the simulator has no earlier change time, so none is invented)
    t_maint = ev[0]["t"]
    assert r.state_at(scope, "work_unit:WU-CWP-101A", t_maint)["state"]["status"]["value"] == "RUNNING"
    now = r.state_at(scope, "work_unit:WU-CWP-101A", t_maint + 1)["state"]["status"]
    assert now["value"] == "UNDER_MAINTENANCE" and now["since"] == t_maint + 1 and now["origin"] == "change"
    life = r.state_changes(scope, 0, T_END + 1, entity_id="site:SITE-TE", prop="lifecycle_status")["rows"]
    assert [(x["t"], x["value"], x["origin"]) for x in life] == [(0, "READY", "baseline"), (1, "RUNNING", "change"),
                                                                 (T_END, "PAUSED", "change")]


def test_events_are_the_operational_stream(recorded):
    r, scope, svc = recorded["reader"], recorded["scope"], recorded["svc"]
    got = r.events(scope, 0, T_END + 1, limit=MAX_ROWS)["rows"]
    expected = svc.get_events(limit=50000)
    assert [(e["event_id"], e["event_type"], e["t"], e["payload"], e["causation_id"], e["correlation_id"])
            for e in got] == [(e["event_id"], e["type"], e["simulation_time"], e["payload"], e["causation_id"],
                               e["correlation_id"]) for e in expected]
    assert all(re.fullmatch(r"(OE|LC)-\d{7}", e["event_id"]) for e in got)


def test_history_order_is_simulation_time_then_sequence(recorded):
    db = sqlite3.connect(recorded["path"])
    ev = db.execute("SELECT t, seq, event_id FROM events ORDER BY t, seq").fetchall()
    st = db.execute("SELECT seq FROM state_changes").fetchall()
    db.close()
    assert [e[2] for e in ev] == [e["event_id"] for e in recorded["svc"].get_events(limit=50000)]
    seqs = [s for _, s, _ in ev]
    assert seqs == sorted(seqs) and len(set(seqs + [s for (s,) in st])) == len(seqs) + len(st)   # one shared order


# ---------------------------------------------------------------------------- 14-16 scopes
def test_reset_starts_a_new_scope_and_scopes_stay_isolated(tmp_path):
    path = tmp_path / "h.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, path).attach()
        svc.run_until(120)
        a = operational_scope_id(svc.engine)
        svc.reset_simulation()
        svc.run_until(60)
        b = operational_scope_id(svc.engine)
        w.close()
        assert a != b
        agent, ev = HistorianReader(path), HistorianReader(path, access="evaluator")
        assert agent.current_scope() == b and [s["operational_scope_id"] for s in agent.scopes()] == [b]
        assert [s["operational_scope_id"] for s in ev.scopes()] == [a, b]
        with pytest.raises(HistorianAccessError):
            agent.samples(a, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 10)
        xa = ev.samples(a, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 1000)["rows"]
        xb = agent.samples(b, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 1000)["rows"]
        assert (xa[-1]["t"], len(xa), xb[-1]["t"], len(xb)) == (120, 121, 60, 61)      # previous scope kept
        assert ev.coverage(a) == [{"from_t": 0, "to_t": 120}] and agent.coverage(b) == [{"from_t": 0, "to_t": 60}]
        assert ev.events(b, 0, 61)["rows"][0]["event_type"] == "SIMULATION_RESET"
        assert {e["event_id"] for e in ev.events(a, 0, 121)["rows"]} & {e["event_id"] for e in ev.events(b, 0, 61)["rows"]}
        db = sqlite3.connect(path)                     # same ids in both scopes, never mixed
        for table in ("entities", "series", "state_changes", "events", "coverage"):
            assert {r[0] for r in db.execute(f"SELECT DISTINCT operational_scope_id FROM {table}")} == {a, b}
        db.close()
        with pytest.raises(ValueError):
            agent.samples("", "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 10)
    finally:
        svc.shutdown()


# ---------------------------------------------------------------------------- 17-18 duplicates
def test_duplicate_observations_are_idempotent_and_conflicts_raise(tmp_path):
    store = HistorianStore(tmp_path / "h.sqlite")
    scope = "OS-" + "0" * 32
    store.put_scope(scope, "2026-01-05T06:00:00Z", 100, 1)
    store.put_scope(scope, "2026-01-05T06:00:00Z", 100, 1)                         # same scope again: no-op
    with pytest.raises(HistorianIntegrityError):
        store.put_scope(scope, "2026-01-05T06:00:00Z", 100, 10)
    store.put_entity(scope, "site:SITE-TE", "site", None, ["site:SITE-TE"], "M-SITE", "Site", 0)
    sid = store.series_id(scope, "site:SITE-TE", "x", "property", "kg", "step_state", 1, None, "simulator")
    assert store.series_id(scope, "site:SITE-TE", "x", "property", "kg", "step_state", 1, None, "simulator") == sid
    store.put_samples([(sid, 5, 1.5, None)])
    store.put_samples([(sid, 5, 1.5, None)])                                        # identical: no-op
    with pytest.raises(HistorianIntegrityError):
        store.put_samples([(sid, 5, 2.5, None)])
    ev = {"event_id": "OE-0000001", "simulation_time": 5, "event_type": "ALARM_ACTIVATED", "entity_id": "site:SITE-TE",
          "source": "alarms", "severity": "warning", "payload": {"a": 1}, "causation_id": None, "correlation_id": "OE-0000001"}
    assert store.put_event(scope, ev, 1) is True
    assert store.put_event(scope, ev, 2) is False                                  # identical: no-op
    with pytest.raises(HistorianIntegrityError):
        store.put_event(scope, dict(ev, payload={"a": 2}), 3)
    store.close()
    db = sqlite3.connect(tmp_path / "h.sqlite")
    assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    db.close()


def test_reattached_writer_reingests_identically(tmp_path):
    """A writer closed and reattached at the same simulated time re-observes that second: the same
    samples are no-ops, and nothing is duplicated."""
    path = tmp_path / "h.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, path).attach()
        svc.run_until(50)
        w.close()
        db = sqlite3.connect(path)
        before = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("samples", "events", "series")}
        at50 = db.execute("SELECT series_id, value, quality FROM samples WHERE t = 50 ORDER BY series_id").fetchall()
        db.close()
        w = HistorianWriter(svc, path).attach()                                    # same scope, same t = 50
        w.close()
        db = sqlite3.connect(path)
        after = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("samples", "events", "series")}
        assert after == before and at50 == db.execute(
            "SELECT series_id, value, quality FROM samples WHERE t = 50 ORDER BY series_id").fetchall()
        db.close()
    finally:
        svc.shutdown()


# ---------------------------------------------------------------------------- 19-21 queries
def test_value_at_returns_the_last_sample_without_interpolation(recorded, tmp_path):
    r, scope = recorded["reader"], recorded["scope"]
    v = r.value_at(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 105)
    row = r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 105, 106)["rows"][0]
    assert (v["value"], v["sample_time"], v["age"], v["semantics"]) == (row["value"], 105, 0, "step_state")
    svc = SimulatorService(start_runner=False)                                      # a 10 s grid
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, tmp_path / "h10.sqlite", sample_period_s=10).attach()
        svc.run_until(120)
        w.close()
        r10 = HistorianReader(tmp_path / "h10.sqlite")
        v = r10.value_at(r10.current_scope(), "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 107)
        assert (v["sample_time"], v["age"]) == (100, 7)
        assert r10.series(r10.current_scope(), variable="measurement:XMEAS(9)")[0]["sample_period_s"] == 10
        r10.close()
    finally:
        svc.shutdown()


def test_ranges_are_half_open(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    rows = r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 100, 110)["rows"]
    assert [x["t"] for x in rows] == list(range(100, 110))
    t0 = next(e["t"] for e in r.events(scope, 0, T_END + 1, limit=MAX_ROWS)["rows"] if e["t"] > 0)
    assert [e for e in r.events(scope, t0, t0 + 1)["rows"]] and \
        all(e["t"] == t0 for e in r.events(scope, t0, t0 + 1)["rows"])               # start included
    assert all(e["t"] < t0 for e in r.events(scope, 0, t0)["rows"])                   # end excluded
    assert all(x["t"] < t0 for x in r.state_changes(scope, 0, t0, limit=MAX_ROWS)["rows"])


def test_queries_are_bounded(recorded):
    r, scope = recorded["reader"], recorded["scope"]
    page = r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 1000, limit=5)
    assert len(page["rows"]) == 5 and page["truncated"] and page["next_start"] == 5
    assert not r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 5, limit=5)["truncated"]
    for bad in (dict(limit=0), dict(limit=MAX_ROWS + 1)):
        with pytest.raises(ValueError):
            r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 10, **bad)
    with pytest.raises(ValueError):
        r.events(scope, 0, MAX_SPAN_S + 1)
    with pytest.raises(ValueError):
        r.state_changes(scope, 10, 10)
    assert r.events(scope, 0, T_END + 1, limit=3)["truncated"]


# ---------------------------------------------------------------------------- 22-25 boundary, time, MQTT
def test_evaluator_only_information_never_reaches_sqlite(recorded):
    svc, text = recorded["svc"], dump(recorded["path"], mask_scope=False)
    e = svc.engine
    faults = list(e.state.collection("faults").values())
    for secret in [e.state.run["run_id"], e.scenario["id"], e.scenario["name"], e.config_hash[:10],
                   *[f.id for f in faults], *[f.type for f in faults], "EV-0", "xmeas_true", "RUN-SCN"]:
        assert secret not in text, secret
    db = sqlite3.connect(recorded["path"])
    st = e.state
    for entity, prop in db.execute("SELECT DISTINCT entity_id, property FROM state_changes"):
        native = entity.split(":", 1)[1]
        if st.has_entity(native):
            assert prop not in hidden_properties(st.entity(native)), (entity, prop)
    for entity, var in db.execute("SELECT entity_id, variable FROM series"):
        native = entity.split(":", 1)[1]
        if st.has_entity(native):
            assert var not in hidden_properties(st.entity(native)), (entity, var)
    # samples are the transmitted measurements, not the true process values
    rows = dict(db.execute("SELECT s.variable, x.value FROM samples x JOIN series s USING (series_id) "
                           "WHERE x.t = ? AND s.kind = 'xmeas' AND s.semantics = 'step_state'", (T_END,)).fetchall())
    db.close()
    fin = recorded["final"]
    for var, value in rows.items():
        i = int(var[len("measurement:XMEAS("):-1])
        assert value == fin["xmeas"][i - 1]
    assert "xmeas_true" not in text and len(rows) == 22


def test_no_wall_clock_time_is_stored(recorded):
    db = sqlite3.connect(recorded["path"])
    columns = {c[1] for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
               for c in db.execute(f"PRAGMA table_info({t})")}
    db.close()
    assert not columns & {"observed_at", "published_at", "received_at", "ingested_at", "created_at", "wall_time"}
    iso = set(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", dump(recorded["path"])))
    assert iso and all(t.startswith("2026-01-05") for t in iso), iso              # the simulated calendar only


def test_historian_imports_no_mqtt():
    code = ("import sys; sys.path.insert(0, '.'); import historian.writer, historian.reader; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('paho', 'uns')); print(bad); "
            "sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@requires_mosquitto
def test_stopping_mqtt_does_not_change_the_history(tmp_path):
    dumps = []
    for with_uns in (True, False):
        path = tmp_path / f"h{with_uns}.sqlite"
        svc = SimulatorService(start_runner=False)
        broker = LocalBroker().start() if with_uns else None
        try:
            svc.create_simulation("SCN-COOL-001")
            w = HistorianWriter(svc, path).attach()
            if with_uns:
                from uns.publisher import UNSPublisher
                pub = UNSPublisher(svc, port=broker.port).attach()
            svc.run_until(200)
            if with_uns:
                broker.stop()                                   # MQTT goes away mid-run
            svc.run_until(400)
            w.close()
            if with_uns:
                pub.close()
        finally:
            if broker:
                broker.stop()
            svc.shutdown()
        dumps.append(dump(path))
    assert dumps[0] == dumps[1]


# ---------------------------------------------------------------------------- 26-30 determinism and writer lifecycle
def test_recording_is_deterministic(tmp_path):
    dumps = []
    for i in range(2):
        svc = SimulatorService(start_runner=False)
        try:
            svc.create_simulation("SCN-COOL-001")
            w = HistorianWriter(svc, tmp_path / f"h{i}.sqlite").attach()
            svc.run_until(600)
            w.close()
        finally:
            svc.shutdown()
        dumps.append(dump(tmp_path / f"h{i}.sqlite"))
    assert dumps[0] == dumps[1]


def test_commits_every_interval_and_coverage_is_only_committed_history(tmp_path):
    path = tmp_path / "h.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, path, commit_interval_s=60)     # driven by hand, without lifecycle commands
        w.sync()
        eng = svc.engine
        reader = HistorianReader(path, access="evaluator")
        for _ in range(90):
            eng.step(1)
            w.sync()
        scope = w.scope
        # the READY -> RUNNING change at t = 1 committed; the next commit is 60 s later
        assert reader.coverage(scope) == [{"from_t": 0, "to_t": 61}]
        last = reader.db.execute("SELECT MAX(t) FROM samples").fetchone()[0]
        assert last == 61
        w.flush()
        assert reader.coverage(scope) == [{"from_t": 0, "to_t": 90}]
        assert reader.db.execute("SELECT MAX(t) FROM samples").fetchone()[0] == 90
        svc.pause_simulation() if svc.runner.running else None
        w.close()
        assert w.sync not in svc._observers
        reader.close()
    finally:
        svc.shutdown()


def test_coverage_gaps_and_mid_run_attach_never_backfill(tmp_path):
    path = tmp_path / "h.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        svc.run_until(40)                                        # before any writer: never recorded
        w = HistorianWriter(svc, path).attach()
        svc.run_until(100)
        w.close()
        svc.run_until(200)                                       # no writer: a gap
        w = HistorianWriter(svc, path).attach()
        svc.run_until(300)
        w.close()
        r = HistorianReader(path)
        scope = r.current_scope()
        assert r.coverage(scope) == [{"from_t": 40, "to_t": 100}, {"from_t": 200, "to_t": 300}]
        t = [x["t"] for x in r.samples(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 0, 400,
                                        limit=MAX_ROWS)["rows"]]
        assert t == list(range(40, 101)) + list(range(200, 301))
        assert not r.events(scope, 0, 40)["rows"]                # SIMULATION_STARTED happened before: not backfilled
        v = r.value_at(scope, "equipment_module:EM-REACTOR", "measurement:XMEAS(9)", 150)
        assert (v["sample_time"], v["covered"], v["continuous_since_sample"]) == (100, False, False)
        base = r.state_changes(scope, 200, 201, entity_id="work_unit:WU-CWP-101A")["rows"]
        assert base and all(x["origin"] == "baseline" for x in base)
        first = r.state_changes(scope, 0, 41, entity_id="work_unit:WU-CWP-101A")["rows"]
        assert first and all(x["t"] == 40 and x["origin"] == "baseline" for x in first)
        r.close()
    finally:
        svc.shutdown()


def test_close_flushes_and_detaches(tmp_path):
    path = tmp_path / "h.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, path, commit_interval_s=3600).attach()
        svc.start_simulation()                                   # RUNNING; runner not started: no stepping
        for _ in range(30):
            svc.engine.step(1)
            svc._notify()
        w.close()
        assert w.sync not in svc._observers
        r = HistorianReader(path)
        assert r.coverage(r.current_scope()) == [{"from_t": 0, "to_t": 30}]
        r.close()
    finally:
        svc.shutdown()


# ---------------------------------------------------------------------------- peers: UNS and full demo
def test_historian_agrees_with_the_uns_at_shared_observation_times(recorded):
    """At every UNS sampling tick the UNS measurement equals the Historian sample; analyzer results are
    the value the UNS shows until the next analyzer result."""
    uns = {}
    for topic, payload, _, _ in recorded["calls"]:
        if "/measurement/" in topic and payload:
            d = json.loads(payload)
            uns[(d["entity_id"], d["variable"], d["simulation_time"])] = d["value"]
    db = sqlite3.connect(recorded["path"])
    hist = {(e, v, t): x for e, v, t, x in db.execute(
        "SELECT s.entity_id, s.variable, x.t, x.value FROM samples x JOIN series s USING (series_id)")}
    sem = dict(((e, v), s) for e, v, s in db.execute("SELECT entity_id, variable, semantics FROM series"))
    db.close()
    analyzer_times = {}
    for (e, v, t) in hist:
        if sem[(e, v)] == "analyzer_sample":
            analyzer_times.setdefault((e, v), []).append(t)
    for times in analyzer_times.values():
        times.sort()
    checked = 0
    for (e, v, t), value in uns.items():
        if sem.get((e, v)) == "step_state":
            assert hist[(e, v, t)] == value, (e, v, t)
            checked += 1
        elif sem.get((e, v)) == "analyzer_sample":
            times = analyzer_times[(e, v)]
            i = bisect.bisect_right(times, t)
            if i:                                              # the latest analyzer result at or before t
                assert hist[(e, v, times[i - 1])] == value, (e, v, t)
                checked += 1
    assert checked > 100000


@requires_fortran
def test_uns_is_byte_identical_with_the_historian_attached(tmp_path):
    golden = {k: v for k, v in json.loads(GOLDEN.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    assert summary(record(extra=lambda svc: HistorianWriter(svc, tmp_path / "h.sqlite").attach())) == golden


@requires_fortran
def test_full_demo_with_historian_and_uns_attached_is_unchanged(demo_run, tmp_path):
    path = tmp_path / "demo.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, path).attach()
        pub, client = attach_recording_publisher(svc)
        svc.run_until(10800)
        eng = svc.engine
        assert eng.completed
        assert [(e.event_id, e.type, e.simulation_time, e.payload) for e in eng.bus.log] == \
            [(e.event_id, e.type, e.simulation_time, e.payload) for e in demo_run.bus.log]
        assert (eng.adapter.get_states() == demo_run.adapter.get_states()).all()
        assert pub.sent == DEMO_MESSAGES                          # the UNS publishes exactly as before
        w.close()
        pub.close()
        r = HistorianReader(path)
        scope = r.current_scope()
        assert r.coverage(scope) == [{"from_t": 0, "to_t": 10800}]
        life = r.state_at(scope, "site:SITE-TE", 10800)["state"]["lifecycle_status"]
        assert life["value"] == "COMPLETED"
        assert len(r.events(scope, 0, 10801, limit=MAX_ROWS)["rows"]) == len(svc.get_events(limit=50000))
        r.close()
    finally:
        svc.shutdown()
