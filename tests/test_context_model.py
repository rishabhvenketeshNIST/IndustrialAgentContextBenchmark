"""Canonical context model (contract/context_model.yaml) vs the simulator.

The context model is documentation metadata. These tests keep it honest: every entity type selects
something the simulator actually has, every simulator entity and record has exactly one canonical
identity, classifications use the defined vocabularies, and observability agrees with the operational
boundary (so the context model cannot reintroduce hidden information).
"""
from pathlib import Path

import pytest
import yaml

import simulator
from api.operational import ASSET_STATUS_OPERATIONAL, hidden_properties
from simulator.isa95 import EquipmentLevel
from simulator.tep import catalog
from simulator.tep import control_scheme as cs

from .conftest import requires_fortran

ROOT = Path(__file__).resolve().parents[1]
CTX = yaml.safe_load((ROOT / "contract" / "context_model.yaml").read_text(encoding="utf-8"))
CONTRACT = yaml.safe_load((ROOT / "contract" / "canonical_contract.yaml").read_text(encoding="utf-8"))
VOCAB = CTX["vocabularies"]
TYPES = CTX["entity_types"]
FREE_TEXT_ENDPOINTS = {"any entity type", "entity property", "entity property or boundary parameter",
                       "boundary parameter"}


def _as_list(x):
    return x if isinstance(x, list) else [x]


def _entity_type_of(rec):
    if rec.kind == "equipment":
        level = rec.meta["level"]
        return next(t for t, d in TYPES.items() if d["source"].get("hierarchy_level") == level)
    return next(t for t, d in TYPES.items() if d["source"].get("entity_kind") == rec.kind)


def _scope(state, lab, eid):
    rec = state.entities[eid]
    if rec.kind == "utility":
        return "utility"
    if eid == lab:
        return "laboratory"
    if rec.meta.get("level") == "StorageUnit":
        return "storage"
    if rec.meta.get("asset"):
        return "asset"
    return None


def test_versions_are_current():
    c = CTX["context_model"]
    assert c["describes_simulator_version"] == simulator.__version__
    assert c["canonical_contract_version"] == CONTRACT["contract"]["version"]


def test_classifications_use_the_vocabularies():
    obs = set(VOCAB["observability"])
    for name, d in TYPES.items():
        assert d["observability"] in obs, name
        assert d.get("source"), f"{name} has no source"
        assert d.get("owner"), f"{name} has no owner"
    for name, d in CTX["observations"].items():
        assert d["observability"] in obs, name
        assert set(d.get("exceptions", {}).values()) <= obs, name
    for name, d in CTX["relationships"].items():
        assert d["observability"] in obs and d.get("source"), name
    for name, d in CTX["state_domains"].items():
        assert d["observability"] in obs, name
    ids = [m["id"] for m in CTX["isa95_mapping"]]
    assert len(ids) == len(set(ids))
    assert {m["mapping"] for m in CTX["isa95_mapping"]} <= set(VOCAB["mapping"])
    assert {d["isa95"] for d in TYPES.values()} <= set(ids), "entity types refer to unknown mapping ids"


def test_entity_type_sources_exist(engine):
    st, cfg = engine.state, engine.config
    used_levels = {e.level.value for e in engine.hierarchy.elements.values()}
    for name, d in TYPES.items():
        src = d["source"]
        if "hierarchy_level" in src:
            assert src["hierarchy_level"] in {lv.value for lv in EquipmentLevel}, name
            assert src["hierarchy_level"] in used_levels, f"{name}: level not used by the simulator"
        elif "entity_kind" in src:
            assert st.entities_of_kind(src["entity_kind"]), name
        elif "collection" in src:
            assert src["collection"] in st.collections, name
        elif "catalog" in src:
            assert len(getattr(catalog, src["catalog"])) > 0, name
        elif "config_key" in src:
            section, key = src["config_key"].split(".")
            assert cfg[section][key], name
        else:
            path = next(iter(src.values()))
            assert (ROOT / path).exists(), name
        if "config" in d:
            assert (ROOT / d["config"]).exists(), name
    # the context model does not add a hierarchy: every level the simulator uses has one entity type
    assert used_levels == {d["source"]["hierarchy_level"] for d in TYPES.values() if "hierarchy_level" in d["source"]}


@requires_fortran
def test_every_simulator_object_has_one_unique_canonical_identity(demo_run):
    st, cfg = demo_run.state, demo_run.config
    ids = []
    for rec in st.entities.values():
        ids.append(f"{_entity_type_of(rec)}:{rec.id}")
    by_collection = {d["source"]["collection"]: t for t, d in TYPES.items() if "collection" in d["source"]}
    assert set(st.collections) == set(by_collection), "every record collection has an entity type"
    for coll, records in st.collections.items():
        ids += [f"{by_collection[coll]}:{rid}" for rid in records]
    ids += [f"measurement:{v.id}" for v in catalog.XMEAS] + [f"manipulated_variable:{v.id}" for v in catalog.XMV]
    ids += [f"disturbance:{v.id}" for v in catalog.IDV]
    ids += [f"product:{p}" for p in cfg["production"]["products"]]
    ids += [f"quality_test:{t['id']}" for p in cfg["quality"]["product_specifications"].values() for t in p["tests"]]
    assert len(ids) == len(set(ids)), "duplicate canonical identities"
    # purchase and production orders share the PO- prefix; the type qualifier keeps them apart
    native = [i.split(":", 1)[1] for i in ids if i.startswith(("production_order:", "purchase_order:"))]
    assert any(n.startswith("PO-2026") for n in native) and any(n.startswith("PO-0") for n in native)
    # every control loop resolves to exactly one pid_loop control module
    loops = [demo_run.mapping.loops[lid] for lid in cs.LOOPS]
    assert len(loops) == len(set(loops)) == len(cs.LOOPS)
    assert all(st.entity(cm).meta["equipment_class"] == "pid_loop" for cm in loops)


def test_relationships_connect_defined_entity_types():
    for name, d in CTX["relationships"].items():
        for end in ("from", "to"):
            for t in _as_list(d[end]):
                assert t in TYPES or t in FREE_TEXT_ENDPOINTS, (name, end, t)
    causal = {"fault_targets", "correlated_with_fault", "coupling_relation", "process_influence"}
    assert all(CTX["relationships"][r]["observability"] == "evaluator_only" for r in causal)


def test_observations_define_timestamp_unit_and_subject():
    for name, d in CTX["observations"].items():
        assert d["timestamp"] in VOCAB["timestamp"], name
        assert d["unit"].get("from") or d["unit"].get("literal"), name
        for s in _as_list(d["subject"]):
            assert s in TYPES, (name, s)
    assert all(v.unit for v in catalog.XMEAS), "every measurement has a unit"


@requires_fortran
def test_observation_observability_agrees_with_the_operational_boundary(demo_run):
    """An observation the context model calls operational is served operationally, and one it calls
    evaluator_only is withheld - for every entity that has the property in the full demo run."""
    st = demo_run.state
    lab = demo_run.config["quality"]["laboratory"]
    checked = 0
    for name, d in CTX["observations"].items():
        for scope, props in d.get("properties", {}).items():
            for eid, rec in st.entities.items():
                if _scope(st, lab, eid) != scope:
                    continue
                obs = d.get("exceptions", {}).get(rec.meta.get("utility_type"), d["observability"])
                for prop in props:
                    if prop not in rec.properties:
                        continue
                    hidden = prop in hidden_properties(rec)
                    assert hidden == (obs in ("evaluator_only", "derived_evaluator_only")), (name, eid, prop, obs)
                    checked += 1
    assert checked > 100
    assert CTX["observations"]["equipment_status"]["observability"] == "derived_operational"
    assert ASSET_STATUS_OPERATIONAL == {"DEGRADED": "RUNNING"}


def test_events_follow_the_contract_and_operational_identity():
    ev = CTX["events"]
    assert set(ev["identity"]["operational"]["prefixes"]) == {"OE", "LC"}
    assert set(ev["identity"]["evaluator"]["prefixes"]) == {"EV", "LC"}
    assert CONTRACT["events"]["types"] and CONTRACT["operational_boundary"]["event_stream"]["ids"].startswith("OE-")
    assert ev["timestamp"]["semantics"] in VOCAB["timestamp"]


@pytest.mark.parametrize("mapping_id", ["M-UNIT-LEVEL", "M-PROCESS-SEGMENT", "M-SUBLOT", "M-PERSONNEL-CLASS"])
def test_not_represented_concepts_are_not_invented(engine, mapping_id):
    """NOT_REPRESENTED is a valid mapping: the simulator has no such objects, and the context model
    defines no entity type for them."""
    m = next(x for x in CTX["isa95_mapping"] if x["id"] == mapping_id)
    assert m["mapping"] == "NOT_REPRESENTED"
    assert mapping_id not in {d["isa95"] for d in TYPES.values()}
    if mapping_id == "M-UNIT-LEVEL":
        assert not engine.hierarchy.by_level(EquipmentLevel.UNIT)


# ---------------------------------------------------------------------------- resolved decisions
@requires_fortran
def test_u03_product_yields_exactly_one_material_and_lots_are_stored_as_it(demo_run):
    """U-03: a Product Definition (PROD-GH-M1) yields exactly one Material Definition (MAT-GH). The
    tanks that hold production lots store that material, so lot -> product -> material is consistent."""
    st, cfg = demo_run.state, demo_run.config
    rel = CTX["relationships"]["yields_material"]
    assert (rel["from"], rel["to"], rel["derivation"]) == ("product", "material", "configuration")
    products = cfg["production"]["products"]
    for pid, p in products.items():
        mat = p["material"]                                    # exactly one material per product
        assert isinstance(mat, str) and st.entity(mat).kind == "material", pid
        assert st.get(mat, "category") == "finished_good", pid
        assert pid != mat                                      # distinct identities, not aliases
    lots = list(st.collection("production_lots").values())
    assert lots
    for lot in lots:
        mat = products[lot.product_id]["material"]
        assert st.get(lot.location, "material") == mat, (lot.lot_id, lot.location)


@requires_fortran
def test_u05_canonical_ids_are_type_qualified_unambiguous_and_frozen(demo_run):
    """U-05: <entity_type>:<native_id>; native ids are unchanged and contain no ':' so the canonical id
    splits unambiguously; type names are well formed; the PO- collision is resolved by the type."""
    import re
    ident = CTX["identity"]
    assert ident["canonical_id"] == "<entity_type>:<native_id>" and ident["status"] == "frozen"
    pattern = re.compile(ident["entity_type_pattern"])
    assert all(pattern.match(t) for t in TYPES)
    st = demo_run.state
    natives = list(st.entities) + [rid for coll in st.collections.values() for rid in coll]
    natives += [v.id for v in catalog.XMEAS + catalog.XMV + catalog.IDV]
    assert not [n for n in natives if ":" in str(n)], "a native id contains the separator"
    po_prod = next(iter(st.collection("production_orders")))
    po_purch = next(iter(st.collection("purchase_orders")))
    assert po_prod[:3] == po_purch[:3] == "PO-"
    assert f"production_order:{po_prod}" != f"purchase_order:{po_purch}"
    for cid in (f"production_order:{po_prod}", "measurement:XMEAS(9)", "control_module:CM-TIC-RX"):
        etype, native = cid.split(ident["separator"], 1)
        assert etype in TYPES and native


def test_u07_lifecycle_events_are_operational_and_carry_no_ground_truth():
    """U-07: lifecycle events appear in the operational stream with the documented ids, entity and
    payloads, and without run or scenario identity."""
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.service import SimulatorService
    svc = SimulatorService(start_runner=False)
    try:
        client = TestClient(create_app(svc, autoload="SCN-COOL-001"))
        client.post("/api/simulation/start")
        client.post("/api/simulation/pause")          # published only while the runner is running
        client.post("/api/simulation/resume")
        client.post("/api/simulation/step", json={"n": 10})
        before_reset = client.get("/api/events", params={"limit": 20000}).json()
        client.post("/api/simulation/reset", json={"duration_seconds": 5})
        client.post("/api/simulation/step", json={"n": 5})
        after_reset = client.get("/api/events", params={"limit": 20000}).json()
    finally:
        svc.shutdown()
    spec = CTX["events"]["lifecycle"]["types"]
    seen = {e["type"]: e for e in before_reset + after_reset if e["type"] in spec}
    assert set(seen) == set(spec)
    for t, e in seen.items():
        assert e["event_id"].startswith(spec[t]["id"] + "-"), t
        assert e["target"] == "SITE-TE"
        assert set(e["payload"]) == set(spec[t]["operational_payload"]), t
    assert after_reset[0]["type"] == "SIMULATION_RESET"          # a reset begins a new run
