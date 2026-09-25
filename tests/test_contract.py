"""Canonical simulator contract (contract/canonical_contract.yaml) vs the implementation.

These tests do not change or constrain simulation behaviour. They fail when the implementation and
the documented contract drift apart - a new event type, property, route or TEP coupling that has no
documented semantics, or a documented item that no longer exists - so that the contract is updated
deliberately (docs/CANONICAL_SIMULATOR_CONTRACT.md, "Versioning").
"""
from fnmatch import fnmatch
from pathlib import Path

import yaml

import simulator
from simulator.events import BENCHMARK_EVENT_TYPES, LIFECYCLE_EVENT_TYPES, EventType
from simulator.tep import catalog
from simulator.tep import control_scheme as cs
from simulator.tep.boundary import BOUNDARY_PARAMETERS

from .conftest import requires_fortran

CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[1] / "contract" / "canonical_contract.yaml")
                          .read_text(encoding="utf-8"))


def test_contract_version_describes_current_simulator():
    c = CONTRACT["contract"]
    assert c["describes_simulator_version"] == simulator.__version__
    assert len(str(c["version"]).split(".")) == 3


def test_event_types_and_visibility_match_contract():
    declared = CONTRACT["events"]["types"]
    assert set(declared) == {t.value for t in EventType}
    for t in EventType:
        d = declared[t.value]
        assert d["visibility"] == ("benchmark" if t in BENCHMARK_EVENT_TYPES else "operational"), t
        assert d["id"] == ("LC" if t in LIFECYCLE_EVENT_TYPES else "EV"), t


def test_tep_boundary_couplings_match_contract(engine):
    declared = {c["relation"]: c for c in CONTRACT["tep_boundary"]["couplings"]}
    actual = {r.id: r for r in engine.coupling.relations if r.category == "tep_boundary"}
    assert set(declared) == set(actual)
    for rid, r in actual.items():
        d = declared[rid]
        assert r.output.kind == "boundary" and r.output.a == d["parameter"], rid
        assert BOUNDARY_PARAMETERS[d["parameter"]].fortran_symbol == d["symbol"], rid
    coupled = {d["parameter"] for d in declared.values()}
    assert set(CONTRACT["tep_boundary"]["defined_but_not_coupled"]) == set(BOUNDARY_PARAMETERS) - coupled


def test_only_tep_boundary_relations_write_into_tep(engine):
    """Invariant: enterprise effects reach TEP only through boundary parameters. No relation may
    write a process variable (XMEAS/XMV), a nominal value or a fault channel."""
    for r in engine.coupling.relations:
        assert r.output.kind in ("entity", "boundary"), r.id
        assert (r.output.kind == "boundary") == (r.category == "tep_boundary"), r.id


def _scope(state, mapping, quality_lab, entity_id, prop):
    rec = state.entities[entity_id]
    if any(b.equipment_id == entity_id and b.property == prop
           for b in list(mapping.xmeas.values()) + list(mapping.xmv.values())):
        return "process"
    if entity_id in mapping.loops.values():
        return "loop"
    if rec.kind in ("utility", "material"):
        return rec.kind
    if entity_id == quality_lab:
        return "laboratory"
    if rec.meta.get("level") == "StorageUnit":
        return "storage"
    if rec.meta.get("asset"):
        return "asset"
    return "unscoped"


def _lookup(table, prop, lab_tests):
    if prop in table:
        return table[prop]
    for pattern, spec in table.items():
        if "*" in pattern and fnmatch(prop, pattern):
            return spec
    if prop in lab_tests and "<test id, lower case>" in table:
        return table["<test id, lower case>"]
    return None


@requires_fortran
def test_every_entity_property_has_contract_semantics(demo_run):
    """Every property the simulator produces (full demo run) has a documented semantic class."""
    st, mapping = demo_run.state, demo_run.mapping
    lab = demo_run.config["quality"].get("laboratory")
    lab_tests = {t["id"].lower() for p in demo_run.config["quality"]["product_specifications"].values()
                 for t in p["tests"]}
    loop_props = CONTRACT["process_bound_properties"]["loop_properties"]
    classes = set(CONTRACT["semantic_classes"])
    missing = []
    for eid, rec in st.entities.items():
        for prop in rec.properties:
            scope = _scope(st, mapping, lab, eid, prop)
            if scope == "process":
                continue          # classified by the xmeas_binding / xmv_binding rules
            spec = loop_props.get(prop) if scope == "loop" else \
                _lookup(CONTRACT["properties"].get(scope, {}), prop, lab_tests)
            if spec is None:
                missing.append(f"{scope}:{eid}.{prop}")
            else:
                assert spec["class"] in classes, (eid, prop)
    assert not missing, f"properties without contract semantics: {sorted(set(missing))}"


@requires_fortran
def test_metadata_tags_agree_with_contract_observability(demo_run):
    """Properties the implementation tags as model_internal / unobservable are model_internal in the contract."""
    props = CONTRACT["properties"]
    for rec in demo_run.state.entities.values():
        for prop in rec.meta.get("model_internal", []):
            assert props["asset"][prop]["observability"] == "model_internal", (rec.id, prop)
        for prop in rec.meta.get("unobservable", []):
            assert props["storage"][prop]["observability"] == "model_internal", (rec.id, prop)


@requires_fortran
def test_collections_match_contract(demo_run):
    assert set(demo_run.state.collections) == set(CONTRACT["collections"])
    assert CONTRACT["collections"]["faults"]["observability"] == "benchmark"


def test_process_variable_catalog_matches_contract():
    pv = CONTRACT["process_variables"]
    assert pv["xmeas"]["count"] == len(catalog.XMEAS) == 41
    assert pv["xmv"]["count"] == len(catalog.XMV) == 12
    assert pv["idv"]["count"] == len(catalog.IDV) == 20
    assert pv["control_loops"]["count"] == len(cs.LOOPS)
    periods = {}
    for loop in cs.LOOPS.values():
        periods[loop.period_steps] = periods.get(loop.period_steps, 0) + 1
    assert periods == pv["control_loops"]["periods_s"]


def test_every_api_route_is_classified():
    """Every /api route has a ground-truth classification; only /api/benchmark routes are 'benchmark'."""
    from api.app import create_app
    from api.service import SimulatorService
    svc = SimulatorService(start_runner=False)
    try:
        app = create_app(svc, autoload=None)
        routes = {f"{m} {r.path}" for r in app.routes if r.path.startswith("/api/")
                  for m in (getattr(r, "methods", None) or set()) - {"HEAD"}}
    finally:
        svc.shutdown()
    declared = CONTRACT["api_routes"]
    assert routes == set(declared)
    for key, spec in declared.items():
        assert (spec["class"] == "benchmark") == key.split(" ", 1)[1].startswith("/api/benchmark/"), key
