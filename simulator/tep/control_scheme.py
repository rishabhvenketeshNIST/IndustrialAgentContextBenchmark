"""Native TEP closed-loop control scheme (temain_mod.f).

The decentralized plant-wide control scheme of Russell, Chiang and Braatz is
implemented in ``temain_mod.f`` as the subroutines CONTRL1..CONTRL22. Their
tuning parameters and initial setpoints are assigned inside the Fortran MAIN
program, which is never executed by this simulator. This module transcribes
those assignments (temain_mod.f lines 243-332) verbatim so the adapter can load
them into the /CTRLALL/ and /CTRLn/ common blocks before calling the native
subroutines.

Nothing in here changes the control law. The Fortran backend calls the native
subroutines; the Python development backend uses a line-by-line transcription
(see ``python_backend.py``).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np


def r4(x) -> float:
    """A Fortran default-REAL (single precision) literal assigned to DOUBLE PRECISION.

    temain_mod.f writes constants such as ``SETPT(3)=.25052`` or
    ``GAIN10= -0.156 * 10.`` without a D exponent, so gfortran folds them in
    single precision before widening. Reproducing that keeps the native
    controller initialisation bit-identical.
    """
    return float(np.float32(x))


def _r4mul(a, b) -> float:
    return float(np.float32(a) * np.float32(b))


def _r4div(a, b) -> float:
    return float(np.float32(a) / np.float32(b))


# temain_mod.f: "DELTAT = 1. / 3600." - integrator step, 1 second in hours (REAL arithmetic)
DELTAT_H = _r4div(1.0, 3600.0)


class ControlMode(str, Enum):
    """Plant-level control mode."""
    CLOSED_LOOP = "CLOSED_LOOP"  # all native loops execute
    MANUAL = "MANUAL"            # no native loop executes; XMVs are operator-set


class LoopMode(str, Enum):
    AUTO = "AUTO"      # loop executes; setpoint is local
    CASCADE = "CAS"    # loop executes; setpoint written by an outer loop
    MANUAL = "MAN"     # loop does not execute; output is operator-set


@dataclass(frozen=True)
class LoopDefinition:
    """One native CONTRLn subroutine."""
    loop_id: int                 # n in CONTRLn
    tag: str
    name: str
    pv: int                      # XMEAS index read by the loop
    sp: int                      # SETPT index holding the loop setpoint
    output_kind: str             # "XMV" or "SETPT"
    output_index: int            # XMV index or SETPT index written
    pv_span: float               # ERR = (SETPT - XMEAS) * 100 / pv_span
    output_span: float           # SETPT outputs: SETPT += DXMV * output_span / 100
    gain: float
    taui_h: Optional[float]      # None => velocity-form proportional only
    period_steps: int            # executed when MOD(I, period) == 0
    initial_setpoint: float
    note: str = ""

    @property
    def algorithm(self) -> str:
        return "PI (velocity form)" if self.taui_h is not None else "P (velocity form)"


# (loop_id, tag, name, pv, sp, out_kind, out_idx, pv_span, out_span, gain, taui_h, period, sp0)
# Spans are the literal constants in the ERRn / SETPT update statements of each CONTRLn;
# they are used for metadata and bumpless transfer only (the native code holds its own).
_LOOPS: List[Tuple] = [
    (1, "FC-D", "D Feed Flow Control", 2, 1, "XMV", 1, 5811.0, 0, r4(1.0), None, 3, r4(3664.0)),
    (2, "FC-E", "E Feed Flow Control", 3, 2, "XMV", 2, 8354.0, 0, r4(1.0), None, 3, r4(4509.3)),
    (3, "FC-A", "A Feed Flow Control", 1, 3, "XMV", 3, r4(1.017), 0, r4(1.0), None, 3, r4(0.25052)),
    (4, "FC-AC", "A and C Feed Flow Control", 4, 4, "XMV", 4, 15.25, 0, r4(1.0), None, 3, r4(9.3477)),
    (5, "FC-RCY", "Recycle Flow Control", 5, 5, "XMV", 5, 53.0, 0, r4(-0.083), _r4div(1.0, 3600.0), 3,
     r4(26.902)),
    (6, "FC-PRG", "Purge Rate Control (with separator pressure override)", 10, 6, "XMV", 6, 1.0, 0,
     r4(1.22), None, 3, r4(0.33712)),
    (7, "LC-SEP", "Separator Level Control", 12, 7, "XMV", 7, 70.0, 0, r4(-2.06), None, 3, r4(50.0)),
    (8, "LC-STR", "Stripper Level Control", 15, 8, "XMV", 8, 70.0, 0, r4(-1.62), None, 3, r4(50.0)),
    (9, "FC-STM", "Stripper Steam Flow Control", 19, 9, "XMV", 9, 460.0, 0, r4(0.41), None, 3, r4(230.31)),
    (10, "TC-RCW", "Reactor Cooling Water Outlet Temperature Control", 21, 10, "XMV", 10, 150.0, 0,
     _r4mul(-0.156, 10.0), _r4div(1452.0, 3600.0), 3, r4(94.599)),
    (11, "FC-PRD", "Product (Stripper Underflow) Flow Control", 17, 11, "XMV", 11, 46.0, 0,
     r4(1.09), _r4div(2600.0, 3600.0), 3, r4(22.949)),
    (13, "AC-RFA", "Reactor Feed A Composition Control", 23, 13, "SETPT", 3, 100.0, r4(1.017),
     r4(18.0), _r4div(3168.0, 3600.0), 360, r4(32.188)),
    (14, "AC-RFD", "Reactor Feed D Composition Control", 26, 14, "SETPT", 1, 100.0, 5811.0,
     r4(8.3), _r4div(3168.0, 3600.0), 360, r4(6.8820)),
    (15, "AC-RFE", "Reactor Feed E Composition Control", 27, 15, "SETPT", 2, 100.0, 8354.0,
     r4(2.37), _r4div(5069.0, 3600.0), 360, r4(18.776)),
    (16, "TC-STR", "Stripper Temperature Control", 18, 16, "SETPT", 9, 130.0, 460.0,
     _r4div(1.69, 10.0), _r4div(236.0, 3600.0), 3, r4(65.731)),
    (17, "LC-RX", "Reactor Level Control", 8, 17, "SETPT", 4, 50.0, 15.25,
     _r4div(11.1, 10.0), _r4div(3168.0, 3600.0), 3, r4(75.0)),
    (18, "TC-RX", "Reactor Temperature Control", 9, 18, "SETPT", 10, 150.0, 150.0,
     _r4mul(2.83, 10.0), _r4div(982.0, 3600.0), 3, r4(120.40)),
    (19, "AC-PRGB", "Purge Gas B Composition Control", 30, 19, "SETPT", 6, 26.0, 1.0,
     _r4div(_r4div(-83.2, 5.0), 3.0), _r4div(6336.0, 3600.0), 360, r4(13.823)),
    (20, "AC-PRDE", "Product E Composition Control", 38, 20, "SETPT", 16, r4(1.6), 130.0,
     _r4div(-16.3, 5.0), _r4div(12408.0, 3600.0), 900, r4(0.83570)),
]

LOOPS: Dict[int, LoopDefinition] = {
    row[0]: LoopDefinition(*row) for row in _LOOPS
}

# Execution order inside the native main loop (temain_mod.f lines 366-392).
EXECUTION_ORDER: List[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18, 13, 14, 15, 19, 20]

# CONTRL22 (separator pressure -> XMV(6)) is defined in temain_mod.f but never
# called by the native main loop; its parameters are loaded for completeness.
UNUSED_LOOP_22 = {"setpt_index": 12, "setpoint": r4(2633.7), "gain": _r4mul(-1.0, 5.0),
                  "taui_h": _r4div(1000.0, 3600.0)}

# Initial XMV(1..11) assigned in the main program (temain_mod.f lines 322-332).
# XMV(12) (agitator) is not assigned there and keeps the TEINIT value 50.0.
INITIAL_XMV: Dict[int, float] = {k: r4(v) for k, v in {
    1: 63.053, 2: 53.980, 3: 24.644, 4: 61.302, 5: 22.210, 6: 40.064,
    7: 38.100, 8: 46.534, 9: 47.446, 10: 41.106, 11: 18.114,
}.items()}

# CONTRL6 separator pressure override constants (temain_mod.f lines 710-728).
# In CONTRL6 these literals are compared/assigned inside the compiled subroutine
# (single-precision REAL constants).
PURGE_OVERRIDE = {"high_kPa": r4(2950.0), "reset_kPa": r4(2633.7), "low_kPa": r4(2300.0),
                  "reset_xmv": r4(40.060), "reset_setpoint": r4(0.33712)}


def cascade_parent(loop_id: int) -> Optional[int]:
    """Return the outer loop that writes this loop's setpoint, if any."""
    sp_index = LOOPS[loop_id].sp
    for lid, d in LOOPS.items():
        if d.output_kind == "SETPT" and d.output_index == sp_index:
            return lid
    return None


def loop_for_setpoint(sp_index: int) -> Optional[int]:
    for lid, d in LOOPS.items():
        if d.sp == sp_index:
            return lid
    return None


def loop_for_xmv(xmv_index: int) -> Optional[int]:
    for lid, d in LOOPS.items():
        if d.output_kind == "XMV" and d.output_index == xmv_index:
            return lid
    return None


def error_for(loop: LoopDefinition, setpoint: float, pv_value: float) -> float:
    """ERRn exactly as computed at the top of CONTRLn."""
    return (setpoint - pv_value) * 100.0 / loop.pv_span
