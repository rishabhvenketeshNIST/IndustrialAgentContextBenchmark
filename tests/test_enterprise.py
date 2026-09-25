"""Validation 7-12: faults, degradation propagation, maintenance, production, quality, inventory."""
import numpy as np
import pytest

from simulator.events import EventType
from simulator.faults.types import FaultValidationError
from simulator.production.orders import transition
from tests.conftest import make_engine


def types_of(e, since=0):
    return [ev.type for ev in e.bus.log if ev.simulation_time >= since]


# ------------------------------------------------------------------ 7. fault engine
def test_fault_lifecycle_and_validation():
    e = make_engine()
    fe = e.faults
    f = fe.create_fault({"id": "F1", "type": "utility_capacity_loss", "target": "UT-STEAM", "severity": 0.5,
                         "trigger": {"mode": "simulation_time", "time": 60}, "duration": 120})
    assert f.status == "SCHEDULED" and f.category == "ENTERPRISE_FAULT"
    e.step(59)
    assert f.status == "SCHEDULED"
    e.step(2)
    assert f.status == "ACTIVE" and e.state.fault_effects.value("UT-STEAM", "capacity_loss") == 0.5
    e.step(130)
    assert f.status == "STOPPED" and e.state.fault_effects.value("UT-STEAM", "capacity_loss") == 0.0
    fe.reset_fault("F1")
    assert f.status == "RESET"
    evs = [ev.type for ev in e.bus.log if ev.payload.get("fault_id") == "F1"]
    assert evs == [EventType.FAULT_CREATED, EventType.FAULT_SCHEDULED, EventType.FAULT_STARTED,
                   EventType.FAULT_STOPPED, EventType.FAULT_RESET]
    for bad in [{"id": "B1", "type": "IDV4", "target": "TEP", "progression": "gradual", "duration": 60},
                {"id": "B2", "type": "IDV4", "target": "TEP", "severity": 0.5},
                {"id": "B3", "type": "no_such_type", "target": "TEP"},
                {"id": "B4", "type": "equipment_failure", "target": "NOT-AN-ASSET"},
                {"id": "B5", "type": "utility_capacity_loss", "target": "UT-STEAM", "severity": 2}]:
        with pytest.raises(FaultValidationError):
            fe.create_fault(bad)


def test_tep_native_fault_only_toggles_idv():
    e = make_engine()
    e.faults.create_fault({"id": "F-IDV4", "type": "IDV4", "target": "TEP", "trigger": {"mode": "simulation_time", "time": 10},
                           "duration": 100})
    e.step(11)
    assert list(e.state.process.idv).count(1) == 1 and e.state.process.idv[3] == 1
    assert e.process.boundary_nominal == e.state.process.boundary      # no boundary parameter touched
    e.step(110)
    assert e.state.process.idv[3] == 0


def test_sensor_bias_changes_transmitted_not_true_value():
    e = make_engine()
    e.faults.create_fault({"id": "F-S", "type": "sensor_bias", "target": "XMEAS(21)", "parameters": {"bias": 3.0}})
    e.step(30)
    e.faults.start_fault("F-S")
    e.step(1)
    p = e.state.process
    assert p.xmeas[20] - p.xmeas_true[20] == pytest.approx(3.0)
    assert e.state.get("EM-RX-COOLING", "cw_outlet_temperature") == pytest.approx(p.xmeas[20])
    e.step(1800)   # TC-RCW acts on the biased value -> true outlet temperature moves ~3 degC below setpoint
    assert np.mean([e.state.process.xmeas_true[20]]) < e.state.process.setpoints[10] - 1.5
    e.faults.stop_fault("F-S")
    e.step(1)
    assert e.state.process.xmeas[20] == e.state.process.xmeas_true[20]


def test_fault_injection_is_deterministic():
    def run():
        e = make_engine(seed=11, duration_seconds=2400, faults=[
            {"id": "FA", "type": "pump_efficiency_loss", "target": "WU-CWP-201A", "severity": 0.5,
             "trigger": {"mode": "simulation_time", "time": 300}, "progression": {"mode": "gradual", "ramp_s": 600}},
            {"id": "FB", "type": "sensor_drift", "target": "XMEAS(7)", "severity": 0.2,
             "trigger": {"mode": "event", "event_type": "UTILITY_STATE_CHANGED", "delay_s": 60}},
            {"id": "FC", "type": "IDV8", "target": "TEP", "trigger": {"mode": "simulation_time", "time": 600},
             "progression": {"mode": "intermittent", "period_s": 300, "duty": 0.5}, "duration": 1200}])
        e.run()
        return [(ev.event_id, ev.simulation_time, ev.type.value, ev.target) for ev in e.bus.log], \
            e.state.process.xmeas_true.copy(), e.faults.faults["FC"].status
    a, b = run(), run()
    assert a[0] == b[0] and np.array_equal(a[1], b[1])
    assert a[2] == "STOPPED"


# ------------------------------------------------------------------ 8. degradation propagation
def test_equipment_degradation_propagates_through_coupling_to_tep():
    e = make_engine(duration_seconds=5400, faults=[
        {"id": "F-D", "type": "equipment_degradation", "target": "WU-CWP-101A", "severity": 0.5,
         "trigger": {"mode": "simulation_time", "time": 60}}])
    e.run_until(59)
    xmv10_before = e.state.process.xmv[9]
    assert e.state.process.boundary["reactor_cw_max_flow"] == 1000.0
    e.run_until(1200)          # before the maintenance response (vibration alarm -> WO -> standby pump)
    st = e.state
    assert st.get("WU-CWP-101A", "health") == pytest.approx(0.97 - 0.5, abs=0.002)   # cause
    assert st.get("WU-CWP-101A", "efficiency") < 0.5                                   # equipment capability
    frac = st.get("UT-CW-REACTOR", "capacity_fraction")
    assert frac < 0.55                                                                 # utility capacity
    assert st.process.boundary["reactor_cw_max_flow"] == pytest.approx(1000.0 * frac)  # TEP boundary
    assert st.process.xmv[9] > xmv10_before + 20                                        # native controller response
    assert st.process.xmeas_true[8] == pytest.approx(120.4, abs=1.5)                   # still controlled
    # causal context reaches the process measurements
    assert st.causal.get("XMEAS(9)").correlation_id == "F-D"
    ev = [x for x in e.bus.log if x.type == EventType.UTILITY_STATE_CHANGED][0]
    assert ev.correlation_id == "F-D"
    e.run_until(5400)          # maintenance switched to the standby pump and repaired P-101A
    assert st.get("UT-CW-REACTOR", "capacity_fraction") == 1.0
    assert st.get("WU-CWP-101B", "status") == "RUNNING"


def test_coupling_baseline_is_native_and_graph_is_acyclic(engine):
    assert engine.state.process.boundary == engine.process.boundary_nominal
    order = [r.output.key for r in engine.coupling.relations]
    for i, r in enumerate(engine.coupling.relations):
        for ref in r.inputs.values():
            if ref.key in order:
                assert order.index(ref.key) < i
    g = engine.coupling.graph()
    assert any(n["id"] == "tep.boundary.reactor_cw_max_flow" for n in g["nodes"])


def test_power_loss_cascades_to_pumps_and_compressor():
    e = make_engine(duration_seconds=600, faults=[
        {"id": "F-P", "type": "utility_capacity_loss", "target": "UT-POWER", "severity": 0.75,
         "trigger": {"mode": "simulation_time", "time": 10}}])
    e.run_until(30)
    st = e.state
    assert st.get("UT-POWER", "process_supply_fraction") < 1.0
    assert st.get("WU-MCC-401", "supply_fraction") < 1.0
    assert st.process.boundary["compressor_max_flow"] < 280275.0


# ------------------------------------------------------------------ 9. maintenance
def test_failure_changeover_and_maintenance_recovery():
    e = make_engine(duration_seconds=7200, faults=[
        {"id": "F-X", "type": "equipment_failure", "target": "WU-CWP-201A",
         "trigger": {"mode": "simulation_time", "time": 60}}])
    e.run_until(62)
    st = e.state
    assert st.get("WU-CWP-201A", "status") == "FAILED"
    assert st.get("UT-CW-CONDENSER", "capacity_fraction") == 0.0
    e.run_until(120)
    assert st.get("WU-CWP-201B", "status") == "RUNNING"                  # automatic changeover
    assert st.get("UT-CW-CONDENSER", "capacity_fraction") == 1.0
    wos = [w for w in st.records("work_orders") if w.asset_id == "WU-CWP-201A"]
    assert len(wos) == 1 and wos[0].priority == 1
    parts_before = st.collection("spare_parts")["SP-IMP-201"].on_hand
    e.run_until(7200)
    wo = wos[0]
    assert wo.status == "COMPLETED"
    assert st.collection("spare_parts")["SP-IMP-201"].on_hand == parts_before - 1
    assert st.get("WU-CWP-201A", "status") == "STANDBY"                  # repaired, standby unit keeps running
    assert st.get("WU-CWP-201A", "health") == pytest.approx(0.98)
    assert e.faults.faults["F-X"].remediated
    t = types_of(e)
    for et in (EventType.EQUIPMENT_FAILED, EventType.MAINTENANCE_REQUESTED, EventType.MAINTENANCE_SCHEDULED,
               EventType.MAINTENANCE_STARTED, EventType.MAINTENANCE_COMPLETED, EventType.EQUIPMENT_REPAIRED):
        assert et in t


def test_spare_part_shortage_blocks_work_order():
    e = make_engine(duration_seconds=1200, faults=[
        {"id": "F-SP", "type": "spare_part_shortage", "target": "SP-IMP-101", "trigger": {"mode": "simulation_time", "time": 0}}])
    e.step(5)
    wo = e.maintenance.request("WU-CWP-101B", "corrective", 2, "test")
    e.step(10)
    assert wo.status == "WAITING_PARTS"
    e.faults.stop_fault("F-SP")
    e.step(5)
    assert wo.status == "SCHEDULED"


# ------------------------------------------------------------------ 10. production orders
def test_production_order_state_machine(engine):
    e = engine
    o = e.state.collection("production_orders")["PO-T1"]
    assert o.status == "PLANNED"
    e.step(2)
    assert o.status == "RUNNING" and o.actual_start is not None
    with pytest.raises(ValueError):
        transition(e.ctx, o, "PLANNED", "illegal")
    e.operator.execute("order_command", {"order_id": "PO-T1", "command": "pause"})
    assert o.status == "PAUSED"
    e.step(60)
    assert o.status == "PAUSED"
    e.operator.execute("order_command", {"order_id": "PO-T1", "command": "resume"})
    e.run_until(3600)
    assert o.status == "COMPLETED" and o.net_kg >= o.quantity
    seq = [ev.type for ev in e.bus.log if ev.target == "PO-T1"]
    assert seq[:3] == [EventType.PRODUCTION_ORDER_CREATED, EventType.PRODUCTION_ORDER_RELEASED,
                       EventType.PRODUCTION_ORDER_STARTED]
    assert EventType.PRODUCTION_ORDER_PAUSED in seq and EventType.PRODUCTION_ORDER_COMPLETED in seq


def test_order_blocked_by_material_shortage():
    e = make_engine(duration_seconds=1800, faults=[
        {"id": "F-M", "type": "raw_material_shortage", "target": "MAT-D", "severity": 1.0,
         "trigger": {"mode": "simulation_time", "time": 0}}],
        production_orders=[{"order_id": "PO-B", "product_id": "PROD-GH-M1", "quantity": 5000,
                            "planned_start": 30, "planned_end": 3600}])
    e.run_until(60)
    st = e.state
    assert st.get("SU-TK-101", "supply_availability") == 0.0
    assert st.process.boundary["d_feed_max_flow"] == 0.0              # D feed line has no supply
    assert st.collection("production_orders")["PO-B"].status == "BLOCKED"
    assert EventType.MATERIAL_SHORTAGE in types_of(e)


# ------------------------------------------------------------------ 11. quality
def test_quality_calculation_from_tep_composition(engine):
    e = engine
    e.run_until(3600)
    samples = [s for s in e.state.records("quality_samples") if s.subject_type == "product"]
    assert len(samples) >= 3
    s = samples[-1]
    r = {x["test_id"]: x for x in s.results}
    p = e.state.process
    G, H = p.xmeas[39], p.xmeas[40]
    assert r["G_MASS_PCT"]["value"] == pytest.approx(100 * 62 * G / (62 * G + 76 * H), abs=1e-4)
    assert 47.5 < r["G_MASS_PCT"]["value"] < 52.5 and s.overall == "PASS"
    assert s.lot_id is not None


def test_quality_failure_fault_rejects_lot():
    e = make_engine(duration_seconds=6000, faults=[
        {"id": "F-Q", "type": "quality_failure", "target": "G_MASS_PCT", "severity": 1.0,
         "trigger": {"mode": "simulation_time", "time": 0}}])
    e.run()
    lots = e.state.records("production_lots")
    assert lots[0].status == "REJECTED"
    assert e.state.production["rejected_kg"] > 0
    assert all(s.overall == "FAIL" for s in e.state.records("quality_samples") if s.subject_type == "product")
    assert e.state.collection("alarms")["QA-OFFSPEC"].count >= 1


# ------------------------------------------------------------------ 12. inventory
def test_inventory_consumption_matches_metered_feed(engine):
    e = engine
    q0 = e.state.get("SU-TK-102", "quantity_kg")
    integral = 0.0
    for _ in range(1800):
        e.step(1)
        integral += e.state.process.xmeas_true[2] / 3600.0          # E feed kg/h (XMEAS 3)
    assert q0 - e.state.get("SU-TK-102", "quantity_kg") == pytest.approx(integral, abs=0.01)
    lots = [l for l in e.state.records("material_lots") if l.material_id == "MAT-D"]
    assert lots[0].consumed_kg > 0 and lots[1].consumed_kg == 0     # FIFO
    assert EventType.MATERIAL_CONSUMED in types_of(e)


def test_replenishment_orders_and_receives_material():
    e = make_engine(duration_seconds=10800)
    e.run()
    pos = [p for p in e.state.records("purchase_orders") if p.item_id == "MAT-D"]
    assert pos and pos[0].status == "RECEIVED"
    new = [l for l in e.state.records("material_lots") if l.lot_id.startswith("LOT-D-R")]
    assert new and new[0].quality_status in ("RELEASED", "CONSUMED")    # passed incoming inspection
    t = types_of(e)
    assert EventType.MATERIAL_ORDERED in t and EventType.MATERIAL_RECEIVED in t
