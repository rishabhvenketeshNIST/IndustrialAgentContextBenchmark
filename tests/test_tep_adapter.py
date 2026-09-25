"""Validation 1: the TEP adapter reproduces the native baseline behaviour."""
import numpy as np
import pytest

from simulator.tep import catalog, create_adapter
from simulator.tep import control_scheme as cs
from simulator.tep.boundary import BOUNDARY_PARAMETERS
from simulator.tep.interface import TEPAdapterError
from tests.conftest import requires_fortran

BACKENDS = [pytest.param("fortran", marks=requires_fortran), "python"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_initial_state_matches_downs_vogel_base_case(backend):
    a = create_adapter(backend)
    a.initialize(tep_seed=12345)
    x = a.get_measurements()
    for v in catalog.XMEAS:
        assert x[v.index - 1] == pytest.approx(v.base_value, rel=1e-3, abs=1e-3), v.id
    assert a.get_states().shape == (50,)
    assert a.get_manipulated_variables().shape == (12,)
    assert not a.is_shutdown()


@pytest.mark.parametrize("backend", BACKENDS)
def test_boundary_parameters_equal_teinit(backend):
    a = create_adapter(backend)
    a.initialize()
    bp = a.get_boundary_parameters()
    for name, p in BOUNDARY_PARAMETERS.items():
        assert bp[name] == pytest.approx(p.nominal), name


@requires_fortran
def test_closed_loop_holds_base_case_for_two_hours():
    a = create_adapter("fortran")
    a.initialize(tep_seed=4651207995)
    xs = []
    for _ in range(7200):
        a.step()
        xs.append(a.get_measurements())
    xs = np.array(xs)[3600:]
    assert not a.is_shutdown()
    assert xs[:, 8].mean() == pytest.approx(120.40, abs=0.1)        # reactor temperature
    assert xs[:, 6].mean() == pytest.approx(2705.0, abs=25.0)       # reactor pressure
    assert xs[:, 7].mean() == pytest.approx(75.0, abs=1.5)          # reactor level
    assert xs[:, 11].mean() == pytest.approx(50.0, abs=5.0)         # separator level
    assert xs[:, 14].mean() == pytest.approx(50.0, abs=5.0)         # stripper level
    assert a.get_time() == pytest.approx(2.0, abs=1e-5)


@requires_fortran
def test_fortran_is_deterministic_and_seed_dependent():
    def run(seed):
        a = create_adapter("fortran")
        a.initialize(tep_seed=seed)
        a.step(600)
        return a.get_measurements(), a.get_states()
    m1, s1 = run(1001)
    m2, s2 = run(1001)
    m3, _ = run(2002)
    assert np.array_equal(m1, m2) and np.array_equal(s1, s2)
    assert not np.array_equal(m1, m3)


@requires_fortran
def test_python_backend_statistically_matches_fortran():
    res = {}
    for b in ("fortran", "python"):
        a = create_adapter(b)
        a.initialize(tep_seed=12345)
        xs = []
        for _ in range(1800):
            a.step()
            xs.append(a.get_measurements())
        res[b] = np.array(xs)[600:].mean(axis=0)
    for i in (6, 7, 8, 11, 14, 16, 20):
        assert res["python"][i] == pytest.approx(res["fortran"][i], rel=0.01), f"XMEAS({i + 1})"


def test_native_control_scheme_transcription():
    assert len(cs.LOOPS) == 19
    assert sorted(cs.EXECUTION_ORDER) == sorted(cs.LOOPS)
    assert cs.DELTAT_H == pytest.approx(1 / 3600, rel=1e-7)
    assert cs.LOOPS[18].output_kind == "SETPT" and cs.LOOPS[18].output_index == 10   # TC-RX -> TC-RCW
    assert cs.LOOPS[10].output_index == 10 and cs.LOOPS[10].pv == 21
    assert cs.cascade_parent(10) == 18 and cs.cascade_parent(18) is None
    assert {cs.LOOPS[i].period_steps for i in (13, 14, 15, 19)} == {360}
    assert cs.LOOPS[20].period_steps == 900


@pytest.mark.parametrize("backend", BACKENDS)
def test_setpoint_measurement_and_output_are_distinct(backend):
    a = create_adapter(backend)
    a.initialize()
    a.step(30)
    loops = {l["loop_id"]: l for l in a.get_loop_states()}
    l18 = loops[18]
    assert l18["pv_id"] == "XMEAS(9)" and l18["output_id"] == "SETPT(10)"
    assert l18["setpoint"] == pytest.approx(120.40, abs=1e-4)
    assert loops[10]["setpoint"] == pytest.approx(l18["output"])       # cascade: master output = slave SP
    with pytest.raises(TEPAdapterError):
        a.set_setpoint(10, 90.0)                                        # remote setpoint is protected
    with pytest.raises(TEPAdapterError):
        a.set_manipulated_variable(10, 50.0)                            # XMV owned by an AUTO loop
    a.set_setpoint(18, 121.0)
    assert a.get_setpoints()[18] == 121.0


@pytest.mark.parametrize("backend", BACKENDS)
def test_manual_mode_freezes_controller_outputs(backend):
    a = create_adapter(backend)
    a.initialize()
    a.set_control_mode(cs.ControlMode.MANUAL)
    xmv0 = a.get_manipulated_variables()
    a.step(300)
    assert np.array_equal(a.get_manipulated_variables(), xmv0)
    a.set_manipulated_variable(10, 55.0)
    assert a.get_manipulated_variables()[9] == 55.0
    a.set_control_mode(cs.ControlMode.CLOSED_LOOP)
    a.step(30)
    assert a.get_loop_modes()[18] == cs.LoopMode.AUTO


@requires_fortran
def test_measurement_filter_does_not_touch_true_process():
    a, b = create_adapter("fortran"), create_adapter("fortran")
    a.initialize(tep_seed=99)
    b.initialize(tep_seed=99)
    for _ in range(120):
        a.step(1)
        b.step(1, measurement_filter=lambda x: x)            # identity filter
    assert np.array_equal(a.get_measurements(), b.get_measurements())


@requires_fortran
def test_idv_disturbance_changes_trajectory():
    a, b = create_adapter("fortran"), create_adapter("fortran")
    for x in (a, b):
        x.initialize(tep_seed=5)
    b.set_disturbance(1, True)
    assert b.get_disturbances()[0] == 1
    a.step(3600)
    b.step(3600)
    # IDV(1) lowers the A/C ratio of stream 4: the A-feed cascade (AC-RFA -> FC-A) raises A feed
    assert b.get_measurements()[0] - a.get_measurements()[0] > 0.1


@requires_fortran
def test_shutdown_detected_with_native_limits():
    a = create_adapter("fortran")
    a.initialize()
    a.set_boundary_parameter("reactor_cw_max_flow", 0.0)   # total loss of reactor cooling water
    for _ in range(3 * 3600):
        a.step(1)
        if a.is_shutdown():
            break
    assert a.is_shutdown()
    reason = a.get_shutdown_reason()
    assert "Reactor" in reason
    frozen = a.get_states()
    a.step(60)
    assert np.array_equal(frozen, a.get_states())          # TEFUNC zeroes all derivatives after ISD
