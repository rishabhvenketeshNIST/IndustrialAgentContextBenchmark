"""Fortran TEP backend (authoritative).

Loads a shared library compiled from the *unmodified* ``teprob.f`` and
``temain_mod.f`` (see ``scripts/build_fortran.py``) with ctypes and drives the
native subroutines TEINIT, INTGTR (TEFUNC + Euler), CONSHAND and CONTRLn.
Common blocks are mapped with ctypes Structures whose layout follows the
Fortran declarations field by field.

Because common blocks are process-global, each adapter instance loads a
private copy of the library so that several simulations can coexist in one
Python process (e.g. reproducibility tests).
"""
from __future__ import annotations

import ctypes as C
import hashlib
import os
import platform
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from . import control_scheme as cs
from .boundary import BoundaryParameter
from .interface import BaseTEPAdapter, ProcessInternals, TEPAdapterError

HERE = Path(__file__).resolve().parent
FORTRAN_DIR = HERE / "fortran"
SOURCE_DIR = FORTRAN_DIR / "src"
LIB_DIR = FORTRAN_DIR / "lib"
SOURCES = ("teprob.f", "temain_mod.f")


def library_filename() -> str:
    if sys.platform.startswith("win"):
        return "tep_fortran.dll"
    if sys.platform == "darwin":
        return "libtep_fortran.dylib"
    return "libtep_fortran.so"


def library_path() -> Path:
    override = os.environ.get("TEP_FORTRAN_LIB")
    return Path(override) if override else LIB_DIR / library_filename()


def is_available() -> bool:
    return library_path().exists()


def source_hashes() -> dict:
    out = {}
    for name in SOURCES:
        p = SOURCE_DIR / name
        out[name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return out


# ---------------------------------------------------------------------------
# Common block layouts (teprob.f lines 210-313 and temain_mod.f lines 115-176)
# ---------------------------------------------------------------------------
_D = C.c_double
_I = C.c_int

_TEPROC_FIELDS: List[Tuple[str, object]] = []


def _f(name: str, n: int = 1, t=_D) -> None:
    _TEPROC_FIELDS.append((name, t * n if n > 1 else t))


for _name, _n in [
    ("UCLR", 8), ("UCVR", 8), ("UTLR", 1), ("UTVR", 1), ("XLR", 8), ("XVR", 8), ("ETR", 1), ("ESR", 1),
    ("TCR", 1), ("TKR", 1), ("DLR", 1), ("VLR", 1), ("VVR", 1), ("VTR", 1), ("PTR", 1), ("PPR", 8),
    ("CRXR", 8), ("RR", 4), ("RH", 1), ("FWR", 1), ("TWR", 1), ("QUR", 1), ("HWR", 1), ("UAR", 1),
    ("UCLS", 8), ("UCVS", 8), ("UTLS", 1), ("UTVS", 1), ("XLS", 8), ("XVS", 8), ("ETS", 1), ("ESS", 1),
    ("TCS", 1), ("TKS", 1), ("DLS", 1), ("VLS", 1), ("VVS", 1), ("VTS", 1), ("PTS", 1), ("PPS", 8),
    ("FWS", 1), ("TWS", 1), ("QUS", 1), ("HWS", 1),
    ("UCLC", 8), ("UTLC", 1), ("XLC", 8), ("ETC", 1), ("ESC", 1), ("TCC", 1), ("DLC", 1),
    ("VLC", 1), ("VTC", 1), ("QUC", 1),
    ("UCVV", 8), ("UTVV", 1), ("XVV", 8), ("ETV", 1), ("ESV", 1), ("TCV", 1), ("TKV", 1),
    ("VTV", 1), ("PTV", 1),
    ("VCV", 12), ("VRNG", 12), ("VTAU", 12), ("FTM", 13), ("FCM", 104), ("XST", 104), ("XMWS", 13),
    ("HST", 13), ("TST", 13), ("SFR", 8), ("CPFLMX", 1), ("CPPRMX", 1), ("CPDH", 1),
    ("TCWR", 1), ("TCWS", 1), ("HTR", 3), ("AGSP", 1), ("XDEL", 41), ("XNS", 41),
    ("TGAS", 1), ("TPROD", 1), ("VST", 12),
]:
    _f(_name, _n)
_f("IVST", 12, _I)


class TEProc(C.Structure):
    _fields_ = _TEPROC_FIELDS


class Wlk(C.Structure):
    _fields_ = [(n, _D * 12) for n in ("ADIST", "BDIST", "CDIST", "DDIST", "TLAST", "TNEXT",
                                        "HSPAN", "HZERO", "SSPAN", "SZERO", "SPSPAN")] + [("IDVWLK", _I * 12)]


class PV(C.Structure):
    _fields_ = [("XMEAS", _D * 41), ("XMV", _D * 12)]


class CtrlAll(C.Structure):
    _fields_ = [("SETPT", _D * 20), ("DELTAT", _D)]


class CtrlP(C.Structure):       # /CTRLn/ GAINn, ERROLDn
    _fields_ = [("GAIN", _D), ("ERROLD", _D)]


class CtrlPI(C.Structure):      # /CTRLn/ GAINn, TAUIn, ERROLDn
    _fields_ = [("GAIN", _D), ("TAUI", _D), ("ERROLD", _D)]


_P_ONLY_LOOPS = {1, 2, 3, 4, 6, 7, 8, 9}
_PI_LOOPS = {5, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 22}

_lib_lock = threading.Lock()
_lib_in_use: set = set()


def _xst(i: int, j: int) -> int:
    """0-based flat index of XST(I,J) in the column-major (8,13) array."""
    return (j - 1) * 8 + (i - 1)


class FortranTEPAdapter(BaseTEPAdapter):
    """Authoritative backend: native Fortran TEINIT/TEFUNC/CONTRLn via ctypes."""

    backend_name = "fortran"

    def __init__(self, lib_path: Optional[Path] = None) -> None:
        path = Path(lib_path) if lib_path else library_path()
        if not path.exists():
            raise TEPAdapterError(
                f"Fortran TEP library not found at {path}. Build it with "
                f"'python scripts/build_fortran.py' (needs gfortran or Docker).")
        self._source_lib = path
        self._private_copy: Optional[Path] = None
        self._lib = self._load(path)
        self._bind()
        super().__init__()

    # ---- library management ---------------------------------------------
    def _load(self, path: Path):
        key = str(path.resolve())
        with _lib_lock:
            if key in _lib_in_use:
                tmpdir = Path(tempfile.mkdtemp(prefix="tep_fortran_"))
                # unique file name: the Windows loader de-duplicates by module name
                copy = tmpdir / f"{path.stem}_{os.getpid()}_{id(self)}{path.suffix}"
                shutil.copy2(path, copy)
                self._private_copy = copy
                load_path = copy
            else:
                _lib_in_use.add(key)
                load_path = path
            self._lib_key = key if self._private_copy is None else None
        if sys.platform.startswith("win"):
            return C.CDLL(str(load_path), winmode=0)
        return C.CDLL(str(load_path))

    def close(self) -> None:
        with _lib_lock:
            if self._lib_key:
                _lib_in_use.discard(self._lib_key)
                self._lib_key = None

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.close()
        except Exception:
            pass

    def _bind(self) -> None:
        lib = self._lib
        self._pv = PV.in_dll(lib, "pv_")
        self._dvec = (C.c_int * 20).in_dll(lib, "dvec_")
        self._randsd = C.c_double.in_dll(lib, "randsd_")
        self._teproc = TEProc.in_dll(lib, "teproc_")
        self._wlk = Wlk.in_dll(lib, "wlk_")
        self._ctrlall = CtrlAll.in_dll(lib, "ctrlall_")
        self._flag6 = C.c_int.in_dll(lib, "flag6_")
        self._ctrl = {}
        for n in _P_ONLY_LOOPS:
            self._ctrl[n] = CtrlP.in_dll(lib, f"ctrl{n}_")
        for n in _PI_LOOPS:
            self._ctrl[n] = CtrlPI.in_dll(lib, f"ctrl{n}_")
        self._contrl = {n: getattr(lib, f"contrl{n}_") for n in list(cs.LOOPS) + [22]}
        for fn in self._contrl.values():
            fn.restype = None
            fn.argtypes = []
        self._teinit = lib.teinit_
        self._intgtr = lib.intgtr_
        self._conshand = lib.conshand_
        self._nn = C.c_int(50)
        self._time = C.c_double(0.0)
        self._deltat = C.c_double(cs.DELTAT_H)
        self._yy = (C.c_double * 50)()
        self._yp = (C.c_double * 50)()
        # numpy views onto common-block memory (writes go straight to Fortran)
        self._xmeas_np = np.ctypeslib.as_array(self._pv.XMEAS)
        self._xmv_np = np.ctypeslib.as_array(self._pv.XMV)
        self._setpt_np = np.ctypeslib.as_array(self._ctrlall.SETPT)
        self._yy_np = np.ctypeslib.as_array(self._yy)

    # ---- primitives ---------------------------------------------------------
    def _native_init(self) -> None:
        # The native program calls TEINIT once on freshly loaded, zero-initialised COMMON
        # storage. TEINIT does not reset everything TEFUNC reads: e.g. TCR/TCS/TCC/TCV are the
        # initial guesses of the TESUB2 Newton solve. Zero every common block first so a
        # re-initialisation is bit-identical to a fresh program start.
        for blk in [self._pv, self._dvec, self._randsd, self._teproc, self._wlk, self._ctrlall, self._flag6,
                    *self._ctrl.values()]:
            C.memset(C.addressof(blk), 0, C.sizeof(blk))
        for arr in (self._yy, self._yp):
            C.memset(arr, 0, C.sizeof(arr))
        self._time.value = 0.0
        self._teinit(C.byref(self._nn), C.byref(self._time), self._yy, self._yp)
        self._ctrlall.DELTAT = cs.DELTAT_H

    def _set_seed(self, g: float) -> None:
        self._randsd.value = float(g)

    def _integrate_one_second(self) -> None:
        self._intgtr(C.byref(self._nn), C.byref(self._time), C.byref(self._deltat), self._yy, self._yp)
        self._conshand()

    def _run_native_controller(self, loop_id: int) -> None:
        self._contrl[loop_id]()

    def _xmeas_array(self) -> np.ndarray:
        return self._xmeas_np

    def _xmv_array(self) -> np.ndarray:
        return self._xmv_np

    def _setpt_array(self) -> np.ndarray:
        return self._setpt_np

    def _set_controller_params(self, loop_id: int, gain: float, taui_h: Optional[float], errold: float) -> None:
        blk = self._ctrl[loop_id]
        blk.GAIN = gain
        if isinstance(blk, CtrlPI):
            if taui_h is None:
                raise TEPAdapterError(f"Loop {loop_id} requires TAUI")
            blk.TAUI = taui_h
        blk.ERROLD = errold

    def _get_errold(self, loop_id: int) -> float:
        return float(self._ctrl[loop_id].ERROLD)

    def _set_errold(self, loop_id: int, value: float) -> None:
        self._ctrl[loop_id].ERROLD = value

    def _set_purge_flag(self, value: int) -> None:
        self._flag6.value = int(value)

    def _get_purge_flag(self) -> int:
        return int(self._flag6.value)

    def _get_boundary(self, p: BoundaryParameter) -> float:
        loc = p.locator
        if loc[0] == "VRNG":
            return self._teproc.VRNG[loc[1] - 1]
        if loc[0] == "CPFLMX":
            return self._teproc.CPFLMX
        if loc[0] == "SZERO":
            return self._wlk.SZERO[loc[1] - 1]
        if loc[0] == "XST_IMPURITY":
            _, comp, stream, _main = loc
            return self._teproc.XST[_xst(comp, stream)]
        raise TEPAdapterError(f"Unsupported locator {loc}")

    def _set_boundary(self, p: BoundaryParameter, value: float) -> None:
        loc = p.locator
        if loc[0] == "VRNG":
            self._teproc.VRNG[loc[1] - 1] = value
        elif loc[0] == "CPFLMX":
            self._teproc.CPFLMX = value
        elif loc[0] == "SZERO":
            self._wlk.SZERO[loc[1] - 1] = value
        elif loc[0] == "XST_IMPURITY":
            _, comp, stream, main = loc
            self._teproc.XST[_xst(comp, stream)] = value
            self._teproc.XST[_xst(main, stream)] = 1.0 - value - self._other_minor(stream, comp, main)
        else:
            raise TEPAdapterError(f"Unsupported locator {loc}")

    def _other_minor(self, stream: int, comp: int, main: int) -> float:
        return sum(self._teproc.XST[_xst(i, stream)] for i in range(1, 9) if i not in (comp, main))

    def _internals(self) -> ProcessInternals:
        t = self._teproc
        return ProcessInternals(
            reactor_pressure_kpa=(t.PTR - 760.0) / 760.0 * 101.325,
            reactor_temperature_c=t.TCR,
            reactor_liquid_m3=t.VLR / 35.3145,
            separator_liquid_m3=t.VLS / 35.3145,
            stripper_liquid_m3=t.VLC / 35.3145,
        )

    # ---- remaining interface ---------------------------------------------------
    def get_states(self) -> np.ndarray:
        return self._yy_np.copy()

    def get_disturbances(self) -> np.ndarray:
        return np.array(self._dvec[:], dtype=int)

    def set_disturbance(self, index: int, active: bool) -> None:
        if not 1 <= index <= 20:
            raise ValueError(f"IDV index must be 1..20, got {index}")
        self._dvec[index - 1] = 1 if active else 0

    def get_time(self) -> float:
        return float(self._time.value)

    def version_info(self) -> dict:
        info = super().version_info()
        info.update({
            "library": str(self._source_lib.name),
            "library_sha256": hashlib.sha256(self._source_lib.read_bytes()).hexdigest(),
            "sources": source_hashes(),
            "platform": platform.platform(),
        })
        return info
