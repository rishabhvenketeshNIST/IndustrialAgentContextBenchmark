"""TEPProcessAdapter - the only interface the enterprise simulator uses to reach TEP.

Backends implement a small set of primitives (initialize the Fortran/Python
model, integrate one 1-second step, call one native controller, read and write
common-block data). Everything that is common to both backends - the native
controller schedule, loop modes, the measurement hook used by the
instrumentation layer, boundary-parameter bookkeeping and shutdown detection -
lives in :class:`BaseTEPAdapter` so that the two backends behave identically.

No enterprise logic belongs here.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from . import control_scheme as cs
from .boundary import BOUNDARY_PARAMETERS, BoundaryParameter, get_parameter
from .catalog import NUM_XMEAS, NUM_XMV, SHUTDOWN_LIMITS

# Callable used by the instrumentation layer: receives the true XMEAS vector
# (41,) and returns what the transmitters report to the control system.
MeasurementFilter = Callable[[np.ndarray], np.ndarray]

DEFAULT_TEP_SEED = 4651207995.0  # value assigned to G by TEINIT in teprob.f


class TEPAdapterError(RuntimeError):
    pass


@dataclass
class StepResult:
    steps: int
    time_s: int
    shutdown: bool
    shutdown_reason: Optional[str]
    controllers_executed: List[int] = field(default_factory=list)


@dataclass
class ProcessInternals:
    """Values of the TEFUNC common block used for shutdown detection (noise-free)."""
    reactor_pressure_kpa: float
    reactor_temperature_c: float
    reactor_liquid_m3: float
    separator_liquid_m3: float
    stripper_liquid_m3: float


class TEPProcessAdapter(abc.ABC):
    """Abstract TEP process interface consumed by the simulation engine."""

    backend_name: str = "abstract"

    # lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    def initialize(self, tep_seed: Optional[float] = None) -> None: ...

    @abc.abstractmethod
    def reset(self) -> None: ...

    @abc.abstractmethod
    def step(self, n: int = 1, measurement_filter: Optional[MeasurementFilter] = None) -> StepResult: ...

    # process data ------------------------------------------------------
    @abc.abstractmethod
    def get_measurements(self) -> np.ndarray: ...

    @abc.abstractmethod
    def get_manipulated_variables(self) -> np.ndarray: ...

    @abc.abstractmethod
    def get_states(self) -> np.ndarray: ...

    @abc.abstractmethod
    def get_disturbances(self) -> np.ndarray: ...

    @abc.abstractmethod
    def set_manipulated_variable(self, index: int, value: float) -> None: ...

    @abc.abstractmethod
    def set_disturbance(self, index: int, active: bool) -> None: ...

    @abc.abstractmethod
    def get_time(self) -> float:
        """Simulation time in hours (the TEP TIME variable)."""

    @abc.abstractmethod
    def get_time_seconds(self) -> int: ...

    @abc.abstractmethod
    def is_shutdown(self) -> bool: ...

    @abc.abstractmethod
    def get_shutdown_reason(self) -> Optional[str]: ...

    # control -----------------------------------------------------------
    @abc.abstractmethod
    def get_control_mode(self) -> cs.ControlMode: ...

    @abc.abstractmethod
    def set_control_mode(self, mode: cs.ControlMode) -> None: ...

    @abc.abstractmethod
    def set_loop_mode(self, loop_id: int, mode: cs.LoopMode) -> None: ...

    @abc.abstractmethod
    def get_loop_modes(self) -> Dict[int, cs.LoopMode]: ...

    @abc.abstractmethod
    def get_setpoints(self) -> Dict[int, float]: ...

    @abc.abstractmethod
    def set_setpoint(self, loop_id: int, value: float) -> None: ...

    @abc.abstractmethod
    def get_loop_states(self) -> List[dict]: ...

    # boundary ----------------------------------------------------------
    @abc.abstractmethod
    def get_boundary_parameters(self) -> Dict[str, float]: ...

    @abc.abstractmethod
    def set_boundary_parameter(self, name: str, value: float) -> None: ...

    def get_boundary_parameter_definitions(self) -> Dict[str, BoundaryParameter]:
        return dict(BOUNDARY_PARAMETERS)

    def version_info(self) -> dict:
        return {"backend": self.backend_name}


class BaseTEPAdapter(TEPProcessAdapter):
    """Shared behaviour. Subclasses provide the backend primitives."""

    def __init__(self) -> None:
        self._initialized = False
        self._step_count = 0
        self._tep_seed = DEFAULT_TEP_SEED
        self._control_mode = cs.ControlMode.CLOSED_LOOP
        self._loop_modes: Dict[int, cs.LoopMode] = {}
        self._shutdown_reason: Optional[str] = None
        self._reset_loop_modes()

    # ---- primitives each backend must provide --------------------------
    @abc.abstractmethod
    def _native_init(self) -> None:
        """Call TEINIT (resets every common block to its TEINIT values)."""

    @abc.abstractmethod
    def _set_seed(self, g: float) -> None: ...

    @abc.abstractmethod
    def _integrate_one_second(self) -> None:
        """INTGTR (TEFUNC + Euler) followed by CONSHAND, exactly as the native main loop."""

    @abc.abstractmethod
    def _run_native_controller(self, loop_id: int) -> None: ...

    @abc.abstractmethod
    def _xmeas_array(self) -> np.ndarray:
        """A *view* (writable) of XMEAS(1..41)."""

    @abc.abstractmethod
    def _xmv_array(self) -> np.ndarray:
        """A *view* (writable) of XMV(1..12)."""

    @abc.abstractmethod
    def _setpt_array(self) -> np.ndarray:
        """A *view* (writable) of SETPT(1..20)."""

    @abc.abstractmethod
    def _set_controller_params(self, loop_id: int, gain: float, taui_h: Optional[float], errold: float) -> None: ...

    @abc.abstractmethod
    def _get_errold(self, loop_id: int) -> float: ...

    @abc.abstractmethod
    def _set_errold(self, loop_id: int, value: float) -> None: ...

    @abc.abstractmethod
    def _set_purge_flag(self, value: int) -> None: ...

    @abc.abstractmethod
    def _get_purge_flag(self) -> int: ...

    @abc.abstractmethod
    def _get_boundary(self, p: BoundaryParameter) -> float: ...

    @abc.abstractmethod
    def _set_boundary(self, p: BoundaryParameter, value: float) -> None: ...

    @abc.abstractmethod
    def _internals(self) -> ProcessInternals: ...

    # ---- lifecycle -------------------------------------------------------
    def initialize(self, tep_seed: Optional[float] = None) -> None:
        if tep_seed is not None:
            g = float(int(tep_seed) % 4294967296)
            if g == 0.0:
                raise TEPAdapterError("TEP seed must not be congruent to 0 mod 2^32 (degenerate LCG)")
            self._tep_seed = g
        self._native_init()
        # TEINIT assigns its own G; override afterwards exactly like the
        # upstream Python wrapper does.
        self._set_seed(self._tep_seed)
        self._load_native_controller_configuration()
        self._step_count = 0
        self._shutdown_reason = None
        self._control_mode = cs.ControlMode.CLOSED_LOOP
        self._reset_loop_modes()
        self._initialized = True

    def reset(self) -> None:
        self.initialize(self._tep_seed)

    def _load_native_controller_configuration(self) -> None:
        """Replicates the assignments of temain_mod.f MAIN before the simulation loop."""
        sp = self._setpt_array()
        sp[:] = 0.0
        for loop in cs.LOOPS.values():
            sp[loop.sp - 1] = loop.initial_setpoint
            self._set_controller_params(loop.loop_id, loop.gain, loop.taui_h, 0.0)
        u = cs.UNUSED_LOOP_22
        sp[u["setpt_index"] - 1] = u["setpoint"]
        self._set_controller_params(22, u["gain"], u["taui_h"], 0.0)
        xmv = self._xmv_array()
        for idx, value in cs.INITIAL_XMV.items():
            xmv[idx - 1] = value
        self._set_purge_flag(0)

    def _reset_loop_modes(self) -> None:
        self._loop_modes = {}
        for lid in cs.LOOPS:
            self._loop_modes[lid] = cs.LoopMode.CASCADE if cs.cascade_parent(lid) else cs.LoopMode.AUTO

    def _require_init(self) -> None:
        if not self._initialized:
            raise TEPAdapterError("TEP adapter not initialized; call initialize() first")

    # ---- stepping ------------------------------------------------------
    def _due_loops(self, i: int) -> List[int]:
        due = []
        for lid in cs.EXECUTION_ORDER:
            loop = cs.LOOPS[lid]
            if i % loop.period_steps == 0 and self._loop_executes(lid):
                due.append(lid)
        return due

    def _loop_executes(self, loop_id: int) -> bool:
        if self._control_mode == cs.ControlMode.MANUAL:
            return False
        return self._loop_modes[loop_id] != cs.LoopMode.MANUAL

    def step(self, n: int = 1, measurement_filter: Optional[MeasurementFilter] = None) -> StepResult:
        """Advance ``n`` one-second integration steps.

        Mirrors one iteration of the temain_mod.f simulation loop per step:
        controllers due at step I run first, then INTGTR, then CONSHAND.
        When a ``measurement_filter`` is supplied, the native controllers see
        the filtered (transmitter) values; the true XMEAS vector is restored
        immediately afterwards, so the process model is untouched.
        """
        self._require_init()
        if n < 1:
            raise ValueError("n must be >= 1")
        executed: List[int] = []
        for _ in range(n):
            i = self._step_count + 1
            if not self.is_shutdown():
                due = self._due_loops(i)
                if due:
                    xmeas = self._xmeas_array()
                    saved = None
                    if measurement_filter is not None:
                        saved = xmeas.copy()
                        observed = np.asarray(measurement_filter(saved.copy()), dtype=float)
                        if observed.shape != (NUM_XMEAS,):
                            raise TEPAdapterError("measurement_filter must return 41 values")
                        xmeas[:] = observed
                    try:
                        for lid in due:
                            self._run_native_controller(lid)
                    finally:
                        if saved is not None:
                            xmeas[:] = saved
                    executed.extend(due)
            self._integrate_one_second()
            self._step_count = i
            self._update_shutdown()
        return StepResult(n, self._step_count, self.is_shutdown(), self._shutdown_reason, executed)

    # ---- shutdown --------------------------------------------------------
    def _update_shutdown(self) -> None:
        if self._shutdown_reason is not None:
            return
        reason = self._evaluate_shutdown()
        if reason:
            self._shutdown_reason = reason

    def _evaluate_shutdown(self) -> Optional[str]:
        """Same conditions as TEFUNC (teprob.f 702-710), on noise-free values."""
        p = self._internals()
        L = SHUTDOWN_LIMITS
        if not np.isfinite([p.reactor_pressure_kpa, p.reactor_temperature_c]).all():
            return "Numerical instability"
        if p.reactor_pressure_kpa > L["reactor_pressure_high_kPa"]:
            return "Reactor pressure high (> 3000 kPa)"
        if p.reactor_liquid_m3 > L["reactor_liquid_volume_high_m3"]:
            return "Reactor level high (> 24 m3 liquid)"
        if p.reactor_liquid_m3 < L["reactor_liquid_volume_low_m3"]:
            return "Reactor level low (< 2 m3 liquid)"
        if p.reactor_temperature_c > L["reactor_temperature_high_degC"]:
            return "Reactor temperature high (> 175 degC)"
        if p.separator_liquid_m3 > L["separator_liquid_volume_high_m3"]:
            return "Separator level high (> 12 m3 liquid)"
        if p.separator_liquid_m3 < L["separator_liquid_volume_low_m3"]:
            return "Separator level low (< 1 m3 liquid)"
        if p.stripper_liquid_m3 > L["stripper_liquid_volume_high_m3"]:
            return "Stripper level high (> 8 m3 liquid)"
        if p.stripper_liquid_m3 < L["stripper_liquid_volume_low_m3"]:
            return "Stripper level low (< 1 m3 liquid)"
        return None

    def is_shutdown(self) -> bool:
        return self._shutdown_reason is not None

    def get_shutdown_reason(self) -> Optional[str]:
        return self._shutdown_reason

    # ---- data access -----------------------------------------------------
    def get_measurements(self) -> np.ndarray:
        self._require_init()
        return self._xmeas_array().copy()

    def get_manipulated_variables(self) -> np.ndarray:
        self._require_init()
        return self._xmv_array().copy()

    def set_manipulated_variable(self, index: int, value: float) -> None:
        self._require_init()
        if not 1 <= index <= NUM_XMV:
            raise ValueError(f"XMV index must be 1..{NUM_XMV}, got {index}")
        if not np.isfinite(value):
            raise ValueError("XMV value must be finite")
        loop_id = cs.loop_for_xmv(index)
        if loop_id is not None and self._loop_executes(loop_id):
            raise TEPAdapterError(
                f"XMV({index}) is driven by loop {loop_id} ({cs.LOOPS[loop_id].tag}) in "
                f"{self._loop_modes[loop_id].value}; switch the loop to MAN first")
        self._xmv_array()[index - 1] = float(np.clip(value, 0.0, 100.0))

    def get_time_seconds(self) -> int:
        return self._step_count

    # ---- control ---------------------------------------------------------
    def get_control_mode(self) -> cs.ControlMode:
        return self._control_mode

    def set_control_mode(self, mode: cs.ControlMode) -> None:
        mode = cs.ControlMode(mode)
        if mode == self._control_mode:
            return
        previous_active = {lid for lid in cs.LOOPS if self._loop_executes(lid)}
        self._control_mode = mode
        if mode == cs.ControlMode.CLOSED_LOOP:
            self._reset_loop_modes()
            for lid in cs.LOOPS:
                if lid not in previous_active:
                    self._bumpless_init(lid)

    def set_loop_mode(self, loop_id: int, mode: cs.LoopMode) -> None:
        if loop_id not in cs.LOOPS:
            raise ValueError(f"Unknown loop {loop_id}")
        mode = cs.LoopMode(mode)
        parent = cs.cascade_parent(loop_id)
        if mode == cs.LoopMode.CASCADE and parent is None:
            raise TEPAdapterError(f"Loop {loop_id} has no cascade master")
        if mode == cs.LoopMode.AUTO and parent is not None and self._loop_modes[parent] != cs.LoopMode.MANUAL:
            mode = cs.LoopMode.CASCADE  # setpoint remains remote while master is active
        was_active = self._loop_executes(loop_id)
        self._loop_modes[loop_id] = mode
        if mode == cs.LoopMode.MANUAL:
            # A slave loop whose master goes to MAN keeps executing on a local setpoint.
            for lid, d in cs.LOOPS.items():
                if cs.cascade_parent(lid) == loop_id and self._loop_modes[lid] == cs.LoopMode.CASCADE:
                    self._loop_modes[lid] = cs.LoopMode.AUTO
        elif not was_active and self._loop_executes(loop_id):
            self._bumpless_init(loop_id)
            for lid in cs.LOOPS:
                if cs.cascade_parent(lid) == loop_id and self._loop_modes[lid] == cs.LoopMode.AUTO:
                    self._loop_modes[lid] = cs.LoopMode.CASCADE

    def _bumpless_init(self, loop_id: int) -> None:
        """Initialise ERROLDn to the current error so the velocity-form
        algorithm produces no proportional kick on the MAN->AUTO transition.
        (Enterprise-layer mode handling; the control law itself is unchanged.)"""
        loop = cs.LOOPS[loop_id]
        err = cs.error_for(loop, self._setpt_array()[loop.sp - 1], self._xmeas_array()[loop.pv - 1])
        self._set_errold(loop_id, float(err))

    def get_loop_modes(self) -> Dict[int, cs.LoopMode]:
        return {lid: (cs.LoopMode.MANUAL if self._control_mode == cs.ControlMode.MANUAL else m)
                for lid, m in self._loop_modes.items()}

    def get_setpoints(self) -> Dict[int, float]:
        sp = self._setpt_array()
        return {lid: float(sp[d.sp - 1]) for lid, d in cs.LOOPS.items()}

    def set_setpoint(self, loop_id: int, value: float) -> None:
        self._require_init()
        if loop_id not in cs.LOOPS:
            raise ValueError(f"Unknown loop {loop_id}")
        if not np.isfinite(value):
            raise ValueError("Setpoint must be finite")
        parent = cs.cascade_parent(loop_id)
        if parent is not None and self._loop_executes(parent):
            raise TEPAdapterError(
                f"Setpoint of loop {loop_id} is written by cascade master loop {parent} "
                f"({cs.LOOPS[parent].tag}); put the master in MAN to set it locally")
        self._setpt_array()[cs.LOOPS[loop_id].sp - 1] = float(value)

    def get_loop_states(self) -> List[dict]:
        xmeas = self._xmeas_array()
        xmv = self._xmv_array()
        sp = self._setpt_array()
        modes = self.get_loop_modes()
        out = []
        for lid in cs.EXECUTION_ORDER:
            d = cs.LOOPS[lid]
            output = float(xmv[d.output_index - 1]) if d.output_kind == "XMV" else float(sp[d.output_index - 1])
            out.append({
                "loop_id": lid, "tag": d.tag, "name": d.name,
                "pv_id": f"XMEAS({d.pv})", "pv": float(xmeas[d.pv - 1]),
                "sp_index": d.sp, "setpoint": float(sp[d.sp - 1]),
                "output_kind": d.output_kind,
                "output_id": f"{d.output_kind}({d.output_index})" if d.output_kind == "XMV"
                else f"SETPT({d.output_index})",
                "output": output,
                "output_target_loop": cs.loop_for_setpoint(d.output_index) if d.output_kind == "SETPT" else None,
                "mode": modes[lid].value, "cascade_parent": cs.cascade_parent(lid),
                "algorithm": d.algorithm, "gain": d.gain, "taui_h": d.taui_h,
                "period_s": d.period_steps, "error": cs.error_for(d, sp[d.sp - 1], xmeas[d.pv - 1]),
                "saturated": d.output_kind == "XMV" and (output <= 0.0 or output >= 100.0),
            })
        if self._get_purge_flag() != 0:
            for row in out:
                if row["loop_id"] == 6:
                    row["override"] = "HIGH_PRESSURE" if self._get_purge_flag() == 1 else "LOW_PRESSURE"
        return out

    # ---- boundary --------------------------------------------------------
    def get_boundary_parameters(self) -> Dict[str, float]:
        self._require_init()
        return {name: float(self._get_boundary(p)) for name, p in BOUNDARY_PARAMETERS.items()}

    def set_boundary_parameter(self, name: str, value: float) -> None:
        self._require_init()
        p = get_parameter(name)
        if not np.isfinite(value):
            raise ValueError(f"Boundary parameter {name} must be finite")
        v = float(min(max(value, p.min_value), p.max_value))
        self._set_boundary(p, v)

    def version_info(self) -> dict:
        return {"backend": self.backend_name, "tep_seed": self._tep_seed}
