"""The read-only Historian HTTP API (historian/api.py) over HistorianReader.

A recorded file with two operational scopes (a short one, then a reset into the current one) is
served with the default ``current`` access and with ``evaluator`` access. Expected values always come
from HistorianReader itself: the API must add no query semantics of its own.
"""
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from api.service import SimulatorService
from historian import HistorianSchemaError
from historian.api import create_app
from historian.reader import DEFAULT_LIMIT, MAX_ROWS, MAX_SPAN_S, HistorianReader
from historian.writer import HistorianWriter
from projection.operational import ROOT_DIR

X9 = ("equipment_module:EM-REACTOR", "measurement:XMEAS(9)")
PUMP = "work_unit:WU-CWP-101A"
FORBIDDEN_KEYS = {"run_id", "scenario_id", "scenario", "seed", "configuration_hash", "config_hash", "faults",
                  "xmeas_true", "idv", "health", "observed_at"}


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    path = tmp_path_factory.mktemp("api") / "history.sqlite"
    svc = SimulatorService(start_runner=False)
    try:
        svc.create_simulation("SCN-COOL-001")
        w = HistorianWriter(svc, path).attach()
        svc.run_until(120)                                   # scope A (older)
        svc.reset_simulation()
        svc.run_until(1000)                                  # scope B (current), with QS-00001 at 901
        secrets = [svc.engine.state.run["run_id"], svc.engine.scenario["id"], svc.engine.scenario["name"],
                   svc.engine.config_hash[:10]] + [f.id for f in svc.engine.state.collection("faults").values()]
        w.close()
    finally:
        svc.shutdown()
    ev = HistorianReader(path, access="evaluator")
    a, b = [s["operational_scope_id"] for s in ev.scopes()]
    ev.close()
    return {"path": path, "a": a, "b": b, "secrets": secrets}


@pytest.fixture(scope="module")
def api(recorded):
    return TestClient(create_app(recorded["path"]))                      # default: current access


@pytest.fixture(scope="module")
def evaluator(recorded):
    return TestClient(create_app(recorded["path"], access="evaluator"))


@pytest.fixture(scope="module")
def reader(recorded):
    r = HistorianReader(recorded["path"], access="evaluator")
    yield r
    r.close()


def err(resp):
    return resp.status_code, resp.json()["error"]["code"]


def keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from keys(v)


# ---------------------------------------------------------------------------- 1-4 status, access, scopes
def test_status(api, recorded):
    s = api.get("/status").json()
    assert (s["api"], s["schema"], s["access"], s["current_scope"], s["scopes_visible"]) == \
        ("acme-historian-api/1", "acme-historian/1", "current", recorded["b"], 1)
    assert s["coverage"] == [{"from_t": 0, "to_t": 1000}] and s["covered_range"] == {"from_t": 0, "to_t": 1000}
    assert s["limits"] == {"default_limit": DEFAULT_LIMIT, "max_limit": MAX_ROWS, "max_span_s": MAX_SPAN_S}


def test_current_scope_is_the_default(api, recorded):
    assert [s["operational_scope_id"] for s in api.get("/scopes").json()["scopes"]] == [recorded["b"]]


def test_evaluator_access_is_explicit(api, evaluator, recorded):
    assert evaluator.get("/status").json()["access"] == "evaluator"
    assert [s["operational_scope_id"] for s in evaluator.get("/scopes").json()["scopes"]] == [recorded["a"], recorded["b"]]
    assert evaluator.get("/series", params={"scope": recorded["a"], "entity": X9[0]}).status_code == 200
    # a query parameter can never widen the service's access
    r = api.get("/series", params={"scope": recorded["a"], "entity": X9[0], "access": "evaluator"})
    assert err(r) == (403, "scope_not_permitted")


def test_scopes(evaluator, reader, recorded):
    scopes = evaluator.get("/scopes").json()["scopes"]
    for s in scopes:
        base = {k: s[k] for k in ("operational_scope_id", "simulation_start", "duration_seconds", "sample_period_s", "schema")}
        assert base in reader.scopes()
        assert s["coverage"] == reader.coverage(s["operational_scope_id"])
    assert [s["covered_range"] for s in scopes] == [{"from_t": 0, "to_t": 120}, {"from_t": 0, "to_t": 1000}]


# ---------------------------------------------------------------------------- 5-11 endpoints agree with the reader
def test_entities(api, reader, recorded):
    b = recorded["b"]
    got = api.get("/entities", params={"scope": b}).json()
    assert got["rows"] == reader.entities(b)["rows"] and not got["truncated"]
    rows = api.get("/entities", params={"scope": b, "type": "control_module", "parent": "equipment_module:EM-REACTOR"}).json()["rows"]
    assert rows == reader.entities(b, entity_type="control_module", parent_id="equipment_module:EM-REACTOR")["rows"]
    assert rows and all(set(r) >= {"entity_id", "entity_type", "parent_id", "isa95_path", "isa95_mapping_id", "name"}
                        for r in rows)


def test_series(api, reader, recorded):
    b = recorded["b"]
    assert api.get("/series", params={"scope": b, "entity": X9[0]}).json()["rows"] == reader.series(b, X9[0])
    one = api.get("/series", params={"scope": b, "entity": X9[0], "variable": X9[1]}).json()["rows"]
    assert one == reader.series(b, *X9) and len(one) == 1
    assert err(api.get("/series", params={"scope": b, "entity": "equipment_module:NOPE"})) == (404, "unknown_entity")


def test_samples(api, reader, recorded):
    b = recorded["b"]
    got = api.get("/samples", params={"scope": b, "entity": X9[0], "variable": X9[1], "start": 100, "end": 200}).json()
    want = reader.samples(b, *X9, 100, 200)
    assert got["rows"] == want["rows"] and got["series"] == want["series"] and not got["truncated"]
    assert got["next_cursor"] is None
    assert err(api.get("/samples", params={"scope": b, "entity": X9[0], "variable": "measurement:XMEAS(99)",
                                           "start": 0, "end": 10})) == (404, "unknown_series")


def test_value_at(api, reader, recorded):
    b = recorded["b"]
    got = api.get("/value_at", params={"scope": b, "entity": X9[0], "variable": X9[1], "t": 500}).json()
    want = reader.value_at(b, *X9, 500)
    assert {k: got[k] for k in want} == want and got["series"] == reader.series(b, *X9)[0]
    an = api.get("/value_at", params={"scope": b, "entity": "control_module:CM-AT-RXFEED",
                                      "variable": "measurement:XMEAS(23)", "t": 700}).json()
    assert (an["semantics"], an["sample_time"], an["dead_time_s"], an["represents_time"], an["covered"]) == \
        ("analyzer_sample", 361, 360, 1, True)


def test_events(api, reader, recorded):
    b = recorded["b"]
    got = api.get("/events", params={"scope": b, "start": 0, "end": 1001}).json()
    assert got["rows"] == reader.events(b, 0, 1001)["rows"] and got["rows"][0]["event_type"] == "SIMULATION_RESET"
    assert all(re.fullmatch(r"(OE|LC)-\d{7}", e["event_id"]) for e in got["rows"])
    one = api.get("/events", params={"scope": b, "start": 0, "end": 1001, "type": "QUALITY_SAMPLE_TAKEN"}).json()["rows"]
    assert one == reader.events(b, 0, 1001, event_type="QUALITY_SAMPLE_TAKEN")["rows"] and one


def test_states(api, reader, recorded):
    b = recorded["b"]
    got = api.get("/states", params={"scope": b, "entity": PUMP, "start": 0, "end": 1001}).json()
    assert got["rows"] == reader.state_changes(b, 0, 1001, entity_id=PUMP)["rows"] and got["rows"]
    life = api.get("/states", params={"scope": b, "entity": "site:SITE-TE", "property": "lifecycle_status",
                                      "start": 0, "end": 1001}).json()["rows"]
    assert life == reader.state_changes(b, 0, 1001, entity_id="site:SITE-TE", prop="lifecycle_status")["rows"]


def test_state_at(api, reader, recorded):
    b = recorded["b"]
    got = api.get("/state_at", params={"scope": b, "entity": PUMP, "t": 500}).json()
    assert {k: got[k] for k in ("entity_id", "t", "covered", "state")} == reader.state_at(b, PUMP, 500)
    assert got["state"]["status"]["value"] == "RUNNING"
    assert err(api.get("/state_at", params={"scope": b, "entity": "work_unit:NOPE", "t": 5})) == (404, "unknown_entity")


# ---------------------------------------------------------------------------- 12-17 parameters and limits
def test_scope_is_required(api):
    assert err(api.get("/entities")) == (400, "missing_parameter")
    assert err(api.get("/entities", params={"scope": ""})) == (400, "missing_scope")
    assert "scope" in api.get("/events", params={"start": 0, "end": 10}).json()["error"]["message"]


def test_unknown_scope(api):
    assert err(api.get("/entities", params={"scope": "OS-" + "f" * 32})) == (404, "unknown_scope")


def test_ranges_are_half_open(api, recorded):
    rows = api.get("/samples", params={"scope": recorded["b"], "entity": X9[0], "variable": X9[1],
                                       "start": 100, "end": 110}).json()["rows"]
    assert [r["t"] for r in rows] == list(range(100, 110))


def test_range_errors(api, recorded):
    base = {"scope": recorded["b"], "entity": X9[0], "variable": X9[1]}
    assert err(api.get("/samples", params=dict(base, start=0, end=MAX_SPAN_S + 1))) == (400, "span_exceeded")
    assert err(api.get("/samples", params=dict(base, start=10, end=10))) == (400, "invalid_range")
    assert err(api.get("/events", params={"scope": recorded["b"], "start": 5, "end": 1})) == (400, "invalid_range")
    assert err(api.get("/samples", params=dict(base, start="x", end=10))) == (400, "invalid_parameter")


def test_default_limit(api, recorded):
    got = api.get("/samples", params={"scope": recorded["b"], "entity": X9[0], "variable": X9[1],
                                      "start": 0, "end": 1001}).json()
    assert len(got["rows"]) == DEFAULT_LIMIT == 1000 and got["truncated"] and got["next_cursor"]


def test_maximum_limit(api, recorded):
    base = {"scope": recorded["b"], "entity": X9[0], "variable": X9[1], "start": 0, "end": 1001}
    assert err(api.get("/samples", params=dict(base, limit=MAX_ROWS + 1))) == (400, "invalid_limit")
    assert err(api.get("/samples", params=dict(base, limit=0))) == (400, "invalid_limit")
    got = api.get("/samples", params=dict(base, limit=MAX_ROWS)).json()
    assert len(got["rows"]) == 1001 and not got["truncated"]


# ---------------------------------------------------------------------------- 18-20 pagination
def pages(client, path, params):
    out, cursor, n = [], None, 0
    while True:
        r = client.get(path, params=dict(params, **({"cursor": cursor} if cursor else {}))).json()
        out.append(r)
        cursor, n = r["next_cursor"], n + 1
        if not r["truncated"]:
            assert cursor is None
            return out
        assert n < 1000


def test_pagination_covers_every_row_once_in_order(api, reader, recorded):
    b = recorded["b"]
    s = pages(api, "/samples", {"scope": b, "entity": X9[0], "variable": X9[1], "start": 0, "end": 50, "limit": 7})
    assert [x for p in s for x in p["rows"]] == reader.samples(b, *X9, 0, 50)["rows"] and len(s) == 8
    e = pages(api, "/events", {"scope": b, "start": 0, "end": 1001, "limit": 2})
    assert [x for p in e for x in p["rows"]] == reader.events(b, 0, 1001)["rows"]
    st = pages(api, "/states", {"scope": b, "entity": "site:SITE-TE", "start": 0, "end": 1001, "limit": 1})
    assert [x for p in st for x in p["rows"]] == reader.state_changes(b, 0, 1001, entity_id="site:SITE-TE")["rows"]
    many = pages(api, "/states", {"scope": b, "entity": "material_lot:LOT-A-2601", "start": 0, "end": 1001, "limit": 300})
    assert [x for p in many for x in p["rows"]] == \
        reader.state_changes(b, 0, 1001, entity_id="material_lot:LOT-A-2601", limit=MAX_ROWS)["rows"]


def test_pagination_is_deterministic(recorded):
    params = {"scope": recorded["b"], "start": 0, "end": 1001, "limit": 3}
    one = pages(TestClient(create_app(recorded["path"])), "/events", params)
    two = pages(TestClient(create_app(recorded["path"])), "/events", params)
    assert one == two


def test_invalid_cursor(api, recorded):
    b = recorded["b"]
    base = {"scope": b, "entity": X9[0], "variable": X9[1], "start": 0, "end": 100, "limit": 5}
    good = api.get("/samples", params=base).json()["next_cursor"]
    assert err(api.get("/samples", params=dict(base, cursor="not-a-cursor"))) == (400, "invalid_cursor")
    assert err(api.get("/samples", params=dict(base, cursor=good + "x"))) == (400, "invalid_cursor")
    assert err(api.get("/samples", params=dict(base, variable="measurement:XMEAS(7)", cursor=good))) == \
        (400, "invalid_cursor")                                                  # a cursor of another query
    assert err(api.get("/events", params={"scope": b, "start": 0, "end": 100, "cursor": good})) == (400, "invalid_cursor")


# ---------------------------------------------------------------------------- 21-26 boundary, read-only, errors
def crawl(client, scope):
    out = [client.get("/status").json(), client.get("/scopes").json()]
    ents = client.get("/entities", params={"scope": scope, "limit": MAX_ROWS}).json()
    out.append(ents)
    for e in ents["rows"]:
        eid = e["entity_id"]
        out.append(client.get("/series", params={"scope": scope, "entity": eid}).json())
        out.append(client.get("/state_at", params={"scope": scope, "entity": eid, "t": 1000}).json())
        out.append(client.get("/states", params={"scope": scope, "entity": eid, "start": 0, "end": 1001,
                                                  "limit": MAX_ROWS}).json())
    out.append(client.get("/events", params={"scope": scope, "start": 0, "end": 1001, "limit": MAX_ROWS}).json())
    out.append(client.get("/value_at", params={"scope": scope, "entity": X9[0], "variable": X9[1], "t": 999}).json())
    return out


def test_no_evaluator_only_information_is_exposed(api, recorded):
    responses = crawl(api, recorded["b"])
    text = json.dumps(responses)
    for secret in recorded["secrets"] + ["EV-0", "xmeas_true", "RUN-SCN"]:
        assert secret not in text, secret
    iso = set(re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text))
    assert iso and all(t.startswith("2026-01-05") for t in iso)                   # simulated calendar, no wall clock


def test_no_run_scenario_seed_or_configuration_fields(api, evaluator, recorded):
    for client in (api, evaluator):
        found = set(keys(crawl(client, recorded["b"]))) & FORBIDDEN_KEYS
        assert not found, found


def test_the_api_is_read_only(api, recorded):
    app = api.app
    methods = {m for r in app.routes if hasattr(r, "methods") for m in r.methods}
    assert methods <= {"GET", "HEAD"}
    paths = {r.path for r in app.routes}
    assert paths == {"/status", "/scopes", "/entities", "/series", "/samples", "/value_at", "/events", "/states", "/state_at"}
    before = hashlib.sha256(open(recorded["path"], "rb").read()).hexdigest()
    for path in paths:
        for method in ("post", "put", "patch", "delete"):
            assert err(getattr(api, method)(path)) == (405, "method_not_allowed")
    for probe in ("/history.sqlite", "/database", "/sql", "/docs", "/openapi.json"):
        assert err(api.get(probe)) == (404, "not_found")
    crawl(api, recorded["b"])
    assert hashlib.sha256(open(recorded["path"], "rb").read()).hexdigest() == before


def test_incompatible_or_missing_database_is_rejected(tmp_path, recorded):
    copy = tmp_path / "old.sqlite"
    shutil.copy(recorded["path"], copy)
    db = sqlite3.connect(copy)
    db.execute("UPDATE historian_meta SET value = 'acme-historian/0' WHERE key = 'schema'")
    db.commit()
    db.close()
    with pytest.raises(HistorianSchemaError):
        create_app(copy)
    with pytest.raises(FileNotFoundError):
        create_app(tmp_path / "missing.sqlite")
    r = subprocess.run([sys.executable, "scripts/run_historian_api.py", "--database", str(copy)], cwd=ROOT_DIR,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "acme-historian/1" in r.stdout


def test_database_errors_are_reported_without_internals(recorded):
    client = TestClient(create_app(recorded["path"]))
    client.app.state.reader.db.close()                                            # the database goes away
    r = client.get("/status")
    assert err(r) == (503, "database_unavailable")
    assert "sqlite" not in r.text.lower() and str(recorded["path"].parent.name) not in r.text


def test_current_scope_isolation_with_several_scopes(api, recorded):
    a = recorded["a"]
    assert api.get("/status").json()["scopes_visible"] == 1
    for path, params in (("/entities", {}), ("/series", {"entity": X9[0]}),
                         ("/samples", {"entity": X9[0], "variable": X9[1], "start": 0, "end": 10}),
                         ("/value_at", {"entity": X9[0], "variable": X9[1], "t": 5}),
                         ("/events", {"start": 0, "end": 10}), ("/states", {"entity": PUMP, "start": 0, "end": 10}),
                         ("/state_at", {"entity": PUMP, "t": 5})):
        assert err(api.get(path, params=dict(params, scope=a))) == (403, "scope_not_permitted"), path
