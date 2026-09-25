"""Validation 2-6: variable mappings, ISA-95 hierarchy, deterministic clock and events."""
import copy

import pytest

from simulator.common import ConfigError, parse_datetime
from simulator.events import EventBus, EventType, Visibility
from simulator.isa95 import EquipmentLevel, Hierarchy
from simulator.isa95.tep_mapping import TEPMapping
from simulator.simulation.clock import SimulationClock, validate_speed
from simulator.tep import catalog
from simulator.tep import control_scheme as cs
from tests.conftest import base_config


@pytest.fixture(scope="module")
def model():
    cfg = base_config()
    h = Hierarchy.from_config(cfg["site"])
    return h, TEPMapping.from_config(cfg["tep_mapping"], h)


# ------------------------------------------------------------------ 2. XMEAS mapping
@pytest.mark.parametrize("idx,equipment,prop,area,unit", [
    (1, "EM-FEED-A", "flow", "AREA-FEED", "kscmh"),
    (2, "EM-FEED-D", "flow", "AREA-FEED", "kg/h"),
    (7, "EM-REACTOR", "pressure", "AREA-REACTION", "kPa gauge"),
    (9, "EM-REACTOR", "temperature", "AREA-REACTION", "degC"),
    (12, "EM-SEPARATOR", "level", "AREA-SEPARATION", "%"),
    (17, "EM-STRIPPER", "product_flow", "AREA-STRIPPING", "m3/h"),
    (19, "EM-STRIPPER-STEAM", "steam_flow", "AREA-STRIPPING", "kg/h"),
    (20, "EM-COMPRESSOR", "power", "AREA-RECYCLE", "kW"),
    (21, "EM-RX-COOLING", "cw_outlet_temperature", "AREA-REACTION", "degC"),
    (30, "CM-AT-PURGE", "composition_B", "AREA-RECYCLE", "mol%"),
    (40, "CM-AT-PRODUCT", "composition_G", "AREA-STRIPPING", "mol%"),
])
def test_xmeas_mapping(model, idx, equipment, prop, area, unit):
    _, m = model
    b = m.xmeas[idx]
    assert (b.equipment_id, b.property, b.area_id, b.unit) == (equipment, prop, area, unit)


def test_every_variable_is_mapped_and_is_not_equipment(model):
    h, m = model
    assert sorted(m.xmeas) == list(range(1, 42))
    assert sorted(m.xmv) == list(range(1, 13))
    assert sorted(m.idv) == list(range(1, 21))
    for eid in h.elements:
        assert "XMEAS" not in eid and "XMV" not in eid and "IDV" not in eid
    for b in m.xmeas.values():
        assert b.unit == catalog.variable(b.var_id).unit


def test_sampled_measurements_are_on_analyzers(model):
    h, m = model
    for idx in range(23, 42):
        assert h.get(m.xmeas[idx].equipment_id).equipment_class == "online_analyzer"
        v = catalog.XMEAS[idx - 1]
        assert v.measurement_type.value == "sampled"
    assert catalog.XMEAS[36].sample_period_h == 0.25 and catalog.XMEAS[22].sample_period_h == 0.1


# ------------------------------------------------------------------ 3. XMV mapping
@pytest.mark.parametrize("idx,cm,process_eq,name", [
    (1, "CM-FV-01", "EM-FEED-D", "D Feed Flow"),
    (3, "CM-FV-03", "EM-FEED-A", "A Feed Flow"),
    (9, "CM-SV-09", "EM-STRIPPER-STEAM", "Stripper Steam Valve"),
    (10, "CM-TV-10", "EM-RX-COOLING", "Reactor Cooling Water Flow"),
    (11, "CM-CV-11", "EM-CONDENSER", "Condenser Cooling Water Flow"),
    (12, "CM-SC-12", "EM-AGITATOR", "Agitator Speed"),
])
def test_xmv_mapping(model, idx, cm, process_eq, name):
    h, m = model
    b = m.xmv[idx]
    assert (b.equipment_id, b.process_equipment_id, b.name) == (cm, process_eq, name)
    assert h.get(cm).level == EquipmentLevel.CONTROL_MODULE
    assert h.get(cm).attributes["xmv"] == idx


def test_loops_map_to_control_modules_under_final_element_or_pv(model):
    h, m = model
    for lid, loop in cs.LOOPS.items():
        cm = m.loops[lid]
        assert h.get(cm).level == EquipmentLevel.CONTROL_MODULE
        if loop.output_kind == "XMV":           # under the EM owning the valve
            assert h.get(cm).parent_id == h.get(m.xmv[loop.output_index].equipment_id).parent_id


# ------------------------------------------------------------------ 4. IDV mapping
@pytest.mark.parametrize("idx,equipment", [(1, "EM-FEED-AC"), (3, "EM-FEED-D"), (4, "EM-RX-COOLING"),
                                           (5, "EM-CONDENSER"), (6, "EM-FEED-A"), (13, "EM-REACTOR"),
                                           (14, "CM-TV-10"), (15, "CM-CV-11")])
def test_idv_mapping(model, idx, equipment):
    _, m = model
    assert equipment in m.idv[idx]
    assert catalog.IDV[idx - 1].kind.value == "IDV"


def test_idv_catalog_matches_teprob():
    assert "Reactor Cooling Water Inlet Temperature" in catalog.IDV[3].name
    assert catalog.IDV_TYPES[14] == "Sticking" and catalog.IDV_TYPES[8] == "Random Variation"


# ------------------------------------------------------------------ 5. ISA-95 hierarchy
def test_hierarchy_is_valid(model):
    h, _ = model
    assert h.root.id == "ENT-ACME" and h.root.name == "ACME Manufacturing"
    sites = h.by_level(EquipmentLevel.SITE)
    assert [s.name for s in sites] == ["Tennessee Eastman Manufacturing Site"]
    areas = {a.id for a in h.by_level(EquipmentLevel.AREA)}
    assert {"AREA-FEED", "AREA-REACTION", "AREA-SEPARATION", "AREA-RECYCLE", "AREA-STRIPPING", "AREA-UTILITIES"} <= areas
    assert h.area_of("CM-TIC-RX") == "AREA-REACTION"
    assert h.path("CM-TV-10")[:4] == ["ENT-ACME", "SITE-TE", "AREA-REACTION", "PU-REACTOR"]
    levels = {e.level for e in h.elements.values()}
    for lv in (EquipmentLevel.PRODUCTION_UNIT, EquipmentLevel.EQUIPMENT_MODULE, EquipmentLevel.CONTROL_MODULE,
               EquipmentLevel.STORAGE_ZONE, EquipmentLevel.STORAGE_UNIT, EquipmentLevel.WORK_CENTER,
               EquipmentLevel.WORK_UNIT):
        assert lv in levels


def test_hierarchy_rejects_invalid_containment():
    cfg = copy.deepcopy(base_config()["site"])
    area = cfg["enterprise"]["children"][0]["children"][0]
    area["children"].append({"id": "BAD", "name": "bad", "level": "WorkUnit"})   # WorkUnit directly in Area
    with pytest.raises(ConfigError, match="ISA-95 violation"):
        Hierarchy.from_config(cfg)


def test_hierarchy_rejects_duplicates_and_bad_levels():
    cfg = copy.deepcopy(base_config()["site"])
    cfg["enterprise"]["children"][0]["children"][0]["id"] = "AREA-REACTION"
    with pytest.raises(ConfigError):
        Hierarchy.from_config(cfg)
    cfg = copy.deepcopy(base_config()["site"])
    cfg["enterprise"]["children"][0]["level"] = "Factory"
    with pytest.raises(ConfigError):
        Hierarchy.from_config(cfg)


# ------------------------------------------------------------------ 6. clock / events
def test_clock_is_deterministic():
    c = SimulationClock.from_config("2026-01-05T06:00:00Z")
    assert c.timestamp() == "2026-01-05T06:00:00Z"
    c.advance(3661)
    assert c.time_s == 3661 and c.timestamp() == "2026-01-05T07:01:01Z"
    assert c.to_dict()["time_hms"] == "01:01:01"
    with pytest.raises(ValueError):
        c.advance(-1)
    assert parse_datetime("2026-01-05T06:00:00").tzinfo is not None
    for s in (0.1, 1, 5, 10, 100):
        assert validate_speed(s) == s
    with pytest.raises(ValueError):
        validate_speed(0)


def test_event_bus_fields_ids_and_causation():
    c = SimulationClock.from_config("2026-01-05T06:00:00Z")
    bus = EventBus(c)
    seen = []
    bus.subscribe(seen.append, [EventType.ALARM_ACTIVATED])
    e1 = bus.publish(EventType.FAULT_STARTED, "bench", "X", {"a": 1}, correlation_id="F-1")
    c.advance(5)
    e2 = bus.publish(EventType.ALARM_ACTIVATED, "alarms", "Y", {}, cause=e1)
    lc = bus.publish(EventType.SIMULATION_PAUSED, "simulation", "SITE-TE")
    e3 = bus.publish(EventType.ALARM_CLEARED, "alarms", "Y", {})
    assert (e1.event_id, e2.event_id, e3.event_id) == ("EV-0000001", "EV-0000002", "EV-0000003")
    assert lc.event_id.startswith("LC-")                    # lifecycle ids never shift the trace
    assert e2.causation_id == e1.event_id and e2.correlation_id == "F-1"
    assert e2.simulation_time == 5 and e2.timestamp == "2026-01-05T06:00:05Z"
    assert e1.visibility == Visibility.BENCHMARK and e2.visibility == Visibility.OPERATIONAL
    assert seen == [e2]
    for k in ("event_id", "timestamp", "simulation_time", "type", "source", "target", "payload",
              "correlation_id", "causation_id"):
        assert k in e2.to_dict()
    assert [e.event_id for e in bus.query(include_benchmark=False, types=["ALARM_ACTIVATED"])] == ["EV-0000002"]
