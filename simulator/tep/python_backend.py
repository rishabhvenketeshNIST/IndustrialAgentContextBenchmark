"""Pure-Python TEP backend (development / testing only).

Process physics: the vendored ``PythonTEProcess`` from
https://github.com/jkitchin/tennessee-eastman-profbraatz (BSD-3-Clause), a
line-by-line port of TEINIT/TEFUNC, copied unmodified into
``simulator/tep/vendor``.

Control: a transcription of the temain_mod.f subroutines CONTRL1..CONTRL20
that follows the Fortran statement order exactly.

The Fortran backend remains the authoritative validation backend; the two are
compared in ``tests/test_tep_adapter.py``.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import control_scheme as cs
from .boundary import BoundaryParameter
from .interface import BaseTEPAdapter, ProcessInternals, TEPAdapterError
from .vendor.tep_python_backend import PythonTEProcess


class PythonTEPAdapter(BaseTEPAdapter):
    backend_name = "python"

    def __init__(self) -> None:
        self._proc: Optional[PythonTEProcess] = None
        self._setpt = np.zeros(20)
        self._gain = {}
        self._taui = {}
        self._errold = {}
        self._flag = 0
        super().__init__()

    # ---- primitives ---------------------------------------------------------
    def _native_init(self) -> None:
        # A fresh object guarantees every "common block" returns to TEINIT values
        self._proc = PythonTEProcess()
        self._proc.initialize()

    def _set_seed(self, g: float) -> None:
        self._proc._g = float(g)

    def _integrate_one_second(self) -> None:
        self._proc.step(cs.DELTAT_H)

    def _run_native_controller(self, loop_id: int) -> None:
        if loop_id == 6:
            self._contrl6()
            return
        d = cs.LOOPS[loop_id]
        xmeas, xmv, sp = self._proc._xmeas, self._proc._xmv, self._setpt
        err = (sp[d.sp - 1] - xmeas[d.pv - 1]) * 100.0 / d.pv_span
        errold = self._errold[loop_id]
        if d.taui_h is None:
            dxmv = self._gain[loop_id] * ((err - errold))
        else:
            dxmv = self._gain[loop_id] * ((err - errold) + err * cs.DELTAT_H * float(d.period_steps)
                                          / self._taui[loop_id])
        if d.output_kind == "XMV":
            xmv[d.output_index - 1] = xmv[d.output_index - 1] + dxmv
        else:
            sp[d.output_index - 1] = sp[d.output_index - 1] + dxmv * d.output_span / 100.0
        self._errold[loop_id] = err

    def _contrl6(self) -> None:
        """CONTRL6: purge flow control with separator-pressure override."""
        o = cs.PURGE_OVERRIDE
        xmeas, xmv, sp = self._proc._xmeas, self._proc._xmv, self._setpt
        p13 = xmeas[12]
        if p13 >= o["high_kPa"]:
            xmv[5] = 100.0
            self._flag = 1
        elif self._flag == 1 and p13 >= o["reset_kPa"]:
            xmv[5] = 100.0
        elif self._flag == 1 and p13 <= o["reset_kPa"]:
            xmv[5] = o["reset_xmv"]
            sp[5] = o["reset_setpoint"]
            self._errold[6] = 0.0
            self._flag = 0
        elif p13 <= o["low_kPa"]:
            xmv[5] = 0.0
            self._flag = 2
        elif self._flag == 2 and p13 <= o["reset_kPa"]:
            xmv[5] = 0.0
        elif self._flag == 2 and p13 >= o["reset_kPa"]:
            xmv[5] = o["reset_xmv"]
            sp[5] = o["reset_setpoint"]
            self._errold[6] = 0.0
            self._flag = 0
        else:
            self._flag = 0
            err6 = (sp[5] - xmeas[9]) * 100.0 / 1.0
            dxmv = self._gain[6] * ((err6 - self._errold[6]))
            xmv[5] = xmv[5] + dxmv
            self._errold[6] = err6

    def _xmeas_array(self) -> np.ndarray:
        return self._proc._xmeas

    def _xmv_array(self) -> np.ndarray:
        return self._proc._xmv

    def _setpt_array(self) -> np.ndarray:
        return self._setpt

    def _set_controller_params(self, loop_id: int, gain: float, taui_h: Optional[float], errold: float) -> None:
        self._gain[loop_id] = gain
        self._taui[loop_id] = taui_h
        self._errold[loop_id] = errold

    def _get_errold(self, loop_id: int) -> float:
        return float(self._errold[loop_id])

    def _set_errold(self, loop_id: int, value: float) -> None:
        self._errold[loop_id] = value

    def _set_purge_flag(self, value: int) -> None:
        self._flag = int(value)

    def _get_purge_flag(self) -> int:
        return self._flag

    def _get_boundary(self, p: BoundaryParameter) -> float:
        tp, wlk = self._proc._teproc, self._proc._wlk
        loc = p.locator
        if loc[0] == "VRNG":
            return float(tp.vrng[loc[1] - 1])
        if loc[0] == "CPFLMX":
            return float(tp.cpflmx)
        if loc[0] == "SZERO":
            return float(wlk.szero[loc[1] - 1])
        if loc[0] == "XST_IMPURITY":
            return float(tp.xst[loc[1] - 1, loc[2] - 1])
        raise TEPAdapterError(f"Unsupported locator {loc}")

    def _set_boundary(self, p: BoundaryParameter, value: float) -> None:
        tp, wlk = self._proc._teproc, self._proc._wlk
        loc = p.locator
        if loc[0] == "VRNG":
            tp.vrng[loc[1] - 1] = value
        elif loc[0] == "CPFLMX":
            tp.cpflmx = value
        elif loc[0] == "SZERO":
            wlk.szero[loc[1] - 1] = value
        elif loc[0] == "XST_IMPURITY":
            _, comp, stream, main = loc
            col = tp.xst[:, stream - 1]
            col[comp - 1] = value
            others = sum(col[i] for i in range(8) if i not in (comp - 1, main - 1))
            col[main - 1] = 1.0 - value - others
        else:
            raise TEPAdapterError(f"Unsupported locator {loc}")

    def _internals(self) -> ProcessInternals:
        tp = self._proc._teproc
        return ProcessInternals(
            reactor_pressure_kpa=(tp.ptr - 760.0) / 760.0 * 101.325,
            reactor_temperature_c=tp.tcr,
            reactor_liquid_m3=tp.vlr / 35.3145,
            separator_liquid_m3=tp.vls / 35.3145,
            stripper_liquid_m3=tp.vlc / 35.3145,
        )

    # ---- remaining interface ----------------------------------------------------
    def get_states(self) -> np.ndarray:
        return np.asarray(self._proc.yy, dtype=float).copy()

    def get_disturbances(self) -> np.ndarray:
        return np.asarray(self._proc._idv, dtype=int).copy()

    def set_disturbance(self, index: int, active: bool) -> None:
        if not 1 <= index <= 20:
            raise ValueError(f"IDV index must be 1..20, got {index}")
        self._proc._idv[index - 1] = 1 if active else 0

    def get_time(self) -> float:
        return float(self._proc.time)

    def version_info(self) -> dict:
        info = super().version_info()
        info["physics"] = "vendored jkitchin PythonTEProcess (tennessee-eastman-profbraatz)"
        return info
